"""Main orchestrator for multi-agent task delegation.

The Orchestrator is the primary entry point for all inter-agent workflows.
It composes the SandboxBridge, TaskManager, and SharedWorkspace into a
single cohesive API that callers interact with.

Example::

    from orchestrator import Orchestrator

    orc = Orchestrator()

    # Single-agent delegation
    answer = orc.delegate("What is CUDA unified memory?", agent="gemini")

    # Pre-built pipeline
    result = orc.research_and_implement("Build a rate-limiter in Python")
    print(result.final_output)

    # Fan-out to multiple specialists in parallel
    opinions = orc.parallel_specialists("Review this design", ["claude", "gemini"])
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from orchestrator.config import OrchestratorSettings
from orchestrator.sandbox_bridge import SandboxBridge
from orchestrator.shared_mcp import SharedWorkspace
from orchestrator.task_manager import TaskManager, TaskType

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Pipeline models
# ---------------------------------------------------------------------------


class PipelineStep(BaseModel):
    """A single step in an orchestrated pipeline.

    Attributes:
        agent: Logical agent name that should handle this step.
        task_type: Category of work performed in this step.
        prompt_template: Jinja-like template for the prompt.  The string
            ``{prev_result}`` is replaced with the previous step's output
            before the prompt is dispatched.  Additional named placeholders
            ``{step_N_result}`` (1-indexed) are also substituted.
    """

    agent: str
    task_type: TaskType
    prompt_template: str


class StepResult(BaseModel):
    """Result produced by one pipeline step.

    Attributes:
        step_index: 0-based index of this step in the pipeline.
        agent: Agent that handled this step.
        task_type: Category of work performed.
        output: Agent response text.
        duration_ms: Wall-clock time for the step in milliseconds.
        task_id: ID of the underlying Task record.
    """

    step_index: int
    agent: str
    task_type: TaskType
    output: str
    duration_ms: float
    task_id: str


class PipelineResult(BaseModel):
    """Aggregated result of a full pipeline execution.

    Attributes:
        steps: Ordered list of individual step results.
        final_output: Output from the last step in the pipeline.
        total_duration_ms: Sum of all step durations.
    """

    steps: list[StepResult] = Field(default_factory=list)
    final_output: str = ""
    total_duration_ms: float = 0.0


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class Orchestrator:
    """Coordinates task delegation across OpenShell agent sandboxes.

    Composes :class:`~orchestrator.sandbox_bridge.SandboxBridge`,
    :class:`~orchestrator.task_manager.TaskManager`, and
    :class:`~orchestrator.shared_mcp.SharedWorkspace` into a single
    high-level API.

    Attributes:
        settings: Active orchestrator configuration.
        bridge: Sandbox communication layer.
        task_manager: Task persistence and lifecycle manager.
        workspace: Shared MCP filesystem manager.
    """

    def __init__(self, settings: OrchestratorSettings | None = None) -> None:
        """Initialise the orchestrator and all sub-components.

        Args:
            settings: Optional pre-built settings object.  When omitted,
                settings are loaded from environment variables and defaults.
        """
        self.settings: OrchestratorSettings = settings or OrchestratorSettings()
        self.bridge = SandboxBridge(self.settings)
        self.task_manager = TaskManager(self.settings.shared_workspace)
        self.workspace = SharedWorkspace(self.settings.shared_workspace)
        self.workspace.setup()

    # ------------------------------------------------------------------
    # Single-agent delegation
    # ------------------------------------------------------------------

    def delegate(
        self,
        prompt: str,
        agent: str,
        task_type: TaskType = "analysis",
        parent_task_id: str | None = None,
    ) -> str:
        """Delegate *prompt* to a single agent sandbox and return its response.

        Creates a Task record, dispatches the prompt via SandboxBridge, then
        marks the task completed or failed.

        Args:
            prompt: Natural-language instruction for the agent.
            agent: Logical agent name (key in ``settings.agents``).
            task_type: Broad category of work for reporting purposes.
            parent_task_id: Optional parent task ID for subtask tracing.

        Returns:
            Agent response text.

        Raises:
            KeyError: If *agent* is not in the current settings.
            RuntimeError: If the sandbox command fails.
            subprocess.TimeoutExpired: If the agent exceeds the timeout.
        """
        agent_config = self.settings.get_agent(agent)

        task = self.task_manager.create_task(
            type=task_type,
            prompt=prompt,
            assigned_to=agent,
            parent_task_id=parent_task_id,
        )

        self.task_manager.update_task(task.id, "running")

        try:
            response = self.bridge.send_prompt(
                sandbox_name=agent_config.sandbox_name,
                prompt=prompt,
                agent_type=agent,
            )
            self.task_manager.update_task(task.id, "completed", result=response)
            return response
        except Exception as exc:
            self.task_manager.update_task(task.id, "failed", result=str(exc))
            raise

    # ------------------------------------------------------------------
    # Pipeline execution
    # ------------------------------------------------------------------

    def pipeline(
        self,
        prompt: str,
        steps: list[PipelineStep],
        parent_task_id: str | None = None,
    ) -> PipelineResult:
        """Execute a sequential multi-step pipeline.

        Each step receives the previous step's output via the
        ``{prev_result}`` placeholder in its ``prompt_template``.
        All historical step outputs are available as ``{step_N_result}``
        (1-indexed) tokens.

        Args:
            prompt: Initial prompt passed to the first step as
                ``{prev_result}`` and also available as ``{step_0_prompt}``.
            steps: Ordered list of pipeline steps to execute.
            parent_task_id: Optional task ID to attach all generated tasks to.

        Returns:
            PipelineResult containing every step's output and the final
            combined output.
        """
        result = PipelineResult()
        prev_result = prompt
        step_outputs: dict[str, str] = {"step_0_prompt": prompt}

        for idx, step in enumerate(steps):
            substitutions = {
                "prev_result": prev_result,
                **step_outputs,
            }
            try:
                resolved_prompt = step.prompt_template.format(**substitutions)
            except KeyError:
                # If a placeholder is missing just use the template as-is.
                resolved_prompt = step.prompt_template

            t_start = time.monotonic()
            output = self.delegate(
                prompt=resolved_prompt,
                agent=step.agent,
                task_type=step.task_type,
                parent_task_id=parent_task_id,
            )
            duration_ms = (time.monotonic() - t_start) * 1000

            # Look up the most recently created task for this agent to
            # retrieve the task ID.
            agent_tasks = self.task_manager.list_tasks(assigned_to=step.agent)
            task_id = agent_tasks[-1].id if agent_tasks else ""

            step_result = StepResult(
                step_index=idx,
                agent=step.agent,
                task_type=step.task_type,
                output=output,
                duration_ms=duration_ms,
                task_id=task_id,
            )
            result.steps.append(step_result)
            result.total_duration_ms += duration_ms

            key = f"step_{idx + 1}_result"
            step_outputs[key] = output
            prev_result = output

        result.final_output = prev_result
        return result

    # ------------------------------------------------------------------
    # Pre-built pipelines
    # ------------------------------------------------------------------

    def research_and_implement(self, prompt: str) -> PipelineResult:
        """Run a research -> implement -> review pipeline.

        Gemini researches the topic, Codex produces an implementation based
        on the research findings, and Claude reviews the resulting code.

        Args:
            prompt: High-level description of what should be built.

        Returns:
            PipelineResult with all three step outputs.
        """
        steps = [
            PipelineStep(
                agent="gemini",
                task_type="research",
                prompt_template=(
                    "Research the following topic thoroughly and provide a comprehensive "
                    "summary of best practices, available libraries, and implementation "
                    "strategies:\n\n{prev_result}"
                ),
            ),
            PipelineStep(
                agent="codex",
                task_type="code_generation",
                prompt_template=(
                    "Based on the following research, implement a complete, working "
                    "solution with clear comments:\n\n"
                    "Original request: {step_0_prompt}\n\n"
                    "Research findings:\n{step_1_result}"
                ),
            ),
            PipelineStep(
                agent="claude",
                task_type="code_review",
                prompt_template=(
                    "Review the following implementation for correctness, security, "
                    "performance, and style. Provide specific, actionable feedback:\n\n"
                    "{prev_result}"
                ),
            ),
        ]
        return self.pipeline(prompt, steps)

    def code_review_pipeline(self, code: str) -> PipelineResult:
        """Run a review -> fix -> re-review pipeline on *code*.

        Claude performs an initial review, Codex applies the suggested fixes,
        and Claude re-reviews the patched version.

        Args:
            code: Source code to review and improve.

        Returns:
            PipelineResult with review, fixed code, and final review.
        """
        steps = [
            PipelineStep(
                agent="claude",
                task_type="code_review",
                prompt_template=(
                    "Review the following code carefully. List all bugs, anti-patterns, "
                    "security issues, and style violations with specific line references "
                    "where possible:\n\n{prev_result}"
                ),
            ),
            PipelineStep(
                agent="codex",
                task_type="code_generation",
                prompt_template=(
                    "Apply the following code review feedback to improve the code. "
                    "Return the complete corrected source file:\n\n"
                    "Original code:\n{step_0_prompt}\n\n"
                    "Review feedback:\n{step_1_result}"
                ),
            ),
            PipelineStep(
                agent="claude",
                task_type="code_review",
                prompt_template=(
                    "Perform a final review of this revised code. Confirm that all "
                    "previous issues have been addressed and identify any remaining "
                    "concerns:\n\n{prev_result}"
                ),
            ),
        ]
        return self.pipeline(code, steps)

    # ------------------------------------------------------------------
    # Parallel fan-out
    # ------------------------------------------------------------------

    def parallel_specialists(
        self,
        prompt: str,
        agents: list[str],
        task_type: TaskType = "analysis",
        max_workers: int | None = None,
    ) -> dict[str, str]:
        """Send *prompt* to multiple agents concurrently.

        Uses a :class:`~concurrent.futures.ThreadPoolExecutor` so that all
        sandbox calls are in-flight simultaneously.  Errors from individual
        agents are captured as error strings in the returned dict rather
        than propagating.

        Args:
            prompt: Prompt to send to every agent unchanged.
            agents: List of logical agent names.
            task_type: Task category applied to every delegation.
            max_workers: Thread pool size.  Defaults to ``len(agents)``.

        Returns:
            Dictionary mapping agent name to its response (or an error
            message prefixed with ``"ERROR: "``).
        """
        workers = max_workers if max_workers is not None else len(agents)
        results: dict[str, str] = {}

        def _delegate_one(agent: str) -> tuple[str, str]:
            try:
                return agent, self.delegate(prompt, agent=agent, task_type=task_type)
            except Exception as exc:
                return agent, f"ERROR: {exc}"

        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_agent = {executor.submit(_delegate_one, a): a for a in agents}
            for future in as_completed(future_to_agent):
                agent_name, response = future.result()
                results[agent_name] = response

        return results
