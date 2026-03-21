# Phase 1 Deployment Log

*Executed: 2026-03-21 on DGX Spark (spark-caeb)*

---

## Pre-deployment State

| Component | Status before |
|-----------|--------------|
| Ollama | Running on 0.0.0.0:11434, models downloaded |
| LM Studio | Installed (`lms` CLI), daemon not running |
| OpenShell | v0.0.11 installed in openshell-env venv |
| NemoClaw CLI | Installed via npm |
| Docker | 29.1.3, running |
| Node.js | v24.11.1 |
| Tailscale | Connected (100.93.220.104) |

## Steps Executed

### Step 1: Configure Ollama with keep-alive

```bash
sudo tee /etc/systemd/system/ollama.service.d/override.conf << 'EOF'
[Service]
Environment="OLLAMA_HOST=0.0.0.0"
Environment="OLLAMA_KEEP_ALIVE=-1"
EOF
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

**Result:** Ollama restarted, listening on `*:11434`.

### Step 2: Start LM Studio headless daemon

```bash
lms server start --port 1234 --bind 0.0.0.0 --cors
```

**Note:** LM Studio's `llmster` daemon was already running on port 1234. The `lms server start` command reported "Failed to verify" but the daemon was active. Verified with `ss -tlnp | grep 1234`.

**Result:** LM Studio running on `0.0.0.0:1234`.

### Step 3: Start OpenShell gateway

```bash
source ~/workspace/nemoclaw/openshell-env/bin/activate
openshell gateway start --recreate
```

**Note:** Previous gateway existed but wasn't running. Used `--recreate` to clean start. Gateway bootstrapped k3s in ~15 seconds.

**Result:** Gateway connected at `https://127.0.0.1:8080`, version `0.0.13-dev.9`.

### Step 4: Register providers

```bash
# Ollama provider
openshell provider create \
    --name local-ollama \
    --type openai \
    --credential OPENAI_API_KEY=not-needed \
    --config OPENAI_BASE_URL=http://host.openshell.internal:11434/v1

# LM Studio provider
openshell provider create \
    --name local-lmstudio \
    --type openai \
    --credential OPENAI_API_KEY=lm-studio \
    --config OPENAI_BASE_URL=http://host.openshell.internal:1234/v1
```

**Result:** Both providers registered.

### Step 5: Set inference route

```bash
openshell inference set --provider local-ollama --model nemotron-3-super:120b
```

**Result:** Route validated — endpoint `http://host.openshell.internal:11434/v1/chat/completions` confirmed reachable.

### Step 6: Create OpenClaw sandbox

```bash
openshell sandbox create \
    --keep \
    --forward 18789 \
    --name nemoclaw-main \
    --from openclaw \
    -- openclaw-start
```

**Note:** The `openclaw-start` script runs `openclaw onboard` which is an interactive wizard. The wizard defaulted to "No" on the security prompt, so it needed to be run manually.

**Result:** Sandbox created, status `Ready`, port 18789 forwarded.

### Step 7: Run OpenClaw onboarding (interactive)

Connected to sandbox and ran `openclaw onboard`:

```bash
openshell sandbox connect nemoclaw-main
# Inside sandbox:
openclaw onboard
```

**Wizard selections:**
1. Security prompt: **Yes**
2. Onboarding mode: **QuickStart**
3. Model/auth provider: **Custom Provider**
4. API Base URL: `https://inference.local/v1` (NOT `:11434` — OpenShell routes it)
5. API Key: `ollama`
6. Endpoint compatibility: **OpenAI-compatible**
7. Model ID: `nemotron-3-super:120b`
8. Endpoint ID: `custom-inference-local`
9. Channel: **Skip for now**
10. Search: **Skip for now**
11. Skills: **No**
12. Hooks: **Skip for now**

**Common mistake:** Using `https://inference.local:11434` instead of `https://inference.local/v1`. The port is wrong because `inference.local` is a virtual hostname handled by OpenShell's proxy — it doesn't need a port. Adding `:11434` causes a 403 error.

**Result:** Onboarding complete. Config saved to `/sandbox/.openclaw/openclaw.json`.

### Step 8: Start OpenClaw gateway

```bash
# Inside sandbox:
nohup openclaw gateway run > /tmp/gateway.log 2>&1 &
```

**Result:** Gateway listening on `ws://127.0.0.1:18789`, model set to `custom-inference-local/nemotron-3-super:120b`.

### Step 9: Verify end-to-end

```bash
# From inside sandbox:
curl -s https://inference.local/v1/chat/completions \
    -H 'Content-Type: application/json' \
    -d '{"model":"nemotron-3-super:120b","messages":[{"role":"user","content":"Say hello in one word"}],"max_tokens":10}'
```

**Result:** Nemotron responded with a completion. Full chain working: sandbox -> inference.local -> OpenShell gateway -> Ollama -> Nemotron 120B -> response.

## Final State

| Component | Status | Address |
|-----------|--------|---------|
| Ollama | Running | 0.0.0.0:11434 |
| LM Studio | Running | 0.0.0.0:1234 |
| OpenShell Gateway | Connected | https://127.0.0.1:8080 |
| Provider: local-ollama | Registered | host.openshell.internal:11434/v1 |
| Provider: local-lmstudio | Registered | host.openshell.internal:1234/v1 |
| Inference route | Active | nemotron-3-super:120b via local-ollama |
| Sandbox: nemoclaw-main | Ready | Port 18789 forwarded |
| OpenClaw gateway | Running | ws://127.0.0.1:18789 |
| End-to-end inference | Verified | Nemotron responded through inference.local |

## Access

| Method | URL |
|--------|-----|
| Browser (with token) | `http://127.0.0.1:18789/#token=7cfb6a0efd17c1ea4f3cda511ffd5e1528ec013d9e8c6634` |
| Browser (plain) | `http://127.0.0.1:18789/` |
| Tailscale | `http://100.93.220.104:18789/` (needs port forward verification) |

## Lessons Learned

1. **`inference.local` needs no port** — use `https://inference.local/v1`, not `:11434`. OpenShell's proxy handles routing.
2. **`openclaw-start` wizard is interactive** — if the security prompt defaults to "No", the setup doesn't complete. Must run `openclaw onboard` manually.
3. **LM Studio daemon (`llmster`)** may already be running — check `ss -tlnp | grep 1234` before trying to start.
4. **`openshell gateway start --recreate`** is needed if a previous gateway exists but isn't running.
