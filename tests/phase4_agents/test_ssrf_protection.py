"""
Phase 4 — Coding Agent Sandboxes: SSRF protection tests.

Validates that sandboxes cannot reach internal infrastructure endpoints that
should be unreachable from agent code:
- The cloud metadata service (169.254.169.254) used by AWS/GCP/Azure to
  expose instance credentials.
- The host Docker daemon socket (host.docker.internal:2375) which would
  allow container escape.

These are lightweight canary tests — they verify that the most dangerous
SSRF targets are blocked rather than testing every possible internal endpoint.

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

import pytest
from fabric import Connection

from ..models import CommandResult
from ._openshell_cli import run_sandbox_command

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ALL_SANDBOXES: list[str] = ["nemoclaw-main", "claude-dev", "codex-dev", "gemini-dev"]

# Cloud instance metadata endpoint (link-local, used by AWS/GCP/Azure)
_METADATA_URL: str = "http://169.254.169.254/latest/meta-data/"

# Docker daemon API on the host (if exposed)
_DOCKER_API_URL: str = "http://host.docker.internal:2375/"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_curl_blocked(
    spark_ssh: Connection,
    sandbox_name: str,
    url: str,
    description: str,
) -> None:
    """Assert that curl to *url* from inside *sandbox_name* fails.

    Runs curl with a 3-second timeout and checks for a non-zero exit code.
    A zero exit code (successful HTTP response) indicates the endpoint is
    reachable, which is the SSRF vulnerability we are testing for.

    Args:
        spark_ssh: SSH connection to the Spark host.
        sandbox_name: Name of the sandbox container to test.
        url: URL to attempt to reach from inside the sandbox.
        description: Human-readable name of the target for error messages.
    """
    result: CommandResult = run_sandbox_command(
        spark_ssh,
        sandbox_name,
        f"sh -c 'curl --max-time 3 -sf {url} 2>&1; echo EXIT:$?'",
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
            f"SSRF: {sandbox_name!r} was able to reach {description} at {url!r} "
            f"(curl exit 0). This endpoint must be blocked from inside sandboxes. "
            "Fix: add a network policy rule blocking access to link-local and "
            "internal Docker endpoints."
        )
    else:
        # Fallback: check for success indicators in the output
        success_indicators = {"200", "ami-id", "instance-id", "docker", "api version"}
        assert not any(ind in combined.lower() for ind in success_indicators), (
            f"SSRF: curl to {url!r} from {sandbox_name!r} appears to have "
            f"succeeded. Output: {combined[:300]!r}"
        )


# ---------------------------------------------------------------------------
# Behavioral tests — SSRF protection
# ---------------------------------------------------------------------------


@pytest.mark.phase4
@pytest.mark.behavioral
class TestSSRFProtection:
    """Layer B: Sandboxes cannot reach SSRF-sensitive internal endpoints.

    SSRF (Server-Side Request Forgery) attacks trick server-side code into
    making requests to internal endpoints.  In the NemoClaw context, an agent
    could be prompted to fetch a URL that resolves to an internal service,
    potentially accessing cloud credentials or the Docker API.

    These tests verify that the two most critical SSRF targets are blocked.
    """

    @pytest.fixture(params=_ALL_SANDBOXES)
    def sandbox_name(self, request: pytest.FixtureRequest) -> str:
        """Yield each sandbox name as a test parameter."""
        return request.param

    def test_sandbox_cannot_reach_internal_metadata(
        self, spark_ssh: Connection, sandbox_name: str
    ) -> None:
        """Sandboxes cannot access the cloud metadata service (169.254.169.254).

        The cloud metadata endpoint is a link-local address that exposes
        instance credentials, IAM role tokens, and other sensitive data on
        cloud VMs.  Even though the NemoClaw deployment may not be on a cloud
        VM, blocking this endpoint is a defence-in-depth measure that prevents
        credential theft if the deployment is later migrated to the cloud.
        """
        _assert_curl_blocked(
            spark_ssh,
            sandbox_name,
            _METADATA_URL,
            "cloud instance metadata service",
        )

    def test_sandbox_cannot_reach_host_docker(
        self, spark_ssh: Connection, sandbox_name: str
    ) -> None:
        """Sandboxes cannot access the host Docker API (port 2375).

        If the Docker daemon's HTTP API is exposed on port 2375 (either via
        TCP or via the ``host.docker.internal`` alias), a sandbox could use it
        to create privileged containers, mount the host filesystem, or stop
        other sandboxes — achieving full container escape.

        This test verifies that port 2375 is not reachable from inside
        sandboxes, regardless of whether the Docker API is actually listening
        on that port.
        """
        _assert_curl_blocked(
            spark_ssh,
            sandbox_name,
            _DOCKER_API_URL,
            "host Docker daemon API",
        )
