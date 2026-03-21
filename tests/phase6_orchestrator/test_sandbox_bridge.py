"""
test_sandbox_bridge.py — Phase 6 tests for orchestrator.sandbox_bridge.

Covers SandboxBridge contract (initialisation, sandbox listing, health),
behavioural execution (running commands, timeout enforcement, prompt sending),
and negative / error-path cases.

All tests are marked @pytest.mark.phase6.  Tests that open real SSH connections
to the DGX Spark use the session-scoped ``spark_ssh`` fixture from conftest.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.phase6

# ---------------------------------------------------------------------------
# Contract tests — SandboxBridge initialisation and introspection
# ---------------------------------------------------------------------------


class TestSandboxBridge:
    """Contract tests: SandboxBridge can be created and reports sandbox state."""

    def test_bridge_initializes(self) -> None:
        """SandboxBridge instantiates with default OrchestratorSettings.

        Verifies that the bridge can be constructed without any arguments and
        that it exposes the expected attributes derived from OrchestratorSettings.
        """
        from orchestrator.config import OrchestratorSettings
        from orchestrator.sandbox_bridge import SandboxBridge

        settings = OrchestratorSettings()
        bridge = SandboxBridge(settings=settings)

        assert bridge is not None
        assert bridge.settings is settings
        assert bridge.settings.sandbox_timeout > 0

    def test_list_sandboxes(self, spark_ssh) -> None:
        """list_sandboxes() returns a non-empty collection containing nemoclaw-main.

        Uses the openshell CLI (or equivalent) on the DGX Spark to enumerate
        running sandboxes and verifies the primary sandbox is present.
        """
        from orchestrator.config import OrchestratorSettings
        from orchestrator.sandbox_bridge import SandboxBridge

        settings = OrchestratorSettings()
        bridge = SandboxBridge(settings=settings, conn=spark_ssh)

        sandboxes = bridge.list_sandboxes()

        assert isinstance(sandboxes, list), "list_sandboxes() must return a list"
        assert len(sandboxes) > 0, "Expected at least one sandbox to be listed"
        sandbox_names = [s if isinstance(s, str) else s.get("name", str(s)) for s in sandboxes]
        assert any("nemoclaw-main" in name for name in sandbox_names), (
            f"Expected 'nemoclaw-main' in sandbox list, got: {sandbox_names}"
        )

    def test_sandbox_health_check(self, spark_ssh) -> None:
        """health_check('nemoclaw-main') reports the sandbox as healthy.

        A healthy sandbox is one that responds to a trivial command within the
        configured timeout and returns exit code 0.
        """
        from orchestrator.config import OrchestratorSettings
        from orchestrator.sandbox_bridge import SandboxBridge

        settings = OrchestratorSettings()
        bridge = SandboxBridge(settings=settings, conn=spark_ssh)

        healthy = bridge.health_check("nemoclaw-main")

        assert healthy is True, "nemoclaw-main sandbox should report as healthy"


# ---------------------------------------------------------------------------
# Behavioural tests — command execution inside sandboxes
# ---------------------------------------------------------------------------


class TestSandboxExecution:
    """Behavioural tests: SandboxBridge executes commands and prompts correctly."""

    def test_run_command_in_sandbox(self, spark_ssh) -> None:
        """run_command() executes 'echo hello' in nemoclaw-main and captures stdout.

        Verifies that the SandboxResult contains 'hello' in its output and
        that the exit code is 0.
        """
        from orchestrator.config import OrchestratorSettings
        from orchestrator.sandbox_bridge import SandboxBridge, SandboxResult

        settings = OrchestratorSettings()
        bridge = SandboxBridge(settings=settings, conn=spark_ssh)

        result = bridge.run_command(sandbox_name="nemoclaw-main", command="echo hello")

        assert isinstance(result, SandboxResult), (
            f"run_command() must return a SandboxResult, got {type(result)}"
        )
        assert result.exit_code == 0, (
            f"'echo hello' should exit 0, got {result.exit_code}. stderr={result.stderr!r}"
        )
        assert "hello" in result.stdout, f"Expected 'hello' in stdout, got {result.stdout!r}"

    def test_run_command_timeout(self, spark_ssh) -> None:
        """run_command() raises TimeoutError when a slow command exceeds the timeout.

        Sends 'sleep 60' with a 2-second timeout to guarantee the timeout path
        is exercised without waiting for the full sleep duration.
        """
        from orchestrator.config import OrchestratorSettings
        from orchestrator.sandbox_bridge import SandboxBridge

        settings = OrchestratorSettings()
        bridge = SandboxBridge(settings=settings, conn=spark_ssh)

        with pytest.raises(TimeoutError):
            bridge.run_command(
                sandbox_name="nemoclaw-main",
                command="sleep 60",
                timeout=2,
            )

    @pytest.mark.slow
    @pytest.mark.timeout(180)
    def test_send_prompt_to_openclaw(self, spark_ssh) -> None:
        """send_prompt() sends a simple prompt to the openclaw agent and gets a response.

        Verifies that the response is a non-empty string.  The actual content
        is not asserted on since LLM output is non-deterministic.
        """
        from orchestrator.config import OrchestratorSettings
        from orchestrator.sandbox_bridge import SandboxBridge

        settings = OrchestratorSettings()
        bridge = SandboxBridge(settings=settings, conn=spark_ssh)

        response = bridge.send_prompt(
            sandbox_name="nemoclaw-main",
            prompt="Reply with a single word: ready",
        )

        assert isinstance(response, str), f"send_prompt() must return a str, got {type(response)}"
        assert len(response.strip()) > 0, "send_prompt() returned an empty response"


# ---------------------------------------------------------------------------
# Negative / error-path tests
# ---------------------------------------------------------------------------


class TestSandboxBridgeNegative:
    """Negative tests: SandboxBridge raises appropriate errors for invalid inputs."""

    def test_nonexistent_sandbox_fails(self, spark_ssh) -> None:
        """run_command() raises an error when the target sandbox does not exist.

        The exact exception type is implementation-defined, but it must not
        silently return a successful SandboxResult.
        """
        from orchestrator.config import OrchestratorSettings
        from orchestrator.sandbox_bridge import SandboxBridge

        settings = OrchestratorSettings()
        bridge = SandboxBridge(settings=settings, conn=spark_ssh)

        with pytest.raises(Exception) as exc_info:
            bridge.run_command(
                sandbox_name="nonexistent-sandbox",
                command="echo this-should-never-run",
            )

        # Accept any exception; just verify something was raised.
        assert exc_info.value is not None, (
            "Expected an exception for a nonexistent sandbox, but none was raised"
        )

    def test_empty_command_fails(self, spark_ssh) -> None:
        """run_command() raises ValueError when given an empty command string.

        An empty command is logically invalid and should be rejected before
        any network call is made.
        """
        from orchestrator.config import OrchestratorSettings
        from orchestrator.sandbox_bridge import SandboxBridge

        settings = OrchestratorSettings()
        bridge = SandboxBridge(settings=settings, conn=spark_ssh)

        with pytest.raises(ValueError, match="command"):
            bridge.run_command(sandbox_name="nemoclaw-main", command="")
