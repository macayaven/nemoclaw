# End-to-End Test Guide

Manual and semi-automated E2E test scenarios for validating the full NemoClaw deployment.  Run these after major changes (version upgrades, config changes, new sandbox additions).

## Quick Start

```bash
# Run the scriptable subset automatically
make e2e

# Run the full security audit
make security-audit

# Run all automated tests
make test
```

## Test Scenarios

### E2E-1: Basic Chat (Scriptable)

**What it validates:** Sandbox -> inference.local -> Ollama inference pipeline.

```bash
make chat MSG="What GPU are you running on?"
# Expected: A response mentioning the GPU (e.g. GH200, Blackwell)
```

**Pass criteria:** Non-empty response from Nemotron containing coherent text.

---

### E2E-2: Model Hot-Swap (Scriptable)

**What it validates:** `make use-nemotron` / `make use-mac` switches the active inference backend.

```bash
# Switch to Mac
make use-mac
make chat-mac MSG="Say hello"
# Expected: Response from qwen3:8b on Mac

# Switch back to Spark
make use-nemotron
make chat MSG="Say hello"
# Expected: Response from nemotron-3-super on Spark
```

**Pass criteria:** Both models respond correctly after switching.

---

### E2E-3: Claude Sandbox (Manual)

**What it validates:** Claude Code operates inside its sandbox with credential isolation.

```bash
make connect-claude
# Inside sandbox:
claude "Write a hello world in Python"
# Expected: Claude responds with code

# Verify credentials are not in env:
env | grep -i api_key
# Expected: No ANTHROPIC_API_KEY visible
```

**Pass criteria:** Claude responds; no API keys visible in `env` output.

---

### E2E-4: Codex Sandbox (Manual)

**What it validates:** Codex CLI is operational inside its sandbox using inference.local.

```bash
make connect-codex
# Inside sandbox:
codex "List files in /sandbox"
# Expected: Codex responds using local inference
```

**Pass criteria:** Codex executes a command using nemotron-3-super via inference.local.

---

### E2E-5: Inter-Agent Delegation (Scriptable)

**What it validates:** Orchestrator can delegate tasks to agents via `make delegate`.

```bash
make delegate AGENT=openclaw PROMPT="Say hello in one sentence"
# Expected: Non-empty response from the openclaw agent
```

**Pass criteria:** Agent returns a non-empty response within 60 seconds.

---

### E2E-6: Multi-Agent Pipeline (Scriptable)

**What it validates:** Multi-step pipeline across agents works end-to-end.

```bash
make pipeline STEPS="gemini:research,codex:implement" PROMPT="Write a Python function that reverses a string"
# Expected: Research output from Gemini, implementation from Codex
```

**Pass criteria:** Pipeline completes with output from each step.

---

### E2E-7: Sandbox Isolation (Scriptable)

**What it validates:** Per-sandbox network policy enforcement.

```bash
make security-audit
# Expected: All security tests pass (or skip with documented reason)
```

**Pass criteria:** No security test failures in the audit output.

---

### E2E-8: WhatsApp Channel (Manual)

**What it validates:** WhatsApp -> Gateway -> Agent -> WhatsApp message flow.

```bash
make whatsapp-setup
make channels-status
# Expected: WhatsApp channel shows "connected"

# Send a message to your WhatsApp bot
# Expected: Bot responds with a coherent reply
```

**Pass criteria:** Round-trip message flow completes within 30 seconds.

---

### E2E-9: Telegram Channel (Manual)

**What it validates:** Telegram -> Gateway -> Agent -> Telegram message flow.

```bash
make telegram-setup
make channels-status
# Expected: Telegram channel shows "connected"

# Send /start to your Telegram bot
# Expected: Bot responds with a welcome message
```

**Pass criteria:** Round-trip message flow completes within 30 seconds.

---

### E2E-10: Remote Access (Manual)

**What it validates:** Tailscale -> port forward -> Gateway remote access path.

```bash
# From a remote device on the same tailnet:
curl -sf https://spark-caeb.tail48bab7.ts.net:18789/
# Expected: Gateway responds (may require device approval)

make mac-approve  # If needed
```

**Pass criteria:** Gateway UI is accessible from a Tailscale peer.

---

## Summary Matrix

| Test | What it validates | Automated? | `make` command |
|------|-------------------|------------|----------------|
| E2E-1 | Basic chat pipeline | Yes | `make e2e` |
| E2E-2 | Model hot-swap | Yes | `make use-nemotron` / `make use-mac` |
| E2E-3 | Claude sandbox + cred isolation | No | `make connect-claude` |
| E2E-4 | Codex sandbox operation | No | `make connect-codex` |
| E2E-5 | Inter-agent delegation | Yes | `make e2e` |
| E2E-6 | Multi-agent pipeline | Yes | `make pipeline` |
| E2E-7 | Sandbox isolation | Yes | `make security-audit` |
| E2E-8 | WhatsApp channel | No | `make whatsapp-setup` |
| E2E-9 | Telegram channel | No | `make telegram-setup` |
| E2E-10 | Remote access | No | `curl` from Tailscale peer |
