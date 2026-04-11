"""
conftest.py — Shared pytest fixtures for the NemoClaw TDD test suite.

Provides:
  - Session-scoped SSH connections (Fabric) to Spark, Mac, and Pi.
  - Convenience IP / URL / host string fixtures derived from settings.
  - testinfra host fixtures wrapping each Fabric connection.
  - Session-scoped prereq fixtures (SparkPrereqs, MacPrereqs, PiPrereqs) that
    gather host state over SSH and parse it into Pydantic models.
"""

from __future__ import annotations

import os

import pytest
from fabric import Connection

from .helpers import run_remote
from .models import CommandResult, MacPrereqs, PiPrereqs, SparkPrereqs
from .settings import TestSettings

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def test_settings() -> TestSettings:
    """Load and validate TestSettings from environment variables / .env file.

    The pydantic-settings model reads from the environment and from the
    ``tests/.env`` file (if present) automatically.
    """
    return TestSettings()


# ---------------------------------------------------------------------------
# SSH connection helpers
# ---------------------------------------------------------------------------


def _format_ssh_failure(host: str, user: str, ssh_key: str | None, exc: Exception) -> str:
    """Build a concise operator-facing SSH failure message."""
    agent_hint = (
        f"SSH_AUTH_SOCK is set: {os.environ['SSH_AUTH_SOCK']}"
        if os.environ.get("SSH_AUTH_SOCK")
        else "SSH_AUTH_SOCK is not set; no ssh-agent is visible to the test runner."
    )
    auth_hint = (
        f"Configured key: {ssh_key}"
        if ssh_key
        else "No explicit SSH key configured; relying on ssh-agent or default ~/.ssh/id_* keys."
    )
    return (
        f"Failed to open SSH connection to {user}@{host}.\n"
        f"{auth_hint}\n"
        f"{agent_hint}\n"
        "Fix one of the following before rerunning the live phase tests:\n"
        "1. Configure a working SSH key path in tests/.env (for example SPARK_SSH_KEY or MAC_SSH_KEY).\n"
        "2. Start ssh-agent and load the correct key with ssh-add.\n"
        "3. Verify the host accepts non-interactive SSH: ssh -o BatchMode=yes <host> true\n"
        f"Original error: {exc}"
    )


def _make_connection(host: str, user: str, ssh_key: str | None, timeout: float = 10) -> Connection:
    """Build a Fabric Connection with a 10-second connect timeout.

    Args:
        host: Hostname or IP address of the target machine.
        user: SSH username.
        ssh_key: Path to the private key file, or ``None`` to rely on the SSH
            agent / default key.

    Returns:
        An open :class:`fabric.Connection` instance.
    """
    connect_kwargs: dict = {"timeout": timeout}
    if ssh_key:
        connect_kwargs["key_filename"] = str(ssh_key)

    conn = Connection(
        host=host,
        user=user,
        connect_kwargs=connect_kwargs,
    )
    # Eagerly open the transport so failures surface at fixture setup time
    # rather than mid-test.
    try:
        conn.open()
    except Exception as exc:  # pragma: no cover - exercised in live environments
        raise RuntimeError(_format_ssh_failure(host, user, ssh_key, exc)) from exc
    return conn


def _require_enabled(enabled: bool, label: str) -> None:
    """Skip fixtures and tests for optional topology components when disabled."""
    if not enabled:
        pytest.skip(
            f"{label} host is disabled in the active test topology. "
            f"Enable it in tests/.env if you want to validate that optional component."
        )


def pytest_collection_modifyitems(config, items) -> None:
    """Skip optional Pi-only tests when the Pi topology is disabled."""
    settings = TestSettings()
    if settings.pi.enabled:
        return

    skip_pi = pytest.mark.skip(
        reason=(
            "Raspberry Pi topology is disabled in the active test configuration. "
            "Set PI_ENABLED=true in tests/.env to run Pi-specific validation."
        )
    )
    for item in items:
        if (
            "tests/phase3_pi/" in item.nodeid
            or "tests/phase0_preflight/test_pi_prerequisites.py" in item.nodeid
        ):
            item.add_marker(skip_pi)


# ---------------------------------------------------------------------------
# SSH connection fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def spark_ssh(test_settings: TestSettings):
    """Open Fabric SSH connection to the DGX Spark (session-scoped).

    Yields the connection and guarantees it is closed in teardown even if
    tests raise exceptions.
    """
    s = test_settings.spark
    _require_enabled(s.enabled, "Spark")
    conn = _make_connection(
        host=s.host,
        user=s.user,
        ssh_key=s.ssh_key,
        timeout=test_settings.ssh_connect_timeout,
    )
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture(scope="session")
def mac_ssh(test_settings: TestSettings):
    """Open Fabric SSH connection to the Mac Studio (session-scoped).

    Yields the connection and guarantees it is closed in teardown.
    """
    s = test_settings.mac
    _require_enabled(s.enabled, "Mac Studio")
    conn = _make_connection(
        host=s.host,
        user=s.user,
        ssh_key=s.ssh_key,
        timeout=test_settings.ssh_connect_timeout,
    )
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture(scope="session")
def pi_ssh(test_settings: TestSettings):
    """Open Fabric SSH connection to the Raspberry Pi (session-scoped).

    Yields the connection and guarantees it is closed in teardown.
    """
    s = test_settings.pi
    _require_enabled(s.enabled, "Raspberry Pi")
    conn = _make_connection(
        host=s.host,
        user=s.user,
        ssh_key=s.ssh_key,
        timeout=test_settings.ssh_connect_timeout,
    )
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# IP / URL convenience fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def spark_ip(test_settings: TestSettings) -> str:
    """Return the primary IP address of the DGX Spark."""
    return test_settings.spark.ip


@pytest.fixture(scope="session")
def mac_ip(test_settings: TestSettings) -> str:
    """Return the primary IP address of the Mac Studio."""
    return test_settings.mac.ip


@pytest.fixture(scope="session")
def pi_ip(test_settings: TestSettings) -> str:
    """Return the primary IP address of the Raspberry Pi."""
    _require_enabled(test_settings.pi.enabled, "Raspberry Pi")
    return test_settings.pi.ip


@pytest.fixture(scope="session")
def spark_ollama_url(spark_ip: str) -> str:
    """Return the base URL for the Ollama API on the DGX Spark.

    Example: ``"http://10.0.0.10:11434"``
    """
    return f"http://{spark_ip}:11434"


@pytest.fixture(scope="session")
def spark_tailscale_ip(test_settings: TestSettings) -> str:
    """Return the Tailscale IP of the DGX Spark (100.x.x.x range)."""
    return test_settings.spark.tailscale_ip


@pytest.fixture(scope="session")
def spark_remote_ui_url(test_settings: TestSettings) -> str | None:
    """Return the preferred remote UI URL for Spark when one is configured."""
    return test_settings.spark_remote_ui_url


# ---------------------------------------------------------------------------
# testinfra host fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def spark_host(spark_ssh: Connection):
    """Return a testinfra host object backed by the Spark Fabric connection.

    testinfra's ``get_host`` accepts a Fabric connection via the
    ``"local"`` backend when passed directly; for SSH-backed hosts we use the
    ``paramiko`` backend with the existing transport to avoid re-authenticating.
    """
    import testinfra

    host = testinfra.get_host(
        f"paramiko://{spark_ssh.host}",
        ssh_config=None,
        ssh_identity_file=spark_ssh.connect_kwargs.get("key_filename"),
    )
    return host


@pytest.fixture(scope="session")
def mac_host(mac_ssh: Connection):
    """Return a testinfra host object backed by the Mac Fabric connection."""
    import testinfra

    host = testinfra.get_host(
        f"paramiko://{mac_ssh.host}",
        ssh_config=None,
        ssh_identity_file=mac_ssh.connect_kwargs.get("key_filename"),
    )
    return host


@pytest.fixture(scope="session")
def pi_host(pi_ssh: Connection):
    """Return a testinfra host object backed by the Pi Fabric connection."""
    import testinfra

    host = testinfra.get_host(
        f"paramiko://{pi_ssh.host}",
        ssh_config=None,
        ssh_identity_file=pi_ssh.connect_kwargs.get("key_filename"),
    )
    return host


# ---------------------------------------------------------------------------
# Prereq gathering helpers
# ---------------------------------------------------------------------------


def _cmd(conn: Connection, cmd: str, timeout: int = 30) -> CommandResult:
    """Thin wrapper around run_remote for internal prereq gathering."""
    return run_remote(conn, cmd, timeout=timeout)


def _stdout(result: CommandResult) -> str:
    """Return stripped stdout from a CommandResult."""
    return result.stdout.strip()


def _ok(result: CommandResult) -> bool:
    """Return True when a command exited with return code 0."""
    return result.return_code == 0


# ---------------------------------------------------------------------------
# SparkPrereqs fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def spark_prereqs(spark_ssh: Connection) -> SparkPrereqs:
    """Gather all prerequisite state from the DGX Spark over SSH.

    Runs a series of diagnostic commands and parses their output into a
    :class:`~tests.models.SparkPrereqs` Pydantic model so that individual
    test assertions operate on typed fields rather than raw strings.
    """
    conn = spark_ssh

    # Docker version — "Docker version 28.0.4, build abcdef"
    docker_ver_result = _cmd(conn, "docker --version 2>&1")
    raw_docker = _stdout(docker_ver_result)
    # Extract version token: "28.0.4"
    import re as _re

    docker_version_match = _re.search(r"(\d+\.\d+[\.\d]*)", raw_docker)
    docker_version = docker_version_match.group(1) if docker_version_match else ""

    # Docker running: "docker info" exits 0 when daemon is up
    docker_running_result = _cmd(conn, "docker info > /dev/null 2>&1 && echo OK || echo FAIL")
    docker_running = "OK" in _stdout(docker_running_result)

    # Ollama version — "ollama version 0.6.0"
    ollama_ver_result = _cmd(conn, "ollama --version 2>&1")
    raw_ollama = _stdout(ollama_ver_result)
    ollama_version_match = _re.search(r"(\d+\.\d+[\.\d]*)", raw_ollama)
    ollama_version = ollama_version_match.group(1) if ollama_version_match else ""

    # Models available — parse "ollama list" output (name is the first column)
    ollama_list_result = _cmd(conn, "ollama list 2>&1", timeout=60)
    models_available: list[str] = []
    for line in _stdout(ollama_list_result).splitlines():
        parts = line.split()
        if parts and parts[0] not in ("NAME", "name") and ":" in parts[0]:
            models_available.append(parts[0])

    # Disk free on / in GB
    df_result = _cmd(conn, "df -BG / | tail -1 2>&1")
    disk_free_gb = 0.0
    df_line = _stdout(df_result)
    df_parts = df_line.split()
    if len(df_parts) >= 4:
        # Column 4 (index 3) is "Available" — strip trailing "G"
        avail_str = df_parts[3].rstrip("G")
        try:
            disk_free_gb = float(avail_str)
        except ValueError:
            disk_free_gb = 0.0

    # Free inodes on /
    inode_result = _cmd(conn, "df -i / | awk 'NR==2 {print $4}' 2>&1")
    try:
        disk_inodes_free = int(_stdout(inode_result))
    except ValueError:
        disk_inodes_free = 0

    # Node.js version — "v22.4.0"
    node_result = _cmd(conn, "node --version 2>&1")
    raw_node = _stdout(node_result)
    node_version_match = _re.search(r"(\d+\.\d+[\.\d]*)", raw_node)
    node_version = node_version_match.group(1) if node_version_match else ""

    # npm version
    npm_result = _cmd(conn, "npm --version 2>&1")
    raw_npm = _stdout(npm_result)
    npm_version_match = _re.search(r"(\d+\.\d+[\.\d]*)", raw_npm)
    npm_version = npm_version_match.group(1) if npm_version_match else ""

    # Landlock: check kernel LSM list
    landlock_result = _cmd(conn, "cat /sys/kernel/security/lsm 2>&1")
    landlock_supported = "landlock" in _stdout(landlock_result).lower()

    # seccomp: check kernel config or /proc
    seccomp_result = _cmd(
        conn,
        "grep -c CONFIG_SECCOMP=y /boot/config-$(uname -r) 2>/dev/null "
        "|| grep -c seccomp /proc/filesystems 2>/dev/null "
        "|| echo 0",
    )
    seccomp_supported = _stdout(seccomp_result) not in ("0", "")

    # cgroup v2: unified hierarchy means /sys/fs/cgroup is cgroup2
    cgroup_result = _cmd(conn, "findmnt -n -o FSTYPE /sys/fs/cgroup 2>&1")
    cgroup_v2 = "cgroup2" in _stdout(cgroup_result).lower()

    # Tailscale connected: "tailscale status" exits 0 and shows an IP
    tailscale_result = _cmd(conn, "tailscale status 2>&1", timeout=15)
    tailscale_connected = _ok(tailscale_result) and _stdout(tailscale_result) not in (
        "",
        "Tailscale is stopped.",
    )
    tailscale_ip_result = _cmd(conn, "tailscale ip -4 2>/dev/null | head -1", timeout=15)
    tailscale_ip = _stdout(tailscale_ip_result) or None

    return SparkPrereqs(
        docker_version=docker_version,
        docker_running=docker_running,
        ollama_version=ollama_version,
        models_available=models_available,
        disk_free_gb=disk_free_gb,
        disk_inodes_free=disk_inodes_free,
        node_version=node_version,
        npm_version=npm_version,
        landlock_supported=landlock_supported,
        seccomp_supported=seccomp_supported,
        cgroup_v2=cgroup_v2,
        tailscale_connected=tailscale_connected,
        tailscale_ip=tailscale_ip,
    )


# ---------------------------------------------------------------------------
# MacPrereqs fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def mac_prereqs(mac_ssh: Connection) -> MacPrereqs:
    """Gather all prerequisite state from the Mac Studio over SSH.

    Runs diagnostic commands and parses the output into a
    :class:`~tests.models.MacPrereqs` Pydantic model.
    """
    import re as _re

    conn = mac_ssh

    # Ollama version
    ollama_result = _cmd(conn, "ollama --version 2>&1")
    raw_ollama = _stdout(ollama_result)
    ollama_match = _re.search(r"(\d+\.\d+[\.\d]*)", raw_ollama)
    ollama_version = ollama_match.group(1) if ollama_match else ""
    if not ollama_version:
        ollama_api_version_result = _cmd(
            conn,
            "curl -fsS http://127.0.0.1:11434/api/version 2>/dev/null",
            timeout=15,
        )
        ollama_api_version_match = _re.search(
            r'"version"\s*:\s*"(\d+\.\d+[\.\d]*)"',
            _stdout(ollama_api_version_result),
        )
        if ollama_api_version_match:
            ollama_version = ollama_api_version_match.group(1)

    # Models available on Mac
    ollama_list_result = _cmd(conn, "ollama list 2>&1", timeout=60)
    mac_models: list[str] = []
    for line in _stdout(ollama_list_result).splitlines():
        parts = line.split()
        if parts and parts[0] not in ("NAME", "name") and ":" in parts[0]:
            mac_models.append(parts[0])
    if not mac_models:
        ollama_tags_result = _cmd(
            conn, "curl -fsS http://127.0.0.1:11434/api/tags 2>/dev/null", timeout=30
        )
        tags_output = _stdout(ollama_tags_result)
        for match in _re.finditer(r'"name"\s*:\s*"([^"]+)"', tags_output):
            mac_models.append(match.group(1))

    # Ollama API listening on localhost
    ollama_listening_result = _cmd(
        conn,
        "curl -fsS http://127.0.0.1:11434/api/version >/dev/null 2>&1 && echo OK || echo FAIL",
        timeout=15,
    )
    ollama_listening = _stdout(ollama_listening_result) == "OK"

    # Tailscale connected
    tailscale_result = _cmd(conn, "tailscale status 2>&1", timeout=15)
    tailscale_connected = _ok(tailscale_result) and _stdout(tailscale_result) not in (
        "",
        "Tailscale is stopped.",
    )
    tailscale_ip_result = _cmd(conn, "tailscale ip -4 2>/dev/null | head -1", timeout=15)
    tailscale_ip = _stdout(tailscale_ip_result) or None

    # launchd service state for Ollama
    launchd_result = _cmd(
        conn,
        "launchctl list 2>/dev/null | grep -q 'com\\.ollama\\.ollama' && echo OK || echo FAIL",
        timeout=15,
    )
    launchd_ollama_active = _stdout(launchd_result) == "OK"

    return MacPrereqs(
        ollama_version=ollama_version,
        ollama_listening=ollama_listening,
        models_available=mac_models,
        tailscale_connected=tailscale_connected,
        tailscale_ip=tailscale_ip,
        launchd_ollama_active=launchd_ollama_active,
    )


# ---------------------------------------------------------------------------
# PiPrereqs fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def pi_prereqs(pi_ssh: Connection) -> PiPrereqs:
    """Gather all prerequisite state from the Raspberry Pi over SSH.

    Runs diagnostic commands and parses the output into a
    :class:`~tests.models.PiPrereqs` Pydantic model.
    """
    import re as _re

    conn = pi_ssh

    # Python3 version — "Python 3.11.2"
    python_result = _cmd(conn, "python3 --version 2>&1")
    raw_python = _stdout(python_result)
    python_match = _re.search(r"(\d+\.\d+[\.\d]*)", raw_python)
    python3_version = python_match.group(1) if python_match else ""

    # Free RAM in MB: parse "free -m" output, second line (Mem:), column 4 (available)
    free_result = _cmd(conn, "free -m 2>&1")
    free_ram_mb = 0
    for line in _stdout(free_result).splitlines():
        parts = line.split()
        if parts and parts[0].lower() == "mem:":
            try:
                # Index 6 is "available" column; fallback to index 3 (free)
                if len(parts) >= 7:
                    free_ram_mb = int(parts[6])
                elif len(parts) >= 4:
                    free_ram_mb = int(parts[3])
            except (ValueError, IndexError):
                free_ram_mb = 0
            break

    # Tailscale connected
    tailscale_result = _cmd(conn, "tailscale status 2>&1", timeout=15)
    tailscale_connected = _ok(tailscale_result) and _stdout(tailscale_result) not in (
        "",
        "Tailscale is stopped.",
    )

    # Disk free on / in GB (Pi storage can be tight)
    df_result = _cmd(conn, "df -BG / | tail -1 2>&1")
    disk_free_gb = 0.0
    df_parts = _stdout(df_result).split()
    if len(df_parts) >= 4:
        avail_str = df_parts[3].rstrip("G")
        try:
            disk_free_gb = float(avail_str)
        except ValueError:
            disk_free_gb = 0.0

    # pip3 / pip available
    pip_result = _cmd(conn, "pip3 --version 2>&1 || pip --version 2>&1")
    pip_available = _ok(pip_result) or "pip" in _stdout(pip_result)

    return PiPrereqs(
        python3_version=python3_version,
        free_ram_mb=free_ram_mb,
        tailscale_connected=tailscale_connected,
        disk_free_gb=disk_free_gb,
        pip_available=pip_available,
    )
