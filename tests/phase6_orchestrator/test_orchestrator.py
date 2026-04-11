"""Phase 6 tests for :mod:`orchestrator.orchestrator`."""

from __future__ import annotations

import pytest

from orchestrator.config import OrchestratorSettings
from orchestrator.models import SandboxResult
from orchestrator.orchestrator import Orchestrator, PipelineResult, PipelineStep

pytestmark = pytest.mark.phase6


def _make_orchestrator(
    tmp_path,
    monkeypatch,
    responder,
    *,
    max_delegation_depth: int = 5,
):
    """Create an orchestrator whose bridge is replaced with a deterministic stub."""
    settings = OrchestratorSettings(
        shared_workspace=tmp_path / "shared",
        max_delegation_depth=max_delegation_depth,
    )
    orchestrator = Orchestrator(settings=settings)
    calls: list[tuple[str, str, str]] = []

    def fake_send_prompt(
        sandbox_name: str, prompt: str, agent_type: str = "openclaw"
    ) -> SandboxResult:
        calls.append((sandbox_name, prompt, agent_type))
        return SandboxResult(
            sandbox_name=sandbox_name,
            command="fake",
            stdout=f"{responder(sandbox_name, prompt, agent_type)}\n",
            stderr="",
            return_code=0,
            duration_ms=12.0,
        )

    monkeypatch.setattr(orchestrator.bridge, "send_prompt", fake_send_prompt)
    return orchestrator, calls


class TestDelegation:
    """Single-agent delegation tests."""

    def test_delegate_returns_response_and_completes_task(self, tmp_path, monkeypatch) -> None:
        orchestrator, calls = _make_orchestrator(
            tmp_path,
            monkeypatch,
            responder=lambda sandbox_name, prompt, agent_type: "ok",
        )

        response = orchestrator.delegate(
            prompt="Reply with a single word: ok",
            agent="openclaw",
        )

        tasks = orchestrator.task_manager.list_tasks()

        assert response.output_text == "ok"
        assert calls == [("nemoclaw-main", "Reply with a single word: ok", "openclaw")]
        assert len(tasks) == 1
        assert tasks[0].status == "completed"
        assert tasks[0].assigned_to == "openclaw"

    def test_delegate_marks_task_failed_on_bridge_error(self, tmp_path, monkeypatch) -> None:
        def fail(_sandbox_name: str, _prompt: str, _agent_type: str) -> str:
            raise RuntimeError("sandbox unreachable")

        orchestrator, _ = _make_orchestrator(tmp_path, monkeypatch, responder=fail)

        with pytest.raises(RuntimeError, match="sandbox unreachable"):
            orchestrator.delegate(prompt="hello", agent="openclaw")

        failed_tasks = orchestrator.task_manager.list_tasks(status="failed")

        assert len(failed_tasks) == 1
        assert failed_tasks[0].result is not None
        assert failed_tasks[0].result.error == "sandbox unreachable"

    def test_delegate_enforces_max_delegation_depth(self, tmp_path, monkeypatch) -> None:
        orchestrator, _ = _make_orchestrator(
            tmp_path,
            monkeypatch,
            responder=lambda sandbox_name, prompt, agent_type: "ok",
            max_delegation_depth=1,
        )

        with pytest.raises(RecursionError, match="Maximum delegation depth exceeded"):
            orchestrator.delegate(
                prompt="Nested delegation",
                agent="openclaw",
                _current_depth=2,
            )


class TestPipeline:
    """Pipeline execution tests."""

    def test_pipeline_executes_steps_in_order(self, tmp_path, monkeypatch) -> None:
        orchestrator, calls = _make_orchestrator(
            tmp_path,
            monkeypatch,
            responder=lambda sandbox_name, prompt, agent_type: f"handled::{prompt}",
        )
        steps = [
            PipelineStep(
                agent="openclaw",
                task_type="analysis",
                prompt_template="alpha {prev_result}",
            ),
            PipelineStep(
                agent="codex",
                task_type="implementation",
                prompt_template="beta {step_1_result}",
            ),
        ]

        result = orchestrator.pipeline(prompt="seed", steps=steps)

        assert isinstance(result, PipelineResult)
        assert len(result.steps) == 2
        assert calls[0] == ("nemoclaw-main", "alpha seed", "openclaw")
        assert calls[1] == ("codex-dev", "beta handled::alpha seed", "codex")
        assert result.final_output == "handled::beta handled::alpha seed"
        assert all(step.task_id for step in result.steps)
        assert result.steps[0].output_text == "handled::alpha seed"

    def test_pipeline_preserves_literal_template_when_substitution_is_missing(
        self, tmp_path, monkeypatch
    ) -> None:
        orchestrator, calls = _make_orchestrator(
            tmp_path,
            monkeypatch,
            responder=lambda sandbox_name, prompt, agent_type: prompt,
        )
        steps = [
            PipelineStep(
                agent="openclaw",
                task_type="analysis",
                prompt_template="literal {missing_token}",
            )
        ]

        result = orchestrator.pipeline(prompt="seed", steps=steps)

        assert calls[0][1] == "literal {missing_token}"
        assert result.final_output == "literal {missing_token}"

    def test_pipeline_with_unknown_agent_raises(self, tmp_path) -> None:
        orchestrator = Orchestrator(
            settings=OrchestratorSettings(shared_workspace=tmp_path / "shared")
        )
        steps = [
            PipelineStep(
                agent="does-not-exist",
                task_type="analysis",
                prompt_template="hello",
            )
        ]

        with pytest.raises(KeyError):
            orchestrator.pipeline(prompt="seed", steps=steps)


class TestParallelSpecialists:
    """Parallel fan-out tests."""

    def test_parallel_specialists_returns_results_by_agent(self, tmp_path, monkeypatch) -> None:
        orchestrator, _ = _make_orchestrator(
            tmp_path,
            monkeypatch,
            responder=lambda sandbox_name, prompt, agent_type: f"{agent_type}:{prompt}",
        )

        results = orchestrator.parallel_specialists(
            prompt="ping",
            agents=["openclaw", "gemini"],
        )

        assert results["openclaw"].success is True
        assert results["openclaw"].delegation is not None
        assert results["openclaw"].delegation.output_text == "openclaw:ping"
        assert results["gemini"].success is True
        assert results["gemini"].delegation is not None
        assert results["gemini"].delegation.output_text == "gemini:ping"

    def test_parallel_specialists_returns_empty_dict_for_empty_input(
        self, tmp_path, monkeypatch
    ) -> None:
        orchestrator, _ = _make_orchestrator(
            tmp_path,
            monkeypatch,
            responder=lambda sandbox_name, prompt, agent_type: "unused",
        )

        assert orchestrator.parallel_specialists(prompt="ping", agents=[]) == {}
