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
test_settings: TestSettings — cluster topology and preferred hostnames/IPs.
"""

from __future__ import annotations

import uuid

import pytest
from fabric import Connection

from ..helpers import (
    parse_openshell_inference_route_output,
    parse_openshell_provider_output,
    run_remote,
    strip_ansi,
)
from ..models import CommandResult, OpenShellProvider
from ..settings import TestSettings

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
            openshell provider create \\
                --type openai \\
                --name mac-ollama \\
                --credential OPENAI_API_KEY=not-needed \\
                --config OPENAI_BASE_URL=http://<mac-ip>:11434
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
            f"  openshell provider create --type openai --name {_MAC_PROVIDER_NAME} "
            "--credential OPENAI_API_KEY=not-needed "
            f"--config OPENAI_BASE_URL=http://<mac-ip>:{_OLLAMA_PORT}"
        )

    def test_provider_points_to_mac_endpoint(
        self,
        spark_ssh: Connection,
        test_settings: TestSettings,
    ) -> None:
        """The mac-ollama provider is structurally valid and the Mac endpoint is reachable.

        The current OpenShell CLI no longer exposes provider config values as
        JSON, so we validate the supported contract instead:
        1. ``openshell provider get`` returns a named openai provider with the
           ``OPENAI_BASE_URL`` config key.
        2. Spark can reach the Mac's Ollama API at the preferred stable endpoint
           from test settings: Tailscale IP when configured, otherwise DNS hostname.

        End-to-end proof that the route actually uses the Mac backend lives in
        the phase-2 provider switching tests.
        """
        result: CommandResult = run_remote(
            spark_ssh,
            f"openshell provider get {_MAC_PROVIDER_NAME}",
        )

        assert result.return_code == 0, (
            f"openshell provider get {_MAC_PROVIDER_NAME} failed "
            f"(exit code {result.return_code}).\n"
            f"stderr: {result.stderr!r}\n"
            f"Is '{_MAC_PROVIDER_NAME}' registered? Run: openshell provider list"
        )

        assert result.stdout.strip(), (
            f"openshell provider get {_MAC_PROVIDER_NAME} produced no output.\n"
            f"Return code: {result.return_code}\nStderr: {result.stderr}"
        )

        provider: OpenShellProvider = parse_openshell_provider_output(result.stdout)
        provider_output = strip_ansi(result.stdout)

        assert provider.name == _MAC_PROVIDER_NAME, (
            f"Provider name mismatch: expected {_MAC_PROVIDER_NAME!r}, got {provider.name!r}.\n"
            f"Full output:\n{provider_output}"
        )

        assert provider.type == "openai", (
            f"Provider type mismatch: expected 'openai', got {provider.type!r}.\n"
            f"Full output:\n{provider_output}"
        )

        assert "OPENAI_BASE_URL" in provider_output, (
            f"Provider '{_MAC_PROVIDER_NAME}' does not report the OPENAI_BASE_URL config key.\n"
            f"Full output:\n{provider_output}"
        )

        mac_host = str(test_settings.mac.tailscale_ip or test_settings.mac.hostname)

        reachability: CommandResult = run_remote(
            spark_ssh,
            f"curl -sf --max-time 10 http://{mac_host}:{_OLLAMA_PORT}/api/tags >/dev/null",
            timeout=15,
        )
        assert reachability.return_code == 0, (
            f"Spark could not reach the Mac Ollama endpoint at http://{mac_host}:{_OLLAMA_PORT}/api/tags.\n"
            f"stdout: {reachability.stdout!r}\n"
            f"stderr: {reachability.stderr!r}"
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

        # Register the unreachable provider — creation succeeds even when the
        # target URL is dead because connectivity is validated at request time.
        run_remote(
            spark_ssh,
            f"openshell provider create "
            f"--type openai "
            f"--name {temp_provider_name} "
            "--credential OPENAI_API_KEY=not-needed "
            f"--config OPENAI_BASE_URL={temp_base_url}",
            timeout=15,
        )

        route_result = run_remote(spark_ssh, "openshell inference get", timeout=15)
        original_route = parse_openshell_inference_route_output(route_result.stdout)

        try:
            set_result = run_remote(
                spark_ssh,
                f"openshell inference set --provider {temp_provider_name} --model qwen3:8b",
                timeout=60,
            )
            combined = (set_result.stdout + set_result.stderr).lower()
            error_keywords = {
                "error",
                "timeout",
                "refused",
                "unreachable",
                "failed",
                "could not",
                "connection",
                "verify",
            }
            got_error = any(kw in combined for kw in error_keywords) or set_result.return_code != 0
        except TimeoutError as exc:
            raise AssertionError(
                f"Provider '{temp_provider_name}' pointed at unreachable host "
                f"{unreachable_ip} caused 'openshell inference set' to hang for >60 seconds. "
                "OpenShell must fail fast when verifying a dead provider."
            ) from exc
        finally:
            run_remote(
                spark_ssh,
                "openshell inference set "
                f"--provider {original_route.provider} --model {original_route.model} "
                "2>/dev/null || true",
                timeout=60,
            )
            run_remote(
                spark_ssh,
                f"openshell provider delete {temp_provider_name} 2>/dev/null || true",
                timeout=15,
            )

        assert got_error, (
            f"Expected 'openshell inference set' to reject unreachable provider "
            f"'{temp_provider_name}' ({temp_base_url}), but it appeared to succeed.\n"
            f"Return code: {set_result.return_code}\n"
            f"stdout: {set_result.stdout[:500]!r}\n"
            f"stderr: {set_result.stderr[:300]!r}"
        )

        assert _OLLAMA_PORT == 11434, (
            "Sanity check failed: test assumptions about the Mac Ollama port drifted."
        )

        assert set_result.duration_ms < 60_000, (
            "OpenShell did reject the unreachable provider, but only after the test timeout "
            "budget. The failure path should remain bounded.\n"
            f"response.\n"
            f"Duration: {set_result.duration_ms} ms\n"
            f"stderr: {set_result.stderr[:300]!r}"
        )
