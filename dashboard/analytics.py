"""Read-only analytics over the amoCRM warehouse."""

from __future__ import annotations

import csv
import io
import os
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import pymysql
from db_security import connect as secure_connect, tls_context

from amocrm_client import load_env
from .classifier import (
    SPEED_DIAL_LASER_SOURCE,
    SPEED_DIAL_MASSAGE_SOURCE,
    TagClassifier,
    UNKNOWN,
    analytics_source_label,
    is_speed_dial_source,
)


MOSCOW = ZoneInfo("Europe/Moscow")
UTC = timezone.utc
TAG_SEPARATOR = "\x1f"
NETWORK_WIDE_BRANCH = "Без привязки к филиалу"
MIN_DASHBOARD_DATE = date(2026, 7, 1)
EXCLUDED_LOSS_REASONS = {
    23796590: "Тест",
    23796594: "Дубль",
    23882034: "Другой город",
    23882042: "Спам / не лид",
}
EXCLUDED_LOSS_REASON_IDS_SQL = ", ".join(
    str(reason_id) for reason_id in EXCLUDED_LOSS_REASONS
)


@dataclass(frozen=True)
class LeadRecord:
    lead_id: int
    created_date: date
    branch: str
    direction: str
    offer: str
    source: str
    booked: bool


@dataclass(frozen=True)
class MarketingTagRecord:
    """Lead dimensions for marketer reconciliation, derived only from tags."""

    lead_id: int
    created_date: date
    branch: str
    direction: str
    source: str
    management_branch: str
    management_direction: str
    management_source: str
    excluded_reason: Optional[str] = None


@dataclass(frozen=True)
class VerificationRecord:
    lead_id: int
    lead_name: str
    created_at: datetime
    booking_date: Optional[date]
    branch: str
    source: str
    pipeline: str
    status: str
    tags: Tuple[str, ...]
    booked: bool
    client_phone: str = ""


def conversion(leads: int, bookings: int) -> float:
    return round((bookings / leads * 100) if leads else 0.0, 2)


def build_marketing_reconciliation(
    records: Iterable[MarketingTagRecord],
    *,
    source: str = "",
    branch: str = "",
    direction: str = "",
    amo_subdomain: str = "",
) -> Dict[str, Any]:
    """Compare strict tag attribution with the management branch attribution."""
    materialized = list(records)
    tag_groups: Dict[Tuple[str, str, str], List[MarketingTagRecord]] = defaultdict(list)
    management_counts: Dict[Tuple[str, str, str], int] = defaultdict(int)

    for record in materialized:
        tag_groups[(record.source, record.branch, record.direction)].append(record)
        if not record.excluded_reason:
            management_counts[
                (
                    record.management_source,
                    record.management_branch,
                    record.management_direction,
                )
            ] += 1

    def selected(key: Tuple[str, str, str]) -> bool:
        return (
            (not source or key[0] == source)
            and (not branch or key[1] == branch)
            and (not direction or key[2] == direction)
        )

    rows: List[Dict[str, Any]] = []
    all_keys = set(tag_groups) | set(management_counts)
    for key in sorted(all_keys):
        if not selected(key):
            continue
        items = tag_groups.get(key, [])
        excluded = [record for record in items if record.excluded_reason]
        clean = [record for record in items if not record.excluded_reason]
        management_leads = management_counts.get(key, 0)
        deals = []
        for record in items:
            lead_url = ""
            if amo_subdomain:
                lead_url = (
                    f"https://{amo_subdomain}.amocrm.ru/"
                    f"leads/detail/{record.lead_id}"
                )
            deals.append(
                {
                    "lead_id": record.lead_id,
                    "lead_url": lead_url,
                    "excluded_reason": record.excluded_reason,
                }
            )
        rows.append(
            {
                "source": key[0],
                "branch": key[1],
                "direction": key[2],
                "tag_leads": len(items),
                "excluded_leads": len(excluded),
                "analytics_leads": len(clean),
                "management_leads": management_leads,
                "difference": len(clean) - management_leads,
                "deals": deals,
            }
        )

    totals = {
        "tag_leads": sum(row["tag_leads"] for row in rows),
        "excluded_leads": sum(row["excluded_leads"] for row in rows),
        "analytics_leads": sum(row["analytics_leads"] for row in rows),
        "management_leads": sum(row["management_leads"] for row in rows),
    }
    totals["difference"] = totals["analytics_leads"] - totals["management_leads"]
    return {
        "rows": rows,
        "totals": totals,
        "options": {
            "sources": sorted({record.source for record in materialized}),
            "branches": sorted({record.branch for record in materialized}),
            "directions": sorted({record.direction for record in materialized}),
        },
        "method": {
            "tag_attribution": "Источник, филиал и направление определяются только по тегам",
            "management_attribution": "Филиал записи имеет приоритет над тегом филиала",
            "difference": "Лиды в аналитике по тегам минус лиды по управленческой логике",
        },
    }


def marketing_reconciliation_to_csv(report: Mapping[str, Any]) -> str:
    stream = io.StringIO()
    stream.write("\ufeff")
    writer = csv.writer(stream, delimiter=";")
    writer.writerow(
        [
            "Источник",
            "Филиал по тегу",
            "Направление по тегу",
            "Всего по тегам",
            "Исключено",
            "В аналитике",
            "По управленческой логике",
            "Разница",
            "ID сделок",
        ]
    )
    for row in report.get("rows", []):
        writer.writerow(
            [
                row["source"],
                row["branch"],
                row["direction"],
                row["tag_leads"],
                row["excluded_leads"],
                row["analytics_leads"],
                row["management_leads"],
                row["difference"],
                ", ".join(str(deal["lead_id"]) for deal in row.get("deals", [])),
            ]
        )
    totals = report.get("totals", {})
    writer.writerow(
        [
            "ИТОГО",
            "",
            "",
            totals.get("tag_leads", 0),
            totals.get("excluded_leads", 0),
            totals.get("analytics_leads", 0),
            totals.get("management_leads", 0),
            totals.get("difference", 0),
            "",
        ]
    )
    return stream.getvalue()


def analytics_group_value(record: object, attribute: str) -> str:
    if attribute == "branch" and is_speed_dial_source(
        getattr(record, "source", "")
    ):
        return NETWORK_WIDE_BRANCH
    return str(getattr(record, attribute))


def matches_analytics_filters(
    record: object,
    selected: Mapping[str, set[str]],
    attributes: Sequence[str] = ("branch", "direction", "offer", "source"),
    *,
    allow_speed_dial_branch: bool = False,
) -> bool:
    for attribute in attributes:
        values = selected.get(attribute, set())
        if not values:
            continue
        # Скорозвон has no reliable branch attribution. It is visible only
        # for the whole network and must not leak into a selected branch.
        if (
            attribute == "branch"
            and not allow_speed_dial_branch
            and is_speed_dial_source(getattr(record, "source", ""))
        ):
            return False
        if str(getattr(record, attribute)) not in values:
            return False
    return True


class DashboardAnalytics:
    def __init__(self, env_path: str = ".env") -> None:
        self.env_path = env_path
        env = load_env(env_path)
        custom_mapping = env.get("DASHBOARD_TAG_MAPPING")
        self.classifier = TagClassifier(
            Path(custom_mapping) if custom_mapping else None
        )
        self.cache_seconds = int(env.get("DASHBOARD_CACHE_SECONDS", "300"))
        self.amo_subdomain = env.get("AMO_SUBDOMAIN", "").strip()
        self._cache: Dict[Tuple[date, date], Tuple[float, List[LeadRecord]]] = {}
        self._verification_cache: Dict[
            Tuple[date, date], Tuple[float, List[VerificationRecord]]
        ] = {}
        self._marketing_tag_cache: Dict[
            Tuple[date, date], Tuple[float, List[MarketingTagRecord]]
        ] = {}
        self._cache_lock = Lock()

    def _database(self):
        env = load_env(self.env_path)
        return secure_connect(
            host=os.environ.get("AMO_DB_HOST_OVERRIDE", env["AMO_DB_HOST"]),
            port=int(env.get("AMO_DB_PORT", "3306")),
            user=env["AMO_DB_USER"],
            password=env["AMO_DB_PASSWORD"],
            database=env["AMO_DB_NAME"],
            charset="utf8mb4",
            autocommit=True,
            ssl=tls_context(env),
            connect_timeout=int(env.get("AMO_DB_CONNECT_TIMEOUT", "10")),
            read_timeout=45,
            cursorclass=pymysql.cursors.DictCursor,
        )

    @staticmethod
    def _utc_boundary(day: date) -> datetime:
        local = datetime.combine(day, datetime_time.min, tzinfo=MOSCOW)
        return local.astimezone(UTC).replace(tzinfo=None)

    def _fetch_records(self, start: date, end: date) -> List[LeadRecord]:
        start_utc = self._utc_boundary(start)
        end_utc = self._utc_boundary(end + timedelta(days=1))
        sql = f"""
            SELECT
                l.amo_lead_id,
                l.created_at,
                l.branch_name,
                l.yclients_record_id,
                l.appointment_transition_date,
                MAX(CASE WHEN booked_status.amo_lead_id IS NOT NULL
                         THEN 1 ELSE 0 END) AS reached_booking_stage,
                GROUP_CONCAT(
                    DISTINCT t.tag_name
                    ORDER BY t.tag_name
                    SEPARATOR '{TAG_SEPARATOR}'
                ) AS tag_names
            FROM amo_leads l
            LEFT JOIN amo_lead_tags lt
              ON lt.amo_lead_id = l.amo_lead_id
            LEFT JOIN amo_tags t
              ON t.amo_tag_id = lt.amo_tag_id
            LEFT JOIN (
                SELECT DISTINCT h.amo_lead_id
                FROM amo_lead_status_history h
                JOIN amo_statuses s
                  ON s.amo_status_id = h.new_status_id
                WHERE LOWER(TRIM(s.status_name)) IN (
                    'клиент записан',
                    'клиент пришел',
                    'клиент не пришел',
                    'успешно реализовано'
                )
            ) booked_status
              ON booked_status.amo_lead_id = l.amo_lead_id
            WHERE l.is_deleted = 0
              AND NOT (
                  l.closed_at IS NOT NULL
                  AND COALESCE(
                      CAST(
                          JSON_UNQUOTE(
                              JSON_EXTRACT(l.raw_json, '$.loss_reason_id')
                          ) AS UNSIGNED
                      ),
                      0
                  ) IN ({EXCLUDED_LOSS_REASON_IDS_SQL})
              )
              AND l.created_at >= %s
              AND l.created_at < %s
            GROUP BY
                l.amo_lead_id,
                l.created_at,
                l.branch_name,
                l.yclients_record_id,
                l.appointment_transition_date
            ORDER BY l.created_at, l.amo_lead_id
        """
        connection = self._database()
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, (start_utc, end_utc))
                rows = cursor.fetchall()
        finally:
            connection.close()

        result: List[LeadRecord] = []
        for row in rows:
            raw_created = row["created_at"]
            created_utc = raw_created.replace(tzinfo=UTC)
            created_date = created_utc.astimezone(MOSCOW).date()
            tags = (row.get("tag_names") or "").split(TAG_SEPARATOR)
            dimensions = self.classifier.classify(
                tags, branch_name=row.get("branch_name")
            )
            booked = bool(
                row.get("yclients_record_id")
                or row.get("appointment_transition_date")
                or row.get("reached_booking_stage")
            )
            result.append(
                LeadRecord(
                    lead_id=int(row["amo_lead_id"]),
                    created_date=created_date,
                    branch=dimensions["branch"],
                    direction=dimensions["direction"],
                    offer=dimensions["offer"],
                    source=analytics_source_label(
                        dimensions["source"], dimensions["direction"]
                    ),
                    booked=booked,
                )
            )
        return result

    def records(self, start: date, end: date) -> List[LeadRecord]:
        key = (start, end)
        now = time.monotonic()
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached and now - cached[0] <= self.cache_seconds:
                return list(cached[1])
        records = self._fetch_records(start, end)
        with self._cache_lock:
            self._cache[key] = (now, records)
            if len(self._cache) > 12:
                oldest = min(self._cache, key=lambda item: self._cache[item][0])
                self._cache.pop(oldest, None)
        return list(records)

    def _fetch_marketing_tag_records(
        self, start: date, end: date
    ) -> List[MarketingTagRecord]:
        """Load raw leads and classify campaign dimensions strictly by tags."""
        start_utc = self._utc_boundary(start)
        end_utc = self._utc_boundary(end + timedelta(days=1))
        sql = f"""
            SELECT
                l.amo_lead_id,
                l.created_at,
                l.branch_name,
                l.closed_at,
                COALESCE(
                    CAST(
                        JSON_UNQUOTE(
                            JSON_EXTRACT(l.raw_json, '$.loss_reason_id')
                        ) AS UNSIGNED
                    ),
                    0
                ) AS loss_reason_id,
                GROUP_CONCAT(
                    DISTINCT t.tag_name
                    ORDER BY t.tag_name
                    SEPARATOR '{TAG_SEPARATOR}'
                ) AS tag_names
            FROM amo_leads l
            LEFT JOIN amo_lead_tags lt
              ON lt.amo_lead_id = l.amo_lead_id
            LEFT JOIN amo_tags t
              ON t.amo_tag_id = lt.amo_tag_id
            WHERE l.is_deleted = 0
              AND l.created_at >= %s
              AND l.created_at < %s
            GROUP BY
                l.amo_lead_id,
                l.created_at,
                l.branch_name,
                l.closed_at,
                loss_reason_id
            ORDER BY l.created_at, l.amo_lead_id
        """
        connection = self._database()
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, (start_utc, end_utc))
                rows = cursor.fetchall()
        finally:
            connection.close()

        result: List[MarketingTagRecord] = []
        for row in rows:
            raw_created = row["created_at"]
            created_utc = raw_created.replace(tzinfo=UTC)
            created_date = created_utc.astimezone(MOSCOW).date()
            tags = (row.get("tag_names") or "").split(TAG_SEPARATOR)
            tag_dimensions = self.classifier.classify(tags)
            management_dimensions = self.classifier.classify(
                tags, branch_name=row.get("branch_name")
            )
            loss_reason_id = int(row.get("loss_reason_id") or 0)
            excluded_reason = (
                EXCLUDED_LOSS_REASONS.get(loss_reason_id)
                if row.get("closed_at")
                else None
            )
            result.append(
                MarketingTagRecord(
                    lead_id=int(row["amo_lead_id"]),
                    created_date=created_date,
                    branch=tag_dimensions["branch"],
                    direction=tag_dimensions["direction"],
                    source=analytics_source_label(
                        tag_dimensions["source"], tag_dimensions["direction"]
                    ),
                    management_branch=management_dimensions["branch"],
                    management_direction=management_dimensions["direction"],
                    management_source=analytics_source_label(
                        management_dimensions["source"],
                        management_dimensions["direction"],
                    ),
                    excluded_reason=excluded_reason,
                )
            )
        return result

    def marketing_tag_records(
        self, start: date, end: date
    ) -> List[MarketingTagRecord]:
        key = (start, end)
        now = time.monotonic()
        with self._cache_lock:
            cached = self._marketing_tag_cache.get(key)
            if cached and now - cached[0] <= self.cache_seconds:
                return list(cached[1])
        records = self._fetch_marketing_tag_records(start, end)
        with self._cache_lock:
            self._marketing_tag_cache[key] = (now, records)
            if len(self._marketing_tag_cache) > 12:
                oldest = min(
                    self._marketing_tag_cache,
                    key=lambda item: self._marketing_tag_cache[item][0],
                )
                self._marketing_tag_cache.pop(oldest, None)
        return list(records)

    def marketing_reconciliation(
        self,
        start: date,
        end: date,
        *,
        source: str = "",
        branch: str = "",
        direction: str = "",
    ) -> Dict[str, Any]:
        return build_marketing_reconciliation(
            self.marketing_tag_records(start, end),
            source=source,
            branch=branch,
            direction=direction,
            amo_subdomain=self.amo_subdomain,
        )

    @staticmethod
    def _moscow_datetime(value: datetime) -> datetime:
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(MOSCOW)

    @classmethod
    def _booking_date(
        cls,
        first_booking_at: Optional[datetime],
        fallback_transition_date: Optional[date],
        created_at: Optional[datetime] = None,
        merged_first_booking_at: Optional[datetime] = None,
    ) -> Optional[date]:
        """Prefer the actual first entry into the booking stage."""
        if first_booking_at:
            candidate = cls._moscow_datetime(first_booking_at).date()
        else:
            candidate = fallback_transition_date
        if merged_first_booking_at:
            merged_candidate = cls._moscow_datetime(
                merged_first_booking_at
            ).date()
            candidate = (
                min(candidate, merged_candidate)
                if candidate
                else merged_candidate
            )
        if candidate and created_at:
            created_date = cls._moscow_datetime(created_at).date()
            return max(candidate, created_date)
        return candidate

    def _fetch_verification_records(
        self, start: date, end: date
    ) -> List[VerificationRecord]:
        start_utc = self._utc_boundary(start)
        end_utc = self._utc_boundary(end + timedelta(days=1))
        sql = f"""
            SELECT
                l.amo_lead_id,
                l.lead_name,
                l.created_at,
                l.branch_name,
                l.yclients_record_id,
                l.appointment_transition_date,
                p.pipeline_name,
                current_status.status_name,
                history.first_booking_at,
                merged_history.first_booking_at AS merged_first_booking_at,
                history.reached_booking_stage,
                tags.tag_names,
                (
                    SELECT cp.phone_original
                    FROM amo_lead_contacts lc
                    JOIN amo_contact_phones cp
                      ON cp.amo_contact_id = lc.amo_contact_id
                    WHERE lc.amo_lead_id = l.amo_lead_id
                      AND NULLIF(TRIM(cp.phone_original), '') IS NOT NULL
                    ORDER BY
                        lc.is_main_contact DESC,
                        cp.is_primary DESC,
                        cp.ordinal_position ASC,
                        lc.amo_contact_id ASC
                    LIMIT 1
                ) AS client_phone
            FROM amo_leads l
            LEFT JOIN amo_pipelines p
              ON p.amo_pipeline_id = l.amo_pipeline_id
            LEFT JOIN amo_statuses current_status
              ON current_status.amo_pipeline_id = l.amo_pipeline_id
             AND current_status.amo_status_id = l.amo_status_id
            LEFT JOIN (
                SELECT
                    h.amo_lead_id,
                    MIN(
                        CASE
                            WHEN LOWER(TRIM(s.status_name)) = 'клиент записан'
                            THEN h.changed_at
                        END
                    ) AS first_booking_at,
                    MAX(
                        CASE
                            WHEN LOWER(TRIM(s.status_name)) IN (
                                'клиент записан',
                                'клиент пришел',
                                'клиент не пришел',
                                'успешно реализовано'
                            ) THEN 1 ELSE 0
                        END
                    ) AS reached_booking_stage
                FROM amo_lead_status_history h
                JOIN amo_statuses s
                  ON s.amo_status_id = h.new_status_id
                 AND (
                     h.new_pipeline_id IS NULL
                     OR s.amo_pipeline_id = h.new_pipeline_id
                 )
                GROUP BY h.amo_lead_id
            ) history
              ON history.amo_lead_id = l.amo_lead_id
            LEFT JOIN (
                SELECT
                    merged_targets.target_lead_id,
                    MIN(
                        COALESCE(
                            old_history.first_booking_at,
                            TIMESTAMP(old_lead.appointment_transition_date)
                        )
                    ) AS first_booking_at
                FROM (
                    SELECT DISTINCT e.amo_entity_id AS target_lead_id
                    FROM amo_events e
                    WHERE e.entity_type = 'lead'
                      AND e.event_type = 'entity_merged'
                ) merged_targets
                JOIN amo_leads target_lead
                  ON target_lead.amo_lead_id = merged_targets.target_lead_id
                JOIN amo_lead_contacts target_contact
                  ON target_contact.amo_lead_id = target_lead.amo_lead_id
                JOIN amo_lead_contacts old_contact
                  ON old_contact.amo_contact_id = target_contact.amo_contact_id
                JOIN amo_leads old_lead
                  ON old_lead.amo_lead_id = old_contact.amo_lead_id
                 AND old_lead.amo_lead_id <> target_lead.amo_lead_id
                 AND old_lead.created_at = target_lead.created_at
                LEFT JOIN (
                    SELECT
                        h.amo_lead_id,
                        MIN(h.changed_at) AS first_booking_at
                    FROM amo_lead_status_history h
                    JOIN amo_statuses s
                      ON s.amo_status_id = h.new_status_id
                     AND (
                         h.new_pipeline_id IS NULL
                         OR s.amo_pipeline_id = h.new_pipeline_id
                     )
                    WHERE LOWER(TRIM(s.status_name)) = 'клиент записан'
                    GROUP BY h.amo_lead_id
                ) old_history
                  ON old_history.amo_lead_id = old_lead.amo_lead_id
                WHERE COALESCE(
                    old_history.first_booking_at,
                    TIMESTAMP(old_lead.appointment_transition_date)
                ) IS NOT NULL
                GROUP BY merged_targets.target_lead_id
            ) merged_history
              ON merged_history.target_lead_id = l.amo_lead_id
            LEFT JOIN (
                SELECT
                    lt.amo_lead_id,
                    GROUP_CONCAT(
                        DISTINCT t.tag_name
                        ORDER BY t.tag_name
                        SEPARATOR '{TAG_SEPARATOR}'
                    ) AS tag_names
                FROM amo_lead_tags lt
                JOIN amo_tags t
                  ON t.amo_tag_id = lt.amo_tag_id
                GROUP BY lt.amo_lead_id
            ) tags
              ON tags.amo_lead_id = l.amo_lead_id
            WHERE l.is_deleted = 0
              AND NOT (
                  l.closed_at IS NOT NULL
                  AND COALESCE(
                      CAST(
                          JSON_UNQUOTE(
                              JSON_EXTRACT(l.raw_json, '$.loss_reason_id')
                          ) AS UNSIGNED
                      ),
                      0
                  ) IN ({EXCLUDED_LOSS_REASON_IDS_SQL})
              )
              AND l.created_at >= %s
              AND l.created_at < %s
            ORDER BY l.created_at DESC, l.amo_lead_id DESC
        """
        connection = self._database()
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, (start_utc, end_utc))
                rows = cursor.fetchall()
        finally:
            connection.close()

        records: List[VerificationRecord] = []
        for row in rows:
            raw_tags = (row.get("tag_names") or "").split(TAG_SEPARATOR)
            tags = tuple(tag for tag in raw_tags if tag)
            dimensions = self.classifier.classify(
                tags, branch_name=row.get("branch_name")
            )
            first_booking = row.get("first_booking_at")
            transition_date = row.get("appointment_transition_date")
            booking_date = self._booking_date(
                first_booking,
                transition_date,
                row.get("created_at"),
                row.get("merged_first_booking_at"),
            )
            booked = bool(
                row.get("yclients_record_id")
                or transition_date
                or row.get("reached_booking_stage")
            )
            records.append(
                VerificationRecord(
                    lead_id=int(row["amo_lead_id"]),
                    lead_name=row.get("lead_name") or "Без названия",
                    created_at=self._moscow_datetime(row["created_at"]),
                    booking_date=booking_date,
                    branch=dimensions["branch"],
                    source=analytics_source_label(
                        dimensions["source"], dimensions["direction"]
                    ),
                    pipeline=row.get("pipeline_name") or UNKNOWN,
                    status=row.get("status_name") or UNKNOWN,
                    tags=tags,
                    booked=booked,
                    client_phone=row.get("client_phone") or "",
                )
            )
        return records

    def verification_records(
        self, start: date, end: date
    ) -> List[VerificationRecord]:
        key = (start, end)
        now = time.monotonic()
        with self._cache_lock:
            cached = self._verification_cache.get(key)
            if cached and now - cached[0] <= self.cache_seconds:
                return list(cached[1])
        records = self._fetch_verification_records(start, end)
        with self._cache_lock:
            self._verification_cache[key] = (now, records)
            if len(self._verification_cache) > 8:
                oldest = min(
                    self._verification_cache,
                    key=lambda item: self._verification_cache[item][0],
                )
                self._verification_cache.pop(oldest, None)
        return list(records)

    def verification(
        self,
        start: date,
        end: date,
        *,
        view: str = "leads",
        filters: Optional[Mapping[str, Any]] = None,
        booking_start: Optional[date] = None,
        booking_end: Optional[date] = None,
        search: str = "",
        sort: str = "created_at",
        order: str = "desc",
        page: int = 1,
        per_page: int = 50,
    ) -> Dict[str, Any]:
        if view not in {"leads", "bookings"}:
            raise ValueError("Неизвестный вид таблицы")

        selected: Dict[str, set[str]] = {}
        for attribute in ("branch", "source"):
            raw = (filters or {}).get(attribute)
            values = [raw] if isinstance(raw, str) else list(raw or [])
            selected[attribute] = {
                str(value) for value in values if value and value != "Все"
            }

        base = [
            record
            for record in self.verification_records(start, end)
            if matches_analytics_filters(
                record,
                selected,
                attributes=("branch", "source"),
                allow_speed_dial_branch=True,
            )
        ]

        query = search.strip().lower()[:200]
        if query:
            base = [
                record
                for record in base
                if query in str(record.lead_id)
                or query in record.client_phone.lower()
                or query in record.lead_name.lower()
                or any(query in tag.lower() for tag in record.tags)
            ]

        bookings = [record for record in base if record.booked]
        if booking_start:
            bookings = [
                record
                for record in bookings
                if record.booking_date and record.booking_date >= booking_start
            ]
        if booking_end:
            bookings = [
                record
                for record in bookings
                if record.booking_date and record.booking_date <= booking_end
            ]

        rows = list(base if view == "leads" else bookings)
        sort_attributes = {
            "lead_id": "lead_id",
            "client_phone": "client_phone",
            "branch": "branch",
            "source": "source",
            "created_at": "created_at",
            "booking_date": "booking_date",
            "status": "status",
            "lead_name": "lead_name",
            "tags": "tags",
        }
        sort_attribute = sort_attributes.get(sort, "created_at")
        reverse = order.lower() != "asc"
        present = [row for row in rows if getattr(row, sort_attribute) is not None]
        missing = [row for row in rows if getattr(row, sort_attribute) is None]
        present.sort(
            key=lambda row: getattr(row, sort_attribute), reverse=reverse
        )
        rows = present + missing

        total = len(rows)
        per_page = min(max(int(per_page), 10), 100000)
        pages = max(1, (total + per_page - 1) // per_page)
        page = min(max(int(page), 1), pages)
        start_index = (page - 1) * per_page
        page_rows = rows[start_index : start_index + per_page]

        serialized = []
        for row in page_rows:
            lead_url = ""
            if self.amo_subdomain:
                lead_url = (
                    f"https://{self.amo_subdomain}.amocrm.ru/"
                    f"leads/detail/{row.lead_id}"
                )
            serialized.append(
                {
                    "lead_id": row.lead_id,
                    "client_phone": row.client_phone,
                    "lead_name": row.lead_name,
                    "lead_url": lead_url,
                    "created_at": row.created_at.isoformat(timespec="minutes"),
                    "booking_date": (
                        row.booking_date.isoformat() if row.booking_date else None
                    ),
                    "branch": row.branch,
                    "source": row.source,
                    "pipeline": row.pipeline,
                    "status": row.status,
                    "tags": list(row.tags),
                }
            )

        return {
            "view": view,
            "rows": serialized,
            "counts": {
                "leads": len(base),
                "bookings": len(bookings),
            },
            "pagination": {
                "page": page,
                "per_page": per_page,
                "pages": pages,
                "total": total,
            },
            "sort": {"key": sort_attribute, "order": "desc" if reverse else "asc"},
        }

    @staticmethod
    def _filtered(
        records: Iterable[LeadRecord], filters: Mapping[str, Any]
    ) -> List[LeadRecord]:
        attributes = ("branch", "direction", "offer", "source")
        selected: Dict[str, set[str]] = {}
        for attribute in attributes:
            raw = filters.get(attribute)
            values = [raw] if isinstance(raw, str) else list(raw or [])
            selected[attribute] = {
                str(value) for value in values if value and value != "Все"
            }
        return [
            record
            for record in records
            if matches_analytics_filters(record, selected, attributes)
        ]

    @staticmethod
    def _group(
        records: Iterable[LeadRecord], keys: Sequence[str]
    ) -> List[Dict[str, Any]]:
        grouped: Dict[Tuple[str, ...], List[int]] = defaultdict(
            lambda: [0, 0]
        )
        for record in records:
            key = tuple(analytics_group_value(record, item) for item in keys)
            grouped[key][0] += 1
            grouped[key][1] += int(record.booked)
        rows: List[Dict[str, Any]] = []
        for key, (leads, bookings) in grouped.items():
            row = dict(zip(keys, key))
            row.update(
                leads=leads,
                bookings=bookings,
                conversion=conversion(leads, bookings),
            )
            rows.append(row)
        return rows

    @staticmethod
    def _top(
        rows: Sequence[Mapping[str, Any]], label_key: str, metric: str
    ) -> Optional[Dict[str, Any]]:
        eligible = [row for row in rows if row[label_key] != UNKNOWN]
        if not eligible:
            return None
        if metric == "leads":
            winner = max(
                eligible,
                key=lambda row: (row["leads"], row["bookings"], row[label_key]),
            )
        else:
            winner = max(
                eligible,
                key=lambda row: (
                    row["conversion"],
                    row["bookings"],
                    row["leads"],
                    row[label_key],
                ),
            )
        return dict(winner)

    def aggregate(
        self,
        records: Iterable[LeadRecord],
        filters: Optional[Mapping[str, Any]] = None,
        search: str = "",
    ) -> Dict[str, Any]:
        filtered = self._filtered(records, filters or {})
        offer_rows = self._group(filtered, ("offer",))
        source_rows = self._group(filtered, ("source",))
        branch_rows = self._group(filtered, ("branch",))
        detail_rows = self._group(
            filtered, ("branch", "direction", "offer", "source")
        )
        query = search.strip().lower()
        if query:
            detail_rows = [
                row
                for row in detail_rows
                if query in row["offer"].lower()
                or query in row["source"].lower()
            ]

        total_leads = len(filtered)
        total_bookings = sum(int(record.booked) for record in filtered)
        offer_rows.sort(key=lambda row: (-row["leads"], row["offer"]))
        source_rows.sort(
            key=lambda row: (-row["conversion"], -row["leads"], row["source"])
        )
        branch_rows.sort(key=lambda row: (-row["leads"], row["branch"]))
        detail_rows.sort(
            key=lambda row: (-row["leads"], row["branch"], row["offer"])
        )
        return {
            "kpi": {
                "leads": total_leads,
                "bookings": total_bookings,
                "conversion": conversion(total_leads, total_bookings),
                "top_offer_leads": self._top(offer_rows, "offer", "leads"),
                "top_offer_conversion": self._top(
                    offer_rows, "offer", "conversion"
                ),
                "top_source_conversion": self._top(
                    source_rows, "source", "conversion"
                ),
            },
            "offers": offer_rows,
            "sources": source_rows,
            "branches": branch_rows,
            "details": detail_rows,
        }

    def filter_options(self, records: Iterable[LeadRecord]) -> Dict[str, List[str]]:
        materialized = list(records)
        options = {
            key: sorted(
                {getattr(record, key) for record in materialized},
                key=lambda value: (value == UNKNOWN, value.lower()),
            )
            for key in ("branch", "direction", "offer", "source")
        }
        # Keep newly configured sources visible before their first lead arrives.
        options["source"] = sorted(
            set(options["source"])
            | {
                "РИС_xs",
                "Сайт Xsize",
                "Сайт МСК",
                "SMM_xs",
                SPEED_DIAL_MASSAGE_SOURCE,
                SPEED_DIAL_LASER_SOURCE,
            },
            key=lambda value: (value == UNKNOWN, value.lower()),
        )
        return options

    def last_sync(self) -> Optional[str]:
        connection = self._database()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT MAX(finished_at) synced_at
                    FROM amo_sync_runs
                    WHERE status = 'success'
                    """
                )
                row = cursor.fetchone()
        finally:
            connection.close()
        value = row.get("synced_at") if row else None
        if not value:
            return None
        return self._moscow_datetime(value).strftime("%Y-%m-%d %H:%M")


def details_to_csv(
    details: Sequence[Mapping[str, Any]], start: date, end: date
) -> bytes:
    stream = io.StringIO(newline="")
    stream.write("\ufeff")
    writer = csv.writer(stream, delimiter=";")
    writer.writerow(
        [
            "Филиал",
            "Направление",
            "Оффер",
            "Источник",
            "Период с",
            "Период по",
            "Лиды",
            "Записи",
            "Конверсия, %",
        ]
    )
    for row in details:
        writer.writerow(
            [
                row["branch"],
                row["direction"],
                row["offer"],
                row["source"],
                start.isoformat(),
                end.isoformat(),
                row["leads"],
                row["bookings"],
                str(row["conversion"]).replace(".", ","),
            ]
        )
    return stream.getvalue().encode("utf-8")


def verification_to_csv(
    rows: Sequence[Mapping[str, Any]], view: str
) -> bytes:
    stream = io.StringIO(newline="")
    stream.write("\ufeff")
    writer = csv.writer(stream, delimiter=";")
    headers = [
        "ID сделки",
        "Телефон клиента",
        "Филиал",
        "Источник",
        "Дата создания",
    ]
    if view == "bookings":
        headers.append("Дата записи")
    headers.extend(["Воронка", "Статус", "Название сделки", "Теги"])
    writer.writerow(headers)
    for row in rows:
        values = [
            row["lead_id"],
            row.get("client_phone") or "",
            row["branch"],
            row["source"],
            row["created_at"],
        ]
        if view == "bookings":
            values.append(row.get("booking_date") or "Дата не зафиксирована")
        values.extend(
            [
                row["pipeline"],
                row["status"],
                row["lead_name"],
                ", ".join(row.get("tags") or []),
            ]
        )
        writer.writerow(values)
    return stream.getvalue().encode("utf-8")
