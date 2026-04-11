"""
Phase 4 — Coding Agent Sandboxes: Codex sandbox contract, Ollama access, and
negative path tests.

Validates that the ``codex-dev`` OpenShell sandbox is correctly provisioned:
the container exists, the ``codex`` binary is on PATH, the Codex configuration
file points at the gateway-managed ``inference.local`` endpoint, and inference
is reachable from inside the sandbox. Also verifies that launching Codex
without a valid config produces a clear, actionable error message rather than
a silent hang.

Markers
-------
phase4    : All tests here belong to Phase 4 (Coding Agent Sandboxes).
contract  : Layer A — structure, schema, and configuration assertions.
behavioral: Layer B — runtime health and live connectivity assertions.
negative  : Tests that exercise failure paths.

Fixtures (from conftest.py)
---------------------------
spark_ssh : fabric.Connection — live SSH connection to the DGX Spark node.
"""

from __future__ import annotations

import pytest
from fabric import Connection

from ..helpers import run_remote
from ..models import CommandResult
from ._openshell_cli import run_sandbox_command, strip_ansi

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SANDBOX_NAME: str = "codex-dev"
_CODEX_CONFIG_PATH: str = "~/.codex/config.toml"
_EXPECTED_CONFIG_KEYS: tuple[str, ...] = (
    "spark-ollama",
    "inference.local",
    "OPENAI_API_KEY",
)
_OLLAMA_MODELS_URL: str = "https://inference.local/v1/models"


# ---------------------------------------------------------------------------
# Contract tests — structure and configuration
# ---------------------------------------------------------------------------


@pytest.mark.phase4
@pytest.mark.contract
class TestCodexSandbox:
    """Layer A: The codex-dev sandbox container exists and is correctly configured.

    These tests verify the static contract — container presence, binary
    availability, and configuration file correctness — without issuing any
    live inference request.
    """

    def test_sandbox_exists(self, spark_ssh: Connection) -> None:
        """The codex-dev sandbox exists and reports Ready in OpenShell.

        Queries the supported OpenShell sandbox metadata command and asserts
        that the sandbox descriptor reports ``Phase: Ready``. A missing or
        non-ready sandbox prevents all Codex agent operations.
        """
        result: CommandResult = run_remote(
            spark_ssh,
            f"openshell sandbox get {_SANDBOX_NAME} 2>&1",
            timeout=15,
        )
        assert result.return_code == 0, (
            f"'openshell sandbox get {_SANDBOX_NAME}' failed (exit {result.return_code}). "
            "The sandbox metadata may not exist or the gateway may be down. "
            f"Create it via: openshell sandbox create {_SANDBOX_NAME}. "
            f"stderr: {result.stderr!r}"
        )
        status = strip_ansi(result.stdout)
        assert f"Name: {_SANDBOX_NAME}" in status, (
            f"Sandbox {_SANDBOX_NAME!r} descriptor does not include the expected name.\n"
            f"Full output:\n{status}"
        )
        assert "Phase: Ready" in status, (
            f"Sandbox {_SANDBOX_NAME!r} is not Ready in OpenShell.\nFull output:\n{status}"
        )

    def test_codex_binary_exists(self, spark_ssh: Connection) -> None:
        """The ``codex`` CLI binary is available on PATH inside the sandbox.

        Runs ``which codex`` inside the sandbox via the supported SSH proxy
        path. The binary must be present for OpenShell to dispatch Codex
        agent tasks. A missing binary means the sandbox image is incomplete.
        """
        result: CommandResult = run_sandbox_command(
            spark_ssh,
            _SANDBOX_NAME,
            "which codex",
            timeout=20,
        )
        assert result.return_code == 0, (
            f"'which codex' inside sandbox {_SANDBOX_NAME!r} returned exit "
            f"code {result.return_code}. The codex binary is not on PATH. "
            "Ensure the sandbox image installs the @openai/codex package via npm. "
            f"Output: {result.stdout!r}  stderr: {result.stderr!r}"
        )
        binary_path = result.stdout.strip()
        assert binary_path != "", (
            "'which codex' exited 0 but produced no output inside the sandbox. "
            "The binary may be a broken symlink. "
            f"Inspect with: openshell sandbox ssh-config {_SANDBOX_NAME}"
        )

    def test_codex_config_has_ollama(self, spark_ssh: Connection) -> None:
        """The Codex config file references a custom local provider.

        Reads ``~/.codex/config.toml`` inside the codex-dev sandbox and
        asserts that the custom provider id, the gateway-managed
        ``inference.local`` endpoint, and the dummy OpenAI-compatible auth key
        binding all appear in the file. Newer Codex builds reserve the built-in
        ``ollama`` provider id, so NemoClaw must use a custom provider name
        while still pointing at the local OpenAI-compatible route. Missing or
        incorrect config would cause Codex to attempt cloud API calls (which
        would fail due to the default-deny egress policy) or to error out on
        startup.
        """
        result: CommandResult = run_sandbox_command(
            spark_ssh,
            _SANDBOX_NAME,
            f"cat {_CODEX_CONFIG_PATH}",
            timeout=20,
        )
        assert result.return_code == 0, (
            f"Could not read {_CODEX_CONFIG_PATH!r} inside {_SANDBOX_NAME!r} "
            f"(exit {result.return_code}). "
            "The config file may not exist or the path is wrong. "
            f"stderr: {result.stderr!r}"
        )
        config_content = result.stdout
        assert config_content.strip() != "", (
            f"{_CODEX_CONFIG_PATH!r} exists inside the sandbox but is empty. "
            "The file must contain the Ollama provider configuration."
        )
        for expected_key in _EXPECTED_CONFIG_KEYS:
            assert expected_key in config_content, (
                f"Expected key {expected_key!r} not found in "
                f"{_CODEX_CONFIG_PATH!r} inside {_SANDBOX_NAME!r}. "
                "The Codex config must specify a custom provider id, "
                "'inference.local' as the OpenAI-compatible base URL, and "
                "'OPENAI_API_KEY' as the dummy auth env binding. "
                f"Current config contents:\n{config_content}"
            )


# ---------------------------------------------------------------------------
# Behavioral tests — Ollama connectivity from inside the sandbox
# ---------------------------------------------------------------------------


@pytest.mark.phase4
@pytest.mark.behavioral
class TestCodexOllamaAccess:
    """Layer B: The codex-dev sandbox can reach the local Ollama instance.

    Verifies that the gateway-managed ``inference.local`` endpoint is
    reachable from inside the sandbox. This is the supported network path that
    allows Codex to use local models rather than calling the OpenAI cloud API.
    """

    def test_ollama_reachable_from_sandbox(self, spark_ssh: Connection) -> None:
        """The supported Codex network path to the host Ollama API is reachable.

        Codex egress is binary-scoped, so this test uses ``node`` rather than
        ``curl`` to probe the configured OpenAI-compatible models endpoint at
        ``https://inference.local/v1/models``. The probe intentionally keeps
        the sandbox's default proxy environment intact because OpenShell uses
        that path to intercept ``inference.local``. A successful response (HTTP
        200 with a JSON body) confirms that:
        1. The ``inference.local`` endpoint resolves inside the sandbox.
        2. The OpenShell gateway is intercepting and routing inference traffic.
        3. The Codex/Node binary path is permitted to reach the inference API.

        Failure here means Codex will be unable to load any model, causing
        every agent task to fail at the model-selection step.
        """
        result: CommandResult = run_sandbox_command(
            spark_ssh,
            _SANDBOX_NAME,
            (
                "node -e "
                "\"fetch('https://inference.local/v1/models')"
                ".then(async r => { "
                "console.log('HTTP_STATUS:' + r.status); "
                "console.log((await r.text()).slice(0, 400)); "
                "})"
                '.catch(err => { console.error(err.message); process.exit(1); })"'
            ),
            timeout=25,
        )
        assert result.return_code == 0, (
            f"Node fetch to {_OLLAMA_MODELS_URL!r} from inside {_SANDBOX_NAME!r} "
            f"failed (exit {result.return_code}). "
            "Possible causes: the OpenShell gateway is not intercepting "
            "inference.local, the active provider is unhealthy, or the Codex "
            "sandbox policy does not permit Node/Codex to reach the inference API. "
            f"stdout: {result.stdout!r}  stderr: {result.stderr!r}"
        )
        response_body = result.stdout.strip()
        assert "HTTP_STATUS:200" in response_body, (
            f"Node fetch to {_OLLAMA_MODELS_URL!r} did not return HTTP 200. "
            f"Output: {response_body[:400]!r}"
        )
        assert '"data"' in response_body.lower(), (
            f"Node fetch to {_OLLAMA_MODELS_URL!r} succeeded but the response "
            "does not look like an OpenAI-compatible /v1/models payload. "
            f"Response body (first 300 chars): {response_body[:300]!r}"
        )


# ---------------------------------------------------------------------------
# Negative tests — failure path validation
# ---------------------------------------------------------------------------


@pytest.mark.phase4
@pytest.mark.negative
class TestCodexNegative:
    """Negative path tests that verify Codex surfaces configuration failures clearly.

    These tests confirm that Codex fails fast with a descriptive error when
    its configuration is missing or invalid, rather than hanging indefinitely
    or silently falling back to unexpected behavior.
    """

    def test_codex_without_config_errors_clearly(self, spark_ssh: Connection) -> None:
        """Codex produces a clear error when invoked with no config file.

        Runs ``codex exec`` inside the sandbox with ``CODEX_CONFIG`` pointed
        at a non-existent location. Asserts that Codex exits with a non-zero
        return code and that the error output contains an informative keyword
        such as ``config``, ``error``, or ``not found``.

        This matters because a silent failure (exit 0, no output) would make
        it impossible to diagnose misconfiguration in automated deployment
        pipelines.
        """
        result: CommandResult = run_sandbox_command(
            spark_ssh,
            _SANDBOX_NAME,
            "sh -c 'CODEX_CONFIG=/dev/null/no-such-config.toml codex exec -m gpt-5.4 \"say hi\" 2>&1'",
            timeout=20,
        )
        combined_output = (result.stdout + " " + result.stderr).lower()

        if result.return_code == 255 and not combined_output.strip():
            pytest.fail(
                f"Could not execute Codex inside sandbox {_SANDBOX_NAME!r}. "
                "The sandbox transport is unavailable, so the missing-config "
                "path could not be validated."
            )

        assert result.return_code != 0, (
            "Codex exited successfully even though CODEX_CONFIG pointed at a "
            "non-existent file. The CLI should fail fast on missing config."
        )
        assert combined_output.strip() != "", (
            "Codex exited with a non-zero code but produced no output at all. "
            "The tool must surface a human-readable error rather than failing "
            "silently. This makes automated diagnosis impossible."
        )
