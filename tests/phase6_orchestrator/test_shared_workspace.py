"""
test_shared_workspace.py — Phase 6 tests for orchestrator.shared_mcp.SharedWorkspace.

Covers:
  - Directory setup (contract)
  - Context file read/write (contract)
  - Inbox / outbox messaging protocol (contract)

All tests are marked @pytest.mark.phase6.  All filesystem operations use
pytest's ``tmp_path`` fixture for full test isolation — no shared state is
written to the real shared workspace during testing.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.phase6


# ---------------------------------------------------------------------------
# Contract tests — workspace directory structure
# ---------------------------------------------------------------------------


class TestWorkspaceSetup:
    """Contract tests: SharedWorkspace.setup() creates the required directory tree."""

    def test_setup_creates_directories(self, tmp_path) -> None:
        """setup() creates inbox/, outbox/, and context/ under the workspace root.

        These three directories are the core of the inter-agent communication
        protocol and must be created by setup() before any messages are written.
        """
        from orchestrator.shared_mcp import SharedWorkspace

        workspace = SharedWorkspace(root=tmp_path / "shared")
        workspace.setup()

        expected_dirs = ["inbox", "outbox", "context"]
        for dir_name in expected_dirs:
            child = workspace.root / dir_name
            assert child.exists(), (
                f"Expected directory {child} to be created by setup(), but it does not exist"
            )
            assert child.is_dir(), f"Expected {child} to be a directory, but it is not"

    def test_setup_is_idempotent(self, tmp_path) -> None:
        """Calling setup() twice on the same root does not raise any error.

        The second call must succeed even though the directories already exist,
        allowing setup() to be safely called at startup without guards.
        """
        from orchestrator.shared_mcp import SharedWorkspace

        workspace = SharedWorkspace(root=tmp_path / "shared")
        workspace.setup()

        # Second call — must not raise FileExistsError or similar.
        workspace.setup()

        # Verify directories are still intact.
        for dir_name in ["inbox", "outbox", "context"]:
            assert (workspace.root / dir_name).is_dir()


# ---------------------------------------------------------------------------
# Contract tests — context file read/write
# ---------------------------------------------------------------------------


class TestContextFiles:
    """Contract tests: SharedWorkspace read/write operations on context files."""

    def test_write_and_read_context(self, tmp_path) -> None:
        """write_context() persists content and read_context() retrieves it unchanged.

        Verifies a full round-trip: write a known string, read it back, and
        assert byte-for-byte equality.
        """
        from orchestrator.shared_mcp import SharedWorkspace

        workspace = SharedWorkspace(root=tmp_path / "shared")
        workspace.setup()

        content = "This is a shared context document.\nLine two."
        workspace.write_context(filename="project_summary.txt", content=content)

        retrieved = workspace.read_context(filename="project_summary.txt")

        assert retrieved is not None, "read_context() returned None for a file that was written"
        assert retrieved == content, (
            f"Context content mismatch.\nExpected: {content!r}\nGot: {retrieved!r}"
        )

    def test_read_nonexistent_context(self, tmp_path) -> None:
        """read_context() returns None or raises FileNotFoundError for a missing file.

        Both behaviours are acceptable; the test simply verifies that the
        implementation does not silently return empty content for a file that
        was never written.
        """
        from orchestrator.shared_mcp import SharedWorkspace

        workspace = SharedWorkspace(root=tmp_path / "shared")
        workspace.setup()

        try:
            result = workspace.read_context(filename="does_not_exist.txt")
            # If the implementation returns None, that is acceptable.
            assert result is None, (
                f"read_context() returned non-None content for a missing file: {result!r}"
            )
        except FileNotFoundError:
            # Raising FileNotFoundError is also a valid contract.
            pass


# ---------------------------------------------------------------------------
# Contract tests — inbox / outbox message passing
# ---------------------------------------------------------------------------


class TestInboxOutbox:
    """Contract tests: SharedWorkspace inbox/outbox inter-agent messaging."""

    def test_write_to_inbox(self, tmp_path) -> None:
        """write_to_inbox() creates a file in the inbox/ subdirectory for the agent.

        The file must exist under inbox/<agent_name>/ (or inbox/ with the agent
        name embedded in the filename) after the call completes.
        """
        from orchestrator.shared_mcp import SharedWorkspace

        workspace = SharedWorkspace(root=tmp_path / "shared")
        workspace.setup()

        workspace.write_to_inbox(
            agent_name="openclaw",
            message="Hello from the orchestrator",
            message_id="msg-001",
        )

        # Verify that some file was created under the inbox subtree.
        inbox_files = list((workspace.root / "inbox").rglob("*"))
        inbox_files = [f for f in inbox_files if f.is_file()]
        assert len(inbox_files) > 0, "No files found under inbox/ after write_to_inbox()"

    def test_read_from_outbox(self, tmp_path) -> None:
        """read_from_outbox() retrieves content that was written to the outbox.

        Writes a message to the outbox for a known message id, then reads it
        back and verifies the content matches.
        """
        from orchestrator.shared_mcp import SharedWorkspace

        workspace = SharedWorkspace(root=tmp_path / "shared")
        workspace.setup()

        outbox_content = "Analysis result: everything looks good."
        workspace.write_to_outbox(
            agent_name="openclaw",
            message=outbox_content,
            message_id="msg-out-001",
        )

        retrieved = workspace.read_from_outbox(
            agent_name="openclaw",
            message_id="msg-out-001",
        )

        assert retrieved is not None, "read_from_outbox() returned None for a written message"
        assert outbox_content in retrieved, (
            f"Outbox content mismatch.\nExpected to contain: {outbox_content!r}\nGot: {retrieved!r}"
        )

    def test_list_inbox(self, tmp_path) -> None:
        """list_inbox() returns all messages written to an agent's inbox.

        Writes three messages and verifies that list_inbox() returns a collection
        with at least three entries for the target agent.
        """
        from orchestrator.shared_mcp import SharedWorkspace

        workspace = SharedWorkspace(root=tmp_path / "shared")
        workspace.setup()

        for i in range(3):
            workspace.write_to_inbox(
                agent_name="openclaw",
                message=f"Message number {i}",
                message_id=f"msg-list-{i:03d}",
            )

        messages = workspace.list_inbox(agent_name="openclaw")

        assert isinstance(messages, list), f"list_inbox() must return a list, got {type(messages)}"
        assert len(messages) >= 3, f"Expected at least 3 inbox messages, got {len(messages)}"

    def test_clean_completed(self, tmp_path) -> None:
        """clean_completed() removes old completed message files from the workspace.

        Creates files and marks them as completed, then verifies clean_completed()
        removes them.  The cleanup boundary (age, status flag, etc.) is
        implementation-defined; the test only checks that at least one file is
        removed.
        """
        from orchestrator.shared_mcp import SharedWorkspace

        workspace = SharedWorkspace(root=tmp_path / "shared")
        workspace.setup()

        # Write a message and immediately mark it as completed so it is eligible
        # for cleanup on the next pass.
        workspace.write_to_inbox(
            agent_name="openclaw",
            message="Old completed message",
            message_id="msg-old-001",
        )
        workspace.mark_completed(agent_name="openclaw", message_id="msg-old-001")

        inbox_before = list((workspace.root / "inbox").rglob("*"))
        inbox_before = [f for f in inbox_before if f.is_file()]
        assert len(inbox_before) > 0, "Setup failed: no files in inbox before clean"

        workspace.clean_completed()

        inbox_after = list((workspace.root / "inbox").rglob("*"))
        inbox_after = [f for f in inbox_after if f.is_file()]

        assert len(inbox_after) < len(inbox_before), (
            f"clean_completed() did not remove any files. "
            f"Before: {len(inbox_before)}, after: {len(inbox_after)}"
        )
