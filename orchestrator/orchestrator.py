"""Main orchestrator for multi-agent task delegation."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from pydantic import BaseModel

from orchestrator.config import OrchestratorSettings
from orchestrator.models import DelegationResult, PipelineResult, StepResult, TaskResult, TaskType
from orchestrator.sandbox_bridge import SandboxBridge
from orchestrator.shared_mcp import SharedWorkspace
from orchestrator.storage import SQLiteStore
from orchestrator.task_manager import TaskManager
from orchestrator.work_queue import WorkQueue


class PipelineStep(BaseModel):
    """A single step in an orchestrated pipeline."""

    agent: str
    task_type: TaskType
    prompt_template: str


class SpecialistResult(BaseModel):
    """Structured result for parallel fan-out workflows."""

    agent: str
    success: bool
    delegation: DelegationResult | None = None
    error: str | None = None


class Orchestrator:
    """Coordinates task delegation across OpenShell agent sandboxes."""

    def __init__(self, settings: OrchestratorSettings | None = None) -> None:
        self.settings: OrchestratorSettings = settings or OrchestratorSettings()
        self.store = SQLiteStore(self.settings.shared_workspace)
        self.bridge = SandboxBridge(self.settings)
        self.task_manager = TaskManager(self.settings.shared_workspace, store=self.store)
        self.work_queue = WorkQueue(self.store)
        self.workspace = SharedWorkspace(self.settings.shared_workspace)
        self.workspace.setup()

    def delegate(
        self,
        prompt: str,
        agent: str,
        task_type: TaskType = "analysis",
        parent_task_id: str | None = None,
        _current_depth: int = 0,
    ) -> DelegationResult:
        if _current_depth > self.settings.max_delegation_depth:
            raise RecursionError(
                "Maximum delegation depth exceeded: "
                f"{_current_depth} > {self.settings.max_delegation_depth}"
            )

        agent_config = self.settings.get_agent(agent)
        task = self.task_manager.create_task(
            type=task_type,
            prompt=prompt,
            assigned_to=agent,
            parent_task_id=parent_task_id,
        )
        self.task_manager.update_task(task.id, "running")

        try:
            sandbox_result = self.bridge.send_prompt(
                sandbox_name=agent_config.sandbox_name,
                prompt=prompt,
                agent_type=agent,
            )
        except Exception as exc:
            self.task_manager.update_task(
                task.id,
                "failed",
                result=TaskResult(error=str(exc)),
            )
            raise

        task_result = TaskResult(
            output_text=sandbox_result.output_text,
            sandbox_result=sandbox_result,
        )
        self.task_manager.update_task(task.id, "completed", result=task_result)
        return DelegationResult(
            task_id=task.id,
            agent=agent,
            task_type=task_type,
            prompt=prompt,
            output_text=sandbox_result.output_text,
            sandbox_result=sandbox_result,
            duration_ms=sandbox_result.duration_ms,
        )

    def pipeline(
        self,
        prompt: str,
        steps: list[PipelineStep],
        parent_task_id: str | None = None,
    ) -> PipelineResult:
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
                resolved_prompt = step.prompt_template

            delegation = self.delegate(
                prompt=resolved_prompt,
                agent=step.agent,
                task_type=step.task_type,
                parent_task_id=parent_task_id,
            )
            step_result = StepResult(
                step_index=idx,
                agent=step.agent,
                task_type=step.task_type,
                prompt=resolved_prompt,
                output_text=delegation.output_text,
                duration_ms=delegation.duration_ms,
                task_id=delegation.task_id,
                sandbox_result=delegation.sandbox_result,
            )
            result.steps.append(step_result)
            result.total_duration_ms += delegation.duration_ms
            prev_result = delegation.output_text
            step_outputs[f"step_{idx + 1}_result"] = delegation.output_text

        result.final_output = prev_result
        return result

    def research_and_implement(self, prompt: str) -> PipelineResult:
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

    def parallel_specialists(
        self,
        prompt: str,
        agents: list[str],
        task_type: TaskType = "analysis",
        max_workers: int | None = None,
    ) -> dict[str, SpecialistResult]:
        if not agents:
            return {}

        workers = max_workers if max_workers is not None else len(agents)
        results: dict[str, SpecialistResult] = {}

        def _delegate_one(agent: str) -> SpecialistResult:
            try:
                delegation = self.delegate(prompt, agent=agent, task_type=task_type)
            except Exception as exc:
                return SpecialistResult(agent=agent, success=False, error=str(exc))
            return SpecialistResult(agent=agent, success=True, delegation=delegation)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_agent = {executor.submit(_delegate_one, agent): agent for agent in agents}
            for future in as_completed(future_to_agent):
                specialist_result = future.result()
                results[specialist_result.agent] = specialist_result

        return results
