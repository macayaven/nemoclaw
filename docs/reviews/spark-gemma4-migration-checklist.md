# Spark Gemma 4 Migration Checklist

Status: operational checklist for migrating the target topology toward
Spark-hosted `gemma4:31b`

This checklist is intentionally separate from the current deployment and
operations guides. The live operational docs still describe the existing
Mac-backed secondary inference path. Use this checklist while migrating the
system, then fold the validated outcome into the main docs once the migration
is complete.

## 1. Target Outcome

After this migration:

- Spark remains the default reasoning node with `nemotron-3-super:120b`
- Spark also hosts `gemma4:31b` as the secondary strong local route
- Spark hosts retrieval support models:
  - `nomic-embed-text`
  - `qllama/bge-reranker-v2-m3:latest`
- Mac is no longer treated as the long-term secondary chat-LLM node
- Mac becomes the future modality node, starting with TTS

## 2. Current Known State

Verified on Spark:

- `nemotron-3-super:120b` present
- `gemma4:31b` present
- `nomic-embed-text:latest` present
- `qllama/bge-reranker-v2-m3:latest` present
- active OpenShell route still points to:
  - provider: `local-ollama`
  - model: `nemotron-3-super:120b`

Observed direct local Ollama behavior for `gemma4:31b`:

- model responds successfully via local Ollama API
- initial cold load took roughly 19.7 seconds total
- most of that time was load time, not generation time

Implication:

- the model is downloaded and runnable
- cold-start behavior must be accounted for operationally
- OpenShell and router-level routing still need explicit validation

## 3. Migration Gates

Do not update the main operations docs or test contracts until all of these are
true:

1. `gemma4:31b` responds locally on Spark
2. OpenShell can route to `gemma4:31b` reliably
3. the orchestrator/runtime docs reflect the final intended role split
4. the Mac inference role is either retired or clearly demoted in the live
   contract

## 4. Step-by-Step Checklist

### Step 1 — Verify Spark Ollama inventory

```bash
curl -sf http://127.0.0.1:11434/api/tags | jq -r '.models[].name'
```

Expected relevant entries:

- `nemotron-3-super:120b`
- `gemma4:31b`
- `nomic-embed-text:latest`
- `qllama/bge-reranker-v2-m3:latest`

### Step 2 — Verify direct local `gemma4:31b` inference

```bash
curl -sf http://127.0.0.1:11434/api/chat -d '{
  "model":"gemma4:31b",
  "messages":[{"role":"user","content":"Reply with the single word READY."}],
  "stream":false,
  "options":{"num_predict":16,"temperature":0}
}'
```

Pass condition:

- non-error JSON response
- model field is `gemma4:31b`
- request completes successfully even if cold-start is slow

Note:

- the first call may spend ~20 seconds mostly on load
- this is acceptable for verification, but not necessarily for a default
  low-latency route

### Step 3 — Confirm current OpenShell default remains stable

```bash
openshell inference get
```

Expected for now:

- provider `local-ollama`
- model `nemotron-3-super:120b`

This should remain the default route until Spark-hosted Gemma routing is fully
validated.

### Step 4 — Validate temporary route switch to Spark Gemma

```bash
openshell inference set --provider local-ollama --model gemma4:31b
openshell inference get
```

Pass condition:

- `openshell inference get` shows `local-ollama / gemma4:31b`

### Step 5 — Validate sandbox path through `inference.local`

Use a sandbox request path, not just direct host Ollama:

```bash
openshell sandbox connect nemoclaw-main -- \
  bash -lc "curl -s -k https://inference.local/v1/chat/completions \
    -H 'Content-Type: application/json' \
    -d '{\"model\":\"gemma4:31b\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with READY only.\"}],\"max_tokens\":16}'"
```

Pass condition:

- non-error OpenAI-compatible JSON response
- request completes through the sandbox path

### Step 6 — Restore default route

```bash
openshell inference set --provider local-ollama --model nemotron-3-super:120b
openshell inference get
```

Pass condition:

- route is restored cleanly to `nemotron-3-super:120b`

### Step 7 — Decide the secondary Spark role

Recommended role for `gemma4:31b`:

- structured coding and code transformation tasks
- long-context retrieval-backed synthesis
- lower-cost local alternative to Nemotron

Not recommended as the global default:

- broad general assistant route
- always-on first-hop model without measuring latency and quality tradeoffs

### Step 8 — Keep retrieval support models on Spark

Do not move these off Spark:

- `nomic-embed-text:latest`
- `qllama/bge-reranker-v2-m3:latest`

They belong beside the future second-brain service.

### Step 9 — Treat Mac as future modality host

Do not rewrite the Mac deployment role in the main docs yet. But the target
direction is:

- Obsidian vault
- optional Smart Connections for human use
- future TTS service

## 5. Model Role Matrix

| Model / service | Host | Role |
|-----------------|------|------|
| `nemotron-3-super:120b` | Spark | default reasoning and synthesis |
| `gemma4:31b` | Spark | secondary strong local answer model |
| `nomic-embed-text:latest` | Spark | embeddings |
| `qllama/bge-reranker-v2-m3:latest` | Spark | reranking |
| future TTS service | Mac | speech output |

## 6. After Successful Validation

Only after the route-switch and sandbox-path checks pass should we update:

- `deployment-guide.md`
- `operations-guide.md`
- `runbook.md`
- `cookbook.md`
- `benchmarks.md`
- Phase 2 expectations if the Mac inference role changes materially

## 7. Explicit Non-Goals For This Migration

This checklist does not:

- introduce the second-brain service itself
- redesign the current Phase 2 test contract yet
- remove the Mac provider from the live system
- make `gemma4:31b` the default route automatically

Those are follow-on steps after validation.
