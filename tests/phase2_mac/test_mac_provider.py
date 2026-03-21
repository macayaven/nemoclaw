"""
Phase 2 — Mac Studio Integration: OpenShell provider registration tests.

Validates that the ``mac-ollama`` provider is correctly registered in the
OpenShell configuration on Spark, pointing at the Mac Studio's Ollama
instance, and that provider failures degrade gracefully (timeout, not hang).

Architecture context
--------------------
NemoClaw runs on Spark (DGX).  The Mac Studio is registered as a secondary
inference provider so that:
1. The user can switch to Mac inference when Spark's GPU is occupied.
2. Automated failover policies can route traffic to the Mac during Spark
   maintenance.

The provider entry must reference the Mac's LAN IP (192.168.1.20 by default)
and port 11434 so OpenShell can reach Ollama directly without going through
any intermediary proxy.

Markers
-------
phase2   : All tests in this module belong to Phase 2.
contract : Layer-A contract / state checks (SSH to Spark, no inference).

Fixtures (from conftest.py)
---------------------------
spark_ssh : fabric.Connection — live SSH connection to the DGX Spark.
mac_ip    : str               — LAN IP of the Mac Studio.
"""

from __future__ import annotations

import uuid

import pytest
from fabric import Connection

from ..helpers import parse_json_output, run_remote
from ..models import CommandResult, OpenShellProvider

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAC_PROVIDER_NAME = "mac-ollama"
_OLLAMA_PORT = 11434


# ---------------------------------------------------------------------------
# Contract tests — provider registration
# ---------------------------------------------------------------------------


@pytest.mark.phase2
@pytest.mark.contract
class TestMacProviderRegistration:
    """The mac-ollama provider must be registered in OpenShell on Spark.

    Provider registration is a prerequisite for all phase-2 inference tests.
    These tests run on Spark (via ``spark_ssh``) because that is where
    OpenShell — and the provider registry — lives.
    """

    def test_mac_ollama_registered(self, spark_ssh: Connection) -> None:
        """``openshell provider list`` on Spark must include 'mac-ollama'.

        Lists all configured providers and asserts the presence of a provider
        named ``mac-ollama``.  This is the stable identifier used by inference
        route switch commands and by NemoClaw's provider-selection logic.

        If the provider is absent, run on Spark:
            openshell provider add \\
                --type openai \\
                --name mac-ollama \\
                --base-url http://<mac-ip>:11434
        """
        result: CommandResult = run_remote(spark_ssh, "openshell provider list")

        assert result.return_code == 0, (
            f"openshell provider list failed with exit code {result.return_code}.\n"
            f"stderr: {result.stderr!r}\n"
            "Is OpenShell running on Spark? Check: openshell status"
        )

        assert _MAC_PROVIDER_NAME in result.stdout, (
            f"Provider '{_MAC_PROVIDER_NAME}' not found in openshell provider list.\n"
            f"Full output:\n{result.stdout}\n"
            "Register the Mac Ollama provider on Spark:\n"
            f"  openshell provider add --type openai --name {_MAC_PROVIDER_NAME} "
            f"--base-url http://<mac-ip>:{_OLLAMA_PORT}"
        )

    def test_provider_points_to_mac_ip(self, spark_ssh: Connection, mac_ip: str) -> None:
        """The mac-ollama provider's base_url must contain the Mac Studio LAN IP.

        Fetches the full provider configuration as JSON, parses it into an
        ``OpenShellProvider`` model, and asserts that ``base_url`` contains
        the Mac's LAN IP address.  This confirms the provider is configured
        to reach the correct host and not pointing at Spark's own Ollama
        instance (127.0.0.1 or the Spark IP) or at a stale placeholder URL.
        """
        result: CommandResult = run_remote(
            spark_ssh,
            f"openshell provider get {_MAC_PROVIDER_NAME} --json",
        )

        assert result.return_code == 0, (
            f"openshell provider get {_MAC_PROVIDER_NAME} --json failed "
            f"(exit code {result.return_code}).\n"
            f"stderr: {result.stderr!r}\n"
            f"Is '{_MAC_PROVIDER_NAME}' registered? Run: openshell provider list"
        )

        assert result.stdout.strip(), (
            f"openshell provider get {_MAC_PROVIDER_NAME} --json produced no output.\n"
            f"Return code: {result.return_code}\nStderr: {result.stderr}"
        )

        provider_data = parse_json_output(result.stdout)
        provider = OpenShellProvider.model_validate(provider_data)

        mac_ip_str = str(mac_ip)

        assert mac_ip_str in (provider.base_url or ""), (
            f"Provider '{_MAC_PROVIDER_NAME}' base_url does not contain the Mac IP "
            f"({mac_ip_str}).\n"
            f"Got base_url: {provider.base_url!r}\n"
            "The provider must point at the Mac Studio's Ollama instance.\n"
            f"Fix: openshell provider update {_MAC_PROVIDER_NAME} "
            f"--base-url http://{mac_ip_str}:{_OLLAMA_PORT}"
        )

        assert str(_OLLAMA_PORT) in (provider.base_url or ""), (
            f"Provider '{_MAC_PROVIDER_NAME}' base_url does not contain port "
            f"{_OLLAMA_PORT}.\n"
            f"Got base_url: {provider.base_url!r}\n"
            f"The URL must end with :{_OLLAMA_PORT} to reach Ollama."
        )


# ---------------------------------------------------------------------------
# Negative tests — graceful degradation when Mac is unreachable
# ---------------------------------------------------------------------------


@pytest.mark.phase2
class TestMacProviderNegative:
    """Validate graceful degradation when the Mac Ollama backend is unreachable.

    These tests do NOT require the Mac to actually be down.  Instead, they
    register a temporary provider pointing at a guaranteed-unreachable URL and
    verify that OpenShell returns an error within a bounded time rather than
    hanging indefinitely.

    A hung provider would block the inference route for all users until the
    connection attempt times out at the OS level (potentially minutes).
    NemoClaw must surface a provider error quickly so the user can switch to
    an available provider.
    """

    def test_mac_unreachable_provider_fails_gracefully(self, spark_ssh: Connection) -> None:
        """An unreachable mac provider must return an error, not hang.

        Registers a temporary provider pointing at a non-routable IP
        (192.0.2.1 — TEST-NET-1, RFC 5737, guaranteed to be unreachable) on
        port 11434.  Sends a minimal inference request through it and asserts
        that the call returns an error response (or a non-zero exit code)
        within 30 seconds.

        The 30-second wall-clock limit is enforced by run_remote's timeout
        parameter.  If Ollama / OpenShell hangs and the remote command does
        not return within that window, ``run_remote`` raises ``TimeoutError``,
        which is a test failure and is correctly interpreted as a hang.

        Cleanup: the temporary provider is deleted after the assertion,
        regardless of test outcome.
        """
        # RFC 5737 TEST-NET-1: 192.0.2.0/24 — documentation / unreachable by definition
        unreachable_ip = "192.0.2.1"
        unique_suffix = uuid.uuid4().hex[:8]
        temp_provider_name = f"test-mac-dead-{unique_suffix}"
        temp_base_url = f"http://{unreachable_ip}:{_OLLAMA_PORT}"

        # Register the unreachable provider — may succeed or fail depending on
        # whether OpenShell validates connectivity at registration time.
        run_remote(
            spark_ssh,
            f"openshell provider add "
            f"--type openai "
            f"--name {temp_provider_name} "
            f"--base-url {temp_base_url} "
            f"2>&1 || true",
            timeout=15,
        )

        # Attempt inference through the dead provider with a strict 30-second
        # timeout.  We expect an error response, NOT a timeout (hang).
        # The sandbox curl targets the unreachable provider explicitly via
        # --provider flag, leaving the primary route untouched.
        sandbox_name = f"test-dead-provider-{unique_suffix}"
        curl_cmd = (
            "curl -s --max-time 20 "
            "--cacert /etc/openshell/ca.crt "
            "https://inference.local/v1/chat/completions "
            "-H 'Content-Type: application/json' "
            "-d '{"
            '"model":"qwen3:8b",'
            '"messages":[{"role":"user","content":"hi"}],'
            '"max_tokens":5'
            "}' "
            "2>&1 || echo CURL_FAILED"
        )

        run_cmd = (
            f"openshell sandbox run "
            f"--name {sandbox_name} "
            f"--provider {temp_provider_name} "
            f"-- {curl_cmd}"
        )

        try:
            result: CommandResult = run_remote(spark_ssh, run_cmd, timeout=30)
            combined = (result.stdout + result.stderr).lower()
            error_keywords = {
                "error",
                "timeout",
                "refused",
                "unreachable",
                "failed",
                "could not",
                "connection",
                "curl_failed",
            }
            got_error = any(kw in combined for kw in error_keywords) or result.return_code != 0
        except TimeoutError as exc:
            # The command itself hung -- this is also a failure mode but
            # distinct: re-raise so pytest surfaces it as a timeout failure.
            raise AssertionError(
                f"Provider '{temp_provider_name}' pointed at unreachable host "
                f"{unreachable_ip} caused run_remote to hang for >30 seconds. "
                "OpenShell must enforce a connect timeout to fail fast."
            ) from exc
        finally:
            # Always clean up: delete temp sandbox and provider
            run_remote(
                spark_ssh,
                f"openshell sandbox delete {sandbox_name} 2>/dev/null || true",
                timeout=15,
            )
            run_remote(
                spark_ssh,
                f"openshell provider delete {temp_provider_name} 2>/dev/null || true",
                timeout=15,
            )

        assert got_error, (
            f"Expected an error when routing inference through unreachable provider "
            f"'{temp_provider_name}' ({temp_base_url}), but got a success-looking "
            f"response.\n"
            f"Return code: {result.return_code}\n"
            f"stdout: {result.stdout[:500]!r}\n"
            f"stderr: {result.stderr[:300]!r}"
        )
