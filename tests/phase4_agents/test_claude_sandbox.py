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

import re

import pytest
from fabric import Connection

from ..helpers import run_remote
from ..models import CommandResult
from ._openshell_cli import run_sandbox_command, strip_ansi

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
        """The claude-dev sandbox exists and reports Ready in OpenShell.

        Queries the supported OpenShell sandbox metadata command and asserts
        that the sandbox descriptor reports ``Phase: Ready``. A missing or
        non-ready sandbox would prevent Claude Code from operating.
        """
        result: CommandResult = run_remote(
            spark_ssh,
            f"openshell sandbox get {_SANDBOX_NAME} 2>&1",
            timeout=15,
        )
        assert result.return_code == 0, (
            f"'openshell sandbox get {_SANDBOX_NAME}' failed (exit {result.return_code}). "
            "The sandbox metadata may not exist or the gateway may be down. "
            f"Create it via: openshell sandbox create {_SANDBOX_NAME}. "
            f"stderr: {result.stderr!r}"
        )
        status = strip_ansi(result.stdout)
        assert re.search(r"^\s*Name:\s*claude-dev\s*$", status, re.MULTILINE), (
            f"Sandbox {_SANDBOX_NAME!r} descriptor does not include the expected name.\n"
            f"Full output:\n{status}"
        )
        assert re.search(r"^\s*Phase:\s*Ready\s*$", status, re.MULTILINE), (
            f"Sandbox {_SANDBOX_NAME!r} is not Ready in OpenShell.\nFull output:\n{status}"
        )

    def test_claude_binary_exists(self, spark_ssh: Connection) -> None:
        """The ``claude`` CLI binary is available on PATH inside the sandbox.

        Runs ``which claude`` inside the sandbox via the supported OpenShell
        sandbox SSH config path. The binary must be present for OpenShell to
        dispatch Claude Code agent tasks. A missing binary typically means
        the sandbox image was built without the Claude Code npm package, or
        the sandbox PATH is misconfigured.
        """
        result: CommandResult = run_sandbox_command(
            spark_ssh,
            _SANDBOX_NAME,
            "which claude",
            timeout=20,
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
            f"Inspect with: openshell sandbox ssh-config {_SANDBOX_NAME}"
        )

    def test_anthropic_policy_present(self, spark_ssh: Connection) -> None:
        """The Anthropic API egress policy allows outbound calls to api.anthropic.com.

        Reads the live OpenShell policy for the claude-dev sandbox and
        asserts that ``api.anthropic.com`` appears in the active network
        policy. Without this entry the sandbox's default-deny firewall would
        block every Claude API call, causing the agent to fail with a
        connection-refused error that looks like an authentication failure
        from the Claude SDK's perspective.
        """
        result: CommandResult = run_remote(
            spark_ssh,
            f"openshell policy get {_SANDBOX_NAME} --full 2>&1",
            timeout=20,
        )
        assert result.return_code == 0, (
            f"'openshell policy get {_SANDBOX_NAME} --full' failed "
            f"(exit {result.return_code}). "
            "Cannot verify egress policy without reading it. "
            f"stderr: {result.stderr!r}"
        )
        policy_output = strip_ansi(result.stdout)
        assert policy_output.strip(), (
            f"'openshell policy get {_SANDBOX_NAME} --full' produced no output."
        )
        assert _EXPECTED_POLICY_DOMAIN in policy_output, (
            f"Egress allow-list for {_SANDBOX_NAME!r} does not contain "
            f"'{_EXPECTED_POLICY_DOMAIN}'. "
            "Without this entry every Claude API call will be blocked at the "
            "sandbox firewall. "
            "Add it with: openshell policy set claude-dev --policy <policy.yaml>. "
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
        """OpenShell reports the claude-dev sandbox status as ready.

        Queries ``openshell sandbox get`` and asserts that the output shows
        ``Phase: Ready``. This catches misconfiguration at the OpenShell
        orchestration layer that Docker alone would not report, such as a
        missing policy attachment or an unresolved volume mount.
        """
        result: CommandResult = run_remote(
            spark_ssh,
            f"openshell sandbox get {_SANDBOX_NAME} 2>&1",
            timeout=20,
        )
        assert result.return_code == 0, (
            f"'openshell sandbox get {_SANDBOX_NAME}' exited with "
            f"code {result.return_code}. "
            "The sandbox may be misconfigured or the OpenShell daemon may be down. "
            f"stderr: {result.stderr!r}"
        )
        status_output = strip_ansi(result.stdout)
        assert re.search(r"^\s*Name:\s*claude-dev\s*$", status_output, re.MULTILINE), (
            f"sandbox descriptor for {_SANDBOX_NAME!r} does not include the "
            "expected name. "
            f"Full output:\n{status_output}"
        )
        assert re.search(r"^\s*Phase:\s*Ready\s*$", status_output, re.MULTILINE), (
            f"sandbox descriptor for {_SANDBOX_NAME!r} does not show Phase: Ready. "
            f"Full output:\n{status_output}"
        )
