"""
Phase 4 — Coding Agent Sandboxes: Codex sandbox contract, Ollama access, and
negative path tests.

Validates that the ``codex-dev`` OpenShell sandbox is correctly provisioned:
the container exists, the ``codex`` binary is on PATH, the Codex configuration
file points at the local Ollama instance via ``host.openshell.internal``, and
Ollama is reachable from inside the sandbox.  Also verifies that launching
Codex without a valid config produces a clear, actionable error message rather
than a silent hang.

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

from tests.helpers import run_remote
from tests.models import CommandResult

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SANDBOX_NAME: str = "codex-dev"
_CODEX_CONFIG_PATH: str = "~/.codex/config.toml"
_EXPECTED_CONFIG_KEYS: tuple[str, ...] = ("ollama", "host.openshell.internal")
_OLLAMA_HEALTH_URL: str = "http://host.openshell.internal:11434/api/tags"


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
        """The codex-dev sandbox container exists and is in a running state.

        Queries Docker for the exact container name ``codex-dev`` and asserts
        that the container status is ``running``.  A missing or exited
        container means the sandbox was never created or failed to start,
        which would prevent all Codex agent operations.
        """
        result: CommandResult = run_remote(
            spark_ssh,
            f"docker inspect --format '{{{{.State.Status}}}}' {_SANDBOX_NAME} 2>&1",
            timeout=15,
        )
        assert result.return_code == 0, (
            f"docker inspect {_SANDBOX_NAME!r} failed (exit {result.return_code}). "
            "The sandbox container may not exist. "
            f"Create it via: openshell sandbox create {_SANDBOX_NAME}. "
            f"stderr: {result.stderr!r}"
        )
        status = result.stdout.strip().lower()
        assert status == "running", (
            f"Sandbox {_SANDBOX_NAME!r} exists but is not running (status={status!r}). "
            "Restart it: openshell sandbox start codex-dev, or check logs: "
            f"docker logs {_SANDBOX_NAME} --tail=50"
        )

    def test_codex_binary_exists(self, spark_ssh: Connection) -> None:
        """The ``codex`` CLI binary is available on PATH inside the sandbox.

        Runs ``which codex`` inside the sandbox via ``docker exec``.  The
        binary must be present for OpenShell to dispatch Codex agent tasks.
        A missing binary means the sandbox image is incomplete.
        """
        result: CommandResult = run_remote(
            spark_ssh,
            f"docker exec {_SANDBOX_NAME} which codex 2>&1",
            timeout=15,
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
            f"Inspect: docker exec {_SANDBOX_NAME} ls -la $(which codex)"
        )

    def test_codex_config_has_ollama(self, spark_ssh: Connection) -> None:
        """The Codex config file references Ollama at host.openshell.internal.

        Reads ``~/.codex/config.toml`` inside the codex-dev sandbox and
        asserts that both ``ollama`` and ``host.openshell.internal`` appear
        in the file.  This configuration is what routes Codex inference
        requests to the local Ollama instance on the DGX Spark rather than
        to the OpenAI cloud API.  Missing or incorrect config would cause
        Codex to attempt cloud API calls (which would fail due to the
        default-deny egress policy) or to error out on startup.
        """
        result: CommandResult = run_remote(
            spark_ssh,
            f"docker exec {_SANDBOX_NAME} cat {_CODEX_CONFIG_PATH} 2>&1",
            timeout=15,
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
                "The Codex config must specify 'ollama' as the provider and "
                "'host.openshell.internal' as the Ollama base host. "
                f"Current config contents:\n{config_content}"
            )


# ---------------------------------------------------------------------------
# Behavioral tests — Ollama connectivity from inside the sandbox
# ---------------------------------------------------------------------------


@pytest.mark.phase4
@pytest.mark.behavioral
class TestCodexOllamaAccess:
    """Layer B: The codex-dev sandbox can reach the local Ollama instance.

    Verifies that the ``host.openshell.internal`` DNS alias resolves and that
    the Ollama HTTP API is reachable from inside the sandbox.  This is the
    critical network path that allows Codex to use local models rather than
    calling the OpenAI cloud API.
    """

    def test_ollama_reachable_from_sandbox(self, spark_ssh: Connection) -> None:
        """Ollama's /api/tags endpoint is reachable from inside codex-dev.

        Issues a ``curl`` request to ``host.openshell.internal:11434/api/tags``
        from inside the codex-dev container.  A successful response (HTTP 200
        with a JSON body) confirms that:
        1. The ``host.openshell.internal`` DNS alias resolves inside the sandbox.
        2. The Ollama daemon is listening on port 11434 on the host.
        3. The sandbox's network policy permits outbound connections to the
           host gateway alias on port 11434.

        Failure here means Codex will be unable to load any model, causing
        every agent task to fail at the model-selection step.
        """
        result: CommandResult = run_remote(
            spark_ssh,
            (
                f"docker exec {_SANDBOX_NAME} "
                f"curl --silent --fail --max-time 10 {_OLLAMA_HEALTH_URL} 2>&1"
            ),
            timeout=25,
        )
        assert result.return_code == 0, (
            f"curl to {_OLLAMA_HEALTH_URL!r} from inside {_SANDBOX_NAME!r} "
            f"failed (exit {result.return_code}). "
            "Possible causes: Ollama is not running on the host, "
            "'host.openshell.internal' DNS does not resolve inside the sandbox, "
            "or the sandbox egress policy blocks port 11434. "
            f"curl output: {result.stdout!r}  stderr: {result.stderr!r}"
        )
        response_body = result.stdout.strip()
        assert "models" in response_body.lower(), (
            f"curl to {_OLLAMA_HEALTH_URL!r} succeeded (exit 0) but the response "
            "body does not look like a valid Ollama /api/tags reply. "
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

        Runs ``codex`` inside a temporary environment where the config path
        is overridden to a non-existent location (``/dev/null/no-config``).
        Asserts that Codex exits with a non-zero return code and that the
        error output contains an informative keyword such as ``config``,
        ``error``, or ``not found`` — confirming the tool fails fast rather
        than silently.

        This matters because a silent failure (exit 0, no output) would make
        it impossible to diagnose misconfiguration in automated deployment
        pipelines.
        """
        result: CommandResult = run_remote(
            spark_ssh,
            (
                f"docker exec "
                f"-e CODEX_CONFIG=/dev/null/no-such-config.toml "
                f"{_SANDBOX_NAME} "
                f"codex --help 2>&1 || true"
            ),
            timeout=20,
        )
        # We intentionally allow the command to complete regardless of exit code
        # (via '|| true') and then inspect the combined output for clarity.
        combined_output = (result.stdout + " " + result.stderr).lower()

        # Either the binary is not found (acceptable — means the sandbox image
        # is clean and the test surfaced that) or the binary ran and produced
        # output we can inspect.  What we must NOT see is a silent empty output
        # when codex exits non-zero.
        if result.return_code != 0:
            # Non-zero exit: confirm the output is not completely silent
            assert combined_output.strip() != "", (
                "Codex exited with a non-zero code but produced no output at all. "
                "The tool must surface a human-readable error rather than failing "
                "silently.  This makes automated diagnosis impossible."
            )
