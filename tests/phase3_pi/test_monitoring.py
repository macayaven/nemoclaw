"""
Phase 3 — Raspberry Pi Infrastructure: monitoring stack tests.

Validates that Uptime Kuma is running on the Raspberry Pi, its dashboard is
accessible over HTTP, and at least one monitor has been configured (i.e. the
instance is not a fresh empty installation).

Port conventions
----------------
  Uptime Kuma: 3001  (default; exposed directly on the Pi's LAN IP)

The ``PiSettings`` model does not yet define an ``uptime_kuma_port`` field,
so the port is read from the ``PI_UPTIME_KUMA_PORT`` environment variable
(defaulting to 3001) to keep the tests configurable without requiring a
settings model change.

Markers
-------
phase3     : All tests belong to Phase 3.
behavioral : Hit real network sockets.
"""

from __future__ import annotations

import os

import httpx
import pytest

from ..settings import TestSettings

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_UPTIME_KUMA_PORT = 3001

# Uptime Kuma's main page always contains its application name in the HTML.
_KUMA_IDENTITY_STRINGS = ["uptime kuma", "uptimekuma"]

# Minimum number of monitors we expect to see when the instance is configured.
_MIN_EXPECTED_MONITORS = 1


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def uptime_kuma_url(test_settings: TestSettings) -> str:
    """Return the Uptime Kuma dashboard URL on the Raspberry Pi.

    Port is read from the ``PI_UPTIME_KUMA_PORT`` environment variable,
    falling back to 3001 when not set.

    Example: ``"http://192.168.1.30:3001"``
    """
    port = int(os.environ.get("PI_UPTIME_KUMA_PORT", _DEFAULT_UPTIME_KUMA_PORT))
    pi_ip = str(test_settings.pi.ip)
    return f"http://{pi_ip}:{port}"


# ---------------------------------------------------------------------------
# Behavioral tests — dashboard availability
# ---------------------------------------------------------------------------


@pytest.mark.phase3
@pytest.mark.behavioral
class TestUptimeKuma:
    """Verify the Uptime Kuma dashboard is reachable and returns valid HTML."""

    def test_dashboard_accessible(self, uptime_kuma_url: str) -> None:
        """GET the Uptime Kuma root URL and expect a 200 OK response.

        A connection error indicates Uptime Kuma is not running or the port is
        not open.  A non-200 status may indicate a reverse proxy
        misconfiguration or the application crashing on startup.
        """
        try:
            response = httpx.get(
                uptime_kuma_url,
                timeout=15,
                follow_redirects=True,
            )
        except httpx.ConnectError as exc:
            pytest.fail(
                f"Cannot reach Uptime Kuma at {uptime_kuma_url}.\n"
                f"Error: {exc}\n"
                "Ensure Uptime Kuma is running on the Pi (default port 3001).\n"
                "Check: sudo systemctl status uptime-kuma  (or the equivalent "
                "Docker / PM2 service)."
            )
        except httpx.TimeoutException:
            pytest.fail(
                f"Uptime Kuma at {uptime_kuma_url} did not respond within 15 s.\n"
                "The process may be starting up or overloaded."
            )

        assert response.status_code == 200, (
            f"Uptime Kuma returned HTTP {response.status_code}, expected 200.\n"
            f"URL: {uptime_kuma_url}\n"
            f"Response body (first 300 chars): {response.text[:300]!r}"
        )

    def test_dashboard_returns_html(self, uptime_kuma_url: str) -> None:
        """The dashboard response must be HTML containing the Uptime Kuma identity.

        Confirms we are talking to Uptime Kuma and not to another service
        accidentally running on port 3001.  The check is case-insensitive so
        it handles both ``Uptime Kuma`` (title) and ``uptimekuma`` (JS bundle
        identifier) variants.
        """
        try:
            response = httpx.get(
                uptime_kuma_url,
                timeout=15,
                follow_redirects=True,
            )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            pytest.fail(
                f"Could not connect to Uptime Kuma at {uptime_kuma_url} "
                f"for HTML content check: {exc}"
            )

        assert response.status_code == 200, (
            f"Uptime Kuma at {uptime_kuma_url} returned {response.status_code}."
        )

        content_type = response.headers.get("content-type", "").lower()
        assert "html" in content_type, (
            f"Expected Content-Type: text/html from Uptime Kuma dashboard.\n"
            f"Got: {content_type!r}\n"
            f"URL: {uptime_kuma_url}\n"
            "Ensure the root path '/' serves the Vue SPA index.html."
        )

        body_lower = response.text.lower()
        is_kuma = any(identity in body_lower for identity in _KUMA_IDENTITY_STRINGS)
        assert is_kuma, (
            f"Uptime Kuma identity string not found in the dashboard HTML.\n"
            f"Looked for: {_KUMA_IDENTITY_STRINGS}\n"
            f"URL: {uptime_kuma_url}\n"
            f"Body snippet (first 500 chars): {response.text[:500]!r}\n"
            "Another service may be running on this port, or the Uptime Kuma "
            "SPA failed to build."
        )


# ---------------------------------------------------------------------------
# Negative / configuration tests — monitors must be configured
# ---------------------------------------------------------------------------


@pytest.mark.phase3
class TestMonitoringNegative:
    """Verify the Uptime Kuma instance is not running empty (has monitors set up)."""

    def test_monitors_configured(self, uptime_kuma_url: str) -> None:
        """Uptime Kuma must have at least one monitor configured.

        An empty Uptime Kuma installation is operationally useless — it will
        not alert on any failures.  This test attempts to query the Uptime Kuma
        status-page API (``/api/status-page/heartbeat/default``) or the metrics
        endpoint to confirm at least one monitor exists.

        Uptime Kuma exposes a public status-page API at:
          GET /api/status-page/heartbeat/<slug>

        The default slug is ``"default"``.  If the status page is not
        configured, we fall back to checking the ``/metrics`` endpoint (if
        Prometheus metrics are enabled) for a positive monitor count.

        The test is marked ``xfail`` rather than ``skip`` when neither endpoint
        is available, so the CI pipeline records the gap without blocking a
        deploy.
        """
        monitors_found = False
        diagnostics: list[str] = []

        # ---- Strategy 1: public status-page heartbeat API ------------------
        status_page_url = f"{uptime_kuma_url}/api/status-page/heartbeat/default"
        try:
            sp_response = httpx.get(status_page_url, timeout=10, follow_redirects=True)
            if sp_response.status_code == 200:
                data = sp_response.json()
                # The heartbeat endpoint returns {"heartbeatList": {...}, "uptimeList": {...}}
                # A non-empty heartbeatList means monitors exist.
                heartbeat_list = data.get("heartbeatList", {})
                if heartbeat_list:
                    monitors_found = True
                    diagnostics.append(
                        f"Found {len(heartbeat_list)} monitor(s) via status-page API."
                    )
                else:
                    diagnostics.append("Status-page heartbeat API returned an empty heartbeatList.")
            else:
                diagnostics.append(f"Status-page heartbeat API returned {sp_response.status_code}.")
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            diagnostics.append(f"Status-page heartbeat API unreachable: {exc}")
        except Exception as exc:
            diagnostics.append(f"Status-page API parse error: {exc}")

        # ---- Strategy 2: Prometheus /metrics endpoint -----------------------
        if not monitors_found:
            metrics_url = f"{uptime_kuma_url}/metrics"
            try:
                m_response = httpx.get(metrics_url, timeout=10, follow_redirects=True)
                if m_response.status_code == 200:
                    # Look for any monitor_status metric which only appears when
                    # monitors exist.
                    if "monitor_status" in m_response.text or "monitor_" in m_response.text:
                        monitors_found = True
                        diagnostics.append("Found monitor metrics in /metrics endpoint.")
                    else:
                        diagnostics.append(
                            "/metrics endpoint returned 200 but contained no "
                            "monitor_status metrics."
                        )
                else:
                    diagnostics.append(
                        f"/metrics endpoint returned {m_response.status_code} "
                        f"(Prometheus metrics may not be enabled in Uptime Kuma settings)."
                    )
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                diagnostics.append(f"/metrics endpoint unreachable: {exc}")

        diagnostic_summary = "\n".join(f"  - {d}" for d in diagnostics)

        assert monitors_found, (
            f"Uptime Kuma appears to have no monitors configured "
            f"(tried status-page API and /metrics).\n"
            f"Diagnostic results:\n{diagnostic_summary}\n\n"
            "Add monitors via the Uptime Kuma web UI (http://<pi-ip>:3001) or "
            "by importing a monitor configuration JSON.\n"
            f"Expected at least {_MIN_EXPECTED_MONITORS} monitor(s) to be present."
        )
