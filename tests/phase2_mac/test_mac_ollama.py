"""
Phase 2 — Mac Studio Integration: Ollama binding and health tests.

Validates that Ollama on the Mac Studio is:
- Bound to all network interfaces (0.0.0.0:11434), not just localhost, so
  that NemoClaw on Spark can reach it as a remote provider.
- Responding correctly on its HTTP API (/api/tags).
- Serving the ``qwen3:8b`` model that is required for Mac-side inference.
- Managed by launchd with the correct OLLAMA_HOST environment configuration.

Architecture note
-----------------
The Mac Studio acts as a secondary inference node.  Ollama must bind to
0.0.0.0 (not 127.0.0.1) because the provider URL in OpenShell on Spark uses
the Mac's LAN IP (192.168.1.20).  A localhost-only binding would make the
provider unreachable and cause all phase-2 inference tests to fail with a
connection-refused error.

Markers
-------
phase2   : All tests in this module belong to Phase 2.
contract : Layer-A contract / state checks (fast, SSH + HTTP, no inference).
behavioral : Layer-B health checks that exercise the live HTTP API.
slow     : Tests that may wait for a model to load.

Fixtures (from conftest.py)
---------------------------
mac_ssh  : fabric.Connection — live SSH connection to the Mac Studio.
mac_ip   : str               — LAN IP of the Mac Studio (e.g. '192.168.1.20').
mac_prereqs : MacPrereqs     — Pydantic model populated via SSH at fixture load time.
"""

from __future__ import annotations

import pytest
from fabric import Connection

from tests.helpers import assert_http_healthy, assert_json_schema, run_remote
from tests.models import CommandResult, MacPrereqs, OllamaTagsResponse


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_OLLAMA_PORT = 11434
_QWEN3_MODEL_NAME = "qwen3"  # match substring; tag may vary (e.g. qwen3:8b)
_LAUNCHD_PLIST_PATH = "~/Library/LaunchAgents/com.ollama.serve.plist"


# ---------------------------------------------------------------------------
# Contract tests — network binding
# ---------------------------------------------------------------------------


@pytest.mark.phase2
@pytest.mark.contract
class TestMacOllamaBinding:
    """Ollama must bind to 0.0.0.0:11434, not just to the loopback interface.

    NemoClaw on Spark connects to the Mac's Ollama instance using the Mac's
    LAN IP address.  If Ollama only listens on 127.0.0.1, connections from
    Spark will be refused at the TCP layer before any HTTP exchange can occur.
    """

    def test_listens_on_all_interfaces(self, mac_ssh: Connection) -> None:
        """``ss -tlnp`` on Mac must show Ollama listening on 0.0.0.0:11434.

        Uses ``ss`` (socket statistics) rather than ``netstat`` because ``ss``
        is available on both Linux and macOS (via iproute2mac or system tools)
        and its output format is more consistent.  Falls back to ``lsof`` if
        ``ss`` is unavailable on the Mac, which is common on stock macOS.

        The assertion confirms ``0.0.0.0:11434`` appears in the listening
        sockets, which is the only binding that allows remote clients to
        connect.
        """
        # macOS may not have ss; use lsof as the primary tool and ss as fallback.
        result: CommandResult = run_remote(
            mac_ssh,
            "lsof -iTCP:11434 -sTCP:LISTEN -n -P 2>/dev/null "
            "|| ss -tlnp 'sport = :11434' 2>/dev/null "
            "|| netstat -an 2>/dev/null | grep LISTEN | grep 11434",
            timeout=15,
        )

        assert result.return_code == 0 or result.stdout.strip(), (
            "Could not query listening sockets on Mac Studio. "
            "Tried: lsof, ss, netstat. "
            f"stderr: {result.stderr!r}. "
            "Ensure at least one of these tools is available."
        )

        output = result.stdout

        # lsof output contains "*:11434" for 0.0.0.0 bindings on macOS
        # ss output contains "0.0.0.0:11434"
        # netstat output contains "*.11434" or "0.0.0.0.11434"
        listening_on_all = (
            "*:11434" in output
            or "0.0.0.0:11434" in output
            or "*.11434" in output
            or "0.0.0.0.11434" in output
        )

        assert listening_on_all, (
            f"Ollama does not appear to be listening on 0.0.0.0:{_OLLAMA_PORT} "
            f"(all interfaces) on Mac Studio.\n"
            f"Socket output:\n{output}\n"
            "Ollama may be bound only to 127.0.0.1, which prevents Spark from "
            "connecting.  Fix by setting OLLAMA_HOST=0.0.0.0 in the launchd plist "
            "or by running: OLLAMA_HOST=0.0.0.0 ollama serve"
        )

    def test_not_localhost_only(self, mac_ssh: Connection) -> None:
        """Verify Ollama is NOT restricted to 127.0.0.1:11434 only.

        This is the complementary assertion to ``test_listens_on_all_interfaces``:
        a binding on ``127.0.0.1:11434`` in addition to ``0.0.0.0:11434`` is
        acceptable (the wildcard subsumes the loopback), but a binding that
        *only* shows ``127.0.0.1:11434`` and no wildcard or all-interfaces
        entry is a misconfiguration.

        The test passes if:
        - There is a wildcard / all-interfaces binding (0.0.0.0, *, ::), OR
        - Port 11434 does not appear to be bound exclusively to 127.0.0.1.
        """
        result: CommandResult = run_remote(
            mac_ssh,
            "lsof -iTCP:11434 -sTCP:LISTEN -n -P 2>/dev/null "
            "|| ss -tlnp 'sport = :11434' 2>/dev/null "
            "|| netstat -an 2>/dev/null | grep LISTEN | grep 11434",
            timeout=15,
        )

        output = result.stdout.strip()

        # If no output, Ollama is not listening at all — that is a different
        # failure, caught by test_listens_on_all_interfaces.
        if not output:
            pytest.skip(
                "No socket output found; skipping localhost-only check. "
                "test_listens_on_all_interfaces will catch a completely-down Ollama."
            )

        # A localhost-only binding would contain 127.0.0.1:11434 with NO
        # wildcard entry anywhere in the output.
        has_wildcard = (
            "*:11434" in output
            or "0.0.0.0:11434" in output
            or "*.11434" in output
            or "0.0.0.0.11434" in output
            or ":::11434" in output  # IPv6 wildcard
        )
        only_localhost = "127.0.0.1" in output and not has_wildcard

        assert not only_localhost, (
            f"Ollama on Mac Studio is bound ONLY to 127.0.0.1:{_OLLAMA_PORT}. "
            "Remote clients (Spark, other nodes) cannot reach it. "
            "Fix: set OLLAMA_HOST=0.0.0.0 before starting Ollama.\n"
            f"Socket output:\n{output}"
        )


# ---------------------------------------------------------------------------
# Behavioral tests — HTTP API health
# ---------------------------------------------------------------------------


@pytest.mark.phase2
@pytest.mark.behavioral
class TestMacOllamaHealth:
    """Ollama HTTP API on the Mac must be reachable and return valid responses.

    These tests exercise the live API over the network (not via SSH), using the
    Mac's LAN IP so the same path that Spark uses is validated.
    """

    def test_responds_on_health(self, mac_ip: str) -> None:
        """GET mac:11434/api/tags must return HTTP 200.

        Hits the Ollama tags endpoint using the Mac's LAN IP — the same URL
        that the mac-ollama provider in OpenShell on Spark uses.  A non-200
        response indicates Ollama is down, bound to a different interface, or
        behind a firewall rule that blocks port 11434 from the Spark subnet.
        """
        url = f"http://{mac_ip}:{_OLLAMA_PORT}/api/tags"
        assert_http_healthy(url, timeout=10, expected_status={200})

    def test_qwen3_8b_available(self, mac_ip: str) -> None:
        """GET /api/tags must list a model whose name contains 'qwen3'.

        The ``qwen3:8b`` model is the designated secondary inference model for
        Phase 2.  If it is missing, the mac-side inference route cannot serve
        completions and the provider-switching tests will fail at inference
        time rather than at the health-check stage.

        Checks by substring ('qwen3') rather than exact match to accommodate
        tag variations (e.g. 'qwen3:8b', 'qwen3:latest', 'qwen3:8b-instruct').
        """
        url = f"http://{mac_ip}:{_OLLAMA_PORT}/api/tags"
        response = assert_http_healthy(url, timeout=10, expected_status={200})
        tags: OllamaTagsResponse = assert_json_schema(response, OllamaTagsResponse)

        matched = tags.find(_QWEN3_MODEL_NAME)
        assert matched is not None, (
            f"Model containing '{_QWEN3_MODEL_NAME}' not found in Ollama on Mac Studio.\n"
            f"Available models: {tags.model_names}\n"
            "Pull the model: ollama pull qwen3:8b\n"
            "(run this command on Mac Studio, not on Spark)"
        )

    def test_json_schema_valid(self, mac_ip: str) -> None:
        """GET /api/tags response must validate against OllamaTagsResponse schema.

        Validates the full JSON structure: top-level ``models`` list, each
        entry having at minimum a ``name`` and ``size`` field.  This catches
        API version mismatches where Ollama returns a different shape (e.g. a
        newer version that restructures the response), ensuring the NemoClaw
        test suite's model definitions stay in sync with the live API.
        """
        url = f"http://{mac_ip}:{_OLLAMA_PORT}/api/tags"
        response = assert_http_healthy(url, timeout=10, expected_status={200})
        tags: OllamaTagsResponse = assert_json_schema(response, OllamaTagsResponse)

        # Extra invariant: each model entry has a non-empty name
        for model_info in tags.models:
            assert model_info.name, (
                f"OllamaModelInfo.name is empty for an entry in /api/tags response.\n"
                f"Full models list: {tags.model_names}"
            )


# ---------------------------------------------------------------------------
# Contract tests — launchd service configuration
# ---------------------------------------------------------------------------


@pytest.mark.phase2
@pytest.mark.contract
class TestMacOllamaLaunchd:
    """Ollama on Mac must be managed by launchd with the correct OLLAMA_HOST.

    A launchd plist in ``~/Library/LaunchAgents/`` ensures Ollama starts
    automatically on login and is restarted if it crashes.  The plist must
    set ``OLLAMA_HOST=0.0.0.0`` (or equivalent) so that every automatic
    restart binds to all interfaces — without this, a restart after a crash
    would revert Ollama to localhost-only and silently break remote inference.
    """

    def test_launchd_plist_exists(self, mac_ssh: Connection) -> None:
        """The Ollama launchd plist file must exist at the expected path.

        Checks for ``~/Library/LaunchAgents/com.ollama.serve.plist`` (the
        conventional location for user-level macOS daemons).  The plist is
        the macOS equivalent of a systemd unit file — its absence means Ollama
        is not configured to start automatically and must be launched manually
        after every reboot.
        """
        result: CommandResult = run_remote(
            mac_ssh,
            f"test -f {_LAUNCHD_PLIST_PATH} && echo EXISTS || echo MISSING",
            timeout=10,
        )

        assert "EXISTS" in result.stdout, (
            f"Launchd plist not found at {_LAUNCHD_PLIST_PATH} on Mac Studio.\n"
            "Create a launchd plist to ensure Ollama starts automatically on login "
            "and always binds to 0.0.0.0.\n"
            "Example plist location: ~/Library/LaunchAgents/com.ollama.serve.plist\n"
            "Load it with: launchctl load ~/Library/LaunchAgents/com.ollama.serve.plist"
        )

    def test_ollama_host_configured(self, mac_ssh: Connection) -> None:
        """The launchd plist must contain OLLAMA_HOST configured to 0.0.0.0.

        Reads the plist and searches for the ``OLLAMA_HOST`` key set to
        ``0.0.0.0``.  This is the only persistent way to guarantee that
        Ollama binds to all interfaces after a launchd-triggered restart.
        A plist that omits ``OLLAMA_HOST`` relies on a shell export in
        ``~/.zshrc`` (or similar), which launchd does not source.
        """
        result: CommandResult = run_remote(
            mac_ssh,
            f"cat {_LAUNCHD_PLIST_PATH} 2>/dev/null || echo PLIST_NOT_FOUND",
            timeout=10,
        )

        if "PLIST_NOT_FOUND" in result.stdout:
            pytest.skip(
                f"Plist {_LAUNCHD_PLIST_PATH} does not exist; "
                "test_launchd_plist_exists covers this failure."
            )

        plist_content = result.stdout

        assert "OLLAMA_HOST" in plist_content, (
            f"OLLAMA_HOST key not found in {_LAUNCHD_PLIST_PATH}.\n"
            "Add an EnvironmentVariables dict to the plist:\n"
            "  <key>EnvironmentVariables</key>\n"
            "  <dict>\n"
            "    <key>OLLAMA_HOST</key>\n"
            "    <string>0.0.0.0</string>\n"
            "  </dict>\n"
            f"Plist content (first 500 chars):\n{plist_content[:500]}"
        )

        assert "0.0.0.0" in plist_content, (
            f"OLLAMA_HOST is present in {_LAUNCHD_PLIST_PATH} but is not set to '0.0.0.0'.\n"
            "Ollama may be configured to listen on a specific IP or on localhost.\n"
            "Update the plist so OLLAMA_HOST=0.0.0.0 to allow all interfaces.\n"
            f"Plist content (first 500 chars):\n{plist_content[:500]}"
        )
