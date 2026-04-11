"""Deterministic routing policy and model capability registry."""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, Field, computed_field

HEALTHCARE_TEXT_KEYWORDS: tuple[str, ...] = (
    "health",
    "healthcare",
    "medical",
    "medicine",
    "medication",
    "diagnosis",
    "diagnostic",
    "symptom",
    "symptoms",
    "treatment",
    "clinical",
    "patient",
    "doctor",
    "hospital",
)


class RoutingTarget(BaseModel):
    """A routing destination resolved by policy."""

    name: str
    kind: Literal["proxy_upstream", "agent"]
    upstream_url: str | None = None
    agent: str | None = None
    provider: str | None = None
    description: str | None = None

    @computed_field  # type: ignore[misc]
    @property
    def proxy_compatible(self) -> bool:
        return self.kind == "proxy_upstream"


class ModelCapability(BaseModel):
    """Capabilities associated with a model pattern."""

    model_pattern: str
    target: str
    provider: str
    modalities: list[str] = Field(default_factory=lambda: ["text"])
    notes: str | None = None
    bypasses_inference_local: bool = False
    resolved_model: str | None = None

    def matches(self, model: str) -> bool:
        return fnmatch.fnmatch(model, self.model_pattern)


class RoutingRule(BaseModel):
    """Deterministic rule applied before default capability lookup."""

    name: str
    target: str
    priority: int = 100
    model_patterns: list[str] = Field(default_factory=list)
    channel: str | None = None
    required_modalities: list[str] = Field(default_factory=list)
    header_equals: dict[str, str] = Field(default_factory=dict)
    metadata_keywords_any: list[str] = Field(default_factory=list)
    model_override: str | None = None

    def matches(self, request: RoutingRequest) -> bool:
        if self.channel is not None and request.channel != self.channel:
            return False
        if self.model_patterns and not any(
            fnmatch.fnmatch(request.model, pattern) for pattern in self.model_patterns
        ):
            return False
        if self.required_modalities and not set(self.required_modalities).issubset(
            set(request.modalities)
        ):
            return False
        for key, expected in self.header_equals.items():
            if request.headers.get(key.lower()) != expected:
                return False
        if self.metadata_keywords_any:
            metadata_text = _collect_metadata_text(request.metadata)
            if not any(keyword.lower() in metadata_text for keyword in self.metadata_keywords_any):
                return False
        return True


class RoutingRequest(BaseModel):
    """Normalized routing input derived from a model request."""

    path: str
    model: str
    channel: str | None = None
    modalities: list[str] = Field(default_factory=lambda: ["text"])
    headers: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, object] = Field(default_factory=dict)


class RoutingDecision(BaseModel):
    """Final routing decision resolved by policy."""

    target: RoutingTarget
    reason: str
    rule_name: str | None = None
    capability: ModelCapability | None = None
    resolved_model: str


class ModelCapabilityRegistry(BaseModel):
    """Registry of models and their routing-relevant capabilities."""

    capabilities: list[ModelCapability] = Field(default_factory=list)

    def resolve(self, model: str) -> ModelCapability | None:
        for capability in self.capabilities:
            if capability.matches(model):
                return capability
        return None

    def models_payload(self) -> dict[str, object]:
        return {
            "object": "list",
            "data": [
                {
                    "id": capability.model_pattern,
                    "object": "model",
                    "owned_by": capability.provider,
                    "modalities": capability.modalities,
                    "bypasses_inference_local": capability.bypasses_inference_local,
                }
                for capability in self.capabilities
            ],
        }


class RoutingPolicy(BaseModel):
    """Deterministic routing rules plus named targets."""

    targets: dict[str, RoutingTarget]
    rules: list[RoutingRule] = Field(default_factory=list)
    default_target: str

    def route(
        self,
        request: RoutingRequest,
        registry: ModelCapabilityRegistry,
    ) -> RoutingDecision:
        forced_target = request.headers.get("x-nemoclaw-route")
        if forced_target is not None:
            return RoutingDecision(
                target=self.targets[forced_target],
                reason="header override",
                rule_name="x-nemoclaw-route",
                resolved_model=request.model,
            )

        for rule in sorted(self.rules, key=lambda item: item.priority, reverse=True):
            if rule.matches(request):
                return RoutingDecision(
                    target=self.targets[rule.target],
                    reason="matched deterministic rule",
                    rule_name=rule.name,
                    resolved_model=rule.model_override or request.model,
                )

        capability = registry.resolve(request.model)
        if capability is not None:
            return RoutingDecision(
                target=self.targets[capability.target],
                reason="matched model capability",
                capability=capability,
                resolved_model=capability.resolved_model or request.model,
            )

        return RoutingDecision(
            target=self.targets[self.default_target],
            reason="default target",
            resolved_model=request.model,
        )


def infer_modalities(payload: dict[str, object]) -> list[str]:
    """Infer request modalities from OpenAI-compatible request bodies."""
    modalities = {"text"}

    def _scan_content_item(item) -> None:
        if not isinstance(item, dict):
            return
        item_type = str(item.get("type", "")).lower()
        if "image" in item_type:
            modalities.add("image")
        if "audio" in item_type:
            modalities.add("audio")
        if "video" in item_type:
            modalities.add("video")

    messages = payload.get("messages")
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, list):
                for item in content:
                    _scan_content_item(item)

    input_items = payload.get("input")
    if isinstance(input_items, list):
        for item in input_items:
            _scan_content_item(item)

    return sorted(modalities)


def infer_prompt_text(payload: dict[str, object]) -> str:
    """Best-effort extraction of user-visible text from an OpenAI-style payload."""
    segments: list[str] = []

    messages = payload.get("messages")
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, str):
                segments.append(content)
                continue
            if isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    item_type = str(item.get("type", "")).lower()
                    if "text" not in item_type:
                        continue
                    text = item.get("text")
                    if isinstance(text, str):
                        segments.append(text)

    input_items = payload.get("input")
    if isinstance(input_items, list):
        for item in input_items:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type", "")).lower()
            if "text" not in item_type:
                continue
            text = item.get("text")
            if isinstance(text, str):
                segments.append(text)

    prompt = payload.get("prompt")
    if isinstance(prompt, str):
        segments.append(prompt)

    return "\n".join(segment.strip() for segment in segments if segment and segment.strip())


def build_default_routing_policy(
    *,
    local_proxy_upstream_url: str,
    medgemma_upstream_url: str,
    medgemma_model: str = "hf.co/google/gemma-3-27b-it-qat-q4_0-gguf",
) -> RoutingPolicy:
    """Return the default host-side routing policy shipped with NemoClaw."""
    return RoutingPolicy(
        targets={
            "local-proxy": RoutingTarget(
                name="local-proxy",
                kind="proxy_upstream",
                upstream_url=local_proxy_upstream_url,
                provider="openshell",
                description="Default private local route via OpenShell.",
            ),
            "medgemma-mac": RoutingTarget(
                name="medgemma-mac",
                kind="proxy_upstream",
                upstream_url=medgemma_upstream_url,
                provider="medgemma-mac",
                description="Mac Studio-hosted MedGemma specialist route for healthcare text.",
            ),
        },
        rules=[
            RoutingRule(
                name="healthcare-text-to-medgemma",
                target="medgemma-mac",
                priority=180,
                metadata_keywords_any=list(HEALTHCARE_TEXT_KEYWORDS),
                model_override=medgemma_model,
            )
        ],
        default_target="local-proxy",
    )


def build_default_capability_registry() -> ModelCapabilityRegistry:
    """Return the baseline capability registry shipped with NemoClaw."""
    return ModelCapabilityRegistry(
        capabilities=[
            ModelCapability(
                model_pattern="nemotron-*",
                target="local-proxy",
                provider="ollama",
                modalities=["text"],
            ),
            ModelCapability(
                model_pattern="gemma4:*",
                target="local-proxy",
                provider="mac-ollama",
                modalities=["text"],
            ),
            ModelCapability(
                model_pattern="hf.co/google/gemma-3-27b-it-qat-q4_0-gguf",
                target="medgemma-mac",
                provider="medgemma-mac",
                modalities=["text"],
                resolved_model="hf.co/google/gemma-3-27b-it-qat-q4_0-gguf",
            ),
        ]
    )


def _collect_metadata_text(metadata: dict[str, object]) -> str:
    """Flatten metadata values into a lowercase text blob for keyword rules."""
    segments: list[str] = []

    def _visit(value: object) -> None:
        if isinstance(value, str):
            segments.append(value.lower())
            return
        if isinstance(value, dict):
            for nested in value.values():
                _visit(nested)
            return
        if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
            for nested in value:
                _visit(nested)

    _visit(metadata)
    return "\n".join(segments)
