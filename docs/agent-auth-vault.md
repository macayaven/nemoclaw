# Agent Auth Vault

This document describes the recommended local secret-management pattern for
subscription-authenticated coding agents in NemoClaw.

## Recommendation

Use [`pass`](https://www.passwordstore.org/) on the Spark host as the local
vault for agent auth blobs.

Reasons:

- It is mature and auditable.
- It avoids introducing a cloud dependency into the control plane.
- It fits the current NemoClaw deployment model, where the Spark host is the
  place that creates sandboxes and materializes agent-specific auth files.

Do not implement a custom secret store for these agents.

## What Gets Stored

The current coding-agent CLIs persist subscription auth in local files rather
than in OpenShell providers:

| Agent | Host-side source file(s) | Sandbox destination |
|---|---|---|
| Claude Code | `~/.claude/.credentials.json` | `/sandbox/.claude/.credentials.json` |
| Codex | `~/.codex/auth.json` | `/sandbox/.codex/auth.json` |
| Gemini CLI | `~/.gemini/gemini-credentials.json`, `~/.gemini/google_accounts.json`, `~/.gemini/oauth_creds.json` | Same paths under `/sandbox/.gemini/` |
| OpenCode | `~/.local/share/opencode/auth.json` | `/sandbox/.local/share/opencode/auth.json` |

Codex also needs a non-secret local-provider config file. That is handled by
`scripts/configure-codex-sandbox.sh`, not by the vault.

## Initialize Pass

Install `pass` and initialize it with your GPG key on the Spark:

```bash
pass init "<your-gpg-key-id>"
```

This document assumes the default pass store under `~/.password-store/`.

## Capture Existing Auth Into Pass

Once you have authenticated each CLI locally at least once, save those auth
files into the pass store:

```bash
./scripts/agent-pass-vault.sh capture claude codex gemini opencode
```

To inspect what the script expects before capturing:

```bash
./scripts/agent-pass-vault.sh status
```

By default the script writes under:

```text
pass insert -m nemoclaw/agents/<agent>/...
```

Override the prefix with `PASS_PREFIX` if needed.

## Materialize Auth Into Sandboxes

After creating a sandbox, materialize the stored auth files into it:

```bash
./scripts/agent-pass-vault.sh materialize claude claude-dev
./scripts/agent-pass-vault.sh materialize codex codex-dev
./scripts/agent-pass-vault.sh materialize gemini gemini-dev
./scripts/agent-pass-vault.sh materialize opencode opencode-dev
```

The script uses `openshell sandbox ssh-config`, copies the file over the
supported OpenShell SSH transport, and applies owner-only permissions
(`chmod 600` on files, `chmod 700` on the destination directory).

## Codex Local Inference Configuration

Codex should not override its reserved built-in `ollama` provider id.

Instead, configure a custom OpenAI-compatible provider pointed at
`https://inference.local/v1`:

```bash
./scripts/configure-codex-sandbox.sh codex-dev
```

This writes `~/.codex/config.toml` inside the sandbox and appends:

```bash
export OPENAI_API_KEY="ollama-local"
```

to the sandbox's `~/.bashrc`.

`ollama-local` is not a real secret. It is a compatibility marker so Codex can
talk to the gateway-managed local route.

## OpenCode Sandbox

OpenCode is supported as an optional coding-agent sandbox using the Z.AI Coding
Plan subscription path.

Bootstrap the sandbox with:

```bash
./scripts/setup-opencode-sandbox.sh opencode-dev
```

What this script does:

- creates `opencode-dev` if it does not exist
- installs `opencode-ai` into `~/.npm-global`
- creates a wrapper at `~/.local/bin/opencode`
- defaults that wrapper to `zai/glm-5.1` unless `--model`/`-m` is provided
- attempts to materialize the OpenCode auth blob from `pass`

The wrapper preserves explicit overrides. For example:

```bash
opencode run "say hi"                  # defaults to zai/glm-5.1
opencode run -m openrouter/qwen3 "hi"  # explicit override wins
```

## Security Notes

- `pass` protects the stored files at rest better than plain JSON under
  `~/.nemoclaw/`, but once a blob is materialized into a sandbox home, it
  becomes sensitive local state again.
- Do not mount the pass store into sandboxes.
- Only materialize the auth blobs into the specific sandboxes that need them.
- Prefer sandbox-local files with `0600` permissions over plaintext
  environment variables for these subscription flows.
- Keep OpenShell provider credentials separate. Provider-managed secrets still
  belong to the OpenShell/OpenClaw host-side path.

## Operational Pattern

Recommended workflow:

1. Authenticate each CLI locally once.
2. Capture the auth blobs into `pass`.
3. Create or recreate the sandbox.
4. Materialize the corresponding auth blob into the sandbox.
5. Re-run the agent-specific health checks.
