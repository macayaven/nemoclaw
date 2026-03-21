# NemoClaw Action Plan

*DGX Spark + Mac Studio + Raspberry Pi*
*March 2026 • carlos@mac-studio.local*

---

## Game Plan Overview

```
Phase 0 ─── Pre-flight checks and Spark preparation     (~15 min)
Phase 1 ─── NemoClaw on DGX Spark (the core)            (~30 min)
Phase 2 ─── Mac Studio integration (companion + remote)  (~20 min)
Phase 3 ─── Raspberry Pi infrastructure plane            (~30 min)
Phase 4 ─── Coding agent sandboxes (Claude/Codex/Gemini)  (~30 min)
Phase 5 ─── Mobile access + Tailscale hardening          (~10 min)
```

Each phase is independently usable. Phase 1 alone gives you a working NemoClaw.

---

## Phase 0 — Pre-flight Checks (~15 min)

> **Goal:** Verify all prerequisites on all machines before making changes.
> **Exit test:** All checks pass.

### 0.1 DGX Spark Checks

**Machine:** spark-caeb.local

```bash
# Disk space (need at least 100GB free for sandbox images + model headroom)
df -h /

# Docker version (OpenShell requires ≥28.04)
docker --version

# Docker is running
docker info > /dev/null 2>&1 && echo "Docker OK" || echo "Docker NOT running"

# NVIDIA Container Runtime configured
nvidia-ctk --version 2>/dev/null || echo "nvidia-ctk not found"

# Ollama installed and models present
ollama --version
ollama list | grep -E "nemotron|qwen3-coder"

# Kernel supports Landlock (required for full OpenShell security)
cat /sys/kernel/security/lsm | grep landlock && echo "Landlock OK" || echo "No Landlock"

# Kernel supports seccomp (required)
grep SECCOMP /boot/config-$(uname -r) 2>/dev/null || echo "Check seccomp manually"

# cgroup v2 (required by NemoClaw on Spark)
stat -f /sys/fs/cgroup 2>/dev/null; mount | grep cgroup

# Tailscale connected
tailscale status | head -5

# Node.js version (NemoClaw requires ≥20)
node --version
npm --version
```

### 0.2 Mac Studio Checks

**Machine:** mac-studio.local

```bash
# Docker Desktop or Colima available
docker --version

# Ollama installed
ollama --version

# Node.js available (for OpenClaw.app)
node --version

# Tailscale connected
tailscale status | head -5

# SSH to Spark works
ssh spark-caeb.local "echo SSH OK"
```

### 0.3 Raspberry Pi Checks

**Machine:** raspi.local

```bash
# Available RAM
free -h

# Python3 available (for LiteLLM)
python3 --version

# Tailscale connected
tailscale status | head -5

# Network connectivity to Spark and Mac
ping -c 1 spark-caeb.local
ping -c 1 mac-studio.local
```

---

## Phase 1 — NemoClaw on DGX Spark (~30 min)

> **Goal:** Working NemoClaw deployment on the Spark with Nemotron 3 Super 120B.
> **Exit test:** `nemoclaw my-assistant status` shows healthy; chat works via `openclaw tui`.

### 1.1 Run the Spark-specific Setup

**Machine:** spark-caeb.local
**Why:** NemoClaw provides a dedicated `setup-spark` command that fixes cgroup v2 configuration and Docker settings specific to DGX OS.

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

### 1.2 Configure Ollama for Sandbox Access

**Machine:** spark-caeb.local
**Why:** Sandboxes run inside containers — they can't reach localhost. Ollama must listen on all interfaces. We also set keep-alive to prevent the 120B model from unloading after idle timeout.

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

### 1.3 Register Local Ollama as Provider

**Machine:** spark-caeb.local
**Why:** This tells OpenShell where the local inference engine is. Use `host.openshell.internal` — the special hostname that resolves to the gateway host from inside sandboxes.

```bash
openshell provider create \
    --name local-ollama \
    --type openai \
    --credential OPENAI_API_KEY=not-needed \
    --config OPENAI_BASE_URL=http://host.openshell.internal:11434/v1
```

### 1.4 Set Inference Route to Nemotron

**Why:** Route all sandbox inference requests to the local Ollama running Nemotron.

```bash
openshell inference set \
    --provider local-ollama \
    --model nemotron-3-super:120b

# Verify
openshell inference get
```

### 1.5 Create the NemoClaw Sandbox

**Why:** This deploys OpenClaw inside an isolated sandbox with all NemoClaw guardrails active.

```bash
openshell sandbox create \
    --keep \
    --forward 18789 \
    --name nemoclaw-main \
    --from openclaw \
    -- openclaw-start
```

### 1.6 Verify End-to-End

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

### 1.7 Pre-warm Nemotron

```bash
# Outside the sandbox — load the model into GPU memory
curl http://$(hostname -I | awk '{print $1}'):11434/api/generate \
    -d '{"model": "nemotron-3-super:120b", "prompt": "hello", "stream": false}'
```

### 1.8 Start the Monitoring TUI

```bash
# In a separate terminal/tmux pane — real-time monitoring
openshell term
```

This shows live sandbox activity, policy decisions, and network request approvals.

**Phase 1 DONE** — NemoClaw is live on the Spark with full guardrails.

---

## Phase 2 — Mac Studio Integration (~20 min)

> **Goal:** Use the Mac as the primary interaction point with OpenClaw.app companion, and as a secondary inference backend.
> **Exit test:** OpenClaw.app connects to Spark gateway; fast model works from Mac Ollama.

### 2.1 Start Ollama on Mac with All-Interface Binding

**Machine:** mac-studio.local

```bash
# Option A: Manual start
OLLAMA_HOST=0.0.0.0 ollama serve &

# Option B: Permanent via launchd (recommended)
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

### 2.2 Pull Fast Models for Mac

```bash
# Primary fast model (upgraded from qwen2.5:7b)
ollama pull qwen3:8b

# Optional: even faster for simple tasks
ollama pull qwen3.5:4b
```

### 2.3 Register Mac Ollama as Provider on Spark

**Machine:** spark-caeb.local

```bash
MAC_IP=$(ssh mac-studio.local "ipconfig getifaddr en0")

openshell provider create \
    --name mac-ollama \
    --type openai \
    --credential OPENAI_API_KEY=not-needed \
    --config OPENAI_BASE_URL=http://$MAC_IP:11434/v1
```

### 2.4 Access NemoClaw UI from Mac

Open the browser on the Mac and navigate to:
- **LAN**: `http://spark-caeb.local:18789`
- **NVIDIA Sync**: Use the NVIDIA Sync app's OpenClaw entry
- **Tailscale**: `http://<spark-tailscale-ip>:18789`

### 2.5 Install OpenClaw.app on Mac

**Machine:** mac-studio.local

Install the macOS companion app (OpenClaw.app). Configure it to connect to the Spark gateway:

- **Connection**: WebSocket to `spark-caeb.local:18789`
- **Tailscale mode**: Use Tailscale IP if connecting remotely
- Grant permissions: Notifications, Accessibility, Screen Recording, Microphone, Speech Recognition

The companion app gives you:
- Menu bar quick access
- Voice wake (trigger phrase)
- Native macOS notifications
- Screen capture and camera as agent tools
- AppleScript automation exposure

### 2.6 Remote Gateway Management from Mac

**Machine:** mac-studio.local

```bash
# Manage the Spark's gateway remotely without SSHing manually
openshell gateway start --remote carlos@spark-caeb.local

# Or register the existing Spark gateway
openshell gateway add ssh://carlos@spark-caeb.local:8080
```

### 2.7 Test Provider Switching

```bash
# Switch to Mac fast model
openshell inference set --provider mac-ollama --model qwen3:8b

# Send a test message — should respond faster (smaller model)

# Switch back to Spark heavy model
openshell inference set --provider local-ollama --model nemotron-3-super:120b
```

**Phase 2 DONE** — Mac is your interface, Spark is your brain.

---

## Phase 3 — Raspberry Pi Infrastructure Plane (~30 min)

> **Goal:** Pi provides unified API gateway, DNS, monitoring, and Tailscale subnet routing.
> **Exit test:** `curl http://ai.lab:4000/v1/models` returns models from both Spark and Mac.

### 3.1 Install LiteLLM Proxy

**Machine:** raspi.local
**Why:** LiteLLM gives you a single `ai.lab:4000` endpoint that routes to the right machine based on model name.

```bash
# Install Python dependencies
pip3 install litellm[proxy]

# Create config
mkdir -p ~/litellm
cat > ~/litellm/config.yaml << 'EOF'
model_list:
  # Heavy models → DGX Spark
  - model_name: "nemotron-3-super:120b"
    litellm_params:
      model: "ollama/nemotron-3-super:120b"
      api_base: "http://spark-caeb.local:11434"

  - model_name: "qwen3-coder-next:q4_K_M"
    litellm_params:
      model: "ollama/qwen3-coder-next:q4_K_M"
      api_base: "http://spark-caeb.local:11434"

  - model_name: "nemotron-3-nano-30b"
    litellm_params:
      model: "ollama/nemotron-3-nano-30b-a3b"
      api_base: "http://spark-caeb.local:11434"

  # Fast models → Mac Studio
  - model_name: "qwen3:8b"
    litellm_params:
      model: "ollama/qwen3:8b"
      api_base: "http://mac-studio.local:11434"

  - model_name: "qwen3.5:4b"
    litellm_params:
      model: "ollama/qwen3.5:4b"
      api_base: "http://mac-studio.local:11434"

router_settings:
  routing_strategy: "usage-based-routing"
  enable_fallbacks: true
EOF
```

### 3.2 Create LiteLLM Systemd Service

```bash
sudo tee /etc/systemd/system/litellm.service << 'EOF'
[Unit]
Description=LiteLLM Proxy
After=network.target

[Service]
User=carlos
ExecStart=/usr/local/bin/litellm --config /home/carlos/litellm/config.yaml --port 4000 --host 0.0.0.0
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable litellm
sudo systemctl start litellm
```

### 3.3 Set Up DNS (Pi-hole or CoreDNS)

```bash
# Install Pi-hole
curl -sSL https://install.pi-hole.net | bash

# Add local DNS entries in Pi-hole admin UI:
#   spark.lab    → <spark IP>
#   mac.lab      → <mac IP>
#   ai.lab       → <pi IP>  (points to LiteLLM)
```

### 3.4 Install Uptime Kuma

```bash
# Install via Node.js
npm install -g uptime-kuma
uptime-kuma &

# Or via Docker (if installed)
# docker run -d --restart=always -p 3001:3001 louislam/uptime-kuma

# Configure monitors:
#   Spark Ollama:     http://spark-caeb.local:11434
#   Spark OpenShell:  https://spark-caeb.local:8080
#   Spark NemoClaw:   http://spark-caeb.local:18789
#   Mac Ollama:       http://mac-studio.local:11434
#   LiteLLM:          http://localhost:4000/health
```

### 3.5 Configure Tailscale Subnet Router

```bash
# Advertise the home network subnet
sudo tailscale up --advertise-routes=192.168.1.0/24 --accept-routes

# In Tailscale admin console: approve the subnet route
```

This means any device on your Tailscale network can reach `spark-caeb.local`, `mac-studio.local`, and `ai.lab` without having Tailscale installed on those machines individually.

### 3.6 Verify

```bash
# Test LiteLLM routing
curl http://ai.lab:4000/v1/models

# Test inference through LiteLLM → Spark
curl http://ai.lab:4000/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"model": "nemotron-3-super:120b", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 10}'

# Test inference through LiteLLM → Mac
curl http://ai.lab:4000/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"model": "qwen3:8b", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 10}'
```

**Phase 3 DONE** — Pi is your infrastructure control plane.

---

## Phase 4 — Coding Agent Sandboxes (~30 min)

> **Goal:** Run Claude Code, Codex, and Gemini CLI alongside OpenClaw in separate sandboxes on the Spark.
> **Exit test:** Four sandboxes running simultaneously, each with its own policies and inference paths.

### 4.1 Create Claude Code Sandbox

**Machine:** spark-caeb.local
**Why:** Claude Code is the best-supported OpenShell agent — full policy coverage out of the box. It uses Anthropic's API for inference (cloud), but can also be configured to use `inference.local` for local model routing.

```bash
# Register Anthropic provider (auto-discovers from env)
export ANTHROPIC_API_KEY="sk-ant-..."
openshell provider create --name anthropic --type claude --from-existing

# Create sandbox — Claude Code is fully supported, no custom policy needed
openshell sandbox create \
    --keep \
    --name claude-dev \
    --provider anthropic \
    -- claude
```

**Verify:**
```bash
openshell sandbox connect claude-dev
# Inside sandbox: claude should start and authenticate via browser
```

### 4.2 Create Codex Sandbox

**Why:** Codex has native Ollama support — it can talk directly to the Spark's Ollama without going through `inference.local`. This gives you local-first coding with models like Nemotron or Qwen Coder. Codex requires a custom network policy (not included by default).

```bash
# Register OpenAI provider (needed for Codex auth, even if using Ollama for inference)
export OPENAI_API_KEY="sk-..."
openshell provider create --name openai-codex --type codex --from-existing

# Create sandbox
openshell sandbox create \
    --keep \
    --name codex-dev \
    --provider openai-codex \
    -- codex
```

**Configure Codex to use local Ollama inside the sandbox:**
```bash
# Connect to sandbox
openshell sandbox connect codex-dev

# Inside sandbox — configure Codex to use Spark's Ollama directly
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

[model_providers.openai]
name = "OpenAI"
base_url = "https://api.openai.com/v1"
env_key = "OPENAI_API_KEY"

[features]
memories = true
TOML
```

**Custom network policy for Codex** (needed because no default policy exists):
```bash
# Outside the sandbox — get and modify the policy
openshell policy get codex-dev --full > /tmp/codex-policy.yaml

# Add OpenAI and Ollama endpoints to network_policies section:
# - name: openai_api
#   destination:
#     host: api.openai.com
#     port: 443
#   tls: terminate
#   enforcement: enforce
#   allowed_binaries: [/usr/local/bin/codex]
#
# - name: ollama_local
#   destination:
#     host: host.openshell.internal
#     port: 11434
#   enforcement: enforce
#   allowed_binaries: [/usr/local/bin/codex, /usr/bin/curl]

openshell policy set codex-dev --policy /tmp/codex-policy.yaml --wait
```

### 4.3 Create Gemini CLI Sandbox

**Why:** Gemini CLI is NOT a listed OpenShell agent, so it needs a custom sandbox. It uses Google's Gemini API (not OpenAI-compatible), meaning it always calls cloud — but the sandbox guardrails control exactly which Google endpoints it can reach.

```bash
# Register a generic provider for the Gemini API key
openshell provider create \
    --name gemini \
    --type generic \
    --credential GEMINI_API_KEY="${GEMINI_API_KEY}"

# Create sandbox from base image (Gemini isn't a community sandbox)
# We install Gemini CLI inside the sandbox
openshell sandbox create \
    --keep \
    --name gemini-dev \
    --provider gemini \
    -- bash
```

**Install Gemini CLI inside the sandbox:**
```bash
# Connect to sandbox
openshell sandbox connect gemini-dev

# Inside sandbox — install Gemini CLI
npm install -g @google/gemini-cli

# Configure Gemini
mkdir -p ~/.gemini
cat > ~/.gemini/settings.json << 'JSON'
{
  "tools": {
    "sandbox": false
  }
}
JSON

# Test it
gemini -p "hello"
```

**Custom network policy for Gemini CLI:**
```bash
# Outside the sandbox — add Google API endpoints
openshell policy get gemini-dev --full > /tmp/gemini-policy.yaml

# Add to network_policies:
# - name: gemini_api
#   destination:
#     host: generativelanguage.googleapis.com
#     port: 443
#   tls: terminate
#   enforcement: enforce
#   allowed_methods: [GET, POST]
#   allowed_binaries: [/usr/local/bin/node]
#
# - name: gemini_auth
#   destination:
#     host: oauth2.googleapis.com
#     port: 443
#   tls: terminate
#   enforcement: enforce
#   allowed_methods: [GET, POST]
#   allowed_binaries: [/usr/local/bin/node]
#
# - name: gemini_ai_studio
#   destination:
#     host: aistudio.google.com
#     port: 443
#   tls: terminate
#   enforcement: enforce
#   allowed_methods: [GET, POST]
#   allowed_binaries: [/usr/local/bin/node]

openshell policy set gemini-dev --policy /tmp/gemini-policy.yaml --wait
```

**Alternative — use `openshell term` for interactive approval:**
Instead of pre-configuring all Google endpoints, you can use the TUI to approve them as Gemini CLI requests them:
```bash
# In a separate terminal
openshell term
# As Gemini CLI makes requests, the TUI will prompt you to approve/deny each endpoint
```

### 4.4 Configure Shared MCP Servers

**Why:** All four agents (OpenClaw, Claude Code, Codex, Gemini CLI) support MCP. By configuring shared MCP servers, you give all agents access to the same tools without duplicating setup.

Useful shared MCP servers:
```bash
# Inside each sandbox, configure MCP for GitHub
# Claude Code: ~/.claude/settings.json
# Codex: ~/.codex/config.toml [mcp_servers] section
# Gemini CLI: ~/.gemini/settings.json mcpServers section
# OpenClaw: via OpenClaw settings

# Example: filesystem MCP server (gives agents controlled file access)
# Example: GitHub MCP server (code operations)
# Example: database MCP server (data access)
```

### 4.5 Manage All Sandboxes

```bash
# List all running sandboxes
openshell sandbox list

# Connect to specific sandboxes
openshell sandbox connect nemoclaw-main   # OpenClaw (NemoClaw)
openshell sandbox connect claude-dev      # Claude Code
openshell sandbox connect codex-dev       # Codex
openshell sandbox connect gemini-dev      # Gemini CLI

# Each has independent policies
openshell policy get nemoclaw-main --full
openshell policy get claude-dev --full
openshell policy get codex-dev --full
openshell policy get gemini-dev --full

# Monitor ALL sandboxes in the TUI (shows all activity, approvals, denials)
openshell term

# View logs per sandbox
openshell logs claude-dev --tail
openshell logs codex-dev --tail
openshell logs gemini-dev --tail
```

### Agent Inference Summary

| Agent | Sandbox | Inference path | Model | Cloud/Local |
|-------|---------|---------------|-------|-------------|
| OpenClaw | `nemoclaw-main` | `inference.local` → Ollama | nemotron-3-super:120b | **Local** |
| Claude Code | `claude-dev` | Anthropic API | claude-sonnet-4-6 | Cloud |
| Codex | `codex-dev` | Ollama direct (config.toml) | nemotron-3-super:120b | **Local** |
| Gemini CLI | `gemini-dev` | Google Gemini API | gemini-3-flash | Cloud |

**Key insight:** OpenClaw and Codex run on LOCAL inference (your data stays on the Spark). Claude Code and Gemini CLI use cloud APIs (data goes to Anthropic/Google). The guardrails ensure this boundary is explicit and controlled.

**Phase 4 DONE** — Four agents running in isolation, each with appropriate policies and inference paths.

---

## Phase 5 — Mobile Access + Tailscale Hardening (~10 min)

> **Goal:** Access NemoClaw from iPhone and harden remote access.
> **Exit test:** iOS app connects to Spark gateway via Tailscale.

### 5.1 Configure Tailscale-Native Gateway Access

**Machine:** spark-caeb.local

```bash
# Option A: Tailnet-only serve (recommended for private access)
openclaw gateway --tailscale serve

# Option B: Bind gateway to Tailscale interface
# Ensures only Tailscale devices can reach the gateway
```

### 5.2 Install iOS App

- Join the OpenClaw **TestFlight** beta (official iOS app)
- Or install **GoClaw** / **ClawOn** from the App Store (third-party)

Configure the app:
- **Discovery**: Tailscale DNS-SD (auto-discovers Spark gateway)
- **Manual**: Enter Spark's Tailscale IP + port 18789
- Grant permissions: Camera, Location, Microphone

### 5.3 Verify Mobile Access

- Open the iOS app → should discover and connect to the Spark gateway
- Send a message → should get a response from Nemotron
- Test camera/location tools (the agent can now see through your phone)

**Phase 5 DONE** — NemoClaw is accessible from everywhere.

---

## Quick Reference

### Provider Switching

```bash
# Local heavy (Nemotron 120B on Spark)
openshell inference set --provider local-ollama --model nemotron-3-super:120b

# Local fast (Qwen 3 8B on Mac)
openshell inference set --provider mac-ollama --model qwen3:8b

# Local coder (Qwen Coder on Spark)
openshell inference set --provider local-ollama --model qwen3-coder-next:q4_K_M

# NVIDIA Cloud (Nemotron via API Catalog)
openshell inference set --provider nvidia-nim --model nvidia/nemotron-3-super-120b-a12b
```

### Key URLs

| Service | URL | Notes |
|---------|-----|-------|
| NemoClaw UI | `http://spark-caeb.local:18789` | Browser chat |
| NemoClaw (Tailscale) | `http://<spark-tailscale>:18789` | Remote access |
| Spark Ollama API | `http://spark-caeb.local:11434` | Direct model access |
| Mac Ollama API | `http://mac-studio.local:11434` | Fast models |
| LiteLLM Unified API | `http://ai.lab:4000` | Routes to both |
| Uptime Kuma | `http://raspi.local:3001` | Monitoring dashboard |
| Pi-hole | `http://raspi.local/admin` | DNS management |

### Key Commands

```bash
# NemoClaw lifecycle
nemoclaw onboard                        # First-time setup wizard
nemoclaw setup-spark                    # DGX Spark optimizations
nemoclaw list                           # List all sandboxes
nemoclaw <name> connect                 # Shell into sandbox
nemoclaw <name> status                  # Health check
nemoclaw <name> logs --follow           # Stream logs
nemoclaw <name> destroy                 # Remove sandbox
nemoclaw <name> policy-add              # Add policy preset
nemoclaw <name> policy-list             # Show policies

# OpenShell management
openshell status                        # Gateway health
openshell term                          # Real-time TUI monitor
openshell gateway start                 # Start local gateway
openshell gateway start --remote user@h # Start remote gateway
openshell sandbox create --from X       # Create sandbox
openshell inference set --provider X    # Switch model
openshell inference get                 # Show active route
openshell provider list                 # List providers
openshell policy get <name> --full      # Show sandbox policy
openshell policy set <name> --policy f  # Update policy
openshell logs <name>                   # View logs
openshell doctor logs                   # Troubleshoot gateway
```

### Execution Checklist

```
Phase 0 — Pre-flight (~15 min)
  [ ] Spark: disk, Docker, NVIDIA runtime, Ollama, models, cgroup, Node.js
  [ ] Mac: Docker, Ollama, Node.js, SSH to Spark
  [ ] Pi: RAM, Python3, Tailscale, network to Spark/Mac

Phase 1 — NemoClaw on Spark (~30 min)
  [ ] 1.1  nemoclaw setup-spark + installer       ✅ nemoclaw status
  [ ] 1.2  Ollama → 0.0.0.0 + keep-alive          ✅ ss -tlnp | grep 11434
  [ ] 1.3  Register local-ollama provider          ✅ openshell provider list
  [ ] 1.4  Set inference → nemotron-3-super:120b   ✅ openshell inference get
  [ ] 1.5  Create NemoClaw sandbox                 ✅ nemoclaw nemoclaw-main status
  [ ] 1.6  Verify: TUI + CLI + inference           ✅ openclaw tui works
  [ ] 1.7  Pre-warm Nemotron                       ✅ First response received
  [ ] 1.8  Start monitoring TUI                    ✅ openshell term shows activity

Phase 2 — Mac Studio (~20 min)
  [ ] 2.1  Ollama → 0.0.0.0 (launchd)             ✅ curl mac-studio:11434
  [ ] 2.2  Pull qwen3:8b + qwen3.5:4b             ✅ ollama list
  [ ] 2.3  Register mac-ollama provider            ✅ openshell provider list
  [ ] 2.4  Access NemoClaw UI from Mac browser     ✅ Browser loads :18789
  [ ] 2.5  Install OpenClaw.app companion          ✅ Menu bar icon appears
  [ ] 2.6  Remote gateway management               ✅ openshell status from Mac
  [ ] 2.7  Test provider switching                 ✅ Both models respond

Phase 3 — Raspberry Pi (~30 min)
  [ ] 3.1  Install LiteLLM proxy                   ✅ litellm --version
  [ ] 3.2  Create systemd service                  ✅ systemctl status litellm
  [ ] 3.3  Set up DNS (Pi-hole)                    ✅ nslookup spark.lab
  [ ] 3.4  Install Uptime Kuma                     ✅ Browser loads :3001
  [ ] 3.5  Tailscale subnet router                 ✅ Remote device reaches spark
  [ ] 3.6  Verify unified API                      ✅ curl ai.lab:4000/v1/models

Phase 4 — Coding Agent Sandboxes (~30 min)
  [ ] 4.1  Claude Code sandbox (full policy)       ✅ openshell sandbox connect claude-dev
  [ ] 4.2  Codex sandbox + Ollama config           ✅ codex uses local Nemotron
  [ ] 4.3  Gemini CLI sandbox + custom policy      ✅ gemini -p "hello" works
  [ ] 4.4  Shared MCP servers configured           ✅ All agents access tools
  [ ] 4.5  All four running + monitored            ✅ openshell term shows all

Phase 5 — Mobile + Tailscale (~10 min)
  [ ] 5.1  Tailscale gateway config                ✅ Remote access works
  [ ] 5.2  iOS app installed + connected           ✅ App shows gateway
  [ ] 5.3  End-to-end mobile test                  ✅ Chat works from phone
```
