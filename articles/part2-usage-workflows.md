# 4 AI Agents, 1 Orchestrator, Zero Cloud: Practical Workflows with NemoClaw

**From code review pipelines to research-driven development — real workflows using local Nemotron 120B, Claude Code, Codex, and Gemini CLI**

*This is Part 2 of the NemoClaw series. [Part 1](./part1-deployment.md) covers deploying the system from scratch across two machines.*

---

## TL;DR

- NemoClaw's four sandboxed agents (OpenClaw, Claude Code, Codex, Gemini CLI) serve distinct roles and can be chained into automated pipelines.
- Codex and OpenClaw route all inference through local Nemotron 120B on the DGX Spark — your code never leaves the hardware.
- Claude Code and Gemini CLI use their respective cloud APIs, but remain contained in network-isolated sandboxes with explicit policy allowlists.
- The `Orchestrator` class coordinates sequential and parallel agent workflows with a simple Python API: `orc.research_and_implement("...")`.
- The system is accessible from Jupyter, VS Code, and custom scripts via the Ollama API on each machine.

---

## Recap: What Was Deployed in Part 1

Part 1 built the following across two physical machines:

**DGX Spark** (GB10 Blackwell, 128 GB UMA) — runs four isolated OpenShell sandboxes housing OpenClaw, Claude Code, Codex, and Gemini CLI. Ollama serves Nemotron 120B and Qwen Coder at `localhost:11434`. LM Studio provides an alternate inference server at `:1234`. The OpenShell gateway intercepts all sandbox network traffic, enforces YAML-defined policies, and routes inference through `inference.local` — a virtual TLS endpoint that resolves only inside sandboxes.

**Mac Studio M4 Max** (36 GB unified memory) — runs Gemma4 27B via Ollama, managed by Cursor IDE. A small Python TCP forwarder exposes it on `0.0.0.0:11435` so the Spark can reach it without disrupting Cursor's `localhost:11434` binding.

Both machines are connected through a Tailscale mesh VPN, and the OpenClaw UI is accessible over HTTPS at `https://spark-caeb.tail48bab7.ts.net/`.

---

## The Privacy Boundary

Before covering individual workflows, it is worth being explicit about which agents send data to external servers. This diagram shows the boundary:

```
YOUR NETWORK (stays private)             CLOUD (data leaves)
=====================================================|===========================
                                                    |
  [DGX Spark]                                       |
    Ollama: Nemotron 120B <---+                     |
    Ollama: Qwen Coder    <---+                     |
                              |                     |
  [Mac Studio]                |                     |
    Ollama: Gemma4 27B  <-----+                     |
                              |                     |
  Sandboxes:                  |                     |
    OpenClaw  ----------------+ local inference     |
    Codex     ----------------+ local inference     |
                                                    |  Claude Code ---> Anthropic API
                                                    |  Gemini CLI  ---> Google API
                                                    |
=====================================================|===========================
```

OpenClaw and Codex are wired to use `inference.local`, the gateway's internal proxy, which routes to whatever local provider is currently active. Both agents are structurally incapable of reaching the public internet for inference — the sandbox network policy does not permit it.

Claude Code connects to `api.anthropic.com`. Gemini CLI connects to `generativelanguage.googleapis.com`. Both sandboxes have explicit allowlists for those endpoints and nothing else. If Claude Code tries to reach an endpoint outside its policy, the gateway blocks the request and surfaces it in the `openshell term` TUI for your approval.

The practical consequence: **never put proprietary source code, internal API schemas, or customer data into a Claude or Gemini prompt without understanding that it will be sent to a third-party API.** For anything sensitive, use the Codex or OpenClaw sandboxes exclusively. For research, competitive analysis, or questions about public libraries — Claude and Gemini are fine.

---

## Scenario 1: Pure Local Code Review (Codex + OpenClaw)

**The scenario:** You have a code change that contains business logic you cannot share externally. You want AI-assisted review with absolute certainty that nothing leaves your network.

This is the default path: Codex handles code analysis inside its sandbox, routing inference through `inference.local` to Nemotron 120B on the Spark. No packet leaves the `192.168.1.0/24` subnet.

**Step 1: Connect to the Codex sandbox.**

```bash
source ~/workspace/nemoclaw/openshell-env/bin/activate
openshell sandbox connect codex-dev
```

You are now inside an isolated container. The filesystem is `/sandbox`. Network access is limited to `inference.local` and `host.openshell.internal:11434` (the Ollama API). Codex's config at `~/.codex/config.toml` points directly to the local model:

```toml
model = "nemotron-3-super:120b"
model_provider = "ollama"

[model_providers.ollama]
name = "Ollama (Spark Local)"
base_url = "http://host.openshell.internal:11434/v1"
wire_api = "responses"
```

**Step 2: Run the review.**

```bash
# Inside codex-dev sandbox — Codex requires a git repo in the working directory
cd ~/projects/myproject
codex "Review auth_service.py for security vulnerabilities, race conditions, and missing error handling"
```

Codex reads the file, sends the contents and your prompt to Nemotron 120B via Ollama's REST API at `host.openshell.internal:11434`, and streams the response back. The inference stays on Spark's GPU.

**Step 3: Pipe the review output to OpenClaw for a summarized action list.**

```bash
# From the Spark host (outside sandboxes):
source ~/workspace/nemoclaw/openshell-env/bin/activate
python3 - << 'EOF'
from orchestrator import Orchestrator
orc = Orchestrator()

code = open("/path/to/auth_service.py").read()
review = orc.delegate(
    f"Review this Python code for security issues:\n\n{code}",
    agent="codex",
    task_type="code_review",
)
summary = orc.delegate(
    f"Summarize this code review into a prioritized action list:\n\n{review}",
    agent="openclaw",
    task_type="analysis",
)
print(summary)
EOF
```

The Orchestrator's `delegate()` method creates a task record, dispatches the prompt into the target sandbox via `SandboxBridge`, and returns the response as a string. Both hops stay local.

**Why this matters:** Enterprise codebases — financial systems, medical software, anything under NDA — cannot use cloud AI for code review without legal review of the vendor's data handling policies. Local inference eliminates the question entirely. Nemotron 120B is competitive with frontier models on code analysis tasks, and the latency tradeoff (30-60 seconds cold start, then fast) is acceptable when the alternative is sending proprietary code to a third-party API.

---

## Scenario 2: Research-Driven Implementation (Gemini + Codex + Claude)

**The scenario:** You need to add a new feature but are not sure about the best approach. Research is not sensitive — it is about public APIs and design patterns. The actual implementation is sensitive because it contains your business logic.

This is the case for a hybrid strategy: let Gemini do the research (cloud is fine, nothing proprietary in a research question), implement with Codex (local, sensitive code stays on hardware), and review with Claude (cloud, but at this point you are sending general code patterns, not internal data).

The orchestrator's `research_and_implement()` pipeline automates exactly this flow:

```python
# From orchestrator.py:
def research_and_implement(self, prompt: str) -> PipelineResult:
    steps = [
        PipelineStep(
            agent="gemini",
            task_type="research",
            prompt_template=(
                "Research the following topic thoroughly and provide a comprehensive "
                "summary of best practices, available libraries, and implementation "
                "strategies:\n\n{prev_result}"
            ),
        ),
        PipelineStep(
            agent="codex",
            task_type="code_generation",
            prompt_template=(
                "Based on the following research, implement a complete, working "
                "solution with clear comments:\n\n"
                "Original request: {step_0_prompt}\n\n"
                "Research findings:\n{step_1_result}"
            ),
        ),
        PipelineStep(
            agent="claude",
            task_type="code_review",
            prompt_template=(
                "Review the following implementation for correctness, security, "
                "performance, and style. Provide specific, actionable feedback:\n\n"
                "{prev_result}"
            ),
        ),
    ]
    return self.pipeline(prompt, steps)
```

Each step's output is threaded into the next via the `{prev_result}` placeholder. The pipeline object also tracks individual step durations and task IDs for debugging.

**Running it:**

```bash
source ~/workspace/nemoclaw/orchestrator-env/bin/activate
python3 - << 'EOF'
from orchestrator import Orchestrator

orc = Orchestrator()
result = orc.research_and_implement(
    "Build a Python rate limiter using a sliding window algorithm"
)

print(f"Total time: {result.total_duration_ms:.0f}ms")
print(f"\nStep durations:")
for step in result.steps:
    print(f"  {step.agent} ({step.task_type}): {step.duration_ms:.0f}ms")

print(f"\nFinal review:\n{result.final_output}")
EOF
```

Sample output:

```
Total time: 94320ms

Step durations:
  gemini (research): 8210ms
  codex (code_generation): 52400ms
  claude (code_review): 33710ms

Final review:
The implementation looks solid. A few notes:
- Line 23: `time.time()` can have floating-point precision issues under load;
  prefer `time.monotonic_ns()` for the window boundary comparison.
- The `cleanup_old_entries()` method is called on every request — consider
  moving it to a background thread or caching the result for N ms.
- Missing type annotations on the public API. Add `-> bool` to `is_allowed()`.
```

**The hybrid model, summarized:** Research travels to Gemini's cloud because the question — "what are the best Python rate limiter approaches" — contains nothing proprietary. The implementation is generated by Codex against Nemotron 120B on the Spark because at this point you have a working code artifact. The review goes to Claude because code quality feedback on an algorithm is not equivalent to sharing your internal architecture — but use your judgment here. If the code contains internal service names, credentials, or business logic specifics, strip those before the review step.

---

## Scenario 3: Model Comparison (Parallel Specialists)

**The scenario:** You want to understand the quality versus speed tradeoff between Nemotron 120B (the heavy model on the Spark) and Gemma4 27B (the fast model on the Mac). You send the same prompt to both simultaneously and compare.

The orchestrator's `parallel_specialists()` method uses a `ThreadPoolExecutor` to dispatch prompts concurrently:

```python
source ~/workspace/nemoclaw/orchestrator-env/bin/activate
python3 - << 'EOF'
from orchestrator import Orchestrator
import time

orc = Orchestrator()

prompt = """
Explain the difference between a mutex and a semaphore.
Include a practical Python example for each.
"""

t_start = time.monotonic()
responses = orc.parallel_specialists(
    prompt,
    agents=["openclaw", "gemini"],   # openclaw -> Nemotron 120B; gemini -> Gemini
    task_type="analysis",
)
elapsed = (time.monotonic() - t_start) * 1000
print(f"Both responded in: {elapsed:.0f}ms\n")

for agent, response in responses.items():
    print(f"=== {agent.upper()} ===")
    print(response[:600])
    print()
EOF
```

To compare local Nemotron 120B against local Gemma4 27B, switch the active provider before the second request:

```bash
# Switch to Mac's fast model
openshell inference set --provider mac-ollama --model gemma4:27b

# Run the same prompt
python3 - << 'EOF'
from orchestrator import Orchestrator
orc = Orchestrator()
fast_response = orc.delegate(
    "Explain mutex vs semaphore with Python examples",
    agent="openclaw",
)
print(fast_response)
EOF

# Switch back to 120B
openshell inference set --provider local-ollama --model nemotron-3-super:120b
```

Switching takes roughly five seconds. No sandbox restart is needed.

**When to use which model:**

| Task | Model | Reason |
|------|-------|--------|
| Complex reasoning, architecture questions | Nemotron 120B | Depth of analysis |
| Quick one-liners, syntax questions | Gemma4 27B (Mac) | Fast responses, good quality |
| Code generation from detailed specs | Qwen Coder (Spark) | Code-tuned, local |
| Web research, long-context summarization | Gemini CLI | Large context window, web access |
| Code review with deep feedback | Claude Code | Strong on reasoning and style |

The Spark carries both Nemotron 120B (~86 GB) and Qwen Coder (~51 GB). Loading both simultaneously consumes 137 GB, which exceeds the 128 GB UMA budget. In practice, you keep one loaded and cold-start the other when needed. Ollama unloads the resident model automatically when a new one is requested.

---

## Scenario 4: Automated Code Fix Pipeline (Claude + Codex)

**The scenario:** You hand a file to the pipeline and want it to come back reviewed, fixed, and re-reviewed — without manual intervention between steps.

The `code_review_pipeline()` method runs a three-step loop: Claude reviews, Codex applies the fixes, Claude re-reviews the corrected version.

```python
# From orchestrator.py:
def code_review_pipeline(self, code: str) -> PipelineResult:
    steps = [
        PipelineStep(
            agent="claude",
            task_type="code_review",
            prompt_template=(
                "Review the following code carefully. List all bugs, anti-patterns, "
                "security issues, and style violations with specific line references "
                "where possible:\n\n{prev_result}"
            ),
        ),
        PipelineStep(
            agent="codex",
            task_type="code_generation",
            prompt_template=(
                "Apply the following code review feedback to improve the code. "
                "Return the complete corrected source file:\n\n"
                "Original code:\n{step_0_prompt}\n\n"
                "Review feedback:\n{step_1_result}"
            ),
        ),
        PipelineStep(
            agent="claude",
            task_type="code_review",
            prompt_template=(
                "Perform a final review of this revised code. Confirm that all "
                "previous issues have been addressed and identify any remaining "
                "concerns:\n\n{prev_result}"
            ),
        ),
    ]
    return self.pipeline(code, steps)
```

**Running it on a file:**

```bash
source ~/workspace/nemoclaw/orchestrator-env/bin/activate
python3 - << 'EOF'
from orchestrator import Orchestrator

orc = Orchestrator()
code = open("/sandbox/myproject/auth_service.py").read()

result = orc.code_review_pipeline(code)

print("=== INITIAL REVIEW ===")
print(result.steps[0].output)
print()
print("=== FIXED CODE ===")
print(result.steps[1].output)
print()
print("=== FINAL REVIEW ===")
print(result.steps[2].output)
print()
print(f"Pipeline completed in {result.total_duration_ms/1000:.1f}s")
EOF
```

The `{step_0_prompt}` placeholder — which resolves to the original code — is passed explicitly into the Codex fix step so that Codex has both the original code and Claude's review comments. Without this, Codex would only see the review comments with no code to fix.

This pattern works well as a pre-commit quality gate. You can wire it into a git pre-commit hook: if the final Claude review contains critical issues, the hook exits non-zero and blocks the commit.

**A note on iteration:** The current pipeline runs one review-fix-review cycle. For more aggressive quality enforcement, run the pipeline in a loop until Claude's final review contains no critical findings. In practice, one or two passes handle the majority of reviewable issues.

---

## Scenario 5: Mobile Agent Access

**The scenario:** You are away from your desk and want a quick answer from Nemotron 120B — not a small cloud model, your own hardware.

Tailscale handles the connectivity. The Spark runs `tailscale serve --bg 18789`, which creates `https://spark-caeb.tail48bab7.ts.net/` pointing to the OpenClaw UI. Any device on your Tailscale network — including your phone — can reach it.

**From an iPhone:**

1. Install the Tailscale app and sign in with the same account.
2. Open Safari and navigate to `https://spark-caeb.tail48bab7.ts.net/`.
3. The OpenClaw chat interface loads. Type your question. Nemotron 120B answers.

The Spark does all the inference. The phone is only rendering the response. Even on a slow mobile connection, the bottleneck is Nemotron's generation speed, not the network — the Tailscale latency on a home network is typically under 10ms.

For a native experience, the OpenClaw iOS app (TestFlight) supports auto-discovery of the Spark gateway via Tailscale DNS-SD. Alternatively, enter `https://spark-caeb.tail48bab7.ts.net/` manually in the app's server field.

**The access model matters:** `tailscale serve` requires Tailscale device authentication. Only devices you have approved in the Tailscale admin panel can reach the URL. This is meaningfully different from exposing the port directly (`tailscale funnel`) which makes it public. Keep it on `serve` unless you have a specific reason to share access externally.

---

## Advanced: The Orchestrator Pattern

The five scenarios above use the orchestrator as a convenience layer. This section explains how it works and when to extend it.

### Manager Pattern vs Handoff Pattern

The orchestrator in NemoClaw uses the Manager pattern from the OpenAI Agents SDK. The orchestrator retains control of the conversation at all times. It calls specialists as tools, gets back results, and decides what to do next. The alternative — the Handoff pattern — transfers control to a specialist, which runs autonomously until it decides to hand back. The Handoff pattern is harder to debug and can produce longer, less predictable loops.

For deterministic pipelines like code review, the Manager pattern is the right choice. The orchestrator knows exactly which agents run, in what order, and what each receives.

### Building a Custom Pipeline

The `pipeline()` method accepts any sequence of `PipelineStep` objects. Here is a custom pipeline that adds a security scan step between implementation and review:

```python
from orchestrator import Orchestrator, PipelineStep

orc = Orchestrator()

steps = [
    PipelineStep(
        agent="gemini",
        task_type="research",
        prompt_template="Research OWASP top 10 mitigations for:\n\n{prev_result}",
    ),
    PipelineStep(
        agent="codex",
        task_type="code_generation",
        prompt_template=(
            "Implement with security mitigations from this research:\n"
            "Feature: {step_0_prompt}\n"
            "Security guidance:\n{step_1_result}"
        ),
    ),
    PipelineStep(
        agent="openclaw",      # Local — no cloud
        task_type="analysis",
        prompt_template=(
            "Perform a security-focused review of this implementation. "
            "Reference OWASP where relevant:\n\n{prev_result}"
        ),
    ),
    PipelineStep(
        agent="claude",
        task_type="code_review",
        prompt_template=(
            "Final review — address any remaining issues:\n\n{prev_result}"
        ),
    ),
]

result = orc.pipeline("Build a user authentication endpoint with JWT", steps)
print(result.final_output)
```

### The Shared Workspace

Agents cannot directly read each other's sandbox filesystems. The inter-agent communication happens through the shared MCP workspace — a directory outside all sandboxes that is mounted read-write into each one.

```bash
# Set up shared workspace (Spark host)
mkdir -p ~/workspace/shared-agents/{inbox,outbox,context}

# Mount it into sandboxes on creation
openshell sandbox create --keep --name codex-dev \
    --upload ~/workspace/shared-agents:/shared \
    -- bash
```

Agents write outputs to `/shared/outbox/<agent>/`, read context from `/shared/context/`, and pick up tasks from `/shared/inbox/<agent>/`. The orchestrator writes task manifests and reads results using the `SharedWorkspace` class from `orchestrator/shared_mcp.py`.

This pattern scales to async workflows: you can have Claude write a spec to `/shared/outbox/claude/spec.md`, walk away, and have Codex pick it up whenever it is ready — without the orchestrator blocking on the response.

---

## Performance Notes

Understanding the latency profile helps set expectations and choose the right model for a task.

**Nemotron 120B on DGX Spark:**
- Cold start (model not loaded): 30-60 seconds for Ollama to load the weights into GPU memory.
- Warm inference: first token in 3-8 seconds, then ~15-25 tokens/second on the GB10 Blackwell.
- The `OLLAMA_KEEP_ALIVE=-1` setting keeps the model resident in GPU memory indefinitely, eliminating cold starts during a working session.

**Gemma4 27B on Mac Studio M4 Max:**
- Always warm (Cursor IDE keeps it loaded).
- First token in under one second. Suitable for autocomplete-speed interactions.
- Reached via the TCP forwarder on `:11435`. Forwarder overhead is negligible.

**Codex + Nemotron 120B for code generation:**
- Expect 45-90 seconds for a non-trivial code generation task. This includes Codex's own agent loop (it may make multiple model calls before returning a final result).

**Tailscale latency (LAN):**
- Under 10ms between machines on the same physical network. Tailscale's WireGuard tunnels add roughly 1-2ms of crypto overhead on LAN.
- From a mobile device on a home network (routing through the Tailscale relay or direct): typically under 20ms for the network hop.

**Orchestrator overhead:**
- The `Orchestrator` class calls `subprocess.run()` to dispatch prompts into sandboxes. The subprocess startup and `openshell sandbox connect` overhead is roughly 500ms per step. For a three-step pipeline, this adds about 1.5 seconds to the total pipeline time — negligible compared to model inference latency.

---

## Conclusion: Why Local AI Matters

After running NemoClaw across a working session, the practical benefits become clear.

**Privacy.** Proprietary code, internal architecture diagrams, and customer data never leave the hardware. This is not a policy — it is enforced at the network layer by the OpenShell gateway. An agent physically cannot reach an unapproved endpoint.

**Control.** You decide what each agent can access: which filesystem paths, which network endpoints, which tools. The YAML policy files are readable and auditable. There is no black box.

**Cost.** After the hardware investment, inference is free. Nemotron 120B at tens of thousands of tokens per day would be expensive on a cloud API. On hardware you own, it costs electricity.

**Flexibility.** Adding a new model is `ollama pull <model>` and registering it as a provider. Adding a new agent is one `openshell sandbox create` command. Switching between models takes five seconds without restarting anything.

**Transparency note:** This article was written with AI assistance, primarily Claude and Gemini, running inside the NemoClaw system described above. The code samples come directly from the production deployment. The orchestrator output examples are representative of actual timing from the DGX Spark hardware.

The [NemoClaw repository](https://github.com/macayaven/nemoclaw) contains the orchestrator source, sandbox configurations, and the full deployment runbook. If you are building something similar, the runbook's "gotchas" sections will save you several hours of debugging.

---

*Carlos Macaya — March 2026*

*Part 1: [Deploying NemoClaw: 2 Machines, 4 Sandboxes, 1 Local 120B Model](./part1-deployment.md)*
