"""Typed orchestration models shared across runtime components."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field, computed_field

TaskStatus = Literal["pending", "running", "completed", "failed"]
TaskType = Literal["research", "code_generation", "code_review", "analysis", "implementation"]
QueueStatus = Literal["queued", "leased", "completed", "dead_letter"]


class SandboxResult(BaseModel):
    """Structured result returned by a sandbox command execution."""

    sandbox_name: str
    command: str
    stdout: str
    stderr: str
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    return_code: int
    duration_ms: float
    timed_out: bool = False
    transport: str = "ssh"

    @computed_field  # type: ignore[misc]
    @property
    def success(self) -> bool:
        return self.return_code == 0 and not self.timed_out

    @computed_field  # type: ignore[misc]
    @property
    def output_text(self) -> str:
        return self.stdout.strip()


class TaskResult(BaseModel):
    """Structured terminal result stored with a task."""

    output_text: str | None = None
    error: str | None = None
    sandbox_result: SandboxResult | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class Task(BaseModel):
    """A unit of work assigned to a single agent sandbox."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: TaskType
    prompt: str
    assigned_to: str
    status: TaskStatus = "pending"
    result: TaskResult | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    started_at: str | None = None
    completed_at: str | None = None
    parent_task_id: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class DelegationResult(BaseModel):
    """Structured result returned by a delegated task execution."""

    task_id: str
    agent: str
    task_type: TaskType
    prompt: str
    output_text: str
    sandbox_result: SandboxResult
    duration_ms: float


class StepResult(BaseModel):
    """Result produced by one pipeline step."""

    step_index: int
    agent: str
    task_type: TaskType
    prompt: str
    output_text: str
    duration_ms: float
    task_id: str
    sandbox_result: SandboxResult


class PipelineResult(BaseModel):
    """Aggregated result of a full pipeline execution."""

    steps: list[StepResult] = Field(default_factory=list)
    final_output: str = ""
    total_duration_ms: float = 0.0


class QueueItem(BaseModel):
    """A durable unit of background work stored in SQLite."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    queue_name: str
    payload: dict[str, object]
    status: QueueStatus = "queued"
    dedupe_key: str | None = None
    available_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    lease_expires_at: str | None = None
    claimed_by: str | None = None
    attempts: int = 0
    max_attempts: int = 5
    last_error: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    completed_at: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


def iso_after(seconds: int) -> str:
    """Return a UTC timestamp a number of seconds in the future."""
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()
