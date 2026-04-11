"""
Phase 4 — Coding Agent Sandboxes: OpenClaw configuration integrity tests.

Validates that the ``.openclaw`` directory inside the main sandbox is protected
against tampering by the sandbox user.  A writable ``.openclaw`` would allow an
agent to hijack its own gateway configuration — rewriting the model list,
changing the gateway bind address, or disabling auth entirely.

These tests verify filesystem ownership and permissions on the configuration
directory and its contents, and check that no symlink attacks have been staged.

Markers
-------
phase4    : All tests here belong to Phase 4 (Coding Agent Sandboxes).
contract  : Layer A — structural assertion about filesystem permissions.

Fixtures (from conftest.py)
---------------------------
spark_ssh : fabric.Connection — live SSH connection to the DGX Spark node.
"""

from __future__ import annotations

import contextlib

import pytest
from fabric import Connection

from ..models import CommandResult
from ._openshell_cli import run_sandbox_command

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SANDBOX: str = "nemoclaw-main"
_OPENCLAW_DIR: str = "/sandbox/.openclaw"
_OPENCLAW_JSON: str = f"{_OPENCLAW_DIR}/openclaw.json"


# ---------------------------------------------------------------------------
# Contract tests — .openclaw integrity
# ---------------------------------------------------------------------------


@pytest.mark.phase4
@pytest.mark.contract
class TestOpenClawIntegrity:
    """Layer A: .openclaw config directory is read-only to the sandbox user.

    The .openclaw directory holds the OpenClaw gateway configuration including
    model definitions, auth tokens, and channel secrets.  If the sandbox user
    (which runs agent-generated code) can write to this directory, it can
    escalate privileges by modifying the gateway config at will.

    All four tests are structural: they inspect filesystem metadata rather than
    triggering runtime behaviour.
    """

    def test_openclaw_dir_not_writable_by_sandbox_user(self, spark_ssh: Connection) -> None:
        """The sandbox user cannot create files inside .openclaw/.

        Attempts to ``touch`` a new file inside the .openclaw directory from
        within the sandbox container.  Under correct permissions, the touch
        command should fail with a permission-denied error (non-zero exit).

        The test file is cleaned up unconditionally in case the touch
        unexpectedly succeeds (indicating a real vulnerability).
        """
        test_file = f"{_OPENCLAW_DIR}/nemoclaw_integrity_test"

        result: CommandResult = run_sandbox_command(
            spark_ssh,
            _SANDBOX,
            f"sh -c 'touch {test_file} 2>&1; echo EXIT:$?'",
            timeout=15,
        )
        assert result.return_code == 0, (
            f"Could not execute the integrity check inside {_SANDBOX!r}. stderr: {result.stderr!r}"
        )
        combined = result.stdout + " " + result.stderr

        # Extract the exit code embedded via echo EXIT:$?
        exit_code: int | None = None
        for part in combined.split():
            if part.startswith("EXIT:"):
                with contextlib.suppress(ValueError):
                    exit_code = int(part.split(":", 1)[1])
                break

        # Clean up in case touch succeeded (vulnerability present)
        run_sandbox_command(spark_ssh, _SANDBOX, f"rm -f {test_file}", timeout=10)

        assert exit_code is not None and exit_code != 0, (
            f"Sandbox user in {_SANDBOX!r} was able to create a file inside "
            f"{_OPENCLAW_DIR!r} (exit code: {exit_code}). "
            "This means the .openclaw directory is writable by agent code, "
            "allowing config hijacking. "
            "Fix: ensure .openclaw is owned by root and mode 0755 or stricter, "
            "with files inside set to mode 0644 or read-only."
        )

    def test_openclaw_dir_owned_by_root(self, spark_ssh: Connection) -> None:
        """The .openclaw directory is owned by root, not the sandbox user.

        Uses ``stat -c '%U'`` to check the owner of the directory.  Root
        ownership combined with restrictive permissions prevents the sandbox
        user from modifying directory entries (renaming, deleting, or creating
        config files).
        """
        result: CommandResult = run_sandbox_command(
            spark_ssh,
            _SANDBOX,
            f"stat -c '%U' {_OPENCLAW_DIR} 2>&1",
            timeout=15,
        )
        assert result.return_code == 0, (
            f"Could not read ownership for {_OPENCLAW_DIR!r} inside {_SANDBOX!r}. "
            f"stderr: {result.stderr!r}"
        )
        owner = result.stdout.strip()

        assert owner == "root", (
            f"{_OPENCLAW_DIR!r} in {_SANDBOX!r} is owned by {owner!r}, expected 'root'. "
            "Non-root ownership allows the sandbox user to modify the directory "
            "and its contents. "
            "Fix: chown root:root on the .openclaw directory in the container image "
            "or entrypoint script."
        )

    def test_openclaw_json_not_writable(self, spark_ssh: Connection) -> None:
        """openclaw.json is not writable by the sandbox user.

        Uses ``test -w`` to check write permission from the perspective of the
        sandbox user.  The echo trick (``WRITABLE`` vs ``PROTECTED``) avoids
        relying on exit code interpretation across different shell
        implementations.
        """
        result: CommandResult = run_sandbox_command(
            spark_ssh,
            _SANDBOX,
            f"sh -c 'test -w {_OPENCLAW_JSON} && echo WRITABLE || echo PROTECTED'",
            timeout=15,
        )
        assert result.return_code == 0, (
            f"Could not check writability for {_OPENCLAW_JSON!r} inside {_SANDBOX!r}. "
            f"stderr: {result.stderr!r}"
        )
        status = result.stdout.strip().upper()

        assert status == "PROTECTED", (
            f"{_OPENCLAW_JSON!r} is writable by the sandbox user in {_SANDBOX!r}. "
            "An agent could overwrite the OpenClaw gateway configuration, "
            "changing model routes, disabling auth, or injecting malicious "
            "channel handlers. "
            "Fix: chmod 644 or 444 on openclaw.json, owned by root."
        )

    def test_openclaw_symlink_not_present(self, spark_ssh: Connection) -> None:
        """The .openclaw path is a real directory, not a symlink.

        A symlink at ``.openclaw`` could redirect config reads/writes to an
        attacker-controlled location.  For example, if the sandbox user creates
        a symlink pointing ``.openclaw`` to ``/tmp/evil-config/``, the gateway
        would load attacker-supplied configuration on next restart.

        This test verifies that ``.openclaw`` is not a symbolic link.
        """
        result: CommandResult = run_sandbox_command(
            spark_ssh,
            _SANDBOX,
            f"sh -c 'test -L {_OPENCLAW_DIR} && echo SYMLINK || echo REAL'",
            timeout=15,
        )
        assert result.return_code == 0, (
            f"Could not check symlink status for {_OPENCLAW_DIR!r} inside {_SANDBOX!r}. "
            f"stderr: {result.stderr!r}"
        )
        status = result.stdout.strip().upper()

        assert status == "REAL", (
            f"{_OPENCLAW_DIR!r} in {_SANDBOX!r} is a symbolic link. "
            "This is a potential symlink attack vector — the gateway may load "
            "configuration from an attacker-controlled path. "
            "Fix: remove the symlink and replace with a real directory owned by root."
        )
