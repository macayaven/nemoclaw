"""
Phase 1 — Core NemoClaw on Spark: Ollama configuration tests.

Verifies that Ollama on the DGX Spark is bound to all interfaces (not just
localhost), has OLLAMA_KEEP_ALIVE set to prevent model unloading, and is
reachable over the network from external hosts.

Markers
-------
phase1   : All tests in this module belong to Phase 1.
contract : Layer-A schema / configuration tests (fast, no inference).
behavioral : Layer-B endpoint / network tests (hit real sockets).
"""

from __future__ import annotations

import pytest
import httpx
from fabric import Connection

from ..models import CommandResult, OllamaTagsResponse
from ..helpers import run_remote, assert_http_healthy, assert_json_schema, parse_json_output


# ---------------------------------------------------------------------------
# Contract tests — Ollama binding and environment configuration
# ---------------------------------------------------------------------------


@pytest.mark.phase1
@pytest.mark.contract
class TestOllamaBinding:
    """Validate that Ollama is bound to all interfaces and correctly configured."""

    def test_listens_on_all_interfaces(self, spark_ssh: Connection) -> None:
        """Ollama must bind 0.0.0.0:11434, not only 127.0.0.1.

        A sandbox running inside the OpenShell gateway reaches Ollama via the
        host IP, not via loopback.  If Ollama is bound to 127.0.0.1 only,
        every inference request from the sandbox will fail with ECONNREFUSED.
        """
        result: CommandResult = run_remote(spark_ssh, "ss -tlnp | grep 11434")

        listening_line = result.stdout
        assert (
            "0.0.0.0:11434" in listening_line or "*:11434" in listening_line
        ), (
            f"Ollama is not bound to all interfaces.  "
            f"Expected '0.0.0.0:11434' or '*:11434' in ss output, got:\n{listening_line}"
        )

    def test_keep_alive_set(self, spark_ssh: Connection) -> None:
        """OLLAMA_KEEP_ALIVE=-1 must be present in the systemd environment.

        Reads the live unit environment via ``systemctl show`` rather than
        parsing the override file, so the test reflects the *effective*
        configuration even if the drop-in path changes.
        """
        result: CommandResult = run_remote(
            spark_ssh,
            "systemctl show ollama -p Environment",
        )
        env_line = result.stdout.strip()

        assert "OLLAMA_KEEP_ALIVE=-1" in env_line, (
            f"OLLAMA_KEEP_ALIVE=-1 not found in the ollama unit environment.\n"
            f"systemctl show output: {env_line}\n"
            "Ensure the systemd override sets Environment=OLLAMA_KEEP_ALIVE=-1"
        )

    def test_not_bound_to_localhost_only(self, spark_ssh: Connection) -> None:
        """Confirm the absence of a 127.0.0.1-only binding on port 11434.

        This is the *negative* complement to test_listens_on_all_interfaces.
        A false-positive in that test (e.g. both 0.0.0.0 and 127.0.0.1
        entries) would not be caught without this check.
        """
        result: CommandResult = run_remote(spark_ssh, "ss -tlnp | grep 11434")
        assert "127.0.0.1:11434" not in result.stdout, (
            "Ollama appears to have a loopback-only binding on 11434.\n"
            f"Full ss output:\n{result.stdout}"
        )


# ---------------------------------------------------------------------------
# Behavioral tests — live HTTP health checks
# ---------------------------------------------------------------------------


@pytest.mark.phase1
@pytest.mark.behavioral
class TestOllamaHealth:
    """Verify Ollama's HTTP API is live and returns well-formed JSON."""

    def test_responds_on_health(self, spark_ollama_url: str) -> None:
        """GET /api/tags returns HTTP 200 and a parseable OllamaTagsResponse.

        Uses the ``spark_ollama_url`` fixture so the host/port is read from
        settings rather than hardcoded — makes the suite portable across
        environments.
        """
        response = httpx.get(f"{spark_ollama_url}/api/tags", timeout=15.0)

        assert response.status_code == 200, (
            f"Ollama /api/tags returned {response.status_code}, expected 200.\n"
            f"URL: {spark_ollama_url}/api/tags"
        )

        payload = response.json()
        parsed = OllamaTagsResponse.model_validate(payload)
        assert isinstance(parsed.models, list), (
            "OllamaTagsResponse.models must be a list; "
            f"got {type(parsed.models)}"
        )

    def test_reachable_from_other_host(self, spark_ip: str) -> None:
        """Ollama is reachable from outside the Spark host (network binding test).

        Connects to the Spark's LAN IP directly on port 11434.  This confirms
        that the socket is truly open to the network and not just to loopback,
        which is exactly what the sandbox will do.
        """
        url = f"http://{spark_ip}:11434/api/tags"
        response = httpx.get(url, timeout=15.0)

        assert response.status_code == 200, (
            f"Ollama is not reachable at {url} (status {response.status_code}).\n"
            "Check that OLLAMA_HOST=0.0.0.0 is set and the firewall allows 11434."
        )

    def test_json_schema_valid(self, spark_ollama_url: str) -> None:
        """The /api/tags response matches the expected OllamaTagsResponse schema.

        Validates structural correctness via Pydantic model parsing so that
        any upstream change to the Ollama response format is caught early.
        Uses ``assert_json_schema`` which accepts an ``httpx.Response`` and
        validates it against the given Pydantic model class.
        """
        response = httpx.get(f"{spark_ollama_url}/api/tags", timeout=15.0)
        assert response.status_code == 200

        # assert_json_schema accepts the raw httpx.Response object and handles
        # JSON decoding + Pydantic validation internally.
        assert_json_schema(response, OllamaTagsResponse)


# ---------------------------------------------------------------------------
# Negative tests — misconfiguration detection
# ---------------------------------------------------------------------------


@pytest.mark.phase1
class TestOllamaNegative:
    """Negative path tests: catch the most common Ollama misconfiguration."""

    def test_not_bound_to_localhost_only(self, spark_ssh: Connection) -> None:
        """Duplicate negative guard: loopback-only binding must not exist.

        This test is intentionally separate from TestOllamaBinding so it can
        be run in isolation with ``-k TestOllamaNegative`` and still catch the
        most critical misconfiguration without depending on the full contract
        suite having run.
        """
        result: CommandResult = run_remote(spark_ssh, "ss -tlnp | grep 11434")

        assert "127.0.0.1:11434" not in result.stdout, (
            "CRITICAL: Ollama is bound to 127.0.0.1:11434 only.\n"
            "Sandboxes cannot reach a loopback-only Ollama binding.\n"
            "Fix: set OLLAMA_HOST=0.0.0.0 in the systemd override and restart "
            "the ollama service.\n"
            f"Current ss output:\n{result.stdout}"
        )
