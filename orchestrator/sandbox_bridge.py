r"""Bridge that executes commands inside OpenShell sandbox containers.

The bridge constructs ``openshell sandbox connect <name> -- <cmd>`` shell
invocations and returns structured results. All I/O crosses the sandbox
boundary through subprocess pipes; no shared filesystem is assumed.

Example::

    from orchestrator.config import OrchestratorSettings
    from orchestrator.sandbox_bridge import SandboxBridge

    bridge = SandboxBridge(OrchestratorSettings())
    result = bridge.run_in_sandbox("codex-dev", "echo hello")
    print(result.stdout)  # "hello\\n"
"""

from __future__ import annotations

import shlex
import subprocess
import time
from typing import TYPE_CHECKING

from pydantic import BaseModel, computed_field

if TYPE_CHECKING:
    from orchestrator.config import OrchestratorSettings


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


class SandboxResult(BaseModel):
    """Structured result returned by a sandbox command execution.

    Attributes:
        sandbox_name: The OpenShell sandbox that handled the command.
        stdout: Captured standard output from the command.
        stderr: Captured standard error from the command.
        return_code: Process exit code; 0 indicates success.
        duration_ms: Wall-clock execution time in milliseconds.
    """

    sandbox_name: str
    stdout: str
    stderr: str
    return_code: int
    duration_ms: float

    @computed_field  # type: ignore[misc]
    @property
    def success(self) -> bool:
        """True when the command exited with return code 0."""
        return self.return_code == 0


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------


# Maps logical agent type strings to the CLI invocation template used when
# calling send_prompt.  The placeholder ``{prompt}`` is replaced with the
# properly shell-quoted prompt text.
_AGENT_CMD_TEMPLATES: dict[str, str] = {
    "openclaw": "openclaw agent --agent main --local -m {prompt} --session-id orchestrator",
    "claude": "claude -p {prompt} --output-format text",
    "codex": "source ~/.bashrc && cd /sandbox && codex exec {prompt}",
    "gemini": "gemini -p {prompt}",
}


class SandboxBridge:
    """Sends commands into OpenShell sandboxes from the DGX Spark host.

    This class wraps ``openshell sandbox connect <name> -- <cmd>`` and
    provides higher-level helpers for prompting agents and checking
    sandbox health.

    Attributes:
        settings: Orchestrator-wide configuration object.
    """

    def __init__(self, settings: OrchestratorSettings) -> None:
        """Initialise the bridge with the given orchestrator settings.

        Args:
            settings: Fully-populated OrchestratorSettings instance.
        """
        self.settings = settings

    # ------------------------------------------------------------------
    # Core execution
    # ------------------------------------------------------------------

    def run_in_sandbox(
        self,
        sandbox_name: str,
        command: str,
        timeout: int | None = None,
    ) -> SandboxResult:
        """Execute *command* inside the named sandbox and return the result.

        The command string is passed verbatim as the ``--`` argument to
        ``openshell sandbox connect``, which runs it inside the container
        via the default shell.

        Args:
            sandbox_name: OpenShell sandbox identifier.
            command: Shell command string to run inside the container.
            timeout: Seconds to wait before raising TimeoutExpired.
                Defaults to ``settings.sandbox_timeout``.

        Returns:
            SandboxResult populated with stdout, stderr, return code, and
            elapsed time.

        Raises:
            subprocess.TimeoutExpired: If the command exceeds *timeout*.
        """
        if not command.strip():
            raise ValueError("command must not be empty")

        effective_timeout = timeout if timeout is not None else self.settings.sandbox_timeout

        # openshell sandbox connect doesn't accept trailing commands.
        # Use SSH with the openshell ssh-proxy as ProxyCommand instead.
        outer_cmd: list[str] = [
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "LogLevel=ERROR",
            "-o",
            f"ProxyCommand=openshell ssh-proxy --gateway-name openshell --name {sandbox_name}",
            f"sandbox@openshell-{sandbox_name}",
            command,
        ]

        start_ns = time.monotonic_ns()
        try:
            proc = subprocess.run(
                outer_cmd,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            elapsed_ms = (time.monotonic_ns() - start_ns) / 1_000_000
            # Re-raise with context so callers can inspect the partial output.
            raise subprocess.TimeoutExpired(
                cmd=outer_cmd,
                timeout=effective_timeout,
                output=exc.output,
                stderr=exc.stderr,
            ) from exc

        elapsed_ms = (time.monotonic_ns() - start_ns) / 1_000_000

        return SandboxResult(
            sandbox_name=sandbox_name,
            stdout=proc.stdout,
            stderr=proc.stderr,
            return_code=proc.returncode,
            duration_ms=elapsed_ms,
        )

    # ------------------------------------------------------------------
    # Higher-level helpers
    # ------------------------------------------------------------------

    def send_prompt(
        self,
        sandbox_name: str,
        prompt: str,
        agent_type: str = "openclaw",
    ) -> str:
        """Send a natural-language prompt to the agent running in *sandbox_name*.

        Selects the correct CLI invocation for the given *agent_type* and
        returns the agent's textual response (stdout).

        Args:
            sandbox_name: OpenShell sandbox identifier.
            prompt: Natural-language prompt to send to the agent.
            agent_type: One of ``"openclaw"``, ``"claude"``, ``"codex"``,
                ``"gemini"``.  Determines which CLI binary is used inside
                the sandbox.

        Returns:
            The agent's response as a stripped string.

        Raises:
            ValueError: If *agent_type* is not recognised.
            RuntimeError: If the sandbox command exits with a non-zero code.
            subprocess.TimeoutExpired: If the agent takes too long.
        """
        if agent_type not in _AGENT_CMD_TEMPLATES:
            supported = ", ".join(sorted(_AGENT_CMD_TEMPLATES))
            raise ValueError(
                f"Unsupported agent_type {agent_type!r}. Supported values: {supported}"
            )

        template = _AGENT_CMD_TEMPLATES[agent_type]
        # Shell-quote the prompt so multi-word / special-character prompts
        # are passed as a single argument inside the container.
        quoted_prompt = shlex.quote(prompt)
        command = template.format(prompt=quoted_prompt)

        result = self.run_in_sandbox(sandbox_name, command)

        if not result.success:
            raise RuntimeError(
                f"Sandbox {sandbox_name!r} returned exit code {result.return_code}.\n"
                f"stderr: {result.stderr.strip()}"
            )

        return result.stdout.strip()

    def is_sandbox_healthy(self, sandbox_name: str) -> bool:
        """Check whether the sandbox is reachable and responsive.

        Runs a trivial ``echo ok`` inside the sandbox to verify that
        ``openshell sandbox connect`` can reach the container.

        Args:
            sandbox_name: OpenShell sandbox identifier.

        Returns:
            True if the sandbox responds to ``echo ok`` within the timeout,
            False otherwise.
        """
        try:
            result = self.run_in_sandbox(sandbox_name, "echo ok", timeout=10)
            return result.success and "ok" in result.stdout
        except (subprocess.TimeoutExpired, OSError):
            return False

    def list_sandboxes(self) -> list[str]:
        """Return the list of known sandbox names from the current settings.

        The list is derived from ``settings.agents`` and reflects configured
        sandboxes, not runtime state.  Use :meth:`is_sandbox_healthy` to
        verify individual sandbox availability.

        Returns:
            Sorted list of sandbox name strings.
        """
        return sorted({agent.sandbox_name for agent in self.settings.agents.values()})
