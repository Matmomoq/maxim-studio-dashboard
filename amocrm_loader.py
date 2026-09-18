"""Idempotent amoCRM -> Jino warehouse loader.

The loader reads amoCRM API v4 and writes the dedicated MariaDB/MySQL schema.
It never changes amoCRM. Timestamps are stored in UTC without timezone info.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import pymysql

from amocrm_client import AmoCRMClient


MOSCOW = ZoneInfo("Europe/Moscow")
HISTORY_START_MOSCOW = datetime(
    2026, 7, 1, 0, 0, 0, tzinfo=MOSCOW
)
MESSAGE_MARKERS = ("message", "chat", "sms")
CALL_NOTE_TYPES = {"call_in", "call_out"}
CALL_LINK_MARKERS = ("link", "record", "audio", "download")

LEAD_FIELD_IDS = {
    "yclients_record_id": 977655,
    "yclients_company_id": 977657,
    "appointment_time": 977659,
    "appointment_date": 977661,
    "branch_name": 977663,
    "visit_status": 977665,
    "appointment_source": 977667,
    "services_text": 978505,
    "appointment_transition_date": 1008203,
}

CONTACT_FIELD_IDS = {
    "yclients_client_id": 977671,
    "network_visits_count": 977675,
    "subscription_name": 977677,
    "subscription_start_date": 977679,
    "subscription_end_date": 977681,
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def from_unix(value: Any) -> Optional[datetime]:
    number = safe_int(value)
    if number is None or number <= 0:
        return None
    return datetime.fromtimestamp(number, timezone.utc).replace(tzinfo=None)


def safe_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def safe_decimal(value: Any) -> Optional[Decimal]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def safe_date(value: Any) -> Optional[date]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        parsed = from_unix(value)
        return (
            parsed.replace(tzinfo=timezone.utc).astimezone(MOSCOW).date()
            if parsed
            else None
        )
    text = str(value).strip()
    if text.isdigit() and len(text) >= 9:
        parsed = from_unix(text)
        return (
            parsed.replace(tzinfo=timezone.utc).astimezone(MOSCOW).date()
            if parsed
            else None
        )
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def safe_transition_date(
    value: Any,
    created_at_utc: Optional[datetime],
) -> Optional[date]:
    """Keep the modeled stage date on or after the lead creation date."""
    parsed = safe_date(value)
    if parsed is None or created_at_utc is None:
        return parsed
    aware_created = created_at_utc
    if aware_created.tzinfo is None:
        aware_created = aware_created.replace(tzinfo=timezone.utc)
    created_date = aware_created.astimezone(MOSCOW).date()
    return max(parsed, created_date)


def json_text(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )


def text_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json_text(value)
    return str(value)


def normalize_phone(value: Any) -> Optional[str]:
    if value is None:
        return None
    digits = re.sub(r"\D+", "", str(value))
    if not digits:
        return None
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 10:
        digits = "7" + digits
    return f"+{digits}"


def is_message_type(value: Any) -> bool:
    text = str(value or "").lower()
    return any(marker in text for marker in MESSAGE_MARKERS)


def sanitize_call_payload(value: Any) -> Any:
    if isinstance(value, list):
        return [sanitize_call_payload(item) for item in value]
    if not isinstance(value, dict):
        return value
    cleaned: Dict[str, Any] = {}
    for key, item in value.items():
        lowered = key.lower()
        if any(marker in lowered for marker in CALL_LINK_MARKERS):
            continue
        cleaned[key] = sanitize_call_payload(item)
    return cleaned


def normalized_call_status(
    raw_status: Any,
    raw_result: Any,
    duration: Any,
) -> str:
    status = safe_int(raw_status)
    if status == 4:
        return "accepted"
    if status in {2, 6}:
        return "missed"
    result = str(raw_result or "").lower()
    accepted_markers = ("принят", "успех", "возражен")
    missed_markers = (
        "пропущ",
        "не дозвон",
        "нет ответа",
        "автоответ",
        "занято",
    )
    if any(marker in result for marker in accepted_markers):
        return "accepted"
    if any(marker in result for marker in missed_markers):
        return "missed"
    return "accepted" if (safe_int(duration) or 0) > 0 else "missed"


def first_field_value(
    fields: Optional[Sequence[Dict[str, Any]]],
    field_id: int,
) -> Any:
    for field in fields or []:
        if safe_int(field.get("field_id")) != field_id:
            continue
        values = field.get("values") or []
        if values:
            return values[0].get("value")
    return None


def chunked(values: Sequence[Any], size: int = 500) -> Iterator[Sequence[Any]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def missing_entity_ids(
    requested_ids: Sequence[int],
    returned_items: Sequence[Dict[str, Any]],
) -> List[int]:
    """Return IDs omitted by a successful amoCRM collection response."""
    returned_ids = {
        entity_id
        for entity_id in (safe_int(item.get("id")) for item in returned_items)
        if entity_id is not None
    }
    return sorted(set(requested_ids) - returned_ids)


class AmoCRMLoader:
    def __init__(
        self,
        *,
        env_path: str = ".env",
        mode: str = "full",
        history_start: datetime = HISTORY_START_MOSCOW,
        selected_stages: Optional[Sequence[str]] = None,
    ) -> None:
        self.client = AmoCRMClient(env_path)
        self.db = self.client._database()
        self.mode = mode
        self.history_start = history_start
        self.snapshot_at = utc_now()
        self.selected_stages = set(selected_stages or [])
        self.run_id: Optional[int] = None
        self.records_read = 0
        self.records_inserted = 0
        self.records_updated = 0
        self.records_failed = 0
        self.stage_counts: Dict[str, int] = {}
        self.failed_stages: List[str] = []
        self.logger = logging.getLogger("amocrm_loader")
        self.account_id: Optional[int] = None
        self.tag_ids_by_name: Dict[str, int] = {}
        self.lock_acquired = False

    def close(self) -> None:
        if self.lock_acquired:
            try:
                with self.db.cursor() as cursor:
                    cursor.execute(
                        "SELECT RELEASE_LOCK(%s)", ("amocrm_jino_loader",)
                    )
            except Exception:
                self.logger.warning(
                    "Не удалось явно освободить блокировку загрузчика"
                )
        self.db.close()

    def _execute_many(
        self,
        sql: str,
        rows: Sequence[Sequence[Any]],
    ) -> int:
        if not rows:
            return 0
        with self.db.cursor() as cursor:
            affected = cursor.executemany(sql, rows)
        return affected

    def _delete_for_ids(
        self,
        table: str,
        id_column: str,
        ids: Sequence[int],
        extra_where: str = "",
        extra_params: Sequence[Any] = (),
    ) -> None:
        if not ids:
            return
        for group in chunked(list(ids), 500):
            placeholders = ",".join(["%s"] * len(group))
            sql = (
                f"DELETE FROM {table} WHERE {id_column} IN ({placeholders})"
                f"{extra_where}"
            )
            with self.db.cursor() as cursor:
                cursor.execute(sql, tuple(group) + tuple(extra_params))

    def _raw_rows(
        self,
        entity_type: str,
        items: Iterable[Dict[str, Any]],
        *,
        id_key: str = "id",
        updated_key: Optional[str] = "updated_at",
        sanitize_calls: bool = False,
    ) -> List[Tuple[Any, ...]]:
        rows: List[Tuple[Any, ...]] = []
        for item in items:
            payload = sanitize_call_payload(item) if sanitize_calls else item
            payload_string = json_text(payload)
            source_updated_at = (
                from_unix(item.get(updated_key)) if updated_key else None
            )
            rows.append(
                (
                    entity_type,
                    str(item.get(id_key)),
                    source_updated_at,
                    hashlib.sha256(payload_string.encode("utf-8")).hexdigest(),
                    payload_string,
                )
            )
        return rows

    def _insert_raw(self, rows: Sequence[Sequence[Any]]) -> None:
        self._execute_many(
            """
            INSERT IGNORE INTO amo_raw_entities (
                entity_type, entity_id, source_updated_at,
                payload_sha256, payload_json
            ) VALUES (%s, %s, %s, %s, %s)
            """,
            rows,
        )

    def iter_pages(
        self,
        path: str,
        embedded_key: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        limit: int = 250,
    ) -> Iterator[List[Dict[str, Any]]]:
        base_params = dict(params or {})
        base_params["limit"] = limit
        page = 1
        while True:
            page_params = dict(base_params)
            page_params["page"] = page
            response = self.client.get(path, page_params)
            items = response.get("_embedded", {}).get(embedded_key, [])
            if not items:
                return
            yield items
            if not response.get("_links", {}).get("next"):
                return
            page += 1

    def start_run(self) -> None:
        with self.db.cursor() as cursor:
            cursor.execute(
                "SELECT GET_LOCK(%s, 0) AS acquired",
                ("amocrm_jino_loader",),
            )
            lock_row = cursor.fetchone()
            if not lock_row or lock_row["acquired"] != 1:
                raise RuntimeError(
                    "Другой экземпляр загрузчика уже выполняется"
                )
            self.lock_acquired = True
            cursor.execute(
                """
                INSERT INTO amo_sync_runs (
                    sync_type, started_at, status, message
                ) VALUES (%s, %s, 'running', %s)
                """,
                (
                    f"{self.mode}_load",
                    utc_now(),
                    f"History starts {self.history_start.isoformat()}",
                ),
            )
            self.run_id = cursor.lastrowid
        self.db.commit()

    def finish_run(self) -> None:
        status = "success" if not self.failed_stages else "partial"
        message = json_text(
            {
                "mode": self.mode,
                "stages": self.stage_counts,
                "failed_stages": self.failed_stages,
            }
        )
        with self.db.cursor() as cursor:
            cursor.execute(
                """
                UPDATE amo_sync_runs
                SET finished_at = %s,
                    status = %s,
                    records_read = %s,
                    records_inserted = %s,
                    records_updated = %s,
                    records_failed = %s,
                    message = %s
                WHERE id = %s
                """,
                (
                    utc_now(),
                    status,
                    self.records_read,
                    self.records_inserted,
                    self.records_updated,
                    self.records_failed,
                    message,
                    self.run_id,
                ),
            )
        self.db.commit()

    def fail_run(self, error: BaseException) -> None:
        self.db.rollback()
        if self.run_id is None:
            return
        with self.db.cursor() as cursor:
            cursor.execute(
                """
                UPDATE amo_sync_runs
                SET finished_at = %s, status = 'failed',
                    records_read = %s, records_inserted = %s,
                    records_updated = %s, records_failed = %s,
                    message = %s
                WHERE id = %s
                """,
                (
                    utc_now(),
                    self.records_read,
                    self.records_inserted,
                    self.records_updated,
                    self.records_failed + 1,
                    str(error)[:10000],
                    self.run_id,
                ),
            )
        self.db.commit()

    def record_error(
        self,
        stage: str,
        error: BaseException,
        *,
        entity_id: Optional[Any] = None,
    ) -> None:
        self.records_failed += 1
        with self.db.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO amo_sync_errors (
                    sync_run_id, entity_type, entity_id,
                    error_code, error_message, retryable
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    self.run_id,
                    stage,
                    None if entity_id is None else str(entity_id),
                    type(error).__name__,
                    str(error)[:10000],
                    0,
                ),
            )
        self.db.commit()

    def update_state(self, entity_type: str) -> None:
        now = utc_now()
        with self.db.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO amo_sync_state (
                    entity_type, cursor_updated_at, first_sync_done,
                    last_success_at
                ) VALUES (%s, %s, 1, %s)
                ON DUPLICATE KEY UPDATE
                    cursor_updated_at = VALUES(cursor_updated_at),
                    first_sync_done = 1,
                    last_success_at = VALUES(last_success_at)
                """,
                (entity_type, now, now),
            )
        self.db.commit()

    def incremental_from(self, entity_type: str) -> Optional[datetime]:
        if self.mode == "full":
            return None
        with self.db.cursor() as cursor:
            cursor.execute(
                """
                SELECT last_success_at
                FROM amo_sync_state
                WHERE entity_type = %s
                """,
                (entity_type,),
            )
            row = cursor.fetchone()
        if not row or not row["last_success_at"]:
            return self.history_start.astimezone(timezone.utc).replace(
                tzinfo=None
            )
        return row["last_success_at"] - timedelta(hours=2)

    def run_stage(self, name: str, method: Any) -> None:
        self.logger.info("Этап %s: начало", name)
        before = self.records_read
        try:
            method()
            count = self.records_read - before
            self.stage_counts[name] = count
            self.update_state(name)
            self.logger.info("Этап %s: %s строк", name, count)
        except Exception as error:
            self.db.rollback()
            self.failed_stages.append(name)
            self.record_error(name, error)
            self.logger.exception("Этап %s завершился ошибкой", name)

    def sync_account(self) -> None:
        item = self.client.get("/api/v4/account")
        self.account_id = safe_int(item.get("id"))
        row = (
            self.account_id,
            item.get("name"),
            item.get("subdomain") or self.client.env["AMO_SUBDOMAIN"],
            item.get("country"),
            item.get("currency"),
            item.get("timezone"),
            from_unix(item.get("created_at")),
            from_unix(item.get("updated_at")),
            json_text(item),
        )
        self._execute_many(
            """
            INSERT INTO amo_accounts (
                amo_account_id, account_name, subdomain, country_code,
                currency_code, timezone_name, created_at, updated_at, raw_json
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                account_name=VALUES(account_name),
                subdomain=VALUES(subdomain),
                country_code=VALUES(country_code),
                currency_code=VALUES(currency_code),
                timezone_name=VALUES(timezone_name),
                created_at=VALUES(created_at),
                updated_at=VALUES(updated_at),
                synced_at=UTC_TIMESTAMP(3),
                raw_json=VALUES(raw_json)
            """,
            [row],
        )
        with self.db.cursor() as cursor:
            cursor.execute(
                "UPDATE amo_oauth_tokens SET account_id=%s WHERE id=1",
                (self.account_id,),
            )
        self._insert_raw(self._raw_rows("account", [item]))
        self.db.commit()
        self.records_read += 1

    def sync_users(self) -> None:
        total = 0
        for items in self.iter_pages("/api/v4/users", "users"):
            rows = []
            for item in items:
                rights = item.get("rights") or {}
                rows.append(
                    (
                        item["id"],
                        item.get("name") or f"User {item['id']}",
                        item.get("email"),
                        item.get("lang"),
                        safe_int(rights.get("group_id")),
                        safe_int(rights.get("role_id")),
                        int(bool(rights.get("is_active", True))),
                        int(bool(rights.get("is_admin", False))),
                        json_text(item),
                    )
                )
            self._execute_many(
                """
                INSERT INTO amo_users (
                    amo_user_id, user_name, email, language_code, group_id,
                    role_id, is_active, is_admin, raw_json
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    user_name=VALUES(user_name), email=VALUES(email),
                    language_code=VALUES(language_code),
                    group_id=VALUES(group_id), role_id=VALUES(role_id),
                    is_active=VALUES(is_active), is_admin=VALUES(is_admin),
                    synced_at=UTC_TIMESTAMP(3), raw_json=VALUES(raw_json)
                """,
                rows,
            )
            self._insert_raw(self._raw_rows("user", items))
            self.db.commit()
            total += len(items)
        self.records_read += total

    def sync_pipelines(self) -> None:
        pipelines: List[Tuple[Any, ...]] = []
        statuses: List[Tuple[Any, ...]] = []
        raw_items: List[Dict[str, Any]] = []
        for items in self.iter_pages(
            "/api/v4/leads/pipelines", "pipelines", limit=50
        ):
            raw_items.extend(items)
            for item in items:
                pipelines.append(
                    (
                        item["id"],
                        item.get("name") or f"Pipeline {item['id']}",
                        item.get("sort"),
                        int(bool(item.get("is_main"))),
                        int(bool(item.get("is_unsorted_on"))),
                        int(bool(item.get("is_archive"))),
                        json_text(item),
                    )
                )
                for status in (
                    item.get("_embedded", {}).get("statuses", []) or []
                ):
                    status_id = safe_int(status.get("id"))
                    status_type = safe_int(status.get("type"))
                    statuses.append(
                        (
                            item["id"],
                            status_id,
                            status.get("name") or f"Status {status_id}",
                            status.get("sort"),
                            status_type,
                            int(status_id == 142 or status_type == 142),
                            int(status_id == 143 or status_type == 143),
                            json_text(status),
                        )
                    )
        self._execute_many(
            """
            INSERT INTO amo_pipelines (
                amo_pipeline_id, pipeline_name, sort_order, is_main,
                is_unsorted_on, is_archive, raw_json
            ) VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                pipeline_name=VALUES(pipeline_name),
                sort_order=VALUES(sort_order), is_main=VALUES(is_main),
                is_unsorted_on=VALUES(is_unsorted_on),
                is_archive=VALUES(is_archive),
                synced_at=UTC_TIMESTAMP(3), raw_json=VALUES(raw_json)
            """,
            pipelines,
        )
        self._execute_many(
            """
            INSERT INTO amo_statuses (
                amo_pipeline_id, amo_status_id, status_name, sort_order,
                status_type, is_final_success, is_final_failure, raw_json
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                status_name=VALUES(status_name), sort_order=VALUES(sort_order),
                status_type=VALUES(status_type),
                is_final_success=VALUES(is_final_success),
                is_final_failure=VALUES(is_final_failure),
                synced_at=UTC_TIMESTAMP(3), raw_json=VALUES(raw_json)
            """,
            statuses,
        )
        self._insert_raw(self._raw_rows("pipeline", raw_items))
        self.db.commit()
        self.records_read += len(pipelines) + len(statuses)

    def sync_custom_fields(self) -> None:
        total = 0
        rows: List[Tuple[Any, ...]] = []
        raw_rows: List[Tuple[Any, ...]] = []
        for entity_type in ("leads", "contacts", "companies"):
            for items in self.iter_pages(
                f"/api/v4/{entity_type}/custom_fields",
                "custom_fields",
            ):
                for item in items:
                    required = bool(item.get("required_statuses"))
                    rows.append(
                        (
                            entity_type.rstrip("s"),
                            item["id"],
                            item.get("name") or f"Field {item['id']}",
                            item.get("code"),
                            item.get("type") or "unknown",
                            safe_int(item.get("group_id")),
                            item.get("sort"),
                            int(bool(item.get("is_api_only"))),
                            int(required),
                            (
                                None
                                if item.get("is_deletable") is None
                                else int(bool(item.get("is_deletable")))
                            ),
                            None,
                            json_text(item),
                        )
                    )
                    raw_rows.extend(
                        self._raw_rows(
                            f"{entity_type.rstrip('s')}_custom_field",
                            [item],
                        )
                    )
                total += len(items)
        self._execute_many(
            """
            INSERT INTO amo_custom_fields (
                entity_type, amo_field_id, field_name, field_code,
                field_type, group_id, sort_order, is_api_only, is_required,
                is_deletable, is_visible, raw_json
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                field_name=VALUES(field_name), field_code=VALUES(field_code),
                field_type=VALUES(field_type), group_id=VALUES(group_id),
                sort_order=VALUES(sort_order),
                is_api_only=VALUES(is_api_only),
                is_required=VALUES(is_required),
                is_deletable=VALUES(is_deletable),
                is_visible=VALUES(is_visible),
                synced_at=UTC_TIMESTAMP(3), raw_json=VALUES(raw_json)
            """,
            rows,
        )
        self._insert_raw(raw_rows)
        self.db.commit()
        self.records_read += total

    def sync_tags(self) -> None:
        total = 0
        all_items: List[Dict[str, Any]] = []
        for items in self.iter_pages("/api/v4/leads/tags", "tags"):
            all_items.extend(items)
        rows = [
            (
                item["id"],
                item.get("name") or f"Tag {item['id']}",
                item.get("color"),
                json_text(item),
            )
            for item in all_items
        ]
        self._execute_many(
            """
            INSERT INTO amo_tags (amo_tag_id, tag_name, color, raw_json)
            VALUES (%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                tag_name=VALUES(tag_name), color=VALUES(color),
                synced_at=UTC_TIMESTAMP(3), raw_json=VALUES(raw_json)
            """,
            rows,
        )
        self._insert_raw(self._raw_rows("tag", all_items))
        self.db.commit()
        self.tag_ids_by_name = {
            str(item.get("name")): int(item["id"]) for item in all_items
        }
        total += len(all_items)
        self.records_read += total

    def _entity_filter(
        self, entity_type: str, *, timestamp_field: str = "updated_at"
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            f"order[{timestamp_field}]": "asc",
            f"filter[{timestamp_field}][to]": int(
                self.snapshot_at.replace(tzinfo=timezone.utc).timestamp()
            ),
        }
        start = self.incremental_from(entity_type)
        if start is None:
            return params
        params[f"filter[{timestamp_field}][from]"] = int(
                start.replace(tzinfo=timezone.utc).timestamp()
            )
        return params

    def _custom_value_rows(
        self,
        entity_type: str,
        entity: Dict[str, Any],
    ) -> List[Tuple[Any, ...]]:
        rows: List[Tuple[Any, ...]] = []
        for field in entity.get("custom_fields_values") or []:
            field_id = safe_int(field.get("field_id"))
            field_type = str(field.get("field_type") or "")
            if field_id is None:
                continue
            for ordinal, value_item in enumerate(
                field.get("values") or [], start=1
            ):
                value = value_item.get("value")
                value_number = safe_decimal(value)
                value_datetime = None
                if field_type in {"date", "date_time", "birthday"}:
                    value_datetime = from_unix(value)
                    if value_datetime is None:
                        parsed_date = safe_date(value)
                        if parsed_date:
                            value_datetime = datetime.combine(
                                parsed_date, datetime.min.time()
                            )
                rows.append(
                    (
                        entity_type,
                        entity["id"],
                        field_id,
                        ordinal,
                        text_value(value),
                        value_number,
                        value_datetime,
                        safe_int(value_item.get("enum_id")),
                        value_item.get("enum_code"),
                        json_text(value_item),
                    )
                )
        return rows

    def _replace_custom_values(
        self,
        entity_type: str,
        ids: Sequence[int],
        rows: Sequence[Sequence[Any]],
    ) -> None:
        self._delete_for_ids(
            "amo_custom_field_values",
            "amo_entity_id",
            ids,
            " AND entity_type=%s",
            (entity_type,),
        )
        self._execute_many(
            """
            INSERT INTO amo_custom_field_values (
                entity_type, amo_entity_id, amo_field_id, ordinal_position,
                value_text, value_number, value_datetime, enum_id, enum_code,
                raw_json
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            rows,
        )

    def sync_leads(self) -> None:
        params = self._entity_filter("leads")
        params["with"] = "contacts"
        total = 0
        for items in self.iter_pages("/api/v4/leads", "leads", params):
            ids = [int(item["id"]) for item in items]
            lead_rows: List[Tuple[Any, ...]] = []
            custom_rows: List[Tuple[Any, ...]] = []
            tag_rows: List[Tuple[Any, ...]] = []
            contact_rows: List[Tuple[Any, ...]] = []
            identity_rows: List[Tuple[Any, ...]] = []
            for item in items:
                fields = item.get("custom_fields_values") or []
                record_id = safe_int(
                    first_field_value(
                        fields, LEAD_FIELD_IDS["yclients_record_id"]
                    )
                )
                company_id = safe_int(
                    first_field_value(
                        fields, LEAD_FIELD_IDS["yclients_company_id"]
                    )
                )
                lead_rows.append(
                    (
                        item["id"],
                        item.get("name") or f"Lead {item['id']}",
                        safe_decimal(item.get("price")) or Decimal("0"),
                        item.get("pipeline_id"),
                        item.get("status_id"),
                        item.get("responsible_user_id"),
                        item.get("group_id"),
                        item.get("created_by"),
                        item.get("updated_by"),
                        from_unix(item.get("created_at")),
                        from_unix(item.get("updated_at")),
                        from_unix(item.get("closed_at")),
                        from_unix(item.get("closest_task_at")),
                        int(bool(item.get("is_deleted"))),
                        record_id,
                        company_id,
                        safe_date(
                            first_field_value(
                                fields,
                                LEAD_FIELD_IDS["appointment_date"],
                            )
                        ),
                        text_value(
                            first_field_value(
                                fields,
                                LEAD_FIELD_IDS["appointment_time"],
                            )
                        ),
                        text_value(
                            first_field_value(
                                fields, LEAD_FIELD_IDS["branch_name"]
                            )
                        ),
                        text_value(
                            first_field_value(
                                fields, LEAD_FIELD_IDS["visit_status"]
                            )
                        ),
                        text_value(
                            first_field_value(
                                fields, LEAD_FIELD_IDS["services_text"]
                            )
                        ),
                        safe_transition_date(
                            first_field_value(
                                fields,
                                LEAD_FIELD_IDS[
                                    "appointment_transition_date"
                                ],
                            ),
                            from_unix(item.get("created_at")),
                        ),
                        text_value(
                            first_field_value(
                                fields,
                                LEAD_FIELD_IDS["appointment_source"],
                            )
                        ),
                        json_text(item),
                    )
                )
                custom_rows.extend(self._custom_value_rows("lead", item))
                embedded = item.get("_embedded") or {}
                for tag in embedded.get("tags") or []:
                    tag_id = safe_int(tag.get("id"))
                    if tag_id:
                        tag_rows.append((item["id"], tag_id))
                for contact in embedded.get("contacts") or []:
                    contact_rows.append(
                        (
                            item["id"],
                            contact["id"],
                            int(bool(contact.get("is_main"))),
                        )
                    )
                if record_id is not None or company_id is not None:
                    identity_rows.append(
                        (
                            item["id"],
                            None,
                            company_id,
                            record_id,
                            None,
                            "lead_custom_fields",
                            Decimal("1.0000"),
                        )
                    )
            self._execute_many(
                """
                INSERT INTO amo_leads (
                    amo_lead_id, lead_name, price, amo_pipeline_id,
                    amo_status_id, responsible_user_id, group_id, created_by,
                    updated_by, created_at, updated_at, closed_at,
                    closest_task_at, is_deleted, yclients_record_id,
                    yclients_company_id, appointment_date, appointment_time,
                    branch_name, visit_status, services_text,
                    appointment_transition_date, appointment_source, raw_json
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                )
                ON DUPLICATE KEY UPDATE
                    lead_name=VALUES(lead_name), price=VALUES(price),
                    amo_pipeline_id=VALUES(amo_pipeline_id),
                    amo_status_id=VALUES(amo_status_id),
                    responsible_user_id=VALUES(responsible_user_id),
                    group_id=VALUES(group_id), created_by=VALUES(created_by),
                    updated_by=VALUES(updated_by),
                    created_at=VALUES(created_at), updated_at=VALUES(updated_at),
                    closed_at=VALUES(closed_at),
                    closest_task_at=VALUES(closest_task_at),
                    is_deleted=VALUES(is_deleted),
                    yclients_record_id=VALUES(yclients_record_id),
                    yclients_company_id=VALUES(yclients_company_id),
                    appointment_date=VALUES(appointment_date),
                    appointment_time=VALUES(appointment_time),
                    branch_name=VALUES(branch_name),
                    visit_status=VALUES(visit_status),
                    services_text=VALUES(services_text),
                    appointment_transition_date=
                        VALUES(appointment_transition_date),
                    appointment_source=VALUES(appointment_source),
                    synced_at=UTC_TIMESTAMP(3), raw_json=VALUES(raw_json)
                """,
                lead_rows,
            )
            self._replace_custom_values("lead", ids, custom_rows)
            self._delete_for_ids("amo_lead_tags", "amo_lead_id", ids)
            self._execute_many(
                """
                INSERT INTO amo_lead_tags (amo_lead_id, amo_tag_id)
                VALUES (%s,%s)
                """,
                tag_rows,
            )
            self._delete_for_ids("amo_lead_contacts", "amo_lead_id", ids)
            self._execute_many(
                """
                INSERT INTO amo_lead_contacts (
                    amo_lead_id, amo_contact_id, is_main_contact
                ) VALUES (%s,%s,%s)
                """,
                contact_rows,
            )
            self._delete_for_ids(
                "amo_yclients_identity_map",
                "amo_lead_id",
                ids,
                " AND match_method=%s",
                ("lead_custom_fields",),
            )
            self._execute_many(
                """
                INSERT INTO amo_yclients_identity_map (
                    amo_lead_id, amo_contact_id, yclients_company_id,
                    yclients_record_id, yclients_client_id, match_method,
                    match_confidence
                ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,
                identity_rows,
            )
            self._insert_raw(self._raw_rows("lead", items))
            self.db.commit()
            total += len(items)
            self.logger.info("Сделки: %s", total)
        inactive = self.reconcile_inactive_leads()
        if inactive:
            self.logger.info(
                "Сделки: исключено после удаления или объединения: %s",
                inactive,
            )
        self.records_read += total

    def reconcile_inactive_leads(self, batch_size: int = 100) -> int:
        """Mark warehouse leads omitted by amoCRM's active lead collection.

        amoCRM stops returning deleted and merged source leads from the list
        endpoint. Incremental loading therefore cannot receive a tombstone for
        them. A successful ID-filtered collection response is used as the
        source of truth; database rows are updated only after every API batch
        has completed successfully.
        """
        with self.db.cursor() as cursor:
            cursor.execute(
                """
                SELECT amo_lead_id
                FROM amo_leads
                WHERE is_deleted = 0
                ORDER BY amo_lead_id
                """
            )
            active_ids = [int(row["amo_lead_id"]) for row in cursor.fetchall()]

        missing_ids: List[int] = []
        for group in chunked(active_ids, batch_size):
            response = self.client.get(
                "/api/v4/leads",
                {
                    "filter[id][]": list(group),
                    "limit": min(250, max(batch_size, len(group))),
                },
            )
            items = response.get("_embedded", {}).get("leads", [])
            missing_ids.extend(missing_entity_ids(group, items))

        if not missing_ids:
            return 0

        affected = 0
        for group in chunked(missing_ids, 500):
            placeholders = ",".join(["%s"] * len(group))
            with self.db.cursor() as cursor:
                affected += cursor.execute(
                    f"""
                    UPDATE amo_leads
                    SET is_deleted = 1,
                        synced_at = UTC_TIMESTAMP(3)
                    WHERE is_deleted = 0
                      AND amo_lead_id IN ({placeholders})
                    """,
                    tuple(group),
                )
        self.db.commit()
        return affected

    def _phone_email_rows(
        self, contact: Dict[str, Any]
    ) -> Tuple[List[Tuple[Any, ...]], List[Tuple[Any, ...]]]:
        phone_rows: List[Tuple[Any, ...]] = []
        email_rows: List[Tuple[Any, ...]] = []
        for field in contact.get("custom_fields_values") or []:
            code = str(field.get("field_code") or "").upper()
            name = str(field.get("field_name") or "").lower()
            is_phone = code == "PHONE" or "телефон" in name
            is_email = code == "EMAIL" or "email" in name or "e-mail" in name
            if not is_phone and not is_email:
                continue
            for ordinal, item in enumerate(field.get("values") or [], start=1):
                value = text_value(item.get("value"))
                if not value:
                    continue
                if is_phone:
                    phone_rows.append(
                        (
                            contact["id"],
                            ordinal,
                            value,
                            normalize_phone(value),
                            item.get("enum_code"),
                            safe_int(item.get("enum_id")),
                            int(ordinal == 1),
                        )
                    )
                elif is_email:
                    email_rows.append(
                        (
                            contact["id"],
                            ordinal,
                            value,
                            value.strip().lower(),
                            item.get("enum_code"),
                            safe_int(item.get("enum_id")),
                            int(ordinal == 1),
                        )
                    )
        return phone_rows, email_rows

    def sync_contacts(self) -> None:
        params = self._entity_filter("contacts")
        params["with"] = "leads"
        total = 0
        for items in self.iter_pages("/api/v4/contacts", "contacts", params):
            ids = [int(item["id"]) for item in items]
            contact_rows: List[Tuple[Any, ...]] = []
            custom_rows: List[Tuple[Any, ...]] = []
            phone_rows: List[Tuple[Any, ...]] = []
            email_rows: List[Tuple[Any, ...]] = []
            lead_contact_rows: List[Tuple[Any, ...]] = []
            identity_rows: List[Tuple[Any, ...]] = []
            for item in items:
                fields = item.get("custom_fields_values") or []
                client_id = safe_int(
                    first_field_value(
                        fields, CONTACT_FIELD_IDS["yclients_client_id"]
                    )
                )
                contact_rows.append(
                    (
                        item["id"],
                        item.get("name"),
                        item.get("first_name"),
                        item.get("last_name"),
                        item.get("responsible_user_id"),
                        item.get("group_id"),
                        item.get("created_by"),
                        item.get("updated_by"),
                        from_unix(item.get("created_at")),
                        from_unix(item.get("updated_at")),
                        from_unix(item.get("closest_task_at")),
                        client_id,
                        None,
                        safe_int(
                            first_field_value(
                                fields,
                                CONTACT_FIELD_IDS["network_visits_count"],
                            )
                        ),
                        text_value(
                            first_field_value(
                                fields,
                                CONTACT_FIELD_IDS["subscription_name"],
                            )
                        ),
                        safe_date(
                            first_field_value(
                                fields,
                                CONTACT_FIELD_IDS["subscription_start_date"],
                            )
                        ),
                        safe_date(
                            first_field_value(
                                fields,
                                CONTACT_FIELD_IDS["subscription_end_date"],
                            )
                        ),
                        int(bool(item.get("is_deleted"))),
                        json_text(item),
                    )
                )
                custom_rows.extend(self._custom_value_rows("contact", item))
                phones, emails = self._phone_email_rows(item)
                phone_rows.extend(phones)
                email_rows.extend(emails)
                for lead in (
                    item.get("_embedded", {}).get("leads", []) or []
                ):
                    lead_contact_rows.append(
                        (lead["id"], item["id"], 0)
                    )
                if client_id is not None:
                    identity_rows.append(
                        (
                            None,
                            item["id"],
                            None,
                            None,
                            client_id,
                            "contact_custom_field",
                            Decimal("0.9500"),
                        )
                    )
            self._execute_many(
                """
                INSERT INTO amo_contacts (
                    amo_contact_id, contact_name, first_name, last_name,
                    responsible_user_id, group_id, created_by, updated_by,
                    created_at, updated_at, closest_task_at,
                    yclients_client_id, client_category, network_visits_count,
                    subscription_name, subscription_start_date,
                    subscription_end_date, is_deleted, raw_json
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s
                )
                ON DUPLICATE KEY UPDATE
                    contact_name=VALUES(contact_name),
                    first_name=VALUES(first_name),
                    last_name=VALUES(last_name),
                    responsible_user_id=VALUES(responsible_user_id),
                    group_id=VALUES(group_id), created_by=VALUES(created_by),
                    updated_by=VALUES(updated_by),
                    created_at=VALUES(created_at), updated_at=VALUES(updated_at),
                    closest_task_at=VALUES(closest_task_at),
                    yclients_client_id=VALUES(yclients_client_id),
                    client_category=VALUES(client_category),
                    network_visits_count=VALUES(network_visits_count),
                    subscription_name=VALUES(subscription_name),
                    subscription_start_date=VALUES(subscription_start_date),
                    subscription_end_date=VALUES(subscription_end_date),
                    is_deleted=VALUES(is_deleted),
                    synced_at=UTC_TIMESTAMP(3), raw_json=VALUES(raw_json)
                """,
                contact_rows,
            )
            self._replace_custom_values("contact", ids, custom_rows)
            self._delete_for_ids("amo_contact_phones", "amo_contact_id", ids)
            self._execute_many(
                """
                INSERT INTO amo_contact_phones (
                    amo_contact_id, ordinal_position, phone_original,
                    phone_normalized, phone_type, enum_id, is_primary
                ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,
                phone_rows,
            )
            self._delete_for_ids("amo_contact_emails", "amo_contact_id", ids)
            self._execute_many(
                """
                INSERT INTO amo_contact_emails (
                    amo_contact_id, ordinal_position, email, email_normalized,
                    email_type, enum_id, is_primary
                ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,
                email_rows,
            )
            # Relations loaded from leads carry the reliable is_main flag.
            self._execute_many(
                """
                INSERT INTO amo_lead_contacts (
                    amo_lead_id, amo_contact_id, is_main_contact
                ) VALUES (%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    synced_at=UTC_TIMESTAMP(3)
                """,
                lead_contact_rows,
            )
            self._delete_for_ids(
                "amo_yclients_identity_map",
                "amo_contact_id",
                ids,
                " AND match_method=%s",
                ("contact_custom_field",),
            )
            self._execute_many(
                """
                INSERT INTO amo_yclients_identity_map (
                    amo_lead_id, amo_contact_id, yclients_company_id,
                    yclients_record_id, yclients_client_id, match_method,
                    match_confidence
                ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,
                identity_rows,
            )
            self._insert_raw(self._raw_rows("contact", items))
            self.db.commit()
            total += len(items)
            self.logger.info("Контакты: %s", total)
        self.records_read += total

    def sync_companies(self) -> None:
        params = self._entity_filter("companies")
        total = 0
        for items in self.iter_pages("/api/v4/companies", "companies", params):
            ids = [int(item["id"]) for item in items]
            rows = [
                (
                    item["id"],
                    item.get("name"),
                    item.get("responsible_user_id"),
                    item.get("group_id"),
                    item.get("created_by"),
                    item.get("updated_by"),
                    from_unix(item.get("created_at")),
                    from_unix(item.get("updated_at")),
                    from_unix(item.get("closest_task_at")),
                    int(bool(item.get("is_deleted"))),
                    json_text(item),
                )
                for item in items
            ]
            custom_rows = [
                row
                for item in items
                for row in self._custom_value_rows("company", item)
            ]
            self._execute_many(
                """
                INSERT INTO amo_companies (
                    amo_company_id, company_name, responsible_user_id,
                    group_id, created_by, updated_by, created_at, updated_at,
                    closest_task_at, is_deleted, raw_json
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    company_name=VALUES(company_name),
                    responsible_user_id=VALUES(responsible_user_id),
                    group_id=VALUES(group_id), created_by=VALUES(created_by),
                    updated_by=VALUES(updated_by),
                    created_at=VALUES(created_at), updated_at=VALUES(updated_at),
                    closest_task_at=VALUES(closest_task_at),
                    is_deleted=VALUES(is_deleted),
                    synced_at=UTC_TIMESTAMP(3), raw_json=VALUES(raw_json)
                """,
                rows,
            )
            self._replace_custom_values("company", ids, custom_rows)
            self._insert_raw(self._raw_rows("company", items))
            self.db.commit()
            total += len(items)
        self.records_read += total

    def sync_tasks(self) -> None:
        params = self._entity_filter("tasks")
        total = 0
        for items in self.iter_pages("/api/v4/tasks", "tasks", params):
            rows = []
            for item in items:
                result = item.get("result") or {}
                rows.append(
                    (
                        item["id"],
                        item.get("entity_type"),
                        item.get("entity_id"),
                        item.get("responsible_user_id"),
                        item.get("created_by"),
                        item.get("updated_by"),
                        item.get("task_type_id"),
                        item.get("text"),
                        result.get("text"),
                        int(bool(item.get("is_completed"))),
                        from_unix(item.get("complete_till")),
                        from_unix(item.get("created_at")),
                        from_unix(item.get("updated_at")),
                        json_text(item),
                    )
                )
            self._execute_many(
                """
                INSERT INTO amo_tasks (
                    amo_task_id, entity_type, amo_entity_id,
                    responsible_user_id, created_by, updated_by, task_type_id,
                    task_text, result_text, is_completed, complete_till,
                    created_at, updated_at, raw_json
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    entity_type=VALUES(entity_type),
                    amo_entity_id=VALUES(amo_entity_id),
                    responsible_user_id=VALUES(responsible_user_id),
                    created_by=VALUES(created_by), updated_by=VALUES(updated_by),
                    task_type_id=VALUES(task_type_id),
                    task_text=VALUES(task_text),
                    result_text=VALUES(result_text),
                    is_completed=VALUES(is_completed),
                    complete_till=VALUES(complete_till),
                    created_at=VALUES(created_at),
                    updated_at=VALUES(updated_at),
                    synced_at=UTC_TIMESTAMP(3), raw_json=VALUES(raw_json)
                """,
                rows,
            )
            self._insert_raw(self._raw_rows("task", items))
            self.db.commit()
            total += len(items)
            self.logger.info("Задачи: %s", total)
        self.records_read += total

    def _history_from_timestamp(self, entity_type: str) -> int:
        earliest = self.history_start.astimezone(timezone.utc).replace(
            tzinfo=None
        )
        incremental = self.incremental_from(entity_type)
        start = max(earliest, incremental) if incremental else earliest
        return int(start.replace(tzinfo=timezone.utc).timestamp())

    @staticmethod
    def _event_value(
        event: Dict[str, Any],
        side: str,
        key: str,
    ) -> Optional[Dict[str, Any]]:
        for item in event.get(side) or []:
            value = item.get(key)
            if isinstance(value, dict):
                return value
        return None

    def sync_events(self) -> None:
        params = {
            "filter[created_at][from]": self._history_from_timestamp("events"),
            "filter[created_at][to]": int(
                self.snapshot_at.replace(tzinfo=timezone.utc).timestamp()
            ),
            "order[created_at]": "asc",
        }
        total = 0
        accepted = 0
        status_rows: List[Tuple[Any, ...]] = []
        tag_rows: List[Tuple[Any, ...]] = []
        for items in self.iter_pages("/api/v4/events", "events", params):
            event_rows: List[Tuple[Any, ...]] = []
            raw_items: List[Dict[str, Any]] = []
            for item in items:
                event_type = str(item.get("type") or "")
                if is_message_type(event_type):
                    continue
                event_rows.append(
                    (
                        str(item["id"]),
                        event_type,
                        item.get("entity_type"),
                        item.get("entity_id"),
                        item.get("created_by"),
                        from_unix(item.get("created_at")),
                        json_text(item.get("value_before") or []),
                        json_text(item.get("value_after") or []),
                        json_text(item),
                    )
                )
                raw_items.append(item)
                if (
                    event_type == "lead_status_changed"
                    and item.get("entity_type") == "lead"
                ):
                    before = self._event_value(
                        item, "value_before", "lead_status"
                    ) or {}
                    after = self._event_value(
                        item, "value_after", "lead_status"
                    ) or {}
                    new_status = safe_int(after.get("id"))
                    if new_status is not None:
                        status_rows.append(
                            (
                                str(item["id"]),
                                item["entity_id"],
                                safe_int(before.get("pipeline_id")),
                                safe_int(before.get("id")),
                                safe_int(after.get("pipeline_id")),
                                new_status,
                                from_unix(item.get("created_at")),
                                item.get("created_by"),
                                json_text(item),
                            )
                        )
                if (
                    event_type in {"entity_tag_added", "entity_tag_deleted"}
                    and item.get("entity_type") == "lead"
                ):
                    side = (
                        "value_after"
                        if event_type == "entity_tag_added"
                        else "value_before"
                    )
                    tag = self._event_value(item, side, "tag") or {}
                    tag_id = self.tag_ids_by_name.get(str(tag.get("name")))
                    if tag_id is not None:
                        tag_rows.append(
                            (
                                str(item["id"]),
                                item["entity_id"],
                                tag_id,
                                (
                                    "added"
                                    if event_type == "entity_tag_added"
                                    else "removed"
                                ),
                                from_unix(item.get("created_at")),
                                item.get("created_by"),
                                json_text(item),
                            )
                        )
            self._execute_many(
                """
                INSERT INTO amo_events (
                    event_id, event_type, entity_type, amo_entity_id,
                    created_by, occurred_at, value_before_json,
                    value_after_json, raw_json
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    event_type=VALUES(event_type),
                    entity_type=VALUES(entity_type),
                    amo_entity_id=VALUES(amo_entity_id),
                    created_by=VALUES(created_by),
                    occurred_at=VALUES(occurred_at),
                    value_before_json=VALUES(value_before_json),
                    value_after_json=VALUES(value_after_json),
                    raw_json=VALUES(raw_json),
                    synced_at=UTC_TIMESTAMP(3)
                """,
                event_rows,
            )
            self._insert_raw(self._raw_rows("event", raw_items))
            self.db.commit()
            total += len(items)
            accepted += len(event_rows)
            self.logger.info(
                "События: просмотрено %s, сохранено %s", total, accepted
            )
        self._execute_many(
            """
            INSERT INTO amo_lead_status_history (
                event_id, amo_lead_id, previous_pipeline_id,
                previous_status_id, new_pipeline_id, new_status_id,
                changed_at, changed_by, raw_json
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                amo_lead_id=VALUES(amo_lead_id),
                previous_pipeline_id=VALUES(previous_pipeline_id),
                previous_status_id=VALUES(previous_status_id),
                new_pipeline_id=VALUES(new_pipeline_id),
                new_status_id=VALUES(new_status_id),
                changed_at=VALUES(changed_at),
                changed_by=VALUES(changed_by),
                raw_json=VALUES(raw_json)
            """,
            status_rows,
        )
        self._execute_many(
            """
            INSERT INTO amo_lead_tag_history (
                event_id, amo_lead_id, amo_tag_id, action,
                changed_at, changed_by, raw_json
            ) VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                amo_lead_id=VALUES(amo_lead_id),
                changed_at=VALUES(changed_at),
                changed_by=VALUES(changed_by),
                raw_json=VALUES(raw_json)
            """,
            tag_rows,
        )
        self.db.commit()
        self._recalculate_status_history()
        self.records_read += total

    def _recalculate_status_history(self) -> None:
        with self.db.cursor() as cursor:
            cursor.execute(
                """
                SELECT event_id, amo_lead_id, changed_at
                FROM amo_lead_status_history
                ORDER BY amo_lead_id, changed_at, event_id
                """
            )
            rows = cursor.fetchall()
        updates: List[Tuple[Any, ...]] = []
        previous: Dict[int, datetime] = {}
        sequence: Dict[int, int] = defaultdict(int)
        for row in rows:
            lead_id = int(row["amo_lead_id"])
            sequence[lead_id] += 1
            prior = previous.get(lead_id)
            duration = (
                max(0, int((row["changed_at"] - prior).total_seconds()))
                if prior
                else None
            )
            updates.append(
                (sequence[lead_id], duration, row["event_id"])
            )
            previous[lead_id] = row["changed_at"]
        with self.db.cursor() as cursor:
            cursor.execute(
                """
                CREATE TEMPORARY TABLE tmp_amo_status_history_calc (
                    event_id VARCHAR(64) NOT NULL PRIMARY KEY,
                    stage_entry_number INT UNSIGNED NOT NULL,
                    previous_stage_duration_sec BIGINT UNSIGNED NULL
                ) ENGINE=InnoDB
                """
            )
        self._execute_many(
            """
            INSERT INTO tmp_amo_status_history_calc (
                stage_entry_number, previous_stage_duration_sec, event_id
            ) VALUES (%s,%s,%s)
            """,
            updates,
        )
        with self.db.cursor() as cursor:
            cursor.execute(
                """
                UPDATE amo_lead_status_history AS history
                JOIN tmp_amo_status_history_calc AS calculated
                    ON calculated.event_id = history.event_id
                SET history.stage_entry_number =
                        calculated.stage_entry_number,
                    history.previous_stage_duration_sec =
                        calculated.previous_stage_duration_sec
                """
            )
            cursor.execute("DROP TEMPORARY TABLE tmp_amo_status_history_calc")
        self.db.commit()

    def _contact_leads(
        self, contact_ids: Sequence[int]
    ) -> Dict[int, List[int]]:
        result: Dict[int, List[int]] = defaultdict(list)
        if not contact_ids:
            return result
        for group in chunked(list(contact_ids), 500):
            placeholders = ",".join(["%s"] * len(group))
            with self.db.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT amo_contact_id, amo_lead_id
                    FROM amo_lead_contacts
                    WHERE amo_contact_id IN ({placeholders})
                    ORDER BY amo_contact_id, is_main_contact DESC, amo_lead_id
                    """,
                    tuple(group),
                )
                for row in cursor.fetchall():
                    result[int(row["amo_contact_id"])].append(
                        int(row["amo_lead_id"])
                    )
        return result

    def _sync_note_page(
        self,
        source_entity_type: str,
        items: Sequence[Dict[str, Any]],
    ) -> int:
        allowed = [
            item
            for item in items
            if not is_message_type(item.get("note_type"))
        ]
        note_rows: List[Tuple[Any, ...]] = []
        call_rows: List[Tuple[Any, ...]] = []
        call_lead_rows: List[Tuple[Any, ...]] = []
        calls = [
            item for item in allowed if item.get("note_type") in CALL_NOTE_TYPES
        ]
        contact_ids = [
            int(item["entity_id"])
            for item in calls
            if source_entity_type == "contact"
        ]
        contact_leads = self._contact_leads(contact_ids)
        for item in allowed:
            is_call = item.get("note_type") in CALL_NOTE_TYPES
            cleaned = sanitize_call_payload(item) if is_call else item
            params = (
                sanitize_call_payload(item.get("params") or {})
                if is_call
                else (item.get("params") or {})
            )
            note_rows.append(
                (
                    item["id"],
                    source_entity_type,
                    item["entity_id"],
                    item.get("note_type") or "unknown",
                    item.get("created_by"),
                    item.get("updated_by"),
                    item.get("responsible_user_id"),
                    item.get("group_id"),
                    from_unix(item.get("created_at")),
                    from_unix(item.get("updated_at")),
                    json_text(params),
                    json_text(cleaned),
                )
            )
            if not is_call:
                continue
            related_leads = (
                contact_leads.get(int(item["entity_id"]), [])
                if source_entity_type == "contact"
                else [int(item["entity_id"])]
            )
            primary_lead = related_leads[0] if len(related_leads) == 1 else None
            direction = (
                "incoming"
                if item.get("note_type") == "call_in"
                else "outgoing"
            )
            phone = (item.get("params") or {}).get("phone")
            raw_status = (item.get("params") or {}).get("call_status")
            raw_result = (item.get("params") or {}).get("call_result")
            duration = safe_int(
                (item.get("params") or {}).get("duration")
            ) or 0
            call_rows.append(
                (
                    item["id"],
                    None,
                    source_entity_type,
                    item["entity_id"],
                    (
                        item["entity_id"]
                        if source_entity_type == "contact"
                        else None
                    ),
                    primary_lead,
                    from_unix(item.get("created_at")),
                    direction,
                    item.get("responsible_user_id"),
                    text_value(phone),
                    normalize_phone(phone),
                    max(0, duration),
                    text_value((item.get("params") or {}).get("source")),
                    text_value(raw_status),
                    text_value(raw_result),
                    normalized_call_status(raw_status, raw_result, duration),
                    from_unix(item.get("updated_at")),
                    json_text(cleaned),
                )
            )
            for lead_id in related_leads:
                call_lead_rows.append(
                    (
                        item["id"],
                        lead_id,
                        (
                            "direct"
                            if source_entity_type == "lead"
                            else "contact_current_relation"
                        ),
                        int(len(related_leads) == 1),
                    )
                )
        self._execute_many(
            """
            INSERT INTO amo_notes (
                amo_note_id, entity_type, amo_entity_id, note_type,
                created_by, updated_by, responsible_user_id, group_id,
                created_at, updated_at, params_json, raw_json
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                entity_type=VALUES(entity_type),
                amo_entity_id=VALUES(amo_entity_id),
                note_type=VALUES(note_type), created_by=VALUES(created_by),
                updated_by=VALUES(updated_by),
                responsible_user_id=VALUES(responsible_user_id),
                group_id=VALUES(group_id), created_at=VALUES(created_at),
                updated_at=VALUES(updated_at),
                params_json=VALUES(params_json), raw_json=VALUES(raw_json),
                synced_at=UTC_TIMESTAMP(3)
            """,
            note_rows,
        )
        self._execute_many(
            """
            INSERT INTO amo_calls (
                amo_note_id, event_id, source_entity_type, source_entity_id,
                amo_contact_id, amo_lead_id, occurred_at, direction,
                responsible_user_id, phone_original, phone_normalized,
                duration_sec, provider, raw_call_status, raw_call_result,
                normalized_status, source_updated_at, raw_json
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
            )
            ON DUPLICATE KEY UPDATE
                source_entity_type=VALUES(source_entity_type),
                source_entity_id=VALUES(source_entity_id),
                amo_contact_id=VALUES(amo_contact_id),
                amo_lead_id=VALUES(amo_lead_id),
                occurred_at=VALUES(occurred_at),
                direction=VALUES(direction),
                responsible_user_id=VALUES(responsible_user_id),
                phone_original=VALUES(phone_original),
                phone_normalized=VALUES(phone_normalized),
                duration_sec=VALUES(duration_sec), provider=VALUES(provider),
                raw_call_status=VALUES(raw_call_status),
                raw_call_result=VALUES(raw_call_result),
                normalized_status=VALUES(normalized_status),
                source_updated_at=VALUES(source_updated_at),
                raw_json=VALUES(raw_json), synced_at=UTC_TIMESTAMP(3)
            """,
            call_rows,
        )
        call_ids = [int(item["id"]) for item in calls]
        self._delete_for_ids("amo_call_leads", "amo_note_id", call_ids)
        self._execute_many(
            """
            INSERT INTO amo_call_leads (
                amo_note_id, amo_lead_id, relation_type, is_primary_relation
            ) VALUES (%s,%s,%s,%s)
            """,
            call_lead_rows,
        )
        self._insert_raw(
            self._raw_rows(
                f"{source_entity_type}_note",
                allowed,
                sanitize_calls=True,
            )
        )
        self.db.commit()
        return len(allowed)

    def sync_notes_calls(self) -> None:
        params = {
            "filter[updated_at][from]": self._history_from_timestamp(
                "notes_calls"
            ),
            "filter[updated_at][to]": int(
                self.snapshot_at.replace(tzinfo=timezone.utc).timestamp()
            ),
            "order[updated_at]": "asc",
        }
        total_seen = 0
        total_saved = 0
        for source_type in ("contacts", "leads"):
            entity_type = source_type.rstrip("s")
            for items in self.iter_pages(
                f"/api/v4/{source_type}/notes",
                "notes",
                params,
            ):
                total_seen += len(items)
                total_saved += self._sync_note_page(entity_type, items)
                self.logger.info(
                    "Примечания: просмотрено %s, сохранено %s",
                    total_seen,
                    total_saved,
                )
        self.records_read += total_seen

    def run(self) -> None:
        self.start_run()
        try:
            stages = [
                ("account", self.sync_account),
                ("users", self.sync_users),
                ("pipelines", self.sync_pipelines),
                ("custom_fields", self.sync_custom_fields),
                ("tags", self.sync_tags),
                ("leads", self.sync_leads),
                ("contacts", self.sync_contacts),
                ("companies", self.sync_companies),
                ("tasks", self.sync_tasks),
                ("events", self.sync_events),
                ("notes_calls", self.sync_notes_calls),
            ]
            if self.selected_stages:
                stages = [
                    stage
                    for stage in stages
                    if stage[0] in self.selected_stages
                ]
            for name, method in stages:
                self.run_stage(name, method)
            self.finish_run()
            if self.failed_stages:
                raise RuntimeError(
                    "Не завершены этапы: " + ", ".join(self.failed_stages)
                )
        except Exception as error:
            if not self.failed_stages:
                self.fail_run(error)
            raise


def configure_logging(verbose: bool = False) -> None:
    Path("logs").mkdir(exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)
    file_handler = logging.FileHandler(
        "logs/amocrm_loader.log", encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Загрузка данных amoCRM в базу Jino"
    )
    parser.add_argument(
        "--mode",
        choices=("full", "incremental"),
        default="full",
        help="full — полная сверка; incremental — изменения с перекрытием 2 часа",
    )
    parser.add_argument("--env", default=".env", help="Путь к .env")
    parser.add_argument(
        "--stages",
        default="",
        help=(
            "Необязательно: этапы через запятую, например notes_calls. "
            "Без параметра выполняются все этапы."
        ),
    )
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configure_logging(args.verbose)
    selected_stages = [
        value.strip()
        for value in args.stages.split(",")
        if value.strip()
    ]
    loader = AmoCRMLoader(
        env_path=args.env,
        mode=args.mode,
        selected_stages=selected_stages,
    )
    try:
        loader.run()
    except KeyboardInterrupt as error:
        loader.fail_run(error)
        logging.getLogger("amocrm_loader").warning(
            "Загрузка остановлена"
        )
        return 130
    except Exception:
        logging.getLogger("amocrm_loader").exception(
            "Загрузка завершилась ошибкой"
        )
        return 1
    finally:
        loader.close()
    logging.getLogger("amocrm_loader").info(
        "Загрузка успешно завершена"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
