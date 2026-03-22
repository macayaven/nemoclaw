"""Phase 6 tests for :mod:`orchestrator.sandbox_bridge`."""

from __future__ import annotations

import shlex
import subprocess
from types import SimpleNamespace

import pytest
from orchestrator.config import OrchestratorSettings
from orchestrator.sandbox_bridge import SandboxBridge, SandboxResult

pytestmark = pytest.mark.phase6


class TestSandboxBridge:
    """Contract and behavioural tests for the sandbox bridge."""

    def test_bridge_initializes_with_settings(self) -> None:
        settings = OrchestratorSettings()
        bridge = SandboxBridge(settings=settings)

        assert bridge.settings is settings
        assert bridge.settings.sandbox_timeout > 0

    def test_list_sandboxes_uses_configured_agents(self) -> None:
        bridge = SandboxBridge(settings=OrchestratorSettings())

        sandboxes = bridge.list_sandboxes()

        assert "nemoclaw-main" in sandboxes
        assert "codex-dev" in sandboxes

    def test_run_in_sandbox_returns_structured_result(self, monkeypatch) -> None:
        bridge = SandboxBridge(settings=OrchestratorSettings())
        recorded: list[list[str]] = []

        def fake_run(cmd, capture_output, text, timeout):
            recorded.append(cmd)
            return SimpleNamespace(stdout="hello\n", stderr="", returncode=0)

        monkeypatch.setattr(subprocess, "run", fake_run)

        result = bridge.run_in_sandbox("nemoclaw-main", "echo hello")

        assert isinstance(result, SandboxResult)
        assert result.return_code == 0
        assert result.stdout == "hello\n"
        assert recorded[0][0] == "ssh"
        assert "nemoclaw-main" in " ".join(recorded[0])

    def test_run_in_sandbox_rejects_empty_command(self) -> None:
        bridge = SandboxBridge(settings=OrchestratorSettings())

        with pytest.raises(ValueError, match="command must not be empty"):
            bridge.run_in_sandbox("nemoclaw-main", "   ")

    def test_run_in_sandbox_propagates_timeout(self, monkeypatch) -> None:
        bridge = SandboxBridge(settings=OrchestratorSettings())

        def fake_run(cmd, capture_output, text, timeout):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(subprocess.TimeoutExpired):
            bridge.run_in_sandbox("nemoclaw-main", "sleep 60", timeout=2)

    def test_send_prompt_uses_agent_template(self, monkeypatch) -> None:
        bridge = SandboxBridge(settings=OrchestratorSettings())
        recorded: list[tuple[str, str]] = []

        def fake_run_in_sandbox(sandbox_name: str, command: str, timeout=None) -> SandboxResult:
            recorded.append((sandbox_name, command))
            return SandboxResult(
                sandbox_name=sandbox_name,
                stdout="ready\n",
                stderr="",
                return_code=0,
                duration_ms=10.0,
            )

        monkeypatch.setattr(bridge, "run_in_sandbox", fake_run_in_sandbox)

        response = bridge.send_prompt(
            sandbox_name="nemoclaw-main",
            prompt="Reply with ready",
            agent_type="openclaw",
        )

        assert response == "ready"
        assert recorded[0][0] == "nemoclaw-main"
        assert "openclaw agent --agent main --local -m" in recorded[0][1]
        assert shlex.quote("Reply with ready") in recorded[0][1]

    def test_send_prompt_rejects_unknown_agent_type(self) -> None:
        bridge = SandboxBridge(settings=OrchestratorSettings())

        with pytest.raises(ValueError, match="Unsupported agent_type"):
            bridge.send_prompt(
                sandbox_name="nemoclaw-main",
                prompt="hello",
                agent_type="unknown",
            )

    def test_send_prompt_raises_for_failed_command(self, monkeypatch) -> None:
        bridge = SandboxBridge(settings=OrchestratorSettings())

        monkeypatch.setattr(
            bridge,
            "run_in_sandbox",
            lambda sandbox_name, command, timeout=None: SandboxResult(
                sandbox_name=sandbox_name,
                stdout="",
                stderr="boom",
                return_code=17,
                duration_ms=12.0,
            ),
        )

        with pytest.raises(RuntimeError, match="returned exit code 17"):
            bridge.send_prompt(
                sandbox_name="nemoclaw-main",
                prompt="hello",
                agent_type="openclaw",
            )

    def test_is_sandbox_healthy_returns_true_when_echo_succeeds(self, monkeypatch) -> None:
        bridge = SandboxBridge(settings=OrchestratorSettings())

        monkeypatch.setattr(
            bridge,
            "run_in_sandbox",
            lambda sandbox_name, command, timeout=None: SandboxResult(
                sandbox_name=sandbox_name,
                stdout="ok\n",
                stderr="",
                return_code=0,
                duration_ms=5.0,
            ),
        )

        assert bridge.is_sandbox_healthy("nemoclaw-main") is True

    def test_is_sandbox_healthy_returns_false_on_timeout(self, monkeypatch) -> None:
        bridge = SandboxBridge(settings=OrchestratorSettings())

        def fake_run_in_sandbox(sandbox_name: str, command: str, timeout=None) -> SandboxResult:
            raise subprocess.TimeoutExpired(cmd=[sandbox_name, command], timeout=timeout or 10)

        monkeypatch.setattr(bridge, "run_in_sandbox", fake_run_in_sandbox)

        assert bridge.is_sandbox_healthy("nemoclaw-main") is False
