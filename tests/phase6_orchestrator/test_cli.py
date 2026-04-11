"""Phase 6 tests for the orchestrator CLI."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from orchestrator.models import DelegationResult, SandboxResult, TaskResult
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
                result=TaskResult(output_text="ready"),
            )
        ]

    def list_tasks(self, status=None, assigned_to=None):
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

    def delegate(self, prompt: str, agent: str, task_type: str = "analysis") -> DelegationResult:
        return DelegationResult(
            task_id="task-1",
            agent=agent,
            task_type=task_type,
            prompt=prompt,
            output_text=f"{agent}:{task_type}:{prompt}",
            sandbox_result=SandboxResult(
                sandbox_name="sandbox",
                command="fake",
                stdout="ok\n",
                stderr="",
                return_code=0,
                duration_ms=10.0,
            ),
            duration_ms=10.0,
        )

    def pipeline(self, prompt: str, steps):
        step_results = [
            SimpleNamespace(
                step_index=index,
                agent=step.agent,
                task_type=step.task_type,
                prompt=f"prompt-{index + 1}",
                output_text=f"output-{index + 1}",
                duration_ms=10.0,
                sandbox_result=SandboxResult(
                    sandbox_name="sandbox",
                    command="fake",
                    stdout="ok\n",
                    stderr="",
                    return_code=0,
                    duration_ms=10.0,
                ),
                model_dump=lambda idx=index, step=step: {
                    "step_index": idx,
                    "agent": step.agent,
                    "task_type": step.task_type,
                    "prompt": f"prompt-{idx + 1}",
                    "output_text": f"output-{idx + 1}",
                    "duration_ms": 10.0,
                    "task_id": f"task-{idx + 1}",
                    "sandbox_result": {
                        "sandbox_name": "sandbox",
                        "command": "fake",
                        "stdout": "ok\n",
                        "stderr": "",
                        "return_code": 0,
                        "duration_ms": 10.0,
                    },
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


class TestCLIServeProxy:
    """Router proxy bootstrap command tests."""

    def test_serve_proxy_starts_with_explicit_upstreams(self, monkeypatch, capsys) -> None:
        from orchestrator import cli

        recorded: dict[str, object] = {}

        class _FakeServer:
            base_url = "http://127.0.0.1:18080"

            def shutdown(self) -> None:
                recorded["shutdown"] = True

        monkeypatch.setattr(
            cli,
            "start_proxy_server",
            lambda app, host, port: (
                recorded.update({"app": app, "host": host, "port": port}) or _FakeServer()
            ),
        )
        monkeypatch.setattr(
            cli.time, "sleep", lambda seconds: (_ for _ in ()).throw(KeyboardInterrupt())
        )

        with pytest.raises(SystemExit) as exc_info:
            cli.main(
                [
                    "serve-proxy",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "18080",
                    "--local-upstream-url",
                    "http://127.0.0.1:11434/v1",
                    "--medgemma-upstream-url",
                    "http://mac-studio.local:11435/v1",
                ]
            )

        captured = capsys.readouterr()

        assert exc_info.value.code == 0
        assert recorded["host"] == "127.0.0.1"
        assert recorded["port"] == 18080
        assert recorded["shutdown"] is True
        assert "Router proxy listening on http://127.0.0.1:18080" in captured.out


class TestCLINegative:
    """Negative command handling tests."""

    def test_invalid_command_exits_nonzero(self) -> None:
        from orchestrator import cli

        with pytest.raises(SystemExit) as exc_info:
            cli.main(["this-is-not-a-real-subcommand"])

        assert exc_info.value.code != 0
