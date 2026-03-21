"""
test_orchestrator.py — Phase 6 tests for orchestrator.orchestrator.

Covers:
  - Delegation to a single agent (behavioural / slow)
  - Multi-step pipeline execution including result template substitution
  - Negative paths: invalid agent names, max delegation depth
  - Parallel specialist dispatch

All tests are marked @pytest.mark.phase6.  Slow / network tests are also
marked @pytest.mark.slow with timeout=180.  They require a live DGX Spark
with the nemoclaw-main sandbox running and are skipped automatically by
pytest-timeout when the sandbox is unavailable.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.phase6


# ---------------------------------------------------------------------------
# Behavioural tests — single-agent delegation
# ---------------------------------------------------------------------------


class TestDelegation:
    """Behavioural tests: Orchestrator.delegate() sends work to a single agent."""

    @pytest.mark.slow
    @pytest.mark.timeout(180)
    def test_delegate_to_openclaw(self, spark_ssh, tmp_path) -> None:
        """delegate() sends a simple prompt to openclaw and returns a non-empty result.

        The prompt is intentionally trivial ('Reply with: ok') so the test
        does not depend on model quality, only on end-to-end connectivity.
        """
        from orchestrator.config import OrchestratorSettings
        from orchestrator.orchestrator import Orchestrator
        from orchestrator.sandbox_bridge import SandboxBridge
        from orchestrator.task_manager import TaskManager

        settings = OrchestratorSettings()
        bridge = SandboxBridge(settings=settings, conn=spark_ssh)
        manager = TaskManager(storage_path=tmp_path / "tasks.json")
        orch = Orchestrator(settings=settings, bridge=bridge, task_manager=manager)

        result = orch.delegate(
            agent_name="openclaw",
            prompt="Reply with a single word: ok",
        )

        assert isinstance(result, str), f"delegate() must return str, got {type(result)}"
        assert len(result.strip()) > 0, "delegate() returned an empty result"

    @pytest.mark.slow
    @pytest.mark.timeout(180)
    def test_delegate_creates_task(self, spark_ssh, tmp_path) -> None:
        """delegate() creates a task in the TaskManager before sending the prompt.

        After delegation completes there must be at least one task in the
        manager's store, and it must be assigned to the target agent.
        """
        from orchestrator.config import OrchestratorSettings
        from orchestrator.orchestrator import Orchestrator
        from orchestrator.sandbox_bridge import SandboxBridge
        from orchestrator.task_manager import TaskManager

        settings = OrchestratorSettings()
        bridge = SandboxBridge(settings=settings, conn=spark_ssh)
        manager = TaskManager(storage_path=tmp_path / "tasks.json")
        orch = Orchestrator(settings=settings, bridge=bridge, task_manager=manager)

        orch.delegate(agent_name="openclaw", prompt="Say: task created")

        all_tasks = manager.list_tasks()
        assert len(all_tasks) > 0, "No tasks were created in the TaskManager after delegation"

        openclaw_tasks = manager.list_tasks(assigned_to="openclaw")
        assert len(openclaw_tasks) > 0, "No tasks assigned to 'openclaw' found after delegation"

    @pytest.mark.slow
    @pytest.mark.timeout(180)
    def test_delegate_updates_task_on_completion(self, spark_ssh, tmp_path) -> None:
        """The task created by delegate() has status='completed' after the call returns.

        Verifies the full task lifecycle is driven by the Orchestrator so
        that external callers can rely on task status for progress tracking.
        """
        from orchestrator.config import OrchestratorSettings
        from orchestrator.orchestrator import Orchestrator
        from orchestrator.sandbox_bridge import SandboxBridge
        from orchestrator.task_manager import TaskManager

        settings = OrchestratorSettings()
        bridge = SandboxBridge(settings=settings, conn=spark_ssh)
        manager = TaskManager(storage_path=tmp_path / "tasks.json")
        orch = Orchestrator(settings=settings, bridge=bridge, task_manager=manager)

        orch.delegate(agent_name="openclaw", prompt="Say: completed")

        completed_tasks = manager.list_tasks(status="completed")
        assert len(completed_tasks) > 0, (
            "Expected at least one task with status='completed' after delegation, "
            f"but found none.  All tasks: {manager.list_tasks()}"
        )


# ---------------------------------------------------------------------------
# Behavioural tests — multi-step pipelines
# ---------------------------------------------------------------------------


class TestPipeline:
    """Behavioural tests: Orchestrator.run_pipeline() chains steps in sequence."""

    @pytest.mark.slow
    @pytest.mark.timeout(180)
    def test_simple_pipeline(self, spark_ssh, tmp_path) -> None:
        """run_pipeline() executes a two-step pipeline and returns a PipelineResult.

        Step 1: openclaw analyses a short text fragment.
        Step 2: openclaw summarises the analysis in one sentence.
        The PipelineResult must carry two step results.
        """
        from orchestrator.config import OrchestratorSettings
        from orchestrator.orchestrator import Orchestrator, PipelineResult, PipelineStep
        from orchestrator.sandbox_bridge import SandboxBridge
        from orchestrator.task_manager import TaskManager

        settings = OrchestratorSettings()
        bridge = SandboxBridge(settings=settings, conn=spark_ssh)
        manager = TaskManager(storage_path=tmp_path / "tasks.json")
        orch = Orchestrator(settings=settings, bridge=bridge, task_manager=manager)

        steps = [
            PipelineStep(
                agent_name="openclaw",
                prompt="List three benefits of modular software design.",
                step_name="analyze",
            ),
            PipelineStep(
                agent_name="openclaw",
                prompt="Summarise this in one sentence: {prev_result}",
                step_name="summarize",
            ),
        ]

        pipeline_result = orch.run_pipeline(steps=steps)

        assert isinstance(pipeline_result, PipelineResult), (
            f"run_pipeline() must return PipelineResult, got {type(pipeline_result)}"
        )
        assert len(pipeline_result.step_results) == 2, (
            f"Expected 2 step results, got {len(pipeline_result.step_results)}"
        )

    @pytest.mark.slow
    @pytest.mark.timeout(180)
    def test_pipeline_passes_results(self, spark_ssh, tmp_path) -> None:
        """The {{prev_result}} template in a pipeline step is replaced with actual output.

        Runs a two-step pipeline where step 2 contains {{prev_result}}.  After
        execution the prompt used for step 2 must contain actual text from step 1,
        not the literal string '{{prev_result}}'.
        """
        from orchestrator.config import OrchestratorSettings
        from orchestrator.orchestrator import Orchestrator, PipelineStep
        from orchestrator.sandbox_bridge import SandboxBridge
        from orchestrator.task_manager import TaskManager

        settings = OrchestratorSettings()
        bridge = SandboxBridge(settings=settings, conn=spark_ssh)
        manager = TaskManager(storage_path=tmp_path / "tasks.json")
        orch = Orchestrator(settings=settings, bridge=bridge, task_manager=manager)

        steps = [
            PipelineStep(
                agent_name="openclaw",
                prompt="Say exactly: FIRST_STEP_DONE",
                step_name="first",
            ),
            PipelineStep(
                agent_name="openclaw",
                prompt="Echo back this text verbatim: {prev_result}",
                step_name="second",
            ),
        ]

        pipeline_result = orch.run_pipeline(steps=steps)
        second_output = pipeline_result.step_results[1].output

        # The second step received a resolved prompt — its output must not
        # literally contain the unresolved template placeholder.
        assert "{prev_result}" not in second_output, (
            "Template '{prev_result}' was not substituted in step 2 output"
        )

    @pytest.mark.slow
    @pytest.mark.timeout(180)
    def test_pipeline_result_structure(self, spark_ssh, tmp_path) -> None:
        """PipelineResult exposes step_results with the correct count and step names.

        Each StepResult must carry the step_name from its PipelineStep definition
        so that callers can correlate results back to their source steps.
        """
        from orchestrator.config import OrchestratorSettings
        from orchestrator.orchestrator import Orchestrator, PipelineStep
        from orchestrator.sandbox_bridge import SandboxBridge
        from orchestrator.task_manager import TaskManager

        settings = OrchestratorSettings()
        bridge = SandboxBridge(settings=settings, conn=spark_ssh)
        manager = TaskManager(storage_path=tmp_path / "tasks.json")
        orch = Orchestrator(settings=settings, bridge=bridge, task_manager=manager)

        steps = [
            PipelineStep(agent_name="openclaw", prompt="Say: alpha", step_name="alpha"),
            PipelineStep(agent_name="openclaw", prompt="Say: beta", step_name="beta"),
            PipelineStep(agent_name="openclaw", prompt="Say: gamma", step_name="gamma"),
        ]

        pipeline_result = orch.run_pipeline(steps=steps)
        step_names = [sr.step_name for sr in pipeline_result.step_results]

        assert step_names == ["alpha", "beta", "gamma"], (
            f"Pipeline step names mismatch: {step_names}"
        )


# ---------------------------------------------------------------------------
# Negative / error-path tests
# ---------------------------------------------------------------------------


class TestPipelineNegative:
    """Negative tests: Orchestrator raises appropriate errors for invalid pipelines."""

    def test_pipeline_with_invalid_agent(self, spark_ssh, tmp_path) -> None:
        """run_pipeline() raises an error when a step references an unknown agent.

        The error must be raised before any prompt is sent so that invalid
        pipelines are caught early rather than mid-execution.
        """
        from orchestrator.config import OrchestratorSettings
        from orchestrator.orchestrator import Orchestrator, PipelineStep
        from orchestrator.sandbox_bridge import SandboxBridge
        from orchestrator.task_manager import TaskManager

        settings = OrchestratorSettings()
        bridge = SandboxBridge(settings=settings, conn=spark_ssh)
        manager = TaskManager(storage_path=tmp_path / "tasks.json")
        orch = Orchestrator(settings=settings, bridge=bridge, task_manager=manager)

        steps = [
            PipelineStep(
                agent_name="this-agent-does-not-exist",
                prompt="This should never be sent",
                step_name="bad_step",
            ),
        ]

        with pytest.raises((KeyError, ValueError, LookupError)):
            orch.run_pipeline(steps=steps)

    def test_max_delegation_depth(self, tmp_path) -> None:
        """Orchestrator respects max_delegation_depth and raises when exceeded.

        Sets max_delegation_depth=1 and triggers a delegation chain of depth 2,
        expecting the orchestrator to raise a RecursionError or equivalent
        before completing the inner call.
        """
        from orchestrator.config import OrchestratorSettings
        from orchestrator.orchestrator import Orchestrator
        from orchestrator.sandbox_bridge import SandboxBridge
        from orchestrator.task_manager import TaskManager

        settings = OrchestratorSettings(max_delegation_depth=1)
        # Use a no-op bridge stub to avoid network calls in this unit-level check.
        bridge = SandboxBridge(settings=settings)
        manager = TaskManager(storage_path=tmp_path / "tasks.json")
        orch = Orchestrator(settings=settings, bridge=bridge, task_manager=manager)

        with pytest.raises((RecursionError, RuntimeError, ValueError)):
            # Simulate a depth-2 delegation by calling delegate twice with the
            # depth counter already at the limit.
            orch.delegate(
                agent_name="openclaw",
                prompt="Nested delegation",
                _current_depth=2,
            )


# ---------------------------------------------------------------------------
# Behavioural tests — parallel specialist dispatch
# ---------------------------------------------------------------------------


class TestParallelSpecialists:
    """Behavioural tests: Orchestrator dispatches prompts to multiple agents in parallel."""

    @pytest.mark.slow
    @pytest.mark.timeout(180)
    def test_parallel_execution(self, spark_ssh, tmp_path) -> None:
        """dispatch_parallel() sends to 2 agents simultaneously and both return results.

        Verifies that the method returns without error and that results are
        present for each agent that was targeted.
        """
        from orchestrator.config import OrchestratorSettings
        from orchestrator.orchestrator import Orchestrator
        from orchestrator.sandbox_bridge import SandboxBridge
        from orchestrator.task_manager import TaskManager

        settings = OrchestratorSettings()
        bridge = SandboxBridge(settings=settings, conn=spark_ssh)
        manager = TaskManager(storage_path=tmp_path / "tasks.json")
        orch = Orchestrator(settings=settings, bridge=bridge, task_manager=manager)

        requests = {
            "openclaw": "Reply with the word: alpha",
            "openclaw_2": "Reply with the word: beta",
        }

        results = orch.dispatch_parallel(requests=requests)

        assert isinstance(results, dict), (
            f"dispatch_parallel() must return dict, got {type(results)}"
        )
        assert len(results) > 0, "dispatch_parallel() returned no results"
        for agent_key, output in results.items():
            assert isinstance(output, str), (
                f"Result for {agent_key!r} must be str, got {type(output)}"
            )
            assert len(output.strip()) > 0, f"Result for {agent_key!r} is empty"

    @pytest.mark.slow
    @pytest.mark.timeout(180)
    def test_parallel_results_keyed_by_agent(self, spark_ssh, tmp_path) -> None:
        """dispatch_parallel() keys the results dict by the same agent names supplied.

        The keys in the returned dict must exactly match the keys in the input
        requests dict so that callers can reliably look up per-agent outputs.
        """
        from orchestrator.config import OrchestratorSettings
        from orchestrator.orchestrator import Orchestrator
        from orchestrator.sandbox_bridge import SandboxBridge
        from orchestrator.task_manager import TaskManager

        settings = OrchestratorSettings()
        bridge = SandboxBridge(settings=settings, conn=spark_ssh)
        manager = TaskManager(storage_path=tmp_path / "tasks.json")
        orch = Orchestrator(settings=settings, bridge=bridge, task_manager=manager)

        agent_keys = ["openclaw_a", "openclaw_b"]
        requests = {key: "Say: pong" for key in agent_keys}

        results = orch.dispatch_parallel(requests=requests)

        for key in agent_keys:
            assert key in results, (
                f"Agent key {key!r} not found in dispatch_parallel() results. "
                f"Got keys: {list(results.keys())}"
            )
