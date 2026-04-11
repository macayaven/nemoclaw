"""Phase 6 tests for the SQLite-backed durable work queue."""

from __future__ import annotations

import pytest

from orchestrator.storage import SQLiteStore
from orchestrator.work_queue import QueueWorker, WorkQueue

pytestmark = pytest.mark.phase6


class TestWorkQueue:
    def test_enqueue_and_lease_round_trip(self, tmp_path) -> None:
        queue = WorkQueue(SQLiteStore(tmp_path / "shared"))

        queued = queue.enqueue("inbound", {"hello": "world"})
        leased = queue.lease("inbound", "worker-1")

        assert leased is not None
        assert leased.id == queued.id
        assert leased.status == "leased"
        assert leased.claimed_by == "worker-1"

    def test_dedupe_returns_existing_item(self, tmp_path) -> None:
        queue = WorkQueue(SQLiteStore(tmp_path / "shared"))

        first = queue.enqueue("inbound", {"n": 1}, dedupe_key="dup")
        second = queue.enqueue("inbound", {"n": 2}, dedupe_key="dup")

        assert first.id == second.id
        assert second.payload == {"n": 1}

    def test_fail_requeues_until_attempts_exhausted(self, tmp_path) -> None:
        queue = WorkQueue(SQLiteStore(tmp_path / "shared"))
        item = queue.enqueue("inbound", {"n": 1}, max_attempts=1)
        leased = queue.lease("inbound", "worker-1")
        assert leased is not None

        failed = queue.fail(item.id, "boom")

        assert failed.status == "dead_letter"
        assert failed.last_error == "boom"

    def test_worker_processes_item_and_marks_complete(self, tmp_path) -> None:
        queue = WorkQueue(SQLiteStore(tmp_path / "shared"))
        queue.enqueue("inbound", {"task": "run"})
        seen: list[dict[str, object]] = []

        worker = QueueWorker(queue, "inbound", lambda item: seen.append(item.payload))

        assert worker.process_once() is True
        assert seen == [{"task": "run"}]
        assert queue.list_items("inbound", status="completed")

    def test_worker_re_raises_handler_error(self, tmp_path) -> None:
        queue = WorkQueue(SQLiteStore(tmp_path / "shared"))
        item = queue.enqueue("inbound", {"task": "run"})
        worker = QueueWorker(
            queue, "inbound", lambda item: (_ for _ in ()).throw(RuntimeError("boom"))
        )

        with pytest.raises(RuntimeError, match="boom"):
            worker.process_once()

        stored = queue.get(item.id)
        assert stored.status == "queued"
        assert stored.last_error == "boom"
