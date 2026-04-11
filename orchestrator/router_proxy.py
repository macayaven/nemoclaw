"""Host-side OpenAI-compatible router proxy."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from pydantic import BaseModel

from orchestrator.routing import (
    ModelCapabilityRegistry,
    RoutingDecision,
    RoutingPolicy,
    RoutingRequest,
    infer_modalities,
    infer_prompt_text,
)


class ProxyResponse(BaseModel):
    """HTTP response produced by the router proxy."""

    status_code: int
    headers: dict[str, str]
    body: bytes


class ForwardError(RuntimeError):
    """Raised when forwarding to an upstream fails."""


class OpenAIRouterProxy:
    """Route OpenAI-compatible requests to host-side upstreams."""

    def __init__(
        self,
        policy: RoutingPolicy,
        registry: ModelCapabilityRegistry,
    ) -> None:
        self.policy = policy
        self.registry = registry

    def handle_http_request(
        self,
        *,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes = b"",
    ) -> ProxyResponse:
        normalized_headers = {key.lower(): value for key, value in headers.items()}
        route_path = urlsplit(path).path

        if method == "GET" and route_path == "/healthz":
            return ProxyResponse(
                status_code=200,
                headers={"content-type": "application/json"},
                body=json.dumps({"status": "ok"}).encode("utf-8"),
            )

        if method == "GET" and route_path == "/v1/models":
            return ProxyResponse(
                status_code=200,
                headers={"content-type": "application/json"},
                body=json.dumps(self.registry.models_payload()).encode("utf-8"),
            )

        if method != "POST":
            return ProxyResponse(
                status_code=405,
                headers={"content-type": "application/json"},
                body=json.dumps({"error": "method not allowed"}).encode("utf-8"),
            )

        payload = json.loads(body.decode("utf-8"))
        model = str(payload.get("model", "")).strip()
        if not model:
            return ProxyResponse(
                status_code=400,
                headers={"content-type": "application/json"},
                body=json.dumps({"error": "request is missing model"}).encode("utf-8"),
            )

        decision = self.policy.route(
            RoutingRequest(
                path=route_path,
                model=model,
                channel=normalized_headers.get("x-nemoclaw-channel"),
                modalities=infer_modalities(payload),
                headers=normalized_headers,
                metadata={"text": infer_prompt_text(payload)},
            ),
            self.registry,
        )
        if not decision.target.proxy_compatible:
            return ProxyResponse(
                status_code=409,
                headers={
                    "content-type": "application/json",
                    "x-nemoclaw-target": decision.target.name,
                    "x-nemoclaw-rule": decision.rule_name or "",
                },
                body=json.dumps(
                    {
                        "error": (
                            "resolved route requires direct agent/provider execution and "
                            "cannot be served via the inference proxy"
                        ),
                        "target": decision.target.name,
                        "reason": decision.reason,
                    }
                ).encode("utf-8"),
            )

        try:
            if decision.resolved_model != model:
                payload["model"] = decision.resolved_model
                body = json.dumps(payload).encode("utf-8")
            upstream = self._forward(decision, route_path, normalized_headers, body)
        except ForwardError as exc:
            return ProxyResponse(
                status_code=502,
                headers={"content-type": "application/json"},
                body=json.dumps({"error": str(exc)}).encode("utf-8"),
            )

        upstream.headers["x-nemoclaw-target"] = decision.target.name
        if decision.rule_name is not None:
            upstream.headers["x-nemoclaw-rule"] = decision.rule_name
        upstream.headers["x-nemoclaw-reason"] = decision.reason
        upstream.headers["x-nemoclaw-model"] = decision.resolved_model
        return upstream

    def _forward(
        self,
        decision: RoutingDecision,
        path: str,
        headers: dict[str, str],
        body: bytes,
    ) -> ProxyResponse:
        upstream_base = decision.target.upstream_url
        if upstream_base is None:
            raise ForwardError(f"Target {decision.target.name!r} is missing upstream_url.")

        url = upstream_base.rstrip("/") + path
        request = urllib.request.Request(url=url, data=body, method="POST")
        request.add_header("content-type", headers.get("content-type", "application/json"))
        for passthrough in ("accept", "authorization", "x-request-id"):
            if passthrough in headers:
                request.add_header(passthrough, headers[passthrough])

        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                response_body = response.read()
                response_headers = {
                    "content-type": response.headers.get("content-type", "application/json")
                }
                return ProxyResponse(
                    status_code=response.status,
                    headers=response_headers,
                    body=response_body,
                )
        except urllib.error.HTTPError as exc:
            return ProxyResponse(
                status_code=exc.code,
                headers={"content-type": exc.headers.get("content-type", "application/json")},
                body=exc.read(),
            )
        except urllib.error.URLError as exc:
            raise ForwardError(f"failed to reach upstream {url}: {exc.reason}") from exc


@dataclass
class RunningProxyServer:
    """Convenience wrapper for a threaded local proxy server."""

    app: OpenAIRouterProxy
    server: ThreadingHTTPServer
    thread: threading.Thread

    @property
    def base_url(self) -> str:
        host, port = _server_host_port(self.server.server_address)
        return f"http://{host}:{port}"

    def shutdown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)


def start_proxy_server(
    app: OpenAIRouterProxy,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
) -> RunningProxyServer:
    """Start the router proxy in a background thread."""

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
            response = app.handle_http_request(
                method=self.command,
                path=self.path,
                headers={key: value for key, value in self.headers.items()},
                body=body,
            )
            self.send_response(response.status_code)
            for key, value in response.headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(response.body)

    server = ThreadingHTTPServer((host, port), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return RunningProxyServer(app=app, server=server, thread=thread)


def _server_host_port(server_address: object) -> tuple[str, int]:
    """Extract the host and port from an IPv4 or IPv6 server address tuple."""
    if not isinstance(server_address, tuple) or len(server_address) < 2:
        raise RuntimeError(f"unexpected server address: {server_address!r}")

    host = str(server_address[0])
    port = int(server_address[1])
    return host, port
