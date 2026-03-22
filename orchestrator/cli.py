r"""Command-line interface for the NemoClaw orchestrator.

Provides a simple argparse-based CLI that wraps the Orchestrator API for
interactive use and scripting.

Usage examples::

    # Delegate a prompt to a specific agent
    python -m orchestrator delegate --agent codex --prompt "write a hello world"

    # Execute a multi-step pipeline
    python -m orchestrator pipeline \\
        --steps "gemini:research,codex:implement,claude:review" \\
        --prompt "build a REST API"

    # Show all task records
    python -m orchestrator status

    # Check reachability of all configured sandboxes
    python -m orchestrator health
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import NoReturn

from orchestrator.config import OrchestratorSettings
from orchestrator.orchestrator import Orchestrator, PipelineStep
from orchestrator.task_manager import TaskType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Construct and return the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="orchestrator",
        description="NemoClaw inter-agent orchestrator CLI",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit output as JSON (where applicable).",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # ---- delegate --------------------------------------------------------
    delegate_parser = subparsers.add_parser(
        "delegate",
        help="Send a single prompt to one agent and print the response.",
    )
    delegate_parser.add_argument(
        "--agent",
        required=True,
        help="Logical agent name (e.g. codex, claude, gemini, openclaw).",
    )
    delegate_parser.add_argument(
        "--prompt",
        required=True,
        help="Prompt text to send to the agent.",
    )
    delegate_parser.add_argument(
        "--task-type",
        default="analysis",
        dest="task_type",
        choices=["research", "code_generation", "code_review", "analysis", "implementation"],
        help="Task category (default: analysis).",
    )

    # ---- pipeline --------------------------------------------------------
    pipeline_parser = subparsers.add_parser(
        "pipeline",
        help=(
            "Execute a sequential multi-agent pipeline. "
            "Steps are specified as a comma-separated list of "
            '"agent:task_type" pairs.'
        ),
    )
    pipeline_parser.add_argument(
        "--steps",
        required=True,
        help=(
            'Comma-separated pipeline steps, e.g. "gemini:research,codex:implement,claude:review".'
        ),
    )
    pipeline_parser.add_argument(
        "--prompt",
        required=True,
        help="Initial prompt passed to the first pipeline step.",
    )

    # ---- status ----------------------------------------------------------
    status_parser = subparsers.add_parser(
        "status",
        help="Show all tasks and their current status.",
    )
    status_parser.add_argument(
        "--filter-status",
        dest="filter_status",
        default=None,
        choices=["pending", "running", "completed", "failed"],
        help="Only show tasks with this status.",
    )
    status_parser.add_argument(
        "--filter-agent",
        dest="filter_agent",
        default=None,
        help="Only show tasks assigned to this agent.",
    )

    # ---- health ----------------------------------------------------------
    subparsers.add_parser(
        "health",
        help="Check reachability of all configured sandboxes.",
    )

    return parser


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def _cmd_delegate(args: argparse.Namespace, orc: Orchestrator) -> int:
    """Handle the ``delegate`` subcommand.

    Args:
        args: Parsed CLI arguments.
        orc: Initialised Orchestrator instance.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    try:
        response = orc.delegate(
            prompt=args.prompt,
            agent=args.agent,
            task_type=args.task_type,
        )
    except KeyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Delegation failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"agent": args.agent, "response": response}, indent=2))
    else:
        print(response)

    return 0


def _cmd_pipeline(args: argparse.Namespace, orc: Orchestrator) -> int:
    """Handle the ``pipeline`` subcommand.

    Args:
        args: Parsed CLI arguments.
        orc: Initialised Orchestrator instance.

    Returns:
        Exit code.
    """
    steps: list[PipelineStep] = []
    for raw in args.steps.split(","):
        raw = raw.strip()
        if ":" not in raw:
            print(
                f'Error: invalid step specification {raw!r}. Expected format: "agent:task_type"',
                file=sys.stderr,
            )
            return 1
        agent, task_type_str = raw.split(":", 1)
        valid_types: tuple[TaskType, ...] = (
            "research",
            "code_generation",
            "code_review",
            "analysis",
            "implementation",
        )
        if task_type_str not in valid_types:
            print(
                f"Error: unknown task type {task_type_str!r}. "
                f"Valid types: {', '.join(valid_types)}",
                file=sys.stderr,
            )
            return 1
        steps.append(
            PipelineStep(
                agent=agent.strip(),
                task_type=task_type_str,  # type: ignore[arg-type]
                prompt_template="{prev_result}",
            )
        )

    # Override the first step to inject the initial prompt directly.
    if steps:
        steps[0] = PipelineStep(
            agent=steps[0].agent,
            task_type=steps[0].task_type,
            prompt_template=args.prompt,
        )

    try:
        result = orc.pipeline(prompt=args.prompt, steps=steps)
    except Exception as exc:
        print(f"Pipeline failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(
            json.dumps(
                {
                    "final_output": result.final_output,
                    "total_duration_ms": result.total_duration_ms,
                    "steps": [s.model_dump() for s in result.steps],
                },
                indent=2,
            )
        )
    else:
        for step in result.steps:
            print(f"\n--- Step {step.step_index + 1}: {step.agent} ({step.task_type}) ---")
            print(step.output)
        print("\n=== Final output ===")
        print(result.final_output)
        print(f"\nTotal duration: {result.total_duration_ms:.0f} ms")

    return 0


def _cmd_status(args: argparse.Namespace, orc: Orchestrator) -> int:
    """Handle the ``status`` subcommand.

    Args:
        args: Parsed CLI arguments.
        orc: Initialised Orchestrator instance.

    Returns:
        Exit code.
    """
    tasks = orc.task_manager.list_tasks(
        status=args.filter_status,
        assigned_to=args.filter_agent,
    )

    if args.json:
        print(json.dumps([t.model_dump() for t in tasks], indent=2))
        return 0

    if not tasks:
        print("No tasks found.")
        return 0

    # Column widths
    col_id = 36
    col_type = 16
    col_agent = 10
    col_status = 10

    header = (
        f"{'ID':<{col_id}}  "
        f"{'TYPE':<{col_type}}  "
        f"{'AGENT':<{col_agent}}  "
        f"{'STATUS':<{col_status}}  "
        f"CREATED"
    )
    print(header)
    print("-" * len(header))

    for task in tasks:
        print(
            f"{task.id:<{col_id}}  "
            f"{task.type:<{col_type}}  "
            f"{task.assigned_to:<{col_agent}}  "
            f"{task.status:<{col_status}}  "
            f"{task.created_at}"
        )

    return 0


def _cmd_health(args: argparse.Namespace, orc: Orchestrator) -> int:
    """Handle the ``health`` subcommand.

    Args:
        args: Parsed CLI arguments.
        orc: Initialised Orchestrator instance.

    Returns:
        Exit code (0 if all healthy, 1 if any sandbox is unreachable).
    """
    sandboxes = orc.bridge.list_sandboxes()
    results: dict[str, bool] = {}

    for sandbox in sandboxes:
        healthy = orc.bridge.is_sandbox_healthy(sandbox)
        results[sandbox] = healthy

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        max_len = max((len(s) for s in sandboxes), default=8)
        print(f"{'SANDBOX':<{max_len}}  STATUS")
        print("-" * (max_len + 10))
        for sandbox, healthy in sorted(results.items()):
            status = "healthy" if healthy else "UNREACHABLE"
            print(f"{sandbox:<{max_len}}  {status}")

    all_healthy = all(results.values())
    return 0 if all_healthy else 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> NoReturn:
    """Parse arguments and dispatch to the appropriate command handler.

    Args:
        argv: Argument list.  Uses ``sys.argv[1:]`` when ``None``.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        settings = OrchestratorSettings()
        orc = Orchestrator(settings)
    except Exception as exc:
        print(f"Failed to initialise orchestrator: {exc}", file=sys.stderr)
        sys.exit(2)

    dispatch = {
        "delegate": _cmd_delegate,
        "pipeline": _cmd_pipeline,
        "status": _cmd_status,
        "health": _cmd_health,
    }

    handler = dispatch.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    exit_code = handler(args, orc)
    sys.exit(exit_code)
