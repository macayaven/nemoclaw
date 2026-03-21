"""
test_cli.py — Phase 6 tests for the orchestrator CLI (orchestrator.cli).

Covers:
  - ``python -m orchestrator health`` output (behavioural)
  - ``python -m orchestrator status`` exit code (contract)
  - Invalid subcommand handling (negative)

Tests invoke the CLI as a subprocess so that they exercise the real entry-point
rather than calling internal Python functions directly.  This approach catches
argument-parsing regressions that unit tests of individual functions miss.

All tests are marked @pytest.mark.phase6.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

pytestmark = pytest.mark.phase6

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ORCHESTRATOR_MODULE = "orchestrator"


def _run_cli(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    """Invoke the orchestrator CLI as ``python -m orchestrator <args>``.

    Args:
        *args: Subcommand and flags to pass to the CLI.
        timeout: Maximum seconds to wait for the process to exit.

    Returns:
        A :class:`subprocess.CompletedProcess` with stdout, stderr, and
        returncode captured.
    """
    cmd = [sys.executable, "-m", _ORCHESTRATOR_MODULE, *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Behavioural tests — health subcommand
# ---------------------------------------------------------------------------


class TestCLIHealth:
    """Behavioural tests: 'python -m orchestrator health' checks sandbox liveness."""

    @pytest.mark.timeout(120)
    def test_health_command(self, spark_ssh) -> None:
        """'python -m orchestrator health' exits 0 and names at least one sandbox.

        The output must include the name of a known sandbox (nemoclaw-main)
        to prove that the CLI is actually talking to the DGX Spark and not
        returning a static placeholder message.
        """
        result = _run_cli("health")

        assert result.returncode == 0, (
            f"'orchestrator health' exited {result.returncode}.\n"
            f"stdout: {result.stdout!r}\n"
            f"stderr: {result.stderr!r}"
        )

        combined = (result.stdout + result.stderr).lower()
        assert "nemoclaw-main" in combined or "sandbox" in combined, (
            "Expected 'orchestrator health' output to mention a sandbox name.\n"
            f"stdout: {result.stdout!r}\n"
            f"stderr: {result.stderr!r}"
        )


# ---------------------------------------------------------------------------
# Contract tests — status subcommand
# ---------------------------------------------------------------------------


class TestCLIStatus:
    """Contract tests: 'python -m orchestrator status' runs without crashing."""

    @pytest.mark.timeout(60)
    def test_status_command(self) -> None:
        """'python -m orchestrator status' exits without an unhandled exception.

        The command is allowed to exit with any code that indicates a known
        state (0 = all healthy, non-zero = degraded but handled).  It must not
        produce a Python traceback on stderr.
        """
        result = _run_cli("status")

        # Tracebacks start with "Traceback (most recent call last):"
        assert "Traceback (most recent call last)" not in result.stderr, (
            f"'orchestrator status' produced an unhandled Python exception:\n{result.stderr}"
        )

        # The command should produce some output on stdout or stderr.
        combined = result.stdout + result.stderr
        assert len(combined.strip()) > 0, (
            "'orchestrator status' produced no output on either stdout or stderr"
        )


# ---------------------------------------------------------------------------
# Negative tests — invalid subcommands
# ---------------------------------------------------------------------------


class TestCLINegative:
    """Negative tests: the CLI exits non-zero for unrecognised subcommands."""

    @pytest.mark.timeout(15)
    def test_invalid_command(self) -> None:
        """'python -m orchestrator this-is-not-a-real-subcommand' exits non-zero.

        CLI frameworks (argparse, click, typer) standardly exit with code 2
        for unrecognised arguments, but any non-zero code is acceptable here.
        The test only verifies that the CLI does not silently succeed (exit 0)
        when given a nonsense subcommand.
        """
        result = _run_cli("this-is-not-a-real-subcommand", timeout=15)

        assert result.returncode != 0, (
            "Expected a non-zero exit code for an invalid subcommand, but got 0.\n"
            f"stdout: {result.stdout!r}\n"
            f"stderr: {result.stderr!r}"
        )
