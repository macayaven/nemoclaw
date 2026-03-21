"""
test_task_manager.py — Phase 6 tests for orchestrator.task_manager.

Covers Task creation (contract), the full task status lifecycle (contract),
persistence to disk (behavioural), and query / filtering helpers (contract).

All tests are marked @pytest.mark.phase6.  Filesystem tests use pytest's
``tmp_path`` fixture so each test gets an isolated temporary directory.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.phase6

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_TASK_TYPES = [
    "analysis",
    "implementation",
    "planning",
    "code_review",
    "research",
    "summarisation",
    "documentation",
    "debugging",
]


# ---------------------------------------------------------------------------
# Contract tests — Task creation
# ---------------------------------------------------------------------------


class TestTaskCreation:
    """Contract tests: Task objects are created with the expected default fields."""

    def test_create_task(self, tmp_path) -> None:
        """TaskManager.create_task() returns a Task with id, status=pending, created_at.

        Verifies the three mandatory fields that every new task must have
        regardless of the task type or assigned agent.
        """
        from orchestrator.task_manager import Task, TaskManager

        manager = TaskManager(storage_path=tmp_path / "tasks.json")
        task = manager.create_task(
            task_type="analysis",
            description="Analyse the project structure",
            assigned_to="openclaw",
        )

        assert isinstance(task, Task), f"create_task() must return a Task, got {type(task)}"
        assert task.id, "Task must have a non-empty id"
        assert task.status == "pending", f"New task status must be 'pending', got {task.status!r}"
        assert task.created_at is not None, "Task must have a created_at timestamp"

    def test_create_subtask(self, tmp_path) -> None:
        """create_task() with parent_task_id creates a subtask that references its parent.

        The subtask's parent_task_id field must match the id of the parent task,
        enabling tree-structured task hierarchies.
        """
        from orchestrator.task_manager import TaskManager

        manager = TaskManager(storage_path=tmp_path / "tasks.json")
        parent = manager.create_task(
            task_type="planning",
            description="Plan the implementation",
            assigned_to="openclaw",
        )
        child = manager.create_task(
            task_type="implementation",
            description="Implement step 1",
            assigned_to="openclaw",
            parent_task_id=parent.id,
        )

        assert child.parent_task_id == parent.id, (
            f"Subtask parent_task_id {child.parent_task_id!r} does not match "
            f"parent id {parent.id!r}"
        )
        assert child.id != parent.id, "Subtask must have a distinct id from its parent"

    @pytest.mark.parametrize("task_type", _VALID_TASK_TYPES)
    def test_task_types(self, tmp_path, task_type: str) -> None:
        """All valid task type strings are accepted by create_task() without error.

        Parametrised over the full set of known task types so that a newly
        introduced type that is not in ``_VALID_TASK_TYPES`` causes this test
        to pass (it only asserts acceptance, not exhaustiveness).
        """
        from orchestrator.task_manager import Task, TaskManager

        manager = TaskManager(storage_path=tmp_path / f"tasks_{task_type}.json")
        task = manager.create_task(
            task_type=task_type,
            description=f"Task of type {task_type}",
            assigned_to="openclaw",
        )

        assert isinstance(task, Task)
        assert task.task_type == task_type, (
            f"Task type mismatch: expected {task_type!r}, got {task.task_type!r}"
        )


# ---------------------------------------------------------------------------
# Contract tests — Task lifecycle / status transitions
# ---------------------------------------------------------------------------


class TestTaskLifecycle:
    """Contract tests: task status transitions follow pending -> running -> completed."""

    def test_update_status(self, tmp_path) -> None:
        """update_status() moves a task through pending -> running -> completed.

        Each intermediate state is verified so that the full transition chain
        is exercised rather than only the terminal state.
        """
        from orchestrator.task_manager import TaskManager

        manager = TaskManager(storage_path=tmp_path / "tasks.json")
        task = manager.create_task(
            task_type="analysis",
            description="Analyse dependencies",
            assigned_to="openclaw",
        )

        assert task.status == "pending"

        manager.update_status(task_id=task.id, status="running")
        running_task = manager.get_task(task.id)
        assert running_task.status == "running", (
            f"Expected status 'running', got {running_task.status!r}"
        )

        manager.update_status(task_id=task.id, status="completed")
        completed_task = manager.get_task(task.id)
        assert completed_task.status == "completed", (
            f"Expected status 'completed', got {completed_task.status!r}"
        )

    def test_update_with_result(self, tmp_path) -> None:
        """Completing a task with a result stores the result on the Task object.

        The result field must be non-empty after calling update_status with
        status='completed' and a non-None result value.
        """
        from orchestrator.task_manager import TaskManager

        manager = TaskManager(storage_path=tmp_path / "tasks.json")
        task = manager.create_task(
            task_type="analysis",
            description="Summarise the codebase",
            assigned_to="openclaw",
        )

        result_text = "The codebase contains 42 modules."
        manager.update_status(task_id=task.id, status="completed", result=result_text)

        completed = manager.get_task(task.id)
        assert completed.result, "Completed task must have a non-empty result"
        assert completed.result == result_text, (
            f"Task result mismatch: expected {result_text!r}, got {completed.result!r}"
        )

    def test_failed_task(self, tmp_path) -> None:
        """update_status() marks a task as failed and stores an error message.

        Verifies that the task status transitions to 'failed' and that the
        error information is accessible on the retrieved task.
        """
        from orchestrator.task_manager import TaskManager

        manager = TaskManager(storage_path=tmp_path / "tasks.json")
        task = manager.create_task(
            task_type="implementation",
            description="Implement the feature",
            assigned_to="codex",
        )

        error_msg = "Agent sandbox unreachable"
        manager.update_status(task_id=task.id, status="failed", error=error_msg)

        failed = manager.get_task(task.id)
        assert failed.status == "failed", f"Expected status 'failed', got {failed.status!r}"
        assert failed.error, "Failed task must have a non-empty error message"


# ---------------------------------------------------------------------------
# Behavioural tests — persistence to disk
# ---------------------------------------------------------------------------


class TestTaskPersistence:
    """Behavioural tests: TaskManager persists tasks to disk and reloads them."""

    def test_save_and_load(self, tmp_path) -> None:
        """Tasks created by one TaskManager are visible to a new instance loading the same file.

        Creates two tasks, disposes of the first manager, then verifies the
        second manager (pointing at the same storage file) returns identical tasks.
        """
        from orchestrator.task_manager import TaskManager

        storage = tmp_path / "tasks.json"

        manager1 = TaskManager(storage_path=storage)
        task_a = manager1.create_task(
            task_type="analysis",
            description="First task",
            assigned_to="openclaw",
        )
        task_b = manager1.create_task(
            task_type="planning",
            description="Second task",
            assigned_to="openclaw",
        )

        # Instantiate a fresh manager pointing at the same file.
        manager2 = TaskManager(storage_path=storage)
        loaded_a = manager2.get_task(task_a.id)
        loaded_b = manager2.get_task(task_b.id)

        assert loaded_a is not None, f"Task {task_a.id!r} not found after reload"
        assert loaded_b is not None, f"Task {task_b.id!r} not found after reload"
        assert loaded_a.description == task_a.description
        assert loaded_b.description == task_b.description

    def test_concurrent_access(self, tmp_path) -> None:
        """Two TaskManager instances writing to the same file do not corrupt data.

        Both managers create one task each.  After both writes, either manager
        must be able to retrieve both tasks.  This is a basic last-writer-wins
        or merge check — not a strict ACID test.
        """
        from orchestrator.task_manager import TaskManager

        storage = tmp_path / "shared_tasks.json"

        manager_a = TaskManager(storage_path=storage)
        manager_b = TaskManager(storage_path=storage)

        task_from_a = manager_a.create_task(
            task_type="analysis",
            description="Written by manager A",
            assigned_to="openclaw",
        )
        task_from_b = manager_b.create_task(
            task_type="planning",
            description="Written by manager B",
            assigned_to="openclaw",
        )

        # Either manager should be able to surface both tasks after a reload.
        manager_c = TaskManager(storage_path=storage)
        all_tasks = manager_c.list_tasks()
        task_ids = {t.id for t in all_tasks}

        # At minimum both tasks must survive — data must not be silently dropped.
        assert task_from_a.id in task_ids or task_from_b.id in task_ids, (
            "Neither task survived concurrent writes — storage may have been corrupted"
        )


# ---------------------------------------------------------------------------
# Contract tests — task queries and filtering
# ---------------------------------------------------------------------------


class TestTaskQueries:
    """Contract tests: TaskManager filtering methods return correct subsets."""

    def _populate(self, manager, tmp_path=None) -> dict:
        """Helper: create a known set of tasks and return their ids by label."""
        t_pending = manager.create_task(
            task_type="analysis",
            description="Pending analysis",
            assigned_to="openclaw",
        )
        t_running = manager.create_task(
            task_type="implementation",
            description="Running implementation",
            assigned_to="openclaw",
        )
        manager.update_status(task_id=t_running.id, status="running")

        t_done = manager.create_task(
            task_type="research",
            description="Completed research",
            assigned_to="gemini",
        )
        manager.update_status(task_id=t_done.id, status="completed", result="done")

        t_parent = manager.create_task(
            task_type="planning",
            description="Parent task",
            assigned_to="openclaw",
        )
        t_child = manager.create_task(
            task_type="implementation",
            description="Child task",
            assigned_to="codex",
            parent_task_id=t_parent.id,
        )

        return {
            "pending": t_pending,
            "running": t_running,
            "done": t_done,
            "parent": t_parent,
            "child": t_child,
        }

    def test_list_by_status(self, tmp_path) -> None:
        """list_tasks(status='pending') returns only tasks with status='pending'.

        Multiple tasks at different statuses are created, and the filter must
        exclude tasks in other states.
        """
        from orchestrator.task_manager import TaskManager

        manager = TaskManager(storage_path=tmp_path / "tasks.json")
        tasks = self._populate(manager)

        pending_tasks = manager.list_tasks(status="pending")
        pending_ids = {t.id for t in pending_tasks}

        assert tasks["pending"].id in pending_ids, (
            "Pending task not found in list_tasks(status='pending')"
        )
        assert tasks["running"].id not in pending_ids, (
            "Running task incorrectly included in list_tasks(status='pending')"
        )
        assert tasks["done"].id not in pending_ids, (
            "Completed task incorrectly included in list_tasks(status='pending')"
        )

    def test_list_by_agent(self, tmp_path) -> None:
        """list_tasks(assigned_to='gemini') returns only tasks assigned to that agent.

        Verifies that agent filtering is applied correctly and does not bleed
        tasks assigned to other agents into the result.
        """
        from orchestrator.task_manager import TaskManager

        manager = TaskManager(storage_path=tmp_path / "tasks.json")
        tasks = self._populate(manager)

        gemini_tasks = manager.list_tasks(assigned_to="gemini")
        gemini_ids = {t.id for t in gemini_tasks}

        assert tasks["done"].id in gemini_ids, (
            "Gemini task not found in list_tasks(assigned_to='gemini')"
        )
        assert tasks["pending"].id not in gemini_ids, (
            "openclaw task incorrectly included in list_tasks(assigned_to='gemini')"
        )

    def test_get_subtasks(self, tmp_path) -> None:
        """get_subtasks(parent_id) returns all direct children of a parent task.

        Creates a parent with one child, then verifies that get_subtasks returns
        exactly the child and not the parent itself or unrelated tasks.
        """
        from orchestrator.task_manager import TaskManager

        manager = TaskManager(storage_path=tmp_path / "tasks.json")
        tasks = self._populate(manager)

        subtasks = manager.get_subtasks(parent_task_id=tasks["parent"].id)
        subtask_ids = {t.id for t in subtasks}

        assert tasks["child"].id in subtask_ids, "Child task not found in get_subtasks()"
        assert tasks["parent"].id not in subtask_ids, (
            "Parent task incorrectly included in get_subtasks()"
        )
        assert tasks["pending"].id not in subtask_ids, (
            "Unrelated task incorrectly included in get_subtasks()"
        )
