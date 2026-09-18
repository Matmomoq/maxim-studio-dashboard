"""Small persistent login throttle shared by all workers on one host."""
import hashlib
import hmac
import math
import os
import sqlite3
import time
from pathlib import Path


class LoginLimiter:
    def __init__(self, path, secret, clock=time.time):
        self.path = str(path)
        self.secret = secret.encode()
        self.clock = clock
        # Limits include successful logins; rotating cookies cannot reset them.
        self.window = 300
        self.limits = (("ip", 20), ("account", 60))

    def attempt(self, address, username):
        now = int(self.clock())
        keys = [(hmac.new(self.secret, (kind + ":" + value).encode(), hashlib.sha256).hexdigest(), limit)
                for (kind, limit), value in zip(self.limits, (address, username))]
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute("CREATE TABLE IF NOT EXISTS login_attempts (key TEXT PRIMARY KEY, started INTEGER NOT NULL, count INTEGER NOT NULL)")
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM login_attempts WHERE started <= ?", (now - self.window,))
            rows = []
            for key, limit in keys:
                row = db.execute("SELECT started, count FROM login_attempts WHERE key = ?", (key,)).fetchone()
                rows.append((key, limit, row))
            retry = max([row[0] + self.window - now for _, limit, row in rows if row and row[1] >= limit] or [0])
            if retry:
                return max(1, math.ceil(retry))
            for key, _, row in rows:
                if row:
                    db.execute("UPDATE login_attempts SET count = count + 1 WHERE key = ?", (key,))
                else:
                    db.execute("INSERT INTO login_attempts VALUES (?, ?, 1)", (key, now))
        return 0
