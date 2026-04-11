<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Second Brain Specification

## Purpose

Define a local-first "second brain" for NemoClaw that:

- turns an Obsidian vault into a usable retrieval tool for agents
- stays private and auditable
- integrates cleanly through MCP instead of custom per-agent hacks
- supports future personalization, routing, and memory write-back
- can be implemented incrementally without destabilizing the current stack

This specification is intentionally stricter than the earlier roadmap language.
It treats Obsidian as the human-facing knowledge editor, not the backend
contract for the agent runtime.

## Recommendation

Build the second brain now in parallel with the model-hosting changes, but only
as a **read-only retrieval system** in v1.

Do **not** start with autonomous note mutation, Smart Connections internals, or
"the agent writes directly into my vault". That is the fastest route to a noisy,
fragile system.

Recommended order:

1. Read-only vault ingestion and indexing
2. MCP query tools with citations
3. Orchestrator integration for contextual retrieval
4. Append-only memory capture inbox
5. Single-writer synthesis and patch proposal flow
6. Optional auto-apply after strong evidence and review thresholds exist

## Design Principles

1. Obsidian is the editor, not the database contract.
2. The second brain is a service, not a plugin side effect.
3. Agents perform read-heavy operations through MCP.
4. Only one service may write canonical knowledge notes.
5. Retrieval must be incremental, disk-backed, and explainable.
6. Every answer that uses the second brain must cite its note sources.
7. Low-confidence memory updates must abstain or propose a patch, not mutate.

## System Placement

### Host roles

- **Mac Studio**
  - authoritative Obsidian vault
  - Obsidian application and any operator-side plugins
  - optional TTS service later

- **DGX Spark**
  - knowledge service
  - retrieval index
  - MCP server exposing the second brain to agents
  - ingestion/refinement workers
  - orchestrator/router integration

### Why this split

- The vault stays on the operator's machine.
- The agent-facing control plane already lives on the Spark.
- Spark already has the local retrieval primitives needed today:
  - `nomic-embed-text`
  - `qllama/bge-reranker-v2-m3:latest`
- This avoids exposing the raw vault directly to multiple sandboxes.

## Non-Goals for v1

- direct writes from sandboxes into the vault
- dependence on Smart Connections private files or undocumented APIs
- full "personal operating system" automation
- agent-generated merges without evidence and review
- coupling retrieval to a specific agent CLI

## Current Repo Seams To Reuse

- [inter-agent-guide.md](/home/carlos/workspace/nemoclaw/docs/inter-agent-guide.md)
  - shared MCP pattern
  - shared workspace pattern
- [orchestrator-runtime.md](/home/carlos/workspace/nemoclaw/docs/orchestrator-runtime.md)
  - queue, worker, router, and host-side service model
- [evolution-roadmap.md](/home/carlos/workspace/nemoclaw/docs/evolution-roadmap.md)
  - product intent for personalized knowledge
- [codex-implementation-review.md](/home/carlos/workspace/nemoclaw/docs/reviews/codex-implementation-review.md)
  - the architectural constraints that must override the roadmap

## Architecture

```text
Obsidian vault on Mac
  -> read-only sync/mirror to Spark
  -> ingestion/indexer on Spark
  -> metadata DB + vector index + lexical index
  -> knowledge service on Spark
  -> MCP server on Spark
  -> OpenClaw / Codex / Claude / Gemini sandboxes
  -> citations returned to orchestrator and agent

append-only memory capture events
  -> queue on Spark
  -> single-writer synthesis service
  -> proposed patches / canonical note updates
  -> synced back to vault on Mac
```

## Core Components

### 1. Vault Mirror

The authoritative vault stays on the Mac. The Spark consumes a **read-only
mirror**.

Requirements:

- sync over Tailscale/SSH or mounted share
- preserve paths, timestamps, and frontmatter
- exclude plugin caches and transient directories
- expose mirror health and last-sync timestamp

Suggested mirrored directories:

- notes and attachments
- exclude `.trash/`, `.obsidian/cache/`, Smart Connections internal files

The mirror is an implementation detail. Agents never see it directly.

### 2. Knowledge Service

A host-side service on the Spark that owns:

- note discovery
- markdown parsing
- chunking
- embedding
- reranking
- metadata lookup
- source citations
- append-only write inbox
- patch proposal generation

This is the stable backend contract. MCP sits in front of it.

### 3. Index Storage

Use a split storage model:

- **SQLite**
  - note metadata
  - chunk metadata
  - backlinks
  - ingestion cursors
  - write inbox events
  - evidence records
  - review queue

- **Disk-backed vector index**
  - dense retrieval for chunk embeddings
  - must support incremental updates and metadata filters

- **SQLite FTS5**
  - lexical fallback
  - exact keyword and path search

The spec intentionally does not hardcode one vector backend yet. The
implementation may choose a local embedded engine or a small local vector DB,
but it must be disk-backed and incremental.

### 4. Retrieval Pipeline

Recommended retrieval path:

1. parse query
2. detect retrieval intent and filters
3. lexical recall via FTS
4. dense recall via embeddings
5. union candidate set
6. rerank candidates
7. assemble a context bundle with citations
8. return bounded context, not raw vault dumps

Use the Spark-local models already present:

- embedding: `nomic-embed-text`
- reranker: `qllama/bge-reranker-v2-m3:latest`

### 5. MCP Server

Expose a **small semantic tool surface**, not dozens of low-level vault
operations.

Required v1 tools:

- `kb.search(query, scope?, tags?, limit?)`
- `kb.get_note(path)`
- `kb.get_context_bundle(query, people?, projects?, time_range?)`
- `kb.get_recent(hours?, tags?)`
- `kb.health()`

Required v2 tools:

- `kb.capture_fact(fact, source, confidence_hint?, tags?)`
- `kb.propose_patch(target_note, claim, evidence)`
- `kb.review_queue(limit?)`

Do not expose raw "write arbitrary file" tools through this server.

## Data Model

### Canonical entities

- note
- chunk
- attachment
- person
- project
- fact
- conversation
- event
- evidence record
- patch proposal

### Minimum metadata per chunk

- `chunk_id`
- `note_path`
- `vault_id`
- `heading_path`
- `created_at`
- `updated_at`
- `tags`
- `entity_refs`
- `time_refs`
- `source_kind`
- `content_hash`
- `embedding_version`

### Source of truth

- Markdown notes remain the human-readable truth.
- The index is derived state.
- Evidence records and write inbox events are machine-managed truth for
  mutation workflows.

## Chunking Strategy

Use markdown-aware chunking, not raw fixed-size splitting.

Rules:

- split by heading boundaries first
- preserve list/table/code block integrity
- keep backlinks and note path metadata
- store an excerpt window for citation previews
- support block-level retrieval only if incremental indexing stays cheap

Initial targets:

- chunk size around 400-900 tokens
- overlap only where heading transitions are abrupt
- one chunk should be attributable to a clear note/section

## Read Path Behavior

### `kb.search`

Returns ranked retrieval results with:

- note path
- score
- excerpt
- tags
- entity hints
- reason for ranking

### `kb.get_context_bundle`

Returns a structured bundle optimized for agent use:

- short synthesis
- top supporting excerpts
- cited source paths
- related people/projects if relevant
- freshness indicators

This should be the default tool the orchestrator calls.

## Write Path Behavior

### v1

No canonical note mutation.

Agents may only submit structured memory candidates to an append-only inbox.

### v2

Introduce a single-writer synthesizer:

- consumes inbox events
- groups by target entity/topic
- proposes merge/update actions
- requires explicit evidence
- writes patch proposals
- optionally materializes canonical note updates

All write actions must carry:

- target note or entity
- evidence list
- confidence
- rationale
- abstain option

Low-confidence actions go to review.

## Routing and Orchestrator Integration

The second brain should not be "always queried for everything".

Use it selectively when one or more of these are true:

- person-specific request
- project-specific request
- "what did we decide / discuss / prefer" request
- follow-up to previous conversation context
- request likely grounded in prior notes or history

### Orchestrator policy

1. classifier or rules determine whether retrieval is needed
2. orchestrator calls `kb.get_context_bundle`
3. returned context is injected into the downstream model prompt
4. answer includes citations or hidden provenance metadata

This is separate from model routing:

- retrieval decides **what context to fetch**
- router decides **which model/provider should answer**

## Security Model

1. Sandboxes never receive direct filesystem access to the authoritative vault.
2. MCP server exposes only bounded retrieval tools.
3. Knowledge service runs outside sandboxes.
4. Write path is append-only first.
5. One service owns canonical note updates.
6. All write actions are auditable.
7. Retrieval responses must be size-bounded to reduce accidental exfiltration.

## Health Checks

Expose:

- mirror freshness
- index freshness
- embedding model availability
- reranker availability
- DB integrity
- chunk count and last successful ingestion
- write inbox backlog
- review queue backlog

## Evaluation

### Offline eval set

Create a small gold dataset of:

- personal preference questions
- project-context questions
- factual recall questions
- "should abstain" questions
- stale/contradictory fact questions

Score:

- retrieval precision@k
- citation correctness
- hallucination rate
- stale fact rate
- write proposal precision

### Test layers

1. parser/chunker tests
2. index update tests
3. retrieval ranking tests
4. MCP contract tests
5. fake orchestrator integration tests
6. one or two live smoke tests against a real vault mirror

## Incremental Delivery Plan

### Phase SB-0: Specification and boundaries

- finalize architecture
- define data model
- define MCP contract
- define sync ownership

### Phase SB-1: Read-only retrieval MVP

- vault mirror
- parser/chunker
- SQLite metadata store
- vector index
- FTS index
- `kb.search`, `kb.get_note`, `kb.get_context_bundle`, `kb.health`
- local evaluation harness

### Phase SB-2: Orchestrator integration

- retrieval-needed classifier/rules
- context injection into orchestrator flows
- source citation handling
- basic telemetry

### Phase SB-3: Memory capture inbox

- structured fact capture API
- append-only event log
- review queue

### Phase SB-4: Single-writer synthesis

- evidence-backed merge/update proposals
- patch proposal files
- optional human approval flow

### Phase SB-5: Controlled write-back

- canonical note updates
- re-index contract
- backup/versioning

## Recommended Tooling

Start with these system tools/services:

- Obsidian vault on Mac
- Spark-hosted knowledge service
- SQLite metadata/event store
- disk-backed vector index
- SQLite FTS5 lexical index
- MCP server exposing semantic retrieval tools
- Spark-local embedding model: `nomic-embed-text`
- Spark-local reranker: `qllama/bge-reranker-v2-m3:latest`
- append-only write inbox and review queue

Do **not** start with:

- direct Smart Connections backend dependency
- direct sandbox writes to notes
- full autonomous memory synthesis
- image ingestion and OCR as part of the first retrieval MVP

## Definition of Done for v1

The second brain v1 is done when:

- the Spark can mirror the vault read-only
- the knowledge service can index incrementally
- agents can query it through MCP
- answers can cite retrieved note sources
- retrieval materially improves at least one real personalized workflow
- no agent has direct write access to the vault

## Best Integration Seam

The best seam is:

- **knowledge service on Spark**
- **semantic MCP server in front of it**
- **orchestrator chooses when to call it**

This is better than:

- direct Smart Connections coupling
- direct vault mounts into sandboxes
- per-agent custom Obsidian adapters

## Delegation Recommendation

Use **Gemini** first for the parallel task if the immediate deliverable is:

- architecture refinement
- research synthesis
- implementation planning
- schema and MCP contract review

Use **Claude Code** or **Codex** after that for:

- concrete Python implementation
- tests
- integration into the checked-in orchestrator/runtime

## Ready-to-Use Delegation Prompt

```text
You are implementing the NemoClaw "second brain" read-only retrieval MVP.

Read these repo files first:
- docs/second-brain-spec.md
- docs/evolution-roadmap.md
- docs/reviews/codex-implementation-review.md
- docs/inter-agent-guide.md
- docs/orchestrator-runtime.md

Goal:
Design and, if asked, scaffold a read-only personalized knowledge service for NemoClaw that uses an Obsidian vault as the human-facing knowledge base but does NOT depend on Smart Connections internals as the backend API.

Required constraints:
- authoritative vault lives on the Mac Studio
- Spark hosts the knowledge service, retrieval index, and MCP server
- agents access the second brain only through MCP
- no direct vault writes from sandboxes
- no direct dependency on private/undocumented Smart Connections internals
- retrieval must be incremental, disk-backed, and citation-friendly
- use a single-writer design for any future write path, but do not implement write-back in v1

Deliverables:
1. A concrete implementation plan for Phase SB-1
2. Proposed package/module layout in this repo
3. Data model for notes, chunks, embeddings, evidence, and retrieval results
4. MCP tool schema for:
   - kb.search
   - kb.get_note
   - kb.get_context_bundle
   - kb.get_recent
   - kb.health
5. Indexing pipeline design:
   - vault mirror
   - markdown-aware chunking
   - embeddings
   - lexical search
   - reranking
6. Test plan:
   - parser/chunker tests
   - index update tests
   - retrieval ranking tests
   - MCP contract tests
   - fake orchestrator integration tests
7. Risks, tradeoffs, and explicit non-goals

Bias toward:
- simple local-first implementation
- clean interfaces
- deterministic behavior
- evidence-backed future write paths
- reuse of existing orchestrator/runtime patterns already in the repo

Do not:
- invent a cloud dependency
- make Smart Connections the system-of-record backend
- let agents write arbitrary files into the vault
- propose a giant all-at-once implementation

If you produce code, keep it limited to Phase SB-1 scaffolding and tests only.
```
