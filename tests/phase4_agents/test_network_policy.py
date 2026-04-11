"""
Phase 4 — Coding Agent Sandboxes: Network policy enforcement tests.

Validates that sandboxes operate under a default-deny egress policy and that
network access is binary-scoped (i.e. only explicitly allowed binaries or
protocols can reach external endpoints).

Extends the single-sandbox egress test in ``test_sandbox_isolation.py`` to
cover all four sandboxes and adds checks for policy configuration via
``openshell policy get <sandbox> --full``.

Markers
-------
phase4     : All tests here belong to Phase 4 (Coding Agent Sandboxes).
behavioral : Layer B — runtime network enforcement tests.

Fixtures (from conftest.py)
---------------------------
spark_ssh : fabric.Connection — live SSH connection to the DGX Spark node.
"""

from __future__ import annotations

import warnings

import pytest
from fabric import Connection

from ..helpers import curl_attempt_was_blocked, run_remote
from ..models import CommandResult
from ._openshell_cli import run_sandbox_command, strip_ansi

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ALL_SANDBOXES: list[str] = ["nemoclaw-main", "claude-dev", "codex-dev", "gemini-dev"]

# External URL used to verify default-deny egress.  example.com is an IANA
# reserved domain that reliably responds when reachable.
_BLOCKED_EXTERNAL_URL: str = "http://example.com"

# Cloud API endpoint that the local-only nemoclaw-main sandbox must not reach.
_CLOUD_API_URL: str = "https://api.anthropic.com"


# ---------------------------------------------------------------------------
# Behavioral tests — network policy
# ---------------------------------------------------------------------------


@pytest.mark.phase4
@pytest.mark.behavioral
class TestNetworkPolicy:
    """Layer B: Network policies enforce default-deny and binary-scoped rules.

    The NemoClaw security model requires that sandboxes cannot make arbitrary
    outbound connections.  Each sandbox's egress is governed by an OpenShell
    network policy that whitelists specific destinations (e.g. inference.local)
    and blocks everything else.
    """

    @pytest.fixture(params=_ALL_SANDBOXES)
    def sandbox_name(self, request: pytest.FixtureRequest) -> str:
        """Yield each sandbox name as a test parameter."""
        return request.param

    def test_sandbox_has_egress_policy(self, spark_ssh: Connection, sandbox_name: str) -> None:
        """Each sandbox has a non-empty network policy configured in OpenShell.

        Runs ``openshell policy get <name> --full`` and asserts that the
        output contains policy rules rather than being empty. An empty policy
        means the sandbox has unrestricted egress, which violates the
        security model.
        """
        result: CommandResult = run_remote(
            spark_ssh,
            f"openshell policy get {sandbox_name} --full 2>&1",
            timeout=20,
        )
        assert result.return_code == 0, (
            f"'openshell policy get {sandbox_name} --full' failed "
            f"(exit {result.return_code}). "
            "Cannot verify egress policy without reading it. "
            f"stderr: {result.stderr!r}"
        )
        output = strip_ansi(result.stdout).strip()

        assert output, (
            f"Sandbox {sandbox_name!r} has no network policy configured. "
            "Without a policy, the sandbox has unrestricted egress to the internet. "
            f"Current output: {output!r}"
        )
        assert "network_policies:" in output, (
            f"Sandbox {sandbox_name!r} policy output does not include a network_policies section. "
            f"Output: {output!r}"
        )

    def test_nemoclaw_main_no_cloud_api_egress(self, spark_ssh: Connection) -> None:
        """The nemoclaw-main sandbox cannot reach cloud API endpoints directly.

        nemoclaw-main runs against the local Ollama inference backend.  It
        should not be able to reach external cloud APIs like api.anthropic.com
        because:
        1. It has no cloud provider credentials.
        2. Direct cloud access from the main sandbox bypasses the gateway's
           credential routing.

        A successful curl (exit 0) is the failure mode.
        """
        result: CommandResult = run_sandbox_command(
            spark_ssh,
            "nemoclaw-main",
            (
                "sh -c '"
                "tmp=$(mktemp); "
                f'status=$(curl --silent --output "$tmp" --write-out "%{{http_code}}" '
                f"--max-time 3 {_CLOUD_API_URL} 2>/dev/null); "
                "rc=$?; "
                'printf "CURL_EXIT:%s HTTP_STATUS:%s\\n" "$rc" "$status"; '
                'cat "$tmp"; rm -f "$tmp"\''
            ),
            timeout=20,
        )
        combined = result.stdout + " " + result.stderr

        assert curl_attempt_was_blocked(combined), (
            f"nemoclaw-main was able to reach {_CLOUD_API_URL!r} without the "
            "request being denied by the sandbox egress path. "
            "The local sandbox should not have direct access to cloud APIs. "
            f"Observed output: {combined[:300]!r}"
        )

    def test_default_deny_all_sandboxes(self, spark_ssh: Connection, sandbox_name: str) -> None:
        """Outbound HTTP to example.com is blocked from every sandbox.

        Extends the single-sandbox test in test_sandbox_isolation.py to all
        four sandboxes.  Under the default-deny policy, curl to example.com
        should time out or be refused (non-zero exit code).
        """
        result: CommandResult = run_sandbox_command(
            spark_ssh,
            sandbox_name,
            (
                "sh -c '"
                "tmp=$(mktemp); "
                f'status=$(curl --silent --output "$tmp" --write-out "%{{http_code}}" '
                f"--max-time 5 {_BLOCKED_EXTERNAL_URL} 2>/dev/null); "
                "rc=$?; "
                'printf "CURL_EXIT:%s HTTP_STATUS:%s\\n" "$rc" "$status"; '
                'cat "$tmp"; rm -f "$tmp"\''
            ),
            timeout=20,
        )
        combined = result.stdout + " " + result.stderr

        assert curl_attempt_was_blocked(combined), (
            f"curl to {_BLOCKED_EXTERNAL_URL!r} from {sandbox_name!r} was not "
            "blocked by the default-deny egress policy. "
            f"Observed output: {combined[:300]!r}"
        )

    def test_policy_output_contains_binary_scope_or_l7(
        self, spark_ssh: Connection, sandbox_name: str
    ) -> None:
        """Policy rules include binary-scoped or L7 protocol entries (advisory).

        Binary-scoped rules restrict network access to specific executables
        (e.g. only ``node`` can reach inference.local).  L7 rules restrict by
        protocol (e.g. ``protocol: rest``).  These provide finer-grained
        control than IP-level firewall rules alone.

        This test is informational: if binary-scoped or L7 rules are absent,
        it emits a warning rather than failing, since this depends on the
        OpenShell version and policy format.
        """
        result: CommandResult = run_remote(
            spark_ssh,
            f"openshell policy get {sandbox_name} --full 2>&1",
            timeout=20,
        )
        assert result.return_code == 0, (
            f"'openshell policy get {sandbox_name} --full' failed "
            f"(exit {result.return_code}). "
            "Cannot inspect policy output. "
            f"stderr: {result.stderr!r}"
        )
        output = strip_ansi(result.stdout).lower()

        binary_scope_indicators = {"binary", "program", "executable", "process"}
        l7_indicators = {"protocol", "rest", "grpc", "http", "l7"}

        has_binary_scope = any(ind in output for ind in binary_scope_indicators)
        has_l7 = any(ind in output for ind in l7_indicators)

        if not has_binary_scope and not has_l7:
            warnings.warn(
                f"Sandbox {sandbox_name!r} policy does not appear to contain "
                "binary-scoped or L7 protocol rules. The policy may be IP-level "
                "only, which provides coarser access control. "
                "Consider upgrading to binary-scoped policies if your OpenShell "
                "version supports them.",
                stacklevel=1,
            )
