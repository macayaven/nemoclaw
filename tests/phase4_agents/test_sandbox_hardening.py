"""
Phase 4 — Coding Agent Sandboxes: Container-level security hardening tests.

Validates that sandbox containers are configured with defence-in-depth security
controls matching the official NemoClaw hardening guide:
- ``no-new-privileges`` prevents setuid/setgid escalation.
- All Linux capabilities are dropped (defence against kernel exploits).
- Read-only root filesystem limits persistence and lateral movement.
- Process limits (nproc ulimit) prevent fork bombs.
- A seccomp profile restricts available system calls.

Each test inspects the live sandbox process state over the supported
OpenShell SSH bridge rather than attempting runtime exploitation, making them
fast and deterministic.

Markers
-------
phase4    : All tests here belong to Phase 4 (Coding Agent Sandboxes).
contract  : Layer A — structural assertion about container configuration.

Fixtures (from conftest.py)
---------------------------
spark_ssh : fabric.Connection — live SSH connection to the DGX Spark node.
"""

from __future__ import annotations

import re

import pytest
from fabric import Connection

from ..models import CommandResult
from ._openshell_cli import run_sandbox_command

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ALL_SANDBOXES: list[str] = ["nemoclaw-main", "claude-dev", "codex-dev", "gemini-dev"]


def _status_value(output: str, key: str) -> str | None:
    """Extract a ``/proc/self/status`` field value."""
    pattern = re.compile(rf"^{re.escape(key)}:\s*(.+)$", re.MULTILINE)
    match = pattern.search(output)
    return match.group(1).strip() if match else None


# ---------------------------------------------------------------------------
# Contract tests — sandbox hardening
# ---------------------------------------------------------------------------


@pytest.mark.phase4
@pytest.mark.contract
class TestSandboxHardening:
    """Layer A: Container security controls match the hardening baseline.

    These tests are parametrized across all four NemoClaw sandboxes to ensure
    uniform security posture.  Each test reads in-sandbox kernel and mount
    state and asserts the expected security controls are present.
    """

    @pytest.fixture(params=_ALL_SANDBOXES)
    def sandbox_name(self, request: pytest.FixtureRequest) -> str:
        """Yield each sandbox name as a test parameter."""
        return request.param

    def test_no_new_privileges(self, spark_ssh: Connection, sandbox_name: str) -> None:
        """The sandbox process runs with ``NoNewPrivs`` enabled."""
        result: CommandResult = run_sandbox_command(
            spark_ssh,
            sandbox_name,
            "grep -E '^(NoNewPrivs|Seccomp):' /proc/self/status",
            timeout=15,
        )
        assert result.return_code == 0, (
            f"Could not read security status inside {sandbox_name!r}. stderr: {result.stderr!r}"
        )
        no_new_privs = _status_value(result.stdout, "NoNewPrivs")
        seccomp = _status_value(result.stdout, "Seccomp")

        assert no_new_privs == "1", (
            f"Sandbox {sandbox_name!r} is not running with NoNewPrivs=1. "
            f"/proc/self/status:\n{result.stdout}"
        )
        assert seccomp == "2", (
            f"Sandbox {sandbox_name!r} is not running with seccomp filter mode. "
            f"/proc/self/status:\n{result.stdout}"
        )

    def test_capabilities_dropped(self, spark_ssh: Connection, sandbox_name: str) -> None:
        """All Linux capability sets are empty inside the sandbox process."""
        result: CommandResult = run_sandbox_command(
            spark_ssh,
            sandbox_name,
            "grep -E '^(CapPrm|CapEff|CapBnd):' /proc/self/status",
            timeout=15,
        )
        assert result.return_code == 0, (
            f"Could not read capability status inside {sandbox_name!r}. stderr: {result.stderr!r}"
        )

        for key in ("CapPrm", "CapEff", "CapBnd"):
            value = _status_value(result.stdout, key)
            assert value is not None, (
                f"{key} is missing from /proc/self/status inside {sandbox_name!r}. "
                f"Output:\n{result.stdout}"
            )
            assert set(value.replace("0x", "").strip()) <= {"0"}, (
                f"Sandbox {sandbox_name!r} still has capability bits set in {key}: {value!r}. "
                f"/proc/self/status:\n{result.stdout}"
            )

    def test_read_only_rootfs(self, spark_ssh: Connection, sandbox_name: str) -> None:
        """The sandbox root filesystem is mounted read-only.

        ``nemoclaw-main`` may keep a writable rootfs for runtime state, so it
        remains an explicit exception if the live deployment chooses that
        model. The other sandboxes must be read-only.
        """
        result: CommandResult = run_sandbox_command(
            spark_ssh,
            sandbox_name,
            "findmnt -no OPTIONS /",
            timeout=15,
        )
        readonly = result.stdout.strip().lower()

        if (
            "ro" not in {opt.strip() for opt in readonly.split(",") if opt.strip()}
            and sandbox_name == "nemoclaw-main"
        ):
            pytest.skip(
                f"nemoclaw-main has root mount options {readonly!r}. "
                "OpenClaw may require a writable rootfs for runtime state."
            )

        assert "ro" in {opt.strip() for opt in readonly.split(",") if opt.strip()}, (
            f"Sandbox {sandbox_name!r} root filesystem is not mounted read-only. "
            f"Mount options: {result.stdout.strip()!r}. "
            "Fix the sandbox run configuration to mount the root filesystem read-only "
            "and confine writable paths to tmpfs or explicit volumes."
        )

    def test_process_limit(self, spark_ssh: Connection, sandbox_name: str) -> None:
        """A process limit (nproc ulimit) is set inside the sandbox."""
        result: CommandResult = run_sandbox_command(
            spark_ssh,
            sandbox_name,
            "bash -lc 'ulimit -u'",
            timeout=15,
        )
        assert result.return_code == 0, (
            f"Could not read process limit inside {sandbox_name!r}. stderr: {result.stderr!r}"
        )
        raw_limit = result.stdout.strip()
        assert raw_limit.isdigit(), (
            f"Unexpected nproc limit output inside {sandbox_name!r}: {raw_limit!r}. "
            "Expected a numeric ulimit -u value."
        )
        nproc_limit = int(raw_limit)
        assert nproc_limit <= 512, (
            f"Sandbox {sandbox_name!r} nproc limit is {nproc_limit}, expected <= 512. "
            "Reduce the sandbox process limit to protect against fork bombs."
        )

    def test_seccomp_profile_active(self, spark_ssh: Connection, sandbox_name: str) -> None:
        """A seccomp filter is active inside the sandbox process."""
        result: CommandResult = run_sandbox_command(
            spark_ssh,
            sandbox_name,
            "grep -E '^(Seccomp|Seccomp_filters):' /proc/self/status",
            timeout=15,
        )
        assert result.return_code == 0, (
            f"Could not read seccomp status inside {sandbox_name!r}. stderr: {result.stderr!r}"
        )
        seccomp = _status_value(result.stdout, "Seccomp")
        seccomp_filters = _status_value(result.stdout, "Seccomp_filters")

        assert seccomp == "2", (
            f"Sandbox {sandbox_name!r} is not running with seccomp filter mode. "
            f"/proc/self/status:\n{result.stdout}"
        )
        assert (
            seccomp_filters is not None and seccomp_filters.isdigit() and int(seccomp_filters) > 0
        ), (
            f"Sandbox {sandbox_name!r} has no active seccomp filters. "
            f"/proc/self/status:\n{result.stdout}"
        )
