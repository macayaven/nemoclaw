"""Phase 6 tests for the orchestrator CLI."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from orchestrator.task_manager import Task

pytestmark = pytest.mark.phase6


class _FakeBridge:
    def list_sandboxes(self) -> list[str]:
        return ["claude-dev", "nemoclaw-main"]

    def is_sandbox_healthy(self, sandbox_name: str) -> bool:
        return sandbox_name != "claude-dev"


class _FakeTaskManager:
    def __init__(self) -> None:
        self._tasks = [
            Task(
                type="analysis",
                prompt="Summarise the deployment state",
                assigned_to="openclaw",
                status="completed",
                result="ready",
            )
        ]

    def list_tasks(self, status=None, assigned_to=None):  # noqa: ANN001, ANN201
        tasks = self._tasks
        if status is not None:
            tasks = [task for task in tasks if task.status == status]
        if assigned_to is not None:
            tasks = [task for task in tasks if task.assigned_to == assigned_to]
        return tasks


class _FakeOrchestrator:
    def __init__(self) -> None:
        self.bridge = _FakeBridge()
        self.task_manager = _FakeTaskManager()

    def delegate(self, prompt: str, agent: str, task_type: str = "analysis") -> str:
        return f"{agent}:{task_type}:{prompt}"

    def pipeline(self, prompt: str, steps):  # noqa: ANN001, ANN201
        step_results = [
            SimpleNamespace(
                step_index=index,
                agent=step.agent,
                task_type=step.task_type,
                output=f"output-{index + 1}",
                duration_ms=10.0,
                model_dump=lambda idx=index, step=step: {
                    "step_index": idx,
                    "agent": step.agent,
                    "task_type": step.task_type,
                    "output": f"output-{idx + 1}",
                    "duration_ms": 10.0,
                    "task_id": f"task-{idx + 1}",
                },
            )
            for index, step in enumerate(steps)
        ]
        return SimpleNamespace(
            final_output=f"pipeline:{prompt}",
            total_duration_ms=20.0,
            steps=step_results,
        )


@pytest.fixture
def fake_cli(monkeypatch):
    """Patch CLI wiring so tests can exercise the real parser offline."""
    from orchestrator import cli

    fake_orchestrator = _FakeOrchestrator()
    monkeypatch.setattr(cli, "OrchestratorSettings", lambda: object())
    monkeypatch.setattr(cli, "Orchestrator", lambda settings: fake_orchestrator)
    return cli


class TestCLIHealth:
    """Health command tests."""

    def test_health_command_reports_sandboxes(self, fake_cli, capsys) -> None:
        with pytest.raises(SystemExit) as exc_info:
            fake_cli.main(["health"])

        captured = capsys.readouterr()

        assert exc_info.value.code == 1
        assert "nemoclaw-main" in captured.out
        assert "UNREACHABLE" in captured.out


class TestCLIStatus:
    """Status command tests."""

    def test_status_command_renders_table(self, fake_cli, capsys) -> None:
        with pytest.raises(SystemExit) as exc_info:
            fake_cli.main(["status"])

        captured = capsys.readouterr()

        assert exc_info.value.code == 0
        assert "ID" in captured.out
        assert "openclaw" in captured.out

    def test_status_command_supports_json(self, fake_cli, capsys) -> None:
        with pytest.raises(SystemExit) as exc_info:
            fake_cli.main(["--json", "status"])

        payload = json.loads(capsys.readouterr().out)

        assert exc_info.value.code == 0
        assert payload[0]["assigned_to"] == "openclaw"
        assert payload[0]["status"] == "completed"


class TestCLIDelegate:
    """Delegate command tests."""

    def test_delegate_command_supports_json(self, fake_cli, capsys) -> None:
        with pytest.raises(SystemExit) as exc_info:
            fake_cli.main(
                [
                    "--json",
                    "delegate",
                    "--agent",
                    "codex",
                    "--prompt",
                    "Build the parser",
                    "--task-type",
                    "implementation",
                ]
            )

        payload = json.loads(capsys.readouterr().out)

        assert exc_info.value.code == 0
        assert payload["agent"] == "codex"
        assert payload["response"] == "codex:implementation:Build the parser"


class TestCLINegative:
    """Negative command handling tests."""

    def test_invalid_command_exits_nonzero(self) -> None:
        from orchestrator import cli

        with pytest.raises(SystemExit) as exc_info:
            cli.main(["this-is-not-a-real-subcommand"])

        assert exc_info.value.code != 0
