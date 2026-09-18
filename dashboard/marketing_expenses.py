"""Historical marketing expense rules and manual period expenses."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

import pymysql
from db_security import connect as secure_connect, tls_context

from amocrm_client import load_env


MIN_EXPENSE_DATE = date(2026, 7, 1)
CPL_SOURCE_DIRECTIONS = {
    "РИС": ("Массаж", "Лазер"),
    "Флоктори": ("Массаж", "Лазер"),
    "РИС_xs": ("Массаж",),
    "Сайт Xsize": ("Массаж",),
    "SMM_xs": ("Массаж",),
}
CPL_SOURCES = tuple(CPL_SOURCE_DIRECTIONS)
PERIOD_SOURCES = ("VK", "Яндекс.Карты")
DIRECTIONS = ("Массаж", "Лазер")
PERIOD_STATUSES = ("draft", "confirmed", "cancelled")
STATUS_LABELS = {
    "draft": "Черновик",
    "confirmed": "Подтверждено",
    "cancelled": "Отменено",
}
MONEY_STEP = Decimal("0.01")


class MarketingSchemaMissing(RuntimeError):
    """Raised when the marketing expense migration was not applied."""


@dataclass(frozen=True)
class PeriodExpenseInput:
    source: str
    branch: Optional[str]
    direction: Optional[str]
    period_from: date
    period_to: date
    amount: Decimal
    status: str
    comment: str


def _parse_date(value: object, label: str) -> date:
    try:
        parsed = date.fromisoformat(str(value or ""))
    except ValueError as error:
        raise ValueError(f"Поле «{label}» содержит некорректную дату") from error
    if parsed < MIN_EXPENSE_DATE:
        raise ValueError(
            f"Дата в поле «{label}» не может быть раньше "
            f"{MIN_EXPENSE_DATE.strftime('%d.%m.%Y')}"
        )
    return parsed


def _parse_money(value: object, label: str) -> Decimal:
    normalized = str(value or "").strip().replace(" ", "").replace(",", ".")
    try:
        amount = Decimal(normalized).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"Поле «{label}» должно содержать сумму") from error
    if amount <= 0:
        raise ValueError(f"Поле «{label}» должно быть больше нуля")
    if amount > Decimal("999999999999.99"):
        raise ValueError(f"Сумма в поле «{label}» слишком большая")
    return amount


def _comment(value: object) -> str:
    result = str(value or "").strip()
    if len(result) > 1000:
        raise ValueError("Комментарий не может быть длиннее 1000 символов")
    return result


def validate_rate_input(payload: Mapping[str, Any]) -> Tuple[str, str, Decimal, date, str]:
    source = str(payload.get("source") or "").strip()
    direction = str(payload.get("direction") or "").strip()
    if source not in CPL_SOURCES:
        raise ValueError("Выберите источник с фиксированной ценой лида")
    if direction not in CPL_SOURCE_DIRECTIONS[source]:
        allowed = " или ".join(CPL_SOURCE_DIRECTIONS[source])
        raise ValueError(f"Для источника {source} доступно направление: {allowed}")
    cost = _parse_money(payload.get("cost_per_lead"), "Цена лида")
    valid_from = _parse_date(payload.get("valid_from"), "Действует с")
    return source, direction, cost, valid_from, _comment(payload.get("comment"))


def validate_period_input(
    payload: Mapping[str, Any], *, branches: Iterable[str]
) -> PeriodExpenseInput:
    source = str(payload.get("source") or "").strip()
    if source not in PERIOD_SOURCES:
        raise ValueError("Расход за период можно задать только для VK или Яндекс.Карт")
    period_from = _parse_date(payload.get("period_from"), "Период с")
    period_to = _parse_date(payload.get("period_to"), "Период по")
    if period_to < period_from:
        raise ValueError("Дата окончания расхода не может быть раньше даты начала")
    if (period_to - period_from).days > 366:
        raise ValueError("Один расход не может охватывать период длиннее года")
    amount = _parse_money(payload.get("amount"), "Расход")
    status = str(payload.get("status") or "draft").strip().lower()
    if status not in {"draft", "confirmed"}:
        raise ValueError("Новый расход можно сохранить как черновик или подтвердить")

    if source == "VK":
        branch = str(payload.get("branch") or "").strip()
        direction = str(payload.get("direction") or "").strip()
        if branch not in set(branches):
            raise ValueError("Выберите филиал рекламного кабинета VK")
        if direction not in DIRECTIONS:
            raise ValueError("Выберите направление кабинета VK")
    else:
        branch = None
        direction = None

    return PeriodExpenseInput(
        source=source,
        branch=branch,
        direction=direction,
        period_from=period_from,
        period_to=period_to,
        amount=amount,
        status=status,
        comment=_comment(payload.get("comment")),
    )


def calculate_period_preview(
    records: Iterable[Any], expense: PeriodExpenseInput
) -> Dict[str, Any]:
    matched_by_tags = [
        record
        for record in records
        if expense.period_from <= record.created_date <= expense.period_to
        and record.source == expense.source
        and (expense.branch is None or record.branch == expense.branch)
        and (expense.direction is None or record.direction == expense.direction)
    ]
    matched = [
        record
        for record in matched_by_tags
        if not getattr(record, "excluded_reason", None)
    ]
    excluded_count = len(matched_by_tags) - len(matched)
    count = len(matched)
    cpl = (
        (expense.amount / count).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)
        if count
        else None
    )
    return {
        "raw_leads": len(matched_by_tags),
        "excluded_leads": excluded_count,
        "matched_leads": count,
        "calculated_cpl": float(cpl) if cpl is not None else None,
        "amount": float(expense.amount),
        "unallocated": count == 0,
    }


class MarketingExpenseRepository:
    """MySQL persistence for expense rules. Schema changes are applied separately."""

    def __init__(
        self,
        env_path: str = ".env",
        connection_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        self.env_path = env_path
        self._connection_factory = connection_factory

    def _database(self):
        if self._connection_factory:
            return self._connection_factory()
        env = load_env(self.env_path)
        return secure_connect(
            host=os.environ.get("AMO_DB_HOST_OVERRIDE", env["AMO_DB_HOST"]),
            port=int(env.get("AMO_DB_PORT", "3306")),
            user=env["AMO_DB_USER"],
            password=env["AMO_DB_PASSWORD"],
            database=env["AMO_DB_NAME"],
            charset="utf8mb4",
            autocommit=False,
            ssl=tls_context(env),
            connect_timeout=int(env.get("AMO_DB_CONNECT_TIMEOUT", "10")),
            read_timeout=45,
            write_timeout=30,
            cursorclass=pymysql.cursors.DictCursor,
        )

    @staticmethod
    def _schema_error(error: Exception) -> Exception:
        if isinstance(error, pymysql.MySQLError) and error.args and error.args[0] == 1146:
            return MarketingSchemaMissing(
                "Раздел расходов ещё не инициализирован в базе. "
                "Примените marketing_expenses_schema.sql."
            )
        return error

    @staticmethod
    def _rate_dict(row: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "id": int(row["id"]),
            "source": row["source"],
            "direction": row["direction"],
            "cost_per_lead": float(row["cost_per_lead"]),
            "valid_from": row["valid_from"].isoformat(),
            "valid_to": row["valid_to"].isoformat() if row.get("valid_to") else None,
            "comment": row.get("comment") or "",
            "created_by": row.get("created_by") or "",
            "created_at": row["created_at"].isoformat(sep=" ", timespec="minutes"),
        }

    @staticmethod
    def _expense_dict(row: Mapping[str, Any]) -> Dict[str, Any]:
        status = row["status"]
        return {
            "id": int(row["id"]),
            "source": row["source"],
            "branch": row.get("branch"),
            "direction": row.get("direction"),
            "period_from": row["period_from"].isoformat(),
            "period_to": row["period_to"].isoformat(),
            "amount": float(row["amount"]),
            "status": status,
            "status_label": STATUS_LABELS.get(status, status),
            "comment": row.get("comment") or "",
            "created_by": row.get("created_by") or "",
            "created_at": row["created_at"].isoformat(sep=" ", timespec="minutes"),
            "updated_at": row["updated_at"].isoformat(sep=" ", timespec="minutes"),
        }

    @staticmethod
    def _audit(
        cursor: Any,
        *,
        entity_type: str,
        entity_id: int,
        action: str,
        old_value: Optional[Mapping[str, Any]],
        new_value: Optional[Mapping[str, Any]],
        user: str,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO marketing_expense_audit (
                entity_type, entity_id, action, old_json, new_json, changed_by
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                entity_type,
                entity_id,
                action,
                json.dumps(old_value, ensure_ascii=False, default=str) if old_value else None,
                json.dumps(new_value, ensure_ascii=False, default=str) if new_value else None,
                user,
            ),
        )

    def list_rates(self) -> List[Dict[str, Any]]:
        connection = self._database()
        try:
            with connection.cursor() as cursor:
                cursor.execute("START TRANSACTION READ ONLY")
                cursor.execute(
                    """
                    SELECT id, source, direction, cost_per_lead, valid_from,
                           valid_to, comment, created_by, created_at
                    FROM marketing_cpl_rates
                    ORDER BY source, direction, valid_from DESC, id DESC
                    """
                )
                rows = cursor.fetchall()
            connection.rollback()
            return [self._rate_dict(row) for row in rows]
        except Exception as error:
            connection.rollback()
            mapped = self._schema_error(error)
            if mapped is error:
                raise
            raise mapped from error
        finally:
            connection.close()

    def add_rate(
        self, payload: Mapping[str, Any], *, user: str
    ) -> Dict[str, Any]:
        source, direction, cost, valid_from, comment = validate_rate_input(payload)
        connection = self._database()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, source, direction, cost_per_lead, valid_from,
                           valid_to, comment, created_by, created_at
                    FROM marketing_cpl_rates
                    WHERE source = %s AND direction = %s
                    ORDER BY valid_from
                    FOR UPDATE
                    """,
                    (source, direction),
                )
                rows = cursor.fetchall()
                if any(row["valid_from"] == valid_from for row in rows):
                    raise ValueError(
                        "Для этого источника и направления уже есть тариф с такой датой начала"
                    )
                next_row = next(
                    (row for row in rows if row["valid_from"] > valid_from), None
                )
                previous = next(
                    (row for row in reversed(rows) if row["valid_from"] < valid_from), None
                )
                if previous and (
                    previous.get("valid_to") is None
                    or previous["valid_to"] >= valid_from
                ):
                    old_previous = self._rate_dict(previous)
                    new_valid_to = valid_from - timedelta(days=1)
                    cursor.execute(
                        "UPDATE marketing_cpl_rates SET valid_to = %s WHERE id = %s",
                        (new_valid_to, previous["id"]),
                    )
                    changed_previous = dict(old_previous)
                    changed_previous["valid_to"] = new_valid_to.isoformat()
                    self._audit(
                        cursor,
                        entity_type="cpl_rate",
                        entity_id=int(previous["id"]),
                        action="closed_by_new_rate",
                        old_value=old_previous,
                        new_value=changed_previous,
                        user=user,
                    )
                valid_to = (
                    next_row["valid_from"] - timedelta(days=1)
                    if next_row
                    else None
                )
                cursor.execute(
                    """
                    INSERT INTO marketing_cpl_rates (
                        source, direction, cost_per_lead, valid_from,
                        valid_to, comment, created_by
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (source, direction, cost, valid_from, valid_to, comment, user),
                )
                rate_id = int(cursor.lastrowid)
                cursor.execute(
                    """
                    SELECT id, source, direction, cost_per_lead, valid_from,
                           valid_to, comment, created_by, created_at
                    FROM marketing_cpl_rates WHERE id = %s
                    """,
                    (rate_id,),
                )
                created = self._rate_dict(cursor.fetchone())
                self._audit(
                    cursor,
                    entity_type="cpl_rate",
                    entity_id=rate_id,
                    action="created",
                    old_value=None,
                    new_value=created,
                    user=user,
                )
            connection.commit()
            return created
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

    def list_period_expenses(
        self, start: date, end: date
    ) -> List[Dict[str, Any]]:
        connection = self._database()
        try:
            with connection.cursor() as cursor:
                cursor.execute("START TRANSACTION READ ONLY")
                cursor.execute(
                    """
                    SELECT id, source, branch, direction, period_from, period_to,
                           amount, status, comment, created_by, created_at, updated_at
                    FROM marketing_period_expenses
                    WHERE period_from <= %s AND period_to >= %s
                    ORDER BY period_from DESC, source, branch, direction, id DESC
                    """,
                    (end, start),
                )
                rows = cursor.fetchall()
            connection.rollback()
            return [self._expense_dict(row) for row in rows]
        except Exception as error:
            connection.rollback()
            mapped = self._schema_error(error)
            if mapped is error:
                raise
            raise mapped from error
        finally:
            connection.close()

    def add_period_expense(
        self,
        payload: Mapping[str, Any],
        *,
        branches: Iterable[str],
        user: str,
    ) -> Dict[str, Any]:
        expense = validate_period_input(payload, branches=branches)
        connection = self._database()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id
                    FROM marketing_period_expenses
                    WHERE source = %s
                      AND branch <=> %s
                      AND direction <=> %s
                      AND status <> 'cancelled'
                      AND period_from <= %s
                      AND period_to >= %s
                    FOR UPDATE
                    """,
                    (
                        expense.source,
                        expense.branch,
                        expense.direction,
                        expense.period_to,
                        expense.period_from,
                    ),
                )
                if cursor.fetchone():
                    raise ValueError(
                        "Для этого источника, филиала и направления уже есть "
                        "расход, пересекающийся с выбранным периодом"
                    )
                cursor.execute(
                    """
                    INSERT INTO marketing_period_expenses (
                        source, branch, direction, period_from, period_to,
                        amount, status, comment, created_by
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        expense.source,
                        expense.branch,
                        expense.direction,
                        expense.period_from,
                        expense.period_to,
                        expense.amount,
                        expense.status,
                        expense.comment,
                        user,
                    ),
                )
                expense_id = int(cursor.lastrowid)
                cursor.execute(
                    """
                    SELECT id, source, branch, direction, period_from, period_to,
                           amount, status, comment, created_by, created_at, updated_at
                    FROM marketing_period_expenses WHERE id = %s
                    """,
                    (expense_id,),
                )
                created = self._expense_dict(cursor.fetchone())
                self._audit(
                    cursor,
                    entity_type="period_expense",
                    entity_id=expense_id,
                    action="created",
                    old_value=None,
                    new_value=created,
                    user=user,
                )
            connection.commit()
            return created
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

    def set_period_status(
        self, expense_id: int, status: str, *, user: str
    ) -> Dict[str, Any]:
        if status not in {"confirmed", "cancelled"}:
            raise ValueError("Расход можно подтвердить или отменить")
        connection = self._database()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, source, branch, direction, period_from, period_to,
                           amount, status, comment, created_by, created_at, updated_at
                    FROM marketing_period_expenses WHERE id = %s FOR UPDATE
                    """,
                    (expense_id,),
                )
                row = cursor.fetchone()
                if not row:
                    raise ValueError("Расход не найден")
                old_value = self._expense_dict(row)
                current = row["status"]
                if current == "cancelled":
                    raise ValueError("Отменённый расход нельзя изменить")
                if status == "confirmed" and current != "draft":
                    raise ValueError("Подтвердить можно только черновик")
                cursor.execute(
                    "UPDATE marketing_period_expenses SET status = %s WHERE id = %s",
                    (status, expense_id),
                )
                cursor.execute(
                    """
                    SELECT id, source, branch, direction, period_from, period_to,
                           amount, status, comment, created_by, created_at, updated_at
                    FROM marketing_period_expenses WHERE id = %s
                    """,
                    (expense_id,),
                )
                updated = self._expense_dict(cursor.fetchone())
                self._audit(
                    cursor,
                    entity_type="period_expense",
                    entity_id=expense_id,
                    action=status,
                    old_value=old_value,
                    new_value=updated,
                    user=user,
                )
            connection.commit()
            return updated
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
