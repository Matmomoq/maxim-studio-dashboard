"""Time-series calculations for the dashboard dynamics chart."""

from __future__ import annotations

from calendar import monthrange
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .analytics import LeadRecord, analytics_group_value, conversion
from .marketing_analytics import (
    CONFIGURED_SOURCES,
    _matches_filters,
    _selected_values,
    allocate_marketing_costs,
)


GRANULARITIES = {"day", "week", "month"}
BREAKDOWNS = {"none", "source", "branch", "direction"}
MONTHS_SHORT = (
    "янв", "фев", "мар", "апр", "май", "июн",
    "июл", "авг", "сен", "окт", "ноя", "дек",
)
MONEY_STEP = Decimal("0.01")


def _money(value: Decimal) -> float:
    return float(value.quantize(MONEY_STEP, rounding=ROUND_HALF_UP))


def _bucket_start(value: date, granularity: str) -> date:
    if granularity == "week":
        return value - timedelta(days=value.weekday())
    if granularity == "month":
        return value.replace(day=1)
    return value


def _next_bucket(value: date, granularity: str) -> date:
    if granularity == "week":
        return value + timedelta(days=7)
    if granularity == "month":
        if value.month == 12:
            return date(value.year + 1, 1, 1)
        return date(value.year, value.month + 1, 1)
    return value + timedelta(days=1)


def _bucket_end(value: date, granularity: str) -> date:
    if granularity == "week":
        return value + timedelta(days=6)
    if granularity == "month":
        return date(value.year, value.month, monthrange(value.year, value.month)[1])
    return value


def _date_label(value: date, *, with_year: bool = False) -> str:
    label = f"{value.day} {MONTHS_SHORT[value.month - 1]}"
    return f"{label} {value.year}" if with_year else label


def _bucket_label(
    bucket: date, granularity: str, period_start: date, period_end: date
) -> str:
    visible_start = max(bucket, period_start)
    visible_end = min(_bucket_end(bucket, granularity), period_end)
    if granularity == "month":
        return f"{MONTHS_SHORT[bucket.month - 1].capitalize()} {bucket.year}"
    if visible_start == visible_end:
        return _date_label(visible_start, with_year=period_start.year != period_end.year)
    return f"{_date_label(visible_start)}–{_date_label(visible_end, with_year=True)}"


def _buckets(start: date, end: date, granularity: str) -> List[date]:
    result: List[date] = []
    current = _bucket_start(start, granularity)
    while current <= end:
        result.append(current)
        current = _next_bucket(current, granularity)
    return result


def calculate_trend_data(
    report_records: Iterable[LeadRecord],
    allocation_records: Iterable[LeadRecord],
    *,
    rates: Sequence[Mapping[str, Any]],
    period_expenses: Sequence[Mapping[str, Any]],
    start: date,
    end: date,
    granularity: str,
    breakdown: str,
    filters: Optional[Mapping[str, Any]] = None,
    include_drafts: bool = False,
    max_series: int = 5,
) -> Dict[str, Any]:
    if granularity not in GRANULARITIES:
        raise ValueError("Детализация доступна по дням, неделям или месяцам")
    if breakdown not in BREAKDOWNS:
        raise ValueError("Некорректная разбивка графика")

    report_materialized = list(report_records)
    selected = _selected_values(filters or {})
    filtered = [
        record
        for record in report_materialized
        if _matches_filters(record, selected)
    ]
    costs, covered, draft_leads, _ = allocate_marketing_costs(
        report_materialized,
        allocation_records,
        rates=rates,
        period_expenses=period_expenses,
        include_drafts=include_drafts,
    )

    bucket_dates = _buckets(start, end, granularity)
    bucket_index = {value: index for index, value in enumerate(bucket_dates)}
    if breakdown == "none":
        top_labels = ["Все данные"]
    else:
        counts = Counter(analytics_group_value(record, breakdown) for record in filtered)
        top_labels = [
            label
            for label, _ in sorted(
                counts.items(), key=lambda item: (-item[1], item[0].lower())
            )[:max_series]
        ]
        if len(counts) > len(top_labels):
            top_labels.append("Прочие")
        if not top_labels:
            top_labels = ["Все данные"]

    grouped: Dict[Tuple[str, date], List[LeadRecord]] = defaultdict(list)
    top_set = set(top_labels)
    for record in filtered:
        if breakdown == "none":
            label = "Все данные"
        else:
            raw_label = analytics_group_value(record, breakdown)
            label = raw_label if raw_label in top_set else "Прочие"
        grouped[(label, _bucket_start(record.created_date, granularity))].append(record)

    series: List[Dict[str, Any]] = []
    for label in top_labels:
        points: List[Dict[str, Any]] = []
        for bucket in bucket_dates:
            items = grouped.get((label, bucket), [])
            leads = len(items)
            bookings = sum(int(record.booked) for record in items)
            configured = [
                record for record in items if record.source in CONFIGURED_SOURCES
            ]
            covered_items = [
                record for record in configured if record.lead_id in covered
            ]
            spend = sum(
                (costs.get(record.lead_id, Decimal("0")) for record in items),
                Decimal("0"),
            )
            has_spend = bool(covered_items) or not items
            points.append(
                {
                    "bucket": bucket.isoformat(),
                    "label": _bucket_label(bucket, granularity, start, end),
                    "leads": leads,
                    "bookings": bookings,
                    "conversion": conversion(leads, bookings),
                    "spend": _money(spend) if has_spend else None,
                    "cpl": _money(spend / leads) if has_spend and leads else None,
                    "cost_per_booking": (
                        _money(spend / bookings)
                        if has_spend and bookings
                        else None
                    ),
                    "coverage": (
                        "complete"
                        if configured and len(covered_items) == len(configured)
                        else "partial"
                        if configured
                        else "not_configured"
                    ),
                    "uses_drafts": any(
                        record.lead_id in draft_leads for record in items
                    ),
                }
            )
        series.append({"key": label, "label": label, "points": points})

    total_leads = len(filtered)
    total_bookings = sum(int(record.booked) for record in filtered)
    total_spend = sum(
        (costs.get(record.lead_id, Decimal("0")) for record in filtered),
        Decimal("0"),
    )
    any_spend = any(record.lead_id in covered for record in filtered)
    return {
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "granularity": granularity,
        "breakdown": breakdown,
        "buckets": [
            {
                "key": bucket.isoformat(),
                "label": _bucket_label(bucket, granularity, start, end),
            }
            for bucket in bucket_dates
        ],
        "series": series,
        "summary": {
            "leads": total_leads,
            "bookings": total_bookings,
            "conversion": conversion(total_leads, total_bookings),
            "spend": _money(total_spend) if any_spend else None,
            "cpl": (
                _money(total_spend / total_leads)
                if any_spend and total_leads
                else None
            ),
            "cost_per_booking": (
                _money(total_spend / total_bookings)
                if any_spend and total_bookings
                else None
            ),
        },
    }
