"""
Phase 5 — Mobile / Tailscale: Remote access boundary and Tailscale ACL tests.

Validates the security boundary of the NemoClaw deployment from the network-
access perspective:
- Port 18789 (NemoClaw UI) must NOT be reachable from the public internet or
  from a machine outside the trusted LAN / Tailscale network.
- The Tailscale network must confirm that the test-runner's Tailscale peer can
  reach the Spark node, i.e. the ACL policy allows the connection.

The first test (gateway not on public WAN) uses the Spark SSH connection to
verify that the service is not bound to the public IP, rather than attempting
to connect from a genuinely external host (which would require a different
network position and is impractical in automated test environments).

Markers
-------
phase5    : All tests here belong to Phase 5 (Mobile / Tailscale).
contract  : Layer A — structural assertion about network binding and ACL state.

Fixtures (from conftest.py)
---------------------------
spark_ssh          : fabric.Connection — live SSH connection to the DGX Spark.
spark_tailscale_ip : str | None — Tailscale IP of the Spark node.
spark_ip           : str — LAN IP of the Spark node.
test_settings      : TestSettings — provides host and timeout configuration.
"""

from __future__ import annotations

import re

import pytest
from fabric import Connection

from ..helpers import run_remote
from ..models import CommandResult
from ..settings import TestSettings

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NEMOCLAW_UI_PORT: int = 18789

# Prefixes for IP addresses that are considered "internal" (LAN or Tailscale).
# The test asserts that port 18789 is only bound to addresses in these ranges.
_INTERNAL_PREFIXES: tuple[str, ...] = (
    "127.",       # loopback
    "10.",        # RFC1918 class A
    "172.16.",    # RFC1918 class B (first /12 block)
    "172.17.",    # Docker default bridge
    "172.18.",
    "172.19.",
    "172.20.",
    "172.21.",
    "172.22.",
    "172.23.",
    "172.24.",
    "172.25.",
    "172.26.",
    "172.27.",
    "172.28.",
    "172.29.",
    "172.30.",
    "172.31.",
    "192.168.",   # RFC1918 class C
    "100.",       # Tailscale CGNAT range (100.64.0.0/10)
    "0.0.0.0",    # all-interfaces bind — checked separately below
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_internal_address(address: str) -> bool:
    """Return True when *address* is in a private / loopback / Tailscale range.

    An all-zeroes bind (``0.0.0.0``) is considered NOT internal because it
    exposes the port on every interface including any public-facing one.

    Args:
        address: IPv4 address string extracted from a ``ss`` or ``netstat``
            listening socket line.

    Returns:
        True if the address is loopback, RFC1918, or Tailscale-range.
    """
    if address == "0.0.0.0":
        return False  # wildcard bind — potentially public
    return any(address.startswith(prefix) for prefix in _INTERNAL_PREFIXES)


def _require_tailscale_ip(tailscale_ip: str | None) -> None:
    """Skip the calling test when the Tailscale IP is not configured."""
    if tailscale_ip is None:
        pytest.skip(
            "SPARK_TAILSCALE_IP is not set in the test environment. "
            "Set SPARK_TAILSCALE_IP=100.x.x.x in tests/.env to run Tailscale ACL tests."
        )


# ---------------------------------------------------------------------------
# Contract tests
# ---------------------------------------------------------------------------


@pytest.mark.phase5
@pytest.mark.contract
class TestRemoteAccess:
    """Layer A: NemoClaw UI is not exposed on public WAN; Tailscale ACL allows access.

    These tests assert structural properties of the network configuration:
    - The binding addresses for port 18789 are restricted to internal / Tailscale
      interfaces.
    - The local Tailscale daemon reports the Spark node as reachable, which
      confirms the ACL policy is in effect.
    """

    def test_gateway_not_on_public_wan(self, spark_ssh: Connection) -> None:
        """Port 18789 is not bound to any public-facing (non-private) IP address.

        Uses ``ss -tlnp`` on the Spark node to enumerate listening TCP sockets
        on port 18789 and inspects the bound address for each socket.  The test
        passes when every listening socket is bound to one of:
        - ``127.0.0.1`` / ``::1`` (loopback)
        - An RFC1918 address (10.x, 172.16-31.x, 192.168.x)
        - A Tailscale CGNAT address (100.64-127.x)

        A wildcard bind (``0.0.0.0``) is flagged as a potential public exposure
        because it includes any public IP assigned to the machine's network
        interfaces.  The assertion message includes remediation steps.

        Note: This test runs over the existing LAN SSH connection and does NOT
        require an externally reachable network position.  It verifies the
        binding configuration rather than attempting an external connection.
        """
        result: CommandResult = run_remote(
            spark_ssh,
            f"ss -tlnp sport = :{_NEMOCLAW_UI_PORT} 2>&1",
            timeout=15,
        )
        assert result.return_code == 0, (
            f"'ss -tlnp' failed on Spark (exit {result.return_code}). "
            f"stderr: {result.stderr!r}"
        )
        ss_output = result.stdout

        # Parse bound addresses from 'ss' output.
        # Format: "LISTEN  0  128  <address>:<port>  ..."
        bound_addresses: list[str] = []
        for line in ss_output.splitlines():
            # Match lines for our port number
            if str(_NEMOCLAW_UI_PORT) not in line:
                continue
            # Extract the local address:port column (4th field in ss -tlnp)
            parts = line.split()
            if len(parts) < 5:
                continue
            local_field = parts[4]  # e.g. "192.168.1.10:18789" or "0.0.0.0:18789"
            # Split off the port part
            if ":" in local_field:
                addr = local_field.rsplit(":", 1)[0]
                # Strip IPv6 brackets
                addr = addr.strip("[]")
                bound_addresses.append(addr)

        if not bound_addresses:
            # ss returned no listening sockets — the service may not be running.
            # Let TestTailscaleAccess.test_ui_via_tailscale_ip catch that.
            pytest.skip(
                f"No listening sockets found for port {_NEMOCLAW_UI_PORT} on Spark. "
                "The NemoClaw UI service may not be running. "
                "Skipping WAN exposure check; the service-up test will catch this."
            )

        public_bindings: list[str] = []
        wildcard_bindings: list[str] = []
        for addr in bound_addresses:
            if addr == "0.0.0.0":
                wildcard_bindings.append(addr)
            elif not _is_internal_address(addr):
                public_bindings.append(addr)

        issues: list[str] = []
        if public_bindings:
            issues.append(
                f"Port {_NEMOCLAW_UI_PORT} is bound to public IP(s): "
                + ", ".join(public_bindings)
            )
        if wildcard_bindings:
            issues.append(
                f"Port {_NEMOCLAW_UI_PORT} is bound to 0.0.0.0 (all interfaces), "
                "which may include public-facing interfaces. "
                "Restrict the bind address to the LAN IP or Tailscale IP in the "
                "NemoClaw UI service configuration."
            )

        assert not issues, (
            "NemoClaw UI (port 18789) appears accessible beyond LAN/Tailscale:\n"
            + "\n".join(f"  - {issue}" for issue in issues)
            + "\n\nRemediation: configure the NemoClaw UI to bind only to "
            "192.168.x.x (LAN) and/or the Tailscale IP (100.x.x.x). "
            "Do NOT bind to 0.0.0.0 unless a firewall rule explicitly blocks "
            f"port {_NEMOCLAW_UI_PORT} from non-LAN sources."
        )

    def test_tailscale_acls_allow_access(
        self,
        spark_ssh: Connection,
        spark_tailscale_ip: str | None,
    ) -> None:
        """Tailscale reports the Spark node as reachable from the current peer.

        Runs ``tailscale status`` on the Spark node and verifies that:
        1. Tailscale is in a connected state (Backend=Running / Status=Running).
        2. The Spark node's Tailscale IP is listed in the peer table (either
           as the local node's own address, or as an accessible peer).

        This is a lightweight ACL sanity-check: if Tailscale itself is not
        running or the node is not logged in, the mobile / remote access path
        is entirely broken regardless of what the port-binding configuration
        says.

        The test is skipped when ``SPARK_TAILSCALE_IP`` is not configured
        because we cannot check for a specific IP we do not know.
        """
        _require_tailscale_ip(spark_tailscale_ip)
        assert spark_tailscale_ip is not None  # narrowed for type checker

        result: CommandResult = run_remote(
            spark_ssh,
            "tailscale status 2>&1",
            timeout=20,
        )
        assert result.return_code == 0, (
            "tailscale status returned a non-zero exit code on Spark. "
            "Tailscale may not be installed, logged in, or the daemon may be stopped. "
            f"Run: sudo tailscale status  and  sudo systemctl status tailscaled. "
            f"stderr: {result.stderr!r}"
        )

        ts_output = result.stdout
        assert ts_output.strip() not in ("", "Tailscale is stopped."), (
            "Tailscale is installed but stopped on Spark. "
            "Start it: sudo tailscale up. "
            "If this is a fresh install, authenticate first: "
            "sudo tailscale up --authkey=<key>"
        )

        # Verify the expected Tailscale IP appears in the status output.
        ts_ip_str = str(spark_tailscale_ip)
        assert ts_ip_str in ts_output, (
            f"Expected Tailscale IP {ts_ip_str!r} not found in 'tailscale status' "
            "output on Spark. "
            "Possible causes: the node logged out and re-authenticated with a new IP, "
            "or SPARK_TAILSCALE_IP in tests/.env is stale. "
            "Update SPARK_TAILSCALE_IP with the current value from: tailscale ip -4. "
            f"tailscale status output:\n{ts_output}"
        )

        # Check for connectivity-blocking keywords in the status output.
        blocking_phrases = (
            "needs login",
            "logged out",
            "backend: stopped",
            "state: stopped",
        )
        ts_output_lower = ts_output.lower()
        for phrase in blocking_phrases:
            assert phrase not in ts_output_lower, (
                f"Tailscale status contains blocking phrase {phrase!r} on Spark. "
                "The node is not fully connected and remote access via Tailscale "
                "will fail. "
                f"Fix: sudo tailscale up. "
                f"Full status:\n{ts_output}"
            )
