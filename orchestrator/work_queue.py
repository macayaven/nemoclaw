"""Durable queue and worker primitives backed by SQLite."""

from __future__ import annotations

import json
import socket
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from orchestrator.models import QueueItem
from orchestrator.storage import SQLiteStore, utc_now


def _parse_ts(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


class WorkQueue:
    """A small multi-process-safe durable queue."""

    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def enqueue(
        self,
        queue_name: str,
        payload: dict[str, object],
        *,
        dedupe_key: str | None = None,
        available_at: str | None = None,
        max_attempts: int = 5,
        metadata: dict[str, object] | None = None,
    ) -> QueueItem:
        item = QueueItem(
            queue_name=queue_name,
            payload=payload,
            dedupe_key=dedupe_key,
            available_at=available_at or utc_now(),
            max_attempts=max_attempts,
            metadata=metadata or {},
        )
        with self.store.transaction() as connection:
            if dedupe_key is not None:
                existing = connection.execute(
                    """
                    SELECT * FROM queue_items
                    WHERE queue_name = ? AND dedupe_key = ?
                    """,
                    (queue_name, dedupe_key),
                ).fetchone()
                if existing is not None:
                    return self._row_to_item(existing)

            connection.execute(
                """
                INSERT INTO queue_items (
                    id, queue_name, payload_json, status, dedupe_key, available_at,
                    lease_expires_at, claimed_by, attempts, max_attempts, last_error,
                    created_at, updated_at, completed_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id,
                    item.queue_name,
                    json.dumps(item.payload),
                    item.status,
                    item.dedupe_key,
                    item.available_at,
                    item.lease_expires_at,
                    item.claimed_by,
                    item.attempts,
                    item.max_attempts,
                    item.last_error,
                    item.created_at,
                    item.updated_at,
                    item.completed_at,
                    json.dumps(item.metadata),
                ),
            )
        return item

    def lease(
        self,
        queue_name: str,
        worker_id: str,
        *,
        lease_seconds: int = 60,
    ) -> QueueItem | None:
        now = datetime.now(UTC)
        now_iso = now.isoformat()
        lease_until = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self.store.transaction() as connection:
            row = connection.execute(
                """
                SELECT * FROM queue_items
                WHERE queue_name = ?
                  AND (
                    status = 'queued'
                    OR (status = 'leased' AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?)
                  )
                  AND available_at <= ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (queue_name, now_iso, now_iso),
            ).fetchone()
            if row is None:
                return None

            item = self._row_to_item(row)
            attempts = item.attempts + 1
            connection.execute(
                """
                UPDATE queue_items
                SET status = 'leased',
                    lease_expires_at = ?,
                    claimed_by = ?,
                    attempts = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (lease_until, worker_id, attempts, now_iso, item.id),
            )

            return item.model_copy(
                update={
                    "status": "leased",
                    "lease_expires_at": lease_until,
                    "claimed_by": worker_id,
                    "attempts": attempts,
                    "updated_at": now_iso,
                }
            )

    def complete(self, item_id: str) -> QueueItem:
        now_iso = utc_now()
        with self.store.transaction() as connection:
            row = self._fetch_row_for_update(connection, item_id)
            connection.execute(
                """
                UPDATE queue_items
                SET status = 'completed',
                    completed_at = ?,
                    updated_at = ?,
                    lease_expires_at = NULL
                WHERE id = ?
                """,
                (now_iso, now_iso, item_id),
            )
            return self._row_to_item(row).model_copy(
                update={
                    "status": "completed",
                    "completed_at": now_iso,
                    "lease_expires_at": None,
                    "updated_at": now_iso,
                }
            )

    def fail(
        self,
        item_id: str,
        error: str,
        *,
        retry_delay_seconds: int = 0,
    ) -> QueueItem:
        now = datetime.now(UTC)
        now_iso = now.isoformat()
        with self.store.transaction() as connection:
            row = self._fetch_row_for_update(connection, item_id)
            item = self._row_to_item(row)
            exhausted = item.attempts >= item.max_attempts
            next_status = "dead_letter" if exhausted else "queued"
            available_at = (
                item.available_at
                if exhausted
                else (now + timedelta(seconds=retry_delay_seconds)).isoformat()
            )
            connection.execute(
                """
                UPDATE queue_items
                SET status = ?,
                    last_error = ?,
                    available_at = ?,
                    updated_at = ?,
                    lease_expires_at = NULL
                WHERE id = ?
                """,
                (next_status, error, available_at, now_iso, item_id),
            )
            return item.model_copy(
                update={
                    "status": next_status,
                    "last_error": error,
                    "available_at": available_at,
                    "updated_at": now_iso,
                    "lease_expires_at": None,
                }
            )

    def heartbeat(self, item_id: str, worker_id: str, *, lease_seconds: int = 60) -> QueueItem:
        now = datetime.now(UTC)
        now_iso = now.isoformat()
        lease_until = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self.store.transaction() as connection:
            row = self._fetch_row_for_update(connection, item_id)
            item = self._row_to_item(row)
            if item.claimed_by != worker_id:
                raise RuntimeError(f"Queue item {item_id!r} is not leased by {worker_id!r}.")
            connection.execute(
                """
                UPDATE queue_items
                SET lease_expires_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (lease_until, now_iso, item_id),
            )
            return item.model_copy(update={"lease_expires_at": lease_until, "updated_at": now_iso})

    def get(self, item_id: str) -> QueueItem:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM queue_items WHERE id = ?",
                (item_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Queue item {item_id!r} not found.")
            return self._row_to_item(row)

    def list_items(self, queue_name: str, *, status: str | None = None) -> list[QueueItem]:
        with self.store.connect() as connection:
            if status is None:
                rows = connection.execute(
                    """
                    SELECT * FROM queue_items
                    WHERE queue_name = ?
                    ORDER BY created_at ASC
                    """,
                    (queue_name,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM queue_items
                    WHERE queue_name = ? AND status = ?
                    ORDER BY created_at ASC
                    """,
                    (queue_name, status),
                ).fetchall()
        return [self._row_to_item(row) for row in rows]

    def _fetch_row_for_update(self, connection, item_id: str):
        row = connection.execute(
            "SELECT * FROM queue_items WHERE id = ?",
            (item_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Queue item {item_id!r} not found.")
        return row

    def _row_to_item(self, row) -> QueueItem:
        return QueueItem(
            id=row["id"],
            queue_name=row["queue_name"],
            payload=json.loads(row["payload_json"]),
            status=row["status"],
            dedupe_key=row["dedupe_key"],
            available_at=row["available_at"],
            lease_expires_at=row["lease_expires_at"],
            claimed_by=row["claimed_by"],
            attempts=row["attempts"],
            max_attempts=row["max_attempts"],
            last_error=row["last_error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
            metadata=json.loads(row["metadata_json"]),
        )


class QueueWorker:
    """Polling worker that processes queued items using a callback."""

    def __init__(
        self,
        queue: WorkQueue,
        queue_name: str,
        handler: Callable[[QueueItem], None],
        *,
        worker_id: str | None = None,
        lease_seconds: int = 60,
    ) -> None:
        self.queue = queue
        self.queue_name = queue_name
        self.handler = handler
        self.worker_id = worker_id or f"{socket.gethostname()}:{id(self)}"
        self.lease_seconds = lease_seconds

    def process_once(self) -> bool:
        """Process a single queue item if available."""
        item = self.queue.lease(
            self.queue_name,
            self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if item is None:
            return False

        try:
            self.handler(item)
        except Exception as exc:
            self.queue.fail(item.id, str(exc))
            raise

        self.queue.complete(item.id)
        return True

    def run_until_idle(self, *, idle_rounds: int = 1, sleep_seconds: float = 0.1) -> int:
        """Process items until the queue stays empty for a number of polls."""
        processed = 0
        idle = 0
        while idle < idle_rounds:
            if self.process_once():
                processed += 1
                idle = 0
                continue
            idle += 1
            if idle < idle_rounds:
                time.sleep(sleep_seconds)
        return processed
