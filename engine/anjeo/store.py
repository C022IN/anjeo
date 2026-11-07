"""Durable state: the approval inbox, the activity ledger, and the owner's memory."""

from __future__ import annotations

import json
import secrets
import time
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS approvals (
    id          TEXT PRIMARY KEY,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL,
    tool        TEXT NOT NULL,
    server      TEXT NOT NULL,
    arguments   TEXT NOT NULL,
    reason      TEXT NOT NULL,
    source      TEXT NOT NULL,          -- app | agent
    intent      TEXT,                   -- the request that led here, when an agent proposed it
    status      TEXT NOT NULL,          -- pending | executing | executed | failed | rejected | expired
    decided_at  REAL,
    result      TEXT
);
CREATE INDEX IF NOT EXISTS approvals_status ON approvals(status, created_at);

CREATE TABLE IF NOT EXISTS activity (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      REAL NOT NULL,
    kind    TEXT NOT NULL,              -- call | approval | ask | memory
    tool    TEXT,
    status  TEXT NOT NULL,
    detail  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  REAL NOT NULL
);
"""

_TERMINAL = ("executed", "failed", "rejected", "expired")


def _row(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    for key in ("arguments", "result", "detail"):
        if d.get(key) is not None:
            d[key] = json.loads(d[key])
    return d


class Store:
    def __init__(self, path: str) -> None:
        self.path = path
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("store is not open")
        return self._db

    # ── approvals ──────────────────────────────────────────────────────────
    async def create_approval(
        self,
        *,
        tool: str,
        server: str,
        arguments: dict[str, Any],
        reason: str,
        source: str,
        intent: str | None,
        ttl_hours: float,
    ) -> dict[str, Any]:
        now = time.time()
        approval_id = secrets.token_urlsafe(9)
        await self.db.execute(
            "INSERT INTO approvals"
            " (id, created_at, expires_at, tool, server, arguments, reason, source, intent, status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
            (approval_id, now, now + ttl_hours * 3600, tool, server, json.dumps(arguments), reason, source, intent),
        )
        await self.db.commit()
        return await self.get_approval(approval_id)  # type: ignore[return-value]

    async def get_approval(self, approval_id: str) -> dict[str, Any] | None:
        async with self.db.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)) as cur:
            return _row(await cur.fetchone())

    async def list_approvals(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        await self.expire_stale()
        if status:
            sql, params = "SELECT * FROM approvals WHERE status = ? ORDER BY created_at DESC LIMIT ?", (status, limit)
        else:
            sql, params = "SELECT * FROM approvals ORDER BY created_at DESC LIMIT ?", (limit,)
        async with self.db.execute(sql, params) as cur:
            return [_row(r) for r in await cur.fetchall()]  # type: ignore[misc]

    async def claim_approval(self, approval_id: str, to_status: str) -> bool:
        """Move a pending, unexpired approval to `to_status` atomically.

        The conditional UPDATE is the lock: two taps on "Approve" from two
        devices cannot both claim it, so the action runs at most once.
        """
        cur = await self.db.execute(
            "UPDATE approvals SET status = ?, decided_at = ? WHERE id = ? AND status = 'pending' AND expires_at > ?",
            (to_status, time.time(), approval_id, time.time()),
        )
        await self.db.commit()
        return cur.rowcount == 1

    async def finish_approval(self, approval_id: str, status: str, result: dict[str, Any]) -> None:
        assert status in _TERMINAL
        await self.db.execute(
            "UPDATE approvals SET status = ?, result = ? WHERE id = ?", (status, json.dumps(result), approval_id)
        )
        await self.db.commit()

    async def expire_stale(self) -> int:
        cur = await self.db.execute(
            "UPDATE approvals SET status = 'expired' WHERE status = 'pending' AND expires_at <= ?", (time.time(),)
        )
        await self.db.commit()
        return cur.rowcount

    # ── activity ───────────────────────────────────────────────────────────
    async def log(self, kind: str, status: str, detail: dict[str, Any], tool: str | None = None) -> None:
        await self.db.execute(
            "INSERT INTO activity (at, kind, tool, status, detail) VALUES (?, ?, ?, ?, ?)",
            (time.time(), kind, tool, status, json.dumps(detail, default=str)),
        )
        await self.db.commit()

    async def activity(self, limit: int = 100) -> list[dict[str, Any]]:
        async with self.db.execute("SELECT * FROM activity ORDER BY id DESC LIMIT ?", (limit,)) as cur:
            return [_row(r) for r in await cur.fetchall()]  # type: ignore[misc]

    # ── memory ─────────────────────────────────────────────────────────────
    async def remember(self, key: str, value: str) -> None:
        await self.db.execute(
            "INSERT INTO memory (key, value, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, value, time.time()),
        )
        await self.db.commit()

    async def forget(self, key: str) -> bool:
        cur = await self.db.execute("DELETE FROM memory WHERE key = ?", (key,))
        await self.db.commit()
        return cur.rowcount == 1

    async def memory(self) -> list[dict[str, Any]]:
        async with self.db.execute("SELECT key, value, updated_at FROM memory ORDER BY key") as cur:
            return [dict(r) for r in await cur.fetchall()]
