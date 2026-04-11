# Second Brain Specification

Status: proposed implementation spec

This document defines the recommended architecture for a NemoClaw "second
brain": a local-first personal knowledge system that can be exposed to agents
as a stable tool without coupling NemoClaw to Obsidian plugin internals.

## 1. Problem Statement

NemoClaw needs a personalized knowledge layer that can:

- retrieve relevant personal and project context for a live query
- store evidence-backed facts learned during interactions
- support long-lived personal memory without turning into a note dump
- remain local-first and auditable
- integrate cleanly as an MCP-backed tool for OpenClaw, Codex, Claude, Gemini,
  OpenCode, and future agents

The existing roadmap points in the right direction, but the backend contract is
still underspecified. The main design risk is treating Obsidian Smart
Connections as the server-side API. That is the wrong abstraction. Obsidian is
an editor and operator workflow surface. NemoClaw needs its own stable
knowledge service and index.

## 2. Goals and Non-Goals

### Goals

- Local-first storage and retrieval
- Stable backend API independent of Obsidian plugin internals
- Deterministic, evidence-backed write path
- Incremental indexing
- Read-heavy retrieval exposed to agents via MCP
- Easy operator inspection using normal Markdown files
- Safe future integration with routing, personalization, wearables, and TTS

### Non-Goals

- Fully autonomous note rewriting by sandboxes
- Direct sandbox write access to the canonical vault
- Dependence on undocumented Smart Connections internal files or APIs
- Full multi-user collaboration in v1
- Re-embedding the entire vault after every write

## 3. Key Design Decisions

### 3.1 Canonical vault stays operator-friendly

The canonical knowledge base is a Markdown vault that the operator can open in
Obsidian. Obsidian remains the human interface, not the system-of-record API.

### 3.2 NemoClaw owns retrieval and indexing

NemoClaw maintains its own retrieval index and metadata store. Smart
Connections can still be used on the operator side for personal exploration,
but agents and backend jobs must rely on NemoClaw's knowledge service.

### 3.3 Single-writer model

Only one trusted host-side service writes canonical notes. Agents and ingest
pipelines do not write notes directly. They append normalized events to a
journal/queue. The materializer/refiner is the sole writer.

### 3.4 Read path and write path are separate

- Read path: low-latency search and context tools for agents
- Write path: queued candidate facts, evidence extraction, review/materialize

### 3.5 Retrieval is not "another chat model"

The second brain should be built around:

- embeddings
- reranking
- entity extraction
- evidence-aware synthesis

The answer model can be Nemotron or Gemma 4 31B, but the value comes from the
retrieval layer, not from adding another general-purpose LLM.

## 4. Recommended Topology

### Spark

Owns:

- orchestrator
- MCP servers
- retrieval index
- embedding and reranking jobs
- read API for agents
- candidate-memory journal
- evidence extraction and refinement workers

Models on Spark:

- `nemotron-3-super:120b` for default reasoning
- `gemma4:31b` for strong local coding / long-context / structured
  knowledge workflows
- `nomic-embed-text` for embeddings
- `qllama/bge-reranker-v2-m3:latest` for reranking

### Mac Studio

Owns:

- operator-facing Obsidian vault
- optional Smart Connections for human exploration only
- future TTS service

This split keeps the Spark as the intelligence and retrieval node while the Mac
stays the operator UX and modality node.

### 4.1 Practical model-role matrix

| Situation | Preferred model / service | Why |
|----------|----------------------------|-----|
| Broad assistant reasoning, planning, synthesis | `nemotron-3-super:120b` | Default high-quality answer model |
| Structured local coding and retrieval-backed synthesis | `gemma4:31b` | Secondary Spark answer model |
| Embedding notes and user queries | `nomic-embed-text` | Dedicated embedding model |
| Reranking retrieval candidates | `qllama/bge-reranker-v2-m3:latest` | Dedicated reranker |
| Future speech output | Mac-hosted TTS service | Output modality, not core reasoning |

## 5. System Architecture

```text
Obsidian vault on Mac
  -> one-way sync or mirrored checkout to Spark read/index workspace
  -> incremental parser/chunker
  -> embedding pipeline
  -> on-disk retrieval index + metadata DB
  -> MCP knowledge tools
  -> agent requests

Agent write intents / ingestion events
  -> append-only journal on Spark
  -> evidence extraction
  -> candidate memory records
  -> review/materialize worker
  -> canonical note updates by single writer
  -> incremental re-index
```

## 6. Storage Layout

Recommended host-side directories:

```text
~/workspace/knowledge/
  canonical-vault/           # mirrored markdown vault used by the service
  index/
    embeddings/
    ann/
    metadata.sqlite
  journal/
    pending/
    processed/
    dead-letter/
  snapshots/
  config/
```

If the operator wants git versioning:

```text
canonical-vault/.git
```

Do not place the ANN index or metadata DB inside the Obsidian vault itself.

## 7. Data Model

### 7.1 Canonical note categories

- `people/`
- `projects/`
- `conversations/`
- `facts/`
- `preferences/`
- `calendar/`
- `reference/`
- `daily/`
- `summaries/`

### 7.2 Metadata schema

Each note or block in the index should track:

- note path
- block id
- title
- category
- created_at
- updated_at
- source_type
- source_refs
- entity_refs
- confidence
- sensitivity
- embedding model version
- hash of canonical text

### 7.3 Candidate memory schema

Agents and ingestion jobs emit structured candidate memories, not free-form
note edits.

```json
{
  "kind": "fact_candidate",
  "subject": "carlos",
  "facet": "preference.explanation_style",
  "claim": "Carlos prefers detailed explanations with reasoning.",
  "evidence": [
    {
      "source_type": "conversation",
      "source_ref": "task-123",
      "excerpt": "Explain why, not just what."
    }
  ],
  "confidence": 0.86,
  "observed_at": "2026-04-11T10:15:00Z"
}
```

## 8. Retrieval Pipeline

### 8.1 Chunking

Chunk at the block or section level, not the whole note.

Recommended default:

- title-aware chunks
- preserve note path and heading hierarchy
- target chunk size: 300-800 tokens
- overlap only when a section is large

### 8.2 Embeddings

Use `nomic-embed-text` first. It is already present on Spark and is good enough
to ship the first retrieval slice.

Keep the embedding pipeline replaceable. The system should support swapping the
embedding model without changing the MCP API.

### 8.3 Retrieval

Recommended retrieval sequence:

1. Query preprocessing
2. Entity extraction
3. ANN recall over chunk embeddings
4. Metadata filtering
5. Rerank top-K with `bge-reranker-v2-m3`
6. Build compact evidence bundle for the answer model

### 8.4 On-disk index

Use a disk-backed index. The service must not assume that all embeddings fit in
RAM once the vault grows.

Acceptable choices:

- SQLite metadata + FAISS/HNSW index files
- SQLite metadata + LanceDB
- SQLite metadata + Qdrant local mode

Recommendation for v1:

- SQLite for metadata
- FAISS or hnswlib for ANN

That keeps the system simple and local.

## 9. MCP Tool Surface

The second brain should be exposed to agents through a narrow, stable MCP API.

### Required read tools

- `search_knowledge(query, categories?, entities?, limit?)`
- `get_note(path)`
- `get_note_section(path, heading_or_block_id)`
- `get_entity_context(entity_type, name, limit?)`
- `get_recent_context(hours?, categories?)`
- `get_related_notes(path, limit?)`

### Required write-intent tools

- `append_memory_candidate(payload)`
- `append_reference_candidate(payload)`
- `append_conversation_summary(payload)`

### Optional utility tools

- `list_categories()`
- `resolve_entity(name)`
- `get_knowledge_stats()`

Agents should never receive "write raw markdown note" tools in v1.

## 10. Write Path and Refinement

### 10.1 Journal-first ingestion

All writes go to a journal. Sources include:

- user conversations
- WhatsApp summaries
- email/calendar digests
- bookmarks/reference imports
- operator corrections

### 10.2 Materializer responsibilities

The single-writer materializer:

- validates schema
- deduplicates candidates
- merges high-confidence updates
- creates review items for low-confidence updates
- writes canonical Markdown notes
- triggers incremental re-index for changed notes/blocks

### 10.3 Evidence discipline

No update should happen without evidence. Every materialized fact needs:

- source references
- evidence excerpts or normalized source pointers
- confidence score
- last verified timestamp

### 10.4 Abstention and review

The materializer must support:

- `apply`
- `defer_for_review`
- `reject`
- `merge`
- `deprecate`

Low-confidence or contradictory updates should be deferred instead of merged.

## 11. Obsidian Integration

### What Obsidian is for

- human browsing and editing
- backlinks/graph exploration
- note authoring
- optional Smart Connections UI

### What Obsidian is not for

- backend retrieval API
- concurrency control
- write arbitration
- index ownership

### Smart Connections stance

Allowed:

- operator-side semantic exploration
- manual discovery

Not required:

- agent retrieval
- server-side ranking
- note-write workflow

## 12. Sync and Deployment Strategy

Recommended v1:

- operator keeps the primary vault on the Mac
- Spark maintains a mirrored canonical copy for indexing and serving
- sync is one-way from Mac to Spark for read/index data
- journal and review artifacts live on Spark
- approved canonical note changes are pushed back through a controlled sync or
  review workflow

This avoids direct concurrent editing between Obsidian and agent pipelines.

If operational simplicity matters more than Mac-local ownership, an alternative
is to make Spark the canonical host and open the vault remotely from the Mac.
That is simpler technically but worse for the operator experience.

## 13. Query Lifecycle

Example:

```text
User asks: "Draft a reply to Sarah about Alpha and mention the new deadline."

1. Orchestrator classifies the task as knowledge-relevant
2. Agent calls search_knowledge("Sarah Alpha new deadline")
3. Retrieval returns:
   - person note for Sarah
   - project note for Alpha
   - recent conversation summary about deadline change
4. Reranker selects strongest evidence
5. Answer model (Nemotron or Gemma 4 31B) synthesizes response
6. If the exchange reveals a new durable fact, append_memory_candidate() records it
7. Materializer later decides whether to merge that into canonical notes
```

## 14. Routing Rules for the Second Brain

The second brain should not be queried for every request.

Recommended triggers:

- people names
- project names
- references to prior conversations or decisions
- "what do I know about ..."
- drafting requests involving known contacts/projects
- requests requiring personalized preferences
- recent-context requests

Do not query the knowledge service for:

- generic coding tasks
- pure math
- straightforward factual lookups that are not personalized

## 15. Supporting Tool Stack

The second brain is not useful alone. The complete useful system should include:

### Must-have

- knowledge MCP server
- embedding worker
- reranking worker
- materializer/refiner
- email/calendar/task importers
- shared filesystem / journal inspector

### High-value next tools

- TTS service on Mac
- calendar context tool
- task/issue context tool
- bookmark/reference importer
- operator review UI for low-confidence knowledge updates

### Later

- image generation specialist
- speech-to-text / voice-note summarization
- wearable-specific interaction layer

## 16. Implementation Phases

### Phase 0: Read-only retrieval foundation

Ship first:

- vault mirror
- parser/chunker
- embedding + ANN index
- SQLite metadata DB
- MCP read tools
- no write-back

This is the safest parallel implementation slice and should start first.

### Phase 1: Journaled write-intent path

- append-only journal
- candidate schemas
- evidence capture from agent interactions
- no autonomous canonical note edits yet

### Phase 2: Materializer and review

- single-writer materializer
- confidence thresholds
- review queue
- incremental re-index on applied changes

### Phase 3: Source importers

- Gmail
- calendar
- tasks/issues
- references/bookmarks

### Phase 4: Routing-aware personalization

- orchestrator triggers KB lookup selectively
- response generation uses retrieved evidence
- routing can consider personalized context

## 17. Recommended Immediate Next Step

Start now, in parallel, but only with Phase 0.

Do not start with:

- autonomous note rewriting
- Smart Connections backend dependency
- complex multi-source ingestion

Start with:

- read-only retrieval over a mirrored vault
- stable MCP tools
- evidence bundle construction for answer generation

That slice is isolated, valuable, and can be implemented without destabilizing
the current inference and agent stack.

## 18. Preferred Delegation Strategy

Best use of peers:

- Gemini: architecture/spec refinement, ingestion taxonomy, retrieval API design
- Claude Code or Codex: repo-local implementation of the Phase 0 slice
- OpenCode/GLM: bounded helper implementation tasks after the main contract is
  fixed

Recommendation:

- delegate spec review and refinement to Gemini
- implement the first read-only retrieval slice with Claude Code or Codex

## 19. Acceptance Criteria for Phase 0

Phase 0 is complete when:

- the service can index a mirrored Markdown vault incrementally
- agents can call MCP read tools and retrieve ranked results
- retrieval quality is improved by reranking
- the answer model can cite the retrieved note paths it used
- no sandbox has direct write access to canonical notes
- the system runs locally without cloud dependencies
