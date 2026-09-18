"""Read-only operational and revenue mart over the Yclients warehouse."""

from __future__ import annotations

import csv
import io
import os
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from threading import Lock
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import pymysql
from db_security import connect as secure_connect, tls_context

from amocrm_client import load_env
from .yclients_rules import (
    administrator_prepayment_direction,
    is_administrator_staff,
)


MIN_YCLIENTS_DATE = date(2023, 1, 1)
UNKNOWN = "Не определено"
MONEY_STEP = Decimal("0.01")
MOSCOW = ZoneInfo("Europe/Moscow")

BRANCH_NAMES = {
    "xs спб академическая": "Академическая",
    "xs спб комендантский проспект": "Комендантский",
    "xs спб площадь восстания": "Невский",
    "площадь восстания": "Невский",
    "спб невский": "Невский",
    "лухмановская": "Лухмановская",
    "свиблово": "Свиблово",
}

LASER_MARKERS = (
    "лазер",
    "эпиляц",
    "лэ ",
    "бикини",
    "подмыш",
    "голени",
    "малая зона",
    " зона",
    " зон",
)

MASSAGE_MARKERS = (
    "массаж",
    "xsize",
    "слим",
    "slim",
    "коррекц",
    "похуд",
    "оберт",
    "пилинг",
    "маска",
    "детокс",
    "прессотерап",
    "миостим",
    "аппаратн",
    "программ",
    "тело",
    "лица",
    "лицо",
    "спа-суш",
    "классик",
    "усиленн",
    "жиротоп",
    "хит лета",
    "полная перезагрузка",
    "преображение",
    "styx",
    "стикс",
)

INSTALLMENT_SERVICES = {
    "взнос по рассрочке (массаж)": "Массаж",
    "взнос по рассрочке (лазер)": "Лазер",
}

VISIT_TYPE_FIRST = "first"
VISIT_TYPE_ONE_OFF = "one_off"
VISIT_TYPE_REPEAT = "repeat"
FIRST_VISIT_COLORS = {"2196f3", "00bcd4"}
ONE_OFF_VISIT_COLORS = {"4caf50"}
VISIT_TYPE_LABELS = {
    VISIT_TYPE_FIRST: "Первичный",
    VISIT_TYPE_ONE_OFF: "Разовый",
    VISIT_TYPE_REPEAT: "Повторный",
}
EXPECTED_RECORD_COLORS = {
    "",
    "2196f3",
    "00bcd4",
    "4caf50",
    "ffeb3b",
    "9e9e9e",
    "c3a5d6",
    "cddc39",
    "e91e63",
}
RECORD_COLOR_LABELS = {
    "": "По умолчанию",
    "2196f3": "Синий",
    "00bcd4": "Бирюзовый",
    "4caf50": "Зелёный",
    "ffeb3b": "Жёлтый",
    "9e9e9e": "Серый",
    "c3a5d6": "Светло-сиреневый",
    "cddc39": "Лаймовый",
    "e91e63": "Розовый",
}


def normalize_record_color(value: Any) -> str:
    return str(value or "").strip().lower().lstrip("#")


def classify_visit_type(value: Any) -> str:
    color = normalize_record_color(value)
    if color in FIRST_VISIT_COLORS:
        return VISIT_TYPE_FIRST
    if color in ONE_OFF_VISIT_COLORS:
        return VISIT_TYPE_ONE_OFF
    return VISIT_TYPE_REPEAT


def record_color_label(value: Any) -> str:
    color = normalize_record_color(value)
    return RECORD_COLOR_LABELS.get(color, f"Новый цвет #{color.upper()}")


def build_color_reconciliation(
    visits: Sequence[Mapping[str, Any]],
    *,
    filters: Optional[Mapping[str, Any]] = None,
    visit_types: Sequence[str] = (),
    colors: Sequence[str] = (),
    search: str = "",
    page: int = 1,
    per_page: int = 50,
) -> Dict[str, Any]:
    """Build an auditable color-to-visit-type registry."""
    matching = [dict(row) for row in visits if _matches(row, filters or {})]
    excluded_administrator_records = sum(
        int(bool(row.get("is_administrator_record"))) for row in matching
    )
    selected = [
        row for row in matching if not row.get("is_administrator_record")
    ]
    type_counts = {
        key: sum(int(row.get("visit_type") == key) for row in selected)
        for key in (VISIT_TYPE_FIRST, VISIT_TYPE_ONE_OFF, VISIT_TYPE_REPEAT)
    }
    color_counts: Dict[str, int] = defaultdict(int)
    for row in selected:
        color_counts[normalize_record_color(row.get("record_color"))] += 1

    type_order = {
        VISIT_TYPE_FIRST: 0,
        VISIT_TYPE_ONE_OFF: 1,
        VISIT_TYPE_REPEAT: 2,
    }
    color_rows = []
    for color, count in color_counts.items():
        visit_type = classify_visit_type(color)
        color_rows.append(
            {
                "color": color,
                "color_hex": f"#{color.upper()}" if color else "",
                "color_label": record_color_label(color),
                "visit_type": visit_type,
                "visit_type_label": VISIT_TYPE_LABELS[visit_type],
                "visits": count,
                "share": round(count / len(selected) * 100, 2) if selected else 0.0,
                "is_new": color not in EXPECTED_RECORD_COLORS,
            }
        )
    color_rows.sort(
        key=lambda row: (
            type_order[row["visit_type"]],
            -row["visits"],
            row["color_label"],
        )
    )

    selected_types = {value for value in visit_types if value in VISIT_TYPE_LABELS}
    selected_colors = {
        "" if str(value).lower() == "default" else normalize_record_color(value)
        for value in colors
        if value is not None
    }
    query = search.strip().lower()[:200]
    filtered_rows = [
        row
        for row in selected
        if (not selected_types or row.get("visit_type") in selected_types)
        and (
            not selected_colors
            or normalize_record_color(row.get("record_color")) in selected_colors
        )
        and (
            not query
            or query in str(row.get("record_id") or "").lower()
            or query in str(row.get("client_id") or "").lower()
            or query in str(row.get("service_text") or "").lower()
        )
    ]
    total = len(filtered_rows)
    per_page = min(max(int(per_page), 20), 100000)
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(max(int(page), 1), pages)
    start_index = (page - 1) * per_page
    rows = filtered_rows[start_index : start_index + per_page]
    classified_total = sum(type_counts.values())
    return {
        "totals": {
            "visits": len(selected),
            "first_visits": type_counts[VISIT_TYPE_FIRST],
            "one_off_visits": type_counts[VISIT_TYPE_ONE_OFF],
            "repeat_visits": type_counts[VISIT_TYPE_REPEAT],
            "classified_total": classified_total,
            "is_balanced": classified_total == len(selected),
            "excluded_administrator_records": excluded_administrator_records,
        },
        "colors": color_rows,
        "new_colors": [row for row in color_rows if row["is_new"]],
        "rows": rows,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "pages": pages,
            "total": total,
        },
    }


def color_reconciliation_to_csv(rows: Sequence[Mapping[str, Any]]) -> bytes:
    stream = io.StringIO(newline="")
    stream.write("\ufeff")
    writer = csv.writer(stream, delimiter=";")
    writer.writerow(
        [
            "ID записи",
            "Дата визита",
            "Филиал",
            "Направление",
            "ID клиента",
            "Цвет Yclients",
            "HEX",
            "Рассчитанный тип",
            "Списание абонемента",
            "Услуги",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row.get("record_id"),
                row.get("visit_at"),
                row.get("branch"),
                row.get("direction"),
                row.get("client_id"),
                record_color_label(row.get("record_color")),
                f"#{normalize_record_color(row.get('record_color')).upper()}"
                if normalize_record_color(row.get("record_color"))
                else "По умолчанию",
                row.get("visit_type_label"),
                "Да" if row.get("uses_subscription") else "Нет",
                row.get("service_text"),
            ]
        )
    return stream.getvalue().encode("utf-8")


def _money(value: Decimal) -> float:
    return float(value.quantize(MONEY_STEP, rounding=ROUND_HALF_UP))


def normalize_branch(value: Any) -> str:
    raw = " ".join(str(value or "").strip().split())
    normalized = raw.lower().replace("ё", "е")
    if normalized in BRANCH_NAMES:
        return BRANCH_NAMES[normalized]
    for source, target in BRANCH_NAMES.items():
        if source in normalized:
            return target
    return raw or UNKNOWN


def classify_direction(values: Iterable[Any]) -> str:
    text = " ".join(str(value or "") for value in values).lower().replace("ё", "е")
    if any(marker in text for marker in LASER_MARKERS):
        return "Лазер"
    if any(marker in text for marker in MASSAGE_MARKERS):
        return "Массаж"
    return UNKNOWN


def _selected(filters: Mapping[str, Any], key: str) -> set[str]:
    raw = filters.get(key)
    values = [raw] if isinstance(raw, str) else list(raw or [])
    return {str(value) for value in values if value and value != "Все"}


def _matches(row: Mapping[str, Any], filters: Mapping[str, Any]) -> bool:
    branches = _selected(filters, "branch")
    directions = _selected(filters, "direction")
    return (
        (not branches or row.get("branch") in branches)
        and (not directions or row.get("direction") in directions)
    )


def build_installment_debt_report(
    rows: Sequence[Mapping[str, Any]],
    *,
    filters: Optional[Mapping[str, Any]] = None,
    detail_limit: int = 300,
) -> Dict[str, Any]:
    """Build the customer's installment-based debt reconciliation block."""
    selected = [dict(row) for row in rows if _matches(row, filters or {})]
    groups: Dict[str, Dict[str, Any]] = {
        direction: {
            "direction": direction,
            "services": 0,
            "service_amount": Decimal("0"),
            "payments": 0,
            "paid_amount": Decimal("0"),
            "outstanding": Decimal("0"),
        }
        for direction in ("Массаж", "Лазер")
    }
    totals = {
        "services": 0,
        "service_amount": Decimal("0"),
        "payments": 0,
        "paid_amount": Decimal("0"),
        "outstanding": Decimal("0"),
    }

    materialized: List[Dict[str, Any]] = []
    for row in selected:
        direction = str(row.get("direction") or UNKNOWN)
        service_amount = Decimal(str(row.get("service_amount") or 0))
        paid_amount = Decimal(str(row.get("paid_amount") or 0))
        outstanding = max(service_amount - paid_amount, Decimal("0"))
        payment_count = int(row.get("payment_count") or 0)
        if paid_amount <= 0:
            payment_status = "Не оплачено"
            payment_status_key = "unpaid"
        elif paid_amount < service_amount:
            payment_status = "Частично оплачено"
            payment_status_key = "partial"
        elif paid_amount > service_amount:
            payment_status = "Переплата"
            payment_status_key = "overpaid"
        else:
            payment_status = "Оплачено"
            payment_status_key = "paid"

        item = dict(row)
        item.update(
            {
                "service_amount": _money(service_amount),
                "paid_amount": _money(paid_amount),
                "outstanding": _money(outstanding),
                "payment_count": payment_count,
                "payment_status": payment_status,
                "payment_status_key": payment_status_key,
            }
        )
        for key in ("visit_at", "first_payment_at", "last_payment_at"):
            if isinstance(item.get(key), datetime):
                item[key] = item[key].isoformat(timespec="minutes")
        materialized.append(item)

        group = groups.setdefault(
            direction,
            {
                "direction": direction,
                "services": 0,
                "service_amount": Decimal("0"),
                "payments": 0,
                "paid_amount": Decimal("0"),
                "outstanding": Decimal("0"),
            },
        )
        for target in (group, totals):
            target["services"] += 1
            target["service_amount"] += service_amount
            target["payments"] += payment_count
            target["paid_amount"] += paid_amount
            target["outstanding"] += outstanding

    materialized.sort(
        key=lambda row: (row.get("visit_at") or "", row.get("record_id") or 0),
        reverse=True,
    )

    def money_metrics(row: Mapping[str, Any]) -> Dict[str, Any]:
        result = dict(row)
        for key in ("service_amount", "paid_amount", "outstanding"):
            result[key] = _money(Decimal(str(result.get(key) or 0)))
        return result

    group_rows = [money_metrics(groups[key]) for key in ("Массаж", "Лазер")]
    return {
        "groups": group_rows,
        "totals": money_metrics(totals),
        "rows": materialized[: max(20, min(int(detail_limit), 500))],
        "row_total": len(materialized),
        "method": (
            "Стоимость услуг «Взнос по рассрочке (массаж)» и "
            "«Взнос по рассрочке (лазер)» в состоявшихся неудалённых визитах."
        ),
    }


def _empty_metrics(branch: str, direction: str) -> Dict[str, Any]:
    return {
        "branch": branch,
        "direction": direction,
        "visits": 0,
        "first_visits": 0,
        "repeat_visits": 0,
        "subscription_visits": 0,
        "one_off_visits": 0,
        "revenue": Decimal("0"),
        "service_revenue": Decimal("0"),
        "subscription_revenue": Decimal("0"),
        "other_revenue": Decimal("0"),
        "first_visit_revenue": Decimal("0"),
        "one_off_revenue": Decimal("0"),
        "subscription_sales": 0,
        "first_subscription_sales": 0,
        "repeat_subscription_sales": 0,
    }


def build_yclients_report(
    visits: Sequence[Mapping[str, Any]],
    transactions: Sequence[Mapping[str, Any]],
    subscription_sales: Sequence[Mapping[str, Any]],
    *,
    filters: Optional[Mapping[str, Any]] = None,
    detail_limit: int = 100,
) -> Dict[str, Any]:
    """Aggregate already classified rows. Kept pure for control tests."""
    active_filters = filters or {}
    matching_visits = [row for row in visits if _matches(row, active_filters)]
    excluded_administrator_visits = [
        row for row in matching_visits if row.get("is_administrator_record")
    ]
    selected_visits = [
        row for row in matching_visits if not row.get("is_administrator_record")
    ]
    selected_transactions = [
        row for row in transactions if _matches(row, active_filters)
    ]
    selected_sales = [
        row for row in subscription_sales if _matches(row, active_filters)
    ]

    grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}

    def bucket(row: Mapping[str, Any]) -> Dict[str, Any]:
        key = (str(row["branch"]), str(row["direction"]))
        if key not in grouped:
            grouped[key] = _empty_metrics(*key)
        return grouped[key]

    for row in selected_visits:
        target = bucket(row)
        target["visits"] += 1
        visit_type = str(row.get("visit_type") or VISIT_TYPE_REPEAT)
        if visit_type == VISIT_TYPE_FIRST:
            target["first_visits"] += 1
        elif visit_type == VISIT_TYPE_ONE_OFF:
            target["one_off_visits"] += 1
        else:
            target["repeat_visits"] += 1
        if row.get("uses_subscription"):
            target["subscription_visits"] += 1

    for row in selected_transactions:
        target = bucket(row)
        amount = Decimal(str(row.get("amount") or 0))
        target["revenue"] += amount
        expense_title = str(row.get("expense_title") or "")
        if expense_title == "Оказание услуг":
            target["service_revenue"] += amount
            if row.get("visit_type") == VISIT_TYPE_FIRST:
                target["first_visit_revenue"] += amount
            elif row.get("visit_type") == VISIT_TYPE_ONE_OFF:
                target["one_off_revenue"] += amount
        elif expense_title == "Продажа абонементов":
            target["subscription_revenue"] += amount
        else:
            target["other_revenue"] += amount

    for row in selected_sales:
        target = bucket(row)
        target["subscription_sales"] += 1
        target[
            "first_subscription_sales"
            if row.get("subscription_kind") == "first"
            else "repeat_subscription_sales"
        ] += 1

    group_rows: List[Dict[str, Any]] = []
    for row in grouped.values():
        materialized = dict(row)
        for key in (
            "revenue",
            "service_revenue",
            "subscription_revenue",
            "other_revenue",
            "first_visit_revenue",
            "one_off_revenue",
        ):
            materialized[key] = _money(materialized[key])
        group_rows.append(materialized)
    group_rows.sort(
        key=lambda row: (-row["revenue"], -row["visits"], row["branch"], row["direction"])
    )

    revenue = sum(
        (Decimal(str(row.get("amount") or 0)) for row in selected_transactions),
        Decimal("0"),
    )
    service_revenue = sum(
        (
            Decimal(str(row.get("amount") or 0))
            for row in selected_transactions
            if row.get("expense_title") == "Оказание услуг"
        ),
        Decimal("0"),
    )
    subscription_revenue = sum(
        (
            Decimal(str(row.get("amount") or 0))
            for row in selected_transactions
            if row.get("expense_title") == "Продажа абонементов"
        ),
        Decimal("0"),
    )
    one_off_revenue = sum(
        (
            Decimal(str(row.get("amount") or 0))
            for row in selected_transactions
            if row.get("expense_title") == "Оказание услуг"
            and row.get("visit_type") == VISIT_TYPE_ONE_OFF
        ),
        Decimal("0"),
    )
    first_visit_revenue = sum(
        (
            Decimal(str(row.get("amount") or 0))
            for row in selected_transactions
            if row.get("expense_title") == "Оказание услуг"
            and row.get("visit_type") == VISIT_TYPE_FIRST
        ),
        Decimal("0"),
    )
    first_visits = sum(
        int(row.get("visit_type") == VISIT_TYPE_FIRST) for row in selected_visits
    )
    one_off_visits = sum(
        int(row.get("visit_type") == VISIT_TYPE_ONE_OFF) for row in selected_visits
    )
    repeat_visits = sum(
        int(row.get("visit_type") == VISIT_TYPE_REPEAT) for row in selected_visits
    )
    first_subscription_sales = sum(
        int(row.get("subscription_kind") == "first") for row in selected_sales
    )
    unknown_visits = sum(
        int(row.get("direction") == UNKNOWN) for row in selected_visits
    )
    unknown_revenue = sum(
        (
            Decimal(str(row.get("amount") or 0))
            for row in selected_transactions
            if row.get("direction") == UNKNOWN
        ),
        Decimal("0"),
    )

    visit_details = sorted(
        selected_visits,
        key=lambda row: (row.get("visit_at") or "", row.get("record_id") or 0),
        reverse=True,
    )[:detail_limit]
    transaction_details = sorted(
        selected_transactions,
        key=lambda row: (
            row.get("transaction_at") or "",
            row.get("transaction_id") or 0,
        ),
        reverse=True,
    )[:detail_limit]

    return {
        "kpi": {
            "revenue": _money(revenue),
            "visits": len(selected_visits),
            "first_visits": first_visits,
            "repeat_visits": repeat_visits,
            "subscription_visits": sum(
                int(bool(row.get("uses_subscription"))) for row in selected_visits
            ),
            "subscription_sales": len(selected_sales),
            "first_subscription_sales": first_subscription_sales,
            "repeat_subscription_sales": len(selected_sales) - first_subscription_sales,
            "subscription_revenue": _money(subscription_revenue),
            "one_off_visits": one_off_visits,
            "first_visit_revenue": _money(first_visit_revenue),
            "one_off_revenue": _money(one_off_revenue),
            "service_revenue": _money(service_revenue),
        },
        "groups": group_rows,
        "details": {
            "visits": [dict(row) for row in visit_details],
            "transactions": [dict(row) for row in transaction_details],
            "visit_total": len(selected_visits),
            "transaction_total": len(selected_transactions),
        },
        "quality": {
            "unknown_visits": unknown_visits,
            "unknown_revenue": _money(unknown_revenue),
            "excluded_administrator_visits": len(excluded_administrator_visits),
        },
        "method": {
            "visits": "По дате состоявшегося визита",
            "revenue": "По дате положительной фактической оплаты",
            "visit_types": (
                "Тип визита определяется цветом записи: синий и бирюзовый — первичный, "
                "зелёный — разовый, остальные цвета и цвет по умолчанию — повторный"
            ),
            "subscription": "Первая или повторная продажа по всей истории клиента",
            "visit_payment_type": "Визит по абонементу учитывается отдельно при наличии списания",
            "administrator_records": (
                "Записи сотрудника «Администратор» не считаются приходами; "
                "предоплаты и продажи абонементов сохраняются в продажах и выручке"
            ),
            "excluded": "Дебиторка и возвраты временно не участвуют",
        },
    }


class YclientsAnalytics:
    def __init__(self, env_path: str = ".env") -> None:
        self.env_path = env_path
        self.cache_seconds = int(load_env(env_path).get("DASHBOARD_CACHE_SECONDS", "300"))
        self._cache_lock = Lock()
        self._subscription_cache: Tuple[float, Dict[Tuple[int, int], Dict[str, Any]]] = (0.0, {})

    def _database(self):
        env = load_env(self.env_path)
        kwargs: Dict[str, Any] = {"ssl": tls_context(env, "YCLIENTS")}
        return secure_connect(
            host=os.environ.get("YCLIENTS_DB_HOST_OVERRIDE", env["YCLIENTS_DB_HOST"]),
            port=int(env.get("YCLIENTS_DB_PORT", "3306")),
            user=env["YCLIENTS_DB_USER"],
            password=env["YCLIENTS_DB_PASSWORD"],
            database=env["YCLIENTS_DB_NAME"],
            charset="utf8mb4",
            autocommit=True,
            connect_timeout=int(env.get("YCLIENTS_DB_CONNECT_TIMEOUT", "10")),
            read_timeout=60,
            cursorclass=pymysql.cursors.DictCursor,
            **kwargs,
        )

    @staticmethod
    def _customer_key(row: Mapping[str, Any]) -> str:
        return str(
            row.get("customer_key")
            or f"id:{row.get('yclients_company_id')}:{row.get('yclients_client_id')}"
        )

    @staticmethod
    def _chunks(values: Sequence[int], size: int = 700):
        for index in range(0, len(values), size):
            yield values[index:index + size]

    def _subscription_documents(self) -> Dict[Tuple[int, int], Dict[str, Any]]:
        now = time.monotonic()
        with self._cache_lock:
            cached_at, cached = self._subscription_cache
            if cached and now - cached_at <= self.cache_seconds:
                return {key: dict(value) for key, value in cached.items()}
        connection = self._database()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT f.yclients_company_id, f.document_id,
                           MIN(f.transaction_date) sold_at,
                           MAX(f.yclients_client_id) yclients_client_id,
                           COALESCE(NULLIF(REGEXP_REPLACE(MAX(c.phone), '[^0-9]', ''), ''),
                             CONCAT('id:', f.yclients_company_id, ':', MAX(f.yclients_client_id))) customer_key,
                           GROUP_CONCAT(DISTINCT CASE WHEN si.business_type='subscription'
                             THEN si.title END ORDER BY si.title SEPARATOR ' | ') item_titles
                    FROM record_finance_transactions f
                    LEFT JOIN clients c
                      ON c.yclients_company_id=f.yclients_company_id
                     AND c.yclients_client_id=f.yclients_client_id
                    LEFT JOIN sale_items si
                      ON si.yclients_company_id=f.yclients_company_id
                     AND si.source_document_id=f.document_id
                    WHERE f.expense_title='Продажа абонементов'
                    GROUP BY f.yclients_company_id, f.document_id
                    ORDER BY customer_key, sold_at, f.yclients_company_id, f.document_id
                    """
                )
                rows = cursor.fetchall()
        finally:
            connection.close()
        seen: set[str] = set()
        result: Dict[Tuple[int, int], Dict[str, Any]] = {}
        for row in rows:
            customer = self._customer_key(row)
            row["subscription_kind"] = "repeat" if customer in seen else "first"
            seen.add(customer)
            row["direction"] = classify_direction([row.get("item_titles")])
            result[(int(row["yclients_company_id"]), int(row["document_id"]))] = dict(row)
        with self._cache_lock:
            self._subscription_cache = (now, result)
        return {key: dict(value) for key, value in result.items()}

    def _visits(self, start: date, end: date) -> List[Dict[str, Any]]:
        connection = self._database()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT r.yclients_company_id, r.yclients_record_id,
                           r.yclients_client_id, r.datetime visit_at,
                           r.custom_color,
                           cpy.title branch_title,
                           COALESCE(NULLIF(REGEXP_REPLACE(cl.phone, '[^0-9]', ''), ''),
                             CONCAT('id:', r.yclients_company_id, ':', r.yclients_client_id)) customer_key
                    FROM records r
                    LEFT JOIN companies cpy
                      ON cpy.yclients_company_id=r.yclients_company_id
                    LEFT JOIN clients cl
                      ON cl.yclients_company_id=r.yclients_company_id
                     AND cl.yclients_client_id=r.yclients_client_id
                    WHERE r.attendance=1 AND r.is_deleted=0
                      AND r.datetime >= %s AND r.datetime < %s
                      AND r.datetime < NOW()
                    ORDER BY r.datetime DESC, r.yclients_record_id DESC
                    """,
                    (datetime.combine(start, datetime.min.time()),
                     datetime.combine(end + timedelta(days=1), datetime.min.time())),
                )
                rows = [dict(row) for row in cursor.fetchall()]
        finally:
            connection.close()
        return rows

    def _transactions(self, start: date, end: date) -> List[Dict[str, Any]]:
        connection = self._database()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT f.yclients_company_id, f.yclients_transaction_id transaction_id,
                           f.document_id, f.yclients_record_id, f.yclients_client_id,
                           f.transaction_date transaction_at, f.expense_title,
                           f.amount, cpy.title branch_title
                    FROM record_finance_transactions f
                    LEFT JOIN companies cpy
                      ON cpy.yclients_company_id=f.yclients_company_id
                    WHERE f.transaction_date >= %s AND f.transaction_date < %s
                      AND f.amount > 0
                    ORDER BY f.transaction_date DESC, f.yclients_transaction_id DESC
                    """,
                    (datetime.combine(start, datetime.min.time()),
                     datetime.combine(end + timedelta(days=1), datetime.min.time())),
                )
                rows = [dict(row) for row in cursor.fetchall()]
        finally:
            connection.close()
        return rows

    def _installment_services(self, start: date, end: date) -> List[Dict[str, Any]]:
        """Return completed installment-contribution services and linked payments."""
        connection = self._database()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT r.yclients_company_id, r.yclients_record_id record_id,
                           r.yclients_client_id client_id, r.datetime visit_at,
                           r.status record_status, r.paid_full record_paid_full,
                           cpy.title branch_title, rs.title service_title,
                           LOWER(TRIM(rs.title)) service_key,
                           COALESCE(rs.cost, 0) * COALESCE(rs.amount, 1) service_amount,
                           COALESCE(pay.payment_count, 0) payment_count,
                           COALESCE(pay.paid_amount, 0) paid_amount,
                           pay.first_payment_at, pay.last_payment_at,
                           pay.transaction_ids
                    FROM records r
                    JOIN record_services rs
                      ON rs.yclients_company_id=r.yclients_company_id
                     AND rs.yclients_record_id=r.yclients_record_id
                    LEFT JOIN companies cpy
                      ON cpy.yclients_company_id=r.yclients_company_id
                    LEFT JOIN (
                      SELECT si.yclients_company_id, si.yclients_record_id,
                             LOWER(TRIM(si.title)) service_key,
                             COUNT(DISTINCT CASE WHEN p.amount > 0 AND p.is_deleted=0
                               THEN p.yclients_transaction_id END) payment_count,
                             SUM(CASE WHEN p.amount > 0 AND p.is_deleted=0
                               THEN p.amount ELSE 0 END) paid_amount,
                             MIN(CASE WHEN p.amount > 0 AND p.is_deleted=0
                               THEN p.payment_date END) first_payment_at,
                             MAX(CASE WHEN p.amount > 0 AND p.is_deleted=0
                               THEN p.payment_date END) last_payment_at,
                             GROUP_CONCAT(DISTINCT CASE
                               WHEN p.amount > 0 AND p.is_deleted=0
                               THEN p.yclients_transaction_id END
                               ORDER BY p.yclients_transaction_id SEPARATOR ', ') transaction_ids
                      FROM sale_items si
                      LEFT JOIN sale_payment_transactions p
                        ON p.yclients_company_id=si.yclients_company_id
                       AND p.sale_item_id=si.sale_item_id
                      WHERE LOWER(TRIM(si.title)) IN (%s, %s)
                      GROUP BY si.yclients_company_id, si.yclients_record_id,
                               LOWER(TRIM(si.title))
                    ) pay
                      ON pay.yclients_company_id=r.yclients_company_id
                     AND pay.yclients_record_id=r.yclients_record_id
                     AND pay.service_key=LOWER(TRIM(rs.title))
                    WHERE r.attendance=1 AND r.is_deleted=0
                      AND r.datetime >= %s AND r.datetime < %s
                      AND r.datetime < NOW()
                      AND LOWER(TRIM(rs.title)) IN (%s, %s)
                    ORDER BY r.datetime DESC, r.yclients_record_id DESC
                    """,
                    (
                        *INSTALLMENT_SERVICES.keys(),
                        datetime.combine(start, datetime.min.time()),
                        datetime.combine(end + timedelta(days=1), datetime.min.time()),
                        *INSTALLMENT_SERVICES.keys(),
                    ),
                )
                rows = [dict(row) for row in cursor.fetchall()]
        finally:
            connection.close()

        for row in rows:
            row["branch"] = normalize_branch(row.get("branch_title"))
            row["direction"] = INSTALLMENT_SERVICES.get(
                str(row.get("service_key") or ""), UNKNOWN
            )
            row["record_id"] = int(row.get("record_id") or 0)
            row["client_id"] = int(row.get("client_id") or 0)
        return rows

    def _record_contexts(
        self, pairs: Iterable[Tuple[int, int]]
    ) -> Dict[Tuple[int, int], Dict[str, Any]]:
        grouped: Dict[int, set[int]] = defaultdict(set)
        for company_id, record_id in pairs:
            if company_id and record_id:
                grouped[int(company_id)].add(int(record_id))
        result: Dict[Tuple[int, int], Dict[str, Any]] = {}
        if not grouped:
            return result
        connection = self._database()
        try:
            with connection.cursor() as cursor:
                for company_id, record_ids in grouped.items():
                    for chunk in self._chunks(sorted(record_ids)):
                        placeholders = ",".join(["%s"] * len(chunk))
                        cursor.execute(
                            f"""
                            SELECT r.yclients_company_id, r.yclients_record_id,
                                   MAX(r.custom_color) custom_color,
                                   MAX(r.yclients_staff_id) yclients_staff_id,
                                   MAX(st.name) staff_name,
                                   GROUP_CONCAT(DISTINCT CONCAT_WS(' ', sc.title, rs.title)
                                     ORDER BY rs.title SEPARATOR ' | ') service_text,
                                   GROUP_CONCAT(DISTINCT LOWER(TRIM(rs.title))
                                     ORDER BY rs.title SEPARATOR '|||') service_titles,
                                   COALESCE(MAX(abonement_totals.abonement_usage),0) abonement_usage
                            FROM records r
                            LEFT JOIN staff st
                              ON st.yclients_company_id=r.yclients_company_id
                             AND st.yclients_staff_id=r.yclients_staff_id
                            LEFT JOIN record_services rs
                              ON rs.yclients_company_id=r.yclients_company_id
                             AND rs.yclients_record_id=r.yclients_record_id
                            LEFT JOIN services s
                              ON s.yclients_company_id=rs.yclients_company_id
                             AND s.yclients_service_id=rs.yclients_service_id
                            LEFT JOIN service_categories sc
                              ON sc.yclients_company_id=s.yclients_company_id
                             AND sc.yclients_category_id=s.yclients_category_id
                            LEFT JOIN (
                              SELECT yclients_company_id, yclients_record_id,
                                     SUM(COALESCE(paid_abonements_count,0)) abonement_usage
                              FROM visit_service_finance
                              GROUP BY yclients_company_id, yclients_record_id
                            ) abonement_totals
                              ON abonement_totals.yclients_company_id=r.yclients_company_id
                             AND abonement_totals.yclients_record_id=r.yclients_record_id
                            WHERE r.yclients_company_id=%s
                              AND r.yclients_record_id IN ({placeholders})
                            GROUP BY r.yclients_company_id, r.yclients_record_id
                            """,
                            [company_id, *chunk],
                        )
                        for row in cursor.fetchall():
                            direction = classify_direction([row.get("service_text")])
                            visit_type = classify_visit_type(row.get("custom_color"))
                            service_titles = str(row.get("service_titles") or "").split("|||")
                            prepayment_direction = administrator_prepayment_direction(
                                service_titles
                            )
                            administrator_record = is_administrator_staff(
                                row.get("staff_name")
                            )
                            result[(int(row["yclients_company_id"]), int(row["yclients_record_id"]))] = {
                                "direction": direction,
                                "service_text": row.get("service_text") or "",
                                "staff_id": int(row.get("yclients_staff_id") or 0),
                                "staff_name": row.get("staff_name") or "",
                                "is_administrator_record": administrator_record,
                                "administrator_prepayment_direction": prepayment_direction,
                                "record_color": normalize_record_color(row.get("custom_color")),
                                "visit_type": visit_type,
                                "visit_type_label": VISIT_TYPE_LABELS[visit_type],
                                "uses_subscription": (
                                    Decimal(str(row.get("abonement_usage") or 0)) > 0
                                ),
                            }
        finally:
            connection.close()
        return result

    def color_reconciliation(
        self,
        start: date,
        end: date,
        *,
        filters: Optional[Mapping[str, Any]] = None,
        visit_types: Sequence[str] = (),
        colors: Sequence[str] = (),
        search: str = "",
        page: int = 1,
        per_page: int = 50,
    ) -> Dict[str, Any]:
        visits = self._visits(start, end)
        pairs = {
            (int(row["yclients_company_id"]), int(row["yclients_record_id"]))
            for row in visits
            if row.get("yclients_record_id")
        }
        contexts = self._record_contexts(pairs)
        for row in visits:
            pair = (int(row["yclients_company_id"]), int(row["yclients_record_id"]))
            context = contexts.get(pair, {})
            row.update(context)
            row["direction"] = context.get("direction", UNKNOWN)
            row["branch"] = normalize_branch(row.get("branch_title"))
            row["record_id"] = int(row["yclients_record_id"])
            row["client_id"] = int(row.get("yclients_client_id") or 0)
            row["visit_type"] = context.get(
                "visit_type", classify_visit_type(row.get("custom_color"))
            )
            row["visit_type_label"] = VISIT_TYPE_LABELS[row["visit_type"]]
            row["record_color"] = normalize_record_color(row.get("custom_color"))
            row["uses_subscription"] = bool(context.get("uses_subscription"))
            if isinstance(row.get("visit_at"), datetime):
                row["visit_at"] = row["visit_at"].isoformat(timespec="minutes")
        return build_color_reconciliation(
            visits,
            filters=filters,
            visit_types=visit_types,
            colors=colors,
            search=search,
            page=page,
            per_page=per_page,
        )

    def report(
        self,
        start: date,
        end: date,
        *,
        filters: Optional[Mapping[str, Any]] = None,
        detail_limit: int = 100,
    ) -> Dict[str, Any]:
        visits = self._visits(start, end)
        transactions = self._transactions(start, end)
        pairs = {
            (int(row["yclients_company_id"]), int(row["yclients_record_id"]))
            for row in [*visits, *transactions]
            if row.get("yclients_record_id")
        }
        contexts = self._record_contexts(pairs)
        subscription_index = self._subscription_documents()

        for row in visits:
            pair = (int(row["yclients_company_id"]), int(row["yclients_record_id"]))
            context = contexts.get(pair, {})
            row.update(context)
            row["direction"] = context.get("direction", UNKNOWN)
            row["branch"] = normalize_branch(row.get("branch_title"))
            row["record_id"] = int(row["yclients_record_id"])
            row["client_id"] = int(row.get("yclients_client_id") or 0)
            row["visit_type"] = context.get(
                "visit_type", classify_visit_type(row.get("custom_color"))
            )
            row["visit_type_label"] = VISIT_TYPE_LABELS[row["visit_type"]]
            row["record_color"] = normalize_record_color(row.get("custom_color"))
            row["uses_subscription"] = bool(context.get("uses_subscription"))
            if isinstance(row.get("visit_at"), datetime):
                row["visit_at"] = row["visit_at"].isoformat(timespec="minutes")

        for row in transactions:
            pair = (
                int(row["yclients_company_id"]),
                int(row["yclients_record_id"] or 0),
            )
            context = contexts.get(pair, {})
            subscription = subscription_index.get(
                (int(row["yclients_company_id"]), int(row["document_id"] or 0)),
                {},
            )
            row["branch"] = normalize_branch(row.get("branch_title"))
            prepayment_direction = context.get("administrator_prepayment_direction")
            if row.get("expense_title") == "Продажа абонементов":
                row["direction"] = subscription.get("direction", UNKNOWN)
            elif context.get("is_administrator_record") and prepayment_direction:
                row["direction"] = prepayment_direction
            else:
                row["direction"] = context.get("direction", UNKNOWN)
            row["subscription_kind"] = subscription.get("subscription_kind")
            row["visit_type"] = (
                VISIT_TYPE_FIRST
                if context.get("is_administrator_record") and prepayment_direction
                else context.get("visit_type", VISIT_TYPE_REPEAT)
            )
            row["uses_subscription"] = bool(context.get("uses_subscription"))
            row["staff_id"] = int(context.get("staff_id") or 0)
            row["staff_name"] = context.get("staff_name") or ""
            row["is_administrator_record"] = bool(
                context.get("is_administrator_record")
            )
            row["amount"] = Decimal(str(row.get("amount") or 0))
            if isinstance(row.get("transaction_at"), datetime):
                row["transaction_at"] = row["transaction_at"].isoformat(timespec="minutes")

        subscription_sales = []
        for (company_id, document_id), row in subscription_index.items():
            sold_at = row.get("sold_at")
            if not isinstance(sold_at, datetime) or not (start <= sold_at.date() <= end):
                continue
            company_title = next(
                (
                    item.get("branch_title")
                    for item in transactions
                    if int(item["yclients_company_id"]) == company_id
                ),
                str(company_id),
            )
            subscription_sales.append(
                {
                    "document_id": document_id,
                    "client_id": int(row.get("yclients_client_id") or 0),
                    "sold_at": sold_at.isoformat(timespec="minutes"),
                    "branch": normalize_branch(company_title),
                    "direction": row.get("direction", UNKNOWN),
                    "subscription_kind": row.get("subscription_kind", "repeat"),
                    "title": row.get("item_titles") or "Абонемент",
                }
            )

        result = build_yclients_report(
            visits,
            transactions,
            subscription_sales,
            filters=filters,
            detail_limit=max(20, min(int(detail_limit), 300)),
        )
        installment_rows = self._installment_services(start, end)
        result["installment_debt"] = build_installment_debt_report(
            installment_rows,
            filters=filters,
            detail_limit=max(20, min(int(detail_limit), 500)),
        )
        result["method"]["excluded"] = (
            "Дебиторка по методике заказчика показывается отдельно и не прибавляется "
            "к выручке; возвраты временно не уменьшают выручку"
        )
        result["period"] = {"from": start.isoformat(), "to": end.isoformat()}
        return result

    def filters(self) -> Dict[str, Any]:
        connection = self._database()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT title FROM companies WHERE is_active=1 ORDER BY title")
                branches = sorted(
                    {normalize_branch(row["title"]) for row in cursor.fetchall()}
                )
                cursor.execute(
                    """
                    SELECT MIN(datetime) min_date,
                           LEAST(MAX(datetime), NOW()) max_date
                    FROM records
                    WHERE is_deleted=0
                    """
                )
                period = cursor.fetchone()
        finally:
            connection.close()
        min_date = (period.get("min_date") or datetime.combine(MIN_YCLIENTS_DATE, datetime.min.time())).date()
        max_date = (period.get("max_date") or datetime.now()).date()
        return {
            "branch": branches,
            "direction": ["Массаж", "Лазер", UNKNOWN],
            "min_date": max(min_date, MIN_YCLIENTS_DATE).isoformat(),
            "max_date": max_date.isoformat(),
            "last_sync": self.last_sync(),
        }

    def last_sync(self) -> Optional[str]:
        connection = self._database()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT GREATEST(
                      COALESCE((SELECT MAX(synced_at) FROM records), '1970-01-01'),
                      COALESCE((SELECT MAX(synced_at) FROM record_finance_transactions), '1970-01-01'),
                      COALESCE((SELECT MAX(synced_at) FROM visit_service_finance), '1970-01-01')
                    ) last_sync
                    """
                )
                value = cursor.fetchone().get("last_sync")
        finally:
            connection.close()
        if not value:
            return None
        if not isinstance(value, datetime):
            try:
                value = datetime.fromisoformat(str(value))
            except ValueError:
                return str(value)
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(MOSCOW).strftime("%d.%m.%Y %H:%M")
