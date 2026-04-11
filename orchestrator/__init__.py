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

from orchestrator.config import AgentConfig, OrchestratorSettings
from orchestrator.models import DelegationResult, PipelineResult, QueueItem, SandboxResult, Task
from orchestrator.orchestrator import Orchestrator, PipelineStep, SpecialistResult
from orchestrator.router_proxy import (
    OpenAIRouterProxy,
    ProxyResponse,
    RunningProxyServer,
    start_proxy_server,
)
from orchestrator.routing import (
    HEALTHCARE_TEXT_KEYWORDS,
    ModelCapability,
    ModelCapabilityRegistry,
    RoutingDecision,
    RoutingPolicy,
    RoutingRequest,
    RoutingRule,
    RoutingTarget,
    build_default_capability_registry,
    build_default_routing_policy,
    infer_prompt_text,
)
from orchestrator.sandbox_bridge import SandboxBridge
from orchestrator.shared_mcp import SharedWorkspace
from orchestrator.task_manager import TaskManager
from orchestrator.whatsapp import (
    DispatchRecord,
    MediaStager,
    RoutedOrchestratorDispatcher,
    RunningWhatsAppServer,
    WhatsAppAttachment,
    WhatsAppConversationWorker,
    WhatsAppIngressApp,
    WhatsAppMessage,
    start_whatsapp_server,
)
from orchestrator.work_queue import QueueWorker, WorkQueue

__all__ = [
    "HEALTHCARE_TEXT_KEYWORDS",
    "AgentConfig",
    "DelegationResult",
    "DispatchRecord",
    "MediaStager",
    "ModelCapability",
    "ModelCapabilityRegistry",
    "OpenAIRouterProxy",
    "Orchestrator",
    "OrchestratorSettings",
    "PipelineResult",
    "PipelineStep",
    "ProxyResponse",
    "QueueItem",
    "QueueWorker",
    "RoutedOrchestratorDispatcher",
    "RoutingDecision",
    "RoutingPolicy",
    "RoutingRequest",
    "RoutingRule",
    "RoutingTarget",
    "RunningProxyServer",
    "RunningWhatsAppServer",
    "SandboxBridge",
    "SandboxResult",
    "SharedWorkspace",
    "SpecialistResult",
    "Task",
    "TaskManager",
    "WhatsAppAttachment",
    "WhatsAppConversationWorker",
    "WhatsAppIngressApp",
    "WhatsAppMessage",
    "WorkQueue",
    "__version__",
    "build_default_capability_registry",
    "build_default_routing_policy",
    "infer_prompt_text",
    "start_proxy_server",
    "start_whatsapp_server",
]
