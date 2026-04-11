"""
Phase 2 — Mac Studio: Cross-machine inference security tests.

Validates the trust boundary between sandboxes on the Spark and the Mac
Studio's Ollama endpoint:
- Sandboxes should not be able to reach the Mac directly (bypassing the
  gateway).  All inference must flow through inference.local / the OpenShell
  gateway which applies provider routing, credential isolation, and audit
  logging.
- When the mac-ollama provider is active, inference.local should successfully
  route requests to the Mac.

Markers
-------
phase2     : All tests here belong to Phase 2 (Mac Studio).
behavioral : Layer B — runtime security and network enforcement tests.

Fixtures (from conftest.py)
---------------------------
spark_ssh : fabric.Connection — live SSH connection to the DGX Spark node.
test_settings: TestSettings — cluster topology and preferred hostnames/IPs.
"""

from __future__ import annotations

import contextlib

import pytest
from fabric import Connection

from ..helpers import run_in_sandbox
from ..models import CommandResult
from ..settings import TestSettings

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SANDBOX: str = "nemoclaw-main"

# Port used by the Mac Ollama TCP forwarder
_MAC_OLLAMA_PORT: int = 11435


# ---------------------------------------------------------------------------
# Behavioral tests — cross-machine inference
# ---------------------------------------------------------------------------


@pytest.mark.phase2
@pytest.mark.behavioral
class TestCrossMachineInference:
    """Layer B: Cross-machine inference respects trust boundaries.

    The Mac Studio's Ollama endpoint should only be reachable through the
    OpenShell gateway's provider routing.  Direct sandbox-to-Mac connections
    bypass credential isolation, audit logging, and provider access controls.
    """

    def test_mac_ollama_not_directly_reachable_from_sandbox(
        self, spark_ssh: Connection, test_settings: TestSettings
    ) -> None:
        """A sandbox cannot directly curl the Mac's Ollama port.

        The sandbox egress policy should block direct connections to the Mac's
        Ollama endpoint (port 11435).  All inference requests must flow through
        ``inference.local`` which the OpenShell gateway intercepts and routes
        to the configured provider.

        Direct access would bypass:
        - Provider-level credential isolation
        - Request audit logging
        - Binary-scoped network policies
        """
        mac_host = str(test_settings.mac.tailscale_ip or test_settings.mac.hostname)
        url = f"http://{mac_host}:{_MAC_OLLAMA_PORT}/api/tags"

        result: CommandResult = run_in_sandbox(
            spark_ssh,
            _SANDBOX,
            f"curl --max-time 5 -sf {url} 2>&1; echo EXIT:$?",
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
                f"Sandbox {_SANDBOX!r} was able to directly reach Mac Ollama at "
                f"{url!r} (curl exit 0). Direct access bypasses the gateway's "
                "credential routing and audit logging. "
                "Fix: ensure the sandbox egress policy blocks direct connections "
                f"to {mac_host}:{_MAC_OLLAMA_PORT}. Inference should flow through "
                "inference.local only."
            )
        else:
            # Fallback check
            success_indicators = {"models", "name", "200"}
            assert not any(ind in combined.lower() for ind in success_indicators), (
                f"Direct curl to Mac Ollama from {_SANDBOX!r} appears to have "
                f"succeeded. Output: {combined[:300]!r}"
            )

    def test_inference_local_routes_to_mac_when_active(self, spark_ssh: Connection) -> None:
        """inference.local resolves and is routable from inside the sandbox.

        This test verifies that the inference.local endpoint (which the
        OpenShell gateway intercepts) is reachable from inside the sandbox.
        It does NOT verify which backend the gateway routes to — that depends
        on the current ``openshell inference set`` configuration.

        A successful DNS resolution and TCP connection to inference.local
        confirms the gateway's request interception is functional.
        """
        result: CommandResult = run_in_sandbox(
            spark_ssh,
            _SANDBOX,
            "curl --max-time 5 -sf https://inference.local/v1/models 2>&1; echo EXIT:$?",
            timeout=20,
        )
        combined = result.stdout + " " + result.stderr

        exit_code: int | None = None
        for part in combined.split():
            if part.startswith("EXIT:"):
                with contextlib.suppress(ValueError):
                    exit_code = int(part.split(":", 1)[1])
                break

        # We expect either a successful response (exit 0) or a TLS/auth error
        # (exit 60, 35, etc.) — both indicate the gateway is intercepting.
        # A DNS resolution failure (exit 6) or connection refused (exit 7)
        # means inference.local is not routed.
        dns_or_connect_failures = {6, 7}
        if exit_code is not None and exit_code in dns_or_connect_failures:
            pytest.fail(
                f"inference.local is not reachable from {_SANDBOX!r} "
                f"(curl exit {exit_code}). The OpenShell gateway may not be "
                "intercepting requests to inference.local. "
                "Check: openshell status, and verify the sandbox's DNS "
                "configuration includes inference.local. "
                f"Output: {combined[:300]!r}"
            )
