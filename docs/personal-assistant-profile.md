<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Personal Assistant Profile

This document defines the default OpenClaw posture for a useful personal assistant on NemoClaw.

The design goal is straightforward:

- WhatsApp is the primary ingress channel
- the web UI remains available for operations and debugging
- tools stay narrow enough to be safe by default
- higher-risk capabilities are opt-in, not baseline

## Channel Profile

Use this order of importance:

1. WhatsApp as the main inbound channel
2. Web UI at `https://spark-caeb.tail48bab7.ts.net/` for operator control
3. Telegram as an optional secondary channel

Why WhatsApp first:

- it is the channel you will actually use most often from the phone
- OpenClaw ships a native WhatsApp channel plugin
- it keeps session handling, pairing, memory, and delivery inside the OpenClaw gateway instead of splitting responsibility across a custom bridge

The NemoClaw Makefile now reflects this flow with:

- `make policy-whatsapp`
- `make whatsapp-setup`
- `make whatsapp-login`
- `make channels-status`
- `make devices-list`
- `make approve-latest-device`
- `make gateway-token`
- `make telegram-setup` for the optional secondary Telegram bot

### Operator quick start

For the browser UI:

1. open `https://spark-caeb.tail48bab7.ts.net/`
2. click **Connect**
3. on the Spark host run:
   - `make devices-list`
   - `make approve-latest-device`

Use `make gateway-token` only as a fallback. The primary path is Tailscale Serve plus one-time device pairing.

## Recommended Tool Baseline

Start from a constrained personal-assistant profile, not an unrestricted coding-agent profile.

The recommended OpenClaw-native tool config is:

```json
{
  "tools": {
    "profile": "messaging",
    "allow": [
      "group:web",
      "group:memory",
      "group:ui",
      "group:automation",
      "group:nodes",
      "image",
      "tts"
    ],
    "deny": [
      "group:runtime",
      "group:fs",
      "image_generate",
      "music_generate",
      "video_generate"
    ]
  }
}
```

That lines up with the official OpenClaw tool model: start from `tools.profile = "messaging"`, add only the extra groups you actually want on the assistant, and explicitly deny runtime execution and broad mutation tools.

This repo's bootstrap now writes that baseline directly into the `native` OpenClaw profile together with:

- `gateway.auth.allowTailscale = true`
- `agents.defaults.timeoutSeconds = 1800`
- `agents.defaults.heartbeat.every = "0m"` during testing
- `channels.whatsapp.configWrites = false`

Tailscale Serve stays on the Spark host, not inside the `nemoclaw-main` sandbox. That matches the actual deployment boundary: OpenClaw runs in OpenShell, while Tailscale terminates HTTPS on the host and proxies to an OpenShell-managed host-local port forward.

The timeout setting matters for WhatsApp. The official OpenClaw personal-assistant guidance uses a longer per-turn timeout because cold model loads and tool usage can exceed shorter defaults during real chat use.

### Keep enabled by default

- `message`
  For channel delivery and follow-up replies.
- `web_search` / `web_fetch`
  For current facts and external lookup.
- `browser`
  For sites that need interaction instead of plain fetch.
- `read`
  For reading workspace context and mounted reference files.
- `memory`
  For retrieval from OpenClaw memory and, later, the second-brain service.
- `cron`
  For scheduled reminders and periodic checks.
- `nodes`
  For Mac/iOS companion-device actions when paired.
- `tts`
  For future speech output once Kokoro is deployed on the Mac.
- `directory`
  For resolving chat peers, groups, and node identities safely.
- `gateway`
  For bounded health checks and scheduler inspection.

### Keep optional, enable only when needed

- `image`
  Useful if you want WhatsApp image understanding.
- `code_execution`
  Useful for structured data analysis, but not required for baseline personal-assistant use.
- `write` / `edit`
  Useful for controlled note capture or automation, but should stay bounded.
- `webhooks`
  Useful for downstream automation and notifications once you have a concrete need.

### Do not enable by default

- unrestricted `exec`
- unrestricted file write/edit across the whole workspace
- coding-heavy mutation tools for the main personal assistant

Those are appropriate for specialist sandboxes such as `codex-dev`, not for the WhatsApp-facing assistant.

## Recommended Skills

Skills should teach the agent when and how to use the approved tools. They should not expand the tool surface by themselves.

### Core assistant skills

- messaging etiquette and channel-aware response formatting
- web research workflow
- calendar and reminder workflow
- memory retrieval workflow
- node/device usage guidance
- escalation guidance for unsafe or high-impact actions
- channel-aware confirmation rules for outbound actions
- speech-friendly response formatting for future TTS outputs

## Ready-Now Skills for Testing

The current `nemoclaw-main` sandbox already has these skills ready without extra dependency work:

- `weather`
  Good first WhatsApp test for short factual queries.
- `github`
  Useful for read-oriented GitHub lookups and summaries.
- `healthcheck`
  Useful for self-diagnostics and deployment posture checks.
- `gh-issues`
  Useful when you explicitly want issue-triage workflows.

The following skills are intentionally **not** part of the main personal-assistant baseline even though they are present:

- `coding-agent`
  Keep this for explicit coding/delegation tasks, not routine WhatsApp use.
- `skill-creator`
  Useful for maintenance, not normal operator chat.

For daily testing, the safe capability mix is:

- WhatsApp chat
- Control UI over Tailscale Serve
- live web research
- short browser-assisted lookups
- lightweight GitHub summaries
- health checks
- reminders and scheduled tasks

### NemoClaw-specific context skills

Mount these read-only when you want the assistant to understand the local system:

- NemoClaw reference
- NemoClaw monitor/troubleshooting
- NemoClaw security posture

These keep the assistant grounded in the actual deployment instead of inventing commands or capabilities.

## Role Separation

Keep these roles distinct:

- `nemoclaw-main`
  Personal assistant exposed via WhatsApp and the web UI.
- `codex-dev`
  Code-focused sandbox with stronger edit/execution tools.
- `claude-dev`, `gemini-dev`, `opencode-dev`
  Specialist sandboxes for delegated work.

Do not turn the main WhatsApp-facing assistant into a full-power coding environment. That is the wrong security boundary.

## Response Policy

For the main assistant:

- prefer concise, phone-friendly replies
- prefer links, summaries, and decisions over verbose dumps
- use speech output only when explicitly requested or when a wearable/voice channel requires it
- avoid autonomous destructive actions
- require confirmation for sending messages, modifying files, or invoking high-impact external tools

## Retrieval and Future Extensions

This profile should stay compatible with the future second-brain service:

- retrieval is read-only by default
- the assistant can cite and summarize retrieved context
- write-back to the knowledge base remains a separate, reviewed workflow

This keeps the WhatsApp agent useful now without coupling it to unfinished knowledge-write automation.
