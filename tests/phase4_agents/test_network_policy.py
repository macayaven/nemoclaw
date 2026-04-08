"""
Phase 4 — Coding Agent Sandboxes: Network policy enforcement tests.

Validates that sandboxes operate under a default-deny egress policy and that
network access is binary-scoped (i.e. only explicitly allowed binaries or
protocols can reach external endpoints).

Extends the single-sandbox egress test in ``test_sandbox_isolation.py`` to
cover all four sandboxes and adds checks for policy configuration via
``openshell sandbox policy show``.

Markers
-------
phase4     : All tests here belong to Phase 4 (Coding Agent Sandboxes).
behavioral : Layer B — runtime network enforcement tests.

Fixtures (from conftest.py)
---------------------------
spark_ssh : fabric.Connection — live SSH connection to the DGX Spark node.
"""

from __future__ import annotations

import contextlib
import warnings

import pytest
from fabric import Connection

from ..helpers import run_remote
from ..models import CommandResult

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

        Runs ``openshell sandbox policy show <name>`` and asserts that the
        output contains policy rules rather than being empty or reporting
        "no policy."  An empty policy means the sandbox has unrestricted
        egress, which violates the security model.
        """
        result: CommandResult = run_remote(
            spark_ssh,
            f"openshell sandbox policy show {sandbox_name} 2>&1",
            timeout=20,
        )
        output = result.stdout.strip()

        # "no policy" or empty output means no egress restrictions
        assert output and "no policy" not in output.lower(), (
            f"Sandbox {sandbox_name!r} has no network policy configured. "
            f"Output: {output!r}. "
            "Without a policy, the sandbox has unrestricted egress to the internet. "
            "Fix: apply a default-deny policy via "
            f"'openshell sandbox policy-add {sandbox_name} default-deny'."
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
        result: CommandResult = run_remote(
            spark_ssh,
            f"docker exec nemoclaw-main curl --max-time 3 -sf {_CLOUD_API_URL} 2>&1; echo EXIT:$?",
            timeout=20,
        )
        combined = result.stdout + " " + result.stderr

        exit_code: int | None = None
        for part in combined.split():
            if part.startswith("EXIT:"):
                with contextlib.suppress(ValueError):
                    exit_code = int(part.split(":", 1)[1])
                break

        if exit_code is not None:
            assert exit_code != 0, (
                f"nemoclaw-main was able to reach {_CLOUD_API_URL!r} (curl exit 0). "
                "The local sandbox should not have direct access to cloud APIs. "
                "Fix: ensure the default-deny egress policy blocks outbound HTTPS "
                "to cloud API endpoints from nemoclaw-main."
            )
        else:
            # Could not parse exit code — check for success indicators
            success_indicators = {"200", "301", "302", "<!doctype", "<html", "json"}
            assert not any(ind in combined.lower() for ind in success_indicators), (
                f"curl to {_CLOUD_API_URL!r} from nemoclaw-main appears to have "
                f"succeeded. Output: {combined[:300]!r}"
            )

    def test_default_deny_all_sandboxes(self, spark_ssh: Connection, sandbox_name: str) -> None:
        """Outbound HTTP to example.com is blocked from every sandbox.

        Extends the single-sandbox test in test_sandbox_isolation.py to all
        four sandboxes.  Under the default-deny policy, curl to example.com
        should time out or be refused (non-zero exit code).
        """
        result: CommandResult = run_remote(
            spark_ssh,
            f"docker exec {sandbox_name} "
            f"curl --silent --max-time 5 {_BLOCKED_EXTERNAL_URL} 2>&1"
            f"; echo EXIT:$?",
            timeout=20,
        )
        combined = result.stdout + " " + result.stderr

        exit_code: int | None = None
        for part in combined.split():
            if part.startswith("EXIT:"):
                with contextlib.suppress(ValueError):
                    exit_code = int(part.split(":", 1)[1])
                break

        if exit_code is not None:
            assert exit_code != 0, (
                f"curl to {_BLOCKED_EXTERNAL_URL!r} from {sandbox_name!r} "
                f"succeeded (exit 0). The default-deny egress policy is not "
                "enforced for this sandbox. "
                f"Fix: openshell sandbox policy-add {sandbox_name} default-deny"
            )
        else:
            success_indicators = {"200 ok", "301", "302", "<!doctype", "<html"}
            assert not any(ind in combined.lower() for ind in success_indicators), (
                f"curl to {_BLOCKED_EXTERNAL_URL!r} from {sandbox_name!r} "
                f"appears to have succeeded. Output: {combined[:300]!r}"
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
            f"openshell sandbox policy show {sandbox_name} 2>&1",
            timeout=20,
        )
        output = result.stdout.lower()

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
