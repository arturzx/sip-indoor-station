from __future__ import annotations

import asyncio
import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from aiohttp import web

from sip_indoor_station.app.events import AppEvent, EventBus
from sip_indoor_station.sip.server import SnapshotProvider

LOGGER = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class CallHistoryEntry:
    id: str
    sip_call_id: str | None
    status: str
    started_at: str
    answered_at: str | None
    ended_at: str | None
    remote_ip: str | None
    has_snapshot: bool
    snapshot_content_type: str | None
    snapshot_captured_at: str | None

    @property
    def snapshot_url(self) -> str | None:
        return f"/api/call_history/{self.id}/snapshot" if self.has_snapshot else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sip_call_id": self.sip_call_id,
            "status": self.status,
            "started_at": self.started_at,
            "answered_at": self.answered_at,
            "ended_at": self.ended_at,
            "remote_ip": self.remote_ip,
            "has_snapshot": self.has_snapshot,
            "snapshot_content_type": self.snapshot_content_type,
            "snapshot_captured_at": self.snapshot_captured_at,
            "snapshot_url": self.snapshot_url,
        }


class CallHistoryStore:
    def __init__(
        self,
        db_path: str,
        retention_days: int,
        event_bus: EventBus,
        snapshot_provider: SnapshotProvider | None = None,
    ) -> None:
        self.db_path = db_path
        self.retention_days = max(retention_days, 1)
        self.snapshot_provider = snapshot_provider
        self._call_to_history: dict[str, str] = {}
        self._closed = False
        self._init_db()
        self.cleanup_retention()
        event_bus.subscribe(self.handle_event)

    def register_routes(self, app: web.Application) -> None:
        app.router.add_get("/api/call_history", self.get_entries)
        app.router.add_delete("/api/call_history", self.delete_entries)
        app.router.add_get("/api/call_history/{history_id}", self.get_entry)
        app.router.add_delete("/api/call_history/{history_id}", self.delete_entry)
        app.router.add_get("/api/call_history/{history_id}/snapshot", self.get_snapshot)

    async def handle_event(self, event: AppEvent) -> None:
        if self._closed:
            return
        if event.name == "incoming_call":
            history_id = self.create_entry(event)
            if history_id and self.snapshot_provider is not None:
                asyncio.create_task(self.capture_snapshot(history_id))
        elif event.name in {"call_answered", "call_confirmed"}:
            self.mark_answered(event.call_id)
        elif event.name == "call_rejected":
            self.mark_finished(event.call_id, "rejected")
        elif event.name == "call_cancelled":
            self.mark_finished(event.call_id, "missed")
        elif event.name == "call_ended":
            self.mark_finished(event.call_id, "ended")
        elif event.name == "call_failed":
            self.mark_finished(event.call_id, "failed")

    def close(self) -> None:
        self._closed = True

    def create_entry(self, event: AppEvent) -> str | None:
        if not event.call_id:
            return None
        history_id = str(uuid.uuid4())
        self._call_to_history[event.call_id] = history_id
        now = iso_timestamp(utc_now())
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO call_history (
                    id, sip_call_id, status, started_at, remote_ip, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    history_id,
                    event.call_id,
                    "ringing",
                    now,
                    event.data.get("remote_ip"),
                    now,
                    now,
                ),
            )
        self.cleanup_retention()
        return history_id

    def mark_answered(self, sip_call_id: str | None) -> None:
        history_id = self.history_id_for_call(sip_call_id)
        if history_id is None:
            return
        now = iso_timestamp(utc_now())
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE call_history
                SET status = 'answered',
                    answered_at = COALESCE(answered_at, ?),
                    updated_at = ?
                WHERE id = ?
                """,
                (now, now, history_id),
            )

    def mark_finished(self, sip_call_id: str | None, status: str) -> None:
        history_id = self.history_id_for_call(sip_call_id)
        if history_id is None:
            return
        now = iso_timestamp(utc_now())
        final_status = status
        with self.connect() as connection:
            row = connection.execute("SELECT status FROM call_history WHERE id = ?", (history_id,)).fetchone()
            if row is not None and row["status"] == "answered" and status == "ended":
                final_status = "answered"
            connection.execute(
                """
                UPDATE call_history
                SET status = ?,
                    ended_at = COALESCE(ended_at, ?),
                    updated_at = ?
                WHERE id = ?
                """,
                (final_status, now, now, history_id),
            )

    async def capture_snapshot(self, history_id: str) -> None:
        if self.snapshot_provider is None:
            return
        try:
            snapshot = await self.snapshot_provider.capture_snapshot()
        except Exception as exc:
            LOGGER.warning("call_history_snapshot_failed history_id=%s error=%s", history_id, exc)
            return
        if snapshot is None:
            return
        self.store_snapshot(history_id, snapshot.content, snapshot.content_type)

    def store_snapshot(self, history_id: str, content: bytes, content_type: str) -> None:
        now = iso_timestamp(utc_now())
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE call_history
                SET snapshot = ?,
                    snapshot_content_type = ?,
                    snapshot_captured_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (content, content_type, now, now, history_id),
            )

    async def get_entries(self, request: web.Request) -> web.Response:
        limit = self.parse_limit(request.query.get("limit"))
        entries = [entry.to_dict() for entry in self.list_entries(limit)]
        return web.json_response({"calls": entries})

    async def get_entry(self, request: web.Request) -> web.Response:
        entry = self.get_entry_by_id(request.match_info["history_id"])
        if entry is None:
            return web.json_response({"ok": False, "reason": "not_found"}, status=404)
        return web.json_response(entry.to_dict())

    async def get_snapshot(self, request: web.Request) -> web.Response:
        snapshot = self.snapshot_by_id(request.match_info["history_id"])
        if snapshot is None:
            return web.json_response({"ok": False, "reason": "not_found"}, status=404)
        content, content_type = snapshot
        return web.Response(body=content, headers={"Content-Type": content_type})

    async def delete_entry(self, request: web.Request) -> web.Response:
        deleted = self.delete_entry_by_id(request.match_info["history_id"])
        if not deleted:
            return web.json_response({"ok": False, "reason": "not_found"}, status=404)
        return web.json_response({"ok": True})

    async def delete_entries(self, _request: web.Request) -> web.Response:
        with self.connect() as connection:
            connection.execute("DELETE FROM call_history")
        self._call_to_history.clear()
        return web.json_response({"ok": True})

    def list_entries(self, limit: int = 50) -> list[CallHistoryEntry]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, sip_call_id, status, started_at, answered_at, ended_at, remote_ip,
                       snapshot IS NOT NULL AS has_snapshot, snapshot_content_type, snapshot_captured_at
                FROM call_history
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self.entry_from_row(row) for row in rows]

    def get_entry_by_id(self, history_id: str) -> CallHistoryEntry | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, sip_call_id, status, started_at, answered_at, ended_at, remote_ip,
                       snapshot IS NOT NULL AS has_snapshot, snapshot_content_type, snapshot_captured_at
                FROM call_history
                WHERE id = ?
                """,
                (history_id,),
            ).fetchone()
        return self.entry_from_row(row) if row is not None else None

    def snapshot_by_id(self, history_id: str) -> tuple[bytes, str] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT snapshot, snapshot_content_type FROM call_history WHERE id = ? AND snapshot IS NOT NULL",
                (history_id,),
            ).fetchone()
        if row is None:
            return None
        return bytes(row["snapshot"]), row["snapshot_content_type"] or "application/octet-stream"

    def delete_entry_by_id(self, history_id: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute("DELETE FROM call_history WHERE id = ?", (history_id,))
            deleted = cursor.rowcount > 0
        for sip_call_id, mapped_id in list(self._call_to_history.items()):
            if mapped_id == history_id:
                del self._call_to_history[sip_call_id]
        return deleted

    def cleanup_retention(self) -> None:
        cutoff = iso_timestamp(utc_now() - timedelta(days=self.retention_days))
        with self.connect() as connection:
            connection.execute("DELETE FROM call_history WHERE started_at < ?", (cutoff,))

    def history_id_for_call(self, sip_call_id: str | None) -> str | None:
        if sip_call_id is None:
            return None
        if sip_call_id in self._call_to_history:
            return self._call_to_history[sip_call_id]
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id FROM call_history WHERE sip_call_id = ? ORDER BY started_at DESC LIMIT 1",
                (sip_call_id,),
            ).fetchone()
        if row is None:
            return None
        self._call_to_history[sip_call_id] = row["id"]
        return row["id"]

    def connect(self) -> sqlite3.Connection:
        parent = Path(self.db_path).parent
        if str(parent) not in {"", "."}:
            parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS call_history (
                    id TEXT PRIMARY KEY,
                    sip_call_id TEXT,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    answered_at TEXT,
                    ended_at TEXT,
                    remote_ip TEXT,
                    snapshot BLOB,
                    snapshot_content_type TEXT,
                    snapshot_captured_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_call_history_sip_call_id ON call_history(sip_call_id)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_call_history_started_at ON call_history(started_at)")

    @staticmethod
    def parse_limit(value: str | None) -> int:
        if value is None:
            return 50
        try:
            return min(max(int(value), 1), 200)
        except ValueError:
            return 50

    @staticmethod
    def entry_from_row(row: sqlite3.Row) -> CallHistoryEntry:
        return CallHistoryEntry(
            id=row["id"],
            sip_call_id=row["sip_call_id"],
            status=row["status"],
            started_at=row["started_at"],
            answered_at=row["answered_at"],
            ended_at=row["ended_at"],
            remote_ip=row["remote_ip"],
            has_snapshot=bool(row["has_snapshot"]),
            snapshot_content_type=row["snapshot_content_type"],
            snapshot_captured_at=row["snapshot_captured_at"],
        )
