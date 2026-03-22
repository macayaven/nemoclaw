# Inter-Agent Communication, Cooperation & Orchestration

How to make your NemoClaw agents work together instead of in isolation.

---

## The Problem

By default, each NemoClaw sandbox is **fully isolated**:
- Claude Code can't see what Codex is doing
- OpenClaw can't delegate tasks to Gemini CLI
- No agent knows what the others have produced

This is a feature (security), but it limits what you can accomplish. This guide covers patterns for making agents cooperate while preserving the security boundaries.

---

## Architecture: Three Patterns

```
Pattern 1: SHARED MCP SERVERS (easiest, recommended start)
  Agents share tools/data through MCP servers running outside sandboxes

Pattern 2: ORCHESTRATOR AGENT (most powerful)
  One agent acts as the manager and delegates tasks to others

Pattern 3: SHARED FILESYSTEM (simplest for file-based workflows)
  Agents read/write to a shared volume mounted in multiple sandboxes
```

---

## Pattern 1: Shared MCP Servers

### Concept

MCP (Model Context Protocol) servers run **outside** the sandboxes and expose tools/resources that any agent can call. The MCP server is the communication bridge — agents don't talk to each other directly, they talk to shared MCP servers.

```
┌──────────────────────────────────────────────────────────┐
│                    DGX Spark                              │
│                                                           │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐     │
│  │  OpenClaw    │  │ Claude Code │  │    Codex     │     │
│  │  (sandbox)   │  │  (sandbox)  │  │   (sandbox)  │     │
│  │      │       │  │      │      │  │      │       │     │
│  │      │ MCP   │  │      │ MCP  │  │      │ MCP   │     │
│  └──────┼───────┘  └──────┼──────┘  └──────┼───────┘     │
│         │                 │                │              │
│         ▼                 ▼                ▼              │
│  ┌─────────────────────────────────────────────────────┐  │
│  │              SHARED MCP SERVERS                     │  │
│  │                                                     │  │
│  │  ┌─────────────┐  ┌──────────┐  ┌───────────────┐  │  │
│  │  │  Filesystem  │  │  GitHub  │  │  Task Queue   │  │  │
│  │  │  MCP Server  │  │  MCP     │  │  MCP Server   │  │  │
│  │  │  (shared     │  │  Server  │  │  (Redis/file  │  │  │
│  │  │   workspace) │  │          │  │   -based)     │  │  │
│  │  └─────────────┘  └──────────┘  └───────────────┘  │  │
│  └─────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

### Implementation

**Step 1: Create a shared workspace directory on the Spark**

```bash
mkdir -p ~/workspace/shared-agents
```

**Step 2: Start a filesystem MCP server**

```bash
# On the Spark (outside sandboxes)
npx -y @modelcontextprotocol/server-filesystem ~/workspace/shared-agents
```

**Step 3: Configure each agent to use the MCP server**

For **Claude Code** (inside sandbox):
```json
// ~/.claude/settings.json
{
  "mcpServers": {
    "shared-workspace": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/shared"]
    }
  }
}
```

For **Codex** (inside sandbox):
```toml
# ~/.codex/config.toml
[mcp_servers.shared-workspace]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "/shared"]
enabled = true
```

For **Gemini CLI** (inside sandbox):
```json
// ~/.gemini/settings.json
{
  "mcpServers": {
    "shared-workspace": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/shared"]
    }
  }
}
```

**Step 4: Mount the shared directory into sandboxes**

```bash
# When creating sandboxes, upload the shared workspace
openshell sandbox create --keep --name claude-dev --upload ~/workspace/shared-agents:/shared -- claude
openshell sandbox create --keep --name codex-dev --upload ~/workspace/shared-agents:/shared -- codex
```

### Use Cases for Shared MCP

| Use Case | How |
|----------|-----|
| Claude writes spec, Codex implements | Claude writes `spec.md` to `/shared/`, Codex reads it and generates code |
| Codex generates code, Gemini reviews | Codex writes code to `/shared/src/`, Gemini reads and provides feedback in `/shared/reviews/` |
| Shared task board | All agents read/write a `/shared/tasks.json` file with task assignments |
| Shared knowledge base | All agents read from `/shared/knowledge/` for project context |

---

## Pattern 2: Orchestrator Agent

### Concept

One agent (the **orchestrator**) acts as the manager and delegates tasks to specialist agents. The orchestrator keeps control and combines outputs. This is the most powerful pattern for complex multi-step workflows.

Two sub-patterns:

**A) Manager Pattern (agents as tools)**
The orchestrator calls specialists as tools and retains control of the conversation.

**B) Handoff Pattern**
The orchestrator hands conversation control to a specialist, which takes over until done.

### Optional example: OpenAI Agents SDK

The current repository implementation uses the checked-in `orchestrator/`
package and `python -m orchestrator` CLI. The example below is an alternative
pattern, not the runtime that is currently shipped in this repo.

```python
# orchestrator.py — runs on the Spark, outside sandboxes
# Uses OpenAI Agents SDK for orchestration logic

from agents import Agent, Runner

# Define specialist agents that call into sandboxes
code_agent = Agent(
    name="Code Specialist",
    instructions=(
        "You are a code generation expert. When given a task, generate "
        "high-quality Python code. Use the sandbox_exec tool to run code "
        "in the Codex sandbox for validation."
    ),
)

review_agent = Agent(
    name="Code Reviewer",
    instructions=(
        "You are a senior code reviewer. Review the code for bugs, "
        "security issues, and best practices. Use the sandbox_exec tool "
        "to run the code in the Claude sandbox for analysis."
    ),
)

research_agent = Agent(
    name="Research Specialist",
    instructions=(
        "You are a research expert. When given a topic, search the web "
        "and summarize findings. Use the Gemini sandbox for deep research."
    ),
)

# The orchestrator delegates to specialists
orchestrator = Agent(
    name="Project Manager",
    instructions=(
        "You manage a development team. Break down user requests into tasks, "
        "delegate to the right specialist, and combine their outputs into "
        "a coherent result."
    ),
    tools=[
        code_agent.as_tool(
            tool_name="write_code",
            tool_description="Generate or modify code. Use for implementation tasks.",
        ),
        review_agent.as_tool(
            tool_name="review_code",
            tool_description="Review code for quality and security. Use after code generation.",
        ),
        research_agent.as_tool(
            tool_name="research",
            tool_description="Research a topic. Use when you need external information.",
        ),
    ],
)

async def main():
    result = await Runner.run(
        orchestrator,
        "Build a FastAPI endpoint that fetches weather data, "
        "review the code, and research the best weather API to use."
    )
    print(result.final_output)
```

### Implementation with OpenShell Sandboxes

The orchestrator runs **outside** sandboxes and sends commands **into** them:

```python
# sandbox_tools.py — bridge between orchestrator and sandboxes

import subprocess

def run_in_sandbox(sandbox_name: str, command: str) -> str:
    """Execute a command inside an OpenShell sandbox and return output."""
    result = subprocess.run(
        ["openshell", "sandbox", "connect", sandbox_name, "--", "bash", "-c", command],
        capture_output=True,
        text=True,
        timeout=120,
    )
    return result.stdout

def claude_analyze(prompt: str) -> str:
    """Ask Claude Code to analyze something inside its sandbox."""
    return run_in_sandbox(
        "claude-dev",
        f'openclaw agent --agent main --local -m "{prompt}" --session-id orchestrator'
    )

def codex_generate(prompt: str) -> str:
    """Ask Codex to generate code inside its sandbox."""
    return run_in_sandbox(
        "codex-dev",
        f'codex exec "{prompt}"'
    )

def gemini_research(prompt: str) -> str:
    """Ask Gemini CLI to research something inside its sandbox."""
    return run_in_sandbox(
        "gemini-dev",
        f'gemini -p "{prompt}"'
    )
```

### Orchestration Flow

```
User: "Build a REST API for managing tasks"
  │
  ▼
Orchestrator (outside sandboxes):
  │
  ├─1─▶ research_agent (Gemini sandbox):
  │       "What are the best practices for task management APIs?"
  │       ◀── Returns: research summary
  │
  ├─2─▶ code_agent (Codex sandbox):
  │       "Based on this research, generate a FastAPI task management API"
  │       ◀── Returns: generated code
  │
  ├─3─▶ review_agent (Claude sandbox):
  │       "Review this code for security and quality"
  │       ◀── Returns: review comments
  │
  ├─4─▶ code_agent (Codex sandbox):
  │       "Fix these review comments: [...]"
  │       ◀── Returns: fixed code
  │
  └─5─▶ User: "Here's your API with review-approved code"
```

---

## Pattern 3: Shared Filesystem

### Concept

The simplest cooperation pattern — mount the same directory into multiple sandboxes. Agents communicate through files.

```
~/workspace/shared-agents/
├── inbox/
│   ├── claude/          # Tasks for Claude to pick up
│   ├── codex/           # Tasks for Codex to pick up
│   └── gemini/          # Tasks for Gemini to pick up
├── outbox/
│   ├── claude/          # Claude's completed work
│   ├── codex/           # Codex's completed work
│   └── gemini/          # Gemini's completed work
├── context/             # Shared project context (all agents read)
│   ├── architecture.md
│   ├── requirements.md
│   └── codebase-summary.md
└── state.json           # Current task state (who's doing what)
```

### Implementation

**Convention-based file protocol:**

```json
// shared-agents/inbox/codex/task-001.json
{
  "id": "task-001",
  "from": "claude-dev",
  "to": "codex-dev",
  "type": "code-generation",
  "priority": "high",
  "prompt": "Implement the UserService class based on the spec in /context/requirements.md",
  "context_files": ["/context/requirements.md", "/context/architecture.md"],
  "created_at": "2026-03-21T17:00:00Z",
  "status": "pending"
}

// After Codex processes it:
// shared-agents/outbox/codex/task-001.json
{
  "id": "task-001",
  "from": "codex-dev",
  "to": "claude-dev",
  "type": "code-generation-result",
  "files_created": ["/outbox/codex/task-001/user_service.py"],
  "summary": "Created UserService with CRUD operations and input validation",
  "completed_at": "2026-03-21T17:02:00Z",
  "status": "completed"
}
```

---

## Pattern Comparison

| Aspect | Shared MCP | Orchestrator Agent | Shared Filesystem |
|--------|-----------|-------------------|-------------------|
| **Complexity** | Medium | High | Low |
| **Real-time** | Yes (tool calls) | Yes (agent loops) | No (polling) |
| **Security** | MCP controls access | Orchestrator controls scope | File permissions |
| **Best for** | Shared tools + data | Complex multi-step workflows | Simple file handoffs |
| **Requires** | MCP server setup | OpenAI Agents SDK or custom | Shared volume mount |
| **Agent awareness** | Agents don't know about each other | Orchestrator knows all agents | Convention-based |

### Recommendation

**Start with Pattern 1 (Shared MCP)** — it's the most natural fit for OpenShell sandboxes and requires the least custom code. Add Pattern 2 (Orchestrator) when you need complex multi-step workflows that require coordination.

---

## Practical Examples

### Example 1: Code Review Pipeline

```
1. You write code on the Mac Studio
2. Push to GitHub
3. OpenClaw detects the push (via GitHub MCP)
4. OpenClaw delegates to Claude Code: "Review this PR"
5. Claude Code reviews, writes comments to shared workspace
6. OpenClaw delegates to Codex: "Fix the issues Claude found"
7. Codex fixes, writes updated code to shared workspace
8. OpenClaw creates a new commit with the fixes
```

### Example 2: Research + Implementation

```
1. You ask OpenClaw: "Add real-time notifications to the app"
2. OpenClaw delegates to Gemini: "Research notification architectures"
3. Gemini researches, writes findings to shared workspace
4. OpenClaw delegates to Codex: "Implement based on Gemini's research"
5. Codex implements, writes code to shared workspace
6. OpenClaw delegates to Claude: "Review the implementation"
7. Claude reviews, writes feedback
8. OpenClaw summarizes everything back to you
```

### Example 3: Parallel Specialists

```
1. You ask: "Optimize this function for both speed and readability"
2. Orchestrator sends to Codex: "Optimize for performance"
3. Orchestrator sends to Claude: "Optimize for readability"
4. Both work in parallel in their sandboxes
5. Orchestrator compares outputs and picks the best approach
6. Orchestrator sends to Gemini: "Benchmark both approaches"
7. Final recommendation based on data
```

---

## Implementation Roadmap

Shared MCP remains an optional enhancement. The lightweight orchestrator itself
is already implemented in the current repository as the `orchestrator/`
package. The recommended path:

### Phase A: Shared MCP (after Phase 4 deployment)

1. Set up a filesystem MCP server on the Spark
2. Mount shared workspace into all sandboxes
3. Configure each agent's MCP settings
4. Test: Claude writes a file, Codex reads it

### Phase B: Orchestrator hardening (current repo path)

1. Use the checked-in `orchestrator/` package on the Spark
2. Extend bridge tools and task routing where needed
3. Validate `python -m orchestrator` commands and Phase 6 tests
4. Test: orchestrator delegates a code task across agents

### Phase C: Advanced Patterns

1. Add task queue MCP server (Redis-backed)
2. Implement async task delegation
3. Add agent status monitoring
4. Build a dashboard showing inter-agent activity

---

## Security Considerations

Inter-agent communication introduces new attack surfaces:

| Risk | Mitigation |
|------|------------|
| Agent A injects malicious prompts for Agent B | Orchestrator validates all inter-agent messages |
| Shared filesystem allows data exfiltration | Mount shared dirs read-only where possible |
| MCP server exposes sensitive tools | Restrict MCP tool access per sandbox via OpenShell policy |
| Orchestrator has excessive permissions | Run orchestrator with its own scoped policy |
| Circular delegation (infinite loops) | Set max delegation depth in orchestrator config |

The key principle: **the sandbox boundaries remain.** Agents still can't escape their sandboxes. The cooperation happens through controlled channels (MCP, shared filesystem, orchestrator bridge) — not by breaking isolation.
