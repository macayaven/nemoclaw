"""
Phase 1 — Core NemoClaw on Spark: idempotency and resilience tests.

Validates that the NemoClaw stack survives a gateway restart with zero manual
intervention:
- The ``local-ollama`` provider registration and the Nemotron inference route
  are preserved after ``openshell gateway stop && openshell gateway start``.
- The ``nemoclaw-main`` sandbox (created with ``--keep``) still exists after
  the gateway restarts.

These tests are the acceptance gate for Phase 1 resilience: if a DGX Spark
reboots or the gateway process crashes, the system must come back up in a
fully-configured state without re-running the setup commands.

Markers
-------
phase1     : All tests in this module belong to Phase 1.
behavioral : Layer-B resilience / end-to-end tests.
slow       : Gateway restart includes k3s bootstrap time (~60–120 s).
"""

from __future__ import annotations

import time

import pytest
from fabric import Connection

from ..models import CommandResult
from ..helpers import run_remote, poll_until_ready


# ---------------------------------------------------------------------------
# Helpers — private to this module
# ---------------------------------------------------------------------------


def _gateway_stop(ssh: Connection) -> None:
    """Issue ``openshell gateway stop`` and wait for the process to exit."""
    run_remote(ssh, "openshell gateway stop")
    # Brief pause to allow the gateway to flush state before we restart it
    time.sleep(3)


def _gateway_start_and_wait(ssh: Connection, timeout: int = 180) -> None:
    """Issue ``openshell gateway start`` and poll until Connected.

    Args:
        ssh:     Fabric SSH connection to the Spark host.
        timeout: Maximum seconds to wait for the gateway to become ready.

    Raises:
        AssertionError: If the gateway does not reach 'Connected' within
                        *timeout* seconds.
    """
    run_remote(ssh, "openshell gateway start")

    def _is_connected() -> bool:
        result: CommandResult = run_remote(ssh, "openshell status")
        return "connected" in result.stdout.lower()

    # poll_until_ready raises TimeoutError when the condition is not satisfied
    # within the timeout budget.
    try:
        poll_until_ready(
            check_fn=_is_connected,
            timeout=timeout,
            interval=5,
            description="OpenShell gateway to reconnect after restart",
        )
    except TimeoutError as exc:
        raise AssertionError(
            f"OpenShell gateway did not reach 'Connected' state within {timeout} s "
            "after restart.\n"
            "Run 'openshell status' on the Spark to inspect the current state.\n"
            "Check Docker is running: systemctl status docker"
        ) from exc


# ---------------------------------------------------------------------------
# Behavioral idempotency tests
# ---------------------------------------------------------------------------


@pytest.mark.phase1
@pytest.mark.behavioral
class TestIdempotency:
    """Verify that provider config and sandbox state survive a gateway restart."""

    @pytest.mark.slow
    @pytest.mark.timeout(420)
    def test_gateway_restart_preserves_state(self, spark_ssh: Connection) -> None:
        """Stop and start the gateway; verify provider and inference route survive.

        Sequence
        --------
        1. Record the current provider list and inference route (pre-restart).
        2. Stop the gateway (``openshell gateway stop``).
        3. Start the gateway and poll until Connected (up to 180 s).
        4. Re-read the provider list and inference route (post-restart).
        5. Assert that ``local-ollama`` is still registered.
        6. Assert that the inference route still points to Nemotron + local-ollama.

        This covers UC-1.4 and UC-1.5 resilience: configuration must be
        persisted to disk and reloaded, not stored in memory only.

        Timeout: 420 s = 60 s pre/post checks + 300 s gateway bootstrap budget.
        """
        # ------------------------------------------------------------------ #
        # Pre-restart: capture the expected state                              #
        # ------------------------------------------------------------------ #
        pre_provider: CommandResult = run_remote(
            spark_ssh, "openshell provider list"
        )
        pre_inference: CommandResult = run_remote(
            spark_ssh, "openshell inference get"
        )

        assert "local-ollama" in pre_provider.stdout, (
            "Pre-restart: provider 'local-ollama' is not registered — "
            "run the Phase 1 setup before this idempotency test."
        )
        assert "nemotron" in pre_inference.stdout.lower(), (
            "Pre-restart: inference route does not reference Nemotron — "
            "run the Phase 1 setup before this idempotency test."
        )
        assert "local-ollama" in pre_inference.stdout, (
            "Pre-restart: inference route does not reference local-ollama — "
            "run the Phase 1 setup before this idempotency test."
        )

        # ------------------------------------------------------------------ #
        # Restart the gateway                                                  #
        # ------------------------------------------------------------------ #
        _gateway_stop(spark_ssh)
        _gateway_start_and_wait(spark_ssh, timeout=300)

        # ------------------------------------------------------------------ #
        # Post-restart: assert state is preserved                              #
        # ------------------------------------------------------------------ #
        post_provider: CommandResult = run_remote(
            spark_ssh, "openshell provider list"
        )
        post_inference: CommandResult = run_remote(
            spark_ssh, "openshell inference get"
        )

        assert "local-ollama" in post_provider.stdout, (
            "IDEMPOTENCY FAILURE: provider 'local-ollama' is missing after gateway restart.\n"
            f"Post-restart provider list:\n{post_provider.stdout}\n"
            "The provider registration is not being persisted to disk. "
            "Check the OpenShell data volume / config directory."
        )

        assert "nemotron" in post_inference.stdout.lower(), (
            "IDEMPOTENCY FAILURE: inference route lost the Nemotron model after restart.\n"
            f"Post-restart inference config:\n{post_inference.stdout}"
        )

        assert "local-ollama" in post_inference.stdout, (
            "IDEMPOTENCY FAILURE: inference route lost the 'local-ollama' provider "
            "after restart.\n"
            f"Post-restart inference config:\n{post_inference.stdout}"
        )

    @pytest.mark.slow
    @pytest.mark.timeout(420)
    def test_sandbox_survives_gateway_restart(self, spark_ssh: Connection) -> None:
        """nemoclaw-main must still exist in the sandbox list after a gateway restart.

        The ``--keep`` flag passed during sandbox creation instructs OpenShell
        not to garbage-collect the sandbox when it is idle.  This test verifies
        that ``--keep`` is honoured across the stop/start boundary: the sandbox
        must appear in ``openshell sandbox list`` both before and after the
        gateway is restarted.

        Sequence
        --------
        1. Verify ``nemoclaw-main`` exists before the restart (pre-condition).
        2. Stop the gateway.
        3. Start the gateway and poll until Connected.
        4. Assert ``nemoclaw-main`` still exists post-restart.

        Timeout: 420 s (same budget as test_gateway_restart_preserves_state).
        """
        # ------------------------------------------------------------------ #
        # Pre-restart: confirm the sandbox exists                              #
        # ------------------------------------------------------------------ #
        pre_list: CommandResult = run_remote(
            spark_ssh, "openshell sandbox list"
        )

        assert "nemoclaw-main" in pre_list.stdout, (
            "Pre-restart: sandbox 'nemoclaw-main' is not in the sandbox list — "
            "run the Phase 1 setup before this idempotency test.\n"
            f"Current sandbox list:\n{pre_list.stdout}"
        )

        # ------------------------------------------------------------------ #
        # Restart the gateway                                                  #
        # ------------------------------------------------------------------ #
        _gateway_stop(spark_ssh)
        _gateway_start_and_wait(spark_ssh, timeout=300)

        # ------------------------------------------------------------------ #
        # Post-restart: confirm the sandbox is still there                    #
        # ------------------------------------------------------------------ #
        post_list: CommandResult = run_remote(
            spark_ssh, "openshell sandbox list"
        )

        assert "nemoclaw-main" in post_list.stdout, (
            "IDEMPOTENCY FAILURE: sandbox 'nemoclaw-main' disappeared after gateway restart.\n"
            f"Post-restart sandbox list:\n{post_list.stdout}\n"
            "The sandbox was not created with --keep, or OpenShell is not honouring "
            "the keep flag on restart.  Recreate with:\n"
            "  openshell sandbox create --from openclaw --name nemoclaw-main "
            "--keep --forward 18789"
        )
