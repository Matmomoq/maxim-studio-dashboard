"""Cross-system funnel analytics joining amoCRM leads with Yclients money."""

from __future__ import annotations

import os
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from threading import Lock
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import pymysql
from db_security import connect as secure_connect, tls_context

from amocrm_client import load_env
from .analytics import (
    LeadRecord,
    MOSCOW,
    analytics_group_value,
    matches_analytics_filters,
)
from .classifier import is_speed_dial_source
from .marketing_analytics import CONFIGURED_SOURCES, allocate_marketing_costs
from .yclients_rules import is_administrator_staff


MONEY_STEP = Decimal("0.01")


def _money(value: Decimal) -> float:
    return float(value.quantize(MONEY_STEP, rounding=ROUND_HALF_UP))


def _ratio(numerator: int, denominator: int) -> float:
    return round((numerator / denominator * 100) if denominator else 0.0, 2)


def _roi(revenue: Decimal, spend: Decimal) -> Optional[float]:
    if not spend:
        return None
    return round(float((revenue - spend) / spend * 100), 2)


def record_counts_as_visit(row: Mapping[str, Any], now_moscow: datetime) -> bool:
    return bool(
        row.get("attendance") == 1
        and not row.get("is_deleted")
        and row.get("datetime") <= now_moscow
        and not is_administrator_staff(row.get("staff_name"))
    )


@dataclass(frozen=True)
class LeadOutcome:
    linked: bool = False
    visited: bool = False
    sales: int = 0
    service_sales: int = 0
    subscription_sales: int = 0
    first_subscription_sales: int = 0
    repeat_subscription_sales: int = 0
    revenue: Decimal = Decimal("0")
    service_revenue: Decimal = Decimal("0")
    subscription_revenue: Decimal = Decimal("0")
    first_subscription_revenue: Decimal = Decimal("0")
    repeat_subscription_revenue: Decimal = Decimal("0")
    other_revenue: Decimal = Decimal("0")


def _selected_values(filters: Mapping[str, Any]) -> Dict[str, set[str]]:
    selected: Dict[str, set[str]] = {}
    for attribute in ("branch", "direction", "offer", "source"):
        raw = filters.get(attribute)
        values = [raw] if isinstance(raw, str) else list(raw or [])
        selected[attribute] = {
            str(value) for value in values if value and value != "Все"
        }
    return selected


def _matches(record: LeadRecord, selected: Mapping[str, set[str]]) -> bool:
    return matches_analytics_filters(record, selected)


def _metrics_row(
    label: str,
    records: Sequence[LeadRecord],
    outcomes: Mapping[int, LeadOutcome],
    costs: Mapping[int, Decimal],
    covered: set[int],
) -> Dict[str, Any]:
    leads = len(records)
    bookings = sum(int(record.booked) for record in records)
    linked = sum(int(outcomes.get(record.lead_id, LeadOutcome()).linked) for record in records)
    visits = sum(int(outcomes.get(record.lead_id, LeadOutcome()).visited) for record in records)
    paying_leads = sum(
        int(outcomes.get(record.lead_id, LeadOutcome()).revenue > 0)
        for record in records
    )
    sales = sum(outcomes.get(record.lead_id, LeadOutcome()).sales for record in records)
    revenue = sum(
        (outcomes.get(record.lead_id, LeadOutcome()).revenue for record in records),
        Decimal("0"),
    )
    spend = sum((costs.get(record.lead_id, Decimal("0")) for record in records), Decimal("0"))
    expense_eligible = [
        record for record in records if record.source in CONFIGURED_SOURCES
    ]
    covered_records = [
        record for record in expense_eligible if record.lead_id in covered
    ]
    covered_count = len(covered_records)
    covered_bookings = sum(int(record.booked) for record in covered_records)
    covered_visits = sum(
        int(outcomes.get(record.lead_id, LeadOutcome()).visited)
        for record in covered_records
    )
    covered_paying_leads = sum(
        int(outcomes.get(record.lead_id, LeadOutcome()).revenue > 0)
        for record in covered_records
    )
    roi_revenue = sum(
        (
            outcomes.get(record.lead_id, LeadOutcome()).revenue
            for record in covered_records
        ),
        Decimal("0"),
    )
    has_spend = covered_count > 0
    return {
        "key": label,
        "label": label,
        "leads": leads,
        "bookings": bookings,
        "linked": linked,
        "visits": visits,
        "paying_leads": paying_leads,
        "sales": sales,
        "spend": _money(spend) if has_spend else None,
        "revenue": _money(revenue),
        "roi_revenue": _money(roi_revenue) if has_spend else None,
        "marketing_profit": _money(roi_revenue - spend) if has_spend else None,
        "booking_conversion": _ratio(bookings, leads),
        "visit_conversion": _ratio(visits, leads),
        "sale_conversion": _ratio(paying_leads, leads),
        "average_check": _money(revenue / sales) if sales else None,
        "cost_per_lead": _money(spend / covered_count) if has_spend else None,
        "cost_per_booking": _money(spend / covered_bookings) if has_spend and covered_bookings else None,
        "cost_per_visit": _money(spend / covered_visits) if has_spend and covered_visits else None,
        "cost_per_sale": _money(spend / covered_paying_leads) if has_spend and covered_paying_leads else None,
        "roi": _roi(roi_revenue, spend) if has_spend else None,
        "expense_coverage": (
            _ratio(covered_count, len(expense_eligible))
            if expense_eligible
            else None
        ),
    }


def calculate_funnel_report(
    records: Iterable[LeadRecord],
    outcomes: Mapping[int, LeadOutcome],
    costs: Mapping[int, Decimal],
    covered: set[int],
    *,
    filters: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    selected = _selected_values(filters or {})
    filtered = [record for record in records if _matches(record, selected)]
    total = _metrics_row("Итого", filtered, outcomes, costs, covered)

    def grouped(attribute: str) -> List[Dict[str, Any]]:
        buckets: Dict[str, List[LeadRecord]] = defaultdict(list)
        for record in filtered:
            buckets[analytics_group_value(record, attribute)].append(record)
        rows = [
            _metrics_row(label, items, outcomes, costs, covered)
            for label, items in buckets.items()
        ]
        rows.sort(key=lambda row: (-row["revenue"], -row["leads"], row["label"]))
        return rows

    source_branch_buckets: Dict[Tuple[str, str], List[LeadRecord]] = defaultdict(list)
    for record in filtered:
        if is_speed_dial_source(record.source):
            continue
        source_branch_buckets[(record.source, record.branch)].append(record)
    source_branch = []
    for (source, branch), items in source_branch_buckets.items():
        row = _metrics_row(branch, items, outcomes, costs, covered)
        row.update({"source": source, "branch": branch, "key": f"{source}\x1f{branch}"})
        source_branch.append(row)
    source_branch.sort(key=lambda row: (row["source"], -row["revenue"], row["branch"]))

    service_revenue = sum(
        (outcomes.get(record.lead_id, LeadOutcome()).service_revenue for record in filtered),
        Decimal("0"),
    )
    first_subscription_revenue = sum(
        (outcomes.get(record.lead_id, LeadOutcome()).first_subscription_revenue for record in filtered),
        Decimal("0"),
    )
    repeat_subscription_revenue = sum(
        (outcomes.get(record.lead_id, LeadOutcome()).repeat_subscription_revenue for record in filtered),
        Decimal("0"),
    )
    first_subscription_sales = sum(
        outcomes.get(record.lead_id, LeadOutcome()).first_subscription_sales
        for record in filtered
    )
    repeat_subscription_sales = sum(
        outcomes.get(record.lead_id, LeadOutcome()).repeat_subscription_sales
        for record in filtered
    )
    service_sales = sum(
        outcomes.get(record.lead_id, LeadOutcome()).service_sales for record in filtered
    )
    classified_revenue = service_revenue + first_subscription_revenue + repeat_subscription_revenue
    other_revenue = max(Decimal(str(total["revenue"])) - classified_revenue, Decimal("0"))
    linked = total["linked"]
    return {
        "kpi": total,
        "funnel": [
            {"key": "leads", "label": "Лиды", "value": total["leads"], "conversion": 100.0 if total["leads"] else 0.0},
            {"key": "bookings", "label": "Записи", "value": total["bookings"], "conversion": _ratio(total["bookings"], total["leads"])},
            {"key": "visits", "label": "Визиты", "value": total["visits"], "conversion": _ratio(total["visits"], total["leads"])},
            {"key": "sales", "label": "Клиенты с оплатой", "value": total["paying_leads"], "conversion": _ratio(total["paying_leads"], total["leads"])},
        ],
        "revenue_mix": [
            {"key": "services", "label": "Оплата услуг", "sales": service_sales, "revenue": _money(service_revenue)},
            {"key": "first_subscription", "label": "Первые абонементы", "sales": first_subscription_sales, "revenue": _money(first_subscription_revenue)},
            {"key": "repeat_subscription", "label": "Повторные абонементы", "sales": repeat_subscription_sales, "revenue": _money(repeat_subscription_revenue)},
            {"key": "other", "label": "Прочие продажи", "sales": max(total["sales"] - service_sales - first_subscription_sales - repeat_subscription_sales, 0), "revenue": _money(other_revenue)},
        ],
        "groups": {
            "source": grouped("source"),
            "branch": grouped("branch"),
            "direction": grouped("direction"),
            "source_branch": source_branch,
        },
        "coverage": {
            "linked_leads": linked,
            "booked_leads": total["bookings"],
            "link_rate": _ratio(linked, total["bookings"]),
            "unlinked_bookings": max(total["bookings"] - linked, 0),
            "expense_rate": total["expense_coverage"],
        },
        "method": {
            "period_basis": "Дата создания лида в amoCRM",
            "revenue_basis": "Фактически полученные оплаты по прямо связанной записи Yclients",
            "roi_formula": "(Выручка − рекламные расходы) ÷ рекламные расходы × 100%",
            "roi_scope": "ROI и стоимости этапов считаются только по лидам, для которых заполнены рекламные расходы",
            "roi_note": "Сейчас ROI считается без себестоимости. После её загрузки формула будет расширена.",
            "subscription_note": "Первой считается самая ранняя продажа абонемента клиенту в доступной истории Yclients.",
            "administrator_note": (
                "Записи сотрудника «Администратор» не считаются визитами, "
                "но связанные продажи и фактические оплаты учитываются"
            ),
        },
    }


class FunnelAnalytics:
    def __init__(self, env_path: str = ".env") -> None:
        self.env_path = env_path
        self.cache_seconds = int(load_env(env_path).get("DASHBOARD_CACHE_SECONDS", "300"))
        self._subscription_cache: Tuple[float, Dict[Tuple[int, int], Dict[str, Any]]] = (0.0, {})
        self._cache_lock = Lock()

    def _database(self, prefix: str):
        env = load_env(self.env_path)
        kwargs: Dict[str, Any] = {"ssl": tls_context(env, prefix)}
        return secure_connect(
            host=os.environ.get(f"{prefix}_DB_HOST_OVERRIDE", env[f"{prefix}_DB_HOST"]),
            port=int(env.get(f"{prefix}_DB_PORT", "3306")),
            user=env[f"{prefix}_DB_USER"],
            password=env[f"{prefix}_DB_PASSWORD"],
            database=env[f"{prefix}_DB_NAME"],
            charset="utf8mb4",
            autocommit=True,
            connect_timeout=int(env.get(f"{prefix}_DB_CONNECT_TIMEOUT", "10")),
            read_timeout=60,
            cursorclass=pymysql.cursors.DictCursor,
            **kwargs,
        )

    @staticmethod
    def _chunks(values: Sequence[int], size: int = 800):
        for index in range(0, len(values), size):
            yield values[index:index + size]

    def _lead_pairs(self, lead_ids: Sequence[int]) -> Dict[int, Tuple[int, int]]:
        result: Dict[int, Tuple[int, int]] = {}
        if not lead_ids:
            return result
        connection = self._database("AMO")
        try:
            with connection.cursor() as cursor:
                for chunk in self._chunks(list(lead_ids)):
                    placeholders = ",".join(["%s"] * len(chunk))
                    cursor.execute(
                        f"""
                        SELECT amo_lead_id, yclients_company_id, yclients_record_id
                        FROM amo_leads
                        WHERE amo_lead_id IN ({placeholders})
                          AND yclients_company_id IS NOT NULL
                          AND yclients_record_id IS NOT NULL
                        """,
                        chunk,
                    )
                    for row in cursor.fetchall():
                        result[int(row["amo_lead_id"])] = (
                            int(row["yclients_company_id"]),
                            int(row["yclients_record_id"]),
                        )
        finally:
            connection.close()
        return result

    def _subscription_documents(self) -> Dict[Tuple[int, int], Dict[str, Any]]:
        now = time.monotonic()
        with self._cache_lock:
            cached_at, cached = self._subscription_cache
            if cached and now - cached_at <= self.cache_seconds:
                return dict(cached)
        connection = self._database("YCLIENTS")
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT f.yclients_company_id, f.yclients_record_id, f.document_id,
                           MIN(f.transaction_date) AS sold_at,
                           SUM(f.amount) AS revenue,
                           COALESCE(NULLIF(REGEXP_REPLACE(c.phone, '[^0-9]', ''), ''),
                                    CONCAT('id:', f.yclients_company_id, ':', f.yclients_client_id)) AS customer_key
                    FROM record_finance_transactions f
                    LEFT JOIN clients c
                      ON c.yclients_company_id = f.yclients_company_id
                     AND c.yclients_client_id = f.yclients_client_id
                    WHERE f.expense_title = 'Продажа абонементов'
                      AND f.amount > 0
                    GROUP BY f.yclients_company_id, f.yclients_record_id,
                             f.document_id, customer_key
                    ORDER BY customer_key, sold_at, f.yclients_company_id, f.document_id
                    """
                )
                rows = cursor.fetchall()
        finally:
            connection.close()
        seen: set[str] = set()
        by_record: Dict[Tuple[int, int], Dict[str, Any]] = defaultdict(
            lambda: {"first_sales": 0, "repeat_sales": 0, "first_revenue": Decimal("0"), "repeat_revenue": Decimal("0")}
        )
        for row in rows:
            key = (int(row["yclients_company_id"]), int(row["yclients_record_id"]))
            customer = str(row["customer_key"])
            revenue = Decimal(str(row["revenue"] or 0))
            if customer in seen:
                by_record[key]["repeat_sales"] += 1
                by_record[key]["repeat_revenue"] += revenue
            else:
                seen.add(customer)
                by_record[key]["first_sales"] += 1
                by_record[key]["first_revenue"] += revenue
        materialized = dict(by_record)
        with self._cache_lock:
            self._subscription_cache = (now, materialized)
        return dict(materialized)

    def outcomes(self, records: Sequence[LeadRecord]) -> Dict[int, LeadOutcome]:
        pairs = self._lead_pairs([record.lead_id for record in records])
        pair_owner: Dict[Tuple[int, int], int] = {}
        for record in sorted(records, key=lambda item: (item.created_date, item.lead_id)):
            pair = pairs.get(record.lead_id)
            if pair and pair not in pair_owner:
                pair_owner[pair] = record.lead_id
        by_pair: Dict[Tuple[int, int], Mapping[str, Any]] = {}
        if pair_owner:
            grouped: Dict[int, List[int]] = defaultdict(list)
            for company_id, record_id in pair_owner:
                grouped[company_id].append(record_id)
            connection = self._database("YCLIENTS")
            try:
                with connection.cursor() as cursor:
                    for company_id, record_ids in grouped.items():
                        for chunk in self._chunks(sorted(set(record_ids))):
                            placeholders = ",".join(["%s"] * len(chunk))
                            cursor.execute(
                                f"""
                                SELECT r.yclients_company_id, r.yclients_record_id,
                                       r.status, r.attendance, r.is_deleted, r.datetime,
                                       MAX(s.name) AS staff_name,
                                       COUNT(DISTINCT f.document_id) AS sales,
                                       COUNT(DISTINCT CASE WHEN f.expense_title='Оказание услуг' THEN f.document_id END) AS service_sales,
                                       COUNT(DISTINCT CASE WHEN f.expense_title='Продажа абонементов' THEN f.document_id END) AS subscription_sales,
                                       COALESCE(SUM(f.amount), 0) AS revenue,
                                       COALESCE(SUM(CASE WHEN f.expense_title='Оказание услуг' THEN f.amount ELSE 0 END), 0) AS service_revenue,
                                       COALESCE(SUM(CASE WHEN f.expense_title='Продажа абонементов' THEN f.amount ELSE 0 END), 0) AS subscription_revenue,
                                       COALESCE(SUM(CASE WHEN f.expense_title NOT IN ('Оказание услуг','Продажа абонементов') THEN f.amount ELSE 0 END), 0) AS other_revenue
                                FROM records r
                                LEFT JOIN record_finance_transactions f
                                  ON f.yclients_company_id=r.yclients_company_id
                                 AND f.yclients_record_id=r.yclients_record_id
                                 AND f.amount > 0
                                LEFT JOIN staff s
                                  ON s.yclients_company_id=r.yclients_company_id
                                 AND s.yclients_staff_id=r.yclients_staff_id
                                WHERE r.yclients_company_id=%s
                                  AND r.yclients_record_id IN ({placeholders})
                                GROUP BY r.yclients_company_id, r.yclients_record_id,
                                         r.status, r.attendance, r.is_deleted, r.datetime
                                """,
                                [company_id, *chunk],
                            )
                            for row in cursor.fetchall():
                                by_pair[(int(row["yclients_company_id"]), int(row["yclients_record_id"]))] = row
            finally:
                connection.close()
        subscriptions = self._subscription_documents() if pair_owner else {}
        result: Dict[int, LeadOutcome] = {}
        now_moscow = datetime.now(MOSCOW).replace(tzinfo=None)
        for lead_id, pair in pairs.items():
            if pair_owner.get(pair) != lead_id:
                result[lead_id] = LeadOutcome(linked=True)
                continue
            row = by_pair.get(pair)
            if not row:
                result[lead_id] = LeadOutcome(linked=True)
                continue
            subscription = subscriptions.get(pair, {})
            result[lead_id] = LeadOutcome(
                linked=True,
                visited=record_counts_as_visit(row, now_moscow),
                sales=int(row["sales"] or 0),
                service_sales=int(row["service_sales"] or 0),
                subscription_sales=int(row["subscription_sales"] or 0),
                first_subscription_sales=int(subscription.get("first_sales", 0)),
                repeat_subscription_sales=int(subscription.get("repeat_sales", 0)),
                revenue=Decimal(str(row["revenue"] or 0)),
                service_revenue=Decimal(str(row["service_revenue"] or 0)),
                subscription_revenue=Decimal(str(row["subscription_revenue"] or 0)),
                first_subscription_revenue=Decimal(str(subscription.get("first_revenue", 0))),
                repeat_subscription_revenue=Decimal(str(subscription.get("repeat_revenue", 0))),
                other_revenue=Decimal(str(row["other_revenue"] or 0)),
            )
        return result

    def report(
        self,
        records: Sequence[LeadRecord],
        allocation_records: Sequence[LeadRecord],
        *,
        rates: Sequence[Mapping[str, Any]],
        period_expenses: Sequence[Mapping[str, Any]],
        filters: Optional[Mapping[str, Any]] = None,
        include_drafts: bool = False,
    ) -> Dict[str, Any]:
        costs, covered, _drafts, _unallocated = allocate_marketing_costs(
            records,
            allocation_records,
            rates=rates,
            period_expenses=period_expenses,
            include_drafts=include_drafts,
        )
        return calculate_funnel_report(
            records,
            self.outcomes(records),
            costs,
            covered,
            filters=filters,
        )
