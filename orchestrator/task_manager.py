"""Task lifecycle management for multi-agent orchestration workflows.

Tasks are persisted to a JSON file in the shared workspace so that
orchestrator restarts and external tools can inspect progress.

Example::

    from pathlib import Path
    from orchestrator.task_manager import TaskManager

    tm = TaskManager(Path.home() / "workspace" / "shared-agents")
    task = tm.create_task("research", "Summarise CUDA 12 features", "gemini")
    tm.update_task(task.id, "completed", result="CUDA 12 adds...")
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Task model
# ---------------------------------------------------------------------------

TaskStatus = Literal["pending", "running", "completed", "failed"]
TaskType = Literal["research", "code_generation", "code_review", "analysis", "implementation"]


class Task(BaseModel):
    """A unit of work assigned to a single agent sandbox.

    Attributes:
        id: UUID4 string uniquely identifying this task.
        type: Broad category of work; used for routing and reporting.
        prompt: The full instruction text sent to the agent.
        assigned_to: Logical agent name (key in OrchestratorSettings.agents).
        status: Current lifecycle state of the task.
        result: Agent response text, populated once the task completes.
        created_at: ISO-8601 UTC timestamp of task creation.
        completed_at: ISO-8601 UTC timestamp when the task reached a
            terminal state (``"completed"`` or ``"failed"``).
        parent_task_id: ID of the parent task when this is a subtask in a
            delegation chain.
        metadata: Arbitrary key-value pairs for caller-specific context.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: TaskType
    prompt: str
    assigned_to: str
    status: TaskStatus = "pending"
    result: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    completed_at: str | None = None
    parent_task_id: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class TaskManager:
    """Manages the full lifecycle of orchestrator tasks.

    State is persisted to ``shared_workspace/tasks.json`` after every
    mutation. Access is protected by a threading lock so that the manager
    is safe to use from :class:`~orchestrator.orchestrator.Orchestrator`
    when parallel delegation is in flight.

    Attributes:
        shared_workspace: Root directory used for task persistence.
    """

    _TASKS_FILE = "tasks.json"

    def __init__(self, shared_workspace: Path) -> None:
        """Initialise the TaskManager and load any previously persisted tasks.

        Args:
            shared_workspace: Directory where ``tasks.json`` will be
                created.  The directory is created if it does not exist.
        """
        self.shared_workspace = Path(shared_workspace)
        self.shared_workspace.mkdir(parents=True, exist_ok=True)
        self._tasks_file = self.shared_workspace / self._TASKS_FILE
        self._tasks: dict[str, Task] = {}
        self._tasks_mtime_ns: int | None = None
        self._lock = threading.Lock()
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_task(
        self,
        type: TaskType,
        prompt: str,
        assigned_to: str,
        parent_task_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> Task:
        """Create and persist a new task in ``"pending"`` state.

        Args:
            type: Category of work (e.g. ``"research"``).
            prompt: Instruction text for the agent.
            assigned_to: Logical agent name.
            parent_task_id: Optional parent task ID for subtask chains.
            metadata: Optional caller-supplied key-value context.

        Returns:
            The newly created Task object.
        """
        task = Task(
            type=type,
            prompt=prompt,
            assigned_to=assigned_to,
            parent_task_id=parent_task_id,
            metadata=metadata or {},
        )
        with self._lock:
            self._sync_from_disk_locked()
            self._tasks[task.id] = task
            self._save()
        return task

    def update_task(
        self,
        task_id: str,
        status: TaskStatus,
        result: str | None = None,
    ) -> Task:
        """Update the status and optionally the result of an existing task.

        Sets ``completed_at`` automatically when *status* is ``"completed"``
        or ``"failed"``.

        Args:
            task_id: UUID of the task to update.
            status: New lifecycle status.
            result: Agent response text (for terminal states).

        Returns:
            The updated Task object.

        Raises:
            KeyError: If *task_id* does not exist.
        """
        with self._lock:
            self._sync_from_disk_locked()
            task = self._get_task_locked(task_id)
            updated = task.model_copy(
                update={
                    "status": status,
                    "result": result if result is not None else task.result,
                    "completed_at": (
                        datetime.now(UTC).isoformat()
                        if status in ("completed", "failed")
                        else task.completed_at
                    ),
                }
            )
            self._tasks[task_id] = updated
            self._save()
        return updated

    def get_task(self, task_id: str) -> Task:
        """Retrieve a task by its ID.

        Args:
            task_id: UUID of the task.

        Returns:
            The Task object.

        Raises:
            KeyError: If *task_id* does not exist.
        """
        with self._lock:
            self._sync_from_disk_locked()
            return self._get_task_locked(task_id)

    def list_tasks(
        self,
        status: TaskStatus | None = None,
        assigned_to: str | None = None,
    ) -> list[Task]:
        """List tasks with optional filtering.

        Args:
            status: If given, only return tasks in this state.
            assigned_to: If given, only return tasks for this agent.

        Returns:
            List of matching tasks ordered by ``created_at``.
        """
        with self._lock:
            self._sync_from_disk_locked()
            tasks = list(self._tasks.values())

        if status is not None:
            tasks = [t for t in tasks if t.status == status]
        if assigned_to is not None:
            tasks = [t for t in tasks if t.assigned_to == assigned_to]

        return sorted(tasks, key=lambda t: t.created_at)

    def get_subtasks(self, parent_task_id: str) -> list[Task]:
        """Return all tasks whose parent is *parent_task_id*.

        Args:
            parent_task_id: UUID of the parent task.

        Returns:
            List of child tasks ordered by ``created_at``.
        """
        with self._lock:
            self._sync_from_disk_locked()
            tasks = [t for t in self._tasks.values() if t.parent_task_id == parent_task_id]
        return sorted(tasks, key=lambda t: t.created_at)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_task_locked(self, task_id: str) -> Task:
        """Return a task; caller must hold ``self._lock``.

        Args:
            task_id: UUID of the task.

        Returns:
            The Task object.

        Raises:
            KeyError: If *task_id* is not found.
        """
        if task_id not in self._tasks:
            raise KeyError(f"Task {task_id!r} not found.")
        return self._tasks[task_id]

    def _save(self) -> None:
        """Persist current in-memory tasks to disk; caller must hold lock."""
        payload = {tid: task.model_dump() for tid, task in self._tasks.items()}
        self._tasks_file.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.shared_workspace,
            prefix=f".{self._TASKS_FILE}.",
            suffix=".tmp",
            delete=False,
        ) as tmp_file:
            tmp_file.write(json.dumps(payload, indent=2))
            temp_path = Path(tmp_file.name)

        temp_path.replace(self._tasks_file)
        self._tasks_mtime_ns = self._tasks_file.stat().st_mtime_ns

    def _sync_from_disk_locked(self) -> None:
        """Merge the latest on-disk state into memory; caller must hold lock."""
        if not self._tasks_file.exists():
            self._tasks = {}
            self._tasks_mtime_ns = None
            return

        file_mtime_ns = self._tasks_file.stat().st_mtime_ns
        if self._tasks_mtime_ns == file_mtime_ns:
            return

        self._tasks = self._read_tasks_file()
        self._tasks_mtime_ns = file_mtime_ns

    def _read_tasks_file(self) -> dict[str, Task]:
        """Read and parse the persisted task file."""
        if not self._tasks_file.exists():
            return {}
        try:
            raw = json.loads(self._tasks_file.read_text(encoding="utf-8"))
            return {tid: Task(**data) for tid, data in raw.items()}
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Failed to read persisted task state from {self._tasks_file}: {exc}"
            ) from exc

    def _load(self) -> None:
        """Load tasks from disk into memory (called once at init)."""
        if not self._tasks_file.exists():
            self._tasks = {}
            self._tasks_mtime_ns = None
            return

        self._tasks = self._read_tasks_file()
        self._tasks_mtime_ns = self._tasks_file.stat().st_mtime_ns
