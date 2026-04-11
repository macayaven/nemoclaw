# Orchestrator Runtime Foundations

This document describes the host-side runtime components now shipped in the
checked-in `orchestrator/` package. The design stays outside OpenShell and
OpenClaw internals on purpose: sandbox boundaries remain intact, credentials
stay on the host, and gateway behavior is treated as upstream-owned.

## What Is Implemented

### 1. SQLite-backed task persistence

- Task state is stored in `shared_workspace/orchestrator.db`.
- The old `tasks.json` file is migrated once on first startup if present.
- Task records now store structured terminal results, including sandbox
  execution metadata, instead of plain strings.

### 2. Durable queue and worker foundation

- `orchestrator/work_queue.py` provides a SQLite-backed queue with:
  - dedupe keys,
  - worker leases,
  - retry / dead-letter handling,
  - queue workers suitable for webhook-driven ingress.
- This is the shared foundation for future asynchronous services.

### 3. Hardened sandbox execution

- `orchestrator/sandbox_bridge.py` now uses bounded incremental capture
  instead of unbounded `capture_output=True`.
- SSH transport now applies connect timeout and keepalive settings.
- Prompt dispatch returns structured execution data with truncation metadata.

### 4. Host-side router proxy

- `orchestrator/router_proxy.py` implements an OpenAI-compatible HTTP proxy.
- Routing decisions come from deterministic policy plus a model capability
  registry in `orchestrator/routing.py`.
- The routing layer can also rewrite the upstream model, which lets specialist
  providers such as Mac-hosted MedGemma stay registered and available without
  replacing the global default route.
- Some models and agents bypass `inference.local` entirely. The policy engine
  models that explicitly by allowing `agent` targets that the proxy refuses to
  serve directly; those routes are intended for orchestrator-level dispatch.

### 5. WhatsApp ingress foundation

- `orchestrator/whatsapp.py` provides:
  - fast webhook acknowledgement,
  - duplicate suppression,
  - queue-backed conversation processing,
  - image staging with TTL-based cleanup,
  - a routed dispatcher that can reuse the routing policy engine.

## Dependency Graph

```text
WhatsApp webhook
  -> InboundEventStore
  -> WorkQueue
  -> WhatsAppConversationWorker
  -> RoutedOrchestratorDispatcher
  -> Orchestrator
  -> SandboxBridge
  -> OpenShell sandboxes

OpenShell gateway managed traffic
  -> host-side router proxy
  -> deterministic routing policy
  -> upstream provider OR orchestrator-level direct route
```

## Startup Order

1. Start OpenShell gateway and persistent sandboxes.
2. Start any upstream inference backends that the router proxy targets.
3. Start the host-side router proxy and point OpenShell at it if dynamic
   routing is required for gateway-managed traffic.
4. Start webhook listeners such as the WhatsApp ingress service.
5. Start queue workers after the listeners so inbound traffic can be accepted
   immediately even if workers restart later.

## Operational Notes

- `orchestrator.db` is the source of truth for task state, queue leases,
  webhook receipts, and staged media metadata.
- The queue worker model is intentionally generic. Future services should
  enqueue work instead of calling blocking sandbox execution directly from
  request handlers.
- The router proxy is a host-side seam, not a gateway modification. That keeps
  the system deployable even when upstream OpenShell internals change.

## Failure Modes

| Component | Failure | Expected Behavior |
|-----------|---------|------------------|
| SQLite store | Database locked briefly | Writers retry via SQLite busy timeout; no JSON clobbering |
| Sandbox bridge | SSH handshake stalls | Connect timeout / keepalive terminates the attempt |
| Large agent output | Output exceeds capture budget | Preview is truncated but task metadata still records byte counts |
| Router proxy | Upstream unavailable | Proxy returns `502` without mutating gateway internals |
| WhatsApp webhook | Duplicate delivery | Event receipt is marked duplicate and not re-enqueued |
| Queue worker | Handler crash | Item is re-queued or dead-lettered based on attempt budget |

## Live Integration Seams

The repository does not contain live Meta credentials, a production WhatsApp
business account, or upstream OpenClaw gateway hooks for external channel
handoff. The shipped code therefore stops at the safe seams below:

- Replace the routed dispatcher with a production dispatcher that hands
  accepted messages to the OpenClaw gateway session/channel layer.
- Replace staged-media URL fetching with the real Meta media fetch flow and
  auth headers.
- Point the router proxy at real upstream backends or an OpenShell-facing
  provider endpoint.
