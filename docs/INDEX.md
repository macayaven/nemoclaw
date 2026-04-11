# NemoClaw Documentation Index

*DGX Spark (GB10 Blackwell, 128 GB) + Mac Studio (M4 Max, 128 GB)*
*Last updated: 2026-04-11*

---

This is the master index for all NemoClaw documentation. Use it to find the right document for your task.

## Quick Navigation

| I want to... | Read this |
|--------------|-----------|
| Understand what NemoClaw is and how it works | [Architecture Guide](#architecture-and-concepts) |
| Deploy NemoClaw from scratch | [Deployment Guide](#deployment-and-operations) |
| Do something day-to-day (start, stop, switch models) | [Operations Guide](#deployment-and-operations) |
| Find a copy-paste recipe for a specific task | [Cookbook](#deployment-and-operations) |
| Understand the security model | [Trust Model + Security](#security) |
| Set up agents working together | [Inter-Agent Guide](#multi-agent-orchestration) |
| Know what to improve next | [Evolution Roadmap](#roadmap) |
| Use NVIDIA agent skills in my coding assistant | [Agent Skills Guide](#agent-skills) |
| Run or write tests | [E2E Test Guide](#testing) |
| Troubleshoot a problem | [Runbook](#deployment-and-operations) |
| See performance numbers | [Benchmarks](#performance) |

---

## Architecture and Concepts

| Document | Description | Audience |
|----------|-------------|----------|
| [nemoclaw-architecture.md](../nemoclaw-architecture.md) | Complete architectural guide: three-layer model (sandboxes, guardrails, inference router), component deep dive, hardware mapping, request flow diagrams, and system constraints. **Start here for understanding the system.** | Everyone |
| [openclaw-concepts.md](openclaw-concepts.md) | Mental model for OpenClaw concepts: agents, memory, skills, tools, MCP, model configuration, and how they map to the NemoClaw deployment. | Everyone |
| [personal-assistant-architecture.md](personal-assistant-architecture.md) | Current live design for the personal-assistant deployment: WhatsApp ingress, Tailscale Serve operator path, Spark reasoning topology, Mac TTS role, and the live tool/skill posture. Includes a Mermaid topology diagram. | Everyone |

---

## Deployment and Operations

| Document | Description | Audience |
|----------|-------------|----------|
| [deployment-guide.md](deployment-guide.md) | Step-by-step deployment from zero: Spark setup, Mac setup, provider registration, sandbox creation, agent configuration. Organized as phases (0-4) matching the test suite. | First-time setup |
| [operations-guide.md](operations-guide.md) | Day-to-day operations: start, stop, pause, restart, monitor, update. Covers full stack, individual sandboxes, and model management. | Daily use |
| [cookbook.md](cookbook.md) | 45+ copy-paste recipes organized by category: providers/models, skills, policies, inter-agent, orchestrator, security, per-sandbox model management. Each recipe has CLI commands, Web UI locations, and verification steps. | Daily use |
| [runbook.md](runbook.md) | Comprehensive runbook: system overview, architecture diagrams, deploy-from-scratch, stop/pause/resume, clean removal, special care items, available tools, recommended workflows, and future improvements. Includes all API endpoints and access methods. | Ops reference |
| [use-cases.md](use-cases.md) | 8 guided use cases: chat with Nemotron, switch models, use Claude Code/Codex/Gemini in sandboxes, run multiple agents, approve network requests, access from phone. | Getting started |
| [agent-auth-vault.md](agent-auth-vault.md) | Local `pass`-backed vault pattern for Claude Code, Codex, Gemini CLI, and OpenCode subscription auth blobs, plus sandbox materialization flow. | Security + ops |
| [personal-assistant-profile.md](personal-assistant-profile.md) | Recommended OpenClaw posture for the main personal assistant: WhatsApp as primary ingress, web UI as operator channel, constrained tool surface, and role separation from the coding sandboxes. | Assistant configuration |
| [kokoro-deployment.md](kokoro-deployment.md) | Concrete deployment guidance for Mac-hosted Kokoro TTS: native macOS service, `launchd`, bind strategy, smoke tests, Docker-on-Mac access, and Spark reachability model. | Operations design |

---

## Security

| Document | Description | Audience |
|----------|-------------|----------|
| [multi-machine-trust-model.md](multi-machine-trust-model.md) | Cross-machine trust analysis: what Tailscale provides, what OpenShell provides, what is NOT enforced (unauthenticated Ollama, no per-sandbox provider ACLs, no rate limiting). Includes risk matrix and mitigations checklist. | Security review |

---

## Multi-Agent Orchestration

| Document | Description | Audience |
|----------|-------------|----------|
| [inter-agent-guide.md](inter-agent-guide.md) | Three patterns for agent cooperation: Shared MCP Servers, Orchestrator Agent, Shared Filesystem. Implementation details, security considerations, and a phased roadmap. | Advanced use |

---

## Agent Skills

| Document | Description | Audience |
|----------|-------------|----------|
| [agent-skills.md](agent-skills.md) | NVIDIA agent skills for coding assistants: what skills are, full catalog of 10 user skills + 1 guide, installation method, and detailed configuration for Claude Code, Codex CLI, and Gemini CLI. Includes skill file format, discovery mechanisms, and per-assistant comparison table. | All users |

Skills are installed at `.agents/skills/` in the project root. Claude Code discovers them automatically via `.claude/skills` symlink. Codex and Gemini require MCP filesystem server configuration — see the guide for details.

---

## Performance

| Document | Description | Audience |
|----------|-------------|----------|
| [benchmarks.md](benchmarks.md) | Measured inference latencies: Nemotron 120B (Spark direct, ~2.5s warm / 18 tok/s), Gemma 4 27B (Mac via Tailscale, ~0.9s warm / 52 tok/s), sandbox overhead analysis (~800ms), GPU memory usage. | Performance tuning |

---

## Testing

| Document | Description | Audience |
|----------|-------------|----------|
| [e2e-test-guide.md](e2e-test-guide.md) | End-to-end test guide: test phases (0-6), what each phase validates, how to run tests, expected results. | Development |

Test phases map to deployment stages:

| Phase | What it validates | Test count |
|-------|-------------------|------------|
| Phase 0 — Preflight | Prerequisites on Spark + Mac | ~15 |
| Phase 1 — Core | Gateway, Ollama, providers, inference routing, sandboxes | ~25 |
| Phase 2 — Mac | Mac Ollama, provider switching, cross-machine inference | ~17 |
| Phase 3 — Pi (legacy/optional) | Tailscale routing, DNS, LiteLLM proxy, monitoring | ~20 |
| Phase 4 — Agents | Sandbox isolation, hardening, network policy, SSRF, secrets | ~30 |
| Phase 5 — Mobile | Tailscale gateway, remote access | ~10 |
| Phase 6 — Orchestrator | Orchestrator, shared workspace, sandbox bridge, task manager | ~25 |

---

## Roadmap

| Document | Description | Audience |
|----------|-------------|----------|
| [evolution-roadmap.md](evolution-roadmap.md) | Comprehensive analysis of 22 improvements needed to evolve NemoClaw from prototype to production. Starts with an alignment review against official NVIDIA agent skills. Covers: intelligent per-query routing, multi-channel/multimodal support, per-sandbox model independence, observability and cost tracking, security hardening, resilience and failover, orchestrator intelligence, conversation memory, authentication, operational maturity, default agent skills, WhatsApp channel integration, and a personalized knowledge base with Obsidian Smart Connections. Includes priority matrix (P0-P-Future) and 5-phase implementation plan (A-E). | Architecture planning |
| [reviews/second-brain-spec.md](reviews/second-brain-spec.md) | Implementation-oriented specification for the local-first "second brain": canonical vault ownership, single-writer model, journaled writes, disk-backed retrieval index, MCP tool surface, phased rollout, and recommended Spark/Mac topology. | Architecture planning |
| [reviews/inference-topology-plan.md](reviews/inference-topology-plan.md) | Target deployment plan for the next model topology: Spark as reasoning and retrieval node (`nemotron`, `gemma4:31b`, embeddings, reranker) and Mac Studio as operator-facing modality node with future TTS. Includes migration ordering and why current ops docs/tests should not be rewritten prematurely. | Architecture planning |
| [reviews/spark-gemma4-migration-checklist.md](reviews/spark-gemma4-migration-checklist.md) | Operational checklist for validating and migrating Spark-hosted `gemma4:31b` into the live topology without prematurely rewriting the current ops docs and test contract. | Architecture planning |
| [reviews/mac-tts-deployment-plan.md](reviews/mac-tts-deployment-plan.md) | Recommended deployment pattern for Mac-hosted TTS: native macOS service, correct `launchd` placement, bind strategy, Docker-on-Mac client access, Spark reachability model, and smoke tests. | Architecture planning |

### Roadmap Priority Summary

| Priority | Items | Theme |
|----------|-------|-------|
| **P0** | Intelligent routing, per-sandbox models, security mitigations, default agent skills | Foundation |
| **P1** | Provider failover, cold start, Mac sleep, metrics, health checks | Reliability |
| **P2** | WhatsApp integration, multimodal preprocessing, orchestrator auto-routing | Intelligence + Channels |
| **P3/P4** | Dashboard, backup/restore, adaptive pipelines, multi-user | Polish |
| **P-Future** | Obsidian personalized knowledge base with Smart Connections | Personalization |

---

## Other Project Files

| File | Location | Description |
|------|----------|-------------|
| `AGENTS.md` | Project root | Agent instructions for coding assistants: project overview, architecture map, quick reference commands, code style conventions, and skill catalog. Read by Cursor and other agents that look for `AGENTS.md`. |
| `README.md` | Project root | Project introduction and quick start. |
| `Makefile` | Project root | Build, test, and operations shortcuts. |
| `nemoclaw-tdd-plan.md` | Project root | TDD plan for the test suite development. |
| `TRAINING-LOOP-BLOCKER.md` | Project root | Tracks blockers for the training loop implementation. |

---

## Document Relationships

```
                    ┌─────────────────────────┐
                    │      INDEX.md           │
                    │     (this file)          │
                    └───────────┬─────────────┘
                                │
          ┌─────────────────────┼─────────────────────┐
          │                     │                     │
          ▼                     ▼                     ▼
  ┌───────────────┐   ┌─────────────────┐   ┌──────────────────┐
  │  Architecture │   │   Operations    │   │    Roadmap       │
  │               │   │                 │   │                  │
  │ architecture  │   │ deployment-guide│   │ evolution-roadmap│
  │ concepts      │   │ operations-guide│   │                  │
  │               │   │ cookbook         │   │  (builds on all  │
  │               │   │ runbook         │   │   other docs)    │
  │               │   │ use-cases       │   │                  │
  └───────┬───────┘   └────────┬────────┘   └──────────────────┘
          │                    │
          ▼                    ▼
  ┌───────────────┐   ┌─────────────────┐
  │   Security    │   │  Multi-Agent    │
  │               │   │                 │
  │ trust-model   │   │ inter-agent     │
  │               │   │ guide           │
  └───────────────┘   └────────┬────────┘
                               │
                               ▼
                      ┌─────────────────┐
                      │  Agent Skills   │
                      │                 │
                      │ agent-skills    │
                      │ .agents/skills/ │
                      │ AGENTS.md       │
                      └─────────────────┘
```

---

## How to Read This Documentation

**If you're new to NemoClaw:**
1. Start with `nemoclaw-architecture.md` — understand the three-layer model
2. Read `use-cases.md` — see what you can do with it
3. Follow `deployment-guide.md` — set it up
4. Keep `cookbook.md` and `operations-guide.md` open for daily use

**If you're evaluating the architecture:**
1. `nemoclaw-architecture.md` for the design
2. `multi-machine-trust-model.md` for security analysis
3. `benchmarks.md` for performance data
4. `evolution-roadmap.md` for known gaps and improvement plan

**If you're developing or extending:**
1. `AGENTS.md` at the project root for codebase structure
2. `inter-agent-guide.md` for orchestration patterns
3. `e2e-test-guide.md` for the test harness
4. `agent-skills.md` for using NVIDIA skills in your coding assistant
5. `evolution-roadmap.md` for the implementation backlog
