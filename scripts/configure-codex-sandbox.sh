#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

SANDBOX_NAME="${1:-codex-dev}"
CFG_FILE="$(mktemp)"
CONFIG_TMP="$(mktemp)"
trap 'rm -f "$CFG_FILE" "$CONFIG_TMP"' EXIT

command -v openshell >/dev/null 2>&1 || {
  printf 'error: openshell is required\n' >&2
  exit 1
}

cat >"$CONFIG_TMP" <<'EOF'
model = "nemotron-3-super:120b"
model_provider = "spark-ollama"
approval_policy = "on-request"
sandbox_mode = "external-sandbox"

[model_providers.spark-ollama]
name = "Spark Ollama via OpenShell"
base_url = "https://inference.local/v1"
env_key = "OPENAI_API_KEY"
wire_api = "responses"

[features]
memories = true
EOF

openshell sandbox ssh-config "$SANDBOX_NAME" >"$CFG_FILE"
ssh -F "$CFG_FILE" "openshell-${SANDBOX_NAME}" \
  "mkdir -p /sandbox/.codex && chmod 700 /sandbox/.codex"
scp -q -F "$CFG_FILE" "$CONFIG_TMP" "openshell-${SANDBOX_NAME}:/sandbox/.codex/config.toml"
ssh -F "$CFG_FILE" "openshell-${SANDBOX_NAME}" "chmod 600 /sandbox/.codex/config.toml"
ssh -F "$CFG_FILE" "openshell-${SANDBOX_NAME}" \
  "grep -qxF 'export OPENAI_API_KEY=\"ollama-local\"' /sandbox/.bashrc || printf '\nexport OPENAI_API_KEY=\"ollama-local\"\n' >> /sandbox/.bashrc"

printf 'Configured %s with local Codex provider via inference.local\n' "$SANDBOX_NAME"
