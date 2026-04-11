"""WhatsApp webhook ingress foundation with durable queue-backed processing."""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import threading
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, Field

from orchestrator.models import DelegationResult
from orchestrator.orchestrator import Orchestrator
from orchestrator.routing import ModelCapabilityRegistry, RoutingPolicy, RoutingRequest
from orchestrator.storage import SQLiteStore, utc_now
from orchestrator.work_queue import QueueItem, QueueWorker, WorkQueue


class WhatsAppAttachment(BaseModel):
    """Normalized WhatsApp attachment payload."""

    kind: str
    external_id: str | None = None
    content_type: str | None = None
    caption: str | None = None
    url: str | None = None
    sha256: str | None = None
    inline_base64: str | None = None
    local_path: str | None = None


class WhatsAppMessage(BaseModel):
    """Normalized inbound WhatsApp message."""

    message_id: str
    sender_id: str
    conversation_id: str
    timestamp: str
    text: str | None = None
    attachments: list[WhatsAppAttachment] = Field(default_factory=list)
    raw_payload: dict[str, object] = Field(default_factory=dict)


class WebhookAck(BaseModel):
    """Immediate webhook acknowledgement payload."""

    status: str
    accepted: int
    duplicates: int


class DispatchRecord(BaseModel):
    """Worker-side result of processing an inbound message."""

    message_id: str
    agent: str
    task_id: str | None = None
    output_text: str | None = None
    notes: str | None = None


class ConversationDispatcher(Protocol):
    """Dispatch a normalized inbound message to a downstream conversation target."""

    def dispatch(self, message: WhatsAppMessage) -> DispatchRecord: ...


class InboundEventStore:
    """Persistent idempotency and receipt tracking for inbound webhook events."""

    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def record(
        self,
        *,
        source: str,
        event_key: str,
        payload: dict[str, object],
        queue_item_id: str | None,
    ) -> bool:
        now_iso = utc_now()
        with self.store.transaction() as connection:
            existing = connection.execute(
                """
                SELECT id FROM webhook_events
                WHERE source = ? AND event_key = ?
                """,
                (source, event_key),
            ).fetchone()
            if existing is not None:
                connection.execute(
                    """
                    UPDATE webhook_events
                    SET duplicate = 1, updated_at = ?
                    WHERE source = ? AND event_key = ?
                    """,
                    (now_iso, source, event_key),
                )
                return False

            connection.execute(
                """
                INSERT INTO webhook_events (
                    id, source, event_key, payload_json, queue_item_id, duplicate,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 0, 'accepted', ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    source,
                    event_key,
                    json.dumps(payload),
                    queue_item_id,
                    now_iso,
                    now_iso,
                ),
            )
        return True

    def mark_processed(self, *, source: str, event_key: str, status: str) -> None:
        with self.store.transaction() as connection:
            connection.execute(
                """
                UPDATE webhook_events
                SET status = ?, updated_at = ?
                WHERE source = ? AND event_key = ?
                """,
                (status, utc_now(), source, event_key),
            )


class MediaStager:
    """Stage inbound attachments on the host for later worker consumption."""

    def __init__(self, store: SQLiteStore, stage_root: Path, *, ttl_hours: int = 24) -> None:
        self.store = store
        self.stage_root = Path(stage_root)
        self.stage_root.mkdir(parents=True, exist_ok=True)
        self.ttl_hours = ttl_hours

    def stage_attachment(self, attachment: WhatsAppAttachment) -> WhatsAppAttachment:
        if attachment.kind != "image":
            return attachment
        content = self._load_content(attachment)
        digest = hashlib.sha256(content).hexdigest()
        extension = mimetypes.guess_extension(attachment.content_type or "") or ".bin"
        relative = Path(datetime.now(UTC).strftime("%Y/%m/%d")) / f"{digest}{extension}"
        destination = self.stage_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)

        expires_at = (datetime.now(UTC) + timedelta(hours=self.ttl_hours)).isoformat()
        now_iso = utc_now()
        with self.store.transaction() as connection:
            connection.execute(
                """
                INSERT INTO media_assets (
                    id, source, external_id, content_type, local_path, sha256, size_bytes,
                    expires_at, status, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'staged', ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    "whatsapp",
                    attachment.external_id,
                    attachment.content_type,
                    str(destination),
                    digest,
                    len(content),
                    expires_at,
                    json.dumps({"kind": attachment.kind}),
                    now_iso,
                    now_iso,
                ),
            )

        return attachment.model_copy(update={"local_path": str(destination), "sha256": digest})

    def cleanup_expired(self) -> int:
        removed = 0
        now_iso = utc_now()
        with self.store.transaction() as connection:
            rows = connection.execute(
                """
                SELECT id, local_path FROM media_assets
                WHERE expires_at <= ? AND status = 'staged'
                """,
                (now_iso,),
            ).fetchall()
            for row in rows:
                path = Path(row["local_path"])
                if path.exists():
                    path.unlink()
                    removed += 1
                connection.execute(
                    """
                    UPDATE media_assets
                    SET status = 'deleted', updated_at = ?
                    WHERE id = ?
                    """,
                    (now_iso, row["id"]),
                )
        return removed

    def _load_content(self, attachment: WhatsAppAttachment) -> bytes:
        if attachment.inline_base64 is not None:
            return base64.b64decode(attachment.inline_base64)
        if attachment.url is None:
            raise ValueError("image attachment is missing both url and inline_base64.")
        with urllib.request.urlopen(attachment.url, timeout=30) as response:
            return response.read()


class RoutedOrchestratorDispatcher:
    """Queue worker dispatcher that integrates WhatsApp ingress with routing policy."""

    def __init__(
        self,
        orchestrator: Orchestrator,
        *,
        default_agent: str = "openclaw",
        routing_policy: RoutingPolicy | None = None,
        capability_registry: ModelCapabilityRegistry | None = None,
        routing_model: str = "nemotron-3-super:120b",
    ) -> None:
        self.orchestrator = orchestrator
        self.default_agent = default_agent
        self.routing_policy = routing_policy
        self.capability_registry = capability_registry
        self.routing_model = routing_model

    def dispatch(self, message: WhatsAppMessage) -> DispatchRecord:
        agent = self.default_agent
        modalities = ["text"] + [attachment.kind for attachment in message.attachments]
        if self.routing_policy is not None and self.capability_registry is not None:
            decision = self.routing_policy.route(
                RoutingRequest(
                    path="/whatsapp/inbound",
                    model=self.routing_model,
                    channel="whatsapp",
                    modalities=sorted(set(modalities)),
                    headers={},
                    metadata={"sender_id": message.sender_id},
                ),
                self.capability_registry,
            )
            if decision.target.kind == "agent" and decision.target.agent is not None:
                agent = decision.target.agent

        prompt_parts = [
            f"WhatsApp sender: {message.sender_id}",
            f"Conversation: {message.conversation_id}",
        ]
        if message.text:
            prompt_parts.append(f"Message:\n{message.text}")
        image_paths = [
            attachment.local_path for attachment in message.attachments if attachment.local_path
        ]
        if image_paths:
            prompt_parts.append("Staged images:\n" + "\n".join(image_paths))
        unsupported = [
            attachment.kind
            for attachment in message.attachments
            if attachment.kind not in {"image"}
        ]
        if unsupported:
            prompt_parts.append(
                "Unsupported attachments were omitted for this run: "
                + ", ".join(sorted(set(unsupported)))
            )

        delegation: DelegationResult = self.orchestrator.delegate(
            prompt="\n\n".join(prompt_parts),
            agent=agent,
            task_type="analysis",
        )
        return DispatchRecord(
            message_id=message.message_id,
            agent=agent,
            task_id=delegation.task_id,
            output_text=delegation.output_text,
        )


class WhatsAppConversationWorker:
    """Queue-backed worker for WhatsApp conversation processing."""

    def __init__(
        self,
        queue: WorkQueue,
        dispatcher: ConversationDispatcher,
        *,
        store: SQLiteStore,
        media_stager: MediaStager | None = None,
        queue_name: str = "whatsapp-inbound",
    ) -> None:
        self.queue = queue
        self.dispatcher = dispatcher
        self.events = InboundEventStore(store)
        self.media_stager = media_stager
        self.worker = QueueWorker(queue, queue_name, self._handle_item)

    def process_once(self) -> bool:
        return self.worker.process_once()

    def _handle_item(self, item: QueueItem) -> None:
        message = WhatsAppMessage.model_validate(item.payload)
        attachments = message.attachments
        if self.media_stager is not None:
            attachments = [
                self.media_stager.stage_attachment(attachment) for attachment in attachments
            ]
            message = message.model_copy(update={"attachments": attachments})
        self.dispatcher.dispatch(message)
        self.events.mark_processed(
            source="whatsapp",
            event_key=message.message_id,
            status="processed",
        )


def parse_whatsapp_messages(payload: dict[str, object]) -> list[WhatsAppMessage]:
    """Extract normalized messages from a Meta webhook payload."""
    messages: list[WhatsAppMessage] = []
    entries = payload.get("entry")
    if not isinstance(entries, list):
        return messages

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        changes = entry.get("changes")
        if not isinstance(changes, list):
            continue

        for change in changes:
            if not isinstance(change, dict):
                continue
            value = change.get("value", {})
            if not isinstance(value, dict):
                continue
            raw_messages = value.get("messages")
            if not isinstance(raw_messages, list):
                continue

            for raw_message in raw_messages:
                if not isinstance(raw_message, dict):
                    continue
                sender_id = str(raw_message.get("from", ""))
                message_id = str(raw_message.get("id", ""))
                timestamp = str(raw_message.get("timestamp", ""))
                message_type = str(raw_message.get("type", "text"))
                attachments: list[WhatsAppAttachment] = []
                text = None
                if message_type == "text":
                    text_payload = raw_message.get("text", {})
                    if isinstance(text_payload, dict):
                        text = str(text_payload.get("body", ""))
                else:
                    attachment_payload = raw_message.get(message_type, {})
                    if isinstance(attachment_payload, dict):
                        attachments.append(
                            WhatsAppAttachment(
                                kind=message_type,
                                external_id=attachment_payload.get("id"),
                                content_type=attachment_payload.get("mime_type"),
                                caption=attachment_payload.get("caption"),
                                url=attachment_payload.get("url"),
                                sha256=attachment_payload.get("sha256"),
                                inline_base64=attachment_payload.get("inline_base64"),
                            )
                        )
                messages.append(
                    WhatsAppMessage(
                        message_id=message_id,
                        sender_id=sender_id,
                        conversation_id=sender_id,
                        timestamp=timestamp,
                        text=text,
                        attachments=attachments,
                        raw_payload=raw_message,
                    )
                )
    return messages


class WhatsAppIngressApp:
    """HTTP handler backing the WhatsApp webhook service."""

    def __init__(
        self,
        store: SQLiteStore,
        queue: WorkQueue,
        *,
        verify_token: str | None = None,
        queue_name: str = "whatsapp-inbound",
    ) -> None:
        self.store = store
        self.queue = queue
        self.verify_token = verify_token
        self.queue_name = queue_name
        self.events = InboundEventStore(store)

    def handle_http_request(
        self,
        *,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes = b"",
    ) -> tuple[int, dict[str, str], bytes]:
        split = urlsplit(path)
        if split.path != "/webhooks/whatsapp":
            return 404, {"content-type": "application/json"}, b'{"error":"not found"}'

        if method == "GET":
            query = parse_qs(split.query)
            token = query.get("hub.verify_token", [""])[0]
            challenge = query.get("hub.challenge", [""])[0]
            if self.verify_token is None or token != self.verify_token:
                return 403, {"content-type": "application/json"}, b'{"error":"verification failed"}'
            return 200, {"content-type": "text/plain"}, challenge.encode("utf-8")

        if method != "POST":
            return 405, {"content-type": "application/json"}, b'{"error":"method not allowed"}'

        payload = json.loads(body.decode("utf-8"))
        messages = parse_whatsapp_messages(payload)
        accepted = 0
        duplicates = 0
        for message in messages:
            queue_item = self.queue.enqueue(
                self.queue_name,
                message.model_dump(mode="json"),
                dedupe_key=f"whatsapp:{message.message_id}",
                metadata={"channel": "whatsapp"},
            )
            is_new = self.events.record(
                source="whatsapp",
                event_key=message.message_id,
                payload=message.model_dump(mode="json"),
                queue_item_id=queue_item.id,
            )
            if is_new:
                accepted += 1
            else:
                duplicates += 1

        ack = WebhookAck(status="accepted", accepted=accepted, duplicates=duplicates)
        return (
            200,
            {"content-type": "application/json"},
            ack.model_dump_json().encode("utf-8"),
        )


@dataclass
class RunningWhatsAppServer:
    """Convenience wrapper around a threaded local webhook server."""

    app: WhatsAppIngressApp
    server: ThreadingHTTPServer
    thread: threading.Thread

    @property
    def base_url(self) -> str:
        host, port = _server_host_port(self.server.server_address)
        return f"http://{host}:{port}"

    def shutdown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)


def start_whatsapp_server(
    app: WhatsAppIngressApp,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
) -> RunningWhatsAppServer:
    """Start the WhatsApp webhook service in a background thread."""

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self._handle()

        def do_POST(self) -> None:
            self._handle()

        def log_message(self, format: str, *args) -> None:
            return

        def _handle(self) -> None:
            length = int(self.headers.get("content-length", "0"))
            body = self.rfile.read(length) if length else b""
            status, response_headers, response_body = app.handle_http_request(
                method=self.command,
                path=self.path,
                headers={key: value for key, value in self.headers.items()},
                body=body,
            )
            self.send_response(status)
            for key, value in response_headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(response_body)

    server = ThreadingHTTPServer((host, port), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return RunningWhatsAppServer(app=app, server=server, thread=thread)


def _server_host_port(server_address: object) -> tuple[str, int]:
    """Extract the host and port from an IPv4 or IPv6 server address tuple."""
    if not isinstance(server_address, tuple) or len(server_address) < 2:
        raise RuntimeError(f"unexpected server address: {server_address!r}")

    host = str(server_address[0])
    port = int(server_address[1])
    return host, port
