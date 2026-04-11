# NemoClaw Implementation Review — Codex
*Date: 2026-04-10*
*Reviewer: Codex CLI*
*Scope: Existing code + evolution roadmap implementation feasibility*

## Executive Summary
The overall direction is feasible, but not on the seams the roadmap currently assumes. The biggest problem is that the proposed intelligent router is placed "inside the OpenShell gateway" even though the reviewed architecture documents describe `inference.local` as a single active gateway route, not an exposed extension point, and several sandboxes bypass that route entirely. The current `orchestrator/` package is a solid prototype for low-volume delegation, but it is not yet a durable control plane: it is synchronous, string-oriented, and uses JSON/file persistence patterns that will break once WhatsApp, retries, background workers, or a knowledge base writer are added. The most underestimated items are intelligent routing, per-sandbox model independence, WhatsApp integration, and any design that treats Smart Connections as a stable backend API.

## Critical Findings
1. The routing design is not buildable "inside the OpenShell gateway" as written. `docs/evolution-roadmap.md:98-127,212-226` assumes a new router inside the gateway, while `nemoclaw-architecture.md:677-681` and `docs/openclaw-concepts.md:218-232` describe a single active route controlled by `openshell inference set`. Without forking OpenShell or inserting an external proxy as the one configured provider, this blocks the design.
Severity: Critical.
Recommendation: Build a host-side OpenAI-compatible router proxy and point OpenShell at that proxy once, or move routing up to the orchestrator/channel layer.

2. `TaskManager` is not safe for the multi-process architecture the roadmap requires. It only protects writes with an in-process `threading.Lock`, then rewrites the full JSON file on every mutation (`orchestrator/task_manager.py:75-77,97,131-134,159-174,252-282`). Two writers will lose updates.
Severity: Critical.
Recommendation: Replace `tasks.json` with SQLite and explicit state transitions before adding webhook workers, retry loops, or background refinement jobs.

3. The current bridge/orchestrator path is synchronous and fully buffered, so it will not survive real inbound channels. `orchestrator/orchestrator.py:134-190,362-404` blocks per delegation, and `orchestrator/sandbox_bridge.py:146-151` buffers all stdout/stderr in memory. That is incompatible with webhook SLAs, cancellation, and large outputs.
Severity: High.
Recommendation: Introduce a durable queue plus worker model; make sandbox execution streamable and bounded.

4. The classifier latency assumption in the roadmap is contradicted by the benchmark document. The roadmap assumes Gemma 27B adds roughly 200 ms (`docs/evolution-roadmap.md:228-235`), but the measured warm latency is roughly 940-970 ms before any extra routing machinery (`docs/benchmarks.md:33-41`).
Severity: High.
Recommendation: Use deterministic rules first, reserve classification for ambiguous cases only, and cache per conversation.

5. The Obsidian plan treats Smart Connections as if it were a stable programmatic backend. The reviewed design only establishes a plugin-managed index under `.smart-env` plus UI-level similarity behavior (`docs/evolution-roadmap.md:1158-1163,1335-1381,1432-1445`), not a supported server API.
Severity: High.
Recommendation: Use MCP for agent access, but back it with your own index or a thin single-writer knowledge service instead of depending on Smart Connections internals.

## Detailed Review
### 1. Existing Orchestrator Code Quality

#### 1.1 The code is modular, but the extension surface is too thin for the roadmap
Finding: `Orchestrator` cleanly composes `SandboxBridge`, `TaskManager`, and `SharedWorkspace` (`orchestrator/orchestrator.py:124-129`), which is good for a prototype. The problem is that all meaningful work returns plain strings. `delegate()` returns `str` (`orchestrator/orchestrator.py:134-190`), `send_prompt()` returns stripped stdout (`orchestrator/sandbox_bridge.py:176-222`), and pipeline context is plain `str.format()` over prompt templates (`orchestrator/orchestrator.py:223-232`). That is enough for demo workflows, but not enough for routing metadata, structured retries, cancellation, streaming, per-step provenance, or channel-aware formatting.

Severity: High

Recommendation: Promote delegation to a typed execution object and keep raw transport details.

```python
@dataclass
class DelegationResult:
    task_id: str
    agent: str
    status: Literal["completed", "failed", "timeout"]
    stdout: str
    stderr: str
    duration_ms: float
    route_metadata: dict[str, Any]
```

Then make `pipeline()` operate on `DelegationResult`, not raw strings.

#### 1.2 Pipeline task tracking is already race-prone
Finding: After each delegation, `pipeline()` looks up "the most recently created task for this agent" and assumes that is the step it just ran (`orchestrator/orchestrator.py:243-246`). That is wrong as soon as another thread or process creates a task for the same agent. The same method also swallows prompt-template mistakes: if any placeholder is missing, it silently falls back to the raw template (`orchestrator/orchestrator.py:228-232`).

Severity: High

Recommendation: Return the created `task.id` directly from `delegate()`, and treat missing placeholders as orchestration errors instead of silently degrading prompts.

#### 1.3 The subprocess bridge is serviceable for manual delegation, not for production ingress
Finding: `SandboxBridge.run_in_sandbox()` shells out to `ssh` with a ProxyCommand (`orchestrator/sandbox_bridge.py:130-151`). It has no `ConnectTimeout`, no `BatchMode=yes`, no keepalive settings, no connection reuse, and no output bounds. If SSH negotiation or the proxy hangs, the worker stays blocked until the global timeout expires. If the sandbox prints a huge payload, `capture_output=True` stores the entire result in memory (`orchestrator/sandbox_bridge.py:146-151`).

Severity: High

Recommendation: Add transport-level controls and a streaming API.

```python
outer_cmd = [
    "ssh",
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=5",
    "-o", "ServerAliveInterval=15",
    "-o", "ServerAliveCountMax=3",
    ...
]
```

For long-running tasks, switch from `subprocess.run()` to `Popen` with bounded readers, streamed progress, and explicit kill-on-timeout.

#### 1.4 JSON task persistence is not adequate beyond a single process
Finding: `TaskManager` explicitly claims thread safety (`orchestrator/task_manager.py:75-77`), but it is only thread-safe within one process because the lock is local (`orchestrator/task_manager.py:97`). Every mutation reads current state, mutates an in-memory dict, then rewrites the full file (`orchestrator/task_manager.py:131-134,159-174,252-268`). With two processes, lost updates are inevitable.

Severity: Critical

Recommendation: Replace `tasks.json` with SQLite before adding any worker pool, webhook receiver, or background ingestion. You need row-level state, leases, dedupe keys, and transactional updates.

#### 1.5 The shared workspace is a useful audit layer, but a poor queue
Finding: The inbox/outbox/context layout is understandable and debuggable (`orchestrator/shared_mcp.py:1-29,46-69`). The problem is that writes are plain `write_text()` calls (`orchestrator/shared_mcp.py:136-137,189-190`), there is no atomic rename, no lock, no lease/ack state, no dedupe key, and `clean_completed()` can delete unread files solely based on age (`orchestrator/shared_mcp.py:212-237`).

Severity: High

Recommendation: Keep the shared workspace for human-inspectable artifacts and attachments, but move task dispatch to SQLite or Redis. Filesystem handoff should be secondary, not the source of truth.

### 2. Routing Implementation Feasibility

#### 2.1 The proposed gateway insertion point is speculative
Finding: The roadmap places a three-tier router inside the OpenShell gateway (`docs/evolution-roadmap.md:98-127,212-226`). The architecture doc and the OpenClaw concepts doc instead describe a single gateway-level active route, switched by `openshell inference set` (`nemoclaw-architecture.md:184-189,677-681`; `docs/openclaw-concepts.md:218-232`). There is no reviewed code or documented plugin seam showing where custom router logic can be injected between interception and provider dispatch.

Severity: Critical

Recommendation: Do not start by forking OpenShell. The realistic implementation is:

1. Build an external `router-proxy` service that exposes an OpenAI-compatible `/v1/chat/completions`.
2. Register that proxy as one OpenShell provider.
3. Point `openshell inference set` at the proxy once.
4. Let the proxy decide which downstream provider/model to call.

That gets dynamic routing without upstream gateway surgery.

#### 2.2 A gateway router would still miss part of the system
Finding: The roadmap frames routing as if it governs all queries. It does not. The reviewed architecture says Codex can use Ollama directly and Gemini CLI does not use `inference.local` at all (`nemoclaw-architecture.md:307-314,345-346,394-399`). So even a perfect gateway router only covers OpenClaw and any sandbox explicitly configured to use `inference.local`.

Severity: Critical

Recommendation: Split routing into two layers:

1. `inference.local` routing for OpenClaw and other OpenAI-compatible clients.
2. Orchestrator-level agent selection for Codex/Gemini/Claude sandboxes that already own their provider path.

Otherwise the design overpromises system-wide routing and underdelivers in exactly the agents you want to orchestrate.

#### 2.3 The classifier overhead is materially underestimated
Finding: The roadmap justifies Gemma 27B as a roughly 200 ms classifier (`docs/evolution-roadmap.md:228-235`). The benchmark file shows warm responses around 940-969 ms on the Mac before any extra router logic (`docs/benchmarks.md:33-41`). The same benchmarks show roughly 800 ms of extra overhead when requests traverse the sandbox/proxy chain (`docs/benchmarks.md:43-68`). A classifier on every query is therefore more likely to add around 1 second than 200 ms.

Severity: High

Recommendation: Use a tiered policy:

1. Deterministic rules handle the majority of traffic.
2. Cache routing decisions per conversation or per normalized prompt class.
3. Run classifier only for ambiguous cases.
4. Prefer a smaller classifier or a non-LLM heuristic pass before Gemma.

#### 2.4 The request data flow needs more than just a router
Finding: The roadmap describes the target flow but skips the concrete code seams (`docs/evolution-roadmap.md:212-226`). The real flow today is: sandbox agent -> `https://inference.local/v1` -> OpenShell gateway -> active provider -> model (`nemoclaw-architecture.md:596-615`). To make routing decisions based on channel, latency, privacy, or attachments, new code is needed in at least six places:

1. Channel layer: generate routing metadata.
2. OpenClaw/orchestrator request layer: preserve that metadata.
3. Router proxy: evaluate rules and classifier.
4. Registry/policy loader: map route decisions to actual providers.
5. Provider adapters: forward requests to Ollama/cloud APIs.
6. Metrics/audit layer: record the final decision and outcome.

Severity: High

Recommendation: Define an explicit request envelope before building the router.

```python
class RouteMeta(BaseModel):
    channel: str
    user_id: str | None
    conversation_id: str | None
    privacy_flags: set[str]
    latency_budget_ms: int | None
    attachments: list[AttachmentMeta]
```

If the metadata does not survive the trip to the router, the router cannot implement the policy the roadmap describes.

#### 2.5 Capability registry should be declarative first, discovered second
Finding: The roadmap suggests a YAML registry (`docs/evolution-roadmap.md:130-210`). That is the right direction, but only a subset of fields can be auto-discovered. Ollama can tell you whether a model exists and maybe what it is called; it cannot infer business semantics like privacy class, cost tier, output quality, or whether you consider it acceptable for code review.

Severity: Medium

Recommendation: Use manual manifests as the source of truth. Add auto-discovery only for liveness, model presence, and loaded-state validation.

### 3. WhatsApp Bridge Engineering

#### 3.1 FastAPI is acceptable, but the proposed bridge is attached to the wrong seam
Finding: The roadmap proposes a host-side `whatsapp_bridge.py` that receives webhooks and forwards directly to the agent via the sandbox bridge (`docs/evolution-roadmap.md:956-992`). The broader OpenClaw docs describe channels and webhooks as gateway responsibilities (`docs/openclaw-concepts.md:64-70,156-170,362-370`). A standalone bridge that bypasses the gateway also bypasses channel binding, session handling, memory, and future channel policy.

Severity: High

Recommendation: Use one of these two patterns:

1. Preferred: implement WhatsApp as an OpenClaw channel/webhook integration so the gateway remains the source of truth.
2. Acceptable fallback: keep a tiny webhook translator outside the sandbox, but have it enqueue work and post into the OpenClaw gateway or a stable ingress API, not directly into `SandboxBridge`.

FastAPI is fine if you want validation, async handlers, and health endpoints. The framework is not the main issue; the ownership boundary is.

#### 3.2 Webhook handlers must acknowledge immediately and process asynchronously
Finding: The current orchestrator is synchronous (`orchestrator/orchestrator.py:134-190`) and the bridge blocks until the agent returns (`orchestrator/sandbox_bridge.py:146-151`). That means a WhatsApp webhook would block on model latency, tool calls, and possible SSH delays. That is not workable for a provider that expects a fast 200 and may retry on failure.

Severity: Critical

Recommendation: Introduce a durable inbound queue keyed by conversation id, with one active worker per conversation.

```python
@app.post("/webhook")
async def receive(payload: dict) -> dict:
    msg = parse_whatsapp(payload)
    if dedupe_store.seen(msg.id):
        return {"status": "duplicate"}
    queue.enqueue(msg.conversation_id, msg.model_dump())
    return {"status": "accepted"}
```

Then let a background worker perform media fetch, routing, agent execution, and outbound sends.

#### 3.3 Media handling needs a real storage policy
Finding: The roadmap says "download media" for images, voice notes, documents, and video (`docs/evolution-roadmap.md:1035-1048`) but does not define storage, cleanup, or failure handling. If you use `/tmp`, long-running jobs and restarts will lose state. If you use the shared workspace directly, you will mix transient blobs with orchestration state and knowledge artifacts.

Severity: Medium

Recommendation: Create a dedicated attachment cache outside sandbox state, for example `~/workspace/whatsapp-cache/<date>/...`, and store metadata in the queue database. Add a janitor job for TTL cleanup and a size cap.

#### 3.4 The 24-hour rule is real, but the example in the prompt is slightly off
Finding: A job that takes 2 hours does not by itself violate a 24-hour user-initiated response window; the real risk is queued or retried work that extends beyond the user's last inbound message. The current design has no persisted "last inbound at" field, so it cannot decide whether a free-form reply is still allowed.

Severity: Medium

Recommendation: Persist `last_user_message_at` per WhatsApp conversation, send a fast acknowledgement when the request is accepted, and fail closed to template-based or manual follow-up when the window expires.

#### 3.5 Duplicate detection and retry semantics are missing
Finding: The roadmap mentions testing for rate limiting (`docs/evolution-roadmap.md:1092-1103`) but does not define idempotency keys, retry backoff, or duplicate inbound detection. Without that, transient errors create duplicate assistant responses and duplicated facts in the knowledge base.

Severity: High

Recommendation: Use the WhatsApp message id as the idempotency key for inbound messages and the queue job id for outbound sends. Persist both.

### 4. Obsidian MCP Server Engineering

#### 4.1 MCP is the right read interface for agents, not the right write engine for the system
Finding: The architecture consistently treats MCP as the shared extensibility layer (`nemoclaw-architecture.md:401-403`; `docs/inter-agent-guide.md:21-29,33-61`). That makes MCP a good fit for agent queries such as `search_knowledge()` and `get_person_context()` (`docs/evolution-roadmap.md:1335-1381`). It is a poor fit for batch ingestion, merge logic, and compaction, which are system responsibilities rather than agent-facing tools.

Severity: Medium

Recommendation: Use a hybrid design:

1. Agents talk to an MCP server for read-heavy operations.
2. Ingestion/refinement jobs talk directly to a host-side knowledge service or library.
3. Only the knowledge service writes to the vault.

#### 4.2 Smart Connections does not yet look like a stable backend contract
Finding: The roadmap depends on Smart Connections for semantic search, similarity scores, and block-level embeddings (`docs/evolution-roadmap.md:1116-1125,1158-1163,1307-1315`). What is missing is a documented server-side API in the reviewed materials. The design only establishes that an index exists under `.smart-env` (`docs/evolution-roadmap.md:1432-1434`) and that similarity is surfaced in the UI, not that a Python process can query it safely or version-stably.

Severity: High

Recommendation: Treat Smart Connections as optional operator-side augmentation, not as the backend contract for NemoClaw. If personalized retrieval is important, maintain your own local embedding index over the vault and let Obsidian remain a markdown editor.

#### 4.3 The merge/update pipeline has no prompt discipline yet
Finding: The refinement section says Gemma 27B will merge duplicates, update confidence, discover connections, and summarize periods (`docs/evolution-roadmap.md:1271-1333`), but it does not define prompts, evidence requirements, abstention conditions, or merge safety. Without that, the system will hallucinate relationships and silently rewrite personal notes.

Severity: High

Recommendation: Make all refinement actions evidence-backed and structured.

```json
{
  "action": "merge | update | abstain",
  "target_note": "projects/project-alpha.md",
  "evidence": [
    {"note": "daily/2026-04-08.md", "quote": "Deadline moved to April 22"}
  ],
  "confidence": 0.92,
  "reason": "Same project, same fact, newer source"
}
```

Only apply updates above a threshold, and send low-confidence merges to review.

#### 4.4 The write path and re-index path are underspecified
Finding: The roadmap assumes an agent can add a fact through MCP and that Smart Connections will later incorporate it (`docs/evolution-roadmap.md:1367-1373,1287-1294`). That only works if Obsidian plus Smart Connections is actually running and notices external file changes. The current document does not state whether Obsidian is always on, whether re-indexing is event-driven, or what happens when the Mac is offline.

Severity: High

Recommendation: Define one explicit re-index contract:

1. Either Obsidian/Smart Connections must run continuously on the Mac and watch the vault.
2. Or the knowledge service owns embeddings itself and updates incrementally on write.

Do not leave re-indexing as eventual magic.

#### 4.5 Concurrent writers will corrupt notes or create conflicts
Finding: The design currently allows multiple writers in principle: real-time agent facts, daily email/calendar jobs, weekly message ingestion, and the refinement pass (`docs/evolution-roadmap.md:1202-1299`). Markdown files plus git are good for auditability, but not for concurrent mutation.

Severity: Medium

Recommendation: Enforce a single-writer model. Writers append normalized events to a queue or journal; one refiner process materializes canonical notes.

### 5. Scalability and Performance

#### 5.1 `ThreadPoolExecutor` is fine for the current fan-out size
Finding: `parallel_specialists()` uses `ThreadPoolExecutor` (`orchestrator/orchestrator.py:398-402`). For the current workload, the GIL is not the bottleneck because the heavy work is subprocess and network I/O. The executor is acceptable for a small specialist set.

Severity: Observation

Recommendation: Keep the executor for low-volume orchestration. Do not over-engineer this part yet.

#### 5.2 The real throughput bottleneck is process launch plus SSH
Finding: Every orchestration call launches a new SSH process (`orchestrator/sandbox_bridge.py:130-151`). The benchmark document already shows roughly 800 ms of path overhead for the sandbox/proxy chain (`docs/benchmarks.md:43-68`). That is acceptable for occasional specialist fan-out, but not for high-frequency channel ingress or fine-grained routing decisions.

Severity: High

Recommendation: Assume low single-digit concurrent delegations are practical today. If you need more, add connection reuse or a resident sandbox-side runner instead of one SSH process per task.

#### 5.3 A 50K-note vault will need a disk-backed retrieval engine
Finding: The roadmap explicitly calls out block-level embeddings (`docs/evolution-roadmap.md:1158-1163`). At 50K notes, block-level indexing can easily mean hundreds of thousands of vectors. Loading that fully into a Python MCP server is likely to become a multi-GB memory problem once metadata and note text are included.

Severity: Medium

Recommendation: Use an on-disk ANN index or database-backed retrieval layer. Keep the MCP server stateless and query the index on demand.

#### 5.4 Re-embedding the entire vault on every change is not viable
Finding: The roadmap's schedule implies changed files should be re-indexed (`docs/evolution-roadmap.md:1287-1299`), which is the correct direction. A full-vault re-embed after each note write would be operationally unacceptable.

Severity: Medium

Recommendation: Make updates incremental only: changed note, changed blocks, and optionally nearest-neighbor recomputation for impacted notes.

### 6. Integration Complexity

#### 6.1 The full-stack process graph is much larger than the roadmap suggests
Finding: To realize the full design, the following moving parts need to be up and healthy:

| Component | Host | Depends on |
|-----------|------|------------|
| Tailscale | Spark + Mac | network |
| OpenShell gateway | Spark | Docker/k3s |
| Spark Ollama | Spark | model files, GPU |
| Mac Ollama | Mac | model files, Metal |
| `nemoclaw-main` sandbox | Spark | OpenShell gateway |
| OpenClaw gateway inside sandbox | Spark sandbox | sandbox + model route |
| `claude-dev`, `codex-dev`, `gemini-dev` sandboxes | Spark | OpenShell gateway + policies |
| Orchestrator service | Spark | bridge + task DB |
| Router proxy | Spark | registry + downstream providers |
| WhatsApp ingress | Spark | queue + outbound credentials |
| Queue/database | Spark | disk |
| Knowledge service / MCP server | Spark or Mac | vault + index |
| Obsidian + Smart Connections | Mac | vault + desktop runtime |
| Background ingestion/refinement jobs | Spark or Mac | queue + knowledge service |
| Cloud APIs | external | credentials + egress |

Severity: High

Recommendation: Treat this as a distributed system, not a feature add. Give each service a clear owner, health check, and restart boundary.

#### 6.2 Startup order needs to be explicit
Finding: The roadmap does not spell out startup sequencing, but it matters. Starting the router before providers are reachable, or starting the WhatsApp ingress before the queue is writable, creates hard-to-diagnose partial failures.

Severity: High

Recommendation: Define startup order and degraded behavior:

1. Tailscale and local providers.
2. OpenShell gateway.
3. Sandboxes.
4. OpenClaw gateway.
5. Queue/database.
6. Router proxy.
7. Orchestrator workers.
8. Channel ingress.
9. Knowledge services.

Each service should refuse traffic until its dependencies pass a readiness check.

#### 6.3 End-to-end testing needs a harness, not just unit tests
Finding: The roadmap lists useful WhatsApp tests (`docs/evolution-roadmap.md:1092-1103`) but does not define an end-to-end harness for the full path. Testing WhatsApp -> preprocessor -> router -> agent -> knowledge base -> response against live services will be too flaky and too slow to rely on.

Severity: High

Recommendation: Build layered tests:

1. Contract tests for webhook parsing, dedupe, routing decisions, and formatting.
2. Fake sandbox bridge tests for orchestration logic.
3. Fake KB tests for personalized retrieval behavior.
4. One or two real smoke tests against a live sandbox stack.

#### 6.4 The update story depends on where you place the new logic
Finding: Today, changing the global route is a gateway action (`docs/openclaw-concepts.md:218-232`). If you embed the router in the gateway, router updates imply gateway restarts. If you externalize the router, the update blast radius is much smaller.

Severity: Medium

Recommendation: Prefer independently restartable services:

1. Router logic lives in `router-proxy`.
2. Knowledge access lives in `kb-service`.
3. WhatsApp ingress only handles ingress/egress.

That lets you update router policy without bouncing the entire agent stack.

### 7. Effort Estimates

| # | Improvement | Roadmap effort | Revised effort | Hidden complexity the roadmap underestimates | Dependencies the roadmap does not mention |
|---|-------------|----------------|----------------|---------------------------------------------|-------------------------------------------|
| 1 | Intelligent routing | Medium-Large | XL | No confirmed gateway extension seam; metadata propagation; benchmarking and fallback behavior | External router proxy or OpenShell fork; metrics; conversation metadata |
| 2 | Per-sandbox model independence | Medium | XL | Conflicts with documented single active route; some agents bypass `inference.local` entirely | Separate gateways, sandbox-local proxies, or orchestrator-level routing policy |
| 3 | Security mitigations checklist | Small | Medium | Five items span multiple machines and trust boundaries, not one patch | Tailscale ACLs, firewall automation, provider scoping model |
| 4 | Default agent skills | Small-Medium | Medium | Static mount is easy; dynamic context refresh is not | Context generators, refresh hooks, packaging during sandbox create |
| 5 | Provider failover | Medium | Large | Need health state, retry policy, idempotency, and recovery semantics | Router proxy, metrics, provider health probes |
| 6 | Cold start mitigation | Small | Small | Easy if limited to warmup; harder if tied to readiness and alerts | Startup hooks, health checks, model preload endpoint |
| 7 | Mac sleep prevention | Small | Small | Straightforward operational change | launchd or `pmset` integration, operator policy |
| 8 | Structured metrics and cost tracking | Medium | Large | Cross-provider token accounting and route annotation are non-trivial | Central event schema, storage, dashboard backend |
| 9 | Automated health checks | Small | Medium | Health is easy; trustworthy remediation and alerting take longer | Cron/systemd, alert transport, runbooks |
| 10 | WhatsApp integration | Medium-Large | XL | Queueing, dedupe, media lifecycle, session ownership, outbound constraints | Meta app setup, tunnel, queue DB, gateway ingress strategy |
| 11 | Multimodal preprocessing | Large | XL | Audio/doc/video pipelines are separate products, not one module | Speech-to-text stack, file extractors, attachment store |
| 12 | Orchestrator auto-routing | Medium | Large | Depends on router metadata, outcome tracking, and structured execution | Registry, historical feedback store, typed delegation results |
| 13 | Conversation memory for routing | Medium | Large | Requires session model plus storage discipline | Channel ids, per-conversation cache, privacy rules |
| 14 | Mac health reporting | Small | Medium | Collecting signal is easy; making it trustworthy for routing is harder | Remote agent or exporter on Mac, auth, retry |
| 15 | Ollama auth proxy | Small | Medium | Low code, but every client path must be updated safely | Reverse proxy config, certificate/auth distribution |
| 16 | Backup and restore | Small | Medium | Full-state restore across gateway, sandboxes, and queues is not trivial | Export format, versioning, migration logic |
| 17 | Sandbox resource limits | Small | Medium | Depends on actual OpenShell/container control points | Docker/k3s resource configuration, policy compatibility |
| 18 | UI authentication | Medium | Large | Replacing static token with real identity touches gateway and clients | Tailscale identity or OIDC integration, session store |
| 19 | Adaptive pipelines | Large | XL | Needs planner quality, policy bounds, and failure recovery | Structured task graph, evaluation data, feedback loop |
| 20 | Dashboard | Medium | Large | Dashboard is easy; clean metrics pipeline is not | Metrics store, auth, alert links |
| 21 | Multi-user support | Large | XL | This is effectively a product redesign | Identity, authorization, tenant isolation, audit |
| 22 | Obsidian knowledge base | Large | XL | The hard part is not MCP; it is ingestion, merge safety, and index ownership | Single-writer knowledge service, re-index contract, backup/versioning |

## Recommendations Summary
1. Severity: Critical
Reference: `docs/evolution-roadmap.md:98-127,212-226`; `nemoclaw-architecture.md:677-681`; `docs/openclaw-concepts.md:218-232`
Action: Move intelligent routing out of the assumed gateway internals and into an external router proxy or the orchestrator.

2. Severity: Critical
Reference: `orchestrator/task_manager.py:75-77,97,131-134,159-174,252-282`
Action: Replace `tasks.json` with SQLite before introducing any second writer.

3. Severity: Critical
Reference: `orchestrator/orchestrator.py:134-190,362-404`; `orchestrator/sandbox_bridge.py:146-151`
Action: Introduce a durable queue/worker model for inbound channels; never run webhook handlers directly on blocking orchestrator calls.

4. Severity: Critical
Reference: `nemoclaw-architecture.md:307-314,345-346,394-399`
Action: Separate `inference.local` routing from orchestrator-level agent/provider selection, because Codex and Gemini do not share the same inference path.

5. Severity: High
Reference: `docs/benchmarks.md:33-41,43-68`; `docs/evolution-roadmap.md:228-235`
Action: Re-scope classifier routing to ambiguous cases only and measure end-to-end latency with the real transport chain.

6. Severity: High
Reference: `orchestrator/orchestrator.py:228-246`
Action: Return typed delegation results, stop looking up "latest task for agent", and fail fast on bad prompt templates.

7. Severity: High
Reference: `orchestrator/sandbox_bridge.py:130-151,176-222`
Action: Add SSH connect timeouts, batch mode, streaming, cancellation, and output size limits.

8. Severity: High
Reference: `docs/evolution-roadmap.md:956-992`; `docs/openclaw-concepts.md:156-170,362-370`
Action: Attach WhatsApp to the OpenClaw gateway/channel model instead of bypassing it with direct bridge-to-sandbox calls.

9. Severity: High
Reference: `docs/evolution-roadmap.md:1158-1163,1335-1381,1432-1445`
Action: Do not depend on Smart Connections internals as the server-side API; put a stable knowledge service behind MCP.

10. Severity: High
Reference: `docs/evolution-roadmap.md:1271-1333`
Action: Make refinement actions evidence-based, structured, and thresholded; require abstention and human review for low-confidence merges.

11. Severity: High
Reference: `docs/evolution-roadmap.md:1202-1299`
Action: Enforce a single-writer model for the knowledge base and treat ingestion as append-only event capture plus periodic compaction.

12. Severity: High
Reference: `docs/evolution-roadmap.md:1092-1103`; `orchestrator/shared_mcp.py:212-237`
Action: Build an end-to-end test harness with fake Meta payloads, fake sandboxes, and a fake KB before wiring live services together.

13. Severity: Medium
Reference: `orchestrator/shared_mcp.py:116-190,212-237`; `docs/inter-agent-guide.md:291-344`
Action: Keep the shared filesystem for artifacts and human inspection, not as the primary transport or queue.

14. Severity: Medium
Reference: `docs/evolution-roadmap.md:1367-1373,1287-1294`
Action: Define a concrete re-index contract for new knowledge writes; do not assume Obsidian will always be running and watching the vault.

15. Severity: Observation
Reference: `orchestrator/orchestrator.py:398-402`
Action: Keep `ThreadPoolExecutor` for now; the urgent bottleneck is transport durability, not the GIL.
