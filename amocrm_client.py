"""Minimal read-only amoCRM API client with encrypted OAuth token rotation."""

from __future__ import annotations

import json
import os
import time
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Optional

import pymysql
from db_security import connect as secure_connect, tls_context
import requests
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


TOKEN_AAD = b"amo_oauth_token_v1"


def load_env(path: str = ".env") -> Dict[str, str]:
    values: Dict[str, str] = {}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {"'", '"'}
        ):
            value = value[1:-1]
        values[key.strip()] = value
    return values


class AmoApiError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class AmoCRMClient:
    def __init__(self, env_path: str = ".env") -> None:
        self.env = load_env(env_path)
        self.base_url = (
            f"https://{self.env['AMO_SUBDOMAIN']}.amocrm.ru"
        )
        self.timeout = int(self.env.get("AMO_API_TIMEOUT", "30"))
        self.requests_per_second = max(
            1, int(self.env.get("AMO_API_REQUESTS_PER_SECOND", "6"))
        )
        self._last_request_at = 0.0
        self._session = requests.Session()
        self._cached_access_token: Optional[str] = None
        self._cached_expires_at: Optional[datetime] = None

    def _database(self):
        return secure_connect(
            host=self.env["AMO_DB_HOST"],
            port=int(self.env.get("AMO_DB_PORT", "3306")),
            user=self.env["AMO_DB_USER"],
            password=self.env["AMO_DB_PASSWORD"],
            database=self.env["AMO_DB_NAME"],
            charset="utf8mb4",
            autocommit=False,
            ssl=tls_context(self.env),
            connect_timeout=int(self.env.get("AMO_DB_CONNECT_TIMEOUT", "10")),
            read_timeout=45,
            write_timeout=30,
            cursorclass=pymysql.cursors.DictCursor,
        )

    def _derive_key(self, salt: bytes) -> bytes:
        key_material = (
            self.env.get("AMO_TOKEN_ENCRYPTION_KEY")
            or self.env["AMO_CLIENT_SECRET"]
        )
        return PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=300_000,
        ).derive(key_material.encode("utf-8"))

    def _read_tokens(self) -> Dict[str, Any]:
        db = self._database()
        try:
            with db.cursor() as cursor:
                cursor.execute("START TRANSACTION READ ONLY")
                cursor.execute(
                    """
                    SELECT access_token_encrypted, refresh_token_encrypted,
                           encryption_salt, access_nonce, refresh_nonce,
                           expires_at
                    FROM amo_oauth_tokens
                    WHERE id = 1
                    """
                )
                row = cursor.fetchone()
            db.rollback()
        finally:
            db.close()
        if not row:
            raise RuntimeError("OAuth tokens have not been initialized")
        aes = AESGCM(self._derive_key(row["encryption_salt"]))
        return {
            "access_token": aes.decrypt(
                row["access_nonce"],
                row["access_token_encrypted"],
                TOKEN_AAD,
            ).decode("utf-8"),
            "refresh_token": aes.decrypt(
                row["refresh_nonce"],
                row["refresh_token_encrypted"],
                TOKEN_AAD,
            ).decode("utf-8"),
            "expires_at": row["expires_at"],
        }

    def _save_tokens(
        self,
        access_token: str,
        refresh_token: str,
        expires_in: int,
    ) -> None:
        salt = os.urandom(16)
        access_nonce = os.urandom(12)
        refresh_nonce = os.urandom(12)
        aes = AESGCM(self._derive_key(salt))
        access_encrypted = aes.encrypt(
            access_nonce, access_token.encode("utf-8"), TOKEN_AAD
        )
        refresh_encrypted = aes.encrypt(
            refresh_nonce, refresh_token.encode("utf-8"), TOKEN_AAD
        )
        expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
        db = self._database()
        try:
            with db.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE amo_oauth_tokens
                    SET access_token_encrypted = %s,
                        refresh_token_encrypted = %s,
                        encryption_salt = %s,
                        access_nonce = %s,
                        refresh_nonce = %s,
                        expires_at = %s
                    WHERE id = 1
                    """,
                    (
                        access_encrypted,
                        refresh_encrypted,
                        salt,
                        access_nonce,
                        refresh_nonce,
                        expires_at,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("OAuth token row is missing")
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _throttle(self) -> None:
        minimum_interval = 1.0 / self.requests_per_second
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < minimum_interval:
            time.sleep(minimum_interval - elapsed)
        self._last_request_at = time.monotonic()

    def _request_json(
        self,
        url: str,
        *,
        method: str = "GET",
        payload: Optional[Dict[str, Any]] = None,
        access_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._throttle()
        headers = {"Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        try:
            response = self._session.request(
                method,
                url,
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
        except requests.RequestException as error:
            raise RuntimeError("Network error while calling amoCRM") from error
        if response.status_code >= 400:
            message = f"amoCRM returned HTTP {response.status_code}"
            if response.content:
                try:
                    details = response.json()
                    message = str(
                        details.get("detail")
                        or details.get("hint")
                        or details.get("title")
                        or message
                    )
                except (ValueError, UnicodeDecodeError):
                    pass
            raise AmoApiError(response.status_code, message)
        return response.json() if response.content else {}

    def refresh_tokens(self) -> str:
        current = self._read_tokens()
        response = self._request_json(
            f"{self.base_url}/oauth2/access_token",
            method="POST",
            payload={
                "client_id": self.env["AMO_CLIENT_ID"],
                "client_secret": self.env["AMO_CLIENT_SECRET"],
                "grant_type": "refresh_token",
                "refresh_token": current["refresh_token"],
                "redirect_uri": self.env["AMO_REDIRECT_URI"],
            },
        )
        access_token = response["access_token"]
        self._save_tokens(
            access_token,
            response["refresh_token"],
            int(response.get("expires_in", 86_400)),
        )
        self._cached_access_token = access_token
        self._cached_expires_at = datetime.utcnow() + timedelta(
            seconds=int(response.get("expires_in", 86_400))
        )
        return access_token

    def get_access_token(self) -> str:
        if (
            self._cached_access_token
            and self._cached_expires_at
            and self._cached_expires_at
            > datetime.utcnow() + timedelta(minutes=5)
        ):
            return self._cached_access_token
        tokens = self._read_tokens()
        if tokens["expires_at"] <= datetime.utcnow() + timedelta(minutes=5):
            return self.refresh_tokens()
        self._cached_access_token = tokens["access_token"]
        self._cached_expires_at = tokens["expires_at"]
        return tokens["access_token"]

    def get(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        query = urllib.parse.urlencode(params or {}, doseq=True)
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{query}"
        token = self.get_access_token()
        retryable_statuses = {429, 500, 502, 503, 504}
        for attempt in range(6):
            try:
                return self._request_json(url, access_token=token)
            except AmoApiError as error:
                if error.status == 401 and attempt == 0:
                    token = self.refresh_tokens()
                    continue
                if error.status not in retryable_statuses or attempt == 5:
                    raise
                time.sleep(min(30, 2**attempt))
            except RuntimeError:
                if attempt == 5:
                    raise
                time.sleep(min(30, 2**attempt))
        raise RuntimeError("amoCRM request retries exhausted")

    def paginate(
        self,
        path: str,
        embedded_key: str,
        params: Optional[Dict[str, Any]] = None,
        max_pages: Optional[int] = None,
    ) -> Iterator[Dict[str, Any]]:
        page = 1
        base_params = dict(params or {})
        base_params.setdefault("limit", 250)
        while max_pages is None or page <= max_pages:
            page_params = dict(base_params)
            page_params["page"] = page
            response = self.get(path, page_params)
            items = response.get("_embedded", {}).get(embedded_key, [])
            if not items:
                return
            yield from items
            if not response.get("_links", {}).get("next"):
                return
            page += 1


if __name__ == "__main__":
    client = AmoCRMClient()
    account = client.get("/api/v4/account")
    print("amoCRM API: доступен" if account.get("id") else "amoCRM API: ошибка")
