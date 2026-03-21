"""
Phase 1 — Core NemoClaw on Spark: inference routing tests.

Validates that:
- The OpenShell inference route is set to the Nemotron model via local-ollama.
- A prompt sent inside a temporary sandbox through ``inference.local`` returns
  a non-empty completion (end-to-end behavioral test).
- The inference endpoint does not return a 502 (provider unreachable) error.
- Requesting a model name that does not exist surfaces an appropriate error.

Markers
-------
phase1     : All tests in this module belong to Phase 1.
contract   : Layer-A schema / state checks (test_route_set_to_nemotron).
behavioral : Layer-B end-to-end inference tests.
slow       : Tests that involve cold model loading (>30 s).
"""

from __future__ import annotations

import json
import uuid

import pytest
from fabric import Connection

from ..models import CommandResult, OpenShellInferenceRoute, InferenceResponse
from ..helpers import run_remote, parse_json_output


# ---------------------------------------------------------------------------
# Contract tests — inference route state
# ---------------------------------------------------------------------------


@pytest.mark.phase1
@pytest.mark.contract
class TestInferenceRoute:
    """Verify the inference route is correctly configured for Nemotron."""

    def test_route_set_to_nemotron(self, spark_ssh: Connection) -> None:
        """``openshell inference get`` must show nemotron-3-super and local-ollama.

        Parses the output into an OpenShellInferenceRoute model and asserts
        both the provider name and the model identifier, ensuring that a
        route switch to a cloud provider or a wrong model would be caught.
        """
        result: CommandResult = run_remote(spark_ssh, "openshell inference get --json")

        assert result.stdout.strip(), (
            "openshell inference get --json produced no output.\n"
            f"Return code: {result.return_code}\nStderr: {result.stderr}"
        )

        route_data = parse_json_output(result.stdout)
        route = OpenShellInferenceRoute.model_validate(route_data)

        assert route.provider == "local-ollama", (
            f"Inference route provider is {route.provider!r}, expected 'local-ollama'.\n"
            "Run: openshell inference set --provider local-ollama "
            "--model nemotron-3-super:120b"
        )

        assert "nemotron" in route.model.lower(), (
            f"Inference route model is {route.model!r}, expected a Nemotron variant.\n"
            "Run: openshell inference set --provider local-ollama "
            "--model nemotron-3-super:120b"
        )


# ---------------------------------------------------------------------------
# Behavioral tests — live inference through inference.local
# ---------------------------------------------------------------------------


@pytest.mark.phase1
@pytest.mark.behavioral
class TestInferenceLocal:
    """End-to-end: a prompt sent through the sandbox inference route returns a completion."""

    @pytest.mark.slow
    @pytest.mark.timeout(120)
    def test_inference_returns_completion(self, spark_ssh: Connection) -> None:
        """Create a temp sandbox, send a prompt to inference.local, parse the response.

        The sandbox curl call targets ``https://inference.local/v1/chat/completions``
        (the OpenShell-intercepted route) rather than Ollama directly.  This
        validates the full request path:

            sandbox → inference.local → OpenShell gateway → local-ollama provider
            → Ollama 11434 → Nemotron model

        The response is parsed into an InferenceResponse Pydantic model and the
        first choice's message content is asserted to be non-empty.

        Timeout is 120 s to accommodate Nemotron's cold-start GPU load time.
        """
        # Build the curl command that will run INSIDE the sandbox
        curl_cmd = (
            "curl -s "
            "--cacert /etc/openshell/ca.crt "
            "https://inference.local/v1/chat/completions "
            "-H 'Content-Type: application/json' "
            "-d '{"
            '"model":"nemotron-3-super:120b",'
            '"messages":[{"role":"user","content":"Reply with the single word: hello"}],'
            '"max_tokens":20'
            "}'"
        )

        # Run the curl inside a temporary, non-persistent sandbox so we don't
        # accumulate leftover sandboxes across test runs.
        sandbox_name = f"test-infer-{uuid.uuid4().hex[:8]}"
        run_cmd = (
            f"openshell sandbox run "
            f"--name {sandbox_name} "
            f"-- {curl_cmd}"
        )

        result: CommandResult = run_remote(spark_ssh, run_cmd)

        # Clean up — best-effort, ignore errors
        run_remote(
            spark_ssh,
            f"openshell sandbox delete {sandbox_name} 2>/dev/null || true",
        )

        assert result.stdout.strip(), (
            "Sandbox inference call produced no stdout output.\n"
            f"Stderr:\n{result.stderr}\n"
            "Check that the gateway is running and inference.local is routed."
        )

        response_data = parse_json_output(result.stdout)
        inference = InferenceResponse.model_validate(response_data)

        assert inference.choices, (
            "InferenceResponse.choices list is empty — no completion was returned.\n"
            f"Full response: {result.stdout[:1000]}"
        )

        content = inference.choices[0].message.content
        assert content and content.strip(), (
            "choices[0].message.content is empty or whitespace-only.\n"
            f"Full response: {result.stdout[:1000]}"
        )

    @pytest.mark.timeout(90)
    def test_inference_not_502(self, spark_ssh: Connection) -> None:
        """inference.local must not return HTTP 502 (provider unreachable).

        A 502 means the OpenShell gateway cannot reach the Ollama backend —
        either Ollama is down, the provider URL is wrong, or the model is not
        loaded.  This test catches that failure mode without waiting for a
        full completion.
        """
        sandbox_name = f"test-502-{uuid.uuid4().hex[:8]}"
        curl_status_cmd = (
            "curl -s -o /dev/null -w '%{http_code}' "
            "--cacert /etc/openshell/ca.crt "
            "https://inference.local/v1/chat/completions "
            "-H 'Content-Type: application/json' "
            "-d '{"
            '"model":"nemotron-3-super:120b",'
            '"messages":[{"role":"user","content":"hi"}],'
            '"max_tokens":5'
            "}'"
        )

        run_cmd = (
            f"openshell sandbox run "
            f"--name {sandbox_name} "
            f"-- {curl_status_cmd}"
        )

        result: CommandResult = run_remote(spark_ssh, run_cmd)

        # Clean up — best-effort
        run_remote(
            spark_ssh,
            f"openshell sandbox delete {sandbox_name} 2>/dev/null || true",
        )

        http_status = result.stdout.strip()

        assert http_status != "502", (
            "inference.local returned HTTP 502 (Bad Gateway).\n"
            "The OpenShell gateway cannot reach the Ollama backend.\n"
            "Diagnose:\n"
            "  1. Check Ollama is running: systemctl status ollama\n"
            "  2. Verify provider URL: openshell provider get local-ollama\n"
            "  3. Check gateway logs: openshell gateway logs"
        )

        # Also fail on connection refused (000) which curl writes as "000"
        assert http_status != "000", (
            "curl returned status 000 — connection refused or timeout.\n"
            "inference.local may not be resolving inside the sandbox."
        )


# ---------------------------------------------------------------------------
# Negative tests — bad model name must return an error, not a completion
# ---------------------------------------------------------------------------


@pytest.mark.phase1
class TestInferenceNegative:
    """Negative path: requesting a non-existent model must surface an error."""

    @pytest.mark.timeout(60)
    def test_wrong_model_name_error(self, spark_ssh: Connection) -> None:
        """Requesting model 'no-such-model-xyz' must return an error response.

        A well-behaved inference gateway should return 4xx (model not found)
        or 5xx (provider rejected), not silently return an empty completion.
        The test checks that the response contains an error field or a
        non-2xx HTTP status code.
        """
        sandbox_name = f"test-badmodel-{uuid.uuid4().hex[:8]}"
        curl_cmd = (
            "curl -s -w '\\nHTTP_STATUS:%{http_code}' "
            "--cacert /etc/openshell/ca.crt "
            "https://inference.local/v1/chat/completions "
            "-H 'Content-Type: application/json' "
            "-d '{"
            '"model":"no-such-model-xyz-99999",'
            '"messages":[{"role":"user","content":"hi"}],'
            '"max_tokens":5'
            "}'"
        )

        run_cmd = (
            f"openshell sandbox run "
            f"--name {sandbox_name} "
            f"-- {curl_cmd}"
        )

        result: CommandResult = run_remote(spark_ssh, run_cmd)

        # Clean up — best-effort
        run_remote(
            spark_ssh,
            f"openshell sandbox delete {sandbox_name} 2>/dev/null || true",
        )

        stdout = result.stdout.strip()

        # Extract HTTP status if present in the curl -w output
        http_status: str | None = None
        for line in stdout.splitlines():
            if line.startswith("HTTP_STATUS:"):
                http_status = line.split(":", 1)[1].strip()
                break

        # If we got an HTTP status, it must not be 2xx
        if http_status:
            assert not http_status.startswith("2"), (
                f"Expected a non-2xx HTTP status for unknown model, got {http_status}.\n"
                f"Full output:\n{stdout}"
            )
        else:
            # Without HTTP status, look for error keywords in the body
            lower_out = stdout.lower()
            error_keywords = {"error", "not found", "unknown model", "does not exist"}
            assert any(kw in lower_out for kw in error_keywords), (
                "Expected an error response for unknown model 'no-such-model-xyz-99999' "
                "but the output contained no error indicators.\n"
                f"Full output:\n{stdout}"
            )
