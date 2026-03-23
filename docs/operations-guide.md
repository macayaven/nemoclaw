# NemoClaw Operations Guide

How to start, stop, pause, restart, and manage your NemoClaw deployment day-to-day.

---

## Current State

**Your deployment status: NOT YET DEPLOYED.**

What exists so far is the validation framework (166 tests across pre-flight plus six phases), documentation, and CI/CD. To deploy, follow the [Deployment Guide](deployment-guide.md) phase by phase.

Once deployed, this guide covers ongoing operations.

---

## 1. Starting NemoClaw

### Start everything (full stack)

Run these in order. Each command assumes you are on the specified machine.

```bash
# 1. Spark — Start Ollama (if not running via systemd)
ssh spark-caeb.local
sudo systemctl start ollama
# Verify: ss -tlnp | grep 11434

# 2. Spark — Start OpenShell gateway
cd ~/workspace/nemoclaw
source openshell-env/bin/activate
openshell gateway start
# Wait ~2 minutes for k3s bootstrap
# Verify: openshell status → "Connected"

# 3. Spark — Sandbox auto-starts if created with --keep
# If not, recreate:
openshell sandbox create --keep --forward 18789 --name nemoclaw-main --from openclaw -- openclaw-start

# 4. Mac — Start Ollama (if not using launchd)
ssh mac-studio.local
OLLAMA_HOST=0.0.0.0 ollama serve &
# Or if launchd is configured, it starts automatically on boot

# 5. Pi — LiteLLM, Pi-hole, Uptime Kuma (all systemd, auto-start on boot)
# Verify:
ssh raspi.local
sudo systemctl status litellm pihole-FTL
```

### Start just the core (Spark only)

```bash
ssh spark-caeb.local
sudo systemctl start ollama
openshell gateway start
# Wait for "Connected", then:
openshell status
```

### Start a specific sandbox

```bash
# If sandbox was created with --keep, it auto-starts with the gateway.
# If you need to create a new one:
openshell sandbox create --keep --forward 18789 --name nemoclaw-main --from openclaw -- openclaw-start

# Or reconnect to an existing one:
openshell sandbox connect nemoclaw-main
```

---

## 2. Stopping NemoClaw

### Stop everything (graceful shutdown)

```bash
# 1. Spark — Stop the gateway (stops all sandboxes)
openshell gateway stop
# Sandboxes created with --keep will restart when gateway starts again

# 2. Spark — Stop Ollama (optional, frees GPU memory)
sudo systemctl stop ollama

# 3. Mac — Stop Ollama
# If running via launchd:
launchctl bootout gui/$(id -u)/com.ollama.serve
# If running manually:
pkill ollama

# 4. Pi — Stop services (usually not needed, they're lightweight)
sudo systemctl stop litellm
```

### Stop a single sandbox (keep everything else running)

```bash
# Delete a specific sandbox
openshell sandbox delete codex-dev

# Or disconnect without deleting:
# Just close the terminal / exit the sandbox shell
```

### Stop Ollama model (free GPU memory without stopping Ollama)

```bash
# Unload a specific model from GPU memory
curl http://localhost:11434/api/generate -d '{"model": "nemotron-3-super:120b", "keep_alive": 0}'
# The model will unload after the response. Next request will cold-start it.
```

---

## 3. Pausing / Suspending

### Pause inference (keep gateway running, stop model serving)

```bash
# Unload all models from GPU memory (Ollama stays running, just no models loaded)
curl http://localhost:11434/api/generate -d '{"model": "nemotron-3-super:120b", "keep_alive": 0}'

# Requests to inference.local will still work, but the first one will trigger
# a cold start (30-60s for Nemotron 120B)
```

### Pause the Mac provider (keep Spark running)

```bash
# Switch inference away from Mac — Spark handles everything
openshell inference set --provider local-ollama --model nemotron-3-super:120b

# Then stop Mac Ollama if desired:
ssh mac-studio.local "pkill ollama"
```

### Pause LiteLLM on Pi (direct access only)

```bash
ssh raspi.local "sudo systemctl stop litellm"
# Now http://ai.lab:4000 is unavailable, but direct access to
# spark:11434 and spark:18789 still works
```

---

## 4. Restarting

### Restart the gateway (preserves provider config)

```bash
openshell gateway stop
openshell gateway start
# Wait ~2 min for k3s
openshell status
# Sandboxes with --keep auto-restore
```

### Restart Ollama (reloads model)

```bash
sudo systemctl restart ollama
# Model will cold-start on next request
```

### Restart a sandbox

```bash
openshell sandbox delete nemoclaw-main
openshell sandbox create --keep --forward 18789 --name nemoclaw-main --from openclaw -- openclaw-start
```

### Full restart (nuclear option)

```bash
# Destroy everything and start fresh
openshell gateway destroy  # WARNING: deletes all sandboxes and providers
openshell gateway start
# Then re-run Phase 1 steps 3-6 from the deployment guide
```

---

## 5. Monitoring

### Real-time TUI (recommended)

```bash
# Shows all sandbox activity, policy decisions, network requests, approvals
openshell term
```

### View logs for a specific sandbox

```bash
openshell logs nemoclaw-main           # Recent logs
openshell logs nemoclaw-main --tail    # Stream live
openshell logs claude-dev --level warn # Only warnings/errors
```

### Check system health

```bash
openshell status                        # Gateway health
openshell sandbox list                  # All sandboxes
openshell inference get                 # Active inference route
openshell provider list                 # All registered providers

# Ollama status
ollama ps                               # Currently loaded models
ollama list                             # All downloaded models
nvidia-smi                              # GPU usage (Spark only)

# Pi monitoring
curl http://raspi.local:4000/health     # LiteLLM proxy
curl http://raspi.local:3001            # Uptime Kuma dashboard
```

### Run the test suite to validate state

```bash
cd ~/workspace/nemoclaw/tests
uv run pytest phase1_core/ -v           # Validate Spark is healthy
uv run pytest -v                        # Validate everything
```

---

## 6. Updating

### Update a model

```bash
# Pull a newer version
ollama pull nemotron-3-super:120b

# The old version is replaced. Next inference request uses the new one.
# No need to restart the gateway or sandbox.
```

### Update OpenShell

```bash
cd ~/workspace/nemoclaw
source openshell-env/bin/activate
uv pip install --upgrade openshell

# Restart the gateway to pick up the new version
openshell gateway stop
openshell gateway start
```

### Update NemoClaw

```bash
curl -fsSL https://www.nvidia.com/nemoclaw.sh | bash
# Or: nemoclaw onboard (re-runs the setup wizard)
```

### Update sandbox policies

```bash
# Get current policy
openshell policy get nemoclaw-main --full > /tmp/policy.yaml

# Edit the policy file, then apply:
openshell policy set nemoclaw-main --policy /tmp/policy.yaml --wait
# No sandbox restart needed — policies hot-reload
```
