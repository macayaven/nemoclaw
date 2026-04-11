# NemoClaw Agent Skills for Coding Assistants

*Source: [NVIDIA NemoClaw Agent Skills Documentation](https://docs.nvidia.com/nemoclaw/latest/resources/agent-skills.html)*

---

## What Are Agent Skills?

NemoClaw ships **agent skills** — structured documentation files that AI coding assistants can consume as context. Each skill is a converted version of one or more NemoClaw doc pages, formatted so that coding assistants (Claude Code, Codex, Gemini CLI) can answer NemoClaw-specific questions with project-accurate guidance instead of relying on general training data.

Skills are organized as directories under `.agents/skills/`, each containing a `SKILL.md` file with YAML frontmatter (name, description, trigger keywords) and optional `references/` subdirectories with supporting material.

---

## Installation

Skills were installed into this project via sparse checkout from the official NVIDIA NemoClaw repository:

```bash
git clone --filter=blob:none --no-checkout https://github.com/NVIDIA/NemoClaw.git
cd NemoClaw
git sparse-checkout set --no-cone \
  '/.agents/skills/nemoclaw-user-*/**' \
  '/.agents/skills/nemoclaw-skills-guide/**' \
  '/.claude/**' \
  '/AGENTS.md' \
  '/CLAUDE.md'
git checkout
```

The skills are now installed at:

```
nemoclaw/
├── .agents/skills/           # All skill directories live here
│   ├── nemoclaw-skills-guide/
│   ├── nemoclaw-user-overview/
│   ├── nemoclaw-user-get-started/
│   ├── nemoclaw-user-configure-inference/
│   ├── nemoclaw-user-manage-policy/
│   ├── nemoclaw-user-monitor-sandbox/
│   ├── nemoclaw-user-deploy-remote/
│   ├── nemoclaw-user-configure-security/
│   ├── nemoclaw-user-workspace/
│   ├── nemoclaw-user-reference/
│   └── nemoclaw-user-skills-coding/
├── .claude/skills -> ../.agents/skills   # Symlink for Claude Code discovery
└── AGENTS.md                             # Agent instructions (Cursor/generic discovery)
```

### Updating Skills

The sparse checkout filter is saved. To update skills after a new NemoClaw release:

```bash
cd /path/to/NemoClaw-sparse-clone
git pull
```

Then copy the updated `.agents/skills/` directory into this project.

---

## Available Skills (10 User Skills + 1 Guide)

| Skill | Summary | When to Use |
|-------|---------|-------------|
| `nemoclaw-skills-guide` | Index of all skills with a decision guide mapping tasks to skills. | Start here to find the right skill for your task. |
| `nemoclaw-user-overview` | What NemoClaw is, ecosystem placement (OpenClaw + OpenShell + NemoClaw), internals, and release notes. | Understanding the stack, architecture questions. |
| `nemoclaw-user-get-started` | Install NemoClaw, launch a sandbox, run the first agent prompt. | Initial setup, first-time users. |
| `nemoclaw-user-configure-inference` | Choose inference providers during onboarding, switch models, set up local inference servers (Ollama, vLLM, TensorRT-LLM, NIM). | Changing models, adding providers, inference routing. |
| `nemoclaw-user-manage-policy` | Approve/deny blocked egress requests in the TUI, customize sandbox network policy. | Network policy issues, allowing/blocking endpoints. |
| `nemoclaw-user-monitor-sandbox` | Check sandbox health, read logs, trace agent behavior. | Debugging, diagnostics, health checks. |
| `nemoclaw-user-deploy-remote` | Deploy to a remote GPU instance, set up Telegram bridge, sandbox hardening. | Remote deployment, Telegram integration. |
| `nemoclaw-user-configure-security` | Security control risk framework, credential storage, posture trade-offs. | Security review, credential management. |
| `nemoclaw-user-workspace` | Back up/restore OpenClaw workspace files, file persistence across sandbox restarts. | Backup, restore, understanding what persists. |
| `nemoclaw-user-reference` | CLI command reference, architecture, baseline network policies, troubleshooting. | Command lookup, troubleshooting errors. |
| `nemoclaw-user-skills-coding` | How to obtain and use agent skills with coding assistants. | Meta-skill: understanding the skill system itself. |

### Example Questions and Triggered Skills

| Question you ask | Skill triggered |
|------------------|-----------------|
| "How do I install NemoClaw?" | `nemoclaw-user-get-started` |
| "Switch my inference provider to Ollama" | `nemoclaw-user-configure-inference` |
| "Why is my sandbox blocking requests to GitHub?" | `nemoclaw-user-manage-policy` |
| "How do I back up my OpenClaw memory?" | `nemoclaw-user-workspace` |
| "What does the `nemoclaw onboard` command do?" | `nemoclaw-user-reference` |
| "What security controls can I configure?" | `nemoclaw-user-configure-security` |
| "My sandbox won't start, help me debug" | `nemoclaw-user-monitor-sandbox` |

---

## Configuring Skills per Coding Assistant

Each coding assistant has a different mechanism for discovering and loading skills. Below is how to configure each of the three agents in the NemoClaw deployment.

### Claude Code

**Discovery method:** Claude Code follows the `.claude/skills` symlink automatically.

**Status:** Ready to use. The symlink `.claude/skills -> ../.agents/skills` is already configured in this project. When Claude Code opens this project directory, it discovers all skills automatically.

**How it works:**
1. Claude Code looks for `.claude/skills/` in the project root
2. The symlink resolves to `.agents/skills/`
3. Each `SKILL.md` file is indexed by its frontmatter `description` field
4. When you ask a question, Claude Code matches your query against skill descriptions and loads the relevant skill as context

**Usage inside the sandbox:**
```bash
# Connect to the Claude Code sandbox
openshell sandbox connect claude-dev

# Claude Code discovers skills automatically if the project is mounted
# Just ask NemoClaw questions naturally:
# "How do I switch inference providers?"
# "What network policies are applied by default?"
```

**To make skills available globally** (outside this project):
```bash
# Copy skills to Claude Code's global config
cp -r .agents/skills/* ~/.claude/skills/
```

### Codex CLI

**Discovery method:** Codex does not have native skill/agent file discovery. Skills must be provided as context via MCP servers or manual file references.

**Option A — MCP filesystem server (recommended):**

Configure an MCP server in Codex's config that exposes the skills directory:

```toml
# ~/.codex/config.toml (inside sandbox)
[mcp_servers.nemoclaw-skills]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/nemoclaw/.agents/skills"]
enabled = true
```

With this configuration, Codex can read any skill file via the MCP filesystem tools when it needs NemoClaw-specific guidance.

**Option B — Direct file reference:**

When asking Codex a NemoClaw question, reference the skill file explicitly:

```bash
codex "Read .agents/skills/nemoclaw-user-configure-inference/SKILL.md and then help me switch my inference provider to Ollama"
```

**Option C — AGENTS.md context:**

Codex reads `AGENTS.md` at the project root if present. The `AGENTS.md` file in this project contains the full skill catalog and project architecture. This provides baseline context but doesn't load individual skill content automatically.

### Gemini CLI

**Discovery method:** Gemini CLI reads `.gemini/settings.json` for MCP server configuration. Like Codex, it does not natively discover `.agents/skills/` directories.

**Option A — MCP filesystem server (recommended):**

```json
// ~/.gemini/settings.json (inside sandbox)
{
  "mcpServers": {
    "nemoclaw-skills": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/nemoclaw/.agents/skills"]
    }
  }
}
```

**Option B — Direct file reference:**

```bash
gemini -p "Read the file .agents/skills/nemoclaw-user-manage-policy/SKILL.md and explain how to allow GitHub access in my sandbox"
```

**Option C — AGENTS.md context:**

Gemini CLI can be pointed to `AGENTS.md` for project context. If the project directory is open, Gemini may discover it automatically depending on its configuration.

---

## Skill File Format

Each skill is a directory containing:

```
nemoclaw-user-<name>/
├── SKILL.md              # Main skill file with YAML frontmatter
└── references/           # Optional supporting material
    ├── topic-a.md
    └── topic-b.md
```

The `SKILL.md` frontmatter structure:

```yaml
---
name: "nemoclaw-user-<name>"
description: "One-line description used for skill matching and discovery.
              Include trigger keywords at the end."
---
```

The body is standard Markdown containing the guidance, instructions, tables, and code examples that the coding assistant uses to answer questions.

---

## Comparison: How Each Assistant Uses Skills

| Capability | Claude Code | Codex CLI | Gemini CLI |
|------------|-------------|-----------|------------|
| **Auto-discovery** | Yes (`.claude/skills` symlink) | No | No |
| **AGENTS.md reading** | Yes | Yes (project root) | Partial |
| **MCP filesystem** | Yes | Yes (`config.toml`) | Yes (`settings.json`) |
| **Skill matching** | Automatic (by description) | Manual or via MCP | Manual or via MCP |
| **Best integration** | Native — zero config needed | MCP server + AGENTS.md | MCP server |
| **Inside sandbox** | Mount project dir or copy to `~/.claude/skills/` | Configure MCP in `config.toml` | Configure MCP in `settings.json` |
