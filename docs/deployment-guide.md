# NemoClaw Deployment Guide

This is the definitive guide for deploying a complete NemoClaw system from scratch. Follow the phases in order — each phase builds on the previous one, and each ends with a validation step using the test suite.

---

## 1. Overview

After following this guide you will have:

- **NemoClaw gateway** running on a DGX Spark, serving as the central AI inference router
- **Mac Studio** registered as a secondary inference provider via Ollama
- **Raspberry Pi** acting as a lightweight proxy (LiteLLM), DNS resolver (Pi-hole), uptime monitor (Uptime Kuma), and Tailscale subnet router
- **Coding agent sandboxes** for Claude Code, Codex, and Gemini CLI — all routing through the NemoClaw gateway
- **Remote access** via Tailscale and the NemoClaw iOS app
- **End-to-end test coverage** validating every layer

The full stack lets you run large models on the Spark, offload to the Mac, monitor everything from the Pi, and access it all securely from anywhere via Tailscale.

---

## 2. Prerequisites

### Hardware

| Device | Role |
|---|---|
| DGX Spark | Primary inference host, NemoClaw gateway |
| Mac Studio | Secondary inference provider (Ollama) |
| Raspberry Pi (4 or 5) | Proxy, DNS, monitoring, subnet router |

### Software

Install the following on the machine you'll run commands from (typically the Spark or your laptop):

| Tool | Version | Install |
|---|---|---|
| Docker | ≥ 28.04 | https://docs.docker.com/engine/install/ |
| Ollama | latest | `curl -fsSL https://ollama.com/install.sh \| sh` |
| Node.js | ≥ 20 | https://nodejs.org/ or `nvm install 20` |
| uv | latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Python | 3.12+ | managed by `uv` |

### Network

- **SSH access** to all three machines (Spark, Mac, Pi) using the same SSH user
- **Tailscale** installed and authenticated on all three machines (`tailscale up`)
- All machines on the same LAN *or* reachable via Tailscale

### Project setup

Clone the repo and install dependencies before running any phase:

```bash
git clone https://github.com/your-org/nemoclaw.git
cd nemoclaw
cp .env.example .env
# Edit .env with your actual IPs, hostnames, and credentials
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
- SSH connectivity to Spark, Mac, and Pi
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

## 4. Phase 1 — Deploy NemoClaw on DGX Spark

This phase installs and configures the NemoClaw gateway on the DGX Spark. The gateway is the central router that all agents, clients, and providers connect to.

### Step 1 — Run the setup wizard

```bash
nemoclaw setup-spark
```

This bootstraps the Spark environment: creates directories, installs the gateway service, and generates initial configuration.

### Step 2 — Configure Ollama on the Spark

Ollama must listen on all interfaces (not just `127.0.0.1`) so the NemoClaw gateway and remote clients can reach it:

```bash
# On the Spark
sudo systemctl edit ollama --force --full
```

Add or update the `[Service]` section:

```ini
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
```

Restart Ollama:

```bash
sudo systemctl daemon-reload
sudo systemctl restart ollama
ollama list  # verify models are visible
```

### Step 3 — Start the NemoClaw gateway

```bash
nemoclaw gateway start
```

Verify it is running:

```bash
nemoclaw gateway status
# Expected: gateway running on :8080
```

### Step 4 — Register the Spark as a provider

```bash
nemoclaw provider register \
  --name spark-ollama \
  --type ollama \
  --url http://localhost:11434
```

### Step 5 — Set the default inference provider

```bash
nemoclaw inference set-default spark-ollama
```

### Step 6 — Create the default sandbox

```bash
nemoclaw sandbox create default
```

### Validate Phase 1

```bash
uv run pytest tests/phase1_core/ -v
```

All tests must pass before continuing. This suite verifies gateway health, provider registration, and basic inference round-trips.

---

## 5. Phase 2 — Integrate Mac Studio

This phase registers the Mac Studio as a secondary inference provider, letting NemoClaw route overflow requests or specific model requests to the Mac.

### Step 1 — Configure Ollama on the Mac

Create a launchd plist so Ollama starts automatically and listens on all interfaces:

```bash
# On the Mac — run this from your laptop over SSH or directly on the Mac
ssh ${SSH_USER}@${MAC_HOSTNAME} "cat > ~/Library/LaunchAgents/com.ollama.server.plist" << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.ollama.server</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/ollama</string>
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
  <string>/tmp/ollama.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/ollama.err</string>
</dict>
</plist>
EOF
```

Load the agent:

```bash
ssh ${SSH_USER}@${MAC_HOSTNAME} "launchctl load ~/Library/LaunchAgents/com.ollama.server.plist"
```

Verify Ollama is reachable from the Spark:

```bash
curl http://${MAC_IP}:11434/api/tags
```

### Step 2 — Register the Mac as a provider

Back on the Spark (or from wherever you run `nemoclaw` commands):

```bash
nemoclaw provider register \
  --name mac-ollama \
  --type ollama \
  --url http://${MAC_IP}:11434
```

### Step 3 — Install OpenClaw.app

Download and install the OpenClaw macOS app on the Mac Studio. OpenClaw provides a native UI for managing sandboxes and monitoring inference traffic.

```bash
# Download the latest release
curl -L https://github.com/your-org/nemoclaw/releases/latest/download/OpenClaw.dmg \
  -o /tmp/OpenClaw.dmg

# Mount and install
hdiutil attach /tmp/OpenClaw.dmg
cp -R /Volumes/OpenClaw/OpenClaw.app /Applications/
hdiutil detach /Volumes/OpenClaw
```

Launch OpenClaw.app and point it at your Spark gateway: `http://${SPARK_IP}:8080`.

### Validate Phase 2

```bash
uv run pytest tests/phase2_mac/ -v
```

This verifies that the Mac provider is registered, reachable, and can serve an inference request through the NemoClaw gateway.

---

## 6. Phase 3 — Set Up Raspberry Pi

The Pi provides lightweight services that complement the main inference stack: LiteLLM proxy, Pi-hole DNS, Uptime Kuma monitoring, and Tailscale subnet routing.

### Step 1 — Install LiteLLM on the Pi

SSH into the Pi:

```bash
ssh ${SSH_USER}@${PI_HOSTNAME}
```

Install LiteLLM:

```bash
pip install litellm[proxy]
```

Create the LiteLLM config at `~/litellm-config.yaml`:

```yaml
model_list:
  - model_name: spark-default
    litellm_params:
      model: ollama/llama3
      api_base: http://${SPARK_IP}:11434

  - model_name: mac-default
    litellm_params:
      model: ollama/llama3
      api_base: http://${MAC_IP}:11434

general_settings:
  master_key: "litellm-master-key-change-me"
  port: 4000
```

### Step 2 — Create a systemd service for LiteLLM

```bash
sudo tee /etc/systemd/system/litellm.service > /dev/null << 'EOF'
[Unit]
Description=LiteLLM Proxy
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi
ExecStart=/home/pi/.local/bin/litellm --config /home/pi/litellm-config.yaml
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable litellm
sudo systemctl start litellm
sudo systemctl status litellm
```

### Step 3 — Set up Pi-hole DNS

```bash
curl -sSL https://install.pi-hole.net | bash
```

Follow the interactive installer. When prompted for upstream DNS, use `1.1.1.1` and `8.8.8.8`. After installation, set a Pi-hole password:

```bash
pihole -a -p
```

Point your router's DHCP DNS setting to `${PI_IP}` so all LAN devices use Pi-hole.

### Step 4 — Install Uptime Kuma

```bash
docker run -d \
  --name uptime-kuma \
  --restart unless-stopped \
  -p 3001:3001 \
  -v uptime-kuma:/app/data \
  louislam/uptime-kuma:1
```

Access the Uptime Kuma dashboard at `http://${PI_IP}:3001` and add monitors for:
- NemoClaw gateway: `http://${SPARK_IP}:8080/health`
- Spark Ollama: `http://${SPARK_IP}:11434`
- Mac Ollama: `http://${MAC_IP}:11434`
- LiteLLM proxy: `http://${PI_IP}:4000/health`

### Step 5 — Configure Tailscale subnet routing

On the Pi, advertise your LAN subnet so Tailscale clients can reach LAN devices:

```bash
sudo tailscale up --advertise-routes=${TAILSCALE_ADVERTISED_SUBNET} --accept-dns=false
```

In the Tailscale admin console (https://login.tailscale.com/admin/machines), approve the subnet route for the Pi.

### Validate Phase 3

Exit the Pi SSH session and run from your local machine:

```bash
uv run pytest tests/phase3_pi/ -v
```

This checks LiteLLM proxy health, Pi-hole DNS resolution, Uptime Kuma reachability, and Tailscale subnet routing.

---

## 7. Phase 4 — Deploy Coding Agents

This phase creates sandboxes for three coding agents, each routing AI requests through the NemoClaw gateway.

### Claude Code sandbox (full policy)

Claude Code works out of the box with the NemoClaw gateway using the full policy:

```bash
nemoclaw sandbox create claude-code \
  --policy full \
  --provider spark-ollama
```

Test that Claude Code can reach the gateway:

```bash
nemoclaw sandbox exec claude-code -- claude --version
```

### Codex sandbox (custom policy + Ollama config)

Codex requires a custom policy and an `ollama-config.toml` pointing at the gateway:

```bash
nemoclaw sandbox create codex \
  --policy custom \
  --provider spark-ollama
```

Create the Codex Ollama config inside the sandbox:

```bash
nemoclaw sandbox exec codex -- bash -c "mkdir -p ~/.codex && cat > ~/.codex/ollama-config.toml << 'EOF'
[ollama]
base_url = \"http://${SPARK_IP}:11434\"
model = \"llama3\"
EOF"
```

### Gemini CLI sandbox (custom sandbox + policy)

Gemini CLI needs its own sandbox definition and a custom policy:

```bash
nemoclaw sandbox create gemini-cli \
  --policy custom \
  --sandbox-def gemini \
  --provider spark-ollama
```

If you have a `GEMINI_API_KEY`, inject it into the sandbox environment:

```bash
nemoclaw sandbox env set gemini-cli GEMINI_API_KEY="${GEMINI_API_KEY}"
```

### Validate Phase 4

```bash
uv run pytest tests/phase4_agents/ -v
```

This suite verifies that all three sandboxes exist, can execute commands, and successfully route an inference request through the NemoClaw gateway.

---

## 8. Phase 5 — Enable Remote Access

This phase configures secure remote access via Tailscale and the NemoClaw iOS app.

### Step 1 — Configure the Tailscale gateway on the Spark

```bash
nemoclaw gateway configure-tailscale \
  --tailscale-ip ${SPARK_TAILSCALE_IP} \
  --port 8080
```

Verify the gateway is reachable via the Tailscale IP from another device on your Tailnet:

```bash
curl http://${SPARK_TAILSCALE_IP}:8080/health
```

### Step 2 — Install the iOS app

Download the NemoClaw iOS app from the App Store (search "NemoClaw") or via TestFlight if you are on a pre-release build.

Configure the app:
1. Open NemoClaw on iOS
2. Go to Settings → Gateway
3. Enter `http://${SPARK_TAILSCALE_IP}:8080`
4. Tap "Connect" — you should see the gateway status turn green

Ensure Tailscale is running on your iPhone (`tailscale` app installed and connected to your Tailnet).

### Validate Phase 5

```bash
uv run pytest tests/phase5_mobile/ -v
```

This verifies that the gateway is reachable on the Tailscale IP and that the Tailscale subnet route from the Pi is functioning end-to-end.

---

## 9. Post-Deployment

### Provider switching cheatsheet

| Command | Effect |
|---|---|
| `nemoclaw inference set-default spark-ollama` | Route all requests to the Spark |
| `nemoclaw inference set-default mac-ollama` | Route all requests to the Mac |
| `nemoclaw provider list` | Show all registered providers and their status |
| `nemoclaw provider status spark-ollama` | Check a specific provider |

### Monitoring with `openshell term`

Open a live terminal session with any sandbox:

```bash
openshell term claude-code   # attach to Claude Code sandbox
openshell term codex          # attach to Codex sandbox
openshell term gemini-cli     # attach to Gemini CLI sandbox
```

### Day-to-day usage

- **Check gateway health**: `nemoclaw gateway status`
- **View active sandboxes**: `nemoclaw sandbox list`
- **Tail gateway logs**: `nemoclaw gateway logs -f`
- **Restart gateway**: `nemoclaw gateway restart`
- **Update a provider URL**: `nemoclaw provider update mac-ollama --url http://${MAC_IP}:11434`
- **Run the full test suite**: `uv run pytest tests/ -v`

---

## 10. Troubleshooting

### Ollama only listening on 127.0.0.1

**Symptom**: `curl http://${SPARK_IP}:11434` times out but `curl http://127.0.0.1:11434` works.

**Fix**: Set `OLLAMA_HOST=0.0.0.0:11434` in the Ollama service environment (see Phase 1, Step 2) and restart the service.

### Gateway timeout

**Symptom**: `nemoclaw gateway status` returns a timeout or connection refused.

**Fix**:
1. Check the gateway is running: `nemoclaw gateway start`
2. Check the port is not blocked: `sudo ss -tlnp | grep 8080`
3. Check firewall rules: `sudo ufw status` — allow port 8080 if needed: `sudo ufw allow 8080/tcp`

### Port conflicts

**Symptom**: Gateway fails to start with "address already in use".

**Fix**: Find and stop the conflicting process:

```bash
sudo ss -tlnp | grep 8080
# Note the PID, then:
sudo kill <PID>
nemoclaw gateway start
```

To change the gateway port permanently:

```bash
nemoclaw gateway configure --port 8090
```

### Sandbox creation fails

**Symptom**: `nemoclaw sandbox create` exits with an error.

**Fix**:
1. Ensure Docker is running: `docker info`
2. Ensure your user is in the `docker` group: `groups $USER` — if not, `sudo usermod -aG docker $USER` and log out/in
3. Check available disk space: `df -h` — Docker needs at least a few GB free
4. Check gateway is up before creating sandboxes: `nemoclaw gateway status`

### SSH connection refused to Mac or Pi

**Symptom**: Phase 0 SSH tests fail for Mac or Pi.

**Fix**:
1. Verify the IP in `.env` is correct: `ping ${MAC_IP}`
2. Ensure SSH is enabled on the Mac: System Settings → General → Sharing → Remote Login
3. Ensure SSH is enabled on the Pi: `sudo raspi-config` → Interface Options → SSH
4. Test manually: `ssh ${SSH_USER}@${MAC_IP}` — if it prompts for a password, consider setting up key-based auth

### Tailscale peer not reachable

**Symptom**: Tailscale IP is unreachable from another device.

**Fix**:
1. Check Tailscale status on the target machine: `tailscale status`
2. Ensure the machine is logged into the same Tailnet: `tailscale up`
3. Check the Tailscale admin console for device approval if your network requires it
