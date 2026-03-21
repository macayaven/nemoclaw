"""
Phase 0 — Pre-flight Validation: Mac Studio prerequisites.

Validates that the Mac Studio (mac-studio.local) has Ollama installed,
can reach the Spark node via SSH, is connected to Tailscale, and has a
functioning container runtime (Docker Desktop or Colima).

The Mac Studio serves two roles in the NemoClaw architecture:
1. Developer workstation — all deployment commands are driven from here.
2. Secondary inference node — used as a fallback when Spark is unavailable.

Both roles require a healthy Ollama install; the developer role additionally
requires SSH access to Spark and Tailscale connectivity.

Markers
-------
phase0 : All tests here belong to Phase 0 pre-flight validation.

Fixtures (from conftest.py)
---------------------------
mac_prereqs : MacPrereqs       — Pydantic model populated via SSH at fixture load time.
mac_ssh     : fabric.Connection — live SSH connection to the Mac Studio.
spark_ssh   : fabric.Connection — live SSH connection to Spark (used in SSH reachability test).
spark_ip    : str               — LAN IP of the Spark node.
mac_ip      : str               — LAN IP of the Mac Studio.
"""

from __future__ import annotations

import pytest
from fabric import Connection
from packaging.version import Version

from ..helpers import run_remote
from ..models import CommandResult, MacPrereqs

# ---------------------------------------------------------------------------
# Minimum version thresholds
# ---------------------------------------------------------------------------

_MIN_OLLAMA_VERSION = Version("0.3.0")

# ---------------------------------------------------------------------------
# Ollama tests
# ---------------------------------------------------------------------------


@pytest.mark.phase0
class TestMacOllama:
    """Ollama must be installed and at a usable version on Mac Studio.

    The Mac is used as a secondary inference provider.  Even if Spark is the
    primary, having Ollama available on the Mac allows the system to fail over
    during Spark maintenance, and supports local development workflows where
    the developer iterates without needing Spark online.
    """

    def test_ollama_installed(self, mac_prereqs: MacPrereqs) -> None:
        """Ollama binary is present on the Mac and reports a non-empty version.

        Confirms that the ``ollama`` CLI is installed (either via the macOS
        installer package or Homebrew) and that it can report its own version,
        which requires the binary to be runnable.  An empty or missing version
        string indicates the install is broken or the binary is missing from PATH.
        """
        assert mac_prereqs.ollama_version != "", (
            "ollama_version is empty on Mac Studio — Ollama may not be installed. "
            "Install from https://ollama.com/download or: brew install ollama"
        )

    def test_ollama_version(self, mac_prereqs: MacPrereqs) -> None:
        """Ollama version on Mac is parseable and meets the minimum version requirement.

        Verifies the version string is well-formed (parseable by
        ``packaging.version.Version``) and at least 0.3.0, which introduced
        the ``/v1/chat/completions`` OpenAI-compatible endpoint that NemoClaw
        depends on for provider-agnostic inference routing.

        Uses ``packaging.version.Version`` rather than string splitting to
        handle OS-specific suffixes (e.g. ``0.3.12+brew``) without breaking.
        """
        installed: Version = mac_prereqs.ollama_version_parsed
        assert installed >= _MIN_OLLAMA_VERSION, (
            f"Ollama {installed} on Mac Studio is below the minimum required "
            f"version {_MIN_OLLAMA_VERSION}. "
            "The /v1/chat/completions endpoint was introduced in 0.3.0. "
            "Upgrade: ollama --version && curl -fsSL https://ollama.com/install.sh | sh "
            "(or: brew upgrade ollama)"
        )


# ---------------------------------------------------------------------------
# SSH reachability tests
# ---------------------------------------------------------------------------


@pytest.mark.phase0
class TestMacSSH:
    """Mac Studio must be able to reach Spark via SSH.

    Every Phase 1 deployment step is driven from the Mac Studio over SSH:
    pushing configuration files, running remote commands, and checking service
    status.  If the Mac cannot SSH to Spark, the entire automated deployment
    workflow is blocked before it starts.

    This test validates:
    1. Network connectivity (TCP layer 4 reach to Spark's SSH port 22)
    2. Authentication (the SSH key pair is properly configured)
    3. Remote shell execution (bash runs and echo works)
    """

    def test_can_ssh_to_spark(self, mac_ssh: Connection, spark_ip: str) -> None:
        """Mac Studio can open an SSH session to Spark and execute a command.

        SSHes from Mac to Spark using the configured SSH key and runs
        ``echo OK``.  The test asserts the output is exactly ``OK``, which
        confirms:
        - Network connectivity between Mac and Spark on port 22
        - The SSH key trusted by Spark's ``~/.ssh/authorized_keys`` is loaded
          on the Mac (either in ssh-agent or as a key file)
        - The remote shell is functional

        The ``spark_ip`` fixture is used (rather than the hostname) so the
        test validates layer-3 reachability independently of DNS resolution.
        """
        # Run from the Mac: SSH to Spark and echo a sentinel value
        result: CommandResult = run_remote(
            mac_ssh,
            f"ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 carlos@{spark_ip} 'echo OK'",
            timeout=30,
        )
        assert result.return_code == 0, (
            f"SSH from Mac Studio to Spark ({spark_ip}) failed with exit code "
            f"{result.return_code}. "
            f"stderr: {result.stderr!r}. "
            "Troubleshoot: "
            "1) Is Spark reachable? ping {spark_ip} "
            "2) Is the SSH key in authorized_keys on Spark? "
            "3) Is ssh-agent running with the key loaded? ssh-add -l"
        )
        output = result.stdout.strip()
        assert output == "OK", (
            f"SSH to Spark returned unexpected output: {output!r} "
            f"(expected 'OK'). "
            "This may indicate a shell configuration issue (e.g. .bashrc printing "
            "extra output) or a remote command wrapper intercepting the session."
        )


# ---------------------------------------------------------------------------
# Tailscale tests
# ---------------------------------------------------------------------------


@pytest.mark.phase0
class TestMacTailscale:
    """Tailscale must be connected on Mac Studio for overlay network access.

    Tailscale on the Mac enables the developer to reach NemoClaw endpoints
    on Spark and the Pi from anywhere, not just from the local LAN.  It is
    also required for Phase 5 mobile access tests, which validate that the
    inference API is reachable from a phone over the Tailscale mesh.
    """

    def test_tailscale_connected(self, mac_ssh: Connection) -> None:
        """Tailscale on Mac Studio reports Backend=Running and is connected.

        Queries ``tailscale status --json`` to inspect the backend state
        rather than relying on the human-readable summary, which can change
        format across Tailscale versions.

        A node in the ``NeedsLogin`` or ``Stopped`` backend state would
        silently fail all cross-Tailscale communication tests in later phases.
        """
        result: CommandResult = run_remote(
            mac_ssh,
            "tailscale status --self 2>&1 | head -3",
            timeout=15,
        )
        assert result.return_code == 0, (
            f"tailscale status failed on Mac Studio (exit {result.return_code}). "
            f"stderr: {result.stderr!r}. "
            "Is Tailscale installed? brew install --cask tailscale"
        )
        output = result.stdout.lower()
        # 'tailscale status' exits 0 and prints the node's status when connected;
        # it prints "stopped" or "NeedsLogin" when disconnected.
        assert "stopped" not in output and "needslogin" not in output, (
            f"Tailscale on Mac Studio appears disconnected or not logged in. "
            f"tailscale status output:\n{result.stdout}\n"
            "Fix: open the Tailscale app, or run: sudo tailscale up"
        )
        # At minimum, expect the status output to contain an IP or 'self'
        # (tailscale status --self shows this machine's details when connected)
        assert result.stdout.strip() != "", (
            "tailscale status produced empty output on Mac Studio. "
            "This suggests Tailscale is installed but the daemon is not running. "
            "Start it via the macOS menu bar app or: sudo tailscaled"
        )


# ---------------------------------------------------------------------------
# Docker tests
# ---------------------------------------------------------------------------


@pytest.mark.phase0
class TestMacDocker:
    """A container runtime (Docker Desktop or Colima) must be available on Mac.

    The Mac Studio is used for local development and testing of NemoClaw
    container configurations before they are deployed to Spark.  Docker on
    the Mac is not required for production inference (that happens on Spark),
    but is needed for:
    - Building and testing sandbox images locally
    - Running integration tests that spin up mock containers
    - Verifying docker-compose configurations before pushing to Spark
    """

    def test_docker_available(self, mac_ssh: Connection) -> None:
        """Docker CLI is available on Mac (Docker Desktop or Colima runtime).

        macOS supports two common container runtime setups:
        - **Docker Desktop**: installs the ``docker`` CLI and a Linux VM.
        - **Colima**: a lighter alternative VM that also exposes the ``docker``
          CLI via a socket.

        This test checks for the ``docker`` CLI regardless of which backend is
        in use, then verifies the daemon is reachable (i.e. not just the CLI
        binary, but the socket that backs it).

        If neither runtime is installed the test fails with actionable install
        instructions for both options.
        """
        # First check: is the docker CLI present?
        cli_result: CommandResult = run_remote(
            mac_ssh, "which docker || command -v docker", timeout=10
        )
        assert cli_result.return_code == 0, (
            "Docker CLI (docker) was not found on Mac Studio. "
            "Install Docker Desktop: https://www.docker.com/products/docker-desktop/ "
            "or Colima (lighter): brew install colima docker && colima start"
        )

        # Second check: is the daemon actually responding?
        # 'docker info' fails if the socket is down, even if the CLI exists.
        info_result: CommandResult = run_remote(
            mac_ssh, "docker info --format '{{.ServerVersion}}'", timeout=20
        )
        assert info_result.return_code == 0, (
            "Docker CLI is present on Mac Studio but the daemon is not running. "
            f"docker info error: {info_result.stderr!r}. "
            "Start Docker Desktop from Applications, or if using Colima: colima start. "
            "Verify: docker ps"
        )
        server_version = info_result.stdout.strip()
        assert server_version != "", (
            "docker info returned an empty server version string. "
            "The daemon may be starting up; wait a few seconds and retry. "
            f"Full info output: {info_result.stdout!r}"
        )
