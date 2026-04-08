# NemoClaw Deployment Guide

This is the definitive guide for deploying a complete NemoClaw system from scratch. Follow the phases in order — each phase builds on the previous one, and each ends with a validation step using the test suite.

---

## 1. Overview

After following this guide you will have:

- **NemoClaw gateway** running on a DGX Spark, serving as the central AI inference router
- **Mac Studio** registered as a secondary inference provider via Ollama (Gemma 4 27B)
- **Coding agent sandboxes** for Claude Code, Codex, and Gemini CLI — all routing through the NemoClaw gateway
- **Remote access** via Tailscale and the NemoClaw iOS app
- **End-to-end test coverage** validating every layer

The full stack lets you run Nemotron 120B on the Spark, offload fast inference to Gemma 4 on the Mac, and access it all securely from anywhere via Tailscale.

---

## 2. Prerequisites

### Hardware

| Device | Role | Key Specs |
|---|---|---|
| **DGX Spark** | Primary inference host, NemoClaw gateway | NVIDIA GPU, runs Nemotron 120B |
| **Mac Studio M4 Max** | Secondary inference, operator workstation | 128GB unified memory, runs Gemma 4 27B |

### Software

Install the following on the machine you'll run commands from (typically the Spark or the Mac):

| Tool | Version | Install |
|---|---|---|
| Docker | >= 28.04 | https://docs.docker.com/engine/install/ |
| Ollama | latest | `curl -fsSL https://ollama.com/install.sh \| sh` |
| Node.js | >= 20 | https://nodejs.org/ or `nvm install 20` |
| uv | latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Python | 3.12+ | managed by `uv` |

### Network

- **SSH access** to the DGX Spark from the Mac Studio
- **Tailscale** installed and authenticated on both machines (`tailscale up`)
- Both machines on the same LAN *or* reachable via Tailscale

### Project setup

```bash
git clone https://github.com/macayaven/nemoclaw.git
cd nemoclaw
cp .env.example .env
# Edit .env with your actual IPs, hostnames, and credentials
cd tests
uv sync
```

---

## 3. Phase 0 — Validate Environment

Before deploying anything, confirm that your environment meets all requirements.

### Run pre-flight tests

```bash
uv run pytest tests/phase0_preflight/ -v
```

This checks:
- Python version is 3.12+
- Required CLI tools are on `PATH` (docker, ollama, node, uv, ssh, tailscale)
- `.env` is present and all required variables are set
- SSH connectivity to Spark and Mac
- Tailscale is up and peers are reachable

### Common fixes

| Failure | Fix |
|---|---|
| `python_version` | Run `uv python install 3.12` then `uv sync` |
| `docker not found` | Install Docker and add your user to the `docker` group: `sudo usermod -aG docker $USER` |
| `ollama not found` | Run the install script above, then `ollama serve &` to start the daemon |
| `node not found` | Install via `nvm install 20 && nvm use 20` |
| `ssh timeout: spark` | Verify `SPARK_IP` in `.env`, check firewall, ensure SSH daemon is running |
| `tailscale peer unreachable` | Run `tailscale up` on the unreachable machine; check `tailscale status` |
| `.env missing` | `cp .env.example .env` and fill in your values |

Do not proceed to Phase 1 until all Phase 0 tests pass.

---

## 4. Phase 1 — Deploy NemoClaw on DGX Spark (~30 min)

> **Goal:** Working NemoClaw deployment on the Spark with Nemotron 3 Super 120B.
> **Exit test:** `nemoclaw my-assistant status` shows healthy; chat works via `openclaw tui`.

### Step 1.1 — Run the Spark-specific setup

**Machine:** spark-caeb.local

```bash
# Download and run the NemoClaw installer
curl -fsSL https://www.nvidia.com/nemoclaw.sh | bash
```

This launches the interactive onboarding wizard which:
1. Installs Node.js if absent
2. Installs OpenShell CLI
3. Creates the OpenShell gateway
4. Registers inference providers
5. Creates the NemoClaw sandbox with policies
6. Configures inference routing

**If you need Spark-specific fixes separately:**
```bash
sudo nemoclaw setup-spark
```

### Step 1.2 — Configure Ollama for sandbox access

**Why:** Sandboxes run inside containers — they can't reach localhost. Ollama must listen on all interfaces.

```bash
sudo mkdir -p /etc/systemd/system/ollama.service.d/

sudo tee /etc/systemd/system/ollama.service.d/override.conf << 'EOF'
[Service]
Environment=OLLAMA_HOST=0.0.0.0
Environment=OLLAMA_KEEP_ALIVE=-1
EOF

sudo systemctl daemon-reload
sudo systemctl restart ollama
```

**Verify:**
```bash
ss -tlnp | grep 11434
# Expected: *:11434 (all interfaces)
```

### Step 1.3 — Register local Ollama as provider

**Why:** This tells OpenShell where the local inference engine is. Use `host.openshell.internal` — the special hostname that resolves to the gateway host from inside sandboxes.

```bash
openshell provider create \
    --name local-ollama \
    --type openai \
    --credential OPENAI_API_KEY=not-needed \
    --config OPENAI_BASE_URL=http://host.openshell.internal:11434/v1
```

### Step 1.4 — Set inference route to Nemotron

```bash
openshell inference set \
    --provider local-ollama \
    --model nemotron-3-super:120b

# Verify
openshell inference get
```

### Step 1.5 — Create the NemoClaw sandbox

```bash
openshell sandbox create \
    --keep \
    --forward 18789 \
    --name nemoclaw-main \
    --from openclaw \
    -- openclaw-start
```

### Step 1.6 — Verify end-to-end

```bash
# Check sandbox status
nemoclaw nemoclaw-main status

# Connect to the sandbox
nemoclaw nemoclaw-main connect

# Inside the sandbox, test the TUI
openclaw tui

# Or test via CLI
openclaw agent --agent main --local -m "hello" --session-id test

# Test inference routing from sandbox
curl https://inference.local/v1/chat/completions \
    --json '{"messages":[{"role":"user","content":"hello"}],"max_tokens":10}'
```

### Step 1.7 — Pre-warm Nemotron

```bash
# Load the model into GPU memory
curl http://$(hostname -I | awk '{print $1}'):11434/api/generate \
    -d '{"model": "nemotron-3-super:120b", "prompt": "hello", "stream": false}'
```

### Step 1.8 — Start the monitoring TUI

```bash
# In a separate terminal/tmux pane
openshell term
```

### Validate Phase 1

```bash
uv run pytest tests/phase1_core/ -v
```

All 25 tests must pass. This verifies gateway health, provider registration, inference routing, and sandbox lifecycle.

---

## 5. Phase 2 — Integrate Mac Studio (~20 min)

> **Goal:** Use the Mac as the primary interaction point with OpenClaw.app, and as a secondary inference backend running Gemma 4.
> **Exit test:** OpenClaw.app connects to Spark gateway; Gemma 4 inference works from Mac Ollama.

### Step 2.1 — Start Ollama on Mac with all-interface binding

**Machine:** mac-studio.local

```bash
# Permanent via launchd (recommended)
cat > ~/Library/LaunchAgents/com.ollama.serve.plist << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.ollama.serve</string>
    <key>ProgramArguments</key>
    <array>
        <string>/opt/homebrew/bin/ollama</string>
        <string>serve</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>OLLAMA_HOST</key>
        <string>0.0.0.0:11434</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/ollama.out.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/ollama.err.log</string>
</dict>
</plist>
PLIST

launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ollama.serve.plist
```

### Step 2.2 — Pull Gemma 4 model

```bash
# Primary fast model for Mac Studio
ollama pull gemma4:27b
```

### Step 2.3 — Register Mac Ollama as provider on Spark

**Machine:** spark-caeb.local

```bash
MAC_IP=$(ssh mac-studio.local "ipconfig getifaddr en0")

openshell provider create \
    --name mac-ollama \
    --type openai \
    --credential OPENAI_API_KEY=not-needed \
    --config OPENAI_BASE_URL=http://$MAC_IP:11434/v1
```

### Step 2.4 — Access NemoClaw UI from Mac

Open the browser on the Mac:
- **LAN**: `http://spark-caeb.local:18789`
- **Tailscale**: `http://<spark-tailscale-ip>:18789`

### Step 2.5 — Install OpenClaw.app on Mac

Install the macOS companion app. Configure it to connect to the Spark gateway:

- **Connection**: WebSocket to `spark-caeb.local:18789`
- **Tailscale mode**: Use Tailscale IP if connecting remotely
- Grant permissions: Notifications, Accessibility, Screen Recording, Microphone, Speech Recognition

The companion app gives you:
- Menu bar quick access
- Voice wake (trigger phrase)
- Native macOS notifications
- Screen capture and camera as agent tools
- AppleScript automation exposure

### Step 2.6 — Test provider switching

```bash
# Switch to Mac fast model
openshell inference set --provider mac-ollama --model gemma4:27b

# Send a test message — should respond faster (smaller model)

# Switch back to Spark heavy model
openshell inference set --provider local-ollama --model nemotron-3-super:120b
```

### Validate Phase 2

```bash
uv run pytest tests/phase2_mac/ -v
```

All 17 tests must pass. This verifies Mac Ollama is serving Gemma 4, provider registration works, and switching between Spark and Mac providers succeeds.

---

## 6. Phase 3 — Coding Agent Sandboxes (~30 min)

> **Goal:** Run Claude Code, Codex, and Gemini CLI alongside OpenClaw in separate sandboxes on the Spark.
> **Exit test:** Four sandboxes running simultaneously, each with its own policies and inference paths.

### Step 3.1 — Create Claude Code sandbox

**Why:** Claude Code is the best-supported OpenShell agent — full policy coverage out of the box. Uses Anthropic's API for inference (cloud).

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
openshell provider create --name anthropic --type claude --from-existing

openshell sandbox create \
    --keep \
    --name claude-dev \
    --provider anthropic \
    -- claude
```

### Step 3.2 — Create Codex sandbox

**Why:** Codex has native Ollama support — it can talk directly to the Spark's Ollama. Codex requires a custom network policy.

```bash
export OPENAI_API_KEY="sk-..."
openshell provider create --name openai-codex --type codex --from-existing

openshell sandbox create \
    --keep \
    --name codex-dev \
    --provider openai-codex \
    -- codex
```

**Configure Codex to use local Ollama inside the sandbox:**
```bash
openshell sandbox connect codex-dev

# Inside sandbox:
mkdir -p ~/.codex
cat > ~/.codex/config.toml << 'TOML'
model = "nemotron-3-super:120b"
model_provider = "ollama"
approval_policy = "on-request"
sandbox_mode = "external-sandbox"

[model_providers.ollama]
name = "Ollama (Spark Local)"
base_url = "http://host.openshell.internal:11434/v1"
env_key = "OLLAMA_API_KEY"
wire_api = "responses"

[features]
memories = true
TOML
```

### Step 3.3 — Create Gemini CLI sandbox

**Why:** Gemini CLI is NOT a listed OpenShell agent — needs a custom sandbox and network policy for Google API endpoints.

```bash
openshell provider create \
    --name gemini \
    --type generic \
    --credential GEMINI_API_KEY="${GEMINI_API_KEY}"

openshell sandbox create \
    --keep \
    --name gemini-dev \
    --provider gemini \
    -- bash
```

**Install Gemini CLI inside the sandbox:**
```bash
openshell sandbox connect gemini-dev

# Inside sandbox:
npm install -g @google/gemini-cli
gemini -p "hello"
```

### Step 3.4 — Verify all sandboxes

```bash
openshell sandbox list                    # All 4 should be Ready
openshell term                            # Real-time monitoring
```

### Agent inference summary

| Agent | Sandbox | Inference Path | Model | Cloud/Local |
|---|---|---|---|---|
| OpenClaw | `nemoclaw-main` | `inference.local` → Ollama | nemotron-3-super:120b | **Local** |
| Claude Code | `claude-dev` | Anthropic API | claude-sonnet-4-6 | Cloud |
| Codex | `codex-dev` | Ollama direct (config.toml) | nemotron-3-super:120b | **Local** |
| Gemini CLI | `gemini-dev` | Google Gemini API | gemini-3-flash | Cloud |

### Validate Phase 3

```bash
uv run pytest tests/phase4_agents/ -v
```

---

## 7. Phase 4 — Mobile Access + Tailscale Hardening (~10 min)

> **Goal:** Access NemoClaw from iPhone and harden remote access.
> **Exit test:** iOS app connects to Spark gateway via Tailscale.

### Step 4.1 — Configure Tailscale-native gateway access

**Machine:** spark-caeb.local

```bash
# Tailnet-only serve (recommended for private access)
openclaw gateway --tailscale serve
```

### Step 4.2 — Install iOS app

- Join the OpenClaw **TestFlight** beta or install from the App Store
- Configure: Enter Spark's Tailscale IP + port 18789

### Step 4.3 — Verify mobile access

- Open the iOS app — should discover and connect to the Spark gateway
- Send a message — should get a response from Nemotron

### Validate Phase 4

```bash
uv run pytest tests/phase5_mobile/ -v
```

---

## 8. Phase 5 — Orchestrator + Inter-Agent Cooperation (~30 min)

> **Goal:** Enable agents to cooperate through an orchestrator that delegates tasks across sandboxes.
> **Exit test:** `python -m orchestrator delegate --agent codex --prompt "write hello world"` returns generated code.

### Step 5.1 — Set up shared workspace

```bash
mkdir -p ~/workspace/shared-agents/{inbox,outbox,context}
mkdir -p ~/workspace/shared-agents/inbox/{openclaw,claude,codex,gemini}
mkdir -p ~/workspace/shared-agents/outbox/{openclaw,claude,codex,gemini}
```

### Step 5.2 — Install the orchestrator

```bash
cd ~/workspace/nemoclaw
pip install -e orchestrator/
```

### Step 5.3 — Test delegation

```bash
# Health check
python -m orchestrator health

# Single delegation
python -m orchestrator delegate --agent codex --prompt "Write a Python function that checks if a number is prime"

# Multi-agent pipeline: research → implement → review
python -m orchestrator pipeline \
  --steps "gemini:research,codex:implement,claude:review" \
  --prompt "Build a rate limiter for a FastAPI application"

# Parallel specialists
python -m orchestrator parallel \
  --agents "codex,claude" \
  --prompt "Optimize this function: def fib(n): return fib(n-1) + fib(n-2) if n > 1 else n"
```

### Validate Phase 5

```bash
uv run pytest tests/phase6_orchestrator/ -v
```

---

## 9. Provider Switching Cheatsheet

```bash
# Heavy model (Nemotron 120B on Spark) — default
openshell inference set --provider local-ollama --model nemotron-3-super:120b

# Fast model (Gemma 4 27B on Mac Studio)
openshell inference set --provider mac-ollama --model gemma4:27b

# NVIDIA Cloud (via API Catalog)
openshell inference set --provider nvidia-nim --model nvidia/nemotron-3-super-120b-a12b

# Check current route
openshell inference get

# List all providers
openshell provider list
```

---

## 10. Key URLs

| Service | URL | Notes |
|---|---|---|
| NemoClaw UI | `http://spark-caeb.local:18789` | Browser chat |
| NemoClaw (Tailscale) | `http://<spark-tailscale>:18789` | Remote access |
| Spark Ollama API | `http://spark-caeb.local:11434` | Nemotron 120B |
| Mac Ollama API | `http://mac-studio.local:11434` | Gemma 4 27B |

---

## 11. Troubleshooting

### Ollama only listening on 127.0.0.1

**Symptom**: `curl http://${SPARK_IP}:11434` times out but `curl http://127.0.0.1:11434` works.

**Fix**: Set `OLLAMA_HOST=0.0.0.0:11434` in the Ollama service environment (see Phase 1, Step 1.2) and restart.

### Gateway timeout

**Symptom**: `nemoclaw gateway status` returns a timeout or connection refused.

**Fix**:
1. Check the gateway is running: `openshell gateway start`
2. Check the port is not blocked: `sudo ss -tlnp | grep 8080`
3. Check firewall rules: `sudo ufw status` — allow port 8080 if needed

### Port forward dead on :18789

**Symptom**: Browser can't reach the NemoClaw UI.

**Fix**:
```bash
# Check forward status
openshell forward list

# Recreate the forward
openshell forward stop 18789 || true
openshell forward start 18789 nemoclaw-main --background

# If that fails, check if the app process is running inside the sandbox
openshell sandbox connect nemoclaw-main
# Inside: check listening ports and process list
```

### Sandbox creation fails

**Symptom**: `openshell sandbox create` exits with an error.

**Fix**:
1. Ensure Docker is running: `docker info`
2. Ensure your user is in the `docker` group: `groups $USER`
3. Check available disk space: `df -h`
4. Check gateway is up: `openshell status`

### SSH connection refused to Mac

**Symptom**: Phase 0 SSH tests fail for Mac.

**Fix**:
1. Verify the IP in `.env` is correct: `ping ${MAC_IP}`
2. Ensure SSH is enabled: System Settings → General → Sharing → Remote Login
3. Test manually: `ssh ${SSH_USER}@${MAC_IP}`
