"""Monthly budget persistence and actual-spend calculations."""

from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import pymysql
from db_security import connect as secure_connect, tls_context

from amocrm_client import load_env


BUDGET_SOURCES = ("VK", "РИС", "Флоктори", "РИС_xs")
BUDGET_DIRECTIONS = ("Массаж", "Лазер")
FIXED_RATE_BUDGET_SOURCES = {"РИС", "Флоктори", "РИС_xs"}
BUDGET_SOURCE_BRANCHES = {
    "РИС_xs": {"Академическая", "Невский", "Комендантский"},
}
BUDGET_SOURCE_DIRECTIONS = {
    "РИС_xs": {"Массаж"},
}
MONEY_STEP = Decimal("0.01")


class BudgetSchemaMissing(RuntimeError):
    """Raised when the monthly budget migration has not been applied."""


@dataclass(frozen=True)
class BudgetInput:
    source: str
    branch: str
    direction: str
    budget_month: date
    amount: Decimal


def _money(value: Decimal) -> float:
    return float(value.quantize(MONEY_STEP, rounding=ROUND_HALF_UP))


def _month(value: object) -> date:
    text = str(value or "").strip()
    try:
        year_text, month_text = text.split("-", 1)
        result = date(int(year_text), int(month_text), 1)
    except (TypeError, ValueError) as error:
        raise ValueError("Выберите корректный месяц бюджета") from error
    if result < date(2026, 7, 1):
        raise ValueError("Планирование доступно начиная с июля 2026 года")
    return result


def _amount(value: object) -> Decimal:
    normalized = str(value or "").strip().replace(" ", "").replace(",", ".")
    try:
        result = Decimal(normalized).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as error:
        raise ValueError("Поле «Бюджет» должно содержать сумму") from error
    if result <= 0:
        raise ValueError("Бюджет должен быть больше нуля")
    if result > Decimal("999999999999.99"):
        raise ValueError("Сумма бюджета слишком большая")
    return result


def validate_budget_input(
    payload: Mapping[str, Any], *, branches: Iterable[str]
) -> BudgetInput:
    source = str(payload.get("source") or "").strip()
    branch = str(payload.get("branch") or "").strip()
    direction = str(payload.get("direction") or "").strip()
    if source not in BUDGET_SOURCES:
        raise ValueError("Выберите источник: VK, РИС, Флоктори или РИС_xs")
    if branch not in set(branches):
        raise ValueError("Выберите филиал")
    if direction not in BUDGET_DIRECTIONS:
        raise ValueError("Выберите направление: Массаж или Лазер")
    allowed_branches = BUDGET_SOURCE_BRANCHES.get(source)
    if allowed_branches is not None and branch not in allowed_branches:
        raise ValueError(
            "Для РИС_xs доступны только филиалы: Академическая, Невский и Комендантский"
        )
    allowed_directions = BUDGET_SOURCE_DIRECTIONS.get(source)
    if allowed_directions is not None and direction not in allowed_directions:
        raise ValueError("Для РИС_xs доступно только направление «Массаж»")
    return BudgetInput(
        source=source,
        branch=branch,
        direction=direction,
        budget_month=_month(payload.get("budget_month")),
        amount=_amount(payload.get("amount")),
    )


def _date_value(value: object) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _rate_for(record: Any, rates: Sequence[Mapping[str, Any]]) -> Optional[Decimal]:
    for rate in rates:
        if rate.get("source") != record.source:
            continue
        if rate.get("direction") != record.direction:
            continue
        valid_from = _date_value(rate["valid_from"])
        valid_to = _date_value(rate["valid_to"]) if rate.get("valid_to") else None
        if valid_from <= record.created_date and (
            valid_to is None or record.created_date <= valid_to
        ):
            return Decimal(str(rate["cost_per_lead"]))
    return None


def _selected(value: object) -> set[str]:
    values = [value] if isinstance(value, str) else list(value or [])
    return {str(item) for item in values if item and item != "Все"}


def calculate_budget_rows(
    records: Iterable[Any],
    budgets: Iterable[Mapping[str, Any]],
    *,
    rates: Sequence[Mapping[str, Any]],
    period_expenses: Sequence[Mapping[str, Any]],
    month_start: date,
    month_end: date,
    filters: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    budget_values: Dict[Tuple[str, str, str], Decimal] = defaultdict(
        lambda: Decimal("0")
    )
    spend_values: Dict[Tuple[str, str, str], Decimal] = defaultdict(
        lambda: Decimal("0")
    )

    for budget in budgets:
        key = (str(budget["source"]), str(budget["branch"]), str(budget["direction"]))
        budget_values[key] += Decimal(str(budget["amount"]))

    for record in records:
        if record.source not in FIXED_RATE_BUDGET_SOURCES:
            continue
        rate = _rate_for(record, rates)
        if rate is not None:
            spend_values[(record.source, record.branch, record.direction)] += rate

    for expense in period_expenses:
        if expense.get("source") != "VK" or expense.get("status") != "confirmed":
            continue
        period_from = _date_value(expense["period_from"])
        period_to = _date_value(expense["period_to"])
        overlap_from = max(period_from, month_start)
        overlap_to = min(period_to, month_end)
        if overlap_to < overlap_from:
            continue
        total_days = Decimal((period_to - period_from).days + 1)
        overlap_days = Decimal((overlap_to - overlap_from).days + 1)
        allocated = Decimal(str(expense["amount"])) * overlap_days / total_days
        key = ("VK", str(expense.get("branch") or ""), str(expense.get("direction") or ""))
        spend_values[key] += allocated

    selected = {
        key: _selected((filters or {}).get(key))
        for key in ("source", "branch", "direction")
    }
    rows: List[Dict[str, Any]] = []
    for source, branch, direction in sorted(set(budget_values) | set(spend_values)):
        if selected["source"] and source not in selected["source"]:
            continue
        if selected["branch"] and branch not in selected["branch"]:
            continue
        if selected["direction"] and direction not in selected["direction"]:
            continue
        budget = budget_values[(source, branch, direction)]
        spent = spend_values[(source, branch, direction)]
        rows.append(
            {
                "key": "\x1f".join((source, branch, direction)),
                "source": source,
                "branch": branch,
                "direction": direction,
                "budget": _money(budget),
                "spent": _money(spent),
                "remaining": _money(budget - spent),
                "budget_configured": (source, branch, direction) in budget_values,
            }
        )

    total_budget = sum((Decimal(str(row["budget"])) for row in rows), Decimal("0"))
    total_spent = sum((Decimal(str(row["spent"])) for row in rows), Decimal("0"))
    return {
        "rows": rows,
        "totals": {
            "budget": _money(total_budget),
            "spent": _money(total_spent),
            "remaining": _money(total_budget - total_spent),
        },
    }


class MarketingBudgetRepository:
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
            return BudgetSchemaMissing(
                "Раздел бюджетов ещё не инициализирован в базе. "
                "Примените marketing_budgets_schema.sql."
            )
        return error

    @staticmethod
    def _dict(row: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "id": int(row["id"]),
            "source": row["source"],
            "branch": row["branch"],
            "direction": row["direction"],
            "budget_month": row["budget_month"].strftime("%Y-%m"),
            "amount": float(row["amount"]),
            "created_by": row.get("created_by") or "",
            "updated_by": row.get("updated_by") or "",
            "updated_at": row["updated_at"].isoformat(sep=" ", timespec="minutes"),
        }

    def list_budgets(self, month: date) -> List[Dict[str, Any]]:
        connection = self._database()
        try:
            with connection.cursor() as cursor:
                cursor.execute("START TRANSACTION READ ONLY")
                cursor.execute(
                    """
                    SELECT id, source, branch, direction, budget_month, amount,
                           created_by, updated_by, updated_at
                    FROM marketing_budgets
                    WHERE budget_month = %s
                    ORDER BY source, branch, direction
                    """,
                    (month,),
                )
                rows = cursor.fetchall()
            connection.rollback()
            return [self._dict(row) for row in rows]
        except Exception as error:
            connection.rollback()
            mapped = self._schema_error(error)
            if mapped is error:
                raise
            raise mapped from error
        finally:
            connection.close()

    def save_budget(
        self,
        payload: Mapping[str, Any],
        *,
        branches: Iterable[str],
        user: str,
    ) -> Dict[str, Any]:
        value = validate_budget_input(payload, branches=branches)
        connection = self._database()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, source, branch, direction, budget_month, amount,
                           created_by, updated_by, updated_at
                    FROM marketing_budgets
                    WHERE source = %s AND branch = %s AND direction = %s
                      AND budget_month = %s
                    FOR UPDATE
                    """,
                    (value.source, value.branch, value.direction, value.budget_month),
                )
                old_row = cursor.fetchone()
                old_value = self._dict(old_row) if old_row else None
                if old_row:
                    budget_id = int(old_row["id"])
                    cursor.execute(
                        """
                        UPDATE marketing_budgets
                        SET amount = %s, updated_by = %s
                        WHERE id = %s
                        """,
                        (value.amount, user, budget_id),
                    )
                    action = "updated"
                else:
                    cursor.execute(
                        """
                        INSERT INTO marketing_budgets (
                            source, branch, direction, budget_month, amount,
                            created_by, updated_by
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            value.source,
                            value.branch,
                            value.direction,
                            value.budget_month,
                            value.amount,
                            user,
                            user,
                        ),
                    )
                    budget_id = int(cursor.lastrowid)
                    action = "created"
                cursor.execute(
                    """
                    SELECT id, source, branch, direction, budget_month, amount,
                           created_by, updated_by, updated_at
                    FROM marketing_budgets WHERE id = %s
                    """,
                    (budget_id,),
                )
                saved = self._dict(cursor.fetchone())
                cursor.execute(
                    """
                    INSERT INTO marketing_budget_audit (
                        budget_id, action, old_json, new_json, changed_by
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        budget_id,
                        action,
                        json.dumps(old_value, ensure_ascii=False, default=str)
                        if old_value
                        else None,
                        json.dumps(saved, ensure_ascii=False, default=str),
                        user,
                    ),
                )
            connection.commit()
            return saved
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

    def update_budget(
        self,
        budget_id: int,
        payload: Mapping[str, Any],
        *,
        branches: Iterable[str],
        user: str,
    ) -> Dict[str, Any]:
        value = validate_budget_input(payload, branches=branches)
        connection = self._database()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, source, branch, direction, budget_month, amount,
                           created_by, updated_by, updated_at
                    FROM marketing_budgets
                    WHERE id = %s
                    FOR UPDATE
                    """,
                    (budget_id,),
                )
                old_row = cursor.fetchone()
                if not old_row:
                    raise ValueError("Запись бюджета не найдена или уже удалена")
                old_value = self._dict(old_row)
                cursor.execute(
                    """
                    UPDATE marketing_budgets
                    SET source = %s, branch = %s, direction = %s,
                        budget_month = %s, amount = %s, updated_by = %s
                    WHERE id = %s
                    """,
                    (
                        value.source,
                        value.branch,
                        value.direction,
                        value.budget_month,
                        value.amount,
                        user,
                        budget_id,
                    ),
                )
                cursor.execute(
                    """
                    SELECT id, source, branch, direction, budget_month, amount,
                           created_by, updated_by, updated_at
                    FROM marketing_budgets
                    WHERE id = %s
                    """,
                    (budget_id,),
                )
                saved = self._dict(cursor.fetchone())
                cursor.execute(
                    """
                    INSERT INTO marketing_budget_audit (
                        budget_id, action, old_json, new_json, changed_by
                    ) VALUES (%s, 'updated', %s, %s, %s)
                    """,
                    (
                        budget_id,
                        json.dumps(old_value, ensure_ascii=False, default=str),
                        json.dumps(saved, ensure_ascii=False, default=str),
                        user,
                    ),
                )
            connection.commit()
            return saved
        except Exception as error:
            connection.rollback()
            if isinstance(error, ValueError):
                raise
            if (
                isinstance(error, pymysql.IntegrityError)
                and error.args
                and error.args[0] == 1062
            ):
                raise ValueError(
                    "Для выбранных источника, филиала, направления и месяца "
                    "бюджет уже существует"
                ) from error
            mapped = self._schema_error(error)
            if mapped is error:
                raise
            raise mapped from error
        finally:
            connection.close()

    def delete_budget(self, budget_id: int, *, user: str) -> Dict[str, Any]:
        connection = self._database()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, source, branch, direction, budget_month, amount,
                           created_by, updated_by, updated_at
                    FROM marketing_budgets
                    WHERE id = %s
                    FOR UPDATE
                    """,
                    (budget_id,),
                )
                old_row = cursor.fetchone()
                if not old_row:
                    raise ValueError("Запись бюджета не найдена или уже удалена")
                old_value = self._dict(old_row)
                cursor.execute(
                    "DELETE FROM marketing_budgets WHERE id = %s",
                    (budget_id,),
                )
                deleted_value = {**old_value, "deleted": True}
                cursor.execute(
                    """
                    INSERT INTO marketing_budget_audit (
                        budget_id, action, old_json, new_json, changed_by
                    ) VALUES (%s, 'deleted', %s, %s, %s)
                    """,
                    (
                        budget_id,
                        json.dumps(old_value, ensure_ascii=False, default=str),
                        json.dumps(deleted_value, ensure_ascii=False, default=str),
                        user,
                    ),
                )
            connection.commit()
            return deleted_value
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
