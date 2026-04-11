"""
Phase 4 — Coding Agent Sandboxes: Multi-sandbox inventory, port-conflict, and
concurrent-access tests.

Validates the holistic sandbox deployment:
- All four expected sandboxes are present and running.
- No two sandboxes expose the same host-side port (which would prevent at
  least one from binding and would cause non-deterministic routing failures).
- Commands can be dispatched to multiple sandboxes concurrently without
  interference.

Markers
-------
phase4    : All tests here belong to Phase 4 (Coding Agent Sandboxes).
contract  : Layer A — structure, schema, and configuration assertions.
behavioral: Layer B — runtime / concurrency assertions.

Fixtures (from conftest.py)
---------------------------
spark_ssh : fabric.Connection — live SSH connection to the DGX Spark node.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import NamedTuple

import pytest
from fabric import Connection

from ..helpers import generate_unique_id, run_remote
from ..models import CommandResult
from ._openshell_cli import run_sandbox_command, strip_ansi

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EXPECTED_SANDBOXES: tuple[str, ...] = (
    "nemoclaw-main",
    "claude-dev",
    "codex-dev",
    "gemini-dev",
)

# Sandboxes to use for the concurrent-access test (avoids nemoclaw-main which
# may be doing real work).
_CONCURRENT_SANDBOXES: tuple[str, str] = ("claude-dev", "codex-dev")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class _SandboxPortInfo(NamedTuple):
    """Container name paired with its list of exposed host-side ports."""

    name: str
    ports: list[int]


def _parse_forward_list(output: str) -> list[_SandboxPortInfo]:
    """Extract sandbox names and forwarded ports from ``openshell forward list``.

    The current OpenShell CLI exposes forwarding state as a tabular list.
    This parser tolerates ANSI escapes and extra whitespace while requiring
    at least a sandbox name and a numeric port column.
    """
    clean = strip_ansi(output)
    entries: list[_SandboxPortInfo] = []

    for raw_line in clean.splitlines():
        line = raw_line.strip()
        if not line or line.lower().startswith("names"):
            continue

        parts = line.split()
        if len(parts) < 4 or not parts[2].isdigit():
            continue

        name_field = parts[0]
        ports = [int(parts[2])]
        entries.append(_SandboxPortInfo(name=name_field, ports=ports))

    return entries


# ---------------------------------------------------------------------------
# Contract tests — all sandboxes present and running
# ---------------------------------------------------------------------------


@pytest.mark.phase4
@pytest.mark.contract
class TestAllSandboxesRunning:
    """Layer A: Every expected sandbox container is present and running.

    Parametrises over all four sandbox names so that a failure message
    identifies exactly which sandbox is missing or stopped.
    """

    @pytest.mark.parametrize("sandbox_name", _EXPECTED_SANDBOXES)
    def test_four_sandboxes_exist(self, spark_ssh: Connection, sandbox_name: str) -> None:
        """Each of the four expected sandboxes is present and Ready.

        Queries the supported OpenShell sandbox metadata command and asserts
        that the descriptor reports ``Phase: Ready``.
        """
        result: CommandResult = run_remote(
            spark_ssh,
            f"openshell sandbox get {sandbox_name} 2>&1",
            timeout=15,
        )
        assert result.return_code == 0, (
            f"'openshell sandbox get {sandbox_name}' failed (exit {result.return_code}). "
            "The sandbox metadata may not exist or the gateway may be down. "
            f"Create it: openshell sandbox create {sandbox_name}. "
            f"stderr: {result.stderr!r}"
        )
        status = strip_ansi(result.stdout)
        assert f"Name: {sandbox_name}" in status, (
            f"Sandbox {sandbox_name!r} descriptor does not include the expected name.\n"
            f"Full output:\n{status}"
        )
        assert "Phase: Ready" in status, (
            f"Sandbox {sandbox_name!r} is not Ready in OpenShell.\nFull output:\n{status}"
        )


# ---------------------------------------------------------------------------
# Contract tests — no duplicate host port bindings
# ---------------------------------------------------------------------------


@pytest.mark.phase4
@pytest.mark.contract
class TestNoPortConflicts:
    """Layer A: No two sandboxes share the same forwarded host port.

    Port conflicts cause OpenShell to fail when binding the forward listener.
    These tests inspect the live forward table before the conflict becomes a
    runtime issue.
    """

    def test_unique_port_forwards(self, spark_ssh: Connection) -> None:
        """All host-side port forwards across the four sandboxes are unique.

        Reads the active OpenShell forward table and asserts that no forwarded
        host port appears more than once.
        """
        result: CommandResult = run_remote(spark_ssh, "openshell forward list 2>&1", timeout=20)
        assert result.return_code == 0, (
            f"'openshell forward list' failed (exit {result.return_code}). "
            "Cannot inspect active port forwards. "
            f"stderr: {result.stderr!r}"
        )
        sandbox_port_infos = _parse_forward_list(result.stdout)
        assert sandbox_port_infos, (
            "OpenShell did not report any active forwards. "
            "This usually means the gateway is not reachable or no sandboxes "
            "have active forward listeners."
        )

        # Build a mapping of port -> list[sandbox_name] to find conflicts.
        port_to_sandboxes: dict[int, list[str]] = {}
        for info in sandbox_port_infos:
            for port in info.ports:
                port_to_sandboxes.setdefault(port, []).append(info.name)

        conflicts = {port: owners for port, owners in port_to_sandboxes.items() if len(owners) > 1}

        assert not conflicts, (
            "Duplicate host-side port forwards detected across sandboxes. "
            "Conflicting ports:\n"
            + "\n".join(
                f"  port {port}: claimed by {', '.join(sorted(owners))}"
                for port, owners in sorted(conflicts.items())
            )
            + "\nResolve by reassigning unique ports in the OpenShell forward configuration."
        )


# ---------------------------------------------------------------------------
# Behavioral tests — concurrent access
# ---------------------------------------------------------------------------


@pytest.mark.phase4
@pytest.mark.behavioral
class TestConcurrentAccess:
    """Layer B: Multiple sandboxes can handle commands in parallel without interference.

    Uses a ``ThreadPoolExecutor`` to dispatch a simple ``echo`` command to two
    sandboxes simultaneously.  If the sandbox orchestration layer serialises
    all commands (e.g. via a global lock), this test will still pass — but it
    confirms that concurrent dispatch does not cause errors, hangs, or cross-
    contamination of output.
    """

    def test_parallel_sandbox_commands(self, spark_ssh: Connection) -> None:
        """Commands issued concurrently to claude-dev and codex-dev both succeed.

        Generates a unique token per sandbox, dispatches an ``echo`` command to
        both containers in parallel threads, and asserts that each thread
        received exactly its own token in stdout.  Cross-contaminated tokens
        would indicate output multiplexing bugs in the sandbox execution layer.
        """
        tokens: dict[str, str] = {name: generate_unique_id() for name in _CONCURRENT_SANDBOXES}
        results: dict[str, CommandResult] = {}
        errors: dict[str, Exception] = {}
        lock = threading.Lock()

        def _run_in_sandbox(sandbox_name: str, token: str) -> None:
            """Execute echo <token> inside the named sandbox and store result."""
            try:
                result = run_sandbox_command(
                    spark_ssh,
                    sandbox_name,
                    f"echo {token}",
                    timeout=20,
                )
                with lock:
                    results[sandbox_name] = result
            except Exception as exc:
                with lock:
                    errors[sandbox_name] = exc

        with ThreadPoolExecutor(max_workers=len(_CONCURRENT_SANDBOXES)) as executor:
            futures = {
                executor.submit(_run_in_sandbox, name, tokens[name]): name
                for name in _CONCURRENT_SANDBOXES
            }
            # Wait for all futures to complete (as_completed is used for early
            # error detection; results are collected via the shared dict above).
            for future in as_completed(futures):
                future.result()  # re-raise any thread-level exception

        # Report all errors first so the operator sees the full picture.
        if errors:
            error_lines = "\n".join(f"  {name}: {exc}" for name, exc in sorted(errors.items()))
            pytest.fail(f"Concurrent sandbox commands raised exceptions:\n{error_lines}")

        # Verify each sandbox returned its own unique token.
        for sandbox_name in _CONCURRENT_SANDBOXES:
            assert sandbox_name in results, (
                f"No result collected for sandbox {sandbox_name!r}. "
                "The concurrent dispatch may have dropped this sandbox's task."
            )
            cmd_result = results[sandbox_name]
            expected_token = tokens[sandbox_name]
            assert cmd_result.return_code == 0, (
                f"echo command inside {sandbox_name!r} returned exit code "
                f"{cmd_result.return_code}. "
                f"stderr: {cmd_result.stderr!r}"
            )
            assert expected_token in cmd_result.stdout, (
                f"Expected token {expected_token!r} not found in stdout of "
                f"sandbox {sandbox_name!r}. "
                "Output may have been routed to the wrong sandbox. "
                f"Actual stdout: {cmd_result.stdout!r}"
            )
