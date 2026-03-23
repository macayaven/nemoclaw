"""Shared MCP filesystem management for cross-sandbox communication.

The SharedWorkspace provides structured inbox/outbox/context directories
that act as a simple message-passing layer between the DGX Spark host and
the agent sandboxes.  This complements the direct subprocess channel
offered by SandboxBridge with an asynchronous, file-based alternative.

Directory layout::

    shared-agents/
        inbox/
            <agent>/
                <task_id>.json
        outbox/
            <agent>/
                <task_id>.json
        context/
            <filename>

Example::

    from pathlib import Path
    from orchestrator.shared_mcp import SharedWorkspace

    ws = SharedWorkspace(Path.home() / "workspace" / "shared-agents")
    ws.setup()
    ws.write_context("project_brief.md", "Build a rate-limiter...")
    ws.write_to_inbox("codex", "task-abc", "Implement the rate-limiter.")
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path


class SharedWorkspace:
    """Manages the shared MCP filesystem directories.

    Attributes:
        workspace_path: Root directory of the shared workspace on the host.
    """

    _INBOX = "inbox"
    _OUTBOX = "outbox"
    _CONTEXT = "context"

    def __init__(self, workspace_path: Path) -> None:
        """Initialise the SharedWorkspace with the given root path.

        Args:
            workspace_path: Root directory; does not need to exist yet.
                Call :meth:`setup` to create the directory tree.
        """
        self.workspace_path = Path(workspace_path)

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def setup(self) -> None:
        """Create the standard inbox, outbox, and context subdirectories.

        Idempotent; safe to call multiple times.
        """
        for subdir in (self._INBOX, self._OUTBOX, self._CONTEXT):
            (self.workspace_path / subdir).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Context files (shared knowledge / brief)
    # ------------------------------------------------------------------

    def write_context(self, filename: str, content: str) -> None:
        """Write *content* to ``context/<filename>``.

        Args:
            filename: Target filename within the context directory.
            content: Text content to write.
        """
        target = self.workspace_path / self._CONTEXT / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def read_context(self, filename: str) -> str:
        """Read and return the content of ``context/<filename>``.

        Args:
            filename: Filename within the context directory.

        Returns:
            File content as a string.

        Raises:
            FileNotFoundError: If the context file does not exist.
        """
        target = self.workspace_path / self._CONTEXT / filename
        return target.read_text(encoding="utf-8")

    def list_context(self) -> list[str]:
        """List all filenames in the context directory.

        Returns:
            Sorted list of filenames (not full paths).
        """
        context_dir = self.workspace_path / self._CONTEXT
        if not context_dir.exists():
            return []
        return sorted(p.name for p in context_dir.iterdir() if p.is_file())

    # ------------------------------------------------------------------
    # Inbox  (host -> agent)
    # ------------------------------------------------------------------

    def write_to_inbox(self, agent: str, task_id: str, content: str) -> None:
        """Write a task payload to ``inbox/<agent>/<task_id>.json``.

        The payload includes the content string plus metadata (timestamp,
        agent name, task ID) so that consumer tooling can process messages
        without additional context.

        Args:
            agent: Logical agent name (used as subdirectory).
            task_id: Task UUID used as the filename stem.
            content: Prompt or instruction text to deliver to the agent.
        """
        inbox_dir = self.workspace_path / self._INBOX / agent
        inbox_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "task_id": task_id,
            "agent": agent,
            "content": content,
            "written_at": datetime.now(UTC).isoformat(),
        }
        target = inbox_dir / f"{task_id}.json"
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def list_inbox(self, agent: str) -> list[str]:
        """List pending task IDs in ``inbox/<agent>/``.

        Args:
            agent: Logical agent name.

        Returns:
            Sorted list of task ID strings (file stems, not full paths).
        """
        inbox_dir = self.workspace_path / self._INBOX / agent
        if not inbox_dir.exists():
            return []
        return sorted(p.stem for p in inbox_dir.iterdir() if p.suffix == ".json")

    def read_inbox(self, agent: str, task_id: str) -> dict[str, object]:
        """Read and parse the inbox payload for *task_id*.

        Args:
            agent: Logical agent name.
            task_id: Task UUID.

        Returns:
            Parsed JSON payload dict.

        Raises:
            FileNotFoundError: If the inbox message does not exist.
        """
        target = self.workspace_path / self._INBOX / agent / f"{task_id}.json"
        return json.loads(target.read_text(encoding="utf-8"))  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Outbox  (agent -> host)
    # ------------------------------------------------------------------

    def write_to_outbox(self, agent: str, task_id: str, content: str) -> None:
        """Write an agent response to ``outbox/<agent>/<task_id>.json``.

        Args:
            agent: Logical agent name.
            task_id: Task UUID.
            content: Response text produced by the agent.
        """
        outbox_dir = self.workspace_path / self._OUTBOX / agent
        outbox_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "task_id": task_id,
            "agent": agent,
            "content": content,
            "written_at": datetime.now(UTC).isoformat(),
        }
        target = outbox_dir / f"{task_id}.json"
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def read_from_outbox(self, agent: str, task_id: str) -> str | None:
        """Read the agent's response from ``outbox/<agent>/<task_id>.json``.

        Args:
            agent: Logical agent name.
            task_id: Task UUID.

        Returns:
            Response content string, or ``None`` if the file does not exist.
        """
        target = self.workspace_path / self._OUTBOX / agent / f"{task_id}.json"
        if not target.exists():
            return None
        payload = json.loads(target.read_text(encoding="utf-8"))
        return str(payload.get("content", ""))

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def clean_completed(self, older_than_hours: int = 24) -> int:
        """Remove inbox and outbox files older than *older_than_hours*.

        Uses file modification time for the age check.

        Args:
            older_than_hours: Files older than this many hours are deleted.

        Returns:
            Total number of files removed.
        """
        cutoff_seconds = older_than_hours * 3600
        now = time.time()
        removed = 0

        for subdir in (self._INBOX, self._OUTBOX):
            base = self.workspace_path / subdir
            if not base.exists():
                continue
            for agent_dir in base.iterdir():
                if not agent_dir.is_dir():
                    continue
                for f in list(agent_dir.iterdir()):
                    if f.is_file() and (now - f.stat().st_mtime) > cutoff_seconds:
                        f.unlink()
                        removed += 1

        return removed
