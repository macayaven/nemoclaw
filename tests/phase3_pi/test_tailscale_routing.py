"""
Phase 3 — Raspberry Pi Infrastructure: Tailscale subnet routing tests.

Validates that the Raspberry Pi is connected to the Tailscale overlay network,
is advertising the correct subnet routes (so that non-Tailscale hosts on the
LAN are reachable via the VPN), and that the DGX Spark is reachable from the
Pi over the Tailscale overlay.

Architecture
------------
The Pi acts as a subnet router that advertises the local LAN (e.g.
``192.168.1.0/24``) into the Tailscale mesh.  Remote devices connected to
Tailscale can reach the Spark and Mac via their LAN IPs by routing through
the Pi.

Tests in this module run SSH commands on the Pi to inspect Tailscale state
rather than relying on local ``tailscale`` CLI availability on the
test-runner machine.

Markers
-------
phase3     : All tests belong to Phase 3.
contract   : Layer-A structural / config tests (tailscale status JSON parsing).
behavioral : Layer-B reachability tests (ping Spark via Tailscale).
"""

from __future__ import annotations

import json
import ipaddress
from typing import Any

import pytest
from fabric import Connection

from ..helpers import run_remote, parse_json_output
from ..models import CommandResult
from ..settings import TestSettings

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The Tailscale JSON status command.
_TAILSCALE_STATUS_CMD = "tailscale status --json"

# We expect the Pi to advertise at least this subnet.
# Defaults to 192.168.1.0/24 but can be overridden via env/settings.
_DEFAULT_ADVERTISED_SUBNET = "192.168.1.0/24"

# Ping count and timeout for reachability tests.
_PING_COUNT = 3
_PING_TIMEOUT_SECS = 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tailscale_status(conn: Connection, timeout: int = 20) -> CommandResult:
    """Run ``tailscale status --json`` on the remote host and return the result.

    Args:
        conn: Open Fabric SSH connection to the target host.
        timeout: SSH command timeout in seconds (default 20).
    """
    return run_remote(conn, _TAILSCALE_STATUS_CMD, timeout=timeout)


def _parse_tailscale_json(result: CommandResult) -> dict[str, Any]:
    """Parse the JSON output of ``tailscale status --json``.

    Args:
        result: A :class:`~tests.models.CommandResult` whose stdout contains
            the raw ``tailscale status --json`` output.

    Returns:
        Parsed JSON as a dictionary.

    Raises:
        ValueError: When the output cannot be parsed as JSON.
    """
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        # Fall back to parse_json_output which scans for embedded JSON.
        raw = parse_json_output(result.stdout)
        if isinstance(raw, dict):
            return raw
        raise ValueError(
            f"tailscale status --json returned unexpected JSON type "
            f"({type(raw).__name__}).\nRaw stdout: {result.stdout[:400]!r}"
        )


def _is_tailscale_connected(status_json: dict[str, Any]) -> bool:
    """Return True when the Tailscale status JSON indicates an active connection.

    Tailscale marks the node as connected when ``BackendState`` is
    ``"Running"`` and ``Self.Online`` is ``true``.
    """
    backend_state = status_json.get("BackendState", "")
    self_info = status_json.get("Self", {})
    self_online = self_info.get("Online", False)
    return backend_state == "Running" and bool(self_online)


def _advertised_routes(status_json: dict[str, Any]) -> list[str]:
    """Extract the list of subnet routes advertised by this node.

    Tailscale ``status --json`` includes the Self node's PrimaryRoutes and
    AdvertisedRoutes fields depending on the version.  We check both.
    """
    self_info = status_json.get("Self", {})

    # PrimaryRoutes: routes currently accepted and advertised to peers
    primary = self_info.get("PrimaryRoutes") or []
    # AllowedIPs: also carries advertised subnets in some output versions
    allowed = self_info.get("AllowedIPs") or []
    # AdvertisedRoutes: explicit field in newer Tailscale versions
    advertised = self_info.get("AdvertisedRoutes") or []

    all_routes = list(primary) + list(advertised) + list(allowed)

    # Filter to keep only RFC-1918 subnets (not single-host /32 or /128 routes).
    subnet_routes = []
    for route in all_routes:
        try:
            network = ipaddress.ip_network(route, strict=False)
            if network.prefixlen < 32 and network.is_private:
                subnet_routes.append(str(network))
        except ValueError:
            pass

    return subnet_routes


# ---------------------------------------------------------------------------
# Contract tests — Tailscale connection and subnet advertisement
# ---------------------------------------------------------------------------


@pytest.mark.phase3
@pytest.mark.contract
class TestTailscaleSubnet:
    """Verify Tailscale is connected on the Pi and is advertising subnet routes."""

    def test_tailscale_connected(self, pi_ssh: Connection) -> None:
        """``tailscale status --json`` on the Pi must report BackendState=Running.

        Runs the Tailscale status command over SSH on the Pi and parses the
        JSON output.  A ``BackendState`` of ``"Running"`` combined with
        ``Self.Online: true`` confirms the node is active in the mesh.
        """
        result = _tailscale_status(pi_ssh)

        if result.return_code != 0:
            pytest.fail(
                "tailscale status --json failed on the Pi.\n"
                f"Return code: {result.return_code}\n"
                f"Stderr: {result.stderr}\n"
                "Ensure Tailscale is installed and the tailscaled daemon is running:\n"
                "  sudo systemctl status tailscaled"
            )

        try:
            status_json = _parse_tailscale_json(result)
        except ValueError as exc:
            pytest.fail(
                f"Could not parse tailscale status --json output: {exc}\n"
                f"Raw stdout: {result.stdout[:400]!r}"
            )

        backend_state = status_json.get("BackendState", "(missing)")
        assert _is_tailscale_connected(status_json), (
            f"Tailscale is not in Running state on the Pi.\n"
            f"BackendState: {backend_state!r}\n"
            f"Self.Online: {status_json.get('Self', {}).get('Online')}\n"
            "Run 'sudo tailscale up' on the Pi to authenticate and connect."
        )

    def test_subnet_route_advertised(
        self, pi_ssh: Connection, test_settings: TestSettings
    ) -> None:
        """The Pi must be advertising at least one LAN subnet into Tailscale.

        Checks the ``tailscale status --json`` output for advertised subnet
        routes (prefix length < /32).  The Pi must advertise the local LAN so
        that remote Tailscale nodes can reach the Spark and Mac Studio without
        installing Tailscale on those machines.

        The expected subnet defaults to ``192.168.1.0/24`` and can be
        overridden by setting ``TAILSCALE_ADVERTISED_SUBNET`` in the
        environment or ``.env`` file.
        """
        import os
        expected_subnet_str = os.environ.get(
            "TAILSCALE_ADVERTISED_SUBNET", _DEFAULT_ADVERTISED_SUBNET
        )
        try:
            expected_network = ipaddress.ip_network(expected_subnet_str, strict=False)
        except ValueError:
            pytest.fail(
                f"TAILSCALE_ADVERTISED_SUBNET={expected_subnet_str!r} is not a "
                "valid CIDR network."
            )

        result = _tailscale_status(pi_ssh)
        if result.return_code != 0:
            pytest.fail(
                f"tailscale status --json failed (exit {result.return_code}).\n"
                f"Stderr: {result.stderr}"
            )

        try:
            status_json = _parse_tailscale_json(result)
        except ValueError as exc:
            pytest.fail(f"Could not parse tailscale status --json: {exc}")

        advertised = _advertised_routes(status_json)

        assert advertised, (
            "The Pi is not advertising any subnet routes into Tailscale.\n"
            "To advertise the LAN subnet, run on the Pi:\n"
            f"  sudo tailscale up --advertise-routes={expected_subnet_str}\n"
            "Then approve the route in the Tailscale admin console."
        )

        # Check whether the expected subnet is covered by any advertised route.
        def _covers(advertised_str: str) -> bool:
            try:
                advertised_net = ipaddress.ip_network(advertised_str, strict=False)
                return (
                    advertised_net == expected_network
                    or expected_network.subnet_of(advertised_net)
                )
            except (ValueError, TypeError):
                return False

        covered = any(_covers(route) for route in advertised)
        assert covered, (
            f"Expected subnet {expected_subnet_str} is not covered by any "
            f"advertised route.\n"
            f"Advertised routes: {advertised}\n"
            "Run: sudo tailscale up --advertise-routes={expected_subnet_str}\n"
            "and approve in the Tailscale admin console."
        )


# ---------------------------------------------------------------------------
# Behavioral tests — reachability over Tailscale from the Pi
# ---------------------------------------------------------------------------


@pytest.mark.phase3
@pytest.mark.behavioral
class TestTailscaleAccess:
    """Verify the Pi can reach cluster nodes over Tailscale."""

    def test_spark_reachable_via_tailscale_from_pi(
        self, pi_ssh: Connection, test_settings: TestSettings
    ) -> None:
        """The Pi must be able to ping the DGX Spark via its Tailscale IP.

        Uses the Spark's Tailscale IP (100.x.x.x range) rather than its LAN
        IP to confirm the overlay path works.  This matters because NemoClaw
        remote-management operations may use Tailscale IPs when the LAN path
        is unavailable (e.g. during a VLAN change or when connecting remotely).

        Skips gracefully when the Spark's Tailscale IP is not configured in
        settings so local/CI environments are not broken.
        """
        spark_ts_ip = test_settings.tailscale_ips.get("spark")
        if spark_ts_ip is None:
            pytest.skip(
                "SPARK_TAILSCALE_IP / TAILSCALE_SPARK_IP not set in settings — "
                "cannot verify Tailscale reachability to Spark.\n"
                "Set the env var to enable this test: "
                "export TAILSCALE_SPARK_IP=100.x.x.x"
            )

        spark_ts_ip_str = str(spark_ts_ip)
        ping_cmd = (
            f"ping -c {_PING_COUNT} -W {_PING_TIMEOUT_SECS} {spark_ts_ip_str}"
        )

        result: CommandResult = run_remote(pi_ssh, ping_cmd, timeout=30)

        assert result.return_code == 0, (
            f"Ping from Pi to Spark Tailscale IP {spark_ts_ip_str} failed.\n"
            f"Command: {ping_cmd}\n"
            f"Return code: {result.return_code}\n"
            f"Stdout:\n{result.stdout}\n"
            f"Stderr:\n{result.stderr}\n"
            "Possible causes:\n"
            "  1. Tailscale is not connected on the Pi or the Spark.\n"
            "  2. The Spark's Tailscale IP has changed — update TAILSCALE_SPARK_IP.\n"
            "  3. Tailscale ACLs are blocking ICMP between these nodes."
        )

        # Confirm at least one packet was received (not 100% loss).
        stdout_lower = result.stdout.lower()
        packet_loss_line_found = "packet loss" in stdout_lower or "packets transmitted" in stdout_lower
        zero_loss = "0% packet loss" in stdout_lower or "0 packet loss" in stdout_lower

        if packet_loss_line_found:
            assert zero_loss, (
                f"Ping to Spark Tailscale IP {spark_ts_ip_str} shows packet loss.\n"
                f"Ping output:\n{result.stdout}"
            )
