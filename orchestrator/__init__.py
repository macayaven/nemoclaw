"""NemoClaw Orchestrator.

This package provides inter-agent communication and task delegation across
OpenShell sandboxes running on DGX Spark infrastructure. The orchestrator
runs outside sandbox containers and dispatches work into them via the
``openshell sandbox connect`` command.

Typical usage::

    from orchestrator import Orchestrator

    orc = Orchestrator()
    result = orc.delegate("Summarise the Attention paper", agent="gemini")
    print(result)

    pipeline_result = orc.research_and_implement("Build a REST API for task tracking")
"""

from __future__ import annotations

__version__ = "0.1.0"

from orchestrator.orchestrator import Orchestrator
from orchestrator.config import OrchestratorSettings, AgentConfig
from orchestrator.task_manager import Task, TaskManager
from orchestrator.sandbox_bridge import SandboxBridge, SandboxResult
from orchestrator.shared_mcp import SharedWorkspace

__all__ = [
    "Orchestrator",
    "OrchestratorSettings",
    "AgentConfig",
    "Task",
    "TaskManager",
    "SandboxBridge",
    "SandboxResult",
    "SharedWorkspace",
    "__version__",
]
