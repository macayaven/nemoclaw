# NemoClaw Multi-Machine Deployment: Hands-On Walkthrough

*From zero to a working multi-agent AI system across DGX Spark, Mac Studio, and Raspberry Pi*
*Deployed: March 21-22, 2026*

---

## What We Built

A complete NemoClaw system running across three machines:

- **DGX Spark** (128GB, Blackwell GPU) — runs the 120B parameter Nemotron model locally, hosts 4 isolated AI agent sandboxes, and serves as the NemoClaw control plane
- **Mac Studio** (M4 Max, 36GB) — provides fast secondary inference with Qwen3 8B, runs the OpenClaw companion app, and serves as the primary development workstation
- **Raspberry Pi** (3.7GB) — acts as the infrastructure control plane with a unified API gateway (LiteLLM), DNS resolution (Pi-hole), uptime monitoring (Uptime Kuma), and Tailscale subnet routing

All connected via Tailscale mesh VPN. Total deployment time: ~2 hours including troubleshooting.

---

## The Conceptual Model

### Why NemoClaw Exists

Running AI agents (ChatGPT-like assistants, coding agents, automation bots) directly on your machine is dangerous:

1. **No isolation** — the agent has the same filesystem/network access as your user account
2. **No privacy control** — your data may be sent to cloud APIs without your knowledge
3. **No access control** — the agent can reach any network endpoint
4. **No auditability** — you can't see what the agent did or tried to do

NemoClaw solves all four by wrapping agents in sandboxes with guardrails and routing inference through a private router.

### The Three-Layer Architecture

```
LAYER 1: SANDBOXES
  Each agent (OpenClaw, Claude Code, Codex, Gemini CLI) runs in an
  isolated container with its own filesystem, network rules, and credentials.
  Agents can't see each other or escape their sandbox.

LAYER 2: GUARDRAILS
  Access Control: Declarative YAML policies define what network endpoints
  each agent can reach. Default-deny — everything blocked unless allowed.
  Privacy: Inference routes through a private router, not directly to cloud.
  Skills: Agent capabilities are explicitly enabled per sandbox.

LAYER 3: PRIVATE INFERENCE ROUTER
  All agents call https://inference.local/v1 — a virtual endpoint.
  The router intercepts requests, injects credentials, and forwards
  to the configured model (local Ollama or cloud API).
  Local models are the DEFAULT. Cloud is opt-in only.
```

### How the Pieces Fit Together

```
Ollama (engine) → loads and serves LLMs via API
  └─→ OpenShell (platform) → manages sandboxes, providers, routing
       └─→ OpenClaw (application) → AI agent with browser UI
            └─→ NemoClaw (deployment) → your specific setup with Nemotron
```

---

## Hardware Inventory

| Machine | Specs | Role | Network |
|---------|-------|------|---------|
| DGX Spark | GB10 Blackwell, 128GB UMA, Ubuntu 24.04 ARM64 | Primary inference + gateway | Tailscale: 100.93.220.104 |
| Mac Studio | M4 Max, 36GB, macOS 15.4 | Secondary inference + dev workstation | Tailscale: 100.116.228.36 |
| Raspberry Pi | ARM64, 3.7GB RAM, Debian Bookworm | Infrastructure control plane | Tailscale: 100.85.6.21 |

---

## Phase 1: NemoClaw on DGX Spark

### Step 1: Configure Ollama

Ollama was already installed with `nemotron-3-super:120b` (86GB) and `qwen3-coder-next:q4_K_M` (51GB) downloaded.

Two critical configurations:
1. **Listen on all interfaces** — sandboxes are containers that can't reach `localhost`
2. **Keep models in VRAM** — prevents the 30-60s cold start on every idle timeout

```bash
sudo tee /etc/systemd/system/ollama.service.d/override.conf << 'EOF'
[Service]
Environment="OLLAMA_HOST=0.0.0.0"
Environment="OLLAMA_KEEP_ALIVE=-1"
EOF
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

**Verification:** `ss -tlnp | grep 11434` should show `*:11434` (all interfaces).

**Gotcha:** If it shows `127.0.0.1:11434`, the override didn't take effect. Check with `systemctl show ollama -p Environment`.

### Step 2: Start LM Studio (Headless)

LM Studio provides an Anthropic-compatible API alongside Ollama's OpenAI-compatible one.

```bash
lms server start --port 1234 --bind 0.0.0.0 --cors
```

**Gotcha:** The `llmster` daemon may already be running. Check with `ss -tlnp | grep 1234`. If it's already bound, you're good.

### Step 3: Start OpenShell Gateway

```bash
source ~/workspace/nemoclaw/openshell-env/bin/activate
openshell gateway start --recreate
```

The `--recreate` flag is important if a previous gateway exists but isn't running. The gateway bootstraps a k3s cluster inside Docker — this takes ~15 seconds.

**Verification:** `openshell status` should show `Status: Connected`.

### Step 4: Register Inference Providers

```bash
# Ollama provider (primary — Nemotron 120B)
openshell provider create \
    --name local-ollama \
    --type openai \
    --credential OPENAI_API_KEY=not-needed \
    --config OPENAI_BASE_URL=http://host.openshell.internal:11434/v1

# LM Studio provider (alternative — Anthropic-compatible)
openshell provider create \
    --name local-lmstudio \
    --type openai \
    --credential OPENAI_API_KEY=lm-studio \
    --config OPENAI_BASE_URL=http://host.openshell.internal:1234/v1
```

**Key concept:** `host.openshell.internal` is a special hostname that resolves to the gateway host from inside sandboxes. Never use `localhost` or `127.0.0.1` — containers can't reach those.

### Step 5: Set Default Inference Route

```bash
openshell inference set --provider local-ollama --model nemotron-3-super:120b
```

This tells all sandboxes: "when you call `https://inference.local/v1`, route to Ollama's Nemotron model."

**Verification:** `openshell inference get` should show the provider and model.

### Step 6: Create the OpenClaw Sandbox

```bash
openshell sandbox create \
    --keep \
    --forward 18789 \
    --name nemoclaw-main \
    --from openclaw \
    -- openclaw-start
```

- `--keep` persists the sandbox across gateway restarts
- `--forward 18789` exposes the OpenClaw UI
- `--from openclaw` pulls the community OpenClaw image with bundled policies

**Gotcha:** The `openclaw-start` script runs an interactive `openclaw onboard` wizard. If the security prompt defaults to "No", the setup doesn't complete.

### Step 7: Run OpenClaw Onboarding (Interactive)

```bash
openshell sandbox connect nemoclaw-main
# Inside sandbox:
openclaw onboard
```

The wizard asks for:
1. **Security acknowledgment** → Yes
2. **Onboarding mode** → QuickStart
3. **Provider** → Custom Provider
4. **API Base URL** → `https://inference.local/v1` (NOT `:11434`)
5. **API Key** → `ollama`
6. **Compatibility** → OpenAI-compatible
7. **Model** → `nemotron-3-super:120b`
8. **Channel** → Skip for now
9. **Search/Skills/Hooks** → Skip for now

**Critical mistake to avoid:** Using `https://inference.local:11434` instead of `https://inference.local/v1`. The `inference.local` hostname is a virtual endpoint handled by OpenShell's proxy — it doesn't need a port number. Adding `:11434` causes a 403 error.

### Step 8: Start the Gateway

```bash
# Inside sandbox:
nohup openclaw gateway run > /tmp/gateway.log 2>&1 &
```

### Step 9: Verify End-to-End

```bash
# Inside sandbox:
curl -s https://inference.local/v1/chat/completions \
    -H 'Content-Type: application/json' \
    -d '{"model":"nemotron-3-super:120b","messages":[{"role":"user","content":"Say hello"}],"max_tokens":10}'
```

**Expected:** JSON response with `choices[0].message.content` containing Nemotron's reply.

**Browser access:** `http://127.0.0.1:18789/` (or with token from onboarding output).

---

## Phase 2: Mac Studio Integration

### The Challenge

The Mac Studio's Ollama is managed by the Ollama.app GUI, which also provides models to Cursor IDE. Both bind to `127.0.0.1:11434` — not reachable from the network.

### Solution: TCP Forwarder

Since we can't change the Ollama.app's binding without disrupting Cursor, we run a simple Python TCP forwarder that exposes the local Ollama on a network-accessible port.

```bash
# On the Mac Studio (via SSH from Spark):
sshpass -p "<password>" ssh carlos@100.116.228.36

# Pull the fast model
/usr/local/bin/ollama pull qwen3:8b

# Start TCP forwarder
python3 -c "
import socket, threading
def forward(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data: break
            dst.sendall(data)
    except: pass
    src.close(); dst.close()

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(('0.0.0.0', 11435))
server.listen(50)
while True:
    client, _ = server.accept()
    upstream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    upstream.connect(('127.0.0.1', 11434))
    threading.Thread(target=forward, args=(client, upstream), daemon=True).start()
    threading.Thread(target=forward, args=(upstream, client), daemon=True).start()
" &
```

**Note:** Port 11435 (not 11434) to avoid conflict with Cursor. The forwarder proxies `0.0.0.0:11435 → 127.0.0.1:11434`.

### Register Mac Provider on Spark

```bash
# Back on the Spark:
openshell provider create \
    --name mac-ollama \
    --type openai \
    --credential OPENAI_API_KEY=not-needed \
    --config OPENAI_BASE_URL=http://100.116.228.36:11435/v1
```

### Test Provider Switching

```bash
# Switch to Mac (fast, 8B model)
openshell inference set --provider mac-ollama --model qwen3:8b

# Switch back to Spark (heavy, 120B model)
openshell inference set --provider local-ollama --model nemotron-3-super:120b
```

Switching takes ~5 seconds. No sandbox restart needed.

---

## Phase 3: Raspberry Pi Infrastructure

### LiteLLM Proxy

The Pi runs LiteLLM as a unified API gateway. Any client hitting `http://pi:4000/v1/chat/completions` gets routed to the right machine based on model name.

```bash
# On the Pi:
python3 -m venv ~/litellm-env
source ~/litellm-env/bin/activate
pip install "litellm[proxy]"

# Create config
cat > ~/litellm/config.yaml << 'EOF'
model_list:
  - model_name: "nemotron-3-super:120b"
    litellm_params:
      model: "ollama/nemotron-3-super:120b"
      api_base: "http://100.93.220.104:11434"
  - model_name: "qwen3:8b"
    litellm_params:
      model: "ollama/qwen3:8b"
      api_base: "http://100.116.228.36:11435"
EOF

# Run as systemd service
sudo systemctl enable litellm && sudo systemctl start litellm
```

### Pi-hole DNS

Provides local DNS resolution: `spark.lab`, `mac.lab`, `ai.lab`.

### Uptime Kuma

Monitors all endpoints and alerts on failures.

### Tailscale Subnet Router

```bash
sudo tailscale up --advertise-routes=192.168.1.0/24 --accept-routes
```

Makes the entire lab accessible from any Tailscale device without per-machine Tailscale installs.

---

## Phase 4: Coding Agent Sandboxes

### Creating Four Isolated Agents

```bash
# Claude Code — full policy support, browser-based auth
openshell sandbox create --keep --name claude-dev --auto-providers -- claude

# Codex — configured for local Ollama inference
openshell sandbox create --keep --name codex-dev --auto-providers -- bash

# Gemini CLI — custom install needed
openshell sandbox create --keep --name gemini-dev --auto-providers -- bash
```

### Configuring Codex for Local Inference

Inside the Codex sandbox, configure it to use Ollama directly:

```toml
# ~/.codex/config.toml
model = "nemotron-3-super:120b"
model_provider = "ollama"

[model_providers.ollama]
name = "Ollama (Spark Local)"
base_url = "http://host.openshell.internal:11434/v1"
env_key = "OLLAMA_API_KEY"
wire_api = "responses"
```

**Key insight:** Codex uses local Ollama inference — your code never leaves the Spark. Claude Code and Gemini CLI use cloud APIs (data goes to Anthropic/Google). The sandbox guardrails make this boundary explicit.

### Installing Gemini CLI in Sandbox

```bash
openshell sandbox connect gemini-dev
# Inside sandbox:
mkdir -p ~/.npm-global
npm config set prefix ~/.npm-global
export PATH=~/.npm-global/bin:$PATH
npm install -g @google/gemini-cli
echo 'export PATH=~/.npm-global/bin:$PATH' >> ~/.bashrc
```

**Gotcha:** The sandbox runs as a non-root `sandbox` user. Global npm installs fail with permission errors. Use `~/.npm-global` as the prefix.

### Final State: Four Sandboxes

```
NAME           STATUS   INFERENCE PATH              PRIVACY
nemoclaw-main  Ready    inference.local → Ollama     LOCAL (Nemotron 120B)
claude-dev     Ready    Anthropic API               CLOUD (Claude subscription)
codex-dev      Ready    Ollama direct (config.toml)  LOCAL (Nemotron 120B)
gemini-dev     Ready    Google Gemini API            CLOUD (Gemini subscription)
```

### Authentication

All agents use subscription-based auth (browser login), not API keys:
- **Claude Code:** `openshell sandbox connect claude-dev` then authenticate via browser
- **Codex:** `openshell sandbox connect codex-dev` then `codex login`
- **Gemini CLI:** `openshell sandbox connect gemini-dev` then `gemini` (authenticates via Google account)

---

## Lessons Learned

### 1. `inference.local` is a Virtual Hostname

It has no port. It's handled by OpenShell's proxy inside each sandbox. Use `https://inference.local/v1`, never `https://inference.local:11434`.

### 2. Cursor Grabs Ollama's Port

On the Mac, Cursor IDE runs its own embedded Ollama on port 11434. You can't change this without breaking Cursor. Solution: TCP forwarder on an alternate port.

### 3. Sandbox Users Have Limited Permissions

The `sandbox` user can't write to `/usr/local` or use `sudo`. Use `~/.npm-global` for npm, `~/venv` for Python, and `~/bin` for binaries.

### 4. Browser Login, Not API Keys

Modern AI subscriptions (Claude Pro, Codex, Gemini) authenticate via browser, not API keys. Use `--auto-providers` when creating sandboxes and authenticate inside.

### 5. Tailscale Simplifies Everything

With all machines on Tailscale, network configuration is trivial — use Tailscale IPs everywhere. No port forwarding, no public exposure, no DNS hacks.

### 6. The Pi Adds Real Value

Not as an SSH relay, but as an infrastructure control plane: unified API gateway, DNS, monitoring, subnet routing — all at 5W power draw.

---

## What's Next

- [ ] Phase 5: Mobile access via iOS app + Tailscale
- [ ] Phase 6: Orchestrator for inter-agent cooperation
- [ ] Replace TCP forwarder with proper Ollama binding on Mac
- [ ] Add vLLM/TensorRT-LLM as alternative inference on Spark
- [ ] Set up Grafana dashboard for GPU/inference metrics
- [ ] Write MCP servers for shared agent tools
