"""Phase 6 tests for :mod:`orchestrator.task_manager`."""

from __future__ import annotations

import pytest
from orchestrator.task_manager import Task, TaskManager

pytestmark = pytest.mark.phase6

_VALID_TASK_TYPES = [
    "analysis",
    "implementation",
    "research",
    "code_generation",
    "code_review",
]


class TestTaskCreation:
    """Task creation and shape tests."""

    def test_create_task(self, tmp_path) -> None:
        manager = TaskManager(tmp_path / "shared")

        task = manager.create_task(
            type="analysis",
            prompt="Analyse the project structure",
            assigned_to="openclaw",
        )

        assert isinstance(task, Task)
        assert task.id
        assert task.status == "pending"
        assert task.created_at is not None
        assert task.type == "analysis"
        assert task.prompt == "Analyse the project structure"

    def test_create_subtask(self, tmp_path) -> None:
        manager = TaskManager(tmp_path / "shared")

        parent = manager.create_task(
            type="analysis",
            prompt="Plan the implementation",
            assigned_to="openclaw",
        )
        child = manager.create_task(
            type="implementation",
            prompt="Implement step 1",
            assigned_to="codex",
            parent_task_id=parent.id,
        )

        assert child.parent_task_id == parent.id
        assert child.id != parent.id

    @pytest.mark.parametrize("task_type", _VALID_TASK_TYPES)
    def test_valid_task_types(self, tmp_path, task_type: str) -> None:
        manager = TaskManager(tmp_path / "shared")

        task = manager.create_task(
            type=task_type,
            prompt=f"Task of type {task_type}",
            assigned_to="openclaw",
        )

        assert task.type == task_type


class TestTaskLifecycle:
    """Task status transition tests."""

    def test_update_task_status(self, tmp_path) -> None:
        manager = TaskManager(tmp_path / "shared")
        task = manager.create_task(
            type="analysis",
            prompt="Analyse dependencies",
            assigned_to="openclaw",
        )

        manager.update_task(task.id, "running")
        running_task = manager.get_task(task.id)
        assert running_task.status == "running"
        assert running_task.completed_at is None

        manager.update_task(task.id, "completed", result="done")
        completed_task = manager.get_task(task.id)
        assert completed_task.status == "completed"
        assert completed_task.result == "done"
        assert completed_task.completed_at is not None

    def test_failed_task_stores_result_message(self, tmp_path) -> None:
        manager = TaskManager(tmp_path / "shared")
        task = manager.create_task(
            type="implementation",
            prompt="Implement the feature",
            assigned_to="codex",
        )

        manager.update_task(task.id, "failed", result="Agent sandbox unreachable")
        failed_task = manager.get_task(task.id)

        assert failed_task.status == "failed"
        assert failed_task.result == "Agent sandbox unreachable"


class TestTaskPersistence:
    """Persistence and multi-instance safety tests."""

    def test_save_and_load(self, tmp_path) -> None:
        shared_workspace = tmp_path / "shared"
        manager1 = TaskManager(shared_workspace)
        task_a = manager1.create_task(
            type="analysis",
            prompt="First task",
            assigned_to="openclaw",
        )
        task_b = manager1.create_task(
            type="research",
            prompt="Second task",
            assigned_to="gemini",
        )

        manager2 = TaskManager(shared_workspace)

        loaded_a = manager2.get_task(task_a.id)
        loaded_b = manager2.get_task(task_b.id)

        assert loaded_a.prompt == task_a.prompt
        assert loaded_b.prompt == task_b.prompt

    def test_concurrent_instances_preserve_each_others_tasks(self, tmp_path) -> None:
        shared_workspace = tmp_path / "shared"
        manager_a = TaskManager(shared_workspace)
        manager_b = TaskManager(shared_workspace)

        task_from_a = manager_a.create_task(
            type="analysis",
            prompt="Written by manager A",
            assigned_to="openclaw",
        )
        task_from_b = manager_b.create_task(
            type="research",
            prompt="Written by manager B",
            assigned_to="gemini",
        )

        manager_c = TaskManager(shared_workspace)
        task_ids = {task.id for task in manager_c.list_tasks()}

        assert task_from_a.id in task_ids
        assert task_from_b.id in task_ids

    def test_corrupt_storage_raises_instead_of_clobbering_state(self, tmp_path) -> None:
        shared_workspace = tmp_path / "shared"
        shared_workspace.mkdir()
        tasks_file = shared_workspace / "tasks.json"
        tasks_file.write_text("{not valid json", encoding="utf-8")

        with pytest.raises(RuntimeError, match="Failed to read persisted task state"):
            TaskManager(shared_workspace)


class TestTaskQueries:
    """Task filtering and hierarchy tests."""

    def _populate(self, manager: TaskManager) -> dict[str, Task]:
        pending = manager.create_task(
            type="analysis",
            prompt="Pending analysis",
            assigned_to="openclaw",
        )
        running = manager.create_task(
            type="implementation",
            prompt="Running implementation",
            assigned_to="codex",
        )
        manager.update_task(running.id, "running")

        completed = manager.create_task(
            type="research",
            prompt="Completed research",
            assigned_to="gemini",
        )
        manager.update_task(completed.id, "completed", result="done")

        parent = manager.create_task(
            type="analysis",
            prompt="Parent task",
            assigned_to="openclaw",
        )
        child = manager.create_task(
            type="implementation",
            prompt="Child task",
            assigned_to="codex",
            parent_task_id=parent.id,
        )

        return {
            "pending": pending,
            "running": running,
            "completed": completed,
            "parent": parent,
            "child": child,
        }

    def test_list_by_status(self, tmp_path) -> None:
        manager = TaskManager(tmp_path / "shared")
        tasks = self._populate(manager)

        pending_ids = {task.id for task in manager.list_tasks(status="pending")}

        assert tasks["pending"].id in pending_ids
        assert tasks["running"].id not in pending_ids
        assert tasks["completed"].id not in pending_ids

    def test_list_by_agent(self, tmp_path) -> None:
        manager = TaskManager(tmp_path / "shared")
        tasks = self._populate(manager)

        gemini_ids = {task.id for task in manager.list_tasks(assigned_to="gemini")}

        assert tasks["completed"].id in gemini_ids
        assert tasks["pending"].id not in gemini_ids

    def test_get_subtasks(self, tmp_path) -> None:
        manager = TaskManager(tmp_path / "shared")
        tasks = self._populate(manager)

        subtask_ids = {task.id for task in manager.get_subtasks(tasks["parent"].id)}

        assert tasks["child"].id in subtask_ids
        assert tasks["parent"].id not in subtask_ids
        assert tasks["pending"].id not in subtask_ids
