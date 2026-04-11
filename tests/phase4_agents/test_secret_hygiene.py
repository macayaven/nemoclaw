"""
Phase 4 — Coding Agent Sandboxes: Secret hygiene and credential masking tests.

Validates that API keys and other credentials are never exposed in plaintext
in operator-visible surfaces: OpenShell audit/service logs, sandbox environment
listings, and the output of ``openshell provider get``.

These tests deliberately do NOT assert that the keys are present and valid —
that is the concern of auth/integration tests.  The sole concern here is that
raw secret values do not leak into any surface that an operator, log shipper,
or monitoring system might capture.

Markers
-------
phase4    : All tests here belong to Phase 4 (Coding Agent Sandboxes).
contract  : Layer A — structural assertion that secrets are absent from outputs.

Fixtures (from conftest.py)
---------------------------
spark_ssh    : fabric.Connection — live SSH connection to the DGX Spark node.
test_settings: TestSettings — provides API key values (as SecretStr) for
               comparison.
"""

from __future__ import annotations

import pytest
from fabric import Connection

from ..helpers import run_remote
from ..models import CommandResult
from ..settings import TestSettings
from ._openshell_cli import run_sandbox_command

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Paths to log files that must not contain raw API key values.
_LOG_PATHS: tuple[str, ...] = (
    "/var/log/openshell/service.log",
    "/var/log/openshell/audit.log",
)

# Maximum number of log lines to scan (prevents the test from being slow on
# very large log files while still covering a meaningful recent window).
_LOG_TAIL_LINES: int = 2000

# Sandbox environments to inspect for raw secret leakage.
_AGENT_SANDBOXES: tuple[str, ...] = ("claude-dev", "codex-dev", "gemini-dev")

# Minimum key length we will check for; shorter strings are too short to be
# meaningful API keys and we avoid false positives.
_MIN_KEY_LENGTH: int = 8


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_secret_value(settings: TestSettings, provider: str) -> str | None:
    """Return the plaintext value of the named provider's API key, or None.

    Args:
        settings: Loaded TestSettings instance.
        provider: One of ``"anthropic"``, ``"openai"``, or ``"gemini"``.

    Returns:
        The raw secret string if the key is configured and long enough to be
        meaningful, otherwise ``None``.
    """
    mapping = {
        "anthropic": settings.anthropic_api_key,
        "openai": settings.openai_api_key,
        "gemini": settings.gemini_api_key,
    }
    secret = mapping.get(provider.lower())
    if secret is None:
        return None
    raw = secret.get_secret_value()
    if len(raw) < _MIN_KEY_LENGTH:
        return None
    return raw


# ---------------------------------------------------------------------------
# Contract tests — secret hygiene
# ---------------------------------------------------------------------------


@pytest.mark.phase4
@pytest.mark.contract
class TestSecretHygiene:
    """Layer A: API keys never appear in plaintext in logs, env, or CLI output.

    These tests use the ``SecretStr`` values from ``TestSettings`` to look for
    the raw key content in operator-visible surfaces.  They are skipped
    automatically for any provider whose API key is not configured in the test
    environment (since we cannot check for a value we do not know).
    """

    def test_api_keys_not_in_logs(self, spark_ssh: Connection, test_settings: TestSettings) -> None:
        """Raw API key values do not appear in any recent OpenShell log entry.

        Scans the last ``_LOG_TAIL_LINES`` lines of each OpenShell log file
        for the literal text of each configured API key.  A match indicates
        that a log statement is printing credentials in plaintext, which would
        expose them to anyone with log read access (including log shippers,
        SIEM systems, and S3 log archives).

        The test is skipped if no API keys are configured in the test
        environment — there is nothing to check for.
        """
        # Collect all plaintext key values that we know about.
        keys_to_check: dict[str, str] = {}
        for provider in ("anthropic", "openai", "gemini"):
            raw = _get_secret_value(test_settings, provider)
            if raw:
                keys_to_check[provider] = raw

        if not keys_to_check:
            pytest.skip(
                "No API keys are configured in the test environment; nothing to check for in logs."
            )

        leaks: list[str] = []

        for log_path in _LOG_PATHS:
            result: CommandResult = run_remote(
                spark_ssh,
                f"tail -n {_LOG_TAIL_LINES} {log_path} 2>/dev/null || echo ''",
                timeout=20,
            )
            log_content = result.stdout

            if not log_content.strip():
                # Log file absent or empty — skip this path.
                continue

            for provider, raw_key in keys_to_check.items():
                if raw_key in log_content:
                    # Find a redacted excerpt for the assertion message.
                    idx = log_content.index(raw_key)
                    snippet = log_content[max(0, idx - 40) : idx + len(raw_key) + 40]
                    redacted_key = raw_key[:4] + "..." + raw_key[-4:]
                    leaks.append(
                        f"  [{provider}] key ({redacted_key}) found in {log_path!r}. "
                        f"Context: ...{snippet!r}..."
                    )

        assert not leaks, (
            "API keys found in plaintext in OpenShell log files:\n"
            + "\n".join(leaks)
            + "\nEnsure log statements use masked placeholders (e.g. '***') "
            "rather than logging raw credential values. "
            "Check the OpenShell logging configuration for credential redaction settings."
        )

    def test_api_keys_not_in_sandbox_env(
        self, spark_ssh: Connection, test_settings: TestSettings
    ) -> None:
        """Raw API key values do not appear in sandbox environment listings.

        Runs ``env`` inside each agent sandbox via the supported OpenShell
        SSH bridge and checks
        that the plaintext API key values are not present in the output.

        OpenShell is expected to inject credentials via the provider SDK's
        native mechanism (e.g. a secrets mount or a sealed env var that the
        SDK reads directly) rather than as plaintext environment variables
        visible to ``env``.  Plaintext exposure via ``env`` means any process
        running inside the sandbox — including agent-generated code — can read
        the credentials.

        The test is skipped if no API keys are configured.
        """
        keys_to_check: dict[str, str] = {}
        for provider in ("anthropic", "openai", "gemini"):
            raw = _get_secret_value(test_settings, provider)
            if raw:
                keys_to_check[provider] = raw

        if not keys_to_check:
            pytest.skip(
                "No API keys are configured in the test environment; "
                "nothing to check for in sandbox env."
            )

        leaks: list[str] = []

        for sandbox_name in _AGENT_SANDBOXES:
            result: CommandResult = run_sandbox_command(
                spark_ssh,
                sandbox_name,
                "env 2>&1 || echo ''",
                timeout=20,
            )
            env_output = result.stdout

            if not env_output.strip():
                continue

            for provider, raw_key in keys_to_check.items():
                if raw_key in env_output:
                    redacted_key = raw_key[:4] + "..." + raw_key[-4:]
                    leaks.append(
                        f"  [{provider}] key ({redacted_key}) is visible in the "
                        f"environment of sandbox {sandbox_name!r}."
                    )

        assert not leaks, (
            "API keys found in plaintext in sandbox environment variables:\n"
            + "\n".join(leaks)
            + "\nUse a secrets-mount or SDK-native credential injection instead of "
            "passing API keys as plain environment variables. "
            "Consult the OpenShell documentation on secret injection."
        )

    def test_provider_credentials_masked(
        self, spark_ssh: Connection, test_settings: TestSettings
    ) -> None:
        """``openshell provider get`` output does not reveal raw API key values.

        Runs ``openshell provider get`` for each configured provider and
        asserts that the raw credential value does not appear in the output.
        The expected behaviour is that OpenShell masks credentials with
        asterisks or a ``***`` placeholder when displaying provider
        configuration.

        The test is skipped if no API keys are configured.
        """
        keys_to_check: dict[str, str] = {}
        for provider in ("anthropic", "openai", "gemini"):
            raw = _get_secret_value(test_settings, provider)
            if raw:
                keys_to_check[provider] = raw

        if not keys_to_check:
            pytest.skip(
                "No API keys are configured in the test environment; "
                "nothing to check for in provider output."
            )

        leaks: list[str] = []

        for provider, raw_key in keys_to_check.items():
            result: CommandResult = run_remote(
                spark_ssh,
                f"openshell provider get {provider} 2>&1 || echo ''",
                timeout=20,
            )
            provider_output = result.stdout

            if not provider_output.strip():
                # Provider not configured in OpenShell — nothing to check.
                continue

            if raw_key in provider_output:
                redacted_key = raw_key[:4] + "..." + raw_key[-4:]
                leaks.append(
                    f"  [{provider}] key ({redacted_key}) is visible in the output "
                    f"of 'openshell provider get {provider}'."
                )

        assert not leaks, (
            "Raw API key values found in 'openshell provider get' output:\n"
            + "\n".join(leaks)
            + "\nOpenShell must mask credential values in CLI output with '***' "
            "or a similar redaction placeholder. "
            "Check the OpenShell CLI rendering options for credential masking."
        )
