"""
Phase 3 — Raspberry Pi Infrastructure: LiteLLM degraded-mode tests.

Verifies that the LiteLLM proxy on the Raspberry Pi behaves gracefully when
one of the upstream Ollama backends becomes unavailable.

Design
------
These tests work by temporarily making the Mac Studio Ollama endpoint
unreachable from the test runner's perspective and checking that:

  1. The Spark-backed (nemotron) route continues to succeed.
  2. The Mac-backed (qwen3) route fails fast with a structured error rather
     than hanging until the proxy's own timeout fires.
  3. Error responses from the proxy are always JSON, never raw proxy
     tracebacks or HTML error pages.

Because we cannot safely stop Ollama on the Mac Studio mid-suite, the tests
simulate an unreachable Mac by routing to a deliberately wrong port or by
checking the proxy's behaviour when the Mac's Ollama is simply offline.
The ``@pytest.mark.skipif`` guards each test so it only runs when the Mac
is actually unreachable, preventing false failures in a healthy environment.

Markers
-------
phase3     : All tests belong to Phase 3.
behavioral : Hit real network sockets.
"""

from __future__ import annotations

import pytest
import httpx

from tests.settings import TestSettings

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_CHAT_ENDPOINT = "/v1/chat/completions"
_PING_MESSAGES = [{"role": "user", "content": "Reply with the single word: pong"}]

_NEMOTRON_SUBSTR = "nemotron"
_QWEN_FULL_MODEL = "qwen3:8b"

# How long we allow qwen3 to fail before declaring a hang (seconds).
_FAIL_FAST_TIMEOUT = 30


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mac_ollama_reachable(test_settings: TestSettings) -> bool:
    """Return True when the Mac Studio Ollama API is reachable.

    Performs a lightweight GET /api/tags with a 5-second timeout.  Used by
    ``skipif`` conditions to decide whether degraded-mode tests should run.
    """
    mac_ollama_url = test_settings.mac.ollama_base_url
    try:
        response = httpx.get(f"{mac_ollama_url}/api/tags", timeout=5)
        return response.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException):
        return False


def _first_nemotron_model(litellm_url: str) -> str | None:
    """Return the first nemotron model id from the LiteLLM catalogue, or None."""
    try:
        response = httpx.get(f"{litellm_url}/v1/models", timeout=10)
        if response.status_code != 200:
            return None
        data = response.json().get("data", [])
        for entry in data:
            if _NEMOTRON_SUBSTR in entry.get("id", "").lower():
                return entry["id"]
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def litellm_url(test_settings: TestSettings) -> str:
    """Return the LiteLLM proxy base URL from settings."""
    return test_settings.pi.litellm_base_url


@pytest.fixture(scope="module")
def mac_down(test_settings: TestSettings) -> bool:
    """Return True when the Mac Studio Ollama is unreachable from this host."""
    return not _mac_ollama_reachable(test_settings)


# ---------------------------------------------------------------------------
# Degraded-mode behavioral tests
# ---------------------------------------------------------------------------


@pytest.mark.phase3
@pytest.mark.behavioral
class TestDegradedMode:
    """Verify proxy resilience when the Mac Studio backend is unavailable."""

    def test_spark_model_works_when_mac_down(
        self, litellm_url: str, mac_down: bool, test_settings: TestSettings
    ) -> None:
        """nemotron inference must succeed even when the Mac Ollama is unreachable.

        The proxy routes nemotron requests exclusively to the DGX Spark.  A
        Mac outage should have zero impact on Spark-backed models.  This test
        runs in both healthy and degraded environments and asserts the positive
        case to confirm no cross-contamination between backend routes.

        If the Mac is currently reachable this test still validates that the
        Spark route works independently; it just does not prove isolation.
        """
        nemotron_model = _first_nemotron_model(litellm_url)
        if nemotron_model is None:
            pytest.skip(
                "No nemotron model found in LiteLLM catalogue — "
                "skipping Spark-path degraded test."
            )

        try:
            response = httpx.post(
                f"{litellm_url}{_CHAT_ENDPOINT}",
                json={"model": nemotron_model, "messages": _PING_MESSAGES},
                timeout=120,
            )
        except httpx.ConnectError as exc:
            pytest.fail(
                f"LiteLLM proxy is unreachable during degraded-mode test: {exc}"
            )
        except httpx.TimeoutException:
            pytest.fail(
                f"nemotron request timed out after 120 s in degraded-mode test.\n"
                f"Model: {nemotron_model}\n"
                "Verify Ollama is running on the DGX Spark."
            )

        mac_status = "DOWN" if mac_down else "UP"
        assert response.status_code == 200, (
            f"nemotron inference failed (Mac is {mac_status}).\n"
            f"Status: {response.status_code}\n"
            f"Body: {response.text[:500]!r}\n"
            "The Spark-backed nemotron route must be independent of Mac availability."
        )

        payload = response.json()
        assert "choices" in payload and len(payload["choices"]) > 0, (
            f"nemotron response contains no choices (Mac is {mac_status})."
        )

    def test_mac_model_fails_gracefully_when_mac_down(
        self, litellm_url: str, mac_down: bool
    ) -> None:
        """qwen3 requests must fail fast (not hang) when Mac Ollama is unreachable.

        When the Mac Studio is offline the LiteLLM proxy should not hold the
        connection open until its own backend timeout fires.  We assert that a
        response (even an error response) arrives within ``_FAIL_FAST_TIMEOUT``
        seconds.

        This test is skipped when the Mac is reachable because in that case
        qwen3 would succeed and we cannot observe failure behaviour.
        """
        if not mac_down:
            pytest.skip(
                "Mac Studio Ollama is reachable — skipping graceful-failure test. "
                "Run this test with the Mac Ollama service stopped to validate "
                "fail-fast behaviour."
            )

        try:
            response = httpx.post(
                f"{litellm_url}{_CHAT_ENDPOINT}",
                json={"model": _QWEN_FULL_MODEL, "messages": _PING_MESSAGES},
                timeout=_FAIL_FAST_TIMEOUT,
            )
        except httpx.TimeoutException:
            pytest.fail(
                f"LiteLLM proxy hung for {_FAIL_FAST_TIMEOUT} s when Mac is down.\n"
                f"Model: {_QWEN_FULL_MODEL}\n"
                "The proxy must propagate backend timeout errors quickly rather "
                "than holding the client connection open.\n"
                "Check the 'request_timeout' and 'num_retries' settings in the "
                "LiteLLM config.yaml on the Pi."
            )
        except httpx.ConnectError as exc:
            pytest.fail(f"LiteLLM proxy is unreachable: {exc}")

        # Any status code is acceptable here as long as we got a response
        # in time — the important thing is the proxy did not hang.
        assert response.status_code >= 400, (
            f"qwen3 request unexpectedly returned {response.status_code} "
            f"when Mac is down.\n"
            f"Body: {response.text[:400]!r}"
        )

    def test_error_response_is_structured(
        self, litellm_url: str, mac_down: bool
    ) -> None:
        """Error responses from the proxy must be JSON, not raw tracebacks or HTML.

        Submits a request that is guaranteed to produce an error (either because
        the Mac is down and qwen3 fails, or because we use a deliberately bad
        model name) and asserts that the response body is valid JSON containing
        a ``message`` field in the standard OpenAI error envelope.

        This test always runs regardless of Mac availability because we can
        always produce an error by requesting a non-existent model.
        """
        if mac_down:
            # Use the real qwen3 model so the error comes from a backend failure.
            error_model = _QWEN_FULL_MODEL
        else:
            # Use a model that cannot possibly be routed to force an error.
            error_model = "nonexistent/degraded-test-model:latest"

        try:
            response = httpx.post(
                f"{litellm_url}{_CHAT_ENDPOINT}",
                json={"model": error_model, "messages": _PING_MESSAGES},
                timeout=_FAIL_FAST_TIMEOUT,
            )
        except httpx.TimeoutException:
            pytest.fail(
                f"LiteLLM proxy timed out ({_FAIL_FAST_TIMEOUT} s) during "
                f"structured-error test for model={error_model!r}."
            )
        except httpx.ConnectError as exc:
            pytest.fail(f"LiteLLM proxy is unreachable: {exc}")

        assert response.status_code >= 400, (
            f"Expected an error status for model={error_model!r}, "
            f"got {response.status_code}."
        )

        # The response body must be parseable JSON.
        try:
            body = response.json()
        except Exception:
            pytest.fail(
                f"LiteLLM error response is not valid JSON.\n"
                f"Status: {response.status_code}\n"
                f"Content-Type: {response.headers.get('content-type', '(none)')}\n"
                f"Body (first 500 chars): {response.text[:500]!r}\n"
                "LiteLLM must always return JSON error envelopes, not raw "
                "tracebacks or HTML error pages."
            )

        # The JSON must contain an error message.  LiteLLM wraps errors in
        # either {"error": {"message": "..."}} or {"detail": "..."}.
        has_message = (
            ("error" in body and isinstance(body["error"], dict) and "message" in body["error"])
            or "message" in body
            or "detail" in body
        )
        assert has_message, (
            f"LiteLLM error JSON does not contain a 'message' or 'detail' field.\n"
            f"Status: {response.status_code}\n"
            f"Body: {body!r}\n"
            "Expected structure: {{\"error\": {{\"message\": \"...\"}}}}"
        )
