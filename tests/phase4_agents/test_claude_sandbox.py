"""
Phase 4 — Coding Agent Sandboxes: Claude sandbox contract and health tests.

Validates that the ``claude-dev`` OpenShell sandbox is correctly provisioned:
the container exists and is running, the ``claude`` binary is on PATH inside
the sandbox, and the Anthropic network-egress policy is in place to gate API
calls.

Markers
-------
phase4    : All tests here belong to Phase 4 (Coding Agent Sandboxes).
contract  : Layer A — structure, schema, and configuration assertions.
behavioral: Layer B — runtime health and live connectivity assertions.

Fixtures (from conftest.py)
---------------------------
spark_ssh : fabric.Connection — live SSH connection to the DGX Spark node.
"""

from __future__ import annotations

import pytest
from fabric import Connection

from ..helpers import run_remote
from ..models import CommandResult

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SANDBOX_NAME: str = "claude-dev"
_EXPECTED_POLICY_DOMAIN: str = "api.anthropic.com"


# ---------------------------------------------------------------------------
# Contract tests — structure and configuration
# ---------------------------------------------------------------------------


@pytest.mark.phase4
@pytest.mark.contract
class TestClaudeSandbox:
    """Layer A: The claude-dev sandbox container exists and is correctly configured.

    These tests verify the static contract — presence of the container, the
    agent binary, and the network-egress allow-list — without executing any
    inference request.  They are fast, idempotent, and suitable for running in
    CI on every push.
    """

    def test_sandbox_exists(self, spark_ssh: Connection) -> None:
        """The claude-dev sandbox container exists and is in a running state.

        Queries Docker for the exact container name ``claude-dev`` and asserts
        that the status field contains ``running``.  A missing or exited
        container means the sandbox was never created, crashed on start, or was
        pruned — all of which would prevent Claude Code from operating.
        """
        result: CommandResult = run_remote(
            spark_ssh,
            f"docker inspect --format '{{{{.State.Status}}}}' {_SANDBOX_NAME} 2>&1",
            timeout=15,
        )
        assert result.return_code == 0, (
            f"docker inspect {_SANDBOX_NAME!r} failed (exit {result.return_code}). "
            "The sandbox container may not exist. "
            f"Create it via: openshell sandbox create {_SANDBOX_NAME}. "
            f"stderr: {result.stderr!r}"
        )
        status = result.stdout.strip().lower()
        assert status == "running", (
            f"Sandbox {_SANDBOX_NAME!r} exists but is not running (status={status!r}). "
            "Restart it: openshell sandbox start claude-dev, or check Docker logs: "
            f"docker logs {_SANDBOX_NAME} --tail=50"
        )

    def test_claude_binary_exists(self, spark_ssh: Connection) -> None:
        """The ``claude`` CLI binary is available on PATH inside the sandbox.

        Runs ``which claude`` inside the sandbox via ``docker exec``.  The
        binary must be present for OpenShell to dispatch Claude Code agent
        tasks.  A missing binary typically means the sandbox image was built
        without the Claude Code npm package, or the container's PATH is
        misconfigured.
        """
        result: CommandResult = run_remote(
            spark_ssh,
            f"docker exec {_SANDBOX_NAME} which claude 2>&1",
            timeout=15,
        )
        assert result.return_code == 0, (
            f"'which claude' inside sandbox {_SANDBOX_NAME!r} returned exit "
            f"code {result.return_code}. The claude binary is not on PATH. "
            "Ensure the sandbox image installs @anthropic-ai/claude-code via npm. "
            f"Output: {result.stdout!r}  stderr: {result.stderr!r}"
        )
        binary_path = result.stdout.strip()
        assert binary_path != "", (
            "'which claude' exited 0 but produced no output inside the sandbox. "
            "The binary may be a broken symlink. "
            f"Inspect with: docker exec {_SANDBOX_NAME} ls -la $(which claude)"
        )

    def test_anthropic_policy_present(self, spark_ssh: Connection) -> None:
        """The Anthropic API egress policy allows outbound calls to api.anthropic.com.

        Reads the OpenShell network-egress policy applied to the claude-dev
        sandbox and asserts that ``api.anthropic.com`` appears in the allow-
        list.  Without this entry the sandbox's default-deny firewall will
        block every Claude API call, causing the agent to fail with a
        connection-refused error that looks like an authentication failure from
        the Claude SDK's perspective.
        """
        result: CommandResult = run_remote(
            spark_ssh,
            f"openshell sandbox policy show {_SANDBOX_NAME} 2>&1",
            timeout=20,
        )
        assert result.return_code == 0, (
            f"'openshell sandbox policy show {_SANDBOX_NAME}' failed "
            f"(exit {result.return_code}). "
            "Cannot verify egress policy without reading it. "
            f"stderr: {result.stderr!r}"
        )
        policy_output = result.stdout
        assert _EXPECTED_POLICY_DOMAIN in policy_output, (
            f"Egress allow-list for {_SANDBOX_NAME!r} does not contain "
            f"'{_EXPECTED_POLICY_DOMAIN}'. "
            "Without this entry every Claude API call will be blocked at the "
            "sandbox firewall. "
            "Add it: openshell sandbox policy allow claude-dev api.anthropic.com:443. "
            f"Current policy output:\n{policy_output}"
        )


# ---------------------------------------------------------------------------
# Behavioral tests — runtime health
# ---------------------------------------------------------------------------


@pytest.mark.phase4
@pytest.mark.behavioral
class TestClaudeHealth:
    """Layer B: The claude-dev sandbox is healthy at runtime.

    These tests execute live commands against the running sandbox to verify
    it can accept requests.  They are slightly slower than contract tests but
    still complete well within the 60-second default timeout.
    """

    def test_sandbox_healthy(self, spark_ssh: Connection) -> None:
        """OpenShell reports the claude-dev sandbox status as healthy.

        Queries ``openshell sandbox status`` and asserts that the output
        contains a positive health indicator (``healthy``, ``running``, or
        ``ok``).  This catches misconfiguration at the OpenShell orchestration
        layer that Docker alone would not report, such as a missing policy
        attachment or an unresolved volume mount.
        """
        result: CommandResult = run_remote(
            spark_ssh,
            f"openshell sandbox status {_SANDBOX_NAME} 2>&1",
            timeout=20,
        )
        assert result.return_code == 0, (
            f"'openshell sandbox status {_SANDBOX_NAME}' exited with "
            f"code {result.return_code}. "
            "The sandbox may be misconfigured or the OpenShell daemon may be down. "
            f"stderr: {result.stderr!r}"
        )
        status_output = result.stdout.lower()
        healthy_indicators = {"healthy", "running", "ok"}
        assert any(indicator in status_output for indicator in healthy_indicators), (
            f"sandbox status for {_SANDBOX_NAME!r} does not contain a positive "
            f"health indicator (checked: {sorted(healthy_indicators)}). "
            f"Full status output:\n{result.stdout}"
        )
