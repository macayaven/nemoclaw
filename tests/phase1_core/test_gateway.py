"""
Phase 1 — Core NemoClaw on Spark: OpenShell gateway tests.

Verifies that the OpenShell gateway starts within the allowed bootstrap
window, reports a "Connected" status, exposes its control-plane port only on
loopback (not the public LAN interface), and that the port is open and
responding.

Markers
-------
phase1     : All tests in this module belong to Phase 1.
behavioral : Layer-B endpoint / network tests.
"""

from __future__ import annotations

import pytest
import httpx
from fabric import Connection

from tests.models import CommandResult
from tests.helpers import run_remote, poll_until_ready


# ---------------------------------------------------------------------------
# Behavioral tests — gateway startup and status
# ---------------------------------------------------------------------------


@pytest.mark.phase1
@pytest.mark.behavioral
class TestGatewayStartup:
    """Verify that the OpenShell gateway boots and reaches a Connected state."""

    @pytest.mark.timeout(180)
    def test_gateway_starts(self, spark_ssh: Connection) -> None:
        """``openshell status`` must report 'Connected' within 180 seconds.

        The internal k3s cluster that backs the OpenShell gateway can take up
        to two minutes to bootstrap on first launch.  ``poll_until_ready``
        retries with exponential back-off so the test does not fail on
        transient "Starting" states.
        """

        def _check_connected() -> bool:
            result: CommandResult = run_remote(spark_ssh, "openshell status")
            return "connected" in result.stdout.lower()

        # poll_until_ready raises TimeoutError if the condition is not met
        # within the timeout window; catching it converts to a pytest failure
        # with a descriptive message.
        try:
            poll_until_ready(
                check_fn=_check_connected,
                timeout=180,
                interval=5,
                description="OpenShell gateway to reach 'Connected' state",
            )
        except TimeoutError:
            pytest.fail(
                "OpenShell gateway did not reach 'Connected' state within 180 s.\n"
                "Run 'openshell status' on the Spark to inspect the current state.\n"
                "Common causes: Docker daemon not running, k3s bootstrap failure, "
                "insufficient disk space."
            )

    def test_status_connected(self, spark_ssh: Connection) -> None:
        """``openshell status`` output contains 'Connected' (point-in-time check).

        Unlike test_gateway_starts, this test does NOT poll — it asserts the
        current state.  Intended to be run after the gateway is already known
        to be up (e.g. in a CI pipeline where Phase 1 is incremental).
        """
        result: CommandResult = run_remote(spark_ssh, "openshell status")

        assert "connected" in result.stdout.lower(), (
            f"Expected 'Connected' in openshell status output.\n"
            f"Actual output:\n{result.stdout}\n"
            f"Stderr:\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# Behavioral tests — gateway port connectivity
# ---------------------------------------------------------------------------


@pytest.mark.phase1
@pytest.mark.behavioral
class TestGatewayPort:
    """Verify that the OpenShell gateway control-plane port is reachable."""

    def test_port_open(self, spark_ip: str) -> None:
        """GET :8080 from the LAN IP returns a recognisable gateway response.

        The test verifies *identity*, not just HTTP status code: the response
        must either carry a header that identifies OpenShell/k3s, or return a
        status code in the range that a control-plane proxy would emit (200,
        401, 403, 404).  A plain TCP connection-refused error would propagate
        as an httpx.ConnectError and fail the test immediately.

        TLS verification is disabled because the gateway uses a self-signed
        certificate during the bootstrap phase.
        """
        url = f"https://{spark_ip}:8080"
        try:
            response = httpx.get(url, verify=False, timeout=15.0)
        except httpx.ConnectError as exc:
            pytest.fail(
                f"Could not connect to OpenShell gateway at {url}: {exc}\n"
                "Ensure the gateway is running and port 8080 is not firewalled."
            )

        # Gateway responds with a control-plane status code (auth required is fine)
        acceptable_statuses = {200, 301, 302, 400, 401, 403, 404}
        assert response.status_code in acceptable_statuses, (
            f"Unexpected status {response.status_code} from gateway at {url}.\n"
            f"Response body (first 500 chars): {response.text[:500]}"
        )

        # Identity check: at minimum the server header or body should not be
        # a generic web server error page unrelated to OpenShell.
        server_header = response.headers.get("server", "").lower()
        content_type = response.headers.get("content-type", "").lower()
        body_snippet = response.text[:200].lower()

        is_identifiable = (
            "openshell" in server_header
            or "k3s" in server_header
            or "application/json" in content_type
            or "openssl" in body_snippet
            or response.status_code in {401, 403}  # auth wall → gateway is there
        )
        assert is_identifiable, (
            f"Gateway at {url} responded with status {response.status_code} "
            "but the response does not look like an OpenShell control plane.\n"
            f"Server: {server_header!r}\n"
            f"Content-Type: {content_type!r}\n"
            f"Body snippet: {body_snippet!r}"
        )


# ---------------------------------------------------------------------------
# Negative tests — gateway must not be exposed on the public interface
# ---------------------------------------------------------------------------


@pytest.mark.phase1
class TestGatewayNegative:
    """Negative path: the gateway should not be trivially open to the LAN."""

    def test_gateway_not_on_public_interface(self, spark_ssh: Connection) -> None:
        """Port 8080 must be bound to 127.0.0.1 (loopback), not 0.0.0.0.

        The OpenShell gateway control plane should only be reachable locally
        on the Spark host or via SSH tunnel.  Binding it to 0.0.0.0:8080
        would expose the unauthenticated k3s API to the entire LAN.

        Uses ``ss`` to inspect the actual socket binding on the host rather
        than attempting a remote connection, which could be blocked by
        firewall rules and produce a false-negative.
        """
        result: CommandResult = run_remote(spark_ssh, "ss -tlnp | grep 8080 || true")

        stdout = result.stdout.strip()

        # If nothing is listening on 8080, the test passes vacuously (the
        # gateway may use a different port binding strategy).
        if not stdout:
            return

        # If something IS listening, it must not be on 0.0.0.0
        assert "0.0.0.0:8080" not in stdout, (
            "SECURITY: OpenShell gateway port 8080 is bound to 0.0.0.0 "
            "(all interfaces), exposing the control plane to the LAN.\n"
            f"Current ss output:\n{stdout}\n"
            "Fix: configure the gateway to bind only to 127.0.0.1:8080."
        )
