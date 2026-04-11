"""
Phase 1 — Core NemoClaw on Spark: OpenClaw sandbox tests.

Validates that:
- The ``nemoclaw-main`` sandbox exists in the OpenShell sandbox list.
- It is reported as ready by the current CLI and survives the restart path
  covered in ``test_idempotency.py``.
- Port 18789 is forwarded from the sandbox to the host.
- The OpenClaw web UI returns HTTP 200 on port 18789.
- An end-to-end chat request goes through the ``inference.local`` route inside
  the sandbox SSH bridge (NOT directly to Ollama) and returns a non-empty
  completion.

Markers
-------
phase1     : All tests in this module belong to Phase 1.
contract   : Layer-A sandbox state checks.
behavioral : Layer-B UI and end-to-end tests.
slow       : Tests involving cold model loading or full chat round-trips.
"""

from __future__ import annotations

import json
import re
import shlex

import httpx
import pytest
from fabric import Connection

from ..helpers import parse_json_output, run_remote
from ..models import CommandResult, InferenceResponse

# ---------------------------------------------------------------------------
# Contract tests — sandbox existence and configuration
# ---------------------------------------------------------------------------


@pytest.mark.phase1
@pytest.mark.contract
class TestSandboxExists:
    """Verify that the nemoclaw-main sandbox is present and correctly configured."""

    def test_sandbox_in_list(self, spark_ssh: Connection) -> None:
        """``openshell sandbox list --json`` must include 'nemoclaw-main'.

        Prefers the ``--json`` flag for structured parsing; falls back to plain
        text output if the flag is not supported by the installed version.
        """
        # Try JSON output first
        json_result: CommandResult = run_remote(
            spark_ssh,
            "openshell sandbox list --json 2>/dev/null || openshell sandbox list",
        )

        raw = json_result.stdout.strip()
        assert raw, (
            "openshell sandbox list produced no output.\n"
            f"Return code: {json_result.return_code}\n"
            f"Stderr: {json_result.stderr}"
        )

        # JSON path: try to parse and look for the sandbox by name
        found_in_json = False
        if raw.startswith("[") or raw.startswith("{"):
            try:
                sandboxes = json.loads(raw)
                if isinstance(sandboxes, list):
                    names = [
                        s.get("name", "") if isinstance(s, dict) else str(s) for s in sandboxes
                    ]
                    found_in_json = "nemoclaw-main" in names
                elif isinstance(sandboxes, dict):
                    found_in_json = "nemoclaw-main" in str(sandboxes)
            except json.JSONDecodeError:
                pass  # Fall through to plain-text check

        if not found_in_json:
            # Plain-text fallback
            assert "nemoclaw-main" in raw, (
                "Sandbox 'nemoclaw-main' not found in openshell sandbox list.\n"
                f"Full output:\n{raw}\n"
                "Run: openshell sandbox create "
                "--from openclaw --name nemoclaw-main --keep --forward 18789"
            )

    def test_sandbox_is_ready(self, spark_ssh: Connection) -> None:
        """``openshell sandbox get nemoclaw-main`` must report a ready sandbox.

        The persistence semantics of ``--keep`` are validated in the dedicated
        idempotency test that restarts the gateway.  Here we only assert the
        stable, supported readiness signal exposed by the current CLI.
        """
        result: CommandResult = run_remote(
            spark_ssh,
            "openshell sandbox get nemoclaw-main",
        )

        assert result.stdout.strip(), (
            "openshell sandbox get nemoclaw-main produced no output — "
            "does the sandbox exist?\n"
            f"Stderr: {result.stderr}"
        )

        output = _strip_ansi(result.stdout)
        assert "Name: nemoclaw-main" in output, (
            f"The sandbox descriptor does not identify nemoclaw-main.\nFull output:\n{output}"
        )
        assert "Phase: Ready" in output, (
            "The nemoclaw-main sandbox is not in Ready phase.\n"
            f"Full output:\n{output}\n"
            "Check the sandbox creation command and gateway health."
        )

    def test_port_18789_forwarded(self, spark_ssh: Connection) -> None:
        """Port 18789 must be forwarded in the nemoclaw-main sandbox configuration.

        Checks the active port-forward list rather than relying on sandbox
        descriptor formatting, which has changed in the current CLI.
        """
        result: CommandResult = run_remote(
            spark_ssh,
            "openshell forward list",
        )

        output = _strip_ansi(result.stdout)

        assert "nemoclaw-main" in output and "18789" in output, (
            "Port 18789 is not listed in the active OpenShell forward table.\n"
            f"Full output:\n{output}\n"
            "Recreate the sandbox with: --forward 18789"
        )


# ---------------------------------------------------------------------------
# Behavioral tests — OpenClaw UI reachability
# ---------------------------------------------------------------------------


@pytest.mark.phase1
@pytest.mark.behavioral
class TestSandboxUI:
    """Verify that the OpenClaw web UI is reachable on the forwarded port."""

    def test_ui_returns_200(self, spark_ip: str) -> None:
        """HTTP GET to the Spark host on port 18789 must return HTTP 200.

        The OpenClaw browser UI serves a React SPA at the root path.  A 200
        response confirms that:
        1. The sandbox is running.
        2. Port 18789 is correctly forwarded from the sandbox to the host.
        3. The OpenClaw web server inside the sandbox is healthy.

        Uses httpx directly (not assert_http_healthy) so we can provide a
        more informative failure message with the actual status code.
        """
        url = f"http://{spark_ip}:18789"
        try:
            response = httpx.get(url, timeout=20.0, follow_redirects=True)
        except httpx.ConnectError as exc:
            pytest.fail(
                f"Could not connect to OpenClaw UI at {url}: {exc}\n"
                "Check that the nemoclaw-main sandbox is running and port 18789 is "
                "forwarded with --forward 18789."
            )

        assert response.status_code == 200, (
            f"OpenClaw UI at {url} returned HTTP {response.status_code}, expected 200.\n"
            f"Response body (first 500 chars):\n{response.text[:500]}"
        )


# ---------------------------------------------------------------------------
# Behavioral end-to-end test — full chat through inference.local
# ---------------------------------------------------------------------------


@pytest.mark.phase1
@pytest.mark.behavioral
class TestEndToEnd:
    """End-to-end: a chat request inside the sandbox goes through inference.local."""

    @pytest.mark.slow
    @pytest.mark.timeout(180)
    def test_chat_through_inference_local(self, spark_ssh: Connection) -> None:
        """Send a chat prompt inside nemoclaw-main via inference.local and verify the reply.

        This test deliberately goes through the OpenShell inference route
        (``https://inference.local/v1/chat/completions``) rather than
        calling Ollama directly.  It validates the full production path:

            nemoclaw-main sandbox
                → inference.local intercept (OpenShell gateway)
                    → local-ollama provider
                        → Ollama 11434
                            → Nemotron 120B model

        If this test passes, the complete Phase 1 inference stack is working
        end-to-end as intended.

        The 180-second timeout accommodates:
        - Nemotron 120B cold GPU load time (~60 s on first call)
        - Network round-trip inside the sandbox
        - Token generation for a short reply
        """
        curl_payload = json.dumps(
            {
                "model": "nemotron-3-super:120b",
                "messages": [
                    {
                        "role": "user",
                        "content": "Reply with exactly one word: hello",
                    }
                ],
                "max_tokens": 120,
                "stream": False,
            }
        )

        exec_cmd = (
            "cfg=$(mktemp); "
            'openshell sandbox ssh-config nemoclaw-main > "$cfg"; '
            + "printf '%s\\n' "
            + shlex.quote(
                "curl -s "
                "-k "
                "https://inference.local/v1/chat/completions "
                "-H 'Content-Type: application/json' "
                f"-d {shlex.quote(curl_payload)}"
            )
            + ' | ssh -F "$cfg" openshell-nemoclaw-main sh; '
            'rc=$?; rm -f "$cfg"; exit $rc'
        )

        result: CommandResult = run_remote(spark_ssh, exec_cmd)

        assert result.stdout.strip(), (
            "Sandbox inference call produced no curl output.\n"
            f"Return code: {result.return_code}\n"
            f"Stderr:\n{result.stderr}\n"
            "Ensure the nemoclaw-main sandbox is running and inference.local is reachable."
        )

        response_data = parse_json_output(result.stdout)
        inference = InferenceResponse.model_validate(response_data)

        assert inference.choices, (
            "End-to-end inference returned an empty choices list.\n"
            f"Full response:\n{result.stdout[:2000]}"
        )

        content: str = inference.choices[0].message.content
        assert content and content.strip(), (
            "End-to-end inference returned an empty message content.\n"
            f"Full response:\n{result.stdout[:2000]}"
        )


_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)
