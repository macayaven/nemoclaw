"""
Phase 4 — Coding Agent Sandboxes: Container-level security hardening tests.

Validates that sandbox containers are configured with defence-in-depth security
controls matching the official NemoClaw hardening guide:
- ``no-new-privileges`` prevents setuid/setgid escalation.
- All Linux capabilities are dropped (defence against kernel exploits).
- Read-only root filesystem limits persistence and lateral movement.
- Process limits (nproc ulimit) prevent fork bombs.
- A seccomp profile restricts available system calls.

Each test inspects container configuration via ``docker inspect`` rather than
attempting runtime exploitation, making them fast and deterministic.

Markers
-------
phase4    : All tests here belong to Phase 4 (Coding Agent Sandboxes).
contract  : Layer A — structural assertion about container configuration.

Fixtures (from conftest.py)
---------------------------
spark_ssh : fabric.Connection — live SSH connection to the DGX Spark node.
"""

from __future__ import annotations

import json

import pytest
from fabric import Connection

from ..helpers import run_remote
from ..models import CommandResult

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ALL_SANDBOXES: list[str] = ["nemoclaw-main", "claude-dev", "codex-dev", "gemini-dev"]


# ---------------------------------------------------------------------------
# Contract tests — sandbox hardening
# ---------------------------------------------------------------------------


@pytest.mark.phase4
@pytest.mark.contract
class TestSandboxHardening:
    """Layer A: Container security controls match the hardening baseline.

    These tests are parametrized across all four NemoClaw sandboxes to ensure
    uniform security posture.  Each test uses ``docker inspect`` to read
    container configuration and asserts the expected security controls are
    present.
    """

    @pytest.fixture(params=_ALL_SANDBOXES)
    def sandbox_name(self, request: pytest.FixtureRequest) -> str:
        """Yield each sandbox name as a test parameter."""
        return request.param

    def test_no_new_privileges(self, spark_ssh: Connection, sandbox_name: str) -> None:
        """The ``no-new-privileges`` security option is set on the container.

        This flag prevents processes inside the container from gaining
        additional privileges via setuid/setgid binaries.  Without it, an
        agent could exploit a setuid binary to escalate to root inside the
        container.
        """
        result: CommandResult = run_remote(
            spark_ssh,
            f"docker inspect --format='{{{{.HostConfig.SecurityOpt}}}}' {sandbox_name} 2>&1",
            timeout=15,
        )
        security_opts = result.stdout.strip().lower()

        assert "no-new-privileges" in security_opts, (
            f"Container {sandbox_name!r} does not have 'no-new-privileges' set. "
            f"SecurityOpt: {result.stdout.strip()!r}. "
            "Fix: add --security-opt=no-new-privileges to the container run command "
            "or OpenShell sandbox creation config."
        )

    def test_capabilities_dropped(self, spark_ssh: Connection, sandbox_name: str) -> None:
        """All Linux capabilities are dropped, with minimal exceptions.

        The container should drop ALL capabilities (``CapDrop: [ALL]``) and
        only add back the absolute minimum needed (e.g. ``NET_BIND_SERVICE``
        for the main sandbox's gateway).  Running with default capabilities
        gives containers access to dangerous operations like ``CAP_SYS_ADMIN``
        which can be used for container escape.
        """
        # Check CapDrop
        drop_result: CommandResult = run_remote(
            spark_ssh,
            f"docker inspect --format='{{{{json .HostConfig.CapDrop}}}}' {sandbox_name} 2>&1",
            timeout=15,
        )
        drop_raw = drop_result.stdout.strip()

        try:
            cap_drop = json.loads(drop_raw) if drop_raw and drop_raw != "null" else []
        except json.JSONDecodeError:
            cap_drop = []

        assert "ALL" in [c.upper() for c in cap_drop] if cap_drop else False, (
            f"Container {sandbox_name!r} does not drop ALL capabilities. "
            f"CapDrop: {drop_raw!r}. "
            "Fix: add --cap-drop=ALL to the container creation command."
        )

        # Check CapAdd is minimal
        add_result: CommandResult = run_remote(
            spark_ssh,
            f"docker inspect --format='{{{{json .HostConfig.CapAdd}}}}' {sandbox_name} 2>&1",
            timeout=15,
        )
        add_raw = add_result.stdout.strip()

        try:
            cap_add = json.loads(add_raw) if add_raw and add_raw != "null" else []
        except json.JSONDecodeError:
            cap_add = []

        # Allow NET_BIND_SERVICE for nemoclaw-main (gateway binds port 18789)
        allowed_caps = {"NET_BIND_SERVICE"}
        if cap_add:
            unexpected = {c.upper() for c in cap_add} - allowed_caps
            assert not unexpected, (
                f"Container {sandbox_name!r} has unexpected capabilities added back: "
                f"{unexpected}. CapAdd: {add_raw!r}. "
                "Only NET_BIND_SERVICE is permitted. "
                "Fix: remove unnecessary --cap-add flags from the container creation."
            )

    def test_read_only_rootfs(self, spark_ssh: Connection, sandbox_name: str) -> None:
        """The container root filesystem is mounted read-only.

        A read-only rootfs prevents agent code from modifying system binaries,
        installing backdoors, or tampering with container configuration.
        Writable paths should be limited to explicit tmpfs mounts (e.g. /tmp,
        /run) and named volumes for workspace data.

        Note: nemoclaw-main may require a writable rootfs for OpenClaw's
        runtime state.  The test emits a warning (skip) rather than a hard
        failure for that sandbox.
        """
        result: CommandResult = run_remote(
            spark_ssh,
            f"docker inspect --format='{{{{.HostConfig.ReadonlyRootfs}}}}' {sandbox_name} 2>&1",
            timeout=15,
        )
        readonly = result.stdout.strip().lower()

        if readonly != "true" and sandbox_name == "nemoclaw-main":
            pytest.skip(
                f"nemoclaw-main has ReadonlyRootfs={readonly!r} — OpenClaw may "
                "require a writable rootfs for runtime state. Consider mounting "
                "specific writable paths via tmpfs instead."
            )

        assert readonly == "true", (
            f"Container {sandbox_name!r} does not have a read-only root filesystem "
            f"(ReadonlyRootfs={result.stdout.strip()!r}). "
            "An agent can modify system binaries or install persistent backdoors. "
            "Fix: add --read-only to the container run command and use tmpfs mounts "
            "for /tmp, /run, and the workspace volume."
        )

    def test_process_limit(self, spark_ssh: Connection, sandbox_name: str) -> None:
        """A process limit (nproc ulimit) is set to prevent fork bombs.

        Without a process limit, agent-generated code could spawn unlimited
        processes, consuming all PIDs on the host and causing a denial of
        service that affects all sandboxes and the gateway itself.
        """
        result: CommandResult = run_remote(
            spark_ssh,
            f"docker inspect --format='{{{{json .HostConfig.Ulimits}}}}' {sandbox_name} 2>&1",
            timeout=15,
        )
        ulimits_raw = result.stdout.strip()

        try:
            ulimits = json.loads(ulimits_raw) if ulimits_raw and ulimits_raw != "null" else []
        except json.JSONDecodeError:
            ulimits = []

        # Find the nproc ulimit
        nproc_limit = None
        if ulimits:
            for entry in ulimits:
                if isinstance(entry, dict) and entry.get("Name") == "nproc":
                    nproc_limit = entry.get("Hard", entry.get("Soft"))
                    break

        assert nproc_limit is not None, (
            f"Container {sandbox_name!r} has no nproc ulimit set. "
            f"Ulimits: {ulimits_raw!r}. "
            "Without a process limit, agent code can fork-bomb the host. "
            "Fix: add --ulimit nproc=512:512 to the container creation command."
        )

        assert nproc_limit <= 512, (
            f"Container {sandbox_name!r} nproc limit is {nproc_limit}, expected <= 512. "
            "A high process limit weakens fork-bomb protection. "
            "Fix: reduce --ulimit nproc to 512 or lower."
        )

    def test_seccomp_profile_active(self, spark_ssh: Connection, sandbox_name: str) -> None:
        """A seccomp profile is applied to the container (not ``unconfined``).

        Seccomp filters restrict the set of system calls available inside the
        container.  The ``unconfined`` profile disables this protection
        entirely, allowing dangerous syscalls like ``mount``, ``reboot``, and
        ``kexec_load`` that could be used for container escape.

        The default Docker seccomp profile is acceptable — it blocks ~44
        dangerous syscalls.  A custom NemoClaw-specific profile is even better
        but not required by this test.
        """
        result: CommandResult = run_remote(
            spark_ssh,
            f"docker inspect --format='{{{{.HostConfig.SecurityOpt}}}}' {sandbox_name} 2>&1",
            timeout=15,
        )
        security_opts = result.stdout.strip().lower()

        # "unconfined" in security opts means seccomp is explicitly disabled
        assert "seccomp=unconfined" not in security_opts, (
            f"Container {sandbox_name!r} has seccomp explicitly disabled "
            f"(seccomp=unconfined). SecurityOpt: {result.stdout.strip()!r}. "
            "This removes syscall filtering and increases the attack surface "
            "for container escape. "
            "Fix: remove --security-opt seccomp=unconfined from the container "
            "creation command. The default Docker seccomp profile is sufficient."
        )
