"""Phase 6 tests for :mod:`orchestrator.shared_mcp`."""

from __future__ import annotations

import os
import time

import pytest
from orchestrator.shared_mcp import SharedWorkspace

pytestmark = pytest.mark.phase6


class TestWorkspaceSetup:
    """Workspace directory management tests."""

    def test_setup_creates_directories(self, tmp_path) -> None:
        workspace = SharedWorkspace(tmp_path / "shared")
        workspace.setup()

        for dir_name in ["inbox", "outbox", "context"]:
            child = workspace.workspace_path / dir_name
            assert child.exists()
            assert child.is_dir()

    def test_setup_is_idempotent(self, tmp_path) -> None:
        workspace = SharedWorkspace(tmp_path / "shared")

        workspace.setup()
        workspace.setup()

        assert (workspace.workspace_path / "inbox").is_dir()


class TestContextFiles:
    """Shared context document tests."""

    def test_write_and_read_context(self, tmp_path) -> None:
        workspace = SharedWorkspace(tmp_path / "shared")
        workspace.setup()

        content = "This is a shared context document.\nLine two."
        workspace.write_context("project_summary.txt", content)

        assert workspace.read_context("project_summary.txt") == content

    def test_read_nonexistent_context_raises(self, tmp_path) -> None:
        workspace = SharedWorkspace(tmp_path / "shared")
        workspace.setup()

        with pytest.raises(FileNotFoundError):
            workspace.read_context("does_not_exist.txt")


class TestInboxOutbox:
    """Inbox/outbox message flow tests."""

    def test_write_to_inbox_and_read_payload(self, tmp_path) -> None:
        workspace = SharedWorkspace(tmp_path / "shared")
        workspace.setup()

        workspace.write_to_inbox(
            agent="openclaw",
            task_id="msg-001",
            content="Hello from the orchestrator",
        )

        payload = workspace.read_inbox("openclaw", "msg-001")

        assert payload["task_id"] == "msg-001"
        assert payload["agent"] == "openclaw"
        assert payload["content"] == "Hello from the orchestrator"

    def test_list_inbox_returns_task_ids(self, tmp_path) -> None:
        workspace = SharedWorkspace(tmp_path / "shared")
        workspace.setup()

        for index in range(3):
            workspace.write_to_inbox(
                agent="openclaw",
                task_id=f"msg-{index:03d}",
                content=f"Message number {index}",
            )

        assert workspace.list_inbox("openclaw") == ["msg-000", "msg-001", "msg-002"]

    def test_write_and_read_outbox(self, tmp_path) -> None:
        workspace = SharedWorkspace(tmp_path / "shared")
        workspace.setup()

        workspace.write_to_outbox(
            agent="openclaw",
            task_id="msg-out-001",
            content="Analysis result: everything looks good.",
        )

        assert (
            workspace.read_from_outbox("openclaw", "msg-out-001")
            == "Analysis result: everything looks good."
        )

    def test_read_missing_outbox_returns_none(self, tmp_path) -> None:
        workspace = SharedWorkspace(tmp_path / "shared")
        workspace.setup()

        assert workspace.read_from_outbox("openclaw", "does-not-exist") is None

    def test_clean_completed_removes_old_inbox_and_outbox_files(self, tmp_path) -> None:
        workspace = SharedWorkspace(tmp_path / "shared")
        workspace.setup()

        workspace.write_to_inbox("openclaw", "old-inbox", "stale inbox")
        workspace.write_to_outbox("openclaw", "old-outbox", "stale outbox")

        old_timestamp = time.time() - 7200
        inbox_file = workspace.workspace_path / "inbox" / "openclaw" / "old-inbox.json"
        outbox_file = workspace.workspace_path / "outbox" / "openclaw" / "old-outbox.json"
        os.utime(inbox_file, (old_timestamp, old_timestamp))
        os.utime(outbox_file, (old_timestamp, old_timestamp))

        removed = workspace.clean_completed(older_than_hours=1)

        assert removed == 2
        assert not inbox_file.exists()
        assert not outbox_file.exists()
