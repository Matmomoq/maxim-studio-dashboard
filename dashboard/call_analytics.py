"""Operational call-centre and administrator analytics."""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional

import pymysql
from db_security import connect as secure_connect, tls_context

from amocrm_client import load_env
from .analytics import MOSCOW


USER_RULES_BY_ID = {
    13984270: ("cc", "Москва"),       # Колл-центр Москва
    13984330: ("cc", "СПБ"),          # Колл-центр СПб 1
    14114098: ("cc", "СПБ"),          # Колл-центр СПБ 2
    13984302: ("admin", "Москва"),    # Администратор Москва
    13984342: ("admin", "СПБ"),       # Администратор СПБ 1
    13984314: ("admin", "СПБ"),       # Администратор СПб 2
}


def _utc_boundary(day: date) -> datetime:
    local = datetime.combine(day, datetime_time.min, tzinfo=MOSCOW)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def _empty_metrics() -> Dict[str, int]:
    return {
        "calls": 0,
        "incoming": 0,
        "outgoing": 0,
        "accepted": 0,
        "missed": 0,
        "talk_seconds": 0,
        "bookings": 0,
    }


def _finish(metrics: Mapping[str, int]) -> Dict[str, Any]:
    result = dict(metrics)
    accepted = int(result["accepted"])
    result["average_talk_seconds"] = round(
        int(result["talk_seconds"]) / accepted if accepted else 0
    )
    result["bookings_per_100_calls"] = round(
        int(result["bookings"]) / accepted * 100 if accepted else 0, 1
    )
    return result


class CallAnalytics:
    def __init__(self, env_path: str = ".env") -> None:
        self.env_path = env_path

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
    def _user_map(rows: List[Mapping[str, Any]]) -> Dict[int, Dict[str, Any]]:
        result: Dict[int, Dict[str, Any]] = {}
        for row in rows:
            user_id = int(row["amo_user_id"])
            rule = USER_RULES_BY_ID.get(user_id)
            if not rule:
                continue
            team, city = rule
            label = str(row.get("user_name") or "").strip()
            result[user_id] = {
                "id": user_id,
                "label": label or f"Пользователь {user_id}",
                "team": team,
                "city": city,
            }
        return result

    def report(
        self,
        start: date,
        end: date,
        *,
        team: str = "cc",
        city: str = "Все",
        user: str = "Все",
    ) -> Dict[str, Any]:
        if team not in {"cc", "admin"}:
            raise ValueError("Неизвестная команда")
        if city not in {"Все", "Москва", "СПБ"}:
            raise ValueError("Неизвестный город")

        connection = self._database()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT amo_user_id, user_name FROM amo_users")
                users = self._user_map(cursor.fetchall())
                team_ids = [key for key, value in users.items() if value["team"] == team]
                if not team_ids:
                    return self._empty_report(start, end, team)
                marks = ",".join(["%s"] * len(team_ids))
                start_utc = _utc_boundary(start)
                end_utc = _utc_boundary(end + timedelta(days=1))
                cursor.execute(
                    f"""
                    SELECT n.created_by AS call_user_id,
                           DATE(CONVERT_TZ(occurred_at, '+00:00', '+03:00')) day,
                           COUNT(*) calls,
                           SUM(direction='incoming') incoming,
                           SUM(direction='outgoing') outgoing,
                           SUM(normalized_status='accepted') accepted,
                           SUM(normalized_status='missed') missed,
                           SUM(CASE WHEN normalized_status='accepted'
                                    THEN duration_sec ELSE 0 END) talk_seconds
                    FROM amo_calls c
                    JOIN amo_notes n ON n.amo_note_id=c.amo_note_id
                    WHERE n.created_by IN ({marks})
                      AND c.occurred_at >= %s AND c.occurred_at < %s
                      AND NOT (
                          LOWER(TRIM(COALESCE(c.provider, '')))='skorozvon'
                          AND LOWER(TRIM(COALESCE(c.raw_call_result, '')))
                              LIKE 'возражение:%%'
                      )
                    GROUP BY n.created_by, day
                    """,
                    (*team_ids, start_utc, end_utc),
                )
                call_rows = cursor.fetchall()
                booking_rows = self._booking_rows(
                    cursor, team, team_ids, marks, start_utc, end_utc
                )
        finally:
            connection.close()

        selected = {
            user_id: meta
            for user_id, meta in users.items()
            if meta["team"] == team
            and (city == "Все" or meta["city"] == city)
            and (user == "Все" or meta["label"] == user)
        }
        labels = sorted({meta["label"] for meta in selected.values()})
        employee_metrics = {label: _empty_metrics() for label in labels}
        daily: Dict[date, Dict[str, int]] = defaultdict(_empty_metrics)

        for row in call_rows:
            meta = selected.get(int(row["call_user_id"]))
            if not meta:
                continue
            values = {key: int(row.get(key) or 0) for key in (
                "calls", "incoming", "outgoing", "accepted", "missed", "talk_seconds"
            )}
            for target in (employee_metrics[meta["label"]], daily[row["day"]]):
                for key, value in values.items():
                    target[key] += value

        for row in booking_rows:
            user_id = row.get("attributed_user_id")
            meta = selected.get(int(user_id)) if user_id is not None else None
            if not meta:
                continue
            employee_metrics[meta["label"]]["bookings"] += 1
            daily[row["day"]]["bookings"] += 1

        total = _empty_metrics()
        employees = []
        for label in labels:
            values = employee_metrics[label]
            for key in total:
                total[key] += values[key]
            meta = next(value for value in selected.values() if value["label"] == label)
            employees.append({"employee": label, "city": meta["city"], **_finish(values)})

        points = []
        current = start
        while current <= end:
            points.append({"date": current.isoformat(), **_finish(daily[current])})
            current += timedelta(days=1)

        return {
            "team": team,
            "period": {"from": start.isoformat(), "to": end.isoformat()},
            "kpi": _finish(total),
            "employees": employees,
            "daily": points,
            "options": {
                "cities": ["Москва", "СПБ"],
                "users": sorted({
                    meta["label"] for meta in users.values() if meta["team"] == team
                }),
            },
            "method": {
                "window_days": 7,
                "booking_label": "Первичные записи" if team == "cc" else "Все записи",
                "stages": ["Клиент записан"] if team == "cc" else [
                    "Клиент записан", "Перезапись", "Запись на следующую"
                ],
            },
        }

    @staticmethod
    def _booking_rows(cursor, team, team_ids, marks, start_utc, end_utc):
        if team == "cc":
            event_cte = """
                ranked_events AS (
                    SELECT h.event_id, h.amo_lead_id, h.changed_at, h.changed_by,
                           ROW_NUMBER() OVER (
                               PARTITION BY h.amo_lead_id
                               ORDER BY h.changed_at, h.event_id
                           ) AS entry_rank
                    FROM amo_lead_status_history h
                    JOIN amo_statuses s ON s.amo_status_id=h.new_status_id
                      AND (h.new_pipeline_id IS NULL OR s.amo_pipeline_id=h.new_pipeline_id)
                    WHERE LOWER(TRIM(s.status_name))='клиент записан'
                ), booking_events AS (
                    SELECT event_id, amo_lead_id, changed_at, changed_by
                    FROM ranked_events
                    WHERE entry_rank=1 AND changed_at >= %s AND changed_at < %s
                )
            """
        else:
            event_cte = """
                booking_events AS (
                    SELECT h.event_id, h.amo_lead_id, h.changed_at, h.changed_by
                    FROM amo_lead_status_history h
                    JOIN amo_statuses s ON s.amo_status_id=h.new_status_id
                      AND (h.new_pipeline_id IS NULL OR s.amo_pipeline_id=h.new_pipeline_id)
                    WHERE LOWER(TRIM(s.status_name)) IN (
                        'клиент записан','перезапись','запись на следующую'
                    ) AND h.changed_at >= %s AND h.changed_at < %s
                )
            """
        cursor.execute(
            f"""
            WITH {event_cte}, call_candidates AS (
                SELECT e.event_id, n.created_by AS call_user_id, c.occurred_at,
                       ROW_NUMBER() OVER (
                           PARTITION BY e.event_id
                           ORDER BY c.occurred_at DESC, c.amo_note_id DESC
                       ) call_rank
                FROM booking_events e
                JOIN amo_call_leads cl ON cl.amo_lead_id=e.amo_lead_id
                JOIN amo_calls c ON c.amo_note_id=cl.amo_note_id
                JOIN amo_notes n ON n.amo_note_id=c.amo_note_id
                 AND c.normalized_status='accepted'
                 AND c.occurred_at <= e.changed_at
                 AND c.occurred_at >= e.changed_at - INTERVAL 7 DAY
                 AND n.created_by IN ({marks})
                 AND NOT (
                     LOWER(TRIM(COALESCE(c.provider, '')))='skorozvon'
                     AND LOWER(TRIM(COALESCE(c.raw_call_result, '')))
                         LIKE 'возражение:%%'
                 )
            )
            SELECT DATE(CONVERT_TZ(e.changed_at, '+00:00', '+03:00')) day,
                   CASE WHEN e.changed_by IN ({marks}) THEN e.changed_by
                        ELSE cc.call_user_id END attributed_user_id
            FROM booking_events e
            LEFT JOIN call_candidates cc
              ON cc.event_id=e.event_id AND cc.call_rank=1
            """,
            (start_utc, end_utc, *team_ids, *team_ids),
        )
        return cursor.fetchall()

    @staticmethod
    def _empty_report(start: date, end: date, team: str) -> Dict[str, Any]:
        return {
            "team": team,
            "period": {"from": start.isoformat(), "to": end.isoformat()},
            "kpi": _finish(_empty_metrics()),
            "employees": [], "daily": [],
            "options": {"cities": ["Москва", "СПБ"], "users": []},
            "method": {"window_days": 7, "booking_label": "Записи", "stages": []},
        }
