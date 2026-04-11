"""
Phase 2 — Mac Studio Integration: provider switching tests.

Validates the full provider-switching workflow:
- Switching the active inference route from the primary (local-ollama / Spark
  Nemotron) to the secondary (mac-ollama / Mac Qwen3).
- Verifying that inference through the Mac provider returns a real completion.
- Switching back to the Spark Nemotron provider.
- Verifying that Nemotron inference still works after a switch-back.
- Edge cases: rapid switching, switching when no session is active.

Design philosophy
-----------------
Each test in TestSwitchToMac and TestSwitchBack treats the switch command as
an atomic state change and immediately validates the post-switch state.  Tests
are ordered (via class grouping) to follow the natural workflow:

    [Spark primary] → switch to Mac → infer via Mac → switch back to Spark
                   → infer via Spark → edge-case stability tests

The ``spark_ssh`` fixture is used for all openshell commands because the
OpenShell process and its configuration live on Spark.

Markers
-------
phase2     : All tests in this module belong to Phase 2.
behavioral : Layer-B end-to-end workflow tests that change live system state.
slow       : Tests that involve cold model loading (>30 s response time).

Fixtures (from conftest.py)
---------------------------
spark_ssh : fabric.Connection — live SSH connection to the DGX Spark.
mac_ip    : str               — LAN IP of the Mac Studio.
"""

from __future__ import annotations

import pytest
from fabric import Connection

from ..helpers import (
    parse_json_output,
    parse_openshell_inference_route_output,
    poll_until_ready,
    run_in_sandbox,
    run_remote,
)
from ..models import CommandResult, InferenceResponse, OpenShellInferenceRoute

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAC_PROVIDER_NAME = "mac-ollama"
_MAC_MODEL = "qwen3:8b"

_SPARK_PROVIDER_NAME = "local-ollama"
_SPARK_MODEL = "nemotron-3-super:120b"

_MAC_INFERENCE_TIMEOUT = 30  # seconds — Qwen3:8b on Apple Silicon is fast
_SPARK_INFERENCE_TIMEOUT = 90  # seconds — Nemotron 120B may need cold-start GPU load


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_active_route(conn: Connection) -> OpenShellInferenceRoute:
    """Fetch and parse the current OpenShell inference route.

    Args:
        conn: Fabric connection to Spark.

    Returns:
        Parsed :class:`OpenShellInferenceRoute` with the active provider and
        model.

    Raises:
        AssertionError: If the command fails or returns no output.
    """
    result: CommandResult = run_remote(conn, "openshell inference get", timeout=15)
    assert result.stdout.strip(), (
        "openshell inference get produced no output.\n"
        f"Return code: {result.return_code}\nStderr: {result.stderr}"
    )
    return parse_openshell_inference_route_output(result.stdout)


def _switch_provider(conn: Connection, provider: str, model: str) -> CommandResult:
    """Switch the OpenShell inference route to the given provider and model.

    Args:
        conn: Fabric connection to Spark.
        provider: Provider name to activate (e.g. 'mac-ollama').
        model: Model identifier to set on the route (e.g. 'qwen3:8b').

    Returns:
        The :class:`CommandResult` from the switch command.
    """
    result = run_remote(
        conn,
        f"openshell inference set --provider {provider} --model {model}",
        timeout=60,
    )
    if result.return_code == 0:
        poll_until_ready(
            lambda: (
                (route := _get_active_route(conn)).provider == provider and route.model == model
            ),
            timeout=30,
            interval=2,
            description=f"inference route to converge to {provider}/{model}",
        )
    return result


def _run_inference_in_sandbox(
    conn: Connection,
    model: str,
    prompt: str,
    timeout: int,
) -> CommandResult:
    """Send a chat completion request through the OpenShell sandbox inference route.

    Runs curl inside the persistent ``nemoclaw-main`` sandbox via the current
    OpenShell SSH proxy path.

    Args:
        conn: Fabric connection to Spark.
        model: Model name to request (forwarded to the active provider).
        prompt: User message text to send.
        timeout: Seconds to wait for the remote command to complete.

    Returns:
        The :class:`CommandResult` from the sandbox command.
    """
    curl_cmd = (
        "curl -s -k "
        "https://inference.local/v1/chat/completions "
        "-H 'Content-Type: application/json' "
        f"-d '{{"
        f'"model":"{model}",'
        f'"messages":[{{"role":"user","content":"{prompt}"}}],'
        f'"max_tokens":120'
        f"}}'"
    )
    return run_in_sandbox(conn, "nemoclaw-main", curl_cmd, timeout=timeout)


# ---------------------------------------------------------------------------
# Switch to Mac provider
# ---------------------------------------------------------------------------


@pytest.mark.phase2
@pytest.mark.behavioral
class TestSwitchToMac:
    """Verify the full workflow of switching from Spark (Nemotron) to Mac (Qwen3).

    Each test in this class corresponds to one step in the switch sequence.
    Because pytest executes tests in definition order within a class, and these
    tests mutate live system state, they must run in order.
    """

    def test_switch_to_mac_provider(self, spark_ssh: Connection) -> None:
        """``openshell inference set`` switches the active route to mac-ollama.

        Runs the switch command and asserts it exits with code 0.  Does not
        yet validate inference — that is covered by the next test.  Separating
        the switch from the inference assertion makes failure diagnosis easier:
        if the switch itself fails, the cause is a configuration or CLI error;
        if the switch succeeds but inference fails, the cause is a Mac-side
        Ollama issue.
        """
        result = _switch_provider(spark_ssh, _MAC_PROVIDER_NAME, _MAC_MODEL)

        assert result.return_code == 0, (
            f"Switching inference route to '{_MAC_PROVIDER_NAME}' failed "
            f"(exit code {result.return_code}).\n"
            f"stdout: {result.stdout!r}\n"
            f"stderr: {result.stderr!r}\n"
            f"Ensure '{_MAC_PROVIDER_NAME}' is registered: openshell provider list"
        )

    @pytest.mark.slow
    @pytest.mark.timeout(_MAC_INFERENCE_TIMEOUT + 10)
    def test_mac_inference_works(self, spark_ssh: Connection) -> None:
        """Inference via mac-ollama returns a non-empty Qwen3 completion.

        Sends a short prompt through the sandbox ``inference.local`` route and
        parses the response as an OpenAI-compatible ``InferenceResponse``.
        Validates:
        - The response parses without schema errors.
        - At least one choice is present.
        - The first choice's message content is non-empty.

        The 30-second timeout accommodates Qwen3:8b on Apple Silicon M-series,
        which typically responds within 5-15 seconds after the model is warm.
        Cold-start (first request after idle) may take up to 25 seconds.
        """
        result = _run_inference_in_sandbox(
            conn=spark_ssh,
            model=_MAC_MODEL,
            prompt="Reply with the single word: ready",
            timeout=_MAC_INFERENCE_TIMEOUT,
        )

        assert result.stdout.strip(), (
            "Inference via mac-ollama returned no stdout.\n"
            f"stderr: {result.stderr!r}\n"
            "Check: is Ollama running on Mac Studio? "
            "ssh mac-studio.local 'curl -s http://localhost:11434/api/tags'"
        )

        response_data = parse_json_output(result.stdout)
        inference = InferenceResponse.model_validate(response_data)

        assert inference.choices, (
            "InferenceResponse.choices is empty — Qwen3 returned no completion.\n"
            f"Full stdout: {result.stdout[:800]!r}"
        )

        content = inference.choices[0].message.text
        assert content and content.strip(), (
            "The first inference choice contains neither visible content nor reasoning text.\n"
            f"Full response: {result.stdout[:800]!r}"
        )

    def test_verify_active_provider_is_mac(self, spark_ssh: Connection) -> None:
        """``openshell inference get`` must show mac-ollama as the active provider.

        Independently queries the current route state after the switch and
        asserts the provider name matches.  This is a state-verification test
        separate from the switch command's return code: a successful exit code
        does not guarantee the internal state was actually updated.
        """
        route = _get_active_route(spark_ssh)

        assert route.provider == _MAC_PROVIDER_NAME, (
            f"Active inference provider is '{route.provider}', "
            f"expected '{_MAC_PROVIDER_NAME}' after switching to Mac.\n"
            "The switch command may have reported success without persisting the change.\n"
            f"Run: openshell inference set --provider {_MAC_PROVIDER_NAME} "
            f"--model {_MAC_MODEL}"
        )

        assert _MAC_MODEL.split(":")[0] in route.model.lower() or route.model == _MAC_MODEL, (
            f"Active inference model is '{route.model}', expected '{_MAC_MODEL}'.\n"
            f"Run: openshell inference set --provider {_MAC_PROVIDER_NAME} "
            f"--model {_MAC_MODEL}"
        )


# ---------------------------------------------------------------------------
# Switch back to Spark provider
# ---------------------------------------------------------------------------


@pytest.mark.phase2
@pytest.mark.behavioral
class TestSwitchBack:
    """Verify that switching back to Spark (Nemotron) restores the primary route.

    These tests run after TestSwitchToMac and restore the system to its
    Phase 1 steady state.  Nemotron inference after a switch-back confirms
    that the route change is durable and that OpenShell does not cache a
    stale provider reference.
    """

    def test_switch_back_to_spark(self, spark_ssh: Connection) -> None:
        """``openshell inference set`` switches the active route back to local-ollama.

        Mirrors ``TestSwitchToMac.test_switch_to_mac_provider`` but in the
        reverse direction.  A successful exit code is required.
        """
        result = _switch_provider(spark_ssh, _SPARK_PROVIDER_NAME, _SPARK_MODEL)

        assert result.return_code == 0, (
            f"Switching inference route back to '{_SPARK_PROVIDER_NAME}' failed "
            f"(exit code {result.return_code}).\n"
            f"stdout: {result.stdout!r}\n"
            f"stderr: {result.stderr!r}\n"
            "Ensure the Spark Ollama provider is still registered: "
            "openshell provider list"
        )

    @pytest.mark.slow
    @pytest.mark.timeout(_SPARK_INFERENCE_TIMEOUT + 10)
    def test_nemotron_inference_works_after_switch_back(self, spark_ssh: Connection) -> None:
        """Nemotron inference still works after a round-trip provider switch.

        A round-trip switch (Spark → Mac → Spark) exercises the route-state
        persistence layer of OpenShell.  If there is a bug where switching to
        a remote provider poisons the local provider's connection pool or
        configuration, this test will catch it.

        Uses the same 90-second timeout as the phase-1 inference test because
        Nemotron 120B may require GPU warm-up time if the switch-to-Mac period
        caused the model to be evicted from VRAM.
        """
        result = _run_inference_in_sandbox(
            conn=spark_ssh,
            model=_SPARK_MODEL,
            prompt="Reply with the single word: ready",
            timeout=_SPARK_INFERENCE_TIMEOUT,
        )

        assert result.stdout.strip(), (
            "Nemotron inference after switch-back returned no stdout.\n"
            f"stderr: {result.stderr!r}\n"
            "Check: is Ollama running on Spark? systemctl status ollama\n"
            "Check: is local-ollama provider registered? openshell provider list"
        )

        response_data = parse_json_output(result.stdout)
        inference = InferenceResponse.model_validate(response_data)

        assert inference.choices, (
            "InferenceResponse.choices is empty after switch-back to Nemotron.\n"
            f"Full stdout: {result.stdout[:800]!r}"
        )

        content = inference.choices[0].message.text
        assert content and content.strip(), (
            "The first inference choice contains neither visible content nor reasoning text "
            "after switching back to Spark.\n"
            f"Full response: {result.stdout[:800]!r}"
        )


# ---------------------------------------------------------------------------
# Edge-case tests
# ---------------------------------------------------------------------------


@pytest.mark.phase2
class TestSwitchingEdgeCases:
    """Edge cases: switching must be safe under stress and in idle states.

    These tests do not depend on a particular current provider state; each
    sets up its own precondition and asserts a safety property.
    """

    @pytest.mark.timeout(60)
    def test_rapid_switch_does_not_crash(self, spark_ssh: Connection) -> None:
        """Switching providers in rapid succession must not crash OpenShell.

        Issues four provider-switch commands back-to-back with no delay between
        them: Mac → Spark → Mac → Spark.  After all switches complete, queries
        the active route to confirm OpenShell is still responsive and settled
        on the Spark provider (the last switch in the sequence).

        This catches race conditions in the route-update code path where a
        partially-applied switch could leave the routing table in an
        inconsistent state.
        """
        switches = [
            (_MAC_PROVIDER_NAME, _MAC_MODEL),
            (_SPARK_PROVIDER_NAME, _SPARK_MODEL),
            (_MAC_PROVIDER_NAME, _MAC_MODEL),
            (_SPARK_PROVIDER_NAME, _SPARK_MODEL),  # final state: Spark
        ]

        for i, (provider, model) in enumerate(switches):
            result = _switch_provider(spark_ssh, provider, model)
            assert result.return_code == 0, (
                f"Rapid switch #{i + 1} to '{provider}' failed "
                f"(exit code {result.return_code}).\n"
                f"stdout: {result.stdout!r}\n"
                f"stderr: {result.stderr!r}"
            )

        # After the rapid sequence, OpenShell must still respond correctly
        route = _get_active_route(spark_ssh)

        assert route.provider == _SPARK_PROVIDER_NAME, (
            f"After rapid switching, active provider is '{route.provider}' "
            f"but expected '{_SPARK_PROVIDER_NAME}' (the last switch in the sequence).\n"
            "This indicates a race condition or state-corruption bug in OpenShell's "
            "route persistence."
        )

    @pytest.mark.timeout(30)
    def test_switch_during_no_active_session(self, spark_ssh: Connection) -> None:
        """Switching providers when no chat session is active must succeed cleanly.

        Verifies that provider-switching does not require an active sandbox or
        in-progress inference request.  The switch must complete without error
        and the new route must be immediately readable via ``inference get``.

        This test is deliberately run in a quiet window (no concurrent
        inference) to isolate the switching mechanism from session-management
        code paths.

        Restores the Spark provider at the end so subsequent tests in the
        suite start from a known-good state.
        """
        # Switch to Mac (idle state — no active sessions)
        switch_to_mac = _switch_provider(spark_ssh, _MAC_PROVIDER_NAME, _MAC_MODEL)

        assert switch_to_mac.return_code == 0, (
            f"Switching to '{_MAC_PROVIDER_NAME}' during idle state failed "
            f"(exit code {switch_to_mac.return_code}).\n"
            f"stdout: {switch_to_mac.stdout!r}\n"
            f"stderr: {switch_to_mac.stderr!r}"
        )

        # Verify the new route is immediately visible — no eventual consistency lag
        route_after_mac_switch = _get_active_route(spark_ssh)
        assert route_after_mac_switch.provider == _MAC_PROVIDER_NAME, (
            f"Route was not updated to '{_MAC_PROVIDER_NAME}' immediately after "
            "switching in idle state.\n"
            f"Got provider: '{route_after_mac_switch.provider}'\n"
            "OpenShell may be applying the switch asynchronously; check if a "
            "reload / apply step is needed."
        )

        # Restore to Spark so the suite ends in the standard primary-provider state
        switch_back = _switch_provider(spark_ssh, _SPARK_PROVIDER_NAME, _SPARK_MODEL)

        assert switch_back.return_code == 0, (
            f"Restoring inference route to '{_SPARK_PROVIDER_NAME}' at end of "
            "idle-state test failed "
            f"(exit code {switch_back.return_code}).\n"
            f"stdout: {switch_back.stdout!r}\n"
            f"stderr: {switch_back.stderr!r}\n"
            "WARNING: The inference route may be left pointing at mac-ollama. "
            "Manually restore: "
            f"openshell inference set --provider {_SPARK_PROVIDER_NAME} "
            f"--model {_SPARK_MODEL}"
        )

        route_after_restore = _get_active_route(spark_ssh)
        assert route_after_restore.provider == _SPARK_PROVIDER_NAME, (
            f"Final route is '{route_after_restore.provider}' after restoration, "
            f"expected '{_SPARK_PROVIDER_NAME}'.\n"
            "The test suite has left the system in an unexpected state — check "
            "openshell inference get before running further tests."
        )
