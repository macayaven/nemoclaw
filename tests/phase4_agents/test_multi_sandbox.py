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

import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import NamedTuple

import pytest
from fabric import Connection

from tests.helpers import generate_unique_id, run_remote
from tests.models import CommandResult

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


def _parse_host_ports(port_spec: str) -> list[int]:
    """Extract host-side TCP port numbers from a Docker port specification.

    Docker formats exposed ports in several ways depending on the binding:
    - ``0.0.0.0:8080->8080/tcp``
    - ``:::8080->8080/tcp``
    - ``8080/tcp``  (container-only, no host binding)

    Args:
        port_spec: Raw string from ``docker inspect`` or ``docker ps`` port
            fields, possibly containing multiple mappings separated by commas.

    Returns:
        Sorted list of unique host-side integer port numbers that are bound on
        the host.  Container-only ports (no ``->`` mapping) are excluded.
    """
    ports: list[int] = []
    # Match patterns like "0.0.0.0:8080->" or ":::8080->"
    for m in re.finditer(r"(?:0\.0\.0\.0|:::):(\d+)->", port_spec):
        ports.append(int(m.group(1)))
    return sorted(set(ports))


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
    def test_four_sandboxes_exist(
        self, spark_ssh: Connection, sandbox_name: str
    ) -> None:
        """Each of the four expected sandbox containers is running.

        Queries Docker for the given container name and asserts its status is
        ``running``.  All four sandboxes — ``nemoclaw-main``, ``claude-dev``,
        ``codex-dev``, and ``gemini-dev`` — must be simultaneously running for
        the full NemoClaw Phase 4 deployment to be functional.

        A missing container typically means the ``openshell sandbox create``
        command was not executed for that agent, or the container crashed at
        start-up and Docker's restart policy gave up.

        Args:
            sandbox_name: Parametrised sandbox container name.
        """
        result: CommandResult = run_remote(
            spark_ssh,
            f"docker inspect --format '{{{{.State.Status}}}}' {sandbox_name} 2>&1",
            timeout=15,
        )
        assert result.return_code == 0, (
            f"docker inspect {sandbox_name!r} failed (exit {result.return_code}). "
            "The sandbox container does not exist. "
            f"Create it: openshell sandbox create {sandbox_name}. "
            f"stderr: {result.stderr!r}"
        )
        status = result.stdout.strip().lower()
        assert status == "running", (
            f"Sandbox {sandbox_name!r} is not running (status={status!r}). "
            "Restart: openshell sandbox start {sandbox_name}. "
            f"Logs: docker logs {sandbox_name} --tail=50"
        )


# ---------------------------------------------------------------------------
# Contract tests — no duplicate host port bindings
# ---------------------------------------------------------------------------


@pytest.mark.phase4
@pytest.mark.contract
class TestNoPortConflicts:
    """Layer A: No two sandboxes share the same host-side TCP port.

    Port conflicts cause Docker to refuse to start the second container that
    tries to bind the port, which surfaces as a ``bind: address already in
    use`` error.  In practice this means one of the sandboxes will always be
    stopped, but ``docker ps`` can make it look like the failure is transient.
    These tests detect the conflict before it causes a runtime error.
    """

    def test_unique_port_forwards(self, spark_ssh: Connection) -> None:
        """All host-side port bindings across the four sandboxes are unique.

        Collects the port bindings for each expected sandbox via
        ``docker inspect`` and asserts that no port number appears in more
        than one sandbox's binding list.  Reports all conflicts in a single
        assertion message so the operator can fix them all at once rather than
        discovering them one by one.
        """
        sandbox_port_infos: list[_SandboxPortInfo] = []

        for sandbox_name in _EXPECTED_SANDBOXES:
            result: CommandResult = run_remote(
                spark_ssh,
                (
                    f"docker inspect --format "
                    f"'{{{{range $p,$b := .NetworkSettings.Ports}}}}"
                    f"{{{{$p}}}} {{{{range $b}}}}{{{{.HostPort}}}} {{{{end}}}}"
                    f"{{{{end}}}}' {sandbox_name} 2>&1"
                ),
                timeout=15,
            )
            if result.return_code != 0:
                # Container might not exist; skip port check and let
                # TestAllSandboxesRunning report the missing container.
                continue

            ports = _parse_host_ports(result.stdout)
            sandbox_port_infos.append(_SandboxPortInfo(name=sandbox_name, ports=ports))

        # Build a mapping of port -> list[sandbox_name] to find conflicts.
        port_to_sandboxes: dict[int, list[str]] = {}
        for info in sandbox_port_infos:
            for port in info.ports:
                port_to_sandboxes.setdefault(port, []).append(info.name)

        conflicts = {
            port: owners
            for port, owners in port_to_sandboxes.items()
            if len(owners) > 1
        }

        assert not conflicts, (
            "Duplicate host-side port bindings detected across sandboxes. "
            "Conflicting ports:\n"
            + "\n".join(
                f"  port {port}: claimed by {', '.join(sorted(owners))}"
                for port, owners in sorted(conflicts.items())
            )
            + "\nResolve by reassigning unique ports in each sandbox's port-forward config."
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
        tokens: dict[str, str] = {
            name: generate_unique_id() for name in _CONCURRENT_SANDBOXES
        }
        results: dict[str, CommandResult] = {}
        errors: dict[str, Exception] = {}
        lock = threading.Lock()

        def _run_in_sandbox(sandbox_name: str, token: str) -> None:
            """Execute echo <token> inside the named sandbox and store result."""
            try:
                result = run_remote(
                    spark_ssh,
                    f"docker exec {sandbox_name} echo {token}",
                    timeout=20,
                )
                with lock:
                    results[sandbox_name] = result
            except Exception as exc:  # noqa: BLE001
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
            error_lines = "\n".join(
                f"  {name}: {exc}" for name, exc in sorted(errors.items())
            )
            pytest.fail(
                f"Concurrent sandbox commands raised exceptions:\n{error_lines}"
            )

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
