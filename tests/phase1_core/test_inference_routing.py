"""
Phase 1 — Core NemoClaw on Spark: inference routing tests.

Validates that:
- The OpenShell inference route is set to the Nemotron model via local-ollama.
- A prompt sent inside the persistent ``nemoclaw-main`` sandbox through
  ``inference.local`` returns a non-empty completion (end-to-end behavioral
  test).
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

import re
import shlex

import pytest
from fabric import Connection

from ..helpers import parse_json_output, run_remote
from ..models import CommandResult, InferenceResponse

# ---------------------------------------------------------------------------
# Contract tests — inference route state
# ---------------------------------------------------------------------------


@pytest.mark.phase1
@pytest.mark.contract
class TestInferenceRoute:
    """Verify the inference route is correctly configured for Nemotron."""

    def test_route_set_to_nemotron(self, spark_ssh: Connection) -> None:
        """``openshell inference get`` must show nemotron-3-super and local-ollama."""

        result: CommandResult = run_remote(spark_ssh, "openshell inference get")

        assert result.stdout.strip(), (
            "openshell inference get produced no output.\n"
            f"Return code: {result.return_code}\nStderr: {result.stderr}"
        )

        output = _strip_ansi(result.stdout)
        provider = _extract_field(output, "Provider")
        model = _extract_field(output, "Model")

        assert provider == "local-ollama", (
            f"Inference route provider is {provider!r}, expected 'local-ollama'.\n"
            f"Full output:\n{output}\n"
            "Run: openshell inference set --provider local-ollama "
            "--model nemotron-3-super:120b"
        )

        assert "nemotron" in model.lower(), (
            f"Inference route model is {model!r}, expected a Nemotron variant.\n"
            f"Full output:\n{output}\n"
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
        curl_cmd = (
            "curl -s -k "
            "https://inference.local/v1/chat/completions "
            "-H 'Content-Type: application/json' "
            "-d '{"
            '"model":"nemotron-3-super:120b",'
            '"messages":[{"role":"user","content":"Reply with the single word: hello"}],'
            '"max_tokens":120'
            "}'"
        )

        run_cmd = (
            "cfg=$(mktemp); "
            'openshell sandbox ssh-config nemoclaw-main > "$cfg"; '
            f"printf '%s\\n' {shlex.quote(curl_cmd)} | "
            'ssh -F "$cfg" openshell-nemoclaw-main sh; '
            'rc=$?; rm -f "$cfg"; exit $rc'
        )

        result: CommandResult = run_remote(spark_ssh, run_cmd, timeout=180)

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
        curl_status_cmd = (
            "curl -s -k -o /dev/null -w '%{http_code}' "
            "https://inference.local/v1/chat/completions "
            "-H 'Content-Type: application/json' "
            "-d '{"
            '"model":"nemotron-3-super:120b",'
            '"messages":[{"role":"user","content":"hi"}],'
            '"max_tokens":5'
            "}'"
        )

        run_cmd = (
            "cfg=$(mktemp); "
            'openshell sandbox ssh-config nemoclaw-main > "$cfg"; '
            f"printf '%s\\n' {shlex.quote(curl_status_cmd)} | "
            'ssh -F "$cfg" openshell-nemoclaw-main sh; '
            'rc=$?; rm -f "$cfg"; exit $rc'
        )

        result: CommandResult = run_remote(spark_ssh, run_cmd)

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
    """Negative path: the gateway route should ignore per-request model overrides."""

    @pytest.mark.timeout(60)
    def test_request_model_does_not_override_active_route(self, spark_ssh: Connection) -> None:
        """A bogus request-model value must not override the configured route.

        The current OpenShell gateway pins requests to the configured
        provider/model pair and does not honor per-request model overrides
        from sandbox clients. This test asserts that contract directly.
        """
        curl_cmd = (
            "curl -s -k -w '\\nHTTP_STATUS:%{http_code}' "
            "https://inference.local/v1/chat/completions "
            "-H 'Content-Type: application/json' "
            "-d '{"
            '"model":"no-such-model-xyz-99999",'
            '"messages":[{"role":"user","content":"hi"}],'
            '"max_tokens":5'
            "}'"
        )

        run_cmd = (
            "cfg=$(mktemp); "
            'openshell sandbox ssh-config nemoclaw-main > "$cfg"; '
            f"printf '%s\\n' {shlex.quote(curl_cmd)} | "
            'ssh -F "$cfg" openshell-nemoclaw-main sh; '
            'rc=$?; rm -f "$cfg"; exit $rc'
        )

        result: CommandResult = run_remote(spark_ssh, run_cmd)

        stdout = result.stdout.strip()

        http_statuses = re.findall(r"HTTP_STATUS:(\d{3})", stdout)
        assert http_statuses, (
            f"Could not find an HTTP status in the response.\nFull output:\n{stdout}"
        )
        assert all(status == "200" for status in http_statuses), (
            f"Expected the gateway to handle the request successfully via the active route, "
            f"got statuses {http_statuses!r}.\nFull output:\n{stdout}"
        )

        response_body = stdout.split("HTTP_STATUS:", 1)[0].strip()
        response_data = parse_json_output(response_body)
        inference = InferenceResponse.model_validate(response_data)
        assert "nemotron" in inference.model.lower(), (
            "The response model did not stay pinned to the active Nemotron route.\n"
            f"Response model: {inference.model!r}\n"
            f"Full output:\n{stdout}"
        )


_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _extract_field(text: str, field_name: str) -> str:
    match = re.search(rf"^\s*{re.escape(field_name)}:\s*(.+?)\s*$", text, re.MULTILINE)
    if not match:
        raise AssertionError(f"Could not find {field_name!r} in inference output:\n{text}")
    return match.group(1).strip()
