"""Pure calculations for marketing spend and cost-per-result metrics."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .analytics import (
    LeadRecord,
    analytics_group_value,
    conversion,
    matches_analytics_filters,
)
from .classifier import is_speed_dial_source
from .marketing_expenses import CPL_SOURCES, PERIOD_SOURCES


MONEY_STEP = Decimal("0.01")
CONFIGURED_SOURCES = set(CPL_SOURCES) | set(PERIOD_SOURCES)
GROUP_LABELS = {
    "source": "Источник",
    "branch": "Филиал",
    "direction": "Направление",
}


def _money(value: Decimal) -> float:
    return float(value.quantize(MONEY_STEP, rounding=ROUND_HALF_UP))


def _selected_values(filters: Mapping[str, Any]) -> Dict[str, set[str]]:
    selected: Dict[str, set[str]] = {}
    for attribute in ("branch", "direction", "offer", "source"):
        raw = filters.get(attribute)
        values = [raw] if isinstance(raw, str) else list(raw or [])
        selected[attribute] = {
            str(value) for value in values if value and value != "Все"
        }
    return selected


def _matches_filters(record: LeadRecord, selected: Mapping[str, set[str]]) -> bool:
    return matches_analytics_filters(record, selected)


def _date_value(value: object) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _rate_for(
    record: LeadRecord, rates: Sequence[Mapping[str, Any]]
) -> Optional[Decimal]:
    for rate in rates:
        if rate.get("source") != record.source:
            continue
        if rate.get("direction") != record.direction:
            continue
        valid_from = _date_value(rate["valid_from"])
        raw_valid_to = rate.get("valid_to")
        valid_to = _date_value(raw_valid_to) if raw_valid_to else None
        if valid_from <= record.created_date and (
            valid_to is None or record.created_date <= valid_to
        ):
            return Decimal(str(rate["cost_per_lead"]))
    return None


def _expense_scope_matches(record: LeadRecord, expense: Mapping[str, Any]) -> bool:
    return (
        record.source == expense.get("source")
        and _date_value(expense["period_from"])
        <= record.created_date
        <= _date_value(expense["period_to"])
        and (not expense.get("branch") or record.branch == expense.get("branch"))
        and (
            not expense.get("direction")
            or record.direction == expense.get("direction")
        )
    )


def _scope_key(record: LeadRecord) -> Tuple[str, ...]:
    if record.source in CPL_SOURCES:
        return (record.source, record.direction)
    if record.source == "VK":
        return (record.source, record.branch, record.direction)
    return (record.source, "Вся сеть", "Все направления")


def _scope_label(scope: Tuple[str, ...]) -> str:
    return " · ".join(scope)


def _group_rows(
    records: Iterable[LeadRecord],
    costs: Mapping[int, Decimal],
    covered: set[int],
    drafts: set[int],
    attribute: str,
) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[LeadRecord]] = defaultdict(list)
    for record in records:
        grouped[analytics_group_value(record, attribute)].append(record)

    rows: List[Dict[str, Any]] = []
    for label, items in grouped.items():
        rows.append(_metrics_row(label, items, costs, covered, drafts))
    rows.sort(key=lambda row: (-row["leads"], row["label"]))
    return rows


def _metrics_row(
    label: str,
    items: Sequence[LeadRecord],
    costs: Mapping[int, Decimal],
    covered: set[int],
    drafts: set[int],
) -> Dict[str, Any]:
    leads = len(items)
    bookings = sum(int(record.booked) for record in items)
    configured = [
        record for record in items if record.source in CONFIGURED_SOURCES
    ]
    covered_items = [record for record in configured if record.lead_id in covered]
    spend = sum(
        (costs.get(record.lead_id, Decimal("0")) for record in items),
        Decimal("0"),
    )
    has_cost_data = bool(covered_items)
    if not configured:
        coverage = "not_configured"
    elif len(covered_items) == len(configured):
        coverage = "complete"
    else:
        coverage = "partial"
    return {
        "key": label,
        "label": label,
        "leads": leads,
        "bookings": bookings,
        "conversion": conversion(leads, bookings),
        "spend": _money(spend) if has_cost_data else None,
        "cpl": _money(spend / leads) if has_cost_data and leads else None,
        "cost_per_booking": (
            _money(spend / bookings) if has_cost_data and bookings else None
        ),
        "coverage": coverage,
        "covered_leads": len(covered_items),
        "configured_leads": len(configured),
        "uses_drafts": any(record.lead_id in drafts for record in items),
    }


def _source_branch_rows(
    records: Iterable[LeadRecord],
    costs: Mapping[int, Decimal],
    covered: set[int],
    drafts: set[int],
) -> List[Dict[str, Any]]:
    grouped: Dict[Tuple[str, str], List[LeadRecord]] = defaultdict(list)
    for record in records:
        if is_speed_dial_source(record.source):
            continue
        grouped[(record.source, record.branch)].append(record)

    rows: List[Dict[str, Any]] = []
    for (source, branch), items in grouped.items():
        row = _metrics_row(branch, items, costs, covered, drafts)
        row.update(
            {
                "key": f"{source}\x1f{branch}",
                "source": source,
                "branch": branch,
            }
        )
        rows.append(row)
    rows.sort(key=lambda row: (row["source"], -row["leads"], row["branch"]))
    return rows


def allocate_marketing_costs(
    report_records: Iterable[LeadRecord],
    allocation_records: Iterable[LeadRecord],
    *,
    rates: Sequence[Mapping[str, Any]],
    period_expenses: Sequence[Mapping[str, Any]],
    include_drafts: bool = False,
) -> Tuple[Dict[int, Decimal], set[int], set[int], Decimal]:
    """Attribute configured marketing spend to individual leads."""
    report_materialized = list(report_records)
    allocation_materialized = list(allocation_records)
    costs: Dict[int, Decimal] = defaultdict(lambda: Decimal("0"))
    covered: set[int] = set()
    draft_leads: set[int] = set()

    for record in report_materialized:
        if record.source not in CPL_SOURCES:
            continue
        rate = _rate_for(record, rates)
        if rate is not None:
            costs[record.lead_id] += rate
            covered.add(record.lead_id)

    active_expenses = [
        expense
        for expense in period_expenses
        if expense.get("status") == "confirmed"
        or (include_drafts and expense.get("status") == "draft")
    ]
    unallocated_spend = Decimal("0")
    for expense in active_expenses:
        pool = [
            record
            for record in allocation_materialized
            if _expense_scope_matches(record, expense)
        ]
        amount = Decimal(str(expense["amount"]))
        if not pool:
            unallocated_spend += amount
            continue
        share = amount / len(pool)
        for record in pool:
            costs[record.lead_id] += share
            covered.add(record.lead_id)
            if expense.get("status") == "draft":
                draft_leads.add(record.lead_id)
    return costs, covered, draft_leads, unallocated_spend


def calculate_marketing_performance(
    report_records: Iterable[LeadRecord],
    allocation_records: Iterable[LeadRecord],
    *,
    rates: Sequence[Mapping[str, Any]],
    period_expenses: Sequence[Mapping[str, Any]],
    filters: Optional[Mapping[str, Any]] = None,
    include_drafts: bool = False,
) -> Dict[str, Any]:
    report_materialized = list(report_records)
    allocation_materialized = list(allocation_records)
    selected = _selected_values(filters or {})
    filtered = [
        record
        for record in report_materialized
        if _matches_filters(record, selected)
    ]

    costs, covered, draft_leads, unallocated_spend = allocate_marketing_costs(
        report_materialized,
        allocation_materialized,
        rates=rates,
        period_expenses=period_expenses,
        include_drafts=include_drafts,
    )

    configured_filtered = [
        record for record in filtered if record.source in CONFIGURED_SOURCES
    ]
    covered_filtered = [
        record for record in configured_filtered if record.lead_id in covered
    ]
    spend = sum(
        (costs.get(record.lead_id, Decimal("0")) for record in filtered),
        Decimal("0"),
    )
    leads = len(filtered)
    bookings = sum(int(record.booked) for record in filtered)

    unit_records: Dict[Tuple[str, ...], List[LeadRecord]] = defaultdict(list)
    for record in configured_filtered:
        unit_records[_scope_key(record)].append(record)
    complete_units = sum(
        all(record.lead_id in covered for record in items)
        for items in unit_records.values()
    )
    missing_units = [
        _scope_label(scope)
        for scope, items in unit_records.items()
        if not all(record.lead_id in covered for record in items)
    ]
    unconfigured_sources = sorted(
        {
            record.source
            for record in filtered
            if record.source not in CONFIGURED_SOURCES
            and not is_speed_dial_source(record.source)
        }
    )

    if not unit_records:
        coverage_status = "empty"
        coverage_label = "Нет источников с настроенной моделью расходов"
    elif complete_units == len(unit_records):
        coverage_status = "complete"
        coverage_label = "Расходы заполнены полностью"
    else:
        coverage_status = "partial"
        coverage_label = (
            f"Расходы заполнены: {complete_units} из {len(unit_records)} срезов"
        )
    if any(record.lead_id in draft_leads for record in filtered):
        coverage_status = "draft"
        coverage_label = "В расчёте есть черновики"

    groups = {
        attribute: _group_rows(
            filtered, costs, covered, draft_leads, attribute
        )
        for attribute in ("source", "branch", "direction")
    }
    groups["source_branch"] = _source_branch_rows(
        filtered, costs, covered, draft_leads
    )
    return {
        "kpi": {
            "spend": _money(spend),
            "leads": leads,
            "bookings": bookings,
            "conversion": conversion(leads, bookings),
            "cpl": _money(spend / leads) if spend and leads else None,
            "cost_per_booking": (
                _money(spend / bookings) if spend and bookings else None
            ),
        },
        "coverage": {
            "status": coverage_status,
            "label": coverage_label,
            "complete_units": complete_units,
            "total_units": len(unit_records),
            "covered_leads": len(covered_filtered),
            "configured_leads": len(configured_filtered),
            "missing_units": sorted(missing_units),
            "unconfigured_sources": unconfigured_sources,
            "unallocated_spend": _money(unallocated_spend),
            "includes_drafts": include_drafts,
        },
        "groups": groups,
    }
