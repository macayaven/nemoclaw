"""Routing policy and capability registry tests."""

from __future__ import annotations

import pytest

from orchestrator.routing import (
    HEALTHCARE_TEXT_KEYWORDS,
    ModelCapability,
    ModelCapabilityRegistry,
    RoutingPolicy,
    RoutingRequest,
    RoutingRule,
    RoutingTarget,
    build_default_capability_registry,
    build_default_routing_policy,
    infer_modalities,
    infer_prompt_text,
)

pytestmark = pytest.mark.phase6


def _make_policy() -> tuple[RoutingPolicy, ModelCapabilityRegistry]:
    policy = RoutingPolicy(
        targets={
            "local-proxy": RoutingTarget(
                name="local-proxy",
                kind="proxy_upstream",
                upstream_url="http://127.0.0.1:18081",
                provider="openshell",
            ),
            "gemini-direct": RoutingTarget(
                name="gemini-direct",
                kind="agent",
                agent="gemini",
                provider="google",
            ),
            "medgemma-mac": RoutingTarget(
                name="medgemma-mac",
                kind="proxy_upstream",
                upstream_url="http://mac-studio.local:11435/v1",
                provider="medgemma-mac",
            ),
        },
        rules=[
            RoutingRule(
                name="whatsapp-images-to-gemini",
                target="gemini-direct",
                priority=200,
                channel="whatsapp",
                required_modalities=["image"],
            ),
            RoutingRule(
                name="healthcare-text-to-medgemma",
                target="medgemma-mac",
                priority=180,
                metadata_keywords_any=list(HEALTHCARE_TEXT_KEYWORDS),
                model_override="hf.co/google/gemma-3-27b-it-qat-q4_0-gguf",
            ),
        ],
        default_target="local-proxy",
    )
    registry = ModelCapabilityRegistry(
        capabilities=[
            ModelCapability(
                model_pattern="nemotron-*",
                target="local-proxy",
                provider="ollama",
                modalities=["text"],
            ),
            ModelCapability(
                model_pattern="gemini-*",
                target="gemini-direct",
                provider="google",
                modalities=["text", "image", "audio"],
                bypasses_inference_local=True,
            ),
        ]
    )
    return policy, registry


class TestRoutingPolicy:
    def test_deterministic_rule_wins_over_registry(self) -> None:
        policy, registry = _make_policy()

        decision = policy.route(
            RoutingRequest(
                path="/v1/chat/completions",
                model="nemotron-3-super:120b",
                channel="whatsapp",
                modalities=["image", "text"],
                headers={},
            ),
            registry,
        )

        assert decision.target.name == "gemini-direct"
        assert decision.rule_name == "whatsapp-images-to-gemini"

    def test_capability_registry_routes_direct_models(self) -> None:
        policy, registry = _make_policy()

        decision = policy.route(
            RoutingRequest(
                path="/v1/chat/completions",
                model="gemini-2.5-pro",
                modalities=["text"],
                headers={},
            ),
            registry,
        )

        assert decision.target.name == "gemini-direct"
        assert decision.capability is not None
        assert decision.capability.bypasses_inference_local is True

    def test_header_override_forces_target(self) -> None:
        policy, registry = _make_policy()

        decision = policy.route(
            RoutingRequest(
                path="/v1/chat/completions",
                model="gemini-2.5-pro",
                modalities=["text"],
                headers={"x-nemoclaw-route": "local-proxy"},
            ),
            registry,
        )

        assert decision.target.name == "local-proxy"
        assert decision.rule_name == "x-nemoclaw-route"
        assert decision.resolved_model == "gemini-2.5-pro"

    def test_healthcare_keyword_routes_to_medgemma_with_model_override(self) -> None:
        policy, registry = _make_policy()

        decision = policy.route(
            RoutingRequest(
                path="/v1/chat/completions",
                model="nemotron-3-super:120b",
                modalities=["text"],
                headers={},
                metadata={"text": "Please explain the likely medication side effects."},
            ),
            registry,
        )

        assert decision.target.name == "medgemma-mac"
        assert decision.rule_name == "healthcare-text-to-medgemma"
        assert decision.resolved_model == "hf.co/google/gemma-3-27b-it-qat-q4_0-gguf"

    def test_infer_modalities_detects_image_content(self) -> None:
        payload = {
            "model": "gemini-2.5-pro",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "describe this"},
                        {"type": "input_image", "image_url": {"url": "https://example/image.png"}},
                    ],
                }
            ],
        }

        assert infer_modalities(payload) == ["image", "text"]

    def test_infer_prompt_text_extracts_text_segments(self) -> None:
        payload = {
            "model": "nemotron-3-super:120b",
            "messages": [
                {"role": "system", "content": "You are a careful assistant."},
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "Summarize this clinical note."},
                        {"type": "input_image", "image_url": {"url": "https://example/image.png"}},
                    ],
                },
            ],
        }

        extracted = infer_prompt_text(payload)

        assert "careful assistant" in extracted
        assert "clinical note" in extracted

    def test_default_builders_include_medgemma_route(self) -> None:
        policy = build_default_routing_policy(
            local_proxy_upstream_url="http://127.0.0.1:18081",
            medgemma_upstream_url="http://mac-studio.local:11435/v1",
        )
        registry = build_default_capability_registry()

        assert "medgemma-mac" in policy.targets
        assert policy.targets["medgemma-mac"].upstream_url == "http://mac-studio.local:11435/v1"
        assert any(
            capability.model_pattern == "hf.co/google/gemma-3-27b-it-qat-q4_0-gguf"
            for capability in registry.capabilities
        )
