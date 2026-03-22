# NemoClaw Cookbook

*30 copy-pasteable recipes for daily operations on the DGX Spark + Mac Studio + Raspberry Pi stack.*

*All commands that run inside the `nemoclaw-main` sandbox are preceded by `openshell sandbox connect nemoclaw-main`.*
*All commands that require the OpenShell venv assume `source ~/workspace/nemoclaw/openshell-env/bin/activate` has been run on the Spark.*

---

## Category 1: Providers and Models

### Recipe 1 — Add a New Inference Provider

**Why:** You have a new model server running somewhere on your Tailscale network and want OpenClaw to route inference to it. Providers are registered at the OpenShell gateway level so every sandbox picks them up without restart.

**CLI command (run on Spark host):**

```bash
source ~/workspace/nemoclaw/openshell-env/bin/activate

openshell provider create \
    --name my-new-provider \
    --type openai \
    --credential OPENAI_API_KEY=not-needed \
    --config OPENAI_BASE_URL=http://<TAILSCALE_IP>:<PORT>/v1
```

For a provider on the Mac at port 11435:

```bash
openshell provider create \
    --name mac-ollama-v2 \
    --type openai \
    --credential OPENAI_API_KEY=not-needed \
    --config OPENAI_BASE_URL=http://100.116.228.36:11435/v1
```

For an Anthropic-compatible endpoint (LM Studio):

```bash
openshell provider create \
    --name local-lmstudio-anthropic \
    --type anthropic \
    --credential ANTHROPIC_API_KEY=lm-studio \
    --config ANTHROPIC_BASE_URL=http://host.openshell.internal:1234
```

**Web UI location:** There is no Web UI for provider management. Use the CLI.

**Verify:**

```bash
openshell provider list
# Expected: new provider name appears in the list
# Test it by switching to it:
openshell inference set --provider my-new-provider --model <model-name>
openshell inference get
```

---

### Recipe 2 — Switch the Active Model

**Why:** You want to switch from Nemotron 120B (heavy, slow first token) to Qwen3 8B on the Mac (fast, lightweight) or back. This takes about 5 seconds and does not restart any sandbox.

**CLI command (run on Spark host):**

```bash
source ~/workspace/nemoclaw/openshell-env/bin/activate

# Switch to Mac (fast, 8B — good for quick Q&A)
openshell inference set --provider mac-ollama --model qwen3:8b

# Switch back to Spark (120B — good for complex reasoning)
openshell inference set --provider local-ollama --model nemotron-3-super:120b

# Switch to LM Studio on Spark (OpenAI-compatible)
openshell inference set --provider local-lmstudio --model nemotron-3-super:120b
```

**Web UI location:** Dashboard at `https://spark-caeb.tail48bab7.ts.net/` → Settings (gear icon) → Inference → Provider dropdown. Changes apply immediately.

**Verify:**

```bash
openshell inference get
# Expected: provider=<name you set>, model=<model you set>

# Confirm inference is actually working:
curl -s http://100.93.220.104:11434/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"model":"nemotron-3-super:120b","messages":[{"role":"user","content":"ping"}],"max_tokens":5}'
```

---

### Recipe 3 — Pull a New Model into Ollama

**Why:** You want to add a model that does not yet exist on the Spark or Mac. Models must be pulled before they can be routed through the inference gateway.

**CLI command:**

```bash
# Pull on Spark (large models — Nemotron-class)
ollama pull nemotron-3-super:120b

# Pull on Spark (coder model)
ollama pull qwen3-coder-next:q4_K_M

# Pull on Mac (fast/small models — SSH in first)
ssh carlos@100.116.228.36
/usr/local/bin/ollama pull qwen3:8b

# Verify available disk space before pulling a large model
df -h /
du -sh ~/.ollama/models/

# Check GPU memory before loading a second large model
nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader
```

**Web UI location:** No Web UI for model pulling. Use Ollama CLI or LM Studio:

```bash
# LM Studio alternative for pulling via hub ID
lms get bartowski/Nemotron-3-Super-120B-GGUF
```

**Verify:**

```bash
# On Spark
ollama list
# On Mac
ssh carlos@100.116.228.36 "/usr/local/bin/ollama list"

# Test the new model responds via the unified LiteLLM endpoint:
curl http://100.85.6.21:4000/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"model":"<new-model-name>","messages":[{"role":"user","content":"hello"}]}'
```

---

## Category 2: Skills

### Recipe 4 — List Available Skills

**Why:** Before enabling a skill, you want to know what skills exist, which are enabled, and whether their dependencies are satisfied (binaries present, API keys set, network policies in place).

**CLI command (run inside `nemoclaw-main` sandbox):**

```bash
openshell sandbox connect nemoclaw-main

# Show all skills and their enabled/disabled state
openclaw skills list

# Show which enabled skills are fully ready vs missing dependencies
openclaw skills check
```

**Web UI location:** Dashboard → Settings → Skills tab. Each skill shows as a toggle card with a status indicator (green = ready, yellow = missing dependency, gray = disabled).

**Verify:**

```bash
# Inside the sandbox:
openclaw skills list | grep -i enabled
openclaw skills check
# Look for any skill marked "missing" and read the dependency hint
```

---

### Recipe 5 — Enable a Skill

**Why:** Skills are disabled by default. To give the agent a new capability (GitHub, web browsing, code execution), you enable the skill in OpenClaw and — for skills that need network access — add the corresponding policy preset to the sandbox.

**CLI command (two steps):**

Step 1: Enable the skill inside the sandbox:

```bash
openshell sandbox connect nemoclaw-main

openclaw skills enable github
# or:
openclaw skills enable web-browsing
openclaw skills enable code-execution
openclaw skills enable file-management
```

Step 2: Add the network policy preset for skills that need external access (run on Spark host, not inside sandbox):

```bash
# Exit sandbox first, then on host:
nemoclaw nemoclaw-main policy-add github
nemoclaw nemoclaw-main policy-add npm-registry
```

**Web UI location:** Dashboard → Settings → Skills tab → toggle the skill on. The Web UI will show a warning banner if the skill requires a policy preset that is not yet applied.

**Verify:**

```bash
openshell sandbox connect nemoclaw-main
openclaw skills check
# The newly enabled skill should show "ready" instead of "disabled" or "missing"
```

---

### Recipe 6 — Create a Custom Skill

**Why:** You have a recurring task — such as querying your internal API, summarizing Jira tickets, or checking a private database — that no built-in skill covers. Custom skills let you package that as a named, reusable tool the agent can invoke.

**CLI command (run inside `nemoclaw-main` sandbox):**

```bash
openshell sandbox connect nemoclaw-main

# Create a new custom skill definition
openclaw skills create my-internal-api \
    --description "Query the internal project tracking API" \
    --command "curl -s http://100.85.6.21:8080/projects" \
    --output-format json

# List to confirm it was created
openclaw skills list | grep my-internal-api

# Enable it
openclaw skills enable my-internal-api
```

For a skill backed by a script, place the script in the sandbox workspace first:

```bash
# Inside the sandbox:
mkdir -p /sandbox/scripts
cat > /sandbox/scripts/fetch-status.sh << 'EOF'
#!/bin/bash
curl -s http://100.85.6.21:4000/health | python3 -m json.tool
EOF
chmod +x /sandbox/scripts/fetch-status.sh

openclaw skills create infra-health \
    --description "Check health of all Pi-hosted services" \
    --command "/sandbox/scripts/fetch-status.sh" \
    --output-format text
```

**Web UI location:** Dashboard → Settings → Skills tab → "Create Custom Skill" button (appears at the bottom of the skills list).

**Verify:**

```bash
# Inside sandbox:
openclaw skills check
# Ask the agent to use it:
openclaw agent --agent main --local -m "use the infra-health skill and tell me the result" --session-id skill-test
```

---

## Category 3: Sessions

### Recipe 7 — Start a New Session

**Why:** The agent carries conversation history in the current session. When you switch topics and do not want prior context to bleed in, start a fresh session. This clears the context window without touching memory.

**CLI command (inside sandbox):**

```bash
openshell sandbox connect nemoclaw-main

# Start a new named session (useful for tracking separate projects)
openclaw sessions new --name "refactor-auth-module"

# Or use the TUI and type /new at the prompt
openclaw tui
# Then at the prompt: /new
```

**Web UI location:** Dashboard at `https://spark-caeb.tail48bab7.ts.net/` → click the "+" button in the sessions sidebar (top-left panel) to open a new chat thread.

Alternatively, use the token URL to open a fresh session directly:

```
https://spark-caeb.tail48bab7.ts.net/#token=7cfb6a0efd17c1ea4f3cda511ffd5e1528ec013d9e8c6634
```

**Verify:**

```bash
# Inside sandbox:
openclaw sessions list
# The new session should appear with status "active" and a timestamp
```

---

### Recipe 8 — List and Switch Sessions

**Why:** You have multiple parallel conversations — one for a research task, one for a coding task, one for a weekly summary. You want to see all of them and resume a specific one.

**CLI command (inside sandbox):**

```bash
openshell sandbox connect nemoclaw-main

# List all sessions across all agents
openclaw sessions list

# Switch to a specific session by ID
openclaw sessions switch <session-id>

# Or resume by name
openclaw sessions resume refactor-auth-module

# Attach the TUI to a specific session
openclaw tui --session <session-id>
```

**Web UI location:** Dashboard → sessions sidebar (left panel) shows all sessions grouped by agent. Click any session to switch to it. The active session is highlighted.

**Verify:**

```bash
# Inside sandbox:
openclaw sessions list
# Look for "active: true" next to the session you switched to
```

---

### Recipe 9 — Delete a Session

**Why:** Old sessions accumulate and consume disk space inside the sandbox. Sessions you no longer need can be pruned. This does not affect memory — only the conversation history is removed.

**CLI command (inside sandbox):**

```bash
openshell sandbox connect nemoclaw-main

# Delete a specific session by ID
openclaw sessions delete <session-id>

# Prune all sessions older than 7 days
openclaw sessions prune --older-than 7d

# Prune all sessions except the most recent one per agent
openclaw sessions prune --keep-latest 1
```

**Web UI location:** Dashboard → sessions sidebar → hover over a session → trash icon appears → click to delete. Bulk pruning is CLI-only.

**Verify:**

```bash
openclaw sessions list
# Deleted session should no longer appear
```

---

## Category 4: Channels

### Recipe 10 — Add a Telegram Channel

**Why:** You want to reach your NemoClaw agent from Telegram on your phone, without opening a browser. Telegram messages will route through the gateway to the main agent.

**Prerequisites:** Create a Telegram bot via @BotFather and copy the bot token.

**CLI command (inside sandbox):**

```bash
openshell sandbox connect nemoclaw-main

# Register the Telegram bot token with the gateway
openclaw channels add telegram --token YOUR_BOT_TOKEN_HERE

# Bind the Telegram channel to the main agent
openclaw agents bind main --channel telegram --target YOUR_BOT_TOKEN_HERE

# List channels to confirm
openclaw channels list
```

**Web UI location:** Dashboard → Settings → Channels tab → "Add Channel" → select Telegram → paste bot token → click "Connect". The bind step is done in the same dialog (agent selector dropdown).

**Verify:**

```bash
# Inside sandbox:
openclaw channels list
# Telegram entry should show status "connected"

# On your phone: open Telegram, find your bot, send "/start"
# The agent should respond within a few seconds
```

---

### Recipe 11 — Add a Discord Channel

**Why:** Your team communicates on Discord and you want the agent available in a specific channel or via DM.

**Prerequisites:** Create a Discord application and bot at `https://discord.com/developers/applications`. Copy the bot token. Invite the bot to your server with "Send Messages" and "Read Messages" permissions.

**CLI command (inside sandbox):**

```bash
openshell sandbox connect nemoclaw-main

# Register Discord bot
openclaw channels add discord \
    --token YOUR_DISCORD_BOT_TOKEN \
    --guild YOUR_SERVER_ID \
    --channel YOUR_CHANNEL_ID

# Bind to the main agent
openclaw agents bind main --channel discord --target YOUR_CHANNEL_ID

# Confirm
openclaw channels list
```

**Web UI location:** Dashboard → Settings → Channels tab → "Add Channel" → select Discord → fill in bot token, server ID, and channel ID → click "Connect".

**Verify:**

```bash
openclaw channels list
# Discord entry shows status "connected"

# In your Discord server: type a message in the bound channel
# The agent should reply
```

---

### Recipe 12 — Remove a Channel

**Why:** You want to revoke access from a channel (bot token compromised, Telegram bot retired, or you no longer want a specific integration active).

**CLI command (inside sandbox):**

```bash
openshell sandbox connect nemoclaw-main

# List channels to get the channel ID
openclaw channels list

# Remove by ID
openclaw channels remove <channel-id>

# Or remove by type if only one of that type exists
openclaw channels remove --type telegram
openclaw channels remove --type discord
```

**Web UI location:** Dashboard → Settings → Channels tab → find the channel → click the three-dot menu → "Remove". This also unbinds the channel from any agent.

**Verify:**

```bash
openclaw channels list
# Removed channel no longer appears

# Attempt to message the bot — it should not respond
```

---

## Category 5: Nodes

### Recipe 13 — Add the Mac Studio as a Node

**Why:** The agent runs inside a sandboxed container on the Spark and has no access to your Mac's screen, clipboard, camera, or macOS APIs. Adding the Mac as a node gives the agent remote access to those capabilities.

**CLI command:**

Step 1 — Install the node host service on the Mac:

```bash
ssh carlos@100.116.228.36
export PATH="$HOME/.npm-global/bin:$HOME/.nvm/versions/node/v24.13.0/bin:$PATH"

openclaw node install \
    --host spark-caeb.tail48bab7.ts.net \
    --port 443 \
    --tls \
    --force \
    --display-name "Mac Studio"
```

Step 2 — Approve the pairing request on the Spark (the node host will disconnect until you approve):

```bash
openshell sandbox connect nemoclaw-main
openclaw devices list
# Find the pending "Mac Studio" request and copy its request ID
openclaw devices approve <request-id>
exit
```

Step 3 — Restart the node host on the Mac so it reconnects with approved status:

```bash
ssh carlos@100.116.228.36
export PATH="$HOME/.npm-global/bin:$HOME/.nvm/versions/node/v24.13.0/bin:$PATH"
openclaw node stop
openclaw node start
```

Step 4 — Add the gateway token to the launchd plist so it survives reboots:

```bash
ssh carlos@100.116.228.36
python3 -c "
import plistlib
plist_path = '$HOME/Library/LaunchAgents/ai.openclaw.node.plist'
with open(plist_path, 'rb') as f:
    plist = plistlib.load(f)
plist.setdefault('EnvironmentVariables', {})['OPENCLAW_GATEWAY_TOKEN'] = '7cfb6a0efd17c1ea4f3cda511ffd5e1528ec013d9e8c6634'
with open(plist_path, 'wb') as f:
    plistlib.dump(plist, f)
"
launchctl bootout gui/$(id -u)/ai.openclaw.node 2>/dev/null
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.openclaw.node.plist
```

**Web UI location:** Dashboard → Settings → Nodes tab shows connected nodes. Device pairing approvals appear in Dashboard → Settings → Devices tab.

**Verify:**

```bash
# On Mac:
ssh carlos@100.116.228.36
export PATH="$HOME/.npm-global/bin:$HOME/.nvm/versions/node/v24.13.0/bin:$PATH"
openclaw node status
# Expected: Runtime: running (pid XXXX, state active)

# On Spark, inside sandbox:
openshell sandbox connect nemoclaw-main
openclaw nodes list
# Expected: "Mac Studio" with status "connected"
```

---

### Recipe 14 — Add an iOS Device as a Node

**Why:** You want to use your iPhone's camera, GPS location, or voice input as tools the agent can invoke. The iOS app creates a node that connects to the gateway over Tailscale.

**Steps:**

1. Install the OpenClaw iOS app via TestFlight (official) or GoClaw/ClawOn from the App Store.
2. In the app settings, enter the gateway URL: `https://spark-caeb.tail48bab7.ts.net/`
3. When prompted for a token, enter: `7cfb6a0efd17c1ea4f3cda511ffd5e1528ec013d9e8c6634`
4. The app will attempt to pair — you must approve from the sandbox.

**CLI command (approve pairing on Spark):**

```bash
openshell sandbox connect nemoclaw-main
openclaw devices list
# Find the iOS device entry (shows as "iPhone" or the device name you set)
openclaw devices approve <request-id>
```

Grant iOS permissions when prompted by the app:
- Camera (for photo tools)
- Location (for GPS tools)
- Microphone (for voice input)

**Web UI location:** Dashboard → Settings → Devices tab for pending pairing requests. Dashboard → Settings → Nodes tab to see connected iOS devices.

**Verify:**

```bash
openshell sandbox connect nemoclaw-main
openclaw nodes list
# iOS device should appear with status "connected"

# Ask the agent to use the iPhone camera:
openclaw agent --agent main --local -m "take a photo using the iOS node" --session-id node-test
```

---

### Recipe 15 — Invoke a Node Command Directly

**Why:** You want to run a specific node capability (take a screenshot, read the clipboard, trigger an AppleScript) without asking the agent in natural language. Useful for testing and automation scripts.

**CLI command (inside sandbox):**

```bash
openshell sandbox connect nemoclaw-main

# List available node capabilities
openclaw nodes list --capabilities

# Take a screenshot from the Mac node
openclaw node invoke mac-studio --tool screen-capture

# Read the Mac clipboard
openclaw node invoke mac-studio --tool clipboard-read

# Run an AppleScript on the Mac
openclaw node invoke mac-studio \
    --tool applescript \
    --args '{"script": "tell application \"Finder\" to get name of front window"}'

# Send a native macOS notification
openclaw node invoke mac-studio \
    --tool notify \
    --args '{"title": "NemoClaw", "body": "Task complete"}'
```

**Web UI location:** Dashboard → Settings → Nodes tab → click on a node → "Available Tools" panel → click any tool to invoke it with a test payload.

**Verify:**

```bash
# After running screen-capture, the output should include a base64-encoded PNG
# After running notify, you should see a macOS notification pop up on the Mac
openclaw node invoke mac-studio --tool screen-capture | python3 -c "import sys, json; d=json.load(sys.stdin); print('Image size:', len(d.get('image','')) , 'bytes')"
```

---

## Category 6: Cron Jobs

### Recipe 16 — Schedule a Daily Summary

**Why:** You want the agent to automatically generate a morning briefing at 9am — GitHub notifications, open PRs, or any other digest — and send it somewhere (Telegram, memory, or the Web UI).

**CLI command (inside sandbox):**

```bash
openshell sandbox connect nemoclaw-main

# Daily at 9am: GitHub digest sent to the main session
openclaw cron add "0 9 * * *" \
    "Summarize my GitHub notifications from the past 24 hours. List anything that needs my attention today, sorted by priority. Keep it under 200 words."

# Daily at 8am: disk usage check, results stored to memory
openclaw cron add "0 8 * * *" \
    "Check disk usage on the Spark by running df -h and ollama ps. If disk usage is above 85%, alert me. Save the summary to memory as DAILY_DISK_CHECK."

# Weekly on Monday at 7am: project status summary
openclaw cron add "0 7 * * 1" \
    "Generate a weekly status summary of any open tasks, recent completions, and upcoming priorities based on memory and recent sessions."

# List all scheduled jobs
openclaw cron list
```

**Web UI location:** Dashboard → Settings → Cron tab → "Add Schedule" → fill in cron expression and prompt → click "Save".

**Verify:**

```bash
openclaw cron list
# Should show the new job with its next scheduled run time

# Trigger immediately to test (override schedule for one run)
openclaw cron run <job-id>
```

---

### Recipe 17 — Schedule a Health Check

**Why:** You want the system to self-monitor and alert you (via Telegram or memory) if any component goes down — Ollama, the Pi LiteLLM proxy, or the Mac forwarder.

**CLI command (inside sandbox):**

```bash
openshell sandbox connect nemoclaw-main

# Every 30 minutes: check all services
openclaw cron add "*/30 * * * *" \
    "Run a health check: (1) verify Ollama is responding at http://100.93.220.104:11434/api/tags, (2) verify LiteLLM is responding at http://100.85.6.21:4000/health, (3) verify Mac forwarder is reachable at http://100.116.228.36:11435/api/tags. If any check fails, send an alert to the Telegram channel. Report the status of all three checks."

# Every hour: sandbox status check
openclaw cron add "0 * * * *" \
    "Check the status of all OpenShell sandboxes using openshell sandbox list. If any sandbox is not in Ready state, report the name and current status."

# Every 5 minutes: port forward watchdog
# (This is better as a host cron, but can run inside the sandbox via shell command)
openclaw cron add "*/5 * * * *" \
    "Verify that the gateway web UI at https://spark-caeb.tail48bab7.ts.net/ returns HTTP 200. If it does not, note it in memory under FORWARD_FAILURES."

openclaw cron list
```

**Web UI location:** Dashboard → Settings → Cron tab → "Add Schedule".

**Verify:**

```bash
openclaw cron list

# Manually trigger and watch the output
openclaw cron run <job-id>
# The agent will execute the prompt and you will see the response in the current session
```

---

### Recipe 18 — Disable or Delete a Cron Job

**Why:** A scheduled job is no longer needed, is running too frequently, or is generating noise. You want to pause it (disable) or remove it entirely (delete).

**CLI command (inside sandbox):**

```bash
openshell sandbox connect nemoclaw-main

# List all jobs to get IDs
openclaw cron list

# Disable a job (keeps the schedule but stops firing)
openclaw cron disable <job-id>

# Re-enable a disabled job
openclaw cron enable <job-id>

# Delete permanently
openclaw cron delete <job-id>

# Delete all cron jobs (use with caution)
openclaw cron list --json | python3 -c "
import json, sys, subprocess
jobs = json.load(sys.stdin)
for j in jobs:
    subprocess.run(['openclaw', 'cron', 'delete', j['id']])
print(f'Deleted {len(jobs)} jobs')
"
```

**Web UI location:** Dashboard → Settings → Cron tab → find the job → toggle switch to disable, or click the trash icon to delete.

**Verify:**

```bash
openclaw cron list
# Disabled job shows as "disabled" with no next run time
# Deleted job no longer appears
```

---

## Category 7: Agents

### Recipe 19 — Create a Specialized Agent

**Why:** You want an agent with a focused purpose — a coding assistant with GitHub skills enabled, or a research assistant with web browsing enabled — separate from your general-purpose main agent. Each agent has its own memory, session history, and skill configuration.

**CLI command (inside sandbox):**

```bash
openshell sandbox connect nemoclaw-main

# Create a coding-focused agent
openclaw agents add coding-agent \
    --model nemotron-3-super:120b \
    --system-prompt "You are a senior software engineer. Focus exclusively on code quality, security, and performance. Always suggest tests. Never give verbose prose — be terse and direct."

# Create a research agent using the faster Mac model
openclaw agents add research-agent \
    --model qwen3:8b \
    --system-prompt "You are a research assistant. Prioritize finding accurate information from multiple sources. Summarize findings concisely with source attribution."

# List all agents
openclaw agents list
```

Then enable skills specific to that agent:

```bash
# Enable GitHub for coding-agent
openclaw skills enable github --agent coding-agent

# Enable web browsing for research-agent
openclaw skills enable web-browsing --agent research-agent
```

**Web UI location:** Dashboard → Settings → Agents tab → "Add Agent" button → fill in name, model, and system prompt.

**Verify:**

```bash
openclaw agents list
# New agents should appear with their configured models

openclaw agent --agent coding-agent --local -m "what is your role?" --session-id intro
```

---

### Recipe 20 — Bind an Agent to a Channel

**Why:** When a message arrives on a channel (Telegram, Discord, webhook), the gateway must know which agent to route it to. Binding creates that routing rule. One agent can be bound to multiple channels; a channel can only be bound to one agent at a time.

**CLI command (inside sandbox):**

```bash
openshell sandbox connect nemoclaw-main

# Bind coding-agent to a specific Telegram bot
openclaw agents bind coding-agent \
    --channel telegram \
    --target YOUR_CODING_BOT_TOKEN

# Bind research-agent to a Discord channel
openclaw agents bind research-agent \
    --channel discord \
    --target YOUR_DISCORD_CHANNEL_ID

# Bind main agent to a webhook (returns the webhook URL)
openclaw agents bind main \
    --channel webhook \
    --target github-pr-reviews

# View current bindings
openclaw agents list --bindings
```

**Web UI location:** Dashboard → Settings → Channels tab → find the channel → "Assign Agent" dropdown → select the agent.

**Verify:**

```bash
openclaw agents list --bindings
# Each agent shows which channels it is bound to

# Test by sending a message to the bound channel
# The correct agent should respond (not the main agent)
```

---

### Recipe 21 — Delete an Agent

**Why:** A specialized agent is no longer needed. Deleting it removes its configuration, sessions, and memory from the sandbox. This cannot be undone.

**CLI command (inside sandbox):**

```bash
openshell sandbox connect nemoclaw-main

# List agents to confirm which one to delete
openclaw agents list

# Delete by name
openclaw agents delete research-agent

# If the agent is bound to a channel, unbind first (or the delete will prompt you)
openclaw channels remove --agent research-agent  # removes all bindings
openclaw agents delete research-agent

# Confirm the agent is gone
openclaw agents list
```

**Web UI location:** Dashboard → Settings → Agents tab → find the agent → three-dot menu → "Delete". The UI will warn you if the agent is still bound to active channels.

**Verify:**

```bash
openclaw agents list
# research-agent should no longer appear

openclaw channels list
# Any channels that were bound to research-agent should now show "unbound"
```

---

## Category 8: Configuration

### Recipe 22 — Configure OpenClaw via CLI

**Why:** You need to change a specific gateway setting — allowed origins for the Web UI, the inference URL, token authentication, or agent defaults — without running the full onboarding wizard again.

**CLI command (inside sandbox):**

```bash
openshell sandbox connect nemoclaw-main

# View the full current configuration
openclaw configure --list

# Set a specific key
openclaw configure --key inference.url --value "https://inference.local/v1"
openclaw configure --key gateway.port --value "18789"

# Add the Tailscale domain to allowed origins (fixes WebSocket origin errors)
python3 -c "
import json
f = '/sandbox/.openclaw/openclaw.json'
d = json.load(open(f))
ui = d.setdefault('gateway', {}).setdefault('controlUi', {})
origins = ui.setdefault('allowedOrigins', [])
for o in ['https://spark-caeb.tail48bab7.ts.net', 'http://127.0.0.1:18789', 'http://100.93.220.104:18789']:
    if o not in origins:
        origins.append(o)
json.dump(d, open(f, 'w'), indent=2)
print('Origins updated:', origins)
"

# Restart the gateway to apply changes
pkill -f 'openclaw gateway'
sleep 2
nohup openclaw gateway run > /tmp/gateway.log 2>&1 &
```

**Web UI location:** Dashboard → Settings (gear icon) → General tab for most settings. The inference URL and allowed origins must be changed via CLI or direct JSON edit.

**Verify:**

```bash
openclaw configure --list
# Shows updated values

# Check that the gateway is running after restart
curl -s http://127.0.0.1:18789/health
# Expected: HTTP 200
```

---

### Recipe 23 — Configure OpenClaw via Web UI

**Why:** Common settings like the system prompt, model selection, and skill toggles are easier to manage in the browser than in the CLI.

**Steps:**

1. Open `https://spark-caeb.tail48bab7.ts.net/#token=7cfb6a0efd17c1ea4f3cda511ffd5e1528ec013d9e8c6634` in your browser.
2. Click the gear icon (Settings) in the top-right or sidebar.

Available settings panels:

| Panel | What you can change |
|-------|---------------------|
| General | Gateway name, port, token authentication on/off |
| Agents | System prompt, model per agent, agent name |
| Inference | Provider, model, temperature, max tokens |
| Skills | Enable/disable skills, view dependency status |
| Channels | Add/remove channels, assign agents |
| Cron | Add/edit/delete scheduled jobs |
| Nodes | View connected nodes, revoke node access |
| Devices | Approve/revoke device pairing requests |
| Security | Run security audit, view policy coverage |
| Memory | Browse memory files, trigger re-index |

**CLI equivalent for any setting changed in the UI:**

```bash
openshell sandbox connect nemoclaw-main
openclaw configure --list
# Settings changed in the UI are reflected here
```

**Verify:** Changes take effect immediately in the UI. For inference changes, send a test message and confirm the model responds. For skill changes, run `openclaw skills check` in the CLI.

---

### Recipe 24 — Run a Security Audit

**Why:** After adding new skills, channels, or changing policies, you want to confirm that there are no misconfigurations — world-readable credential files, overly permissive network policies, disabled authentication, or unchecked agent permissions.

**CLI command (inside sandbox):**

```bash
openshell sandbox connect nemoclaw-main

# Basic audit
openclaw security audit

# Deep audit including network policy coverage and credential exposure analysis
openclaw security audit --deep

# Auto-fix common misconfigurations (world-readable files, missing auth, etc.)
openclaw security audit --fix

# View pending approvals that require your action
openclaw approvals list
```

Also audit at the OpenShell level from the host:

```bash
source ~/workspace/nemoclaw/openshell-env/bin/activate

# View the current policy for the main sandbox
openshell policy get nemoclaw-main --full

# Export for review
openshell policy get nemoclaw-main --full > /tmp/nemoclaw-policy.yaml
```

**Web UI location:** Dashboard → Settings → Security tab → "Run Audit" button. Results show as a categorized list of findings with severity levels.

**Verify:**

```bash
openclaw security audit
# Look for any "HIGH" or "CRITICAL" severity findings
# Address each one before proceeding

openclaw approvals list
# Any pending approval requests should be reviewed and acted on
```

---

## Category 9: Memory

### Recipe 25 — Search Memory

**Why:** The agent has accumulated memory from past sessions. You want to find what it knows about a specific topic — your project conventions, a past decision, a stored API key name — without scrolling through conversation logs.

**CLI command (inside sandbox):**

```bash
openshell sandbox connect nemoclaw-main

# Semantic search across all memory files
openclaw memory search "database schema"
openclaw memory search "deployment procedure"
openclaw memory search "API authentication"

# List all memory files for the main agent
openclaw memory list

# Read a specific memory file
openclaw memory get PREFERENCES.md
openclaw memory get DAILY_DISK_CHECK

# Rebuild the search index (needed after manually adding files)
openclaw memory index

# Search for memory files modified in the last 7 days
openclaw memory list --since 7d
```

Memory files live at `/sandbox/.openclaw/agents/main/memory/` inside the sandbox. You can browse them directly:

```bash
ls /sandbox/.openclaw/agents/main/memory/
cat /sandbox/.openclaw/agents/main/memory/PREFERENCES.md
```

**Web UI location:** Dashboard → Settings → Memory tab → search box at the top. Results show matching memory chunks with their source file names and relevance scores.

**Verify:**

```bash
openclaw memory search "test query"
# Should return relevant memory snippets or "no results found"
```

---

### Recipe 26 — Add to Memory

**Why:** You want the agent to permanently remember something — your coding conventions, a project decision, your preferred tools, a frequently used command. Adding to memory means the agent will retrieve and use this information in future sessions automatically.

**CLI command (inside sandbox):**

```bash
openshell sandbox connect nemoclaw-main

# Add a new memory file directly
cat > /sandbox/.openclaw/agents/main/memory/PREFERENCES.md << 'EOF'
# Coding Preferences

- Language: Python for scripts, TypeScript for frontend
- Style: Black formatter, 88-char line length, type hints required
- Tests: pytest, minimum 80% coverage for new code
- Commits: conventional commits format (feat:, fix:, chore:)
- Never use print() for logging — use the logging module
EOF

# Rebuild the search index so the new file is searchable
openclaw memory index

# Or use the CLI to add a single note
openclaw memory add \
    --title "Spark GPU Capacity" \
    --content "Nemotron 120B uses ~86 GB of the 128 GB UMA. Always check nvidia-smi before loading a second large model. Use 'ollama ps' to see what is loaded."

# Tell the agent to remember something from the current session
# (type this in the chat UI or TUI):
# "Remember this for all future sessions: the LiteLLM proxy is at http://100.85.6.21:4000/v1"
```

**Web UI location:** Dashboard → Settings → Memory tab → "Add Memory" button → enter title and content → click "Save". The index rebuilds automatically after saving.

**Verify:**

```bash
openclaw memory search "coding preferences"
# Should return the PREFERENCES.md content

openclaw memory search "Spark GPU"
# Should return the GPU capacity note
```

---

## Category 10: Hooks

### Recipe 27 — Enable a Hook

**Why:** Hooks fire automatically when specific agent events occur — a session starts, a session ends, the agent completes a long tool call. They let you add side-effects without modifying agent behavior: log events, notify yourself, save summaries to memory.

**CLI command (inside sandbox):**

```bash
openshell sandbox connect nemoclaw-main

# List all available hook points and which ones have handlers
openclaw hooks list

# Enable the session-end hook to auto-summarize each conversation into memory
openclaw hooks enable session-end \
    --handler "Summarize the key decisions, code snippets, and action items from this session and save them to memory as SESSION_SUMMARY_$(date +%Y%m%d)."

# Enable a pre-tool-call hook to log tool invocations
openclaw hooks enable pre-tool-call \
    --handler "Log the tool name and arguments to /tmp/tool-calls.log"

# Enable a post-message hook to send a Telegram notification when the agent finishes a long task
openclaw hooks enable post-message \
    --handler "If the agent response is longer than 500 words, send a brief notification to the Telegram channel saying 'Task complete — check the dashboard.'"

# Enable the session-start hook to inject relevant memory context
openclaw hooks enable session-start \
    --handler "Search memory for context relevant to the first message and prepend it to the system prompt for this session."

# Confirm hooks are registered
openclaw hooks list
```

**Web UI location:** Dashboard → Settings → Hooks tab → toggle any hook point on → enter the handler prompt or script path in the text field that appears → click "Save".

**Verify:**

```bash
openclaw hooks list
# Enabled hooks show their handler and status as "active"

# Test the session-end hook by ending a session:
openclaw sessions new --name "hook-test"
openclaw agent --agent main --local -m "Remember that the LiteLLM proxy port is 4000." --session-id hook-test
# End the session — the hook should fire and save to memory
openclaw memory search "LiteLLM proxy"
```

---

## Category 11: Monitoring

### Recipe 28 — View Live Logs

**Why:** The agent is not responding as expected, inference is slow, or a skill is failing silently. Live logs show what the gateway and agent are doing in real time — every incoming message, every tool call, every model request, every error.

**CLI command (run on Spark host):**

```bash
source ~/workspace/nemoclaw/openshell-env/bin/activate

# Stream live logs for the OpenClaw sandbox
openshell logs nemoclaw-main --tail

# Stream logs for a specific agent sandbox
openshell logs claude-dev --tail
openshell logs codex-dev --tail
openshell logs gemini-dev --tail

# View last 100 lines without streaming
openshell logs nemoclaw-main --lines 100

# Filter logs to only show errors
openshell logs nemoclaw-main --tail | grep -i error

# View the OpenClaw gateway log directly (inside sandbox)
openshell sandbox connect nemoclaw-main
tail -f /tmp/gateway.log
```

Also check Ollama logs when inference is slow or failing:

```bash
# On Spark host
journalctl -u ollama -f

# Check what model is currently loaded and its memory usage
ollama ps

# Check GPU utilization during inference
nvidia-smi dmon -s u -d 2
```

**Web UI location:** Dashboard → sidebar → "Logs" icon (scroll icon) → shows a streaming log view of the current session's tool calls and gateway events. For full system logs, use the CLI.

**Verify:**

```bash
# Send a test message and watch it flow through the logs
openshell logs nemoclaw-main --tail &
LOG_PID=$!
curl -s https://inference.local/v1/chat/completions \
    -H 'Content-Type: application/json' \
    -d '{"model":"nemotron-3-super:120b","messages":[{"role":"user","content":"ping"}],"max_tokens":5}'
# You should see the request appear in the logs within ~1 second
kill $LOG_PID
```

---

### Recipe 29 — Open the Sandbox TUI Monitor

**Why:** You want a real-time dashboard that shows all sandbox activity — network requests, policy decisions (allowed/blocked), pending approval prompts — in a single terminal view. This is the fastest way to diagnose blocked network access or approve a one-time policy exception.

**CLI command (run on Spark host):**

```bash
source ~/workspace/nemoclaw/openshell-env/bin/activate

# Open the real-time monitoring TUI
openshell term
```

What you see inside the TUI:

| Panel | Content |
|-------|---------|
| Top | List of all sandboxes with current status (Ready/Running/Error) |
| Middle | Real-time network requests from the selected sandbox |
| Bottom | Policy decisions: Allowed (green), Blocked (red), Pending (yellow) |
| Right | Log stream for the selected sandbox |

Keyboard shortcuts inside the TUI:

| Key | Action |
|-----|--------|
| `Tab` | Switch between sandboxes |
| `A` | Approve the highlighted blocked request (this session only) |
| `D` | Deny the highlighted blocked request |
| `I` | Ignore — allow once without saving to policy |
| `L` | Toggle the log panel |
| `Q` | Quit |

To approve a network request permanently (save to policy), press `A` then follow the prompt to escalate to a permanent policy change.

**Web UI location:** There is no Web UI equivalent for the full TUI monitor. The dashboard shows session-level activity but not sandbox-level network policy decisions.

**Verify:**

```bash
# While the TUI is open, trigger a network request from inside a sandbox:
# In another terminal:
openshell sandbox connect nemoclaw-main
curl -s https://inference.local/v1/models
# Watch the TUI — the request should appear in the middle panel as "Allowed"
```

---

### Recipe 30 — Run a Full Health Check

**Why:** After a reboot, a long weekend, or a configuration change, you want to confirm that every component of the NemoClaw stack is functional before relying on it.

**CLI command (run on Spark host):**

```bash
source ~/workspace/nemoclaw/openshell-env/bin/activate

echo "=== OpenShell Gateway ==="
openshell status

echo ""
echo "=== Sandbox Status ==="
openshell sandbox list

echo ""
echo "=== Inference Route ==="
openshell inference get

echo ""
echo "=== Ollama: Loaded Models ==="
ollama ps

echo ""
echo "=== Ollama: Available Models ==="
ollama list

echo ""
echo "=== Spark Ollama API ==="
curl -s http://100.93.220.104:11434/api/tags | python3 -m json.tool | head -20

echo ""
echo "=== Mac Ollama Forwarder ==="
curl -s --connect-timeout 5 http://100.116.228.36:11435/api/tags | python3 -m json.tool | head -10

echo ""
echo "=== Pi LiteLLM Proxy ==="
curl -s http://100.85.6.21:4000/health

echo ""
echo "=== Pi Uptime Kuma ==="
curl -s --connect-timeout 5 http://100.85.6.21:3001 | grep -o '<title>.*</title>'

echo ""
echo "=== Tailscale Serve Status ==="
tailscale serve status

echo ""
echo "=== Port 18789 Forward ==="
ss -tlnp | grep 18789 && echo "forward active" || echo "WARNING: forward is down"

echo ""
echo "=== End-to-End Inference Test ==="
curl -s http://100.93.220.104:11434/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"model":"nemotron-3-super:120b","messages":[{"role":"user","content":"reply with one word: ok"}],"max_tokens":5}' \
    | python3 -c "import sys,json; r=json.load(sys.stdin); print('Inference OK:', r['choices'][0]['message']['content'])"

echo ""
echo "=== OpenClaw Gateway (inside sandbox) ==="
openshell sandbox connect nemoclaw-main -- bash -c "curl -s http://127.0.0.1:18789/health && echo 'gateway healthy'"
```

Save this as a reusable script:

```bash
cat > ~/workspace/nemoclaw/health-check.sh << 'SCRIPT'
#!/bin/bash
set -euo pipefail
source ~/workspace/nemoclaw/openshell-env/bin/activate

PASS=0; FAIL=0

check() {
    local label="$1"; local cmd="$2"
    if eval "$cmd" > /dev/null 2>&1; then
        echo "[PASS] $label"; ((PASS++))
    else
        echo "[FAIL] $label"; ((FAIL++))
    fi
}

check "OpenShell gateway"         "openshell status"
check "nemoclaw-main sandbox"     "openshell sandbox list | grep -q 'nemoclaw-main.*Ready'"
check "Spark Ollama API"          "curl -sf http://100.93.220.104:11434/api/tags"
check "Mac Ollama forwarder"      "curl -sf --connect-timeout 5 http://100.116.228.36:11435/api/tags"
check "Pi LiteLLM proxy"          "curl -sf http://100.85.6.21:4000/health"
check "Port 18789 forward"        "ss -tlnp | grep -q 18789"
check "Tailscale Serve"           "tailscale serve status | grep -q 'spark-caeb'"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ $FAIL -eq 0 ] && echo "All systems healthy." || echo "WARNING: $FAIL check(s) failed."
SCRIPT

chmod +x ~/workspace/nemoclaw/health-check.sh
~/workspace/nemoclaw/health-check.sh
```

**Web UI location:** Open `http://100.85.6.21:3001` (Uptime Kuma) in your browser — shows a dashboard of all monitored endpoints with uptime history and current status.

**Verify:** The health check script exits 0 when all checks pass. Investigate any `[FAIL]` lines using the corresponding recipe in this cookbook.

---

## Category 11: Subscription-Based Model Switching

These recipes cover using cloud models (Claude, Gemini) with subscription authentication instead of API keys.

### Recipe 31: Switch the Main Agent to Claude Opus (Subscription)

**Why:** You want the main OpenClaw agent to use Claude Opus 4.6 instead of local Nemotron. You have an Anthropic subscription (Claude Pro/Team), not a raw API key.

**CLI (inside nemoclaw-main sandbox):**
```bash
openshell sandbox connect nemoclaw-main

# Run the interactive model configuration wizard
openclaw configure --section model

# When prompted:
# 1. Select provider: Anthropic
# 2. Authentication: browser login (opens your browser for Anthropic sign-in)
# 3. Model: claude-opus-4-6
# 4. Confirm

# Verify the switch
openclaw models status
# Expected: Default model shows claude-opus-4-6
```

**Web UI:** Go to Config page → Agents section → change the model.

**Switch back to local Nemotron:**
```bash
openclaw configure --section model
# Select: Custom Provider
# URL: https://inference.local/v1
# API key: ollama
# Model: nemotron-3-super:120b
```

**Important:** When using Claude via subscription, your data goes to Anthropic's servers. When using Nemotron via `inference.local`, data stays on the Spark. This is the privacy boundary.

**Verify:**
```bash
openclaw models status
# Or send a test message:
openclaw agent --agent main --local -m "What model are you?" --session-id test
```

### Recipe 32: Switch the Main Agent to Gemini (Subscription)

**Why:** You want to use Google's Gemini models via your Google account subscription.

**CLI (inside nemoclaw-main sandbox):**
```bash
openshell sandbox connect nemoclaw-main

openclaw configure --section model
# Select: Google Gemini
# Authentication: browser login (Google account)
# Model: gemini-2.5-pro or gemini-2.5-flash
```

**Switch back:** Same as Recipe 31 — run `openclaw configure --section model` and select Custom Provider with `inference.local`.

### Recipe 33: Understanding Subscription vs API Key Auth

**Why:** Know when to use which authentication method.

| Auth type | How it works | When to use | Data goes to |
|-----------|-------------|-------------|--------------|
| **Subscription (browser login)** | `openclaw configure` opens browser, you sign in with your account | Claude Pro, Gemini, when you have a subscription plan | Cloud (Anthropic/Google) |
| **API key** | `openshell provider create --credential OPENAI_API_KEY=sk-...` | OpenAI, NVIDIA API Catalog, pay-per-token providers | Cloud (provider) |
| **Local (no auth)** | `openshell provider create --credential OPENAI_API_KEY=not-needed` | Ollama, LM Studio on your own machines | Stays local |
| **inference.local** | Sandbox calls `https://inference.local/v1`, OpenShell injects credentials | Default for all sandboxes, routes to active provider | Depends on active provider |

**The key rule:** Subscription auth is configured inside the sandbox via `openclaw configure`. API key auth is configured outside the sandbox via `openshell provider create`. Local providers need no real auth.

**For agents in separate sandboxes** (Claude Code, Codex, Gemini CLI):
- **Claude Code (`claude-dev`):** Authenticates via browser login when you first connect: `openshell sandbox connect claude-dev` then follow the browser prompt
- **Codex (`codex-dev`):** Run `codex login` inside the sandbox
- **Gemini CLI (`gemini-dev`):** Run `gemini` inside the sandbox — it opens browser for Google sign-in

### Recipe 34: Add a Cloud Provider Without API Key

**Why:** You want to register a cloud provider (Anthropic, Google) in OpenShell but you only have a subscription, not an API key.

**The answer:** You can't register subscription-based providers in OpenShell's provider system. OpenShell providers require explicit credentials (`--credential`).

**Instead, use one of these approaches:**

**Approach A: Configure inside the sandbox** (recommended for subscriptions)
```bash
openshell sandbox connect nemoclaw-main
openclaw configure --section model
# Browser-based login, no API key needed
```

**Approach B: Extract the session token** (advanced)
After browser login, the auth token is stored in the sandbox. You could extract it and register as a provider, but this is fragile — tokens expire.

**Approach C: Use the agent's native auth** (for Claude Code, Codex, Gemini)
Each coding agent sandbox handles its own subscription auth independently. No OpenShell provider needed — just connect and authenticate:
```bash
openshell sandbox connect claude-dev   # → browser login for Anthropic
openshell sandbox connect codex-dev    # → codex login for OpenAI
openshell sandbox connect gemini-dev   # → browser login for Google
```

---

## Quick Reference

| I want to... | Recipe | Key command |
|---|---|---|
| Add a new inference provider | 1 | `openshell provider create --name ... --type openai ...` |
| Switch to the Mac's fast model | 2 | `openshell inference set --provider mac-ollama --model qwen3:8b` |
| Switch back to Nemotron 120B | 2 | `openshell inference set --provider local-ollama --model nemotron-3-super:120b` |
| Download a new model | 3 | `ollama pull <model-name>` |
| See what skills exist | 4 | `openclaw skills list` (inside sandbox) |
| Check if skills are ready | 4 | `openclaw skills check` (inside sandbox) |
| Enable a skill | 5 | `openclaw skills enable <skill-name>` (inside sandbox) |
| Create a custom skill | 6 | `openclaw skills create <name> --description ... --command ...` |
| Start a fresh conversation | 7 | `openclaw sessions new --name <name>` or `/new` in TUI |
| Resume a past conversation | 8 | `openclaw sessions resume <name>` |
| Delete old sessions | 9 | `openclaw sessions prune --older-than 7d` |
| Add Telegram to the agent | 10 | `openclaw channels add telegram --token <BOT_TOKEN>` |
| Add Discord to the agent | 11 | `openclaw channels add discord --token <BOT_TOKEN> --guild <ID> --channel <ID>` |
| Remove a channel | 12 | `openclaw channels remove <channel-id>` |
| Connect the Mac Studio node | 13 | `openclaw node install --host spark-caeb.tail48bab7.ts.net --port 443 --tls` (on Mac) |
| Connect an iPhone as a node | 14 | Install app, enter gateway URL, approve in sandbox with `openclaw devices approve <id>` |
| Take a Mac screenshot from agent | 15 | `openclaw node invoke mac-studio --tool screen-capture` |
| Schedule a morning digest | 16 | `openclaw cron add "0 9 * * *" "<prompt>"` |
| Schedule a health watchdog | 17 | `openclaw cron add "*/30 * * * *" "<prompt>"` |
| Pause or delete a cron job | 18 | `openclaw cron disable <id>` or `openclaw cron delete <id>` |
| Create a specialized agent | 19 | `openclaw agents add <name> --model <model> --system-prompt "..."` |
| Route a Telegram bot to an agent | 20 | `openclaw agents bind <agent> --channel telegram --target <token>` |
| Delete an agent | 21 | `openclaw agents delete <name>` |
| Fix Web UI origin errors | 22 | Edit `openclaw.json` allowed origins, restart gateway |
| Change settings in browser | 23 | Dashboard → Settings gear icon |
| Check for security issues | 24 | `openclaw security audit --deep` (inside sandbox) |
| Find what the agent remembers | 25 | `openclaw memory search "<topic>"` |
| Add a persistent preference | 26 | Write to `/sandbox/.openclaw/agents/main/memory/` then `openclaw memory index` |
| Auto-summarize sessions | 27 | `openclaw hooks enable session-end --handler "<prompt>"` |
| Stream sandbox logs | 28 | `openshell logs nemoclaw-main --tail` |
| Watch network policy decisions | 29 | `openshell term` (then Tab/A/D to navigate and approve) |
| Check everything is working | 30 | `~/workspace/nemoclaw/health-check.sh` |
| Switch main agent to Claude (subscription) | 31 | `openclaw configure --section model` → Anthropic → browser login |
| Switch main agent to Gemini (subscription) | 32 | `openclaw configure --section model` → Google → browser login |
| Understand subscription vs API key auth | 33 | See Recipe 33 comparison table |
| Use cloud provider without API key | 34 | `openclaw configure --section model` inside sandbox (not `openshell provider`) |
