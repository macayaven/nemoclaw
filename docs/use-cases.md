# NemoClaw Use Cases

Step-by-step guides for the most common things you'll do with NemoClaw after deployment.

---

## Use Case 1: Chat with Nemotron 120B via Browser

**What:** Open a browser, ask questions, get answers from the 120B model running locally on your Spark. No data leaves your network.

**Steps:**

1. Open the NemoClaw UI:
   - **From Mac (LAN):** `http://spark-caeb.local:18789`
   - **From Mac (NVIDIA Sync):** Click the OpenClaw entry in the Sync app
   - **From anywhere (Tailscale):** `http://<spark-tailscale-ip>:18789`
   - **From Pi relay:** `http://ai.lab:18789` (if DNS configured)

2. Type your message in the chat interface.

3. The request flows: Browser -> OpenClaw UI -> OpenClaw Agent -> `inference.local` -> OpenShell Gateway -> Ollama -> Nemotron 120B -> response.

4. All inference happens on the Spark. Your data never leaves the local network.

**Verify it's working:**
```bash
curl -s -o /dev/null -w "%{http_code}" http://spark-caeb.local:18789
# Expected: 200
```

---

## Use Case 2: Switch to a Faster Model for Quick Questions

**What:** The 120B model is great for complex reasoning but slow for simple questions. Switch to the Mac's 8B model for fast responses, then switch back.

**Steps:**

1. Switch to the Mac's fast model:
   ```bash
   openshell inference set --provider mac-ollama --model qwen3:8b
   ```

2. Chat in the browser — responses come from Mac (sub-second latency).

3. When you need the heavy model back:
   ```bash
   openshell inference set --provider local-ollama --model nemotron-3-super:120b
   ```

4. No sandbox restart needed. The switch takes ~5 seconds.

**Available models:**
```bash
# Heavy reasoning (Spark)
openshell inference set --provider local-ollama --model nemotron-3-super:120b

# Code generation (Spark)
openshell inference set --provider local-ollama --model qwen3-coder-next:q4_K_M

# Fast chat (Mac)
openshell inference set --provider mac-ollama --model qwen3:8b

# NVIDIA Cloud (optional)
openshell inference set --provider nvidia-nim --model nvidia/nemotron-3-super-120b-a12b
```

---

## Use Case 3: Use Claude Code in a Secure Sandbox

**What:** Run Claude Code inside an OpenShell sandbox so it can't access your full filesystem or exfiltrate data. It gets access to Anthropic's API (cloud) but everything else is locked down.

**Steps:**

1. Connect to the Claude Code sandbox:
   ```bash
   openshell sandbox connect claude-dev
   ```

2. Inside the sandbox, Claude Code starts automatically. Authenticate via browser when prompted.

3. Use Claude Code normally — it has shell access, file editing, git, etc. — but only inside `/sandbox`.

4. If Claude Code tries to reach an unapproved endpoint, the request is blocked. You'll see it in the TUI:
   ```bash
   # In a separate terminal:
   openshell term
   # The TUI shows blocked requests and lets you approve/deny
   ```

5. Exit the sandbox:
   ```bash
   exit
   ```

**What Claude Code CAN do inside the sandbox:**
- Read/write files in `/sandbox` and `/tmp`
- Run shell commands
- Access GitHub (if policy allows)
- Call Anthropic's API for inference

**What it CANNOT do:**
- Read your home directory or system files
- Make network requests to unapproved endpoints
- Access API keys directly (injected by gateway)

---

## Use Case 4: Use Codex with Local Inference (No Cloud)

**What:** Run OpenAI's Codex CLI inside a sandbox, but instead of calling OpenAI's API, route inference to your local Nemotron model. Zero cloud dependency.

**Steps:**

1. Connect to the Codex sandbox:
   ```bash
   openshell sandbox connect codex-dev
   ```

2. Codex is pre-configured to use Ollama on the Spark (via `~/.codex/config.toml`):
   ```toml
   model = "nemotron-3-super:120b"
   model_provider = "ollama"
   ```

3. Use Codex normally:
   ```bash
   codex "explain this function"
   codex "add error handling to main.py"
   ```

4. All inference stays local — your code never leaves the Spark.

**Switch Codex to a different model:**
```bash
# Inside the sandbox, edit config:
# Change model = "qwen3-coder-next:q4_K_M" for code-focused tasks
```

---

## Use Case 5: Use Gemini CLI in a Sandbox

**What:** Run Google's Gemini CLI in an isolated sandbox with access to Gemini's API (cloud). The sandbox restricts which Google endpoints it can reach.

**Steps:**

1. Connect to the Gemini sandbox:
   ```bash
   openshell sandbox connect gemini-dev
   ```

2. Use Gemini CLI:
   ```bash
   gemini "analyze this codebase"
   gemini -p "what does this function do?" < main.py
   ```

3. Gemini uses Google's cloud API — data goes to Google. But the sandbox ensures it can only reach `generativelanguage.googleapis.com` and `oauth2.googleapis.com`. No other Google services.

---

## Use Case 6: Run Multiple Agents Simultaneously

**What:** Have OpenClaw, Claude Code, Codex, and Gemini CLI all running at the same time in separate sandboxes. Each has its own policies, filesystem, and network rules.

**Steps:**

1. Verify all sandboxes are running:
   ```bash
   openshell sandbox list
   # Should show: nemoclaw-main, claude-dev, codex-dev, gemini-dev
   ```

2. Open multiple terminals:
   ```bash
   # Terminal 1: OpenClaw chat
   # Open http://spark-caeb.local:18789 in browser

   # Terminal 2: Claude Code
   openshell sandbox connect claude-dev

   # Terminal 3: Codex
   openshell sandbox connect codex-dev

   # Terminal 4: Gemini
   openshell sandbox connect gemini-dev
   ```

3. Monitor all of them:
   ```bash
   # Terminal 5: Real-time dashboard
   openshell term
   ```

4. Each sandbox is fully isolated:
   - Files created in one are invisible to others
   - Network policies are independent
   - Inference routing is shared (all use the same active provider)

---

## Use Case 7: Approve or Deny Network Requests

**What:** When an agent tries to reach an endpoint not in its policy, you get an interactive prompt to approve or deny it.

**Steps:**

1. Start the monitoring TUI:
   ```bash
   openshell term
   ```

2. When an agent makes an unapproved network request, the TUI shows:
   ```
   BLOCKED: claude-dev → api.github.com:443 (POST /repos/...)
   Binary: /usr/local/bin/gh
   [A]pprove  [D]eny  [I]gnore
   ```

3. Press `A` to approve (for this session only) or `D` to deny.

4. To make the approval permanent, edit the sandbox policy:
   ```bash
   openshell policy get claude-dev --full > /tmp/policy.yaml
   # Add the endpoint to network_policies, then:
   openshell policy set claude-dev --policy /tmp/policy.yaml --wait
   ```

---

## Use Case 8: Access NemoClaw from Your Phone

**What:** Open the NemoClaw chat UI from your iPhone, anywhere in the world, via Tailscale.

**Steps:**

1. Make sure Tailscale is running on your phone and the Spark.

2. Open Safari on your phone and go to:
   ```
   http://<spark-tailscale-ip>:18789
   ```

3. Alternatively, install the OpenClaw iOS app (TestFlight):
   - The app auto-discovers the Spark gateway via Tailscale DNS-SD
   - Or manually enter the Tailscale IP + port 18789

4. Chat as usual — the Spark does all the inference.

---

## Use Case 9: Use LiteLLM as a Universal API Endpoint

**What:** Access any model on any machine through a single API endpoint at `ai.lab:4000`. Useful for scripts, notebooks, and third-party tools.

**Steps:**

1. The LiteLLM proxy runs on the Pi. It routes by model name:
   - `nemotron-3-super:120b` -> Spark Ollama
   - `qwen3-coder-next:q4_K_M` -> Spark Ollama
   - `qwen3:8b` -> Mac Ollama

2. Use it from any machine on the network:
   ```bash
   # From Python
   from openai import OpenAI
   client = OpenAI(base_url="http://ai.lab:4000/v1", api_key="unused")
   response = client.chat.completions.create(
       model="nemotron-3-super:120b",
       messages=[{"role": "user", "content": "hello"}],
   )
   print(response.choices[0].message.content)
   ```

3. Or via curl:
   ```bash
   curl http://ai.lab:4000/v1/chat/completions \
     -H "Content-Type: application/json" \
     -d '{"model": "qwen3:8b", "messages": [{"role": "user", "content": "hello"}]}'
   ```

4. Check available models:
   ```bash
   curl http://ai.lab:4000/v1/models
   ```

---

## Use Case 10: Monitor Everything from the Pi Dashboard

**What:** Check the health of all services from a single dashboard.

**Steps:**

1. Open Uptime Kuma in a browser:
   ```
   http://raspi.local:3001
   ```

2. The dashboard shows uptime/downtime for:
   - Spark Ollama (port 11434)
   - Spark OpenShell gateway (port 8080)
   - Spark NemoClaw UI (port 18789)
   - Mac Ollama (port 11434)
   - LiteLLM proxy (port 4000)

3. Configure alerts (email, Telegram, etc.) in the Uptime Kuma settings.

---

## Quick Reference: What's Where

| What you want to do | Where | Command/URL |
|---------------------|-------|-------------|
| Chat with AI | Browser | `http://spark-caeb.local:18789` |
| Switch models | Spark terminal | `openshell inference set --provider X --model Y` |
| Monitor sandboxes | Spark terminal | `openshell term` |
| Connect to Claude Code | Spark terminal | `openshell sandbox connect claude-dev` |
| Connect to Codex | Spark terminal | `openshell sandbox connect codex-dev` |
| Connect to Gemini | Spark terminal | `openshell sandbox connect gemini-dev` |
| Use any model via API | Any machine | `curl http://ai.lab:4000/v1/chat/completions` |
| Check health dashboard | Browser | `http://raspi.local:3001` |
| View sandbox logs | Spark terminal | `openshell logs <name> --tail` |
| Manage DNS | Browser | `http://raspi.local/admin` |
| Check GPU usage | Spark terminal | `nvidia-smi` |
| Check loaded models | Spark terminal | `ollama ps` |
| Stop everything | Spark terminal | `openshell gateway stop` |
| Start everything | Spark terminal | `openshell gateway start` |
