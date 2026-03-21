"""
Phase 0 — Pre-flight Validation: Raspberry Pi prerequisites.

Validates that the Raspberry Pi infrastructure node (raspi.local) has
sufficient RAM, a compatible Python 3 runtime, network reachability to the
other cluster members, and Tailscale connectivity.

The Raspberry Pi runs the infrastructure plane of the NemoClaw cluster:
- LiteLLM proxy (routes inference requests across providers)
- Pi-hole DNS (resolves ``inference.local`` and other internal hostnames)
- Prometheus + Grafana monitoring stack

Its resource requirements are modest but non-zero.  A Pi with less than
2 GB of free RAM cannot reliably run LiteLLM and the monitoring stack
simultaneously, leading to OOM kills during peak load.

Markers
-------
phase0 : All tests here belong to Phase 0 pre-flight validation.

Fixtures (from conftest.py)
---------------------------
pi_prereqs : PiPrereqs          — Pydantic model populated via SSH at fixture load time.
pi_ssh     : fabric.Connection  — live SSH connection to the Pi.
spark_ip   : str                — LAN IP of the Spark node.
mac_ip     : str                — LAN IP of the Mac Studio.
pi_ip      : str                — LAN IP of the Raspberry Pi.
"""

from __future__ import annotations

import pytest
from fabric import Connection
from packaging.version import Version

from tests.helpers import parse_version, run_remote
from tests.models import CommandResult, PiPrereqs

# ---------------------------------------------------------------------------
# Minimum resource thresholds
# ---------------------------------------------------------------------------

_MIN_FREE_RAM_MB = 2000           # 2 GB free (available + buffers/cache)
_MIN_PYTHON_VERSION = Version("3.10")

# ---------------------------------------------------------------------------
# Resource tests
# ---------------------------------------------------------------------------


@pytest.mark.phase0
class TestPiResources:
    """The Pi must have sufficient RAM and a compatible Python runtime.

    LiteLLM and the monitoring stack together consume 800–1200 MB under load.
    The 2 GB threshold gives a comfortable margin that prevents OOM kills
    when all services are running simultaneously.
    """

    def test_sufficient_ram(self, pi_prereqs: PiPrereqs) -> None:
        """At least 2000 MB of RAM is free (available + buffers/cache) on the Pi.

        Uses the ``free_ram_mb`` field from the ``PiPrereqs`` model, which is
        calculated as ``MemAvailable`` from ``/proc/meminfo``.  This metric
        is more meaningful than raw ``MemFree`` because Linux aggressively
        uses free RAM for buffers and cache; ``MemAvailable`` accounts for
        reclaimable memory that the OS can return to applications on demand.

        The 2 GB minimum supports:
        - LiteLLM proxy process: ~300-500 MB
        - Prometheus with 30-day retention: ~200-400 MB
        - Grafana: ~150-300 MB
        - Pi-hole + DNS: ~100 MB
        - OS + headroom: ~500 MB
        """
        assert pi_prereqs.free_ram_mb >= _MIN_FREE_RAM_MB, (
            f"Insufficient free RAM on the Raspberry Pi: "
            f"{pi_prereqs.free_ram_mb} MB available, "
            f"need >= {_MIN_FREE_RAM_MB} MB. "
            "Stop unused processes or reduce Prometheus retention: "
            "sudo systemctl stop grafana && free -m"
        )

    def test_python3_available(self, pi_ssh: Connection) -> None:
        """python3 binary is present and executable on the Pi.

        Python 3 is required to run LiteLLM and supporting scripts.  This
        test validates the binary is in PATH and can execute — not just that a
        ``python3`` symlink exists.  A broken Python install (e.g. missing
        shared libraries) would pass a ``which python3`` check but fail here.
        """
        result: CommandResult = run_remote(
            pi_ssh, "python3 --version", timeout=15
        )
        assert result.return_code == 0, (
            "python3 --version failed on the Raspberry Pi "
            f"(exit code {result.return_code}). "
            f"stderr: {result.stderr!r}. "
            "Install Python 3: sudo apt-get update && sudo apt-get install -y python3"
        )
        output = result.stdout.strip() or result.stderr.strip()
        # python3 --version prints to stdout on 3.4+, stderr on earlier versions
        assert output.startswith("Python 3."), (
            f"python3 --version returned unexpected output: {output!r}. "
            "Expected a string starting with 'Python 3.'. "
            "Verify the installation: python3 -c 'import sys; print(sys.version)'"
        )

    def test_python3_version_minimum(self, pi_prereqs: PiPrereqs) -> None:
        """Python 3 version on the Pi is at least 3.10.

        LiteLLM requires Python 3.10+ for its use of structural pattern
        matching (``match`` / ``case``) and union type hints (``X | Y``).
        Older Python 3 releases (3.8, 3.9) will raise ``SyntaxError`` on
        import, causing LiteLLM to fail at startup in a non-obvious way.

        Uses ``packaging.version.Version`` for comparison to correctly handle
        Raspberry Pi OS's package versioning scheme (e.g. ``3.11.2-1+deb12u1``).
        """
        installed: Version = pi_prereqs.python3_version_parsed
        assert installed >= _MIN_PYTHON_VERSION, (
            f"Python {installed} on the Raspberry Pi is below the minimum "
            f"required version {_MIN_PYTHON_VERSION}. "
            "LiteLLM requires Python 3.10+. "
            "Upgrade: sudo apt-get install -y python3.11 "
            "or use deadsnakes PPA: sudo add-apt-repository ppa:deadsnakes/ppa"
        )


# ---------------------------------------------------------------------------
# Network reachability tests
# ---------------------------------------------------------------------------


@pytest.mark.phase0
class TestPiNetwork:
    """The Pi must be able to reach both Spark and Mac Studio over the LAN.

    The Pi acts as the network hub for the NemoClaw cluster:
    - Its DNS (Pi-hole) resolves names for all other nodes.
    - Its LiteLLM proxy receives requests and routes them to Spark or Mac.
    - Its monitoring stack scrapes metrics from all nodes.

    All three functions require reliable LAN connectivity to Spark and Mac.
    A Pi that cannot reach Spark will route inference requests into a black
    hole; a Pi that cannot reach Mac will fail health checks silently.

    IP addresses (not hostnames) are used for the ping tests to validate
    layer-3 connectivity independently of DNS.  DNS failures are tested
    separately in Phase 3.
    """

    def test_can_reach_spark(self, pi_ssh: Connection, spark_ip: str) -> None:
        """Pi can send a single ICMP ping to Spark and receive a reply.

        Uses ``ping -c 1 -W 3`` (one packet, 3-second wait) to confirm basic
        layer-3 reachability from the Pi to the Spark node.  A failed ping
        at this stage indicates a routing problem between the Pi and Spark —
        e.g. a broken switch port, VLAN misconfiguration, or firewall rule
        blocking ICMP — that would prevent all subsequent cluster communication.

        The test uses ``spark_ip`` (not the hostname ``spark-caeb.local``) to
        decouple this test from DNS resolution.  If this test passes but
        hostname-based communication fails, the failure is in DNS, not routing.
        """
        result: CommandResult = run_remote(
            pi_ssh,
            f"ping -c 1 -W 3 {spark_ip}",
            timeout=15,
        )
        assert result.return_code == 0, (
            f"Pi cannot reach Spark at {spark_ip} via ICMP. "
            f"ping output:\n{result.stdout}\n{result.stderr}\n"
            "Troubleshoot: "
            "1) Is Spark powered on? "
            "2) Are Pi and Spark on the same VLAN/subnet? "
            "3) Does a firewall block ICMP? (check: sudo iptables -L INPUT -n)"
        )

    def test_can_reach_mac(self, pi_ssh: Connection, mac_ip: str) -> None:
        """Pi can send a single ICMP ping to Mac Studio and receive a reply.

        Validates layer-3 connectivity from the Pi to the Mac Studio.  The
        Mac Studio serves as the secondary inference provider in NemoClaw's
        failover model; if the Pi cannot reach it, LiteLLM's health checks
        will continuously mark the Mac provider as unavailable and remove it
        from the routing pool.

        Uses ``mac_ip`` directly to avoid DNS dependency (same rationale as
        ``test_can_reach_spark``).
        """
        result: CommandResult = run_remote(
            pi_ssh,
            f"ping -c 1 -W 3 {mac_ip}",
            timeout=15,
        )
        assert result.return_code == 0, (
            f"Pi cannot reach Mac Studio at {mac_ip} via ICMP. "
            f"ping output:\n{result.stdout}\n{result.stderr}\n"
            "Troubleshoot: "
            "1) Is the Mac Studio powered on and awake? "
            "2) Check macOS firewall: System Settings > Network > Firewall. "
            "3) Verify both machines are on the same subnet: ip route on Pi, "
            "ifconfig on Mac."
        )


# ---------------------------------------------------------------------------
# Tailscale tests
# ---------------------------------------------------------------------------


@pytest.mark.phase0
class TestPiTailscale:
    """Tailscale must be connected on the Pi for overlay network access.

    The Pi's Tailscale node enables mobile clients (Phase 5) to reach the
    LiteLLM proxy and monitoring dashboards from outside the LAN without
    exposing the Pi's ports to the public internet.

    A disconnected Tailscale node on the Pi would cause Phase 5 mobile tests
    to fail, and would break any out-of-band monitoring access when the
    developer is away from the LAN.
    """

    def test_tailscale_connected(self, pi_ssh: Connection) -> None:
        """Tailscale on the Pi reports it is connected and the backend is running.

        Checks ``tailscale status`` output for signs of a disconnected,
        stopped, or unauthenticated node.  Tailscale can be in several
        inactive states (``Stopped``, ``NeedsLogin``, ``NoState``) that all
        produce a non-error exit code from ``tailscale status`` but indicate
        that the mesh network is not functional.

        The Pi runs Tailscale as a headless node (no GUI); authentication is
        performed once via ``sudo tailscale up --authkey=...``.  If the auth
        key expires or the node is removed from the Tailscale admin console,
        this test will catch the disconnection.
        """
        result: CommandResult = run_remote(
            pi_ssh,
            "tailscale status --self 2>&1 | head -5",
            timeout=15,
        )
        assert result.return_code == 0, (
            f"tailscale status failed on the Raspberry Pi "
            f"(exit code {result.return_code}). "
            f"stderr: {result.stderr!r}. "
            "Is Tailscale installed? sudo apt-get install tailscale && "
            "sudo tailscale up --authkey=<key>"
        )
        output = result.stdout.lower()
        # Detect known disconnected states in the status output
        disconnected_states = ("stopped", "needslogin", "nostate", "not logged in")
        for bad_state in disconnected_states:
            assert bad_state not in output, (
                f"Tailscale on the Pi is in a disconnected state "
                f"('{bad_state}' found in status output). "
                f"Full tailscale status:\n{result.stdout}\n"
                "Fix: sudo tailscale up (or re-authenticate: "
                "sudo tailscale logout && sudo tailscale up --authkey=<key>)"
            )
        assert result.stdout.strip() != "", (
            "tailscale status produced empty output on the Pi. "
            "The Tailscale daemon may not be running. "
            "Start it: sudo systemctl start tailscaled && sudo tailscale up"
        )
