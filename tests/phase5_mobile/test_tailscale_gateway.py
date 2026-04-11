"""
Phase 5 — Mobile / Tailscale: Tailscale gateway access tests.

Validates that the supported remote UI entrypoint and the Ollama inference API
are reachable from the Tailscale overlay network. These tests confirm that a
user on a mobile device or remote workstation connected to Tailscale can reach
the documented UI URL and issue inference requests without being on the same
physical LAN.

The Ollama check is skipped automatically when ``SPARK_TAILSCALE_IP`` is not
configured in the test environment, since there is nothing to connect to.

Markers
-------
phase5    : All tests here belong to Phase 5 (Mobile / Tailscale).
behavioral: Layer B — live HTTP connectivity tests over the Tailscale overlay.

Fixtures (from conftest.py)
---------------------------
spark_tailscale_ip : str | None — Tailscale IP of the DGX Spark (100.x.x.x).
"""

from __future__ import annotations

import os

import pytest

from ..helpers import assert_http_healthy

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_OLLAMA_PORT: int = 11434
_TAILSCALE_SERVE_URL_ENV: str = "SPARK_TAILSCALE_SERVE_URL"
_DEFAULT_TAILSCALE_SERVE_URL: str = "https://spark-caeb.tail48bab7.ts.net/"

# Timeout for Tailscale-routed HTTP requests.  Tailscale adds a small WireGuard
# overhead on first connection; 15 seconds is generous for a LAN-local peer.
_TAILSCALE_HTTP_TIMEOUT: int = 15


# ---------------------------------------------------------------------------
# Skip helper
# ---------------------------------------------------------------------------


def _require_tailscale_ip(tailscale_ip: str | None) -> None:
    """Skip the test if the Tailscale IP is not configured.

    Args:
        tailscale_ip: Value of the ``spark_tailscale_ip`` fixture, which is
            ``None`` when ``SPARK_TAILSCALE_IP`` is not set in the environment.
    """
    if tailscale_ip is None:
        pytest.skip(
            "SPARK_TAILSCALE_IP is not set in the test environment. "
            "Configure it in tests/.env or as an environment variable to run "
            "Tailscale connectivity tests: SPARK_TAILSCALE_IP=100.x.x.x"
        )


def _tailscale_serve_url() -> str:
    """Return the supported remote UI URL, preferring an explicit override."""
    return os.environ.get(_TAILSCALE_SERVE_URL_ENV, _DEFAULT_TAILSCALE_SERVE_URL)


# ---------------------------------------------------------------------------
# Behavioral tests — Tailscale HTTP connectivity
# ---------------------------------------------------------------------------


@pytest.mark.phase5
@pytest.mark.behavioral
class TestTailscaleAccess:
    """Layer B: the remote UI and Ollama API are reachable via Tailscale.

    These tests make live HTTP requests over the Tailscale overlay network.
    They verify end-to-end connectivity from the test-runner's network
    position to the DGX Spark node via the supported operator-facing URLs.
    """

    def test_ui_via_tailscale_serve_url(self) -> None:
        """The NemoClaw UI is reachable via the documented Tailscale Serve URL.

        Issues a GET request to the operator-facing Serve URL and asserts that
        the server responds with an HTTP 200 status. This confirms that:
        1. The published remote UI endpoint is live.
        2. Tailscale Serve is routing traffic to the UI backend.
        3. The UI returns real content, not just a synthetic empty success.

        This is the primary mobile-access contract, so the test intentionally
        targets the Serve URL rather than the raw overlay IP.
        """
        url = _tailscale_serve_url()
        response = assert_http_healthy(
            url,
            timeout=_TAILSCALE_HTTP_TIMEOUT,
            expected_status={200},
        )
        # Sanity-check: a non-empty body indicates the UI is actually serving
        # content, not just returning an empty 200 from a health-check stub.
        assert len(response.content) > 0, (
            f"GET {url} returned HTTP 200 but the response body is empty. "
            "The NemoClaw UI may be returning an empty page, which indicates "
            "a misconfigured static file server or a missing build artifact."
        )

    def test_ollama_via_tailscale(self, spark_tailscale_ip: str | None) -> None:
        """The Ollama /api/tags endpoint is reachable via the Spark Tailscale IP.

        Issues a GET request to ``http://<tailscale_ip>:11434/api/tags`` and
        asserts that the server responds with HTTP 200 and a JSON body
        containing a ``models`` key.  This confirms that:
        1. Ollama is running and bound to an address accessible via Tailscale
           (i.e. bound to ``0.0.0.0`` or the Tailscale interface, not just
           ``127.0.0.1``).
        2. The Tailscale ACL policy permits TCP port 11434 between the test
           runner and the Spark node.
        3. The Ollama API is functional and can enumerate available models.

        Remote agents and mobile clients that perform inference via Ollama use
        this exact URL, so a failure here means remote inference is unavailable.
        """
        _require_tailscale_ip(spark_tailscale_ip)
        url = f"http://{spark_tailscale_ip}:{_OLLAMA_PORT}/api/tags"
        response = assert_http_healthy(
            url,
            timeout=_TAILSCALE_HTTP_TIMEOUT,
            expected_status={200},
        )
        try:
            body = response.json()
        except Exception as exc:
            raise AssertionError(
                f"GET {url} returned HTTP 200 but the body is not valid JSON. "
                f"Body (first 300 chars): {response.text[:300]!r}"
            ) from exc

        assert "models" in body, (
            f"GET {url} returned HTTP 200 with JSON, but the top-level key "
            "'models' is absent. "
            "Expected an Ollama /api/tags response with a 'models' list. "
            f"Actual keys: {list(body.keys())}"
        )
        # The models list can be empty (no models pulled yet) — that is a
        # separate concern.  Here we only verify the schema is correct.
        assert isinstance(body["models"], list), (
            f"The 'models' key in the Ollama /api/tags response is not a list. "
            f"Got type {type(body['models']).__name__!r}: {body['models']!r}"
        )
