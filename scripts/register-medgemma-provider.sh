#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

PROVIDER_NAME="${PROVIDER_NAME:-medgemma-mac}"
MAC_HOST="${MAC_HOST:-mac-studio.local}"
MAC_PORT="${MAC_PORT:-11435}"
MODEL_ID="${MODEL_ID:-hf.co/google/gemma-3-27b-it-qat-q4_0-gguf}"

usage() {
  cat <<EOF
Usage: register-medgemma-provider.sh [provider-name] [mac-host] [mac-port]

Defaults:
  provider-name: ${PROVIDER_NAME}
  mac-host:      ${MAC_HOST}
  mac-port:      ${MAC_PORT}
  model-id:      ${MODEL_ID}

Notes:
  - Default port 11435 matches the Mac TCP forwarder topology documented in the
    runbook, where Ollama.app stays bound to localhost:11434 and a separate
    forwarder exposes 11435 on the tailnet/LAN.
  - This script registers the provider. It does not pull the model on the Mac.
EOF
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

if [ "${1:-}" != "" ]; then
  PROVIDER_NAME="$1"
fi
if [ "${2:-}" != "" ]; then
  MAC_HOST="$2"
fi
if [ "${3:-}" != "" ]; then
  MAC_PORT="$3"
fi

command -v openshell >/dev/null 2>&1 || {
  printf 'error: openshell is required\n' >&2
  exit 1
}

command -v curl >/dev/null 2>&1 || {
  printf 'error: curl is required\n' >&2
  exit 1
}

BASE_URL="http://${MAC_HOST}:${MAC_PORT}/v1"
TAGS_URL="http://${MAC_HOST}:${MAC_PORT}/api/tags"

curl -sf --max-time 10 "$TAGS_URL" >/dev/null

if openshell provider get "$PROVIDER_NAME" >/dev/null 2>&1; then
  openshell provider update \
    "$PROVIDER_NAME" \
    --credential OPENAI_API_KEY=not-needed \
    --config OPENAI_BASE_URL="$BASE_URL"
else
  openshell provider create \
    --name "$PROVIDER_NAME" \
    --type openai \
    --credential OPENAI_API_KEY=not-needed \
    --config OPENAI_BASE_URL="$BASE_URL"
fi

printf 'Registered provider %s -> %s\n' "$PROVIDER_NAME" "$BASE_URL"
printf 'Model available for manual route switch:\n'
printf '  openshell inference set --provider %s --model %s\n' "$PROVIDER_NAME" "$MODEL_ID"
printf '\n'
printf 'If you run the host-side router proxy, healthcare text can be auto-routed\n'
printf 'to this provider without changing the global default inference route.\n'
