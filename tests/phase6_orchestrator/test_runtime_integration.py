"""Local end-to-end harnesses for the host-side orchestrator runtime."""

from __future__ import annotations

import base64
import json
from urllib.request import Request, urlopen

import pytest

from orchestrator.models import DelegationResult, SandboxResult
from orchestrator.routing import (
    ModelCapability,
    ModelCapabilityRegistry,
    RoutingPolicy,
    RoutingRule,
    RoutingTarget,
)
from orchestrator.storage import SQLiteStore
from orchestrator.whatsapp import (
    MediaStager,
    RoutedOrchestratorDispatcher,
    WhatsAppConversationWorker,
    WhatsAppIngressApp,
    start_whatsapp_server,
)
from orchestrator.work_queue import WorkQueue

pytestmark = pytest.mark.phase6


class _FakeOrchestrator:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def delegate(self, prompt: str, agent: str, task_type: str = "analysis") -> DelegationResult:
        self.calls.append((prompt, agent, task_type))
        return DelegationResult(
            task_id="task-e2e",
            agent=agent,
            task_type=task_type,
            prompt=prompt,
            output_text="ok",
            sandbox_result=SandboxResult(
                sandbox_name="sandbox",
                command="fake",
                stdout="ok\n",
                stderr="",
                return_code=0,
                duration_ms=5.0,
            ),
            duration_ms=5.0,
        )


def test_whatsapp_webhook_to_worker_flow(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "shared")
    queue = WorkQueue(store)
    ingress = WhatsAppIngressApp(store, queue)
    server = start_whatsapp_server(ingress)
    orchestrator = _FakeOrchestrator()
    worker = WhatsAppConversationWorker(
        queue,
        RoutedOrchestratorDispatcher(
            orchestrator,
            routing_policy=RoutingPolicy(
                targets={
                    "openclaw-agent": RoutingTarget(
                        name="openclaw-agent",
                        kind="agent",
                        agent="openclaw",
                    ),
                    "gemini-direct": RoutingTarget(
                        name="gemini-direct",
                        kind="agent",
                        agent="gemini",
                    ),
                },
                rules=[
                    RoutingRule(
                        name="whatsapp-images-to-gemini",
                        target="gemini-direct",
                        channel="whatsapp",
                        required_modalities=["image"],
                    )
                ],
                default_target="openclaw-agent",
            ),
            capability_registry=ModelCapabilityRegistry(
                capabilities=[
                    ModelCapability(
                        model_pattern="nemotron-*",
                        target="openclaw-agent",
                        provider="ollama",
                    )
                ]
            ),
        ),
        store=store,
        media_stager=MediaStager(store, tmp_path / "media"),
    )

    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid.e2e",
                                    "from": "15550999",
                                    "timestamp": "1710000003",
                                    "type": "image",
                                    "image": {
                                        "mime_type": "image/png",
                                        "inline_base64": base64.b64encode(b"e2e-image").decode(
                                            "ascii"
                                        ),
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }

    try:
        request = Request(
            url=f"{server.base_url}/webhooks/whatsapp",
            method="POST",
            data=json.dumps(payload).encode("utf-8"),
            headers={"content-type": "application/json"},
        )
        with urlopen(request) as response:
            ack = json.loads(response.read())
    finally:
        server.shutdown()

    assert ack["accepted"] == 1
    assert worker.process_once() is True
    assert orchestrator.calls
    assert orchestrator.calls[0][1] == "gemini"
