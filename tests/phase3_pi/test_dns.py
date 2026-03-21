"""
Phase 3 — Raspberry Pi Infrastructure: DNS resolution tests.

Validates that the DNS server running on the Raspberry Pi correctly resolves
the cluster's internal .lab hostnames.  All lookups are performed via SSH on
the Pi itself (using ``nslookup``) rather than via the local socket on the
test-runner machine, so the tests reflect the Pi's resolver configuration
rather than the developer workstation's.

Cluster hostnames under test
----------------------------
  spark.lab  — DGX Spark primary inference node
  mac.lab    — Mac Studio secondary inference node
  ai.lab     — Alias / VIP that should resolve (e.g. to the Pi or a VIP)

Markers
-------
phase3   : All tests belong to Phase 3.
contract : DNS contract tests — verifies resolution behaviour (fast, no inference).
"""

from __future__ import annotations

import pytest
from fabric import Connection

from tests.helpers import run_remote
from tests.models import CommandResult

# ---------------------------------------------------------------------------
# DNS hostnames under test
# ---------------------------------------------------------------------------

_LAB_HOSTNAMES = [
    "spark.lab",
    "mac.lab",
    "ai.lab",
]

_NONEXISTENT_HOSTNAME = "random-nonexistent-host-nemoclaw.lab"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _nslookup(conn: Connection, hostname: str, timeout: int = 15) -> CommandResult:
    """Run ``nslookup <hostname>`` on the remote Pi and return the result.

    ``nslookup`` exits with status 1 on NXDOMAIN, which is the expected
    behaviour we test in ``TestDNSNegative``.

    Args:
        conn: Open Fabric SSH connection to the Raspberry Pi.
        hostname: Fully-qualified or local hostname to look up.
        timeout: SSH command timeout in seconds (default 15).

    Returns:
        A :class:`~tests.models.CommandResult` with stdout, stderr, and
        return_code populated.
    """
    return run_remote(conn, f"nslookup {hostname}", timeout=timeout)


def _resolved_successfully(result: CommandResult) -> bool:
    """Return True when nslookup output indicates a successful resolution.

    nslookup prints "Name:" and "Address:" lines on success.  We check for
    both to avoid treating forwarding-error lines as successful lookups.
    """
    stdout_lower = result.stdout.lower()
    return (
        "name:" in stdout_lower
        and "address:" in stdout_lower
        and "can't find" not in stdout_lower
        and "nxdomain" not in stdout_lower
        and "server failed" not in stdout_lower
    )


def _is_nxdomain(result: CommandResult) -> bool:
    """Return True when nslookup output indicates an NXDOMAIN (name not found).

    Covers the ``nslookup`` output variants across Pi OS (Bookworm / Bullseye):
      - "can't find <host>: Non-existent domain"
      - "NXDOMAIN"
      - "server can't find <host>: NXDOMAIN"
    """
    combined = (result.stdout + result.stderr).lower()
    return (
        "nxdomain" in combined
        or "can't find" in combined
        or "non-existent domain" in combined
    )


# ---------------------------------------------------------------------------
# Contract tests — positive resolution
# ---------------------------------------------------------------------------


@pytest.mark.phase3
@pytest.mark.contract
class TestDNSResolution:
    """Verify that the Pi DNS server resolves the expected .lab hostnames."""

    @pytest.mark.parametrize("hostname", _LAB_HOSTNAMES)
    def test_dns_resolves(self, pi_ssh: Connection, hostname: str) -> None:
        """``nslookup <hostname>`` run on the Pi must return a valid IP address.

        The test is parametrized over all three cluster hostnames so that a
        single DNS misconfiguration does not hide failures for other names.
        Each parametrized case is reported independently in the pytest output.

        Args:
            pi_ssh: Open Fabric SSH connection to the Raspberry Pi (session fixture).
            hostname: One of spark.lab, mac.lab, ai.lab (injected by parametrize).
        """
        result = _nslookup(pi_ssh, hostname)

        assert _resolved_successfully(result), (
            f"DNS resolution of '{hostname}' failed on the Pi.\n"
            f"nslookup stdout:\n{result.stdout}\n"
            f"nslookup stderr:\n{result.stderr}\n"
            f"Return code: {result.return_code}\n"
            "Ensure the DNS server (e.g. dnsmasq / bind9) is running on the Pi "
            f"and has a record for '{hostname}'."
        )

        # Sanity-check: at least one address line must contain a dotted-quad IPv4
        # or an IPv6 address.  ``nslookup`` on success prints lines like:
        #   Address: 192.168.1.10
        import re
        address_pattern = re.compile(
            r"address:\s+(\d{1,3}(?:\.\d{1,3}){3}|[0-9a-f:]+)",
            re.IGNORECASE,
        )
        matches = address_pattern.findall(result.stdout)
        # Filter out the DNS server's own address line (first Address: line is
        # often the server itself when nslookup is in non-interactive mode).
        non_server_addresses = [
            addr for addr in matches
            if not result.stdout.lower().startswith(f"server:")
        ]
        assert matches, (
            f"nslookup for '{hostname}' succeeded but no IP address was found "
            f"in the output.\n"
            f"stdout:\n{result.stdout}"
        )


# ---------------------------------------------------------------------------
# Negative tests — NXDOMAIN behaviour
# ---------------------------------------------------------------------------


@pytest.mark.phase3
class TestDNSNegative:
    """Verify that the DNS server correctly returns NXDOMAIN for unknown names."""

    def test_nonexistent_hostname_returns_nxdomain(self, pi_ssh: Connection) -> None:
        """``nslookup`` for a non-existent .lab name must return NXDOMAIN.

        This test guards against an overly permissive DNS configuration that
        wildcard-resolves all .lab names regardless of whether a record exists.
        A correctly configured Pi DNS server should return NXDOMAIN for names
        that have not been explicitly defined.

        The hostname chosen is deliberately random-looking to avoid any chance
        of it accidentally matching a real record.
        """
        result = _nslookup(pi_ssh, _NONEXISTENT_HOSTNAME)

        assert _is_nxdomain(result), (
            f"Expected NXDOMAIN for '{_NONEXISTENT_HOSTNAME}' but the lookup "
            f"did not return a clear failure.\n"
            f"nslookup stdout:\n{result.stdout}\n"
            f"nslookup stderr:\n{result.stderr}\n"
            f"Return code: {result.return_code}\n"
            "If the DNS server wildcard-resolves all .lab names, this is a "
            "misconfiguration: spurious hostnames would silently resolve, "
            "masking typos and test infrastructure bugs."
        )

        # Extra guard: the output must NOT contain a valid address.
        assert not _resolved_successfully(result), (
            f"nslookup for non-existent host '{_NONEXISTENT_HOSTNAME}' "
            f"returned what looks like a successful response — "
            f"possible wildcard DNS misconfiguration.\n"
            f"stdout:\n{result.stdout}"
        )
