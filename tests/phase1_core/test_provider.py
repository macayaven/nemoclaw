"""
Phase 1 — Core NemoClaw on Spark: OpenShell provider registration tests.

Validates that the ``local-ollama`` provider is registered in OpenShell, that
its saved config advertises the Ollama base URL key, and that attempting to
register a provider with a bad URL fails as expected.

Markers
-------
phase1   : All tests in this module belong to Phase 1.
contract : Layer-A contract / schema tests (fast, SSH only, no inference).
"""

from __future__ import annotations

import re
import uuid

import pytest
from fabric import Connection

from ..helpers import run_remote
from ..models import CommandResult

# ---------------------------------------------------------------------------
# Contract tests — provider registration and configuration
# ---------------------------------------------------------------------------


@pytest.mark.phase1
@pytest.mark.contract
class TestProviderRegistration:
    """Verify that the local-ollama provider exists and is correctly configured."""

    def test_local_ollama_registered(self, spark_ssh: Connection) -> None:
        """``openshell provider list`` must include an entry named 'local-ollama'.

        The provider name is the stable identifier used by the inference route
        and by all NemoClaw orchestration commands.  If it is missing, every
        subsequent phase-1 test that depends on inference will fail.
        """
        result: CommandResult = run_remote(spark_ssh, "openshell provider list")

        assert "local-ollama" in result.stdout, (
            "Provider 'local-ollama' not found in openshell provider list.\n"
            f"Full output:\n{result.stdout}\n"
            "Run: openshell provider add --type openai --name local-ollama "
            "--base-url http://<spark-ip>:11434"
        )

    def test_provider_config_correct(self, spark_ssh: Connection) -> None:
        """``openshell provider get local-ollama`` must report the expected config."""

        result: CommandResult = run_remote(spark_ssh, "openshell provider get local-ollama")

        assert result.stdout.strip(), (
            "openshell provider get local-ollama produced no output.\n"
            f"Return code: {result.return_code}\nStderr: {result.stderr}"
        )

        output = _strip_ansi(result.stdout)
        name = _extract_field(output, "Name")
        provider_type = _extract_field(output, "Type")
        config_keys = _extract_field(output, "Config keys")

        assert name == "local-ollama", (
            f"Provider name mismatch: expected 'local-ollama', got {name!r}.\n"
            f"Full output:\n{output}"
        )
        assert provider_type == "openai", (
            f"Provider type mismatch: expected 'openai', got {provider_type!r}.\n"
            f"Full output:\n{output}"
        )
        assert "OPENAI_BASE_URL" in config_keys, (
            "Provider summary does not advertise an OPENAI_BASE_URL config key.\n"
            f"Full output:\n{output}\n"
            "The local-ollama provider must point at the Spark Ollama endpoint."
        )


# ---------------------------------------------------------------------------
# Negative tests — bad provider URL must be rejected
# ---------------------------------------------------------------------------


@pytest.mark.phase1
class TestProviderNegative:
    """Negative path: verify that invalid provider configurations are rejected."""

    def test_unreachable_provider_url_is_persisted(self, spark_ssh: Connection) -> None:
        """Creating a provider with an unreachable URL should still persist config.

        The current CLI stores provider definitions without probing reachability
        at create time.  Runtime validation is covered by the inference routing
        tests, so here we assert the provider config round-trips cleanly.
        """
        unique_suffix = uuid.uuid4().hex[:8]
        bad_provider_name = f"test-bad-provider-{unique_suffix}"
        # Port 19999 is almost certainly not listening on the Spark
        bad_url = "http://127.0.0.1:19999"

        add_result: CommandResult = run_remote(
            spark_ssh,
            (
                "openshell provider create "
                "--type openai "
                f"--name {bad_provider_name} "
                "--credential OPENAI_API_KEY=not-needed "
                f"--config OPENAI_BASE_URL={bad_url}/v1"
            ),
        )

        assert add_result.return_code == 0, (
            f"openshell provider create unexpectedly failed for {bad_url!r}.\n"
            f"Return code: {add_result.return_code}\n"
            f"Output:\n{add_result.stdout}\n"
            f"Stderr:\n{add_result.stderr}"
        )
        assert bad_provider_name in add_result.stdout, (
            "Provider creation did not report the expected provider name.\n"
            f"Full output:\n{add_result.stdout}"
        )

        try:
            get_result: CommandResult = run_remote(
                spark_ssh,
                f"openshell provider get {bad_provider_name}",
            )

            output = _strip_ansi(get_result.stdout)
            assert _extract_field(output, "Name") == bad_provider_name, (
                f"Provider name did not round-trip for {bad_provider_name!r}.\n"
                f"Full output:\n{output}"
            )
            assert _extract_field(output, "Type") == "openai", (
                f"Provider type did not round-trip for {bad_provider_name!r}.\n"
                f"Full output:\n{output}"
            )
            assert "OPENAI_BASE_URL" in _extract_field(output, "Config keys"), (
                "The unreachable provider URL did not persist as a config key.\n"
                f"Full output:\n{output}"
            )
        finally:
            run_remote(
                spark_ssh,
                f"openshell provider delete {bad_provider_name} 2>/dev/null || true",
            )


_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _extract_field(text: str, field_name: str) -> str:
    match = re.search(rf"^\s*{re.escape(field_name)}:\s*(.+?)\s*$", text, re.MULTILINE)
    if not match:
        raise AssertionError(f"Could not find {field_name!r} in provider output:\n{text}")
    return match.group(1).strip()
