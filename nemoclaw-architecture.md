# NemoClaw: Architecture & Conceptual Guide

*Adapted for: DGX Spark + Mac Studio M4 Max + Raspberry Pi*
*March 2026 • carlos@mac-studio.local*

---

## 1. The Challenge

You want to run always-on AI agents — coding assistants, personal assistants, automation bots — on your own hardware. But running AI agents directly on your machines creates real problems:

- **Security**: An unconstrained agent can read any file, make any network request, access credentials, and exfiltrate data. OpenClaw, Claude Code, Codex — they all run with the permissions of whatever user starts them.
- **Privacy**: If the agent sends your code or data to a cloud model, you've lost control of it. You need inference to stay local by default.
- **Control**: You need to decide what the agent can access, what models it uses, and what network destinations it can reach — and change these decisions without restarting everything.
- **Multi-machine complexity**: You have a DGX Spark (128GB, Blackwell GPU) and a Mac Studio (36GB, M4 Max). You want to leverage both, not just one.

NemoClaw exists to solve all four problems at once.

---

## 2. What Is NemoClaw?

NemoClaw is **NVIDIA's open-source reference stack** for running AI agents safely and privately. It is not a single application — it is an orchestrated combination of:

- **OpenShell** — The secure sandbox runtime (the security and isolation layer)
- **OpenClaw** — The AI agent with browser UI (the application layer)
- **Nemotron models** — NVIDIA's optimized inference models (the intelligence layer)
- **NemoClaw CLI** — The orchestration tool that wires everything together (the glue)

Think of NemoClaw as the answer to: *"How do I run OpenClaw safely, with local inference, on my own hardware, without giving the agent unrestricted access to everything?"*

### What NemoClaw is NOT

- It is NOT just OpenClaw with Nemotron. That would be an unsandboxed agent with a local model — no security.
- It is NOT a Kubernetes deployment. k3s runs internally inside OpenShell — you never touch it.
- It is NOT cloud-dependent. Local models are the DEFAULT. Cloud models are opt-in.

---

## 3. The Three-Layer Architecture

The NemoClaw workflow diagram shows three distinct layers. Every request from every agent passes through all three:

```
┌─────────────────────────────────────────────────────────┐
│                     SANDBOXES                            │
│                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │  Claw         │  │  Claude       │  │  Cursor       │  │
│  │  Tools + MCP  │  │  Tools + MCP  │  │  Tools + MCP  │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘   │
│         │                 │                 │            │
│         ▼                 ▼                 ▼            │
│  ┌─────────────────────────────────────────────────────┐ │
│  │                   GUARDRAILS                        │ │
│  │                                                     │ │
│  │  🔑 Access        🔒 Privacy        ⚡ Skills       │ │
│  │  (who can do      (what data        (what agent     │ │
│  │   what)            stays local)      capabilities)  │ │
│  └──────────────────────┬─────────────────────────────┘ │
│                         │                                │
│                         ▼                                │
│  ┌─────────────────────────────────────────────────────┐ │
│  │            PRIVATE INFERENCE ROUTER                 │ │
│  │                                                     │ │
│  │  🖥️ Local Open Models      ☁️ Frontier Models       │ │
│  │     (DEFAULT)                  (OPT-IN ONLY)        │ │
│  │                                                     │ │
│  │  ◄── Approved data ──►  ◄── Frontier models ──►    │ │
│  │      sources only            (opt-in only)          │ │
│  └─────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

### Layer 1: Sandboxes — Where Agents Run

Each agent (Claw, Claude Code, Cursor, Codex) runs inside an **isolated OpenShell sandbox**. A sandbox is a container with:

- **Kernel-level isolation**: Landlock LSM restricts filesystem access. seccomp filters system calls. Network namespaces isolate network traffic.
- **Filesystem confinement**: Agents can only write to `/sandbox` and `/tmp`. All system paths are read-only.
- **Default-deny networking**: ALL outbound connections are blocked unless explicitly allowed by policy.
- **Tools + MCP**: Each agent brings its own tools and can use MCP (Model Context Protocol) servers — but all tool calls pass through the guardrails.

**Key insight**: You can run MULTIPLE agents in SEPARATE sandboxes simultaneously. Claw in one, Claude Code in another, Codex in a third. Each has its own policy, its own filesystem, its own network rules. They don't interfere with each other.

### Layer 2: Guardrails — What Agents Can Do

The guardrails layer sits between the agent and the outside world. Three types of control:

**Access Control** — Who can reach what network destinations:
- Declarative YAML policies define allowed endpoints (host, port, HTTP methods)
- L7 (application-layer) inspection: can allow GET but block POST to the same endpoint
- Unknown destinations trigger operator approval via the TUI (`openshell term`)
- Approved endpoints persist for the session but don't modify the baseline policy

**Privacy Control** — What data stays local:
- Inference routes through the Private Inference Router, not directly to the internet
- Local models are the DEFAULT — data never leaves your hardware unless you opt in
- Network policies ensure agents can't exfiltrate data to unapproved endpoints
- TLS termination allows the proxy to inspect and enforce per-request rules

**Skills Control** — What agent capabilities are available:
- Policy presets can be added/removed per sandbox (`nemoclaw <name> policy-add`)
- Skills (GitHub access, npm registry, Telegram, etc.) are explicitly enabled
- Agents can self-modify their skills, but the sandbox constrains what those skills can actually DO

### Layer 3: Private Inference Router — Where Intelligence Comes From

The inference router (`inference.local`) is the mechanism that keeps model access private:

- **All sandboxes call `https://inference.local/v1`** — they never know the actual model endpoint
- **The OpenShell gateway intercepts** these requests, injects the real credentials, and forwards to the configured provider
- **Client credentials are never transmitted** — the agent doesn't see or handle API keys
- **Hot-reloadable**: Switch models in ~5 seconds without restarting sandboxes

Two tiers of models:

| Tier | Default? | Examples | Data handling |
|------|----------|---------|---------------|
| **Local Open Models** | YES (default) | Nemotron 3 Super 120B, Nemotron Nano 30B, Qwen, Llama | Data stays on your hardware |
| **Frontier Models** | No (opt-in) | Claude, GPT-4.1, Nemotron via NVIDIA Cloud | Data sent to cloud provider |

---

## 4. Component Deep Dive

### 4.1 NemoClaw CLI — The Orchestrator

The `nemoclaw` CLI is the entry point. It uses a **plugin-blueprint architecture**:

- **Plugin** (TypeScript): Lightweight, runs in-process with OpenClaw. Handles CLI commands, blueprint resolution, and sandbox connection.
- **Blueprint** (Python): Versioned artifact with all orchestration logic. Downloads, verifies digest, plans resources, and applies via OpenShell CLI.

**Blueprint lifecycle:**
```
Resolve → Verify (digest) → Plan (what resources to create) → Apply (via openshell CLI) → Status
```

**Key commands:**

| Command | What it does |
|---------|-------------|
| `nemoclaw onboard` | Interactive setup wizard — creates gateway, providers, sandbox, policies |
| `nemoclaw setup-spark` | DGX Spark-specific setup (cgroup v2, Docker fixes) |
| `nemoclaw <name> connect` | Shell into a running sandbox |
| `nemoclaw <name> status` | Show health, blueprint state, inference config |
| `nemoclaw <name> logs` | Stream sandbox and blueprint logs |
| `nemoclaw <name> destroy` | Stop and delete sandbox |
| `nemoclaw <name> policy-add` | Add a policy preset |
| `nemoclaw <name> policy-list` | Show available and applied policies |
| `nemoclaw list` | List all sandboxes with model/provider info |
| `nemoclaw start/stop` | Start/stop auxiliary services (Telegram bridge) |
| `openshell term` | Real-time TUI for monitoring and approving requests |

### 4.2 OpenShell — The Runtime

OpenShell is the secure sandbox runtime. It provides:

**Gateway**: The control plane. Runs inside Docker on the host. Manages sandbox lifecycle, provider credentials, inference routing, and policy enforcement.

```bash
openshell gateway start                    # Local deployment
openshell gateway start --remote user@host # Remote deployment (e.g., DGX Spark from Mac)
openshell gateway add https://...          # Register existing gateway
```

**Sandboxes**: Isolated containers where agents run. Created from community images (OpenClaw, Claude Code, Codex) or custom configurations.

```bash
openshell sandbox create --from openclaw --name nemoclaw-main --keep --forward 18789
```

**Providers**: Named credential bundles for inference backends and external services.

| Provider type | What it connects to |
|---------------|-------------------|
| `nvidia` | NVIDIA API Catalog (build.nvidia.com) |
| `openai` | Any OpenAI-compatible endpoint (Ollama, vLLM, OpenAI) |
| `anthropic` | Anthropic API |
| `claude` | Claude Code credentials |
| `github` | GitHub tokens |
| `generic` | Any custom credential |

**Inference routing**: The `inference.local` mechanism.
```
Agent → https://inference.local/v1 → OpenShell Gateway → Provider → Model
```

**Policies**: Declarative YAML files controlling filesystem access, network egress, and process isolation.

### 4.3 OpenClaw — The Agent

OpenClaw is the AI agent that runs inside the sandbox. It provides:

- **Chat UI** on port 18789 (browser-based)
- **TUI mode** via `openclaw tui` (terminal-based)
- **CLI mode** via `openclaw agent --agent main --local -m "prompt"`
- **Tools + MCP** for interacting with external services
- **Persistent memory** across conversations
- **Self-evolving skills** with hot-reload
- **macOS companion app** (OpenClaw.app) — menu bar, voice wake, native notifications
- **iOS app** (TestFlight) — camera, location, voice, screen snapshot as agent nodes

### 4.4 Nemotron Models — The Intelligence

NVIDIA's optimized models available through inference profiles:

| Model | Size | Context | Best for |
|-------|------|---------|----------|
| Nemotron 3 Super 120B | ~86 GB | 131K | General reasoning, long context (fits in DGX Spark 128GB) |
| Nemotron Ultra 253B | ~140 GB | 131K | Maximum quality (requires quantization on Spark) |
| Nemotron Super 49B v1.5 | ~30 GB | 131K | Balanced quality/speed |
| Nemotron 3 Nano 30B | ~18 GB | 131K | Fast responses, lighter hardware |

### 4.5 Ollama — The Local Model Server

Ollama serves as the local inference engine. It:
- Loads models into GPU memory on demand
- Exposes an OpenAI-compatible API on port 11434
- Supports NVIDIA GPUs (CUDA) and Apple Silicon (Metal)
- Can serve Nemotron models and any other GGUF/safetensors model

### 4.6 Coding Agent CLIs — The Development Tools

NemoClaw's sandbox model supports running multiple coding agents simultaneously, each isolated with its own policies. Three key agents integrate into the stack:

#### Claude Code (Anthropic)

| Aspect | Detail |
|--------|--------|
| **OpenShell support** | **Full** — works out of the box, first-class citizen |
| **Default policy** | Complete — `claude_code` network policy pre-configured for `api.anthropic.com`, `statsig.anthropic.com`, `sentry.io` |
| **Authentication** | `ANTHROPIC_API_KEY` — auto-discovered by `--from-existing` |
| **Inference** | Uses Anthropic's API (cloud) — data goes to Anthropic. Can ALSO use `inference.local` for local model routing if configured |
| **Sandbox creation** | `openshell sandbox create --provider my-claude -- claude` |
| **MCP support** | Yes — configure in `.claude/settings.json` inside the sandbox |
| **Unique value** | Best code reasoning, extended thinking, deep context understanding |

Claude Code is the most seamlessly integrated agent in OpenShell. It gets full policy coverage out of the box, meaning no custom network policy work is needed. The agent auto-discovers credentials and is ready immediately after sandbox creation.

#### Codex CLI (OpenAI)

| Aspect | Detail |
|--------|--------|
| **OpenShell support** | Pre-installed in base image, but **no default policy** — requires custom policy |
| **Default policy** | None — you must create a policy allowing OpenAI endpoints and Codex binary paths |
| **Authentication** | `OPENAI_API_KEY` — or configure any provider in `~/.codex/config.toml` |
| **Inference** | Supports **multiple providers natively**: OpenAI, Ollama, Gemini, Azure, OpenRouter, Groq, Mistral, DeepSeek, xAI |
| **Ollama integration** | **Native** — configure as a provider with `base_url = "http://localhost:11434/v1"` |
| **Sandbox creation** | `openshell sandbox create --provider my-codex -- codex` |
| **Own sandbox model** | Yes — Codex has its own sandbox modes: `read-only`, `workspace-write`, `danger-full-access`, `external-sandbox` |
| **MCP support** | Yes — configure in `~/.codex/config.toml` under `[mcp_servers]` |
| **Unique value** | Multi-provider flexibility, native Ollama support, can use local models without OpenShell inference routing |

Codex is uniquely powerful here because it has **native Ollama support**. Inside an OpenShell sandbox, you can configure Codex to talk directly to the Spark's Ollama (via `host.openshell.internal:11434`) OR use `inference.local` for OpenShell-managed routing. This gives you two paths to local inference.

Codex `config.toml` for local Ollama inside an OpenShell sandbox:
```toml
# ~/.codex/config.toml (inside sandbox)
model = "nemotron-3-super:120b"
model_provider = "ollama"

[model_providers.ollama]
name = "Ollama"
base_url = "http://host.openshell.internal:11434/v1"
env_key = "OLLAMA_API_KEY"
wire_api = "responses"

sandbox_mode = "external-sandbox"  # Let OpenShell handle sandboxing
```

#### Gemini CLI (Google)

| Aspect | Detail |
|--------|--------|
| **OpenShell support** | **Not listed** as a supported agent — requires custom sandbox setup |
| **Default policy** | None — you must create a custom policy allowing Google API endpoints |
| **Authentication** | `GEMINI_API_KEY` or `GOOGLE_API_KEY` or Google Cloud credentials |
| **Inference** | Uses Google's Gemini API directly (NOT OpenAI-compatible) — data goes to Google |
| **Own sandbox mode** | Yes — `GEMINI_SANDBOX=docker` (has its own Docker-based sandboxing) |
| **MCP support** | Yes — configure in `.gemini/settings.json` under `mcpServers` |
| **Extensions** | Supports custom extensions with MCP servers, agents, and skills |
| **Headless mode** | `gemini -p "prompt"` for non-interactive execution |
| **Unique value** | Google ecosystem integration, extensions system, Gemini 3 Flash model access |

Gemini CLI is NOT a listed OpenShell agent, but it CAN run inside an OpenShell sandbox with a custom Dockerfile and policy. The key challenge is that Gemini uses Google's own API (not OpenAI-compatible), so it can't use `inference.local` for local model routing. It always calls Google's cloud.

**Network policy needed for Gemini CLI in OpenShell:**
```yaml
network_policies:
  - name: gemini_api
    destination:
      host: generativelanguage.googleapis.com
      port: 443
    tls: terminate
    enforcement: enforce
    allowed_methods: [GET, POST]
    allowed_binaries: [/usr/local/bin/node]
  - name: gemini_auth
    destination:
      host: oauth2.googleapis.com
      port: 443
    tls: terminate
    enforcement: enforce
    allowed_methods: [GET, POST]
    allowed_binaries: [/usr/local/bin/node]
```

#### How All Four Agents Coexist

```
┌────────────────────────────────────────────────────────────┐
│                    OpenShell Gateway                         │
│                                                             │
│  ┌─────────────┐ ┌─────────────┐ ┌──────────┐ ┌─────────┐ │
│  │  OpenClaw    │ │ Claude Code │ │  Codex   │ │ Gemini  │ │
│  │  (NemoClaw)  │ │             │ │          │ │  CLI    │ │
│  │             │ │             │ │          │ │         │ │
│  │ inference   │ │ Anthropic   │ │ Ollama   │ │ Google  │ │
│  │ .local ─────│─│─────────────│─│──────────│─│─────────│ │
│  │ (Nemotron)  │ │ API (cloud) │ │ (local!) │ │ API     │ │
│  │             │ │ OR inf.local│ │ OR inf.  │ │ (cloud) │ │
│  │             │ │             │ │ local    │ │         │ │
│  │  Policy:    │ │  Policy:    │ │ Policy:  │ │ Policy: │ │
│  │  bundled    │ │  full       │ │ custom   │ │ custom  │ │
│  └─────────────┘ └─────────────┘ └──────────┘ └─────────┘ │
│                                                             │
│  Each sandbox: isolated filesystem, network, credentials    │
│  Shared: inference.local routing, gateway monitoring (TUI)  │
└────────────────────────────────────────────────────────────┘
```

**Key differences in how each agent uses inference:**

| Agent | Default inference path | Can use inference.local? | Can use local Ollama directly? |
|-------|----------------------|--------------------------|-------------------------------|
| OpenClaw | `inference.local` → Nemotron (local) | Yes (primary path) | Via inference.local |
| Claude Code | Anthropic API (cloud) | Yes (can be configured) | Via inference.local |
| Codex | Configurable — OpenAI, Ollama, etc. | Yes | **Yes, natively** (config.toml) |
| Gemini CLI | Google Gemini API (cloud) | No (not OpenAI-compatible) | No |

**MCP as the shared extensibility layer:** All four agents support MCP. This means you can create MCP servers (filesystem access, database connections, API integrations) that are shared across agents. A single MCP server for GitHub, for example, can be used by OpenClaw, Claude Code, Codex, and Gemini CLI simultaneously.

---

## 5. Your Hardware: Capabilities and Constraints

### DGX Spark (spark-caeb.local)

| Spec | Value | Implication |
|------|-------|-------------|
| GPU | NVIDIA GB10 Blackwell | Native CUDA support, TensorRT-LLM, NIM containers |
| RAM | 128 GB UMA | Can run Nemotron 3 Super 120B (~86GB) with 42GB headroom |
| OS | DGX OS (Ubuntu 24.04, ARM64) | Full Linux kernel features: Landlock, seccomp, cgroups v2 |
| Docker | 29.1.3 | Meets OpenShell requirement (≥28.04) |
| Disk | 69% used (2.4TB) | ~800GB free — adequate but monitor before pulling new models |
| Role | **Primary inference + NemoClaw host** | Runs OpenShell gateway, all sandboxes, Ollama with Nemotron |

**Constraints:**
- ARM64 architecture — all containers must be multi-arch (OpenShell provides `linux/arm64` images)
- Disk pressure — monitor with `df -h` before pulling models
- Single GPU — no multi-GPU parallelism, but 128GB UMA compensates
- `nemoclaw setup-spark` needed for cgroup v2 and Docker fixes

### Mac Studio (mac-studio.local)

| Spec | Value | Implication |
|------|-------|-------------|
| GPU | Apple M4 Max | Metal acceleration for Ollama, excellent for ≤20B models |
| RAM | 36 GB unified | Can run qwen3:8b (~5.5GB) or nemotron-nano (~18GB) |
| OS | macOS 15.4 (ARM64) | OpenShell supported via Docker Desktop or Colima |
| Role | **Dev workstation + secondary inference + companion app host** | Runs OpenClaw.app, IDE, fast models via Ollama |

**Constraints:**
- macOS kernel doesn't support Landlock/seccomp natively — security runs inside Docker Desktop's Linux VM
- 36GB limits model size — Nemotron Super 120B won't fit
- Docker Desktop or Colima required for OpenShell
- Ollama must be started separately (not managed by OpenShell)

**Unique capabilities:**
- OpenClaw.app (macOS companion) — menu bar, voice wake, screen recording, AppleScript
- Can run OpenShell gateway in remote mode pointing to Spark
- LM Studio available as alternative inference server
- Primary user interaction point (browser, IDE, terminal)

### Raspberry Pi (raspi.local)

| Spec | Value | Implication |
|------|-------|-------------|
| CPU | ARM64 | Can run lightweight services |
| RAM | 3.7 GB | No models, no heavy workloads |
| OS | Debian Bookworm | Stable, low-maintenance |
| Role | **Infrastructure control plane** | LiteLLM proxy, DNS, monitoring, Tailscale router |

**Constraints:**
- Cannot run ANY inference
- Cannot run OpenShell sandboxes (insufficient RAM)
- Cannot run Docker effectively (too resource-constrained)

**Value proposition:**
- **LiteLLM Proxy** (~300MB): Unified API gateway routing to Spark + Mac Ollama
- **Pi-hole/CoreDNS** (~60MB): DNS resolution for `spark.lab`, `mac.lab`, `ai.lab`
- **Uptime Kuma** (~120MB): Monitors all endpoints
- **Tailscale subnet router** (~40MB): Remote access without per-device Tailscale installs
- **Always-on at 5W**: Infrastructure services that should never sleep

---

## 6. How NemoClaw Adapts to Your Hardware

### Architecture Diagram: Your Deployment

```
┌─────────────────────────────────────────────────────────────────────┐
│                          Tailscale Mesh                              │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │                    DGX Spark (spark-caeb.local)                  │ │
│  │                    ═══════════════════════════                   │ │
│  │                                                                  │ │
│  │  ┌─────────────────────────── SANDBOXES ──────────────────────┐ │ │
│  │  │                                                             │ │ │
│  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │ │ │
│  │  │  │  OpenClaw     │  │  Claude Code  │  │  Codex       │     │ │ │
│  │  │  │  (NemoClaw)   │  │  (sandbox)   │  │  (sandbox)   │     │ │ │
│  │  │  │  :18789       │  │              │  │              │     │ │ │
│  │  │  │  Tools + MCP  │  │  Tools + MCP │  │  Tools + MCP │     │ │ │
│  │  │  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘     │ │ │
│  │  │         └────────────┬────┴────────────┬─────┘             │ │ │
│  │  └──────────────────────┼─────────────────┼───────────────────┘ │ │
│  │                         ▼                 ▼                      │ │
│  │  ┌────────────────── GUARDRAILS ─────────────────────────────┐  │ │
│  │  │  🔑 Access Policies    🔒 Privacy Router    ⚡ Skills     │  │ │
│  │  │  (YAML egress rules)   (inference.local)    (presets)    │  │ │
│  │  └──────────────────────────┬────────────────────────────────┘  │ │
│  │                             ▼                                    │ │
│  │  ┌──────────────── INFERENCE ROUTER ─────────────────────────┐  │ │
│  │  │                                                            │  │ │
│  │  │  ┌────────────────────────┐  ┌──────────────────────────┐ │  │ │
│  │  │  │  LOCAL (default)       │  │  CLOUD (opt-in)          │ │  │ │
│  │  │  │                        │  │                          │ │  │ │
│  │  │  │  Ollama :11434         │  │  NVIDIA API Catalog      │ │  │ │
│  │  │  │  ├ nemotron-3-super    │  │  (build.nvidia.com)      │ │  │ │
│  │  │  │  │  :120b (86GB)       │  │                          │ │  │ │
│  │  │  │  ├ qwen3-coder-next    │  │  Anthropic API           │ │  │ │
│  │  │  │  │  :q4_K_M (51GB)     │  │  (opt-in, via policy)   │ │  │ │
│  │  │  │  └ nemotron-nano       │  │                          │ │  │ │
│  │  │  │    :30b (18GB)         │  │  OpenAI API              │ │  │ │
│  │  │  │                        │  │  (opt-in, via policy)    │ │  │ │
│  │  │  └────────────────────────┘  └──────────────────────────┘ │  │ │
│  │  └────────────────────────────────────────────────────────────┘  │ │
│  │                                                                  │ │
│  │  OpenShell Gateway :8080  │  Docker + k3s (internal)            │ │
│  └──────────────────────────────────────────────────────────────────┘ │
│                          │                                            │
│  ┌───────────────────────┼──────────────────────────────────────────┐ │
│  │  Mac Studio (mac-studio.local)                                   │ │
│  │  ═══════════════════════════════                                 │ │
│  │                       │                                          │ │
│  │  ┌─────────────┐     │     ┌──────────────────┐                 │ │
│  │  │ OpenClaw.app │     │     │ Ollama :11434     │                 │ │
│  │  │ (companion)  │     │     │ ├ qwen3:8b        │                 │ │
│  │  │ Menu bar     │─── WS ──▶│ ├ nemotron-nano   │                 │ │
│  │  │ Voice wake   │  :18789   │ └ embeddings      │                 │ │
│  │  │ Screen/Camera│           └──────────────────┘                 │ │
│  │  └─────────────┘                                                 │ │
│  │                                                                  │ │
│  │  IDE • Claude Code • Codex • Browser (NemoClaw UI)              │ │
│  └──────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────────┐ │
│  │  Raspberry Pi (raspi.local)                                      │ │
│  │  ══════════════════════════                                      │ │
│  │                                                                  │ │
│  │  ┌──────────────┐  ┌─────────────┐  ┌────────────────────┐      │ │
│  │  │ LiteLLM :4000│  │ Pi-hole DNS │  │ Uptime Kuma        │      │ │
│  │  │ Unified API  │  │ spark.lab   │  │ Monitors all       │      │ │
│  │  │ gateway      │  │ mac.lab     │  │ endpoints          │      │ │
│  │  │ Routes to:   │  │ ai.lab      │  └────────────────────┘      │ │
│  │  │ ├ Spark:11434│  └─────────────┘                               │ │
│  │  │ └ Mac:11434  │  ┌─────────────┐  ┌────────────────────┐      │ │
│  │  └──────────────┘  │ Tailscale   │  │ Homepage           │      │ │
│  │                     │ subnet      │  │ Lab dashboard      │      │ │
│  │                     │ router      │  └────────────────────┘      │ │
│  │                     └─────────────┘                               │ │
│  └──────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────────┐ │
│  │  Mobile Devices (iPhone, iPad)                                   │ │
│  │                                                                  │ │
│  │  iOS App (TestFlight) — Camera, Location, Voice → agent node    │ │
│  │  Browser — NemoClaw UI at spark-caeb:18789 via Tailscale        │ │
│  └──────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

### How Requests Flow

```
1. You type in the OpenClaw UI (browser on Mac, or OpenClaw.app)
   │
2. Request reaches OpenClaw agent inside the sandbox on Spark
   │
3. Agent decides it needs inference → calls https://inference.local/v1
   │
4. OpenShell gateway intercepts the request
   │
5. Gateway checks: which provider is active?
   ├─ LOCAL (default): Forwards to Ollama at spark:11434
   │   └─ Ollama serves nemotron-3-super:120b from GPU memory
   │
   └─ CLOUD (opt-in): Forwards to build.nvidia.com
       └─ NVIDIA API Catalog serves the model
   │
6. Response flows back: Model → Gateway → Sandbox → OpenClaw → Your browser
   │
7. If the agent needs to make a network request (GitHub, npm, etc.):
   ├─ Policy check: Is this destination allowed?
   ├─ YES → Request proceeds
   └─ NO → Blocked. TUI shows operator approval prompt.
```

### What Each Machine Does (and Why)

**DGX Spark — The Brain**
- Runs the OpenShell gateway (the control plane for everything)
- Hosts ALL sandboxes (OpenClaw, Claude Code, Codex — all isolated)
- Runs Ollama with Nemotron 3 Super 120B as the default local model
- Enforces all guardrails (access, privacy, skills)
- This is the ONLY machine that needs the full NemoClaw stack
- `nemoclaw setup-spark` optimizes Docker/cgroup configuration specifically for DGX

**Mac Studio — The Interface**
- Your primary interaction point (browser, IDE, terminal)
- Runs OpenClaw.app (macOS companion — voice wake, screen, camera, AppleScript)
- Runs Ollama with fast models (qwen3:8b) for lightweight/secondary inference
- Connects to Spark's gateway via SSH or Tailscale
- Can manage the Spark's OpenShell remotely: `openshell gateway start --remote carlos@spark-caeb.local`
- Runs coding agents (Claude Code, Codex) that point to Spark for heavy inference

**Raspberry Pi — The Infrastructure**
- LiteLLM proxy: Provides a UNIFIED API endpoint at `ai.lab:4000` that routes to both Spark and Mac Ollama by model name
- DNS: Resolves `spark.lab`, `mac.lab`, `ai.lab` so you never type IP addresses
- Monitoring: Uptime Kuma watches all services, alerts on failures
- Tailscale subnet router: Remote access to the entire lab from one node
- Always on at 5W — infrastructure that never sleeps

---

## 7. Benefits of This Setup

### Privacy by Default
- Nemotron 3 Super 120B runs LOCALLY on the Spark — your data never leaves your network
- The Private Inference Router ensures agents can't call cloud models unless you explicitly opt in
- Network policies block data exfiltration to unapproved endpoints

### Security Through Isolation
- Every agent runs in its own sandbox with kernel-level isolation
- Filesystem: agents write only to `/sandbox` and `/tmp`
- Network: default-deny with explicit allowlists
- Credentials: injected by the gateway, never visible to agents

### Multi-Agent, Multi-Model
- Run OpenClaw, Claude Code, and Codex simultaneously in separate sandboxes
- Each sandbox can use a different inference provider
- Switch the active model for any sandbox in ~5 seconds without restart

### Optimized for DGX Spark
- `nemoclaw setup-spark` applies Spark-specific optimizations
- 128GB UMA fits Nemotron 120B with room for concurrent models
- ARM64 multi-arch container images built for Spark's architecture
- NVIDIA Container Runtime for GPU passthrough to sandboxes

### Remote Everything
- Manage Spark's gateway from the Mac: `openshell gateway start --remote`
- Access NemoClaw UI from anywhere via Tailscale
- OpenClaw.app on Mac connects to Spark's gateway via WebSocket
- iOS app connects via Tailscale for mobile access

---

## 8. Restrictions and Constraints

### OpenShell Limitations
- **Single active inference route per gateway**: All sandboxes on a gateway share the same inference provider. To use different models, switch with `openshell inference set` or run separate gateways.
- **Single-node**: Sandboxes can't span multiple machines. The gateway and all its sandboxes run on one host (the Spark).
- **Providers can't be added to running sandboxes**: Must recreate to add new providers.
- **Alpha software**: Single-player mode. One developer, one environment, one gateway.

### Hardware Constraints
- **DGX Spark disk 69% full**: ~800GB free. Monitor before pulling models.
- **Mac Studio 36GB**: Can't run Nemotron 120B. Limited to models ≤20-25B.
- **Pi 3.7GB RAM**: Infrastructure services only. No inference, no sandboxes.
- **Network**: Local network speed affects Mac↔Spark inference latency.

### Software Constraints
- **macOS OpenShell**: Landlock/seccomp run inside Docker VM, not native kernel. Slightly weaker isolation than Linux.
- **Ollama limitations**: No distributed inference across machines. Each instance is independent.
- **NemoClaw blueprint versioning**: Blueprint digest verification means you run tested, immutable versions. Custom modifications require forking.

---

## 9. Glossary

| Term | What it is |
|------|-----------|
| **NemoClaw** | The complete reference stack: OpenShell + OpenClaw + Nemotron + CLI orchestration |
| **OpenShell** | The secure sandbox runtime that provides isolation, policies, and inference routing |
| **OpenClaw** | The AI agent with browser UI, TUI, and companion apps |
| **Sandbox** | An isolated container where an agent runs with enforced policies |
| **Gateway** | The OpenShell control plane — manages sandboxes, providers, inference, policies |
| **Provider** | A named credential bundle pointing to an inference backend or external service |
| **inference.local** | The virtual endpoint inside sandboxes that routes to the configured model provider |
| **Blueprint** | Versioned, digest-verified Python artifact containing NemoClaw's orchestration logic |
| **Policy** | Declarative YAML file controlling filesystem access, network egress, and process isolation |
| **Guardrails** | The collective term for access control, privacy enforcement, and skills management |
| **Private Inference Router** | The OpenShell mechanism that routes model requests locally by default |
| **TUI** | Terminal User Interface (`openshell term`) for real-time monitoring and request approval |
| **MCP** | Model Context Protocol — standard for connecting agents to external tools and data sources |
| **Nemotron** | NVIDIA's family of optimized LLMs, from Nano (30B) to Ultra (253B) |
| **LiteLLM** | Open-source proxy that presents a unified OpenAI-compatible API across multiple backends |
