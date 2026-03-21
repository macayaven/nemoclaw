"""
Phase 1 — Core NemoClaw on Spark: OpenShell provider registration tests.

Validates that the ``local-ollama`` provider is registered in OpenShell, that
its configuration points at the correct Ollama endpoint (port 11434), and
that attempting to register a provider with a bad URL fails as expected.

Markers
-------
phase1   : All tests in this module belong to Phase 1.
contract : Layer-A contract / schema tests (fast, SSH only, no inference).
"""

from __future__ import annotations

import json
import uuid

import pytest
from fabric import Connection

from tests.models import CommandResult, OpenShellProvider
from tests.helpers import run_remote, parse_json_output


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
        """``openshell provider get --json`` output must parse into OpenShellProvider.

        The parsed model is validated against the OpenShellProvider schema and
        the ``base_url`` field must contain port 11434, confirming that the
        provider points at the local Ollama instance and not at a cloud
        endpoint or a wrong port.
        """
        result: CommandResult = run_remote(
            spark_ssh,
            "openshell provider get local-ollama --json",
        )

        # Guard: ensure the command produced output before JSON parsing
        assert result.stdout.strip(), (
            "openshell provider get local-ollama --json produced no output.\n"
            f"Return code: {result.return_code}\nStderr: {result.stderr}"
        )

        provider_data = parse_json_output(result.stdout)
        provider = OpenShellProvider.model_validate(provider_data)

        assert "11434" in provider.base_url, (
            f"Provider 'local-ollama' base_url does not contain port 11434.\n"
            f"Got: {provider.base_url!r}\n"
            "The provider must point to the local Ollama instance "
            "(e.g. http://192.168.1.10:11434 or http://spark-caeb.local:11434)."
        )

        assert provider.name == "local-ollama", (
            f"Provider name mismatch: expected 'local-ollama', got {provider.name!r}"
        )


# ---------------------------------------------------------------------------
# Negative tests — bad provider URL must be rejected
# ---------------------------------------------------------------------------


@pytest.mark.phase1
class TestProviderNegative:
    """Negative path: verify that invalid provider configurations are rejected."""

    def test_wrong_provider_url_fails(self, spark_ssh: Connection) -> None:
        """Attempting to add a provider with a non-reachable URL must fail.

        Uses a unique ephemeral provider name so that a partial success on a
        previous run does not pollute the provider list.  The command is
        expected to fail (non-zero exit code) or emit an error keyword in
        stdout/stderr because OpenShell validates connectivity when a provider
        is added.

        The ephemeral provider is cleaned up after the assertion regardless of
        outcome.
        """
        unique_suffix = uuid.uuid4().hex[:8]
        bad_provider_name = f"test-bad-provider-{unique_suffix}"
        # Port 19999 is almost certainly not listening on the Spark
        bad_url = "http://127.0.0.1:19999"

        add_result: CommandResult = run_remote(
            spark_ssh,
            f"openshell provider add "
            f"--type openai "
            f"--name {bad_provider_name} "
            f"--base-url {bad_url} "
            f"2>&1 || true",
        )

        combined_output = (add_result.stdout + add_result.stderr).lower()

        # Attempt cleanup regardless of whether add succeeded — ignore errors
        run_remote(
            spark_ssh,
            f"openshell provider delete {bad_provider_name} 2>/dev/null || true",
        )

        # Either the command returned a non-zero exit code OR the output
        # contains an error/warning keyword.
        error_keywords = {"error", "fail", "refused", "unreachable", "invalid", "could not"}
        output_has_error = any(kw in combined_output for kw in error_keywords)
        command_failed = add_result.return_code != 0

        assert command_failed or output_has_error, (
            f"Expected openshell provider add to fail or emit an error when given "
            f"unreachable URL {bad_url!r}, but it appeared to succeed.\n"
            f"Return code: {add_result.return_code}\n"
            f"Output:\n{add_result.stdout}\n"
            f"Stderr:\n{add_result.stderr}"
        )
