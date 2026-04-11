"""Phase 6 tests for :mod:`orchestrator.sandbox_bridge`."""

from __future__ import annotations

import io
import selectors
import subprocess

import pytest

from orchestrator.config import OrchestratorSettings
from orchestrator.sandbox_bridge import SandboxBridge

pytestmark = pytest.mark.phase6


class _FakePipe(io.BytesIO):
    def read1(self, size: int | None = -1) -> bytes:  # pragma: no cover - BytesIO compat shim
        if size is None:
            return self.read()
        return self.read(size)


class _FakePopen:
    def __init__(self, stdout: bytes, stderr: bytes, return_code: int = 0) -> None:
        self.stdout = _FakePipe(stdout)
        self.stderr = _FakePipe(stderr)
        self._return_code = return_code
        self.killed = False

    def wait(self, timeout: float | None = None) -> int:
        return self._return_code

    def kill(self) -> None:
        self.killed = True


class _FakeSelector:
    def __init__(self) -> None:
        self._entries: dict[object, str] = {}

    def register(self, fileobj, _event, data=None) -> None:
        self._entries[fileobj] = data

    def unregister(self, fileobj) -> None:
        self._entries.pop(fileobj, None)

    def select(self, timeout=None):
        return [
            (type("Key", (), {"fileobj": fileobj, "data": data})(), None)
            for fileobj, data in list(self._entries.items())
        ]

    def get_map(self):
        return self._entries

    def close(self) -> None:
        self._entries.clear()


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
        assert "opencode-dev" in sandboxes

    def test_run_in_sandbox_returns_structured_result(self, monkeypatch) -> None:
        bridge = SandboxBridge(settings=OrchestratorSettings())
        recorded: list[list[str]] = []

        def fake_popen(cmd, stdout, stderr, stdin):
            recorded.append(cmd)
            return _FakePopen(stdout=b"hello\n", stderr=b"", return_code=0)

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        monkeypatch.setattr(selectors, "DefaultSelector", _FakeSelector)

        result = bridge.run_in_sandbox("nemoclaw-main", "echo hello")

        assert result.return_code == 0
        assert result.stdout == "hello\n"
        assert result.stdout_bytes == len(b"hello\n")
        assert recorded[0][0] == "ssh"
        assert "nemoclaw-main" in " ".join(recorded[0])

    def test_run_in_sandbox_rejects_empty_command(self) -> None:
        bridge = SandboxBridge(settings=OrchestratorSettings())

        with pytest.raises(ValueError, match="command must not be empty"):
            bridge.run_in_sandbox("nemoclaw-main", "   ")

    def test_run_in_sandbox_truncates_large_output(self, monkeypatch) -> None:
        bridge = SandboxBridge(settings=OrchestratorSettings(sandbox_output_limit_bytes=4 * 1024))

        monkeypatch.setattr(
            subprocess,
            "Popen",
            lambda *args, **kwargs: _FakePopen(stdout=b"a" * 8192, stderr=b"", return_code=0),
        )
        monkeypatch.setattr(selectors, "DefaultSelector", _FakeSelector)

        result = bridge.run_in_sandbox("nemoclaw-main", "yes | head")

        assert result.stdout_truncated is True
        assert result.stdout_bytes == 8192
        assert len(result.stdout.encode("utf-8")) == 4096

    def test_send_prompt_uses_agent_template(self, monkeypatch) -> None:
        bridge = SandboxBridge(settings=OrchestratorSettings())
        recorded: list[tuple[str, str]] = []

        def fake_run_in_sandbox(sandbox_name: str, command: str, timeout=None, on_chunk=None):
            recorded.append((sandbox_name, command))
            from orchestrator.models import SandboxResult

            return SandboxResult(
                sandbox_name=sandbox_name,
                command=command,
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

        assert response.output_text == "ready"
        assert recorded[0][0] == "nemoclaw-main"
        assert "openclaw agent --agent main --local -m" in recorded[0][1]

    def test_send_prompt_supports_opencode_template(self, monkeypatch) -> None:
        bridge = SandboxBridge(settings=OrchestratorSettings())
        recorded: list[tuple[str, str]] = []

        def fake_run_in_sandbox(sandbox_name: str, command: str, timeout=None, on_chunk=None):
            recorded.append((sandbox_name, command))
            from orchestrator.models import SandboxResult

            return SandboxResult(
                sandbox_name=sandbox_name,
                command=command,
                stdout="ready\n",
                stderr="",
                return_code=0,
                duration_ms=10.0,
            )

        monkeypatch.setattr(bridge, "run_in_sandbox", fake_run_in_sandbox)

        response = bridge.send_prompt(
            sandbox_name="opencode-dev",
            prompt="Reply with ready",
            agent_type="opencode",
        )

        assert response.output_text == "ready"
        assert recorded[0][0] == "opencode-dev"
        assert "opencode run -m zai/glm-5.1" in recorded[0][1]

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

        from orchestrator.models import SandboxResult

        monkeypatch.setattr(
            bridge,
            "run_in_sandbox",
            lambda sandbox_name, command, timeout=None, on_chunk=None: SandboxResult(
                sandbox_name=sandbox_name,
                command=command,
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

        from orchestrator.models import SandboxResult

        monkeypatch.setattr(
            bridge,
            "run_in_sandbox",
            lambda sandbox_name, command, timeout=None, on_chunk=None: SandboxResult(
                sandbox_name=sandbox_name,
                command=command,
                stdout="ok\n",
                stderr="",
                return_code=0,
                duration_ms=5.0,
            ),
        )

        assert bridge.is_sandbox_healthy("nemoclaw-main") is True

    def test_is_sandbox_healthy_returns_false_on_timeout(self, monkeypatch) -> None:
        bridge = SandboxBridge(settings=OrchestratorSettings())

        def fake_run_in_sandbox(sandbox_name: str, command: str, timeout=None, on_chunk=None):
            raise subprocess.TimeoutExpired(cmd=[sandbox_name, command], timeout=timeout or 10)

        monkeypatch.setattr(bridge, "run_in_sandbox", fake_run_in_sandbox)

        assert bridge.is_sandbox_healthy("nemoclaw-main") is False
