"""Tests for the host-side OpenAI-compatible router proxy."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

import pytest

from orchestrator.router_proxy import OpenAIRouterProxy, start_proxy_server
from orchestrator.routing import (
    HEALTHCARE_TEXT_KEYWORDS,
    ModelCapability,
    ModelCapabilityRegistry,
    RoutingPolicy,
    RoutingRule,
    RoutingTarget,
)

pytestmark = pytest.mark.phase6


def _start_stub_upstream() -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("content-length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            body = json.dumps({"received_model": payload["model"]}).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host = str(server.server_address[0])
    port = int(server.server_address[1])
    return server, thread, f"http://{host}:{port}"


class TestRouterProxy:
    def test_models_endpoint_exposes_registry(self) -> None:
        app = OpenAIRouterProxy(
            policy=RoutingPolicy(
                targets={
                    "local-proxy": RoutingTarget(
                        name="local-proxy",
                        kind="proxy_upstream",
                        upstream_url="http://127.0.0.1:1",
                    )
                },
                default_target="local-proxy",
            ),
            registry=ModelCapabilityRegistry(
                capabilities=[
                    ModelCapability(
                        model_pattern="nemotron-*",
                        target="local-proxy",
                        provider="ollama",
                    )
                ]
            ),
        )

        response = app.handle_http_request(
            method="GET",
            path="/v1/models",
            headers={},
        )

        payload = json.loads(response.body)
        assert response.status_code == 200
        assert payload["data"][0]["id"] == "nemotron-*"

    def test_proxy_forwards_to_upstream(self) -> None:
        upstream_server, upstream_thread, upstream_url = _start_stub_upstream()
        app = OpenAIRouterProxy(
            policy=RoutingPolicy(
                targets={
                    "local-proxy": RoutingTarget(
                        name="local-proxy",
                        kind="proxy_upstream",
                        upstream_url=upstream_url,
                    )
                },
                default_target="local-proxy",
            ),
            registry=ModelCapabilityRegistry(
                capabilities=[
                    ModelCapability(
                        model_pattern="nemotron-*",
                        target="local-proxy",
                        provider="ollama",
                    )
                ]
            ),
        )
        proxy = start_proxy_server(app)

        try:
            request = Request(
                url=f"{proxy.base_url}/v1/chat/completions",
                method="POST",
                data=json.dumps({"model": "nemotron-3-super:120b", "messages": []}).encode("utf-8"),
                headers={"content-type": "application/json"},
            )
            with urlopen(request) as response:
                payload = json.loads(response.read().decode("utf-8"))
                route_header = response.headers.get("x-nemoclaw-target")
        finally:
            proxy.shutdown()
            upstream_server.shutdown()
            upstream_thread.join(timeout=5)

        assert payload["received_model"] == "nemotron-3-super:120b"
        assert route_header == "local-proxy"

    def test_proxy_rewrites_healthcare_requests_to_medgemma(self) -> None:
        upstream_server, upstream_thread, upstream_url = _start_stub_upstream()
        app = OpenAIRouterProxy(
            policy=RoutingPolicy(
                targets={
                    "local-proxy": RoutingTarget(
                        name="local-proxy",
                        kind="proxy_upstream",
                        upstream_url="http://127.0.0.1:1",
                    ),
                    "medgemma-mac": RoutingTarget(
                        name="medgemma-mac",
                        kind="proxy_upstream",
                        upstream_url=upstream_url,
                    ),
                },
                rules=[
                    RoutingRule(
                        name="healthcare-text-to-medgemma",
                        target="medgemma-mac",
                        metadata_keywords_any=list(HEALTHCARE_TEXT_KEYWORDS),
                        model_override="hf.co/google/gemma-3-27b-it-qat-q4_0-gguf",
                    )
                ],
                default_target="local-proxy",
            ),
            registry=ModelCapabilityRegistry(
                capabilities=[
                    ModelCapability(
                        model_pattern="nemotron-*",
                        target="local-proxy",
                        provider="ollama",
                    )
                ]
            ),
        )
        proxy = start_proxy_server(app)

        try:
            request = Request(
                url=f"{proxy.base_url}/v1/chat/completions",
                method="POST",
                data=json.dumps(
                    {
                        "model": "nemotron-3-super:120b",
                        "messages": [
                            {
                                "role": "user",
                                "content": "What medication interactions should be considered?",
                            }
                        ],
                    }
                ).encode("utf-8"),
                headers={"content-type": "application/json"},
            )
            with urlopen(request) as response:
                payload = json.loads(response.read())
                route_header = response.headers.get("x-nemoclaw-target")
                model_header = response.headers.get("x-nemoclaw-model")
        finally:
            proxy.shutdown()
            upstream_server.shutdown()
            upstream_thread.join(timeout=5)

        assert route_header == "medgemma-mac"
        assert model_header == "hf.co/google/gemma-3-27b-it-qat-q4_0-gguf"
        assert payload["received_model"] == "hf.co/google/gemma-3-27b-it-qat-q4_0-gguf"

    def test_proxy_returns_conflict_for_direct_agent_route(self) -> None:
        app = OpenAIRouterProxy(
            policy=RoutingPolicy(
                targets={
                    "gemini-direct": RoutingTarget(
                        name="gemini-direct",
                        kind="agent",
                        agent="gemini",
                    )
                },
                default_target="gemini-direct",
            ),
            registry=ModelCapabilityRegistry(
                capabilities=[
                    ModelCapability(
                        model_pattern="gemini-*",
                        target="gemini-direct",
                        provider="google",
                        bypasses_inference_local=True,
                    )
                ]
            ),
        )

        response = app.handle_http_request(
            method="POST",
            path="/v1/chat/completions",
            headers={"content-type": "application/json"},
            body=json.dumps({"model": "gemini-2.5-pro", "messages": []}).encode("utf-8"),
        )

        payload = json.loads(response.body)
        assert response.status_code == 409
        assert payload["target"] == "gemini-direct"
