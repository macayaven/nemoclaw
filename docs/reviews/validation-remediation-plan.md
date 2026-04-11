# NemoClaw Validation Remediation Plan
*Date: 2026-04-11*
*Author: Codex CLI*
*Scope: Stabilize phase validation, de-scope Raspberry Pi from the default topology, and align tests/docs with the current Spark + Mac deployment*

## Goals

1. Make the documented default deployment match the real supported system.
2. Restore trustworthy phase validation for the active topology.
3. Preserve strong test coverage without forcing optional infrastructure into the mainline path.
4. Prefer clean interface fixes over brittle environment-specific patches.

## Executive Decision

The Raspberry Pi should be removed from the default deployment, docs, and phase flow for now.

Rationale:

- The current working system is Spark + Mac.
- The Pi is repurposed and no longer part of the reliable operator path.
- The repository already frames the Pi topology as optional or legacy in parts of the documentation.
- Keeping Pi in the default validation path creates avoidable false negatives and operator overhead.

The Pi-related work should remain available as an optional legacy/advanced topology, gated explicitly in docs and tests.

## Current Failure Map

### 1. SSH and host-auth failures block live phase validation

Symptoms:

- `ssh spark-caeb.local` fails with public key auth errors.
- `ssh mac-studio.local` fails with public key auth errors.
- `ssh-add -L` reports no active authentication agent.
- Fabric/Paramiko fixtures fail before meaningful assertions.

Impact:

- Phase 0, 1, 2, and 4 fail at setup instead of reporting host state.

### 2. The Pi topology is not available

Symptoms:

- `raspi.local` does not resolve.
- Pi-backed DNS and LiteLLM tests fail.

Impact:

- Phase 3 cannot currently validate anything meaningful.

### 3. Gateway tests do not match the current security model

Symptoms:

- Direct HTTPS probing of `https://<spark-ip>:8080` fails with a TLS certificate-required alert.
- `openshell status` succeeds through the supported client path.

Impact:

- Tests conflate "not publicly reachable" with "not healthy."

### 4. Mobile/Tailscale tests do not match the current access path

Symptoms:

- `http://<spark-tailscale-ip>:18789` is refused.
- `http://127.0.0.1:18789` works locally.
- `https://spark-caeb.tail48bab7.ts.net/` works through Tailscale Serve.

Impact:

- Phase 5 fails even though the intended remote access path appears to be working.

### 5. Ollama response validation has drifted from the real API

Symptoms:

- The test model expects `size_gb`.
- The actual Ollama `/api/tags` response returns `size` in bytes plus nested `details`.

Impact:

- Phase 2 reports a schema failure even though the Mac Ollama endpoint is up and serving tags.

## Remediation Tracks

### Track A — Restore host access prerequisites

Objective:
Make Spark and Mac reachable non-interactively from the test runner.

Tasks:

1. Update `tests/.env` usage and fixture behavior so explicit SSH key paths are first-class.
2. Make preflight/auth failures produce targeted diagnostics instead of deep Paramiko stack traces.
3. Verify non-interactive SSH to Spark and Mac before running any live phase suites.

Implementation notes:

- Prefer explicit key configuration over implicit ssh-agent dependence.
- Keep support for ssh-agent, but do not require it.
- Surface which host failed, which auth paths were attempted, and what the operator should configure next.

Acceptance criteria:

- `ssh -o BatchMode=yes spark-caeb.local true` succeeds.
- `ssh -o BatchMode=yes mac-studio.local true` succeeds.
- `tests/phase0_preflight` reaches real assertions for Spark and Mac.

### Track B — Remove Pi from the default topology cleanly

Objective:
Standardize the default deployment as Spark + Mac, while preserving Pi material as optional.

Tasks:

1. Update the primary docs to describe Spark + Mac as the default supported topology.
2. Mark Pi guidance as optional legacy/advanced infrastructure.
3. Gate Pi tests behind explicit configuration or an opt-in marker.
4. Ensure phase numbering and descriptions remain consistent after de-scoping Pi from the mainline narrative.

Files likely affected:

- `README.md`
- `docs/INDEX.md`
- `docs/deployment-guide.md`
- `docs/operations-guide.md`
- `docs/runbook.md`
- `nemoclaw-tdd-plan.md`
- `tests/settings.py`
- `tests/conftest.py`
- `tests/phase3_pi/*`

Implementation notes:

- Do not delete Pi tests immediately; reclassify them as optional first.
- Avoid ambiguous wording like "required later" unless the repo actually depends on it.

Acceptance criteria:

- A new operator can deploy and validate Spark + Mac only.
- Docs consistently say Pi is optional/legacy.
- The default validation flow does not fail just because Pi is absent.

### Track C — Align phase tests with the actual gateway and access model

Objective:
Make Phase 1 and Phase 5 validate the supported operator-facing interfaces.

Tasks:

1. Refactor gateway health tests to use supported control-plane checks instead of raw unauthenticated HTTPS assumptions.
2. Preserve security assertions by explicitly testing that unsupported access paths are rejected when appropriate.
3. Update mobile/Tailscale tests to validate the documented remote entrypoint.

Files likely affected:

- `tests/phase1_core/test_gateway.py`
- `tests/phase5_mobile/test_tailscale_gateway.py`
- corresponding docs in `docs/deployment-guide.md`, `docs/operations-guide.md`, and `docs/runbook.md`

Implementation notes:

- Separate "healthy through supported client path" from "reachable over arbitrary network path."
- If Tailscale Serve URL is the intended mobile path, the tests and docs should say so plainly.

Acceptance criteria:

- Phase 1 no longer fails because the gateway is correctly locked down.
- Phase 5 validates the actual mobile access method users are expected to use.

### Track D — Fix Ollama API contract drift

Objective:
Bring the local schema model in line with the current Ollama API without weakening validation.

Tasks:

1. Update `OllamaModelInfo` to accept current `/api/tags` payload fields.
2. Derive `size_gb` from `size` bytes as a convenience rather than requiring it on the wire.
3. Preserve validation of model name and nested details.

Files likely affected:

- `tests/models.py`
- `tests/phase2_mac/test_mac_ollama.py`

Implementation notes:

- Model the wire format faithfully.
- Add computed helpers if the tests want human-friendly fields.
- Avoid overfitting to one machine's model list.

Acceptance criteria:

- The Mac `/api/tags` response validates cleanly.
- Phase 2 health checks fail only on real deployment issues, not schema mismatch.

### Track E — Clarify validation layers

Objective:
Distinguish repo-local correctness from live deployment correctness.

Tasks:

1. Document which phases are repo-local and which require lab infrastructure.
2. Keep Phase 6 explicitly runnable without the full lab.
3. Add a recommended validation order that starts with local/offline coverage, then moves to live infra.

Files likely affected:

- `README.md`
- `docs/INDEX.md`
- `docs/e2e-test-guide.md`
- `docs/deployment-guide.md`

Acceptance criteria:

- Contributors can run local/orchestrator tests without the home lab.
- Operators have a clear live validation sequence for Spark + Mac.

## Execution Order

### Stage 1 — Unblock the validation harness

1. Improve SSH/auth diagnostics in the test harness.
2. Restore Spark and Mac host access.
3. Re-run Phase 0 for Spark + Mac only.

### Stage 2 — De-scope the Pi from the default path

1. Update docs and phase descriptions.
2. Gate Pi tests as optional.
3. Re-run documentation-consistent validation commands.

### Stage 3 — Align tests with the actual deployment

1. Fix Ollama schema drift.
2. Fix gateway tests.
3. Fix mobile/Tailscale tests.

### Stage 4 — Revalidate active deployment

Run, in order:

1. `tests/phase0_preflight`
2. `tests/phase1_core`
3. `tests/phase2_mac`
4. `tests/phase4_agents`
5. `tests/phase5_mobile`
6. `tests/phase6_orchestrator`

Pi validation remains optional:

1. `tests/phase3_pi` only when the Pi topology is intentionally configured again

## Peer Review Assignments

These are bounded read-only review tasks suitable for parallel peer-agent analysis:

### Claude Code

Focus:

- SSH/preflight harness design
- how to produce clearer operator-facing failures
- whether the proposed gating of optional topology is clean

Expected output:

- critique of fixture design
- recommendations for auth/config ergonomics
- edge cases in preflight behavior

### Gemini CLI

Focus:

- docs and phase-flow cleanup
- consistency between README, deployment guide, runbook, and index
- how to present the Pi as optional without confusing phase progression

Expected output:

- doc consistency issues
- wording/structure recommendations
- missing references or contradictory guidance

### OpenCode / GLM 5.1

Focus:

- runtime/test alignment
- gateway/mobile path expectations
- whether current tests are asserting implementation details instead of stable operator contracts

Expected output:

- list of test assumptions that should change
- contract vs implementation guidance
- suggested acceptance criteria for each live phase

## Definition of Done

This remediation is complete when all of the following are true:

1. Spark + Mac is the documented default deployment.
2. Pi is clearly optional and no longer blocks default validation.
3. Host-access failures produce actionable messages.
4. Phase 1, 2, 4, 5, and 6 reflect the real deployment model.
5. The remaining failures, if any, represent real deployment defects rather than stale assumptions.
