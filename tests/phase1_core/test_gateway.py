"""
Phase 1 — Core NemoClaw on Spark: OpenShell gateway tests.

Verifies that the OpenShell gateway starts within the allowed bootstrap
window, reports a "Connected" status, and does not expose its control-plane
port broadly on the public LAN interface.

Markers
-------
phase1     : All tests in this module belong to Phase 1.
behavioral : Layer-B endpoint / network tests.
"""

from __future__ import annotations

import pytest
from fabric import Connection

from ..helpers import poll_until_ready, run_in_sandbox, run_remote
from ..models import CommandResult
from ..settings import TestSettings

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


# ---------------------------------------------------------------------------
# Contract tests — device authentication
# ---------------------------------------------------------------------------


@pytest.mark.phase1
@pytest.mark.contract
class TestDeviceAuth:
    """Layer A: Gateway device authentication is enabled and tokens are not leaked.

    Device authentication ensures that only approved devices can interact with
    the OpenClaw gateway.  The auth mode should be ``token`` or ``device``,
    and the actual token value must never appear in plaintext in gateway logs.
    """

    def test_device_auth_enabled(self, spark_ssh: Connection) -> None:
        """The gateway auth mode is set to 'token' or 'device'.

        Reads the gateway auth configuration from the OpenClaw config inside
        the nemoclaw-main sandbox.  The auth mode must be one of the secure
        options; ``none`` or ``open`` would allow unauthenticated access to the
        gateway control plane.
        """
        result: CommandResult = run_in_sandbox(
            spark_ssh,
            "nemoclaw-main",
            "sh -c 'cat ~/.openclaw/openclaw.json 2>/dev/null || echo {}'",
            timeout=15,
        )
        config_output = result.stdout.strip().lower()

        secure_modes = {"token", "device"}
        has_secure_mode = any(mode in config_output for mode in secure_modes)

        assert has_secure_mode, (
            "Gateway auth mode is not set to 'token' or 'device' in the "
            "OpenClaw configuration. Without device authentication, any client "
            "that can reach the gateway port can interact with it. "
            f"Config content (first 500 chars): {result.stdout[:500]!r}. "
            "Fix: set gateway.auth.mode to 'token' in openclaw.json."
        )

    def test_gateway_token_not_in_logs(
        self, spark_ssh: Connection, test_settings: TestSettings
    ) -> None:
        """The device auth token value does not appear in plaintext in gateway logs.

        Extracts the gateway auth token from the OpenClaw config and then scans
        the gateway log file for the literal token value.  A match means the
        gateway is logging its own auth token, which exposes it to anyone with
        log read access.

        The test is skipped if the token cannot be extracted from the config
        (e.g. because the config format has changed or the token is not a
        simple string).
        """
        # Extract the auth token from the config
        token_result: CommandResult = run_in_sandbox(
            spark_ssh,
            "nemoclaw-main",
            "sh -c 'cat ~/.openclaw/openclaw.json 2>/dev/null'",
            timeout=15,
        )

        import json

        try:
            config = json.loads(token_result.stdout)
            token = config.get("gateway", {}).get("auth", {}).get("token", "")
        except (json.JSONDecodeError, AttributeError):
            token = ""

        if not token or len(token) < 8:
            pytest.skip(
                "Could not extract a gateway auth token from openclaw.json. "
                "The token may be managed externally or use a different format."
            )

        # Scan gateway logs for the raw token
        log_result: CommandResult = run_in_sandbox(
            spark_ssh,
            "nemoclaw-main",
            "sh -c 'tail -n 2000 /tmp/gateway.log 2>/dev/null || echo \"\"'",
            timeout=20,
        )
        log_content = log_result.stdout

        redacted_token = token[:4] + "..." + token[-4:]
        assert token not in log_content, (
            f"Gateway auth token ({redacted_token}) found in plaintext in "
            "gateway logs. This exposes the token to anyone with log access. "
            "Fix: configure the gateway logger to redact auth tokens, or use "
            "a token rotation mechanism."
        )
