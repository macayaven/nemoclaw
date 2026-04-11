"""Task lifecycle management for multi-agent orchestration workflows."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from orchestrator.models import Task, TaskResult, TaskStatus, TaskType
from orchestrator.storage import SQLiteStore, utc_now


class TaskManager:
    """Manage task persistence using SQLite instead of a shared JSON file."""

    _LEGACY_TASKS_FILE = "tasks.json"

    def __init__(self, shared_workspace: Path, store: SQLiteStore | None = None) -> None:
        self.shared_workspace = Path(shared_workspace)
        self.shared_workspace.mkdir(parents=True, exist_ok=True)
        self.store = store or SQLiteStore(self.shared_workspace)
        self._migrate_legacy_json_if_present()

    def create_task(
        self,
        type: TaskType,
        prompt: str,
        assigned_to: str,
        parent_task_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> Task:
        task = Task(
            type=type,
            prompt=prompt,
            assigned_to=assigned_to,
            parent_task_id=parent_task_id,
            metadata=metadata or {},
        )
        with self.store.transaction() as connection:
            connection.execute(
                """
                INSERT INTO tasks (
                    id, type, prompt, assigned_to, status, result_json, created_at,
                    started_at, completed_at, parent_task_id, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.id,
                    task.type,
                    task.prompt,
                    task.assigned_to,
                    task.status,
                    None,
                    task.created_at,
                    task.started_at,
                    task.completed_at,
                    task.parent_task_id,
                    json.dumps(task.metadata),
                ),
            )
        return task

    def update_task(
        self,
        task_id: str,
        status: TaskStatus,
        result: TaskResult | None = None,
    ) -> Task:
        now_iso = utc_now()
        with self.store.transaction() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(f"Task {task_id!r} not found.")

            task = self._row_to_task(row)
            started_at = task.started_at
            completed_at = task.completed_at

            if status == "running" and started_at is None:
                started_at = now_iso
            if status in {"completed", "failed"}:
                completed_at = now_iso

            connection.execute(
                """
                UPDATE tasks
                SET status = ?,
                    result_json = ?,
                    started_at = ?,
                    completed_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    json.dumps(result.model_dump()) if result is not None else None,
                    started_at,
                    completed_at,
                    task_id,
                ),
            )

            return task.model_copy(
                update={
                    "status": status,
                    "result": result,
                    "started_at": started_at,
                    "completed_at": completed_at,
                }
            )

    def get_task(self, task_id: str) -> Task:
        with self.store.connect() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(f"Task {task_id!r} not found.")
            return self._row_to_task(row)

    def list_tasks(
        self,
        status: TaskStatus | None = None,
        assigned_to: str | None = None,
    ) -> list[Task]:
        filters: list[str] = []
        params: list[str] = []
        if status is not None:
            filters.append("status = ?")
            params.append(status)
        if assigned_to is not None:
            filters.append("assigned_to = ?")
            params.append(assigned_to)

        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        query = f"""
            SELECT * FROM tasks
            {where_clause}
            ORDER BY created_at ASC
        """
        with self.store.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_task(row) for row in rows]

    def get_subtasks(self, parent_task_id: str) -> list[Task]:
        with self.store.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM tasks
                WHERE parent_task_id = ?
                ORDER BY created_at ASC
                """,
                (parent_task_id,),
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def _row_to_task(self, row) -> Task:
        result = TaskResult(**json.loads(row["result_json"])) if row["result_json"] else None
        return Task(
            id=row["id"],
            type=row["type"],
            prompt=row["prompt"],
            assigned_to=row["assigned_to"],
            status=row["status"],
            result=result,
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            parent_task_id=row["parent_task_id"],
            metadata=json.loads(row["metadata_json"]),
        )

    def _migrate_legacy_json_if_present(self) -> None:
        legacy_file = self.shared_workspace / self._LEGACY_TASKS_FILE
        if not legacy_file.exists():
            return
        with self.store.connect() as connection:
            existing_tasks = connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        if existing_tasks:
            return
        try:
            raw = json.loads(legacy_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Failed to read persisted task state from {legacy_file}: {exc}"
            ) from exc

        with self.store.transaction() as connection:
            for task_id, payload in raw.items():
                task = Task(
                    id=task_id,
                    type=payload["type"],
                    prompt=payload["prompt"],
                    assigned_to=payload["assigned_to"],
                    status=payload["status"],
                    result=(
                        TaskResult(output_text=payload["result"])
                        if payload.get("result") is not None
                        else None
                    ),
                    created_at=payload.get("created_at", datetime.now(UTC).isoformat()),
                    completed_at=payload.get("completed_at"),
                    parent_task_id=payload.get("parent_task_id"),
                    metadata=payload.get("metadata", {}),
                )
                connection.execute(
                    """
                    INSERT INTO tasks (
                        id, type, prompt, assigned_to, status, result_json, created_at,
                        started_at, completed_at, parent_task_id, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task.id,
                        task.type,
                        task.prompt,
                        task.assigned_to,
                        task.status,
                        json.dumps(task.result.model_dump()) if task.result else None,
                        task.created_at,
                        task.started_at,
                        task.completed_at,
                        task.parent_task_id,
                        json.dumps(task.metadata),
                    ),
                )
        legacy_file.rename(legacy_file.with_suffix(".json.migrated"))


__all__ = ["Task", "TaskManager", "TaskResult", "TaskStatus", "TaskType"]
