"""Tests for WhatsApp webhook ingress and queue-backed processing."""

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
    DispatchRecord,
    MediaStager,
    RoutedOrchestratorDispatcher,
    WhatsAppAttachment,
    WhatsAppConversationWorker,
    WhatsAppIngressApp,
    WhatsAppMessage,
    start_whatsapp_server,
)
from orchestrator.work_queue import WorkQueue

pytestmark = pytest.mark.phase6


def _payload() -> dict[str, object]:
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid.1",
                                    "from": "15550001",
                                    "timestamp": "1710000000",
                                    "type": "text",
                                    "text": {"body": "hello from whatsapp"},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }


class _StubDispatcher:
    def __init__(self) -> None:
        self.messages: list[WhatsAppMessage] = []

    def dispatch(self, message: WhatsAppMessage) -> DispatchRecord:
        self.messages.append(message)
        return DispatchRecord(message_id=message.message_id, agent="openclaw", notes="ok")


class _FakeOrchestrator:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def delegate(self, prompt: str, agent: str, task_type: str = "analysis") -> DelegationResult:
        self.calls.append((prompt, agent, task_type))
        return DelegationResult(
            task_id="task-1",
            agent=agent,
            task_type=task_type,
            prompt=prompt,
            output_text="handled",
            sandbox_result=SandboxResult(
                sandbox_name="sandbox",
                command="fake",
                stdout="handled\n",
                stderr="",
                return_code=0,
                duration_ms=5.0,
            ),
            duration_ms=5.0,
        )


class TestWhatsAppIngress:
    def test_webhook_ack_is_immediate_and_deduplicates(self, tmp_path) -> None:
        store = SQLiteStore(tmp_path / "shared")
        queue = WorkQueue(store)
        app = WhatsAppIngressApp(store, queue, verify_token="secret")
        server = start_whatsapp_server(app)

        try:
            request = Request(
                url=f"{server.base_url}/webhooks/whatsapp",
                method="POST",
                data=json.dumps(_payload()).encode("utf-8"),
                headers={"content-type": "application/json"},
            )
            with urlopen(request) as response:
                first = json.loads(response.read())

            with urlopen(request) as response:
                second = json.loads(response.read())
        finally:
            server.shutdown()

        assert first == {"status": "accepted", "accepted": 1, "duplicates": 0}
        assert second == {"status": "accepted", "accepted": 0, "duplicates": 1}
        assert len(queue.list_items("whatsapp-inbound")) == 1

    def test_worker_processes_text_and_stages_images(self, tmp_path) -> None:
        store = SQLiteStore(tmp_path / "shared")
        queue = WorkQueue(store)
        dispatcher = _StubDispatcher()
        stager = MediaStager(store, tmp_path / "media")
        worker = WhatsAppConversationWorker(queue, dispatcher, store=store, media_stager=stager)

        inline_image = base64.b64encode(b"fake-image-bytes").decode("ascii")
        queue.enqueue(
            "whatsapp-inbound",
            {
                "message_id": "wamid.2",
                "sender_id": "15550002",
                "conversation_id": "15550002",
                "timestamp": "1710000001",
                "text": "see image",
                "attachments": [
                    {
                        "kind": "image",
                        "content_type": "image/png",
                        "inline_base64": inline_image,
                    }
                ],
                "raw_payload": {},
            },
            dedupe_key="whatsapp:wamid.2",
        )
        from orchestrator.whatsapp import InboundEventStore

        InboundEventStore(store).record(
            source="whatsapp",
            event_key="wamid.2",
            payload={"id": "wamid.2"},
            queue_item_id="q-1",
        )

        assert worker.process_once() is True
        assert len(dispatcher.messages) == 1
        staged_path = dispatcher.messages[0].attachments[0].local_path
        assert staged_path is not None
        assert (tmp_path / "media").exists()

    def test_routed_dispatcher_uses_agent_route_for_images(self) -> None:
        orchestrator = _FakeOrchestrator()
        dispatcher = RoutedOrchestratorDispatcher(
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
                        name="image-to-gemini",
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
        )

        dispatch = dispatcher.dispatch(
            WhatsAppMessage(
                message_id="wamid.3",
                sender_id="15550003",
                conversation_id="15550003",
                timestamp="1710000002",
                text="describe this",
                attachments=[WhatsAppAttachment(kind="image", local_path="/tmp/example.png")],
            )
        )

        assert dispatch.agent == "gemini"
        assert orchestrator.calls[0][1] == "gemini"
