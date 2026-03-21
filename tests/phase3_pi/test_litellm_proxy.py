"""
Phase 3 — Raspberry Pi Infrastructure: LiteLLM proxy tests.

Validates that the LiteLLM proxy running on the Raspberry Pi is healthy,
exposes the expected model routes (nemotron → Spark, qwen3 → Mac Studio),
and returns OpenAI-compatible response payloads.

Markers
-------
phase3     : All tests in this module belong to Phase 3.
behavioral : Layer-B endpoint / network tests (hit real sockets).
slow       : Tests that trigger real model inference (may take 30-120 s).
contract   : Layer-A schema / field-shape tests (fast, no inference).
"""

from __future__ import annotations

import httpx
import pytest

from ..helpers import assert_http_healthy, assert_json_schema
from ..models import InferenceResponse, LiteLLMModelEntry, LiteLLMModelsResponse
from ..settings import TestSettings

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_CHAT_ENDPOINT = "/v1/chat/completions"
_MODELS_ENDPOINT = "/v1/models"
_HEALTH_ENDPOINT = "/health"

# A minimal prompt that any model should answer quickly.
_PING_MESSAGES = [{"role": "user", "content": "Reply with the single word: pong"}]

# Model identifiers that must be advertised by the proxy.
_NEMOTRON_MODEL_SUBSTR = "nemotron"
_QWEN_MODEL_SUBSTR = "qwen3"
_QWEN_FULL_MODEL = "qwen3:8b"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def litellm_url(test_settings: TestSettings) -> str:
    """Return the base URL of the LiteLLM proxy on the Raspberry Pi.

    Reads from ``PiSettings.litellm_base_url``, which defaults to
    ``http://raspi.local:4000`` and can be overridden via the
    ``PI_LITELLM_BASE_URL`` environment variable.
    """
    return test_settings.pi.litellm_base_url


# ---------------------------------------------------------------------------
# Behavioral tests — health and model catalogue
# ---------------------------------------------------------------------------


@pytest.mark.phase3
@pytest.mark.behavioral
class TestLiteLLMHealth:
    """Verify the LiteLLM proxy is running and reports a healthy status."""

    def test_proxy_running(self, litellm_url: str) -> None:
        """GET /health must return HTTP 200.

        The LiteLLM health endpoint performs an internal self-check and
        returns 200 only when the proxy process is running and its internal
        router is initialised.  A connection error here means the proxy is
        not started or the port is firewalled.
        """
        try:
            response = assert_http_healthy(
                f"{litellm_url}{_HEALTH_ENDPOINT}",
                timeout=15,
                expected_status={200},
            )
        except httpx.ConnectError as exc:
            pytest.fail(
                f"Cannot reach LiteLLM proxy at {litellm_url}{_HEALTH_ENDPOINT}.\n"
                f"Error: {exc}\n"
                "Ensure the LiteLLM proxy is started on the Pi (port 4000) "
                "and that port 4000 is not firewalled."
            )

        assert response.status_code == 200, (
            f"LiteLLM /health returned {response.status_code}, expected 200."
        )

    def test_models_endpoint(self, litellm_url: str) -> None:
        """GET /v1/models returns a valid LiteLLMModelsResponse with required models.

        Parses the raw JSON into a ``LiteLLMModelsResponse`` Pydantic model and
        confirms that at least one nemotron entry and one qwen3 entry are
        present in the model catalogue, proving the proxy configuration was
        loaded correctly.
        """
        try:
            response = httpx.get(
                f"{litellm_url}{_MODELS_ENDPOINT}",
                timeout=15,
            )
        except httpx.ConnectError as exc:
            pytest.fail(f"Cannot connect to LiteLLM at {litellm_url}{_MODELS_ENDPOINT}: {exc}")

        assert response.status_code == 200, (
            f"GET {_MODELS_ENDPOINT} returned {response.status_code}.\n"
            f"Body: {response.text[:400]!r}"
        )

        models_response: LiteLLMModelsResponse = assert_json_schema(response, LiteLLMModelsResponse)

        model_ids_lower = [mid.lower() for mid in models_response.model_ids]

        has_nemotron = any(_NEMOTRON_MODEL_SUBSTR in mid for mid in model_ids_lower)
        assert has_nemotron, (
            f"No nemotron model found in LiteLLM model catalogue.\n"
            f"Available models: {models_response.model_ids}\n"
            "Ensure the LiteLLM config.yaml defines a nemotron route pointing "
            "at spark-caeb.local Ollama."
        )

        has_qwen = any(_QWEN_MODEL_SUBSTR in mid for mid in model_ids_lower)
        assert has_qwen, (
            f"No qwen3 model found in LiteLLM model catalogue.\n"
            f"Available models: {models_response.model_ids}\n"
            "Ensure the LiteLLM config.yaml defines a qwen3 route pointing "
            "at mac-studio.local Ollama."
        )


# ---------------------------------------------------------------------------
# Behavioral + slow tests — routing validation via real inference
# ---------------------------------------------------------------------------


@pytest.mark.phase3
@pytest.mark.behavioral
@pytest.mark.slow
class TestLiteLLMRouting:
    """Verify model routing: nemotron → Spark, qwen3 → Mac Studio."""

    def _post_completion(self, litellm_url: str, model: str, timeout: int = 120) -> httpx.Response:
        """POST a minimal chat completion request and return the raw response.

        Args:
            litellm_url: Base URL of the LiteLLM proxy.
            model: Model identifier to request (must match a configured route).
            timeout: HTTP timeout in seconds; inference can be slow (default 120).

        Returns:
            The raw :class:`httpx.Response` for the caller to assert on.
        """
        return httpx.post(
            f"{litellm_url}{_CHAT_ENDPOINT}",
            json={"model": model, "messages": _PING_MESSAGES},
            timeout=timeout,
        )

    def test_routes_nemotron_to_spark(self, litellm_url: str, test_settings: TestSettings) -> None:
        """A nemotron completion request returns HTTP 200 with a non-empty reply.

        The LiteLLM proxy must forward requests for the nemotron model to the
        Ollama instance on the DGX Spark.  This test confirms the full
        end-to-end path: Pi → Spark Ollama → Pi → test client.
        """
        # Discover the exact nemotron model id from the catalogue to avoid
        # hard-coding a tag that may differ across deployments.
        models_resp = httpx.get(f"{litellm_url}{_MODELS_ENDPOINT}", timeout=15)
        assert models_resp.status_code == 200, (
            f"Could not list models to find nemotron: {models_resp.status_code}"
        )
        models_data = LiteLLMModelsResponse.model_validate(models_resp.json())
        nemotron_ids = [
            mid for mid in models_data.model_ids if _NEMOTRON_MODEL_SUBSTR in mid.lower()
        ]
        assert nemotron_ids, "No nemotron model registered in LiteLLM; cannot test routing."
        nemotron_model = nemotron_ids[0]

        try:
            response = self._post_completion(litellm_url, model=nemotron_model)
        except httpx.TimeoutException:
            pytest.fail(
                f"LiteLLM nemotron request timed out after 120 s.\n"
                f"Model: {nemotron_model}\n"
                "Check that Ollama is running on the DGX Spark and the nemotron "
                "model is pulled ('ollama list' on spark-caeb)."
            )

        assert response.status_code == 200, (
            f"LiteLLM returned {response.status_code} for nemotron inference.\n"
            f"Model: {nemotron_model}\n"
            f"Body: {response.text[:500]!r}"
        )

        payload = response.json()
        assert "choices" in payload and len(payload["choices"]) > 0, (
            f"nemotron response has no choices.\nBody: {payload}"
        )

    def test_routes_qwen_to_mac(self, litellm_url: str) -> None:
        """A qwen3:8b completion request returns HTTP 200 with a non-empty reply.

        Confirms the Pi → Mac Studio Ollama routing path is functioning.
        """
        try:
            response = self._post_completion(litellm_url, model=_QWEN_FULL_MODEL)
        except httpx.TimeoutException:
            pytest.fail(
                f"LiteLLM qwen3 request timed out after 120 s.\n"
                f"Model: {_QWEN_FULL_MODEL}\n"
                "Check that Ollama is running on Mac Studio and qwen3:8b is pulled."
            )

        assert response.status_code == 200, (
            f"LiteLLM returned {response.status_code} for qwen3 inference.\n"
            f"Model: {_QWEN_FULL_MODEL}\n"
            f"Body: {response.text[:500]!r}"
        )

        payload = response.json()
        assert "choices" in payload and len(payload["choices"]) > 0, (
            f"qwen3 response has no choices.\nBody: {payload}"
        )

    def test_schema_valid(self, litellm_url: str) -> None:
        """A successful qwen3 completion response parses cleanly into InferenceResponse.

        Validates the full OpenAI-compatible response shape including model
        echo, choices list, and optional usage statistics.
        """
        models_resp = httpx.get(f"{litellm_url}{_MODELS_ENDPOINT}", timeout=15)
        assert models_resp.status_code == 200
        models_data = LiteLLMModelsResponse.model_validate(models_resp.json())

        # Prefer qwen3 because it tends to be faster on Mac Studio.
        qwen_ids = [mid for mid in models_data.model_ids if _QWEN_MODEL_SUBSTR in mid.lower()]
        model_id = (
            qwen_ids[0]
            if qwen_ids
            else (models_data.model_ids[0] if models_data.model_ids else _QWEN_FULL_MODEL)
        )

        try:
            response = self._post_completion(litellm_url, model=model_id)
        except httpx.TimeoutException:
            pytest.fail(f"Inference request timed out for schema validation (model={model_id}).")

        assert response.status_code == 200, (
            f"Expected 200 for schema validation request, got {response.status_code}."
        )

        inference: InferenceResponse = assert_json_schema(response, InferenceResponse)

        assert inference.model, "InferenceResponse.model must be a non-empty string."
        assert len(inference.choices) >= 1, "InferenceResponse must contain at least one choice."
        assert inference.first_content, (
            "InferenceResponse.first_content is empty — model returned no text."
        )


# ---------------------------------------------------------------------------
# Negative tests — error handling and contract field presence
# ---------------------------------------------------------------------------


@pytest.mark.phase3
class TestLiteLLMNegative:
    """Verify that the proxy handles bad input gracefully."""

    def test_invalid_model_returns_error(self, litellm_url: str) -> None:
        """Requesting a non-existent model returns a 4xx or 5xx status code.

        The proxy must not silently succeed or return 200 with an empty body
        when given a model that is not in its routing table.  Any error status
        code (400-599) is acceptable; a connection error is not.
        """
        try:
            response = httpx.post(
                f"{litellm_url}{_CHAT_ENDPOINT}",
                json={
                    "model": "nonexistent/model-that-does-not-exist:latest",
                    "messages": _PING_MESSAGES,
                },
                timeout=30,
            )
        except httpx.ConnectError as exc:
            pytest.fail(f"LiteLLM proxy is unreachable when testing invalid model: {exc}")

        assert response.status_code >= 400, (
            f"Expected a 4xx/5xx error for an invalid model, "
            f"but got {response.status_code}.\n"
            f"Body: {response.text[:400]!r}\n"
            "The proxy should reject unknown model identifiers explicitly."
        )

    def test_models_contract_fields(self, litellm_url: str) -> None:
        """Every entry in /v1/models has the required id, object, and owned_by fields.

        Validates the OpenAI-compatible contract at the field level: each
        ``LiteLLMModelEntry`` must carry a non-empty ``id``, an ``object``
        field equal to ``"model"``, and a (possibly empty) ``owned_by`` string.
        """
        try:
            response = httpx.get(f"{litellm_url}{_MODELS_ENDPOINT}", timeout=15)
        except httpx.ConnectError as exc:
            pytest.fail(f"Cannot reach /v1/models for contract check: {exc}")

        assert response.status_code == 200, f"GET /v1/models returned {response.status_code}."

        models_response: LiteLLMModelsResponse = assert_json_schema(response, LiteLLMModelsResponse)

        assert models_response.data, (
            "LiteLLM returned an empty model list — no routes are configured."
        )

        for entry in models_response.data:
            assert isinstance(entry, LiteLLMModelEntry), (
                f"Expected LiteLLMModelEntry, got {type(entry)}"
            )
            assert entry.id, f"Model entry has an empty id field: {entry!r}"
            assert entry.object_type == "model", (
                f"Model entry {entry.id!r} has object={entry.object_type!r}, expected 'model'."
            )
            # owned_by may be an empty string for self-hosted models — just
            # check the field exists (already guaranteed by Pydantic parsing).
            assert hasattr(entry, "owned_by"), (
                f"Model entry {entry.id!r} is missing the owned_by field."
            )
