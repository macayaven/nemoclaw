r"""Bridge that executes commands inside OpenShell sandbox containers."""

from __future__ import annotations

import selectors
import shlex
import subprocess
import time
from collections.abc import Callable
from io import BufferedReader
from typing import TYPE_CHECKING, cast

from orchestrator.models import SandboxResult

if TYPE_CHECKING:
    from orchestrator.config import OrchestratorSettings

OutputChunkHandler = Callable[[str, bytes], None]


_AGENT_CMD_TEMPLATES: dict[str, str] = {
    "openclaw": "openclaw agent --agent main --local -m {prompt} --session-id orchestrator",
    "claude": "claude -p {prompt} --output-format text",
    "codex": "source ~/.bashrc && cd /sandbox && codex exec {prompt}",
    "gemini": "gemini -p {prompt}",
    "opencode": "source ~/.bashrc && cd /sandbox && opencode run -m zai/glm-5.1 {prompt}",
}


class SandboxBridge:
    """Sends commands into OpenShell sandboxes from the host."""

    def __init__(self, settings: OrchestratorSettings) -> None:
        self.settings = settings

    def run_in_sandbox(
        self,
        sandbox_name: str,
        command: str,
        timeout: int | None = None,
        *,
        on_chunk: OutputChunkHandler | None = None,
    ) -> SandboxResult:
        if not command.strip():
            raise ValueError("command must not be empty")

        effective_timeout = timeout if timeout is not None else self.settings.sandbox_timeout
        output_limit = self.settings.sandbox_output_limit_bytes
        outer_cmd: list[str] = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "LogLevel=ERROR",
            "-o",
            f"ConnectTimeout={self.settings.ssh_connect_timeout}",
            "-o",
            f"ServerAliveInterval={self.settings.ssh_server_alive_interval}",
            "-o",
            f"ServerAliveCountMax={self.settings.ssh_server_alive_count_max}",
            "-o",
            f"ProxyCommand=openshell ssh-proxy --gateway-name openshell --name {sandbox_name}",
            f"sandbox@openshell-{sandbox_name}",
            command,
        ]

        start_ns = time.monotonic_ns()
        process = subprocess.Popen(
            outer_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
        )
        selector = selectors.DefaultSelector()
        assert process.stdout is not None
        assert process.stderr is not None
        selector.register(process.stdout, selectors.EVENT_READ, data="stdout")
        selector.register(process.stderr, selectors.EVENT_READ, data="stderr")

        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        stdout_bytes = 0
        stderr_bytes = 0
        stdout_kept = 0
        stderr_kept = 0
        stdout_truncated = False
        stderr_truncated = False

        def _capture(stream_name: str, chunk: bytes) -> None:
            nonlocal stdout_bytes, stderr_bytes, stdout_kept, stderr_kept
            nonlocal stdout_truncated, stderr_truncated
            if stream_name == "stdout":
                stdout_bytes += len(chunk)
                remaining = max(output_limit - stdout_kept, 0)
                if remaining > 0:
                    kept = chunk[:remaining]
                    stdout_chunks.append(kept)
                    stdout_kept += len(kept)
                if stdout_kept < stdout_bytes:
                    stdout_truncated = True
            else:
                stderr_bytes += len(chunk)
                remaining = max(output_limit - stderr_kept, 0)
                if remaining > 0:
                    kept = chunk[:remaining]
                    stderr_chunks.append(kept)
                    stderr_kept += len(kept)
                if stderr_kept < stderr_bytes:
                    stderr_truncated = True

        try:
            while selector.get_map():
                elapsed = (time.monotonic_ns() - start_ns) / 1_000_000_000
                remaining_timeout = effective_timeout - elapsed
                if remaining_timeout <= 0:
                    process.kill()
                    process.wait()
                    elapsed_ms = (time.monotonic_ns() - start_ns) / 1_000_000
                    raise subprocess.TimeoutExpired(
                        cmd=outer_cmd,
                        timeout=effective_timeout,
                        output=b"".join(stdout_chunks),
                        stderr=b"".join(stderr_chunks),
                    )

                for key, _mask in selector.select(timeout=remaining_timeout):
                    stream = cast(BufferedReader, key.fileobj)
                    chunk = stream.read1(4096)
                    if not chunk:
                        selector.unregister(stream)
                        continue
                    _capture(key.data, chunk)
                    if on_chunk is not None:
                        on_chunk(key.data, chunk)

            return_code = process.wait(timeout=1)
        finally:
            selector.close()

        elapsed_ms = (time.monotonic_ns() - start_ns) / 1_000_000
        return SandboxResult(
            sandbox_name=sandbox_name,
            command=command,
            stdout=b"".join(stdout_chunks).decode("utf-8", errors="replace"),
            stderr=b"".join(stderr_chunks).decode("utf-8", errors="replace"),
            stdout_bytes=stdout_bytes,
            stderr_bytes=stderr_bytes,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            return_code=return_code,
            duration_ms=elapsed_ms,
        )

    def send_prompt(
        self,
        sandbox_name: str,
        prompt: str,
        agent_type: str = "openclaw",
        *,
        on_chunk: OutputChunkHandler | None = None,
    ) -> SandboxResult:
        if agent_type not in _AGENT_CMD_TEMPLATES:
            supported = ", ".join(sorted(_AGENT_CMD_TEMPLATES))
            raise ValueError(
                f"Unsupported agent_type {agent_type!r}. Supported values: {supported}"
            )

        command = _AGENT_CMD_TEMPLATES[agent_type].format(prompt=shlex.quote(prompt))
        result = self.run_in_sandbox(sandbox_name, command, on_chunk=on_chunk)
        if not result.success:
            raise RuntimeError(
                f"Sandbox {sandbox_name!r} returned exit code {result.return_code}.\n"
                f"stderr: {result.stderr.strip()}"
            )
        return result

    def is_sandbox_healthy(self, sandbox_name: str) -> bool:
        try:
            result = self.run_in_sandbox(sandbox_name, "echo ok", timeout=10)
            return result.success and "ok" in result.stdout
        except (subprocess.TimeoutExpired, OSError):
            return False

    def list_sandboxes(self) -> list[str]:
        return sorted({agent.sandbox_name for agent in self.settings.agents.values()})
