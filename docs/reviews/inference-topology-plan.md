# Inference Topology Plan

Status: target architecture and migration plan

This document captures the recommended next-step model topology for NemoClaw.
It is intentionally separate from the current deployment guides because the
live system and the Phase 2 test suite still reflect the existing Mac-backed
secondary inference setup.

## 1. Target Topology

### DGX Spark

The Spark becomes the main intelligence and retrieval node.

Models and responsibilities:

- `nemotron-3-super:120b`
  - default general reasoning route
  - heavy synthesis, planning, broad assistant tasks
- `gemma4:31b`
  - secondary strong local model
  - coding, long-context, structured analysis, retrieval-backed answers
- `nomic-embed-text`
  - embeddings for the second brain
- `qllama/bge-reranker-v2-m3:latest`
  - reranking for retrieval quality

Services on Spark:

- OpenShell gateway
- NemoClaw sandboxes
- orchestrator
- host-side router proxy
- second-brain retrieval service
- indexing and reranking workers

### Mac Studio

The Mac becomes the operator-facing and modality node.

Responsibilities:

- operator-facing Obsidian vault
- optional Smart Connections for human use only
- future TTS service
- optional future specialist modality services

The Mac should stop being treated as the main place for a secondary fast chat
LLM in the long-term design.

## 2. Why This Split

This split is better than keeping a general-purpose secondary LLM on the Mac:

- it consolidates reasoning and retrieval on the Spark
- it avoids spreading core knowledge-service logic across machines
- it uses the Mac for high-value user-facing modality features
- it reduces architectural confusion around which machine owns intelligence
  versus presentation

The retrieval-heavy second brain especially benefits from living beside the
embedding and reranking stack on the Spark.

## 3. Model Roles

### Default route

- `local-ollama / nemotron-3-super:120b`
- broad assistant behavior
- high-quality local reasoning

### Secondary Spark route

- Spark-hosted `gemma4:31b`
- coding, structured local tasks, long-context retrieval-backed answers
- lower-cost alternative to Nemotron when the task does not justify the 120B
  route

### Retrieval support models

- `nomic-embed-text`
- `qllama/bge-reranker-v2-m3:latest`

These are not user-facing chat defaults. They are infrastructure models for the
knowledge service.

### Mac modality route

Future:

- TTS service on Mac

This is the recommended next modality after the knowledge service, especially
for wearable-oriented workflows.

### Practical routing matrix

| Situation | Preferred model / service | Why |
|----------|----------------------------|-----|
| Broad assistant reasoning, planning, synthesis | `nemotron-3-super:120b` on Spark | Best default quality |
| Strong local coding, structured analysis, long-context retrieval-backed answers | `gemma4:31b` on Spark | Cheaper/faster secondary Spark route |
| Embedding notes and queries for retrieval | `nomic-embed-text` on Spark | Dedicated embedding model |
| Reranking retrieved candidates | `qllama/bge-reranker-v2-m3:latest` on Spark | Dedicated reranker |
| Speech output | future TTS service on Mac | Mac becomes modality node |

## 4. What This Means For MedGemma

MedGemma should not be the mainline plan right now.

If healthcare-specialist support becomes important later, it can be added as a
cold specialist route. But it should not drive the primary topology decisions.

## 5. Migration Plan

### Phase A

- keep current live deployment and tests unchanged
- document the target topology
- avoid rewriting current operations docs as if the migration were already done

### Phase B

- install and validate `gemma4:31b` on the Spark
- add explicit provider and routing guidance for the secondary Spark route
- verify memory and latency tradeoffs

### Phase C

- build the second-brain service in its own repository
- use Spark embeddings and reranking models
- expose retrieval through MCP or an equivalent host-side API

### Phase D

- introduce a Mac-hosted TTS service
- integrate it as a specialist modality output path

### Phase E

- once the live deployment has migrated, update:
  - deployment docs
  - runbook
  - cookbook
  - benchmarks
  - Phase 2 tests if the Mac no longer serves the same inference role

## 6. Important Constraint

Do not rewrite the existing operational docs and tests prematurely.

Right now:

- the current docs and tests still mostly assume Mac-hosted secondary inference
- the live system has not yet completed the migration to Spark-hosted Gemma 4
  31B plus Mac-hosted TTS

Changing those documents first would make the repo less accurate, not more.

The correct order is:

1. define target topology
2. migrate the live deployment
3. then update the operational/test contract
