"""Isolated monthly plan/fact calculations and persistence.

Nothing in this module changes the established dashboard, funnel, marketing, or
Yclients reports. It reads their source databases and writes only plan_fact_* tables.
"""

from __future__ import annotations

import hashlib
import json
import os
from calendar import monthrange
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import pymysql
from db_security import connect as secure_connect, tls_context

from amocrm_client import load_env
from .analytics import EXCLUDED_LOSS_REASON_IDS_SQL, MOSCOW, TAG_SEPARATOR
from .classifier import TagClassifier, UNKNOWN, normalize
from .yclients_analytics import (
    YclientsAnalytics,
    classify_direction,
    normalize_branch,
    normalize_record_color,
)


PLAN_FACT_START = date(2026, 8, 1)
FORMULA_VERSION = "2026-09-v1"
DIRECTIONS = ("Массаж", "Лазер")
CALL_SOURCE = "Звонок"
SPEED_DIAL_SOURCE = "Скорозвон"
OTHER_SOURCE = "Прочее"
LEAD_EXCLUDED_SOURCES = {UNKNOWN, SPEED_DIAL_SOURCE, CALL_SOURCE}
PRIMARY_EXCLUDED_SOURCES = {UNKNOWN}
MONEY_STEP = Decimal("0.01")
VALUE_STEP = Decimal("0.0001")


class PlanFactSchemaMissing(RuntimeError):
    """Raised when plan_fact_schema.sql has not been applied."""


@dataclass(frozen=True)
class MetricDefinition:
    code: str
    label: str
    unit: str
    source: str
    section: str
    formula: str = ""
    favorable: str = "higher"


METRICS: Tuple[MetricDefinition, ...] = (
    MetricDefinition("revenue", "Выручка", "money", "manual", "Общие"),
    MetricDefinition("receivables", "Дебиторка", "money", "manual", "Общие", favorable="lower"),
    MetricDefinition("leads", "Лиды", "count", "manual", "Первичная воронка"),
    MetricDefinition("lead_to_booking_cr", "CR лид → запись", "percent", "manual", "Первичная воронка"),
    MetricDefinition("bookings", "Первичные записи", "count", "calculated", "Первичная воронка", "Лиды × CR лид → запись"),
    MetricDefinition("booking_to_visit_cr", "CR запись → приход", "percent", "manual", "Первичная воронка"),
    MetricDefinition("lead_to_visit_cr", "CR лид → приход", "percent", "calculated", "Первичная воронка", "Первичные приходы ÷ лиды"),
    MetricDefinition("primary_visits", "Первичные визиты (ПВ), кол-во", "count", "calculated", "Первичные визиты", "Записи × CR запись → приход"),
    MetricDefinition("primary_avg_check", "Средний чек ПВ", "money", "manual", "Первичные визиты"),
    MetricDefinition("primary_visit_sum", "ПВ, сумма", "money", "calculated", "Первичные визиты", "ПВ кол-во × средний чек ПВ"),
    MetricDefinition("primary_subscription_cr", "CR первичного абонемента (ПА)", "percent", "manual", "Первичные абонементы"),
    MetricDefinition("primary_subscription_count", "ПА, кол-во", "count", "calculated", "Первичные абонементы", "ПВ кол-во × CR ПА"),
    MetricDefinition("primary_subscription_initial_avg", "Средний первоначальный взнос ПА", "money", "manual", "Первичные абонементы"),
    MetricDefinition("primary_subscription_full_avg", "Средняя стоимость ПА после скидки", "money", "manual", "Первичные абонементы"),
    MetricDefinition("primary_subscription_sum", "ПА, сумма первоначальных взносов", "money", "calculated", "Первичные абонементы", "ПА кол-во × средний взнос ПА"),
    MetricDefinition("repeat_visits", "Повторные визиты", "count", "manual", "Повторные продажи"),
    MetricDefinition("repeat_subscription_cr", "CR повторной продажи (ПП)", "percent", "manual", "Повторные продажи"),
    MetricDefinition("repeat_subscription_initial_avg", "Средний первоначальный взнос ПП", "money", "manual", "Повторные продажи"),
    MetricDefinition("repeat_subscription_full_avg", "Средняя стоимость ПП после скидки", "money", "manual", "Повторные продажи"),
    MetricDefinition("repeat_subscription_count", "ПП, кол-во", "count", "calculated", "Повторные продажи", "Повторные визиты × CR ПП"),
    MetricDefinition("repeat_subscription_sum", "ПП, сумма первоначальных взносов", "money", "calculated", "Повторные продажи", "ПП кол-во × средний взнос ПП"),
    MetricDefinition("one_off_count", "Разовые визиты, кол-во", "count", "manual", "Разовые визиты"),
    MetricDefinition("one_off_avg_check", "Средний чек разового визита", "money", "manual", "Разовые визиты"),
    MetricDefinition("one_off_sum", "Разовые визиты, сумма", "money", "calculated", "Разовые визиты", "Разовые кол-во × средний чек"),
)

METRIC_BY_CODE = {metric.code: metric for metric in METRICS}
MANUAL_METRIC_CODES = tuple(metric.code for metric in METRICS if metric.source == "manual")
COUNT_METRICS = {metric.code for metric in METRICS if metric.unit == "count"}


def metric_catalog() -> List[Dict[str, Any]]:
    return [metric.__dict__.copy() for metric in METRICS]


def _decimal(value: Any, label: str) -> Decimal:
    normalized = str(value if value is not None else "").strip().replace(" ", "").replace(",", ".")
    try:
        result = Decimal(normalized).quantize(VALUE_STEP, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"Поле «{label}» должно содержать число") from error
    if result < 0:
        raise ValueError(f"Поле «{label}» не может быть отрицательным")
    if result > Decimal("9999999999999999"):
        raise ValueError(f"Значение поля «{label}» слишком большое")
    return result


def _whole(value: Decimal) -> Decimal:
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    if not denominator:
        return Decimal("0")
    return (numerator / denominator * Decimal("100")).quantize(VALUE_STEP, rounding=ROUND_HALF_UP)


def calculate_plan_values(values: Mapping[str, Any]) -> Dict[str, Decimal]:
    """Validate manual inputs and materialize all calculated plan metrics."""
    result: Dict[str, Decimal] = {}
    for code in MANUAL_METRIC_CODES:
        metric = METRIC_BY_CODE[code]
        result[code] = _decimal(values.get(code), metric.label)
        if metric.unit == "percent" and result[code] > 100:
            raise ValueError(f"Поле «{metric.label}» не может быть больше 100%")
        if metric.unit == "count":
            result[code] = _whole(result[code])

    result["bookings"] = _whole(result["leads"] * result["lead_to_booking_cr"] / 100)
    result["primary_visits"] = _whole(result["bookings"] * result["booking_to_visit_cr"] / 100)
    result["lead_to_visit_cr"] = _ratio(result["primary_visits"], result["leads"])
    result["primary_visit_sum"] = result["primary_visits"] * result["primary_avg_check"]
    result["primary_subscription_count"] = _whole(
        result["primary_visits"] * result["primary_subscription_cr"] / 100
    )
    result["primary_subscription_sum"] = (
        result["primary_subscription_count"] * result["primary_subscription_initial_avg"]
    )
    result["repeat_subscription_count"] = _whole(
        result["repeat_visits"] * result["repeat_subscription_cr"] / 100
    )
    result["repeat_subscription_sum"] = (
        result["repeat_subscription_count"] * result["repeat_subscription_initial_avg"]
    )
    result["one_off_sum"] = result["one_off_count"] * result["one_off_avg_check"]
    return {code: value.quantize(VALUE_STEP, rounding=ROUND_HALF_UP) for code, value in result.items()}


def derive_actual_metrics(primitives: Mapping[str, Any]) -> Dict[str, Decimal]:
    """Derive the same 24 metrics from additive actual primitives."""
    value = {
        key: Decimal(str(raw or 0))
        for key, raw in primitives.items()
        if key not in {"branch", "direction"}
    }
    result = {
        "revenue": value.get("revenue", Decimal("0")),
        "receivables": value.get("receivables", Decimal("0")),
        "leads": value.get("leads", Decimal("0")),
        "bookings": value.get("bookings", Decimal("0")),
        "primary_visits": value.get("primary_visits", Decimal("0")),
        "primary_visit_sum": value.get("primary_visit_sum", Decimal("0")),
        "primary_subscription_count": value.get("primary_subscription_count", Decimal("0")),
        "primary_subscription_sum": value.get("primary_subscription_initial_sum", Decimal("0")),
        "repeat_visits": value.get("repeat_visits", Decimal("0")),
        "repeat_subscription_count": value.get("repeat_subscription_count", Decimal("0")),
        "repeat_subscription_sum": value.get("repeat_subscription_initial_sum", Decimal("0")),
        "one_off_count": value.get("one_off_count", Decimal("0")),
        "one_off_sum": value.get("one_off_sum", Decimal("0")),
    }
    result.update(
        {
            "lead_to_booking_cr": _ratio(result["bookings"], result["leads"]),
            "booking_to_visit_cr": _ratio(result["primary_visits"], result["bookings"]),
            "lead_to_visit_cr": _ratio(result["primary_visits"], result["leads"]),
            "primary_avg_check": (
                result["primary_visit_sum"] / result["primary_visits"]
                if result["primary_visits"] else Decimal("0")
            ),
            "primary_subscription_cr": _ratio(
                result["primary_subscription_count"], result["primary_visits"]
            ),
            "primary_subscription_initial_avg": (
                result["primary_subscription_sum"] / result["primary_subscription_count"]
                if result["primary_subscription_count"] else Decimal("0")
            ),
            "primary_subscription_full_avg": (
                value.get("primary_subscription_full_sum", Decimal("0"))
                / result["primary_subscription_count"]
                if result["primary_subscription_count"] else Decimal("0")
            ),
            "repeat_subscription_cr": _ratio(
                result["repeat_subscription_count"], result["repeat_visits"]
            ),
            "repeat_subscription_initial_avg": (
                result["repeat_subscription_sum"] / result["repeat_subscription_count"]
                if result["repeat_subscription_count"] else Decimal("0")
            ),
            "repeat_subscription_full_avg": (
                value.get("repeat_subscription_full_sum", Decimal("0"))
                / result["repeat_subscription_count"]
                if result["repeat_subscription_count"] else Decimal("0")
            ),
            "one_off_avg_check": (
                result["one_off_sum"] / result["one_off_count"]
                if result["one_off_count"] else Decimal("0")
            ),
        }
    )
    return {code: result.get(code, Decimal("0")).quantize(VALUE_STEP, rounding=ROUND_HALF_UP) for code in METRIC_BY_CODE}


def aggregate_metric_values(rows: Iterable[Mapping[str, Any]], *, actual: bool) -> Dict[str, Decimal]:
    materialized = list(rows)
    if actual:
        primitives: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        for row in materialized:
            for key, raw in row.items():
                if key not in {"branch", "direction"}:
                    primitives[key] += Decimal(str(raw or 0))
        return derive_actual_metrics(primitives)

    additive = {
        "revenue", "receivables", "leads", "bookings", "primary_visits",
        "primary_visit_sum", "primary_subscription_count", "primary_subscription_sum",
        "repeat_visits", "repeat_subscription_count", "repeat_subscription_sum",
        "one_off_count", "one_off_sum",
    }
    sums = {code: sum((Decimal(str(row.get(code) or 0)) for row in materialized), Decimal("0")) for code in additive}
    result = dict(sums)
    result.update({
        "lead_to_booking_cr": _ratio(sums["bookings"], sums["leads"]),
        "booking_to_visit_cr": _ratio(sums["primary_visits"], sums["bookings"]),
        "lead_to_visit_cr": _ratio(sums["primary_visits"], sums["leads"]),
        "primary_avg_check": sums["primary_visit_sum"] / sums["primary_visits"] if sums["primary_visits"] else Decimal("0"),
        "primary_subscription_cr": _ratio(sums["primary_subscription_count"], sums["primary_visits"]),
        "primary_subscription_initial_avg": sums["primary_subscription_sum"] / sums["primary_subscription_count"] if sums["primary_subscription_count"] else Decimal("0"),
        "repeat_subscription_cr": _ratio(sums["repeat_subscription_count"], sums["repeat_visits"]),
        "repeat_subscription_initial_avg": sums["repeat_subscription_sum"] / sums["repeat_subscription_count"] if sums["repeat_subscription_count"] else Decimal("0"),
        "one_off_avg_check": sums["one_off_sum"] / sums["one_off_count"] if sums["one_off_count"] else Decimal("0"),
    })
    for average_code, count_code in (
        ("primary_subscription_full_avg", "primary_subscription_count"),
        ("repeat_subscription_full_avg", "repeat_subscription_count"),
    ):
        denominator = sums[count_code]
        result[average_code] = (
            sum((Decimal(str(row.get(average_code) or 0)) * Decimal(str(row.get(count_code) or 0)) for row in materialized), Decimal("0")) / denominator
            if denominator else Decimal("0")
        )
    return {code: result.get(code, Decimal("0")).quantize(VALUE_STEP, rounding=ROUND_HALF_UP) for code in METRIC_BY_CODE}


def comparison_rows(plan: Mapping[str, Decimal], actual: Mapping[str, Decimal]) -> List[Dict[str, Any]]:
    rows = []
    for metric in METRICS:
        planned = Decimal(str(plan.get(metric.code) or 0))
        factual = Decimal(str(actual.get(metric.code) or 0))
        variance = factual - planned
        attainment = factual / planned * 100 if planned else None
        rows.append({
            **metric.__dict__,
            "plan": float(planned),
            "actual": float(factual),
            "variance": float(variance),
            "attainment": round(float(attainment), 2) if attainment is not None else None,
            "favorable_variance": float(-variance if metric.favorable == "lower" else variance),
        })
    return rows


def parse_plan_month(raw: Any) -> date:
    try:
        year_text, month_text = str(raw or "").split("-", 1)
        value = date(int(year_text), int(month_text), 1)
    except (TypeError, ValueError) as error:
        raise ValueError("Выберите корректный месяц плана") from error
    if value < PLAN_FACT_START:
        raise ValueError("План–факт доступен с августа 2026 года")
    return value


class PlanFactRepository:
    def __init__(self, env_path: str = ".env", connection_factory: Optional[Callable[[], Any]] = None) -> None:
        self.env_path = env_path
        self._connection_factory = connection_factory

    def _database(self):
        if self._connection_factory:
            return self._connection_factory()
        env = load_env(self.env_path)
        return secure_connect(
            host=os.environ.get("AMO_DB_HOST_OVERRIDE", env["AMO_DB_HOST"]),
            port=int(env.get("AMO_DB_PORT", "3306")),
            user=env["AMO_DB_USER"], password=env["AMO_DB_PASSWORD"],
            database=env["AMO_DB_NAME"], charset="utf8mb4", autocommit=False,
            ssl=tls_context(env), connect_timeout=int(env.get("AMO_DB_CONNECT_TIMEOUT", "10")),
            read_timeout=60, write_timeout=30, cursorclass=pymysql.cursors.DictCursor,
        )

    @staticmethod
    def _schema_error(error: Exception) -> Exception:
        if isinstance(error, pymysql.MySQLError) and error.args and error.args[0] == 1146:
            return PlanFactSchemaMissing("Раздел план–факта ещё не инициализирован. Примените plan_fact_schema.sql.")
        return error

    @staticmethod
    def _materialize(row: Mapping[str, Any], values: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "id": int(row["id"]), "plan_month": row["plan_month"].strftime("%Y-%m"),
            "branch": row["branch"], "direction": row["direction"],
            "formula_version": row.get("formula_version") or FORMULA_VERSION,
            "created_by": row.get("created_by") or "", "updated_by": row.get("updated_by") or "",
            "updated_at": row["updated_at"].isoformat(sep=" ", timespec="minutes"),
            "values": {code: float(values.get(code) or 0) for code in METRIC_BY_CODE},
        }

    def list_plans(self, month: date) -> List[Dict[str, Any]]:
        connection = self._database()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM plan_fact_plans WHERE plan_month=%s ORDER BY branch,direction", (month,))
                plan_rows = cursor.fetchall()
                if not plan_rows:
                    return []
                ids = [int(row["id"]) for row in plan_rows]
                placeholders = ",".join(["%s"] * len(ids))
                cursor.execute(f"SELECT plan_id,metric_code,metric_value FROM plan_fact_values WHERE plan_id IN ({placeholders})", ids)
                values: Dict[int, Dict[str, Any]] = defaultdict(dict)
                for row in cursor.fetchall():
                    values[int(row["plan_id"])][row["metric_code"]] = row["metric_value"]
                return [self._materialize(row, values[int(row["id"])]) for row in plan_rows]
        except Exception as error:
            mapped = self._schema_error(error)
            if mapped is error:
                raise
            raise mapped from error
        finally:
            connection.close()

    def _snapshot(self, cursor: Any, plan_id: int) -> Optional[Dict[str, Any]]:
        cursor.execute("SELECT * FROM plan_fact_plans WHERE id=%s", (plan_id,))
        row = cursor.fetchone()
        if not row:
            return None
        cursor.execute("SELECT metric_code,metric_value FROM plan_fact_values WHERE plan_id=%s", (plan_id,))
        return self._materialize(row, {item["metric_code"]: item["metric_value"] for item in cursor.fetchall()})

    def save_plan(self, payload: Mapping[str, Any], *, branches: Iterable[str], user: str, plan_id: Optional[int] = None) -> Dict[str, Any]:
        month = parse_plan_month(payload.get("plan_month"))
        branch = str(payload.get("branch") or "").strip()
        direction = str(payload.get("direction") or "").strip()
        if branch not in set(branches):
            raise ValueError("Выберите филиал")
        if direction not in DIRECTIONS:
            raise ValueError("Выберите направление")
        raw_values = payload.get("values")
        if not isinstance(raw_values, Mapping):
            raise ValueError("Не удалось прочитать показатели плана")
        values = calculate_plan_values(raw_values)
        connection = self._database()
        try:
            with connection.cursor() as cursor:
                old = None
                if plan_id is not None:
                    old = self._snapshot(cursor, plan_id)
                    if not old:
                        raise ValueError("План не найден")
                    cursor.execute(
                        "UPDATE plan_fact_plans SET plan_month=%s,branch=%s,direction=%s,formula_version=%s,updated_by=%s WHERE id=%s",
                        (month, branch, direction, FORMULA_VERSION, user, plan_id),
                    )
                    saved_id = plan_id
                    action = "updated"
                else:
                    cursor.execute("SELECT id FROM plan_fact_plans WHERE plan_month=%s AND branch=%s AND direction=%s FOR UPDATE", (month, branch, direction))
                    existing = cursor.fetchone()
                    if existing:
                        saved_id = int(existing["id"])
                        old = self._snapshot(cursor, saved_id)
                        cursor.execute("UPDATE plan_fact_plans SET formula_version=%s,updated_by=%s WHERE id=%s", (FORMULA_VERSION, user, saved_id))
                        action = "updated"
                    else:
                        cursor.execute(
                            "INSERT INTO plan_fact_plans (plan_month,branch,direction,formula_version,created_by,updated_by) VALUES (%s,%s,%s,%s,%s,%s)",
                            (month, branch, direction, FORMULA_VERSION, user, user),
                        )
                        saved_id = int(cursor.lastrowid)
                        action = "created"
                cursor.execute("DELETE FROM plan_fact_values WHERE plan_id=%s", (saved_id,))
                cursor.executemany(
                    "INSERT INTO plan_fact_values (plan_id,metric_code,metric_value,value_source) VALUES (%s,%s,%s,%s)",
                    [(saved_id, metric.code, values[metric.code], metric.source) for metric in METRICS],
                )
                saved = self._snapshot(cursor, saved_id)
                cursor.execute(
                    "INSERT INTO plan_fact_plan_audit (plan_id,action,old_json,new_json,changed_by) VALUES (%s,%s,%s,%s,%s)",
                    (saved_id, action, json.dumps(old, ensure_ascii=False, default=str) if old else None, json.dumps(saved, ensure_ascii=False, default=str), user),
                )
            connection.commit()
            return saved or {}
        except Exception as error:
            connection.rollback()
            if isinstance(error, ValueError):
                raise
            if isinstance(error, pymysql.IntegrityError) and error.args and error.args[0] == 1062:
                raise ValueError("План для выбранных месяца, филиала и направления уже существует") from error
            mapped = self._schema_error(error)
            if mapped is error:
                raise
            raise mapped from error
        finally:
            connection.close()

    def delete_plan(self, plan_id: int, *, user: str) -> Dict[str, Any]:
        connection = self._database()
        try:
            with connection.cursor() as cursor:
                old = self._snapshot(cursor, plan_id)
                if not old:
                    raise ValueError("План не найден")
                deleted = {**old, "deleted": True}
                cursor.execute("INSERT INTO plan_fact_plan_audit (plan_id,action,old_json,new_json,changed_by) VALUES (%s,'deleted',%s,%s,%s)", (plan_id, json.dumps(old, ensure_ascii=False, default=str), json.dumps(deleted, ensure_ascii=False, default=str), user))
                cursor.execute("DELETE FROM plan_fact_plans WHERE id=%s", (plan_id,))
            connection.commit()
            return deleted
        except Exception as error:
            connection.rollback()
            if isinstance(error, ValueError):
                raise
            mapped = self._schema_error(error)
            if mapped is error:
                raise
            raise mapped from error
        finally:
            connection.close()

    def history(self, plan_id: int) -> List[Dict[str, Any]]:
        connection = self._database()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT id,action,old_json,new_json,changed_by,changed_at FROM plan_fact_plan_audit WHERE plan_id=%s ORDER BY changed_at DESC,id DESC", (plan_id,))
                return [{"id": int(row["id"]), "action": row["action"], "old": json.loads(row["old_json"]) if row.get("old_json") else None, "new": json.loads(row["new_json"]), "changed_by": row["changed_by"], "changed_at": row["changed_at"].isoformat(sep=" ", timespec="minutes")} for row in cursor.fetchall()]
        except Exception as error:
            mapped = self._schema_error(error)
            if mapped is error:
                raise
            raise mapped from error
        finally:
            connection.close()

    def sync_booking_events(self, candidates: Sequence[Mapping[str, Any]]) -> None:
        if not candidates:
            return
        connection = self._database()
        try:
            with connection.cursor() as cursor:
                statement = """
                        INSERT INTO plan_fact_booking_events (
                            amo_lead_id,first_booking_date,source,branch,direction,
                            first_yclients_company_id,first_yclients_record_id,
                            current_yclients_company_id,current_yclients_record_id,
                            reconstruction_quality
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON DUPLICATE KEY UPDATE
                            current_yclients_company_id=VALUES(current_yclients_company_id),
                            current_yclients_record_id=VALUES(current_yclients_record_id),
                            source=IF(source='Не определено' AND VALUES(source)<>'Не определено',VALUES(source),source),
                            branch=IF(branch='Не определено' AND VALUES(branch)<>'Не определено',VALUES(branch),branch),
                            direction=IF(direction='Не определено' AND VALUES(direction)<>'Не определено',VALUES(direction),direction),
                            last_seen_at=CURRENT_TIMESTAMP(3)
                        """
                rows = [
                    (
                            row["amo_lead_id"], row["first_booking_date"], row["source"], row["branch"], row["direction"],
                            row.get("yclients_company_id"), row.get("yclients_record_id"), row.get("yclients_company_id"), row.get("yclients_record_id"), row["reconstruction_quality"],
                    )
                    for row in candidates
                ]
                for index in range(0, len(rows), 400):
                    cursor.executemany(statement, rows[index:index + 400])
            connection.commit()
        except Exception as error:
            try:
                connection.rollback()
            except Exception:
                pass
            mapped = self._schema_error(error)
            if mapped is error:
                raise
            raise mapped from error
        finally:
            connection.close()

    def booking_events(self, start: date, end: date) -> List[Dict[str, Any]]:
        connection = self._database()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM plan_fact_booking_events WHERE first_booking_date BETWEEN %s AND %s ORDER BY first_booking_date,amo_lead_id", (start, end))
                return [dict(row) for row in cursor.fetchall()]
        except Exception as error:
            mapped = self._schema_error(error)
            if mapped is error:
                raise
            raise mapped from error
        finally:
            connection.close()

    def all_record_events(self) -> List[Dict[str, Any]]:
        connection = self._database()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM plan_fact_booking_events WHERE current_yclients_record_id IS NOT NULL")
                return [dict(row) for row in cursor.fetchall()]
        except Exception as error:
            mapped = self._schema_error(error)
            if mapped is error:
                raise
            raise mapped from error
        finally:
            connection.close()

    def sync_issues(self, month: date, issues: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
        connection = self._database()
        try:
            with connection.cursor() as cursor:
                active_keys = []
                for issue in issues:
                    key = str(issue["issue_key"])
                    active_keys.append(key)
                    cursor.execute(
                        """
                        INSERT INTO plan_fact_data_issues (
                            issue_key,issue_month,issue_type,severity,amo_lead_id,
                            yclients_company_id,yclients_record_id,source,branch,direction,
                            details,payload_json
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON DUPLICATE KEY UPDATE severity=VALUES(severity),
                            yclients_company_id=VALUES(yclients_company_id),
                            yclients_record_id=VALUES(yclients_record_id),
                            source=VALUES(source),branch=VALUES(branch),direction=VALUES(direction),
                            details=VALUES(details),payload_json=VALUES(payload_json),
                            last_seen_at=CURRENT_TIMESTAMP(3),resolved_at=NULL
                        """,
                        (key, month, issue["issue_type"], issue["severity"], issue.get("amo_lead_id"), issue.get("yclients_company_id"), issue.get("yclients_record_id"), issue.get("source"), issue.get("branch"), issue.get("direction"), issue["details"], json.dumps(issue, ensure_ascii=False, default=str)),
                    )
                if active_keys:
                    placeholders = ",".join(["%s"] * len(active_keys))
                    cursor.execute(f"UPDATE plan_fact_data_issues SET resolved_at=CURRENT_TIMESTAMP(3) WHERE issue_month=%s AND resolved_at IS NULL AND issue_key NOT IN ({placeholders})", [month, *active_keys])
                else:
                    cursor.execute("UPDATE plan_fact_data_issues SET resolved_at=CURRENT_TIMESTAMP(3) WHERE issue_month=%s AND resolved_at IS NULL", (month,))
                cursor.execute("SELECT * FROM plan_fact_data_issues WHERE issue_month=%s ORDER BY resolved_at IS NOT NULL,severity='warning',first_seen_at", (month,))
                rows = cursor.fetchall()
            connection.commit()
            return [self._issue_dict(row) for row in rows]
        except Exception as error:
            connection.rollback()
            mapped = self._schema_error(error)
            if mapped is error:
                raise
            raise mapped from error
        finally:
            connection.close()

    @staticmethod
    def _issue_dict(row: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "issue_key": row["issue_key"], "issue_type": row["issue_type"], "severity": row["severity"],
            "amo_lead_id": int(row["amo_lead_id"]) if row.get("amo_lead_id") else None,
            "yclients_company_id": int(row["yclients_company_id"]) if row.get("yclients_company_id") else None,
            "yclients_record_id": int(row["yclients_record_id"]) if row.get("yclients_record_id") else None,
            "source": row.get("source") or "", "branch": row.get("branch") or "", "direction": row.get("direction") or "",
            "details": row["details"],
            "first_seen_at": row["first_seen_at"].isoformat(sep=" ", timespec="minutes"),
            "last_seen_at": row["last_seen_at"].isoformat(sep=" ", timespec="minutes"),
            "resolved_at": row["resolved_at"].isoformat(sep=" ", timespec="minutes") if row.get("resolved_at") else None,
        }


def _issue(issue_type: str, severity: str, details: str, row: Mapping[str, Any]) -> Dict[str, Any]:
    identity = f"{issue_type}|{row.get('amo_lead_id')}|{row.get('yclients_company_id')}|{row.get('yclients_record_id')}"
    return {
        "issue_key": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        "issue_type": issue_type, "severity": severity, "details": details,
        "amo_lead_id": row.get("amo_lead_id"), "yclients_company_id": row.get("yclients_company_id"),
        "yclients_record_id": row.get("yclients_record_id"), "source": row.get("source"),
        "branch": row.get("branch"), "direction": row.get("direction"),
    }


class PlanFactAnalytics:
    def __init__(self, env_path: str = ".env", repository: Optional[PlanFactRepository] = None) -> None:
        self.env_path = env_path
        self.repository = repository or PlanFactRepository(env_path)
        self.classifier = TagClassifier()
        self.yclients = YclientsAnalytics(env_path)

    def _database(self, prefix: str):
        env = load_env(self.env_path)
        kwargs: Dict[str, Any] = {"ssl": tls_context(env, prefix)}
        return secure_connect(
            host=os.environ.get(f"{prefix}_DB_HOST_OVERRIDE", env[f"{prefix}_DB_HOST"]),
            port=int(env.get(f"{prefix}_DB_PORT", "3306")), user=env[f"{prefix}_DB_USER"],
            password=env[f"{prefix}_DB_PASSWORD"], database=env[f"{prefix}_DB_NAME"],
            charset="utf8mb4", autocommit=True, connect_timeout=int(env.get(f"{prefix}_DB_CONNECT_TIMEOUT", "10")),
            read_timeout=60, cursorclass=pymysql.cursors.DictCursor, **kwargs,
        )

    def _classify(self, tags: Sequence[str], branch_name: Any = None) -> Dict[str, str]:
        result = dict(self.classifier.classify(tags, branch_name=branch_name))
        normalized = {normalize(tag) for tag in tags}
        if normalize(CALL_SOURCE) in normalized:
            result["source"] = CALL_SOURCE
        elif normalize(SPEED_DIAL_SOURCE) in normalized:
            result["source"] = SPEED_DIAL_SOURCE
        elif result["source"] == UNKNOWN and normalize(OTHER_SOURCE) in normalized:
            result["source"] = OTHER_SOURCE
        return result

    @staticmethod
    def _moscow_date(value: Any) -> Optional[date]:
        if value is None:
            return None
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        return value.replace(tzinfo=timezone.utc).astimezone(MOSCOW).date()

    def _booking_candidates(self) -> List[Dict[str, Any]]:
        connection = self._database("AMO")
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT l.amo_lead_id,l.branch_name,l.yclients_company_id,l.yclients_record_id,
                           l.appointment_transition_date,l.created_at,
                           history.first_booking_at,
                           GROUP_CONCAT(DISTINCT t.tag_name ORDER BY t.tag_name SEPARATOR '{TAG_SEPARATOR}') tag_names
                    FROM amo_leads l
                    LEFT JOIN amo_lead_tags lt ON lt.amo_lead_id=l.amo_lead_id
                    LEFT JOIN amo_tags t ON t.amo_tag_id=lt.amo_tag_id
                    LEFT JOIN (
                        SELECT h.amo_lead_id,MIN(h.changed_at) first_booking_at
                        FROM amo_lead_status_history h
                        JOIN amo_statuses s ON s.amo_status_id=h.new_status_id
                          AND (h.new_pipeline_id IS NULL OR s.amo_pipeline_id=h.new_pipeline_id)
                        WHERE LOWER(TRIM(s.status_name))='клиент записан'
                        GROUP BY h.amo_lead_id
                    ) history ON history.amo_lead_id=l.amo_lead_id
                    WHERE COALESCE(history.first_booking_at,TIMESTAMP(l.appointment_transition_date)) IS NOT NULL
                      AND NOT (l.closed_at IS NOT NULL AND COALESCE(CAST(JSON_UNQUOTE(JSON_EXTRACT(l.raw_json,'$.loss_reason_id')) AS UNSIGNED),0) IN ({EXCLUDED_LOSS_REASON_IDS_SQL}))
                    GROUP BY l.amo_lead_id,l.branch_name,l.yclients_company_id,l.yclients_record_id,
                             l.appointment_transition_date,l.created_at,history.first_booking_at
                    """
                )
                rows = cursor.fetchall()
        finally:
            connection.close()
        result = []
        for row in rows:
            booking_date = self._moscow_date(row.get("first_booking_at")) or row.get("appointment_transition_date")
            if not booking_date or booking_date < PLAN_FACT_START:
                continue
            tags = str(row.get("tag_names") or "").split(TAG_SEPARATOR)
            dimensions = self._classify(tags, row.get("branch_name"))
            result.append({
                "amo_lead_id": int(row["amo_lead_id"]), "first_booking_date": booking_date,
                "source": dimensions["source"], "branch": dimensions["branch"], "direction": dimensions["direction"],
                "yclients_company_id": int(row["yclients_company_id"]) if row.get("yclients_company_id") else None,
                "yclients_record_id": int(row["yclients_record_id"]) if row.get("yclients_record_id") else None,
                "reconstruction_quality": "exact" if row.get("first_booking_at") else "reconstructed",
            })
        return result

    def _lead_primitives(self, start: date, end: date) -> Dict[Tuple[str, str], Decimal]:
        start_utc = datetime.combine(start, datetime_time.min, tzinfo=MOSCOW).astimezone(timezone.utc).replace(tzinfo=None)
        end_utc = datetime.combine(end + timedelta(days=1), datetime_time.min, tzinfo=MOSCOW).astimezone(timezone.utc).replace(tzinfo=None)
        connection = self._database("AMO")
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT l.amo_lead_id,l.branch_name,
                           GROUP_CONCAT(DISTINCT t.tag_name ORDER BY t.tag_name SEPARATOR '{TAG_SEPARATOR}') tag_names
                    FROM amo_leads l
                    LEFT JOIN amo_lead_tags lt ON lt.amo_lead_id=l.amo_lead_id
                    LEFT JOIN amo_tags t ON t.amo_tag_id=lt.amo_tag_id
                    WHERE l.is_deleted=0 AND l.created_at >= %s AND l.created_at < %s
                      AND NOT (l.closed_at IS NOT NULL AND COALESCE(CAST(JSON_UNQUOTE(JSON_EXTRACT(l.raw_json,'$.loss_reason_id')) AS UNSIGNED),0) IN ({EXCLUDED_LOSS_REASON_IDS_SQL}))
                    GROUP BY l.amo_lead_id,l.branch_name
                    """,
                    (start_utc, end_utc),
                )
                rows = cursor.fetchall()
        finally:
            connection.close()
        result: Dict[Tuple[str, str], Decimal] = defaultdict(lambda: Decimal("0"))
        for row in rows:
            dimensions = self._classify(str(row.get("tag_names") or "").split(TAG_SEPARATOR), row.get("branch_name"))
            if dimensions["source"] in LEAD_EXCLUDED_SOURCES:
                continue
            if dimensions["branch"] == UNKNOWN or dimensions["direction"] == UNKNOWN:
                continue
            result[(dimensions["branch"], dimensions["direction"])] += 1
        return result

    def _eligible_record_events(self) -> Tuple[Dict[Tuple[int, int], Dict[str, Any]], List[Dict[str, Any]]]:
        events = self.repository.all_record_events()
        mapped = {}
        for row in events:
            if row["source"] in PRIMARY_EXCLUDED_SOURCES:
                continue
            company_id = int(row.get("current_yclients_company_id") or 0)
            record_id = int(row.get("current_yclients_record_id") or 0)
            if company_id and record_id:
                mapped[(company_id, record_id)] = row
        return mapped, events

    def _primary_rows(
        self,
        start: date,
        end: date,
        eligible: Mapping[Tuple[int, int], Mapping[str, Any]],
    ) -> Tuple[
        List[Dict[str, Any]],
        Dict[Tuple[str, str], Decimal],
        set[Tuple[int, int]],
        List[Dict[str, Any]],
    ]:
        connection = self._database("YCLIENTS")
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT r.yclients_company_id,r.yclients_record_id,r.datetime visit_at,
                           r.custom_color,cpy.title branch_title,MAX(st.name) staff_name,
                           GROUP_CONCAT(DISTINCT CONCAT_WS(' ',sc.title,rs.title) ORDER BY rs.title SEPARATOR ' | ') service_text
                    FROM records r
                    LEFT JOIN companies cpy ON cpy.yclients_company_id=r.yclients_company_id
                    LEFT JOIN staff st ON st.yclients_company_id=r.yclients_company_id AND st.yclients_staff_id=r.yclients_staff_id
                    LEFT JOIN record_services rs ON rs.yclients_company_id=r.yclients_company_id AND rs.yclients_record_id=r.yclients_record_id
                    LEFT JOIN services s ON s.yclients_company_id=rs.yclients_company_id AND s.yclients_service_id=rs.yclients_service_id
                    LEFT JOIN service_categories sc ON sc.yclients_company_id=s.yclients_company_id AND sc.yclients_category_id=s.yclients_category_id
                    WHERE r.attendance=1 AND r.is_deleted=0 AND r.datetime >= %s AND r.datetime < %s
                      AND r.datetime < NOW()
                    GROUP BY r.yclients_company_id,r.yclients_record_id,r.datetime,r.custom_color,cpy.title
                    """,
                    (datetime.combine(start, datetime.min.time()), datetime.combine(end + timedelta(days=1), datetime.min.time())),
                )
                rows = [dict(row) for row in cursor.fetchall()]
                cursor.execute(
                    """
                    SELECT pay.yclients_company_id,pay.yclients_record_id,MAX(pay.amount) amount,
                           MAX(r.custom_color) custom_color,MAX(cpy.title) branch_title,
                           GROUP_CONCAT(DISTINCT CONCAT_WS(' ',sc.title,rs.title)
                             ORDER BY rs.title SEPARATOR ' | ') service_text
                    FROM (
                      SELECT yclients_company_id,yclients_record_id,SUM(amount) amount
                      FROM record_finance_transactions
                      WHERE transaction_date >= %s AND transaction_date < %s
                        AND amount > 0 AND expense_title='Оказание услуг'
                      GROUP BY yclients_company_id,yclients_record_id
                    ) pay
                    JOIN records r
                      ON r.yclients_company_id=pay.yclients_company_id
                     AND r.yclients_record_id=pay.yclients_record_id
                    LEFT JOIN companies cpy
                      ON cpy.yclients_company_id=r.yclients_company_id
                    LEFT JOIN record_services rs
                      ON rs.yclients_company_id=r.yclients_company_id
                     AND rs.yclients_record_id=r.yclients_record_id
                    LEFT JOIN services s
                      ON s.yclients_company_id=rs.yclients_company_id
                     AND s.yclients_service_id=rs.yclients_service_id
                    LEFT JOIN service_categories sc
                      ON sc.yclients_company_id=s.yclients_company_id
                     AND sc.yclients_category_id=s.yclients_category_id
                    GROUP BY pay.yclients_company_id,pay.yclients_record_id
                    """,
                    (datetime.combine(start, datetime.min.time()), datetime.combine(end + timedelta(days=1), datetime.min.time())),
                )
                payment_rows = [dict(row) for row in cursor.fetchall()]
        finally:
            connection.close()
        visits: Dict[Tuple[str, str], Decimal] = defaultdict(lambda: Decimal("0"))
        eligible_ids: set[Tuple[int, int]] = set()
        selected = []
        issues: List[Dict[str, Any]] = []
        for row in rows:
            key = (int(row["yclients_company_id"]), int(row["yclients_record_id"]))
            event = eligible.get(key)
            if not event:
                continue
            if normalize_record_color(row.get("custom_color")) not in {"2196f3", "00bcd4"}:
                issues.append(_issue(
                    "primary_color_mismatch",
                    "warning",
                    "Связанная первичная запись отмечена цветом, который не относится к первичным визитам",
                    {**event, **row},
                ))
                continue
            branch = normalize_branch(row.get("branch_title"))
            direction = classify_direction([row.get("service_text")])
            if branch == UNKNOWN:
                issues.append(_issue("primary_visit_branch_missing", "critical", "Не удалось определить филиал первичного визита", {**event, **row, "branch": branch, "direction": direction}))
                continue
            if direction not in DIRECTIONS:
                issues.append(_issue("primary_visit_direction_missing", "critical", "Не удалось определить направление первичного визита", {**event, **row, "branch": branch, "direction": direction}))
                continue
            item = {**row, "branch": branch, "direction": direction, "event": event}
            selected.append(item)
            visits[(branch, direction)] += 1
            eligible_ids.add(key)
        revenue: Dict[Tuple[str, str], Decimal] = defaultdict(lambda: Decimal("0"))
        # A payment belongs to its own calendar month, even when the linked
        # primary visit took place earlier. Scope is read from the actual record.
        for row in payment_rows:
            key = (int(row["yclients_company_id"]), int(row.get("yclients_record_id") or 0))
            event = eligible.get(key)
            if not event or normalize_record_color(row.get("custom_color")) not in {"2196f3", "00bcd4"}:
                continue
            branch = normalize_branch(row.get("branch_title"))
            direction = classify_direction([row.get("service_text")])
            if branch == UNKNOWN or direction not in DIRECTIONS:
                continue
            revenue[(branch, direction)] += Decimal(str(row.get("amount") or 0))
        return selected, revenue, eligible_ids, issues

    def _subscription_primitives(self, start: date, end: date, eligible: Mapping[Tuple[int, int], Mapping[str, Any]]) -> Tuple[Dict[Tuple[str, str], Dict[str, Decimal]], List[Dict[str, Any]]]:
        connection = self._database("YCLIENTS")
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT f.yclients_company_id,f.document_id,MIN(f.transaction_date) sold_at,
                           MAX(f.yclients_record_id) record_id,MAX(f.yclients_client_id) client_id,
                           MAX(r.datetime) visit_at,
                           COALESCE(
                             NULLIF(REGEXP_REPLACE(MAX(c.phone),'[^0-9]',''),''),
                             CASE WHEN COALESCE(MAX(f.yclients_client_id),0)>0
                               THEN CONCAT('id:',f.yclients_company_id,':',MAX(f.yclients_client_id))
                               ELSE CONCAT('document:',f.yclients_company_id,':',f.document_id)
                             END
                           ) customer_key,
                           cpy.title branch_title
                    FROM record_finance_transactions f
                    LEFT JOIN companies cpy ON cpy.yclients_company_id=f.yclients_company_id
                    LEFT JOIN clients c
                      ON c.yclients_company_id=f.yclients_company_id
                     AND c.yclients_client_id=f.yclients_client_id
                    LEFT JOIN records r
                      ON r.yclients_company_id=f.yclients_company_id
                     AND r.yclients_record_id=f.yclients_record_id
                    WHERE f.expense_title='Продажа абонементов'
                    GROUP BY f.yclients_company_id,f.document_id,cpy.title
                    ORDER BY customer_key,sold_at,f.yclients_company_id,f.document_id
                    """
                )
                documents = [dict(row) for row in cursor.fetchall()]
                cursor.execute(
                    """
                    SELECT yclients_company_id,source_document_id document_id,
                           SUM(COALESCE(cost_to_pay_total,0)) full_sum,
                           GROUP_CONCAT(DISTINCT title ORDER BY title SEPARATOR ' | ') titles
                    FROM sale_items WHERE business_type='subscription'
                    GROUP BY yclients_company_id,source_document_id
                    """
                )
                items = {(int(row["yclients_company_id"]), int(row["document_id"])): row for row in cursor.fetchall()}
                cursor.execute(
                    """
                    SELECT yclients_company_id,document_id,DATE(payment_date) payment_day,
                           SUM(amount) amount
                    FROM sale_payment_transactions
                    WHERE amount > 0 AND is_deleted=0 AND expense_title='Продажа абонементов'
                    GROUP BY yclients_company_id,document_id,DATE(payment_date)
                    """
                )
                payments = {(int(row["yclients_company_id"]), int(row["document_id"]), row["payment_day"]): Decimal(str(row["amount"] or 0)) for row in cursor.fetchall()}
                cursor.execute("SELECT yclients_company_id,yclients_record_id,custom_color FROM records")
                colors = {(int(row["yclients_company_id"]), int(row["yclients_record_id"])): str(row.get("custom_color") or "").lower().lstrip("#") for row in cursor.fetchall()}
        finally:
            connection.close()

        seen_customers: set[str] = set()
        result: Dict[Tuple[str, str], Dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
        issues = []
        for row in documents:
            company_id = int(row["yclients_company_id"])
            customer = str(row["customer_key"])
            is_subsequent = customer in seen_customers
            seen_customers.add(customer)
            sold_at = row.get("sold_at")
            if not isinstance(sold_at, datetime) or not (start <= sold_at.date() <= end):
                continue
            record_id = int(row.get("record_id") or 0)
            item = items.get((company_id, int(row["document_id"])), {})
            direction = classify_direction([item.get("titles")])
            branch = normalize_branch(row.get("branch_title"))
            scope = (branch, direction)
            if direction not in DIRECTIONS:
                issues.append(_issue("subscription_direction_missing", "critical", "Не удалось определить направление проданного абонемента", {**row, "yclients_company_id": company_id, "yclients_record_id": record_id, "branch": branch, "direction": direction}))
                continue
            visit_at = row.get("visit_at")
            first_visit_sale = (
                colors.get((company_id, record_id)) in {"2196f3", "00bcd4"}
                and (company_id, record_id) in eligible
                and isinstance(visit_at, datetime)
                and visit_at.date() == sold_at.date()
            )
            kind = "repeat" if is_subsequent else ("primary" if first_visit_sale else "")
            if not kind:
                issues.append(_issue("subscription_unclassified", "warning", "Первая продажа абонемента не связана с первичным визитом", {**row, "yclients_company_id": company_id, "yclients_record_id": record_id, "branch": branch, "direction": direction}))
                continue
            initial = payments.get((company_id, int(row["document_id"]), sold_at.date()), Decimal("0"))
            full_sum = Decimal(str(item.get("full_sum") or 0))
            result[scope][f"{kind}_subscription_count"] += 1
            result[scope][f"{kind}_subscription_initial_sum"] += initial
            result[scope][f"{kind}_subscription_full_sum"] += full_sum
            if full_sum <= 0:
                issues.append(_issue("subscription_full_sum_missing", "warning", "Не загружена итоговая стоимость абонемента после скидки", {**row, "yclients_company_id": company_id, "yclients_record_id": record_id, "branch": branch, "direction": direction}))
        return result, issues

    def _issues_for_events(self, month: date, events: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
        issues = []
        for row in events:
            # UNKNOWN source is an intentional exclusion, not an error.
            if row.get("source") == UNKNOWN:
                continue
            if row.get("branch") == UNKNOWN:
                issues.append(_issue("branch_missing", "critical", "Не определён филиал первичной сделки", row))
            if row.get("direction") == UNKNOWN:
                issues.append(_issue("direction_missing", "critical", "Не определено направление первичной сделки", row))
            if not row.get("current_yclients_record_id"):
                issues.append(_issue("yclients_link_missing", "critical", "Сделка дошла до записи, но ID записи YCLIENTS не передан", row))
        return issues

    def report(self, month: date, plans: Sequence[Mapping[str, Any]], *, branches: Sequence[str] = (), directions: Sequence[str] = ()) -> Dict[str, Any]:
        end = date(month.year, month.month, monthrange(month.year, month.month)[1])
        candidates = self._booking_candidates()
        self.repository.sync_booking_events(candidates)
        month_events = self.repository.booking_events(month, end)
        eligible, _all_events = self._eligible_record_events()
        leads = self._lead_primitives(month, end)
        primary_rows, primary_revenue, _eligible_ids, primary_issues = self._primary_rows(month, end, eligible)
        subscriptions, subscription_issues = self._subscription_primitives(month, end, eligible)
        yc_report = self.yclients.report(month, end, detail_limit=20)
        debt_rows = self.yclients._installment_services(month, end)

        primitives: Dict[Tuple[str, str], Dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
        for scope, count in leads.items():
            primitives[scope]["leads"] += count
        for event in month_events:
            if event["source"] in PRIMARY_EXCLUDED_SOURCES or event["branch"] == UNKNOWN or event["direction"] == UNKNOWN:
                continue
            primitives[(event["branch"], event["direction"])]["bookings"] += 1
        for row in primary_rows:
            primitives[(row["branch"], row["direction"])]["primary_visits"] += 1
        for scope, amount in primary_revenue.items():
            primitives[scope]["primary_visit_sum"] += amount
        for row in yc_report.get("groups", []):
            scope = (row["branch"], row["direction"])
            primitives[scope]["revenue"] += Decimal(str(row.get("revenue") or 0))
            primitives[scope]["repeat_visits"] += Decimal(str(row.get("repeat_visits") or 0))
            primitives[scope]["one_off_count"] += Decimal(str(row.get("one_off_visits") or 0))
            primitives[scope]["one_off_sum"] += Decimal(str(row.get("one_off_revenue") or 0))
        for row in debt_rows:
            scope = (row["branch"], row["direction"])
            # Match the operational report's installment total, including paid
            # contributions. The unpaid balance is a separate reconciliation field.
            primitives[scope]["receivables"] += Decimal(str(row.get("service_amount") or 0))
        for scope, values in subscriptions.items():
            for key, amount in values.items():
                primitives[scope][key] += amount

        actual_scopes = [{"branch": scope[0], "direction": scope[1], **values} for scope, values in primitives.items()]
        selected_branches = set(branches)
        selected_directions = set(directions)
        def selected(row: Mapping[str, Any]) -> bool:
            return (not selected_branches or row.get("branch") in selected_branches) and (not selected_directions or row.get("direction") in selected_directions)
        selected_actual = [row for row in actual_scopes if selected(row)]
        selected_plans = [row for row in plans if selected(row)]
        actual = aggregate_metric_values(selected_actual, actual=True)
        plan = aggregate_metric_values([row.get("values", {}) for row in selected_plans], actual=False)
        issues = self._issues_for_events(month, month_events) + primary_issues + subscription_issues
        stored_issues = self.repository.sync_issues(month, issues)
        env = load_env(self.env_path)
        subdomain = env.get("AMO_SUBDOMAIN", "").strip()
        for row in stored_issues:
            row["lead_url"] = f"https://{subdomain}.amocrm.ru/leads/detail/{row['amo_lead_id']}" if subdomain and row.get("amo_lead_id") else ""
        breakdown = []
        plan_by_scope = {(row["branch"], row["direction"]): row.get("values", {}) for row in plans}
        actual_by_scope = {(row["branch"], row["direction"]): row for row in actual_scopes}
        all_scopes = set(actual_by_scope) | set(plan_by_scope)
        for scope in sorted(all_scopes):
            scope_row = {"branch": scope[0], "direction": scope[1]}
            if not selected(scope_row):
                continue
            metrics = derive_actual_metrics(actual_by_scope.get(scope, {}))
            planned = plan_by_scope.get(scope, {})
            breakdown.append({
                "branch": scope[0], "direction": scope[1],
                "plan_configured": bool(planned),
                "revenue_plan": float(planned.get("revenue") or 0), "revenue_actual": float(metrics["revenue"]),
                "leads_plan": float(planned.get("leads") or 0), "leads_actual": float(metrics["leads"]),
                "bookings_plan": float(planned.get("bookings") or 0), "bookings_actual": float(metrics["bookings"]),
                "primary_visits_plan": float(planned.get("primary_visits") or 0), "primary_visits_actual": float(metrics["primary_visits"]),
            })
        return {
            "month": month.strftime("%Y-%m"), "metrics": comparison_rows(plan, actual),
            "breakdown": breakdown, "issues": stored_issues,
            "issue_summary": {
                "active": sum(int(not row.get("resolved_at")) for row in stored_issues),
                "critical": sum(int(not row.get("resolved_at") and row["severity"] == "critical") for row in stored_issues),
                "resolved": sum(int(bool(row.get("resolved_at"))) for row in stored_issues),
            },
            "coverage": {
                "plans": len(selected_plans), "scopes_with_actual": len(selected_actual),
                "exact_booking_events": sum(int(row["reconstruction_quality"] == "exact") for row in month_events),
                "reconstructed_booking_events": sum(int(row["reconstruction_quality"] == "reconstructed") for row in month_events),
            },
            "method": {
                "isolated": True,
                "lead_scope": "Без источников «Не определено», «Скорозвон» и «Звонок»",
                "primary_scope": "Без источника «Не определено»; «Скорозвон» и «Звонок» включены",
                "period": "Каждый этап относится к месяцу фактического события",
            },
        }
