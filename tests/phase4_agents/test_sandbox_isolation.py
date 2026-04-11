"""
Phase 4 — Coding Agent Sandboxes: Filesystem isolation, network isolation, and
egress-blocking tests.

Validates that the sandbox security model functions correctly at runtime:
- Files created inside one sandbox are not visible from another sandbox.
- Outbound connections to unapproved external IPs are blocked.
- Blocked connection attempts produce a log entry in the OpenShell audit log.

These tests are intentionally destructive toward the sandboxes' internal
state (creating temp files, triggering firewall blocks) but perform full
cleanup in teardown and leave host state unmodified.

Markers
-------
phase4    : All tests here belong to Phase 4 (Coding Agent Sandboxes).
behavioral: Layer B — runtime security and network enforcement tests.

Fixtures (from conftest.py)
---------------------------
spark_ssh : fabric.Connection — live SSH connection to the DGX Spark node.
"""

from __future__ import annotations

import pytest
from fabric import Connection

from ..helpers import curl_attempt_was_blocked, generate_unique_id, run_remote
from ..models import CommandResult
from ._openshell_cli import run_sandbox_command

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SANDBOX_A: str = "claude-dev"
_SANDBOX_B: str = "codex-dev"

# An external IP that the sandbox should never be able to reach under the
# default-deny egress policy.  8.8.8.8 (Google Public DNS) is chosen because
# it is reliably reachable from unrestricted hosts, making a timeout
# distinguishable from "the IP is genuinely down."
_BLOCKED_EXTERNAL_IP: str = "8.8.8.8"
_BLOCKED_EXTERNAL_URL: str = f"http://{_BLOCKED_EXTERNAL_IP}"

# Temp file directory inside sandboxes (writable in all NemoClaw sandbox images)
_SANDBOX_TEMP_DIR: str = "/tmp"


# ---------------------------------------------------------------------------
# Filesystem isolation
# ---------------------------------------------------------------------------


@pytest.mark.phase4
@pytest.mark.behavioral
class TestFilesystemIsolation:
    """Layer B: Filesystems of different sandboxes are isolated from one another.

    Each sandbox runs in its own Docker container with its own mount namespace.
    Files created inside one container must not be visible from another.
    Failure here would mean sandboxes share a volume unintentionally, allowing
    one agent to read or tamper with another agent's working files.
    """

    def test_file_created_in_one_not_visible_in_other(self, spark_ssh: Connection) -> None:
        """A file written inside claude-dev is not visible inside codex-dev.

        Procedure:
        1. Generate a globally unique filename using ``generate_unique_id()``.
        2. Write the file inside the ``claude-dev`` sandbox.
        3. Check whether the file path exists inside ``codex-dev``.
        4. Assert the file is absent in codex-dev.
        5. Clean up the file from claude-dev regardless of test outcome.

        The cleanup step runs unconditionally (via a try/finally equivalent)
        so that a test failure does not leave debris in the sandbox.
        """
        unique_id: str = generate_unique_id()
        temp_filename: str = f"nemoclaw_isolation_test_{unique_id}.txt"
        file_path_in_sandbox: str = f"{_SANDBOX_TEMP_DIR}/{temp_filename}"

        # Step 1: Create the file in sandbox A (claude-dev)
        create_result: CommandResult = run_sandbox_command(
            spark_ssh,
            _SANDBOX_A,
            f"touch {file_path_in_sandbox}",
            timeout=15,
        )
        assert create_result.return_code == 0, (
            f"Failed to create temp file {file_path_in_sandbox!r} in {_SANDBOX_A!r}. "
            f"stdout: {create_result.stdout!r}  stderr: {create_result.stderr!r}"
        )

        try:
            # Step 2: Check whether the file appears in sandbox B (codex-dev)
            check_result: CommandResult = run_sandbox_command(
                spark_ssh,
                _SANDBOX_B,
                f"test -f {file_path_in_sandbox} && echo FOUND || echo ABSENT",
                timeout=15,
            )
            visibility = check_result.stdout.strip().upper()
            assert visibility == "ABSENT", (
                f"File {file_path_in_sandbox!r} created inside {_SANDBOX_A!r} is "
                f"visible inside {_SANDBOX_B!r} (got {visibility!r}). "
                "This indicates the two sandboxes share a filesystem mount, "
                "which breaks the security isolation model. "
                "Ensure each sandbox uses an independent overlay filesystem "
                "and does not bind-mount a shared host directory to /tmp."
            )
        finally:
            # Step 3: Cleanup — remove the temp file from sandbox A
            run_sandbox_command(
                spark_ssh,
                _SANDBOX_A,
                f"rm -f {file_path_in_sandbox}",
                timeout=10,
            )


# ---------------------------------------------------------------------------
# Network isolation
# ---------------------------------------------------------------------------


@pytest.mark.phase4
@pytest.mark.behavioral
class TestNetworkIsolation:
    """Layer B: Sandboxes cannot reach unapproved external endpoints.

    The default-deny network-egress policy must block outbound connections
    to any host not explicitly listed in the allow-list.  This prevents
    agent code from exfiltrating data, downloading untrusted payloads, or
    bypassing the local-model requirement by calling external inference APIs.
    """

    def test_sandbox_cannot_reach_unapproved_endpoint(self, spark_ssh: Connection) -> None:
        """A curl request to 8.8.8.8 from inside claude-dev is blocked.

        Uses ``curl --max-time 5`` to attempt an HTTP connection to
        ``8.8.8.8``.  Under the default-deny egress policy the sandbox
        firewall should drop the SYN packet, causing curl to fail with a
        connection-refused or timeout error (non-zero exit code).

        A successful HTTP response (exit code 0 with HTTP 2xx/3xx) is the
        failure mode — it means the egress policy is not enforced.
        """
        result: CommandResult = run_sandbox_command(
            spark_ssh,
            _SANDBOX_A,
            (
                "sh -c '"
                "tmp=$(mktemp); "
                f'status=$(curl --silent --output "$tmp" --write-out "%{{http_code}}" '
                f"--max-time 5 {_BLOCKED_EXTERNAL_URL} 2>/dev/null); "
                "rc=$?; "
                'printf "CURL_EXIT:%s HTTP_STATUS:%s\\n" "$rc" "$status"; '
                'cat "$tmp"; rm -f "$tmp"\''
            ),
            timeout=20,
        )
        combined = result.stdout + " " + result.stderr
        assert curl_attempt_was_blocked(combined), (
            f"curl to {_BLOCKED_EXTERNAL_URL!r} from inside {_SANDBOX_A!r} was "
            "not blocked by the default-deny egress policy. "
            f"Combined output (first 300 chars): {combined[:300]!r}"
        )


# ---------------------------------------------------------------------------
# Egress blocking audit log
# ---------------------------------------------------------------------------


@pytest.mark.phase4
@pytest.mark.behavioral
class TestEgressBlocking:
    """Layer B: Blocked egress attempts are recorded in the OpenShell audit log.

    The security model requires that every denied outbound connection is
    logged so that operators can detect and investigate suspicious agent
    behavior.  Silent dropping without a log entry makes post-incident
    forensics impossible.
    """

    def test_blocked_request_logged(self, spark_ssh: Connection) -> None:
        """A denied egress attempt from claude-dev appears in the OpenShell audit log.

        Procedure:
        1. Note the current log tail position (to avoid false positives from
           old entries).
        2. Trigger a blocked connection attempt from inside the sandbox.
        3. Query the OpenShell audit log and assert that a denial entry for
           the attempted destination appears.

        The exact log format (``DENY``, ``blocked``, ``drop``) depends on the
        OpenShell version; we accept any of these keywords alongside the
        target IP address.
        """
        # Step 1: Trigger a blocked connection from the sandbox.
        run_sandbox_command(
            spark_ssh,
            _SANDBOX_A,
            f"curl --silent --max-time 3 {_BLOCKED_EXTERNAL_URL} 2>&1 || true",
            timeout=15,
        )

        # Step 2: Read recent OpenShell log entries for the sandbox.
        new_entries_result: CommandResult = run_remote(
            spark_ssh,
            f"openshell logs -n 50 --since 1m {_SANDBOX_A}",
            timeout=10,
        )
        new_log_content = new_entries_result.stdout.lower()

        denial_keywords = {"action=deny", "denied", "blocked", "drop", "reject"}
        found_denial = any(kw in new_log_content for kw in denial_keywords)
        found_target = (
            _BLOCKED_EXTERNAL_IP in new_log_content or "dst_host=8.8.8.8" in new_log_content
        )

        assert found_denial and found_target, (
            f"Expected to find a denial log entry referencing {_BLOCKED_EXTERNAL_IP!r} "
            f"in recent OpenShell logs after triggering a blocked connection from {_SANDBOX_A!r}. "
            f"Denial keyword found: {found_denial}. "
            f"Target IP found: {found_target}. "
            "Ensure the OpenShell network policy engine emits deny entries via "
            "'openshell logs'. "
            f"New log entries (first 500 chars):\n{new_entries_result.stdout[:500]}"
        )
