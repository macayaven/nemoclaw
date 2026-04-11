#!/bin/sh
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -eu

PROFILE_NAME="${1:-native}"
LAST_RUN_AT="$(date -u +%Y-%m-%dT%H:%M:%S.000Z)"

oc() {
  openclaw --profile "${PROFILE_NAME}" "$@"
}

mkdir -p "${HOME}/.openclaw-${PROFILE_NAME}/agents/main/sessions" \
  "${HOME}/.openclaw-${PROFILE_NAME}/workspace"

oc config set wizard.lastRunAt "${LAST_RUN_AT}"
oc config set wizard.lastRunVersion "2026.3.11"
oc config set wizard.lastRunCommand "onboard"
oc config set wizard.lastRunMode "local"

oc config set commands.native "auto"
oc config set commands.nativeSkills "auto"
oc config set commands.restart true --strict-json
oc config set commands.ownerDisplay "raw"
oc config set agents.defaults.timeoutSeconds 1800 --strict-json
oc config set agents.defaults.heartbeat.every "0m"

oc config set plugins.entries.whatsapp.enabled true --strict-json
oc config set plugins.entries.telegram.enabled true --strict-json

oc config set models \
  '{"mode":"merge","providers":{"nemotron-local":{"baseUrl":"https://inference.local/v1","apiKey":"ollama","api":"openai-completions","models":[{"id":"nemotron-3-super:120b","name":"nemotron-3-super:120b","reasoning":false,"input":["text"],"cost":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0},"contextWindow":131072,"maxTokens":8192}]}}}' \
  --strict-json
oc config set agents.defaults.model.primary "nemotron-local/nemotron-3-super:120b"

oc config set gateway.mode "local"
oc config set gateway.port 18789 --strict-json
oc config set gateway.bind "loopback"
oc config set gateway.auth.mode "token"
oc config set gateway.auth.allowTailscale true --strict-json
oc config set gateway.controlUi \
  '{"allowedOrigins":["https://spark-caeb.tail48bab7.ts.net","http://127.0.0.1:18789","http://127.0.0.1:39989","http://localhost:39989"]}' \
  --strict-json
oc config set gateway.trustedProxies '["127.0.0.1","::1"]' --strict-json

oc config set tools \
  '{"profile":"messaging","allow":["group:web","group:memory","group:ui","group:automation","group:nodes","image","tts"],"deny":["group:runtime","group:fs","image_generate","music_generate","video_generate"]}' \
  --strict-json

oc config set channels.whatsapp.dmPolicy "pairing"
oc config set channels.whatsapp.groupPolicy "open"
oc config set channels.whatsapp.debounceMs 0 --strict-json
oc config set channels.whatsapp.mediaMaxMb 50 --strict-json
oc config set channels.whatsapp.configWrites false --strict-json
oc config set channels.whatsapp.accounts.default.enabled true --strict-json
oc config set channels.whatsapp.accounts.default.dmPolicy "pairing"
oc config set channels.whatsapp.accounts.default.groupPolicy "open"
oc config set channels.whatsapp.accounts.default.debounceMs 0 --strict-json

chown -R sandbox:sandbox "${HOME}/.openclaw-${PROFILE_NAME}" 2>/dev/null || true
