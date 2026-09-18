"""Local five-call recording demo for contraindication loss reasons."""

from __future__ import annotations

import os
import re
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse, urljoin

import pymysql
from db_security import connect as secure_connect, tls_context
import requests

from amocrm_client import AmoCRMClient, load_env


CONTRAINDICATION_LOSS_REASON_ID = 23882118
CONTRAINDICATION_LOSS_REASON_NAME = "Противопоказание"
DEMO_PROVIDER = "UIS"
DEMO_LIMIT = 5
ALLOWED_RECORDING_HOSTS = {"media.comagic.ru"}


class RecordingUnavailable(RuntimeError):
    """Raised when a selected call has no safe playable recording."""


def validate_recording_url(value: object) -> str:
    url = str(value or "").strip()
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError as error:
        raise RecordingUnavailable("Запись звонка недоступна") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.hostname.lower() not in ALLOWED_RECORDING_HOSTS
        or parsed.username
        or parsed.password
        or port not in {None, 443}
    ):
        raise RecordingUnavailable("Запись звонка недоступна")
    return url


def _masked_phone(value: object) -> str:
    digits = re.sub(r"\D+", "", str(value or ""))
    if len(digits) < 4:
        return "Не указан"
    return f"••• ••• {digits[-4:-2]}-{digits[-2:]}"


def _date_time(value: object) -> str:
    return value.strftime("%d.%m.%Y, %H:%M") if value else "—"


class CallRecordingDemo:
    def __init__(
        self,
        env_path: str = ".env",
        *,
        client_factory: Callable[[str], AmoCRMClient] = AmoCRMClient,
        http_get: Callable[..., Any] = requests.get,
    ) -> None:
        self.env_path = env_path
        self.env = load_env(env_path)
        self._client_factory = client_factory
        self._http_get = http_get

    def _database(self):
        return secure_connect(
            host=os.environ.get("AMO_DB_HOST_OVERRIDE", self.env["AMO_DB_HOST"]),
            port=int(self.env.get("AMO_DB_PORT", "3306")),
            user=self.env["AMO_DB_USER"],
            password=self.env["AMO_DB_PASSWORD"],
            database=self.env["AMO_DB_NAME"],
            charset="utf8mb4",
            autocommit=True,
            ssl=tls_context(self.env),
            connect_timeout=int(self.env.get("AMO_DB_CONNECT_TIMEOUT", "10")),
            read_timeout=45,
            cursorclass=pymysql.cursors.DictCursor,
        )

    @staticmethod
    def _scope_sql(note_filter: bool = False) -> str:
        note_condition = "AND c.amo_note_id=%s" if note_filter else ""
        return f"""
            FROM amo_calls c
            JOIN amo_call_leads cl
              ON cl.amo_note_id=c.amo_note_id
             AND cl.is_primary_relation=1
            JOIN amo_leads l ON l.amo_lead_id=cl.amo_lead_id
            JOIN amo_notes n ON n.amo_note_id=c.amo_note_id
            LEFT JOIN amo_users u ON u.amo_user_id=n.created_by
            WHERE LOWER(TRIM(COALESCE(c.provider, '')))=LOWER(%s)
              AND c.normalized_status='accepted'
              AND c.duration_sec > 0
              AND l.closed_at IS NOT NULL
              AND l.is_deleted=0
              AND CAST(
                    JSON_UNQUOTE(JSON_EXTRACT(l.raw_json, '$.loss_reason_id'))
                    AS UNSIGNED
                  )=%s
              {note_condition}
        """

    def list_calls(self, limit: int = DEMO_LIMIT) -> Dict[str, Any]:
        safe_limit = min(DEMO_LIMIT, max(1, int(limit)))
        connection = self._database()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(DISTINCT c.amo_note_id) eligible_calls, "
                    "COUNT(DISTINCT l.amo_lead_id) eligible_leads "
                    + self._scope_sql(),
                    (DEMO_PROVIDER, CONTRAINDICATION_LOSS_REASON_ID),
                )
                totals = cursor.fetchone() or {}
                cursor.execute(
                    """
                    SELECT c.amo_note_id, l.amo_lead_id, c.occurred_at,
                           c.direction, c.duration_sec, c.provider,
                           c.phone_original, l.lead_name, l.closed_at,
                           COALESCE(NULLIF(TRIM(u.user_name), ''), 'Не определён') employee
                    """
                    + self._scope_sql()
                    + " ORDER BY c.duration_sec DESC, c.occurred_at DESC LIMIT %s",
                    (DEMO_PROVIDER, CONTRAINDICATION_LOSS_REASON_ID, safe_limit),
                )
                rows = cursor.fetchall()
        finally:
            connection.close()

        subdomain = self.env["AMO_SUBDOMAIN"]
        calls = [
            {
                "note_id": int(row["amo_note_id"]),
                "lead_id": int(row["amo_lead_id"]),
                "lead_name": str(row.get("lead_name") or "Без названия"),
                "lead_url": (
                    f"https://{subdomain}.amocrm.ru/leads/detail/{int(row['amo_lead_id'])}"
                ),
                "occurred_at": _date_time(row.get("occurred_at")),
                "closed_at": _date_time(row.get("closed_at")),
                "direction": (
                    "Входящий" if row.get("direction") == "incoming" else "Исходящий"
                ),
                "duration_sec": int(row.get("duration_sec") or 0),
                "provider": str(row.get("provider") or DEMO_PROVIDER),
                "employee": str(row.get("employee") or "Не определён"),
                "phone": _masked_phone(row.get("phone_original")),
                "loss_reason": CONTRAINDICATION_LOSS_REASON_NAME,
                "audio_url": f"/api/call-recordings-demo/{int(row['amo_note_id'])}/audio",
            }
            for row in rows
        ]
        return {
            "calls": calls,
            "shown": len(calls),
            "eligible_calls": int(totals.get("eligible_calls") or 0),
            "eligible_leads": int(totals.get("eligible_leads") or 0),
            "provider": DEMO_PROVIDER,
            "loss_reason": CONTRAINDICATION_LOSS_REASON_NAME,
            "method": "Пять самых продолжительных разговоров UIS",
        }

    def _recording_source(self, note_id: int) -> Dict[str, Any]:
        connection = self._database()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT c.amo_note_id, c.source_entity_type, c.source_entity_id
                    """
                    + self._scope_sql(note_filter=True)
                    + " LIMIT 1",
                    (
                        DEMO_PROVIDER,
                        CONTRAINDICATION_LOSS_REASON_ID,
                        int(note_id),
                    ),
                )
                row = cursor.fetchone()
        finally:
            connection.close()
        if not row:
            raise RecordingUnavailable("Звонок не входит в тестовую выборку")
        return row

    def recording_url(self, note_id: int) -> str:
        source = self._recording_source(note_id)
        entity_type = str(source["source_entity_type"])
        plural = {"lead": "leads", "contact": "contacts"}.get(
            entity_type, entity_type
        )
        note = self._client_factory(self.env_path).get(
            f"/api/v4/{plural}/{int(source['source_entity_id'])}/notes/{int(note_id)}"
        )
        return validate_recording_url((note.get("params") or {}).get("link"))

    def open_audio(self, note_id: int, range_header: Optional[str] = None):
        headers = {"Range": range_header} if range_header else {}
        url = self.recording_url(note_id)
        try:
            for redirect_count in range(4):
                response = self._http_get(
                    validate_recording_url(url), headers=headers, timeout=30,
                    allow_redirects=False, stream=True,
                )
                if response.status_code not in {301, 302, 303, 307, 308}:
                    break
                location = response.headers.get("Location", "")
                response.close()
                if not location or redirect_count == 3:
                    raise RecordingUnavailable("Слишком много перенаправлений записи")
                # Validate BEFORE sending the next request, including relative redirects.
                url = validate_recording_url(urljoin(url, location))
        except requests.RequestException as error:
            raise RecordingUnavailable("Не удалось получить запись звонка") from error
        if response.status_code not in {200, 206}:
            response.close()
            raise RecordingUnavailable("Провайдер не отдал запись звонка")
        try:
            validate_recording_url(response.url)
        except RecordingUnavailable:
            response.close()
            raise
        return response
