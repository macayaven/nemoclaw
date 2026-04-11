#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

SANDBOX_NAME="${1:-opencode-dev}"
MODEL_ID="${OPENCODE_DEFAULT_MODEL:-zai/glm-5.1}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CFG_FILE="$(mktemp)"
trap 'rm -f "$CFG_FILE"' EXIT

command -v openshell >/dev/null 2>&1 || {
  printf 'error: openshell is required\n' >&2
  exit 1
}

if ! openshell sandbox get "$SANDBOX_NAME" >/dev/null 2>&1; then
  openshell sandbox create --keep --name "$SANDBOX_NAME" --auto-providers -- bash
fi

openshell sandbox ssh-config "$SANDBOX_NAME" >"$CFG_FILE"

ssh -F "$CFG_FILE" "openshell-${SANDBOX_NAME}" "mkdir -p /sandbox/.npm-global /sandbox/.local/bin /sandbox/.local/share/opencode"
ssh -F "$CFG_FILE" "openshell-${SANDBOX_NAME}" \
  "npm config set prefix /sandbox/.npm-global >/dev/null 2>&1 || true"
ssh -F "$CFG_FILE" "openshell-${SANDBOX_NAME}" \
  "PATH=/sandbox/.npm-global/bin:\$PATH npm install -g opencode-ai"

ssh -F "$CFG_FILE" "openshell-${SANDBOX_NAME}" "cat > /sandbox/.local/bin/opencode <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
REAL_BIN=\"\$HOME/.npm-global/bin/opencode\"
DEFAULT_MODEL=\"${MODEL_ID}\"
has_model=0
prev=
for arg in \"\$@\"; do
  if [ \"\$prev\" = \"--model\" ] || [ \"\$prev\" = \"-m\" ]; then
    has_model=1
    break
  fi
  case \"\$arg\" in
    -m|--model)
      has_model=1
      break
      ;;
  esac
  prev=\"\$arg\"
done
if [ \"\$has_model\" -eq 0 ]; then
  exec \"\$REAL_BIN\" -m \"\$DEFAULT_MODEL\" \"\$@\"
fi
exec \"\$REAL_BIN\" \"\$@\"
EOF
chmod 755 /sandbox/.local/bin/opencode"

ssh -F "$CFG_FILE" "openshell-${SANDBOX_NAME}" \
  "grep -qxF 'export PATH=\"\$HOME/.local/bin:\$HOME/.npm-global/bin:\$PATH\"' /sandbox/.bashrc || printf '\nexport PATH=\"\$HOME/.local/bin:\$HOME/.npm-global/bin:\$PATH\"\n' >> /sandbox/.bashrc"

if [ -x "${ROOT_DIR}/scripts/agent-pass-vault.sh" ]; then
  "${ROOT_DIR}/scripts/agent-pass-vault.sh" materialize opencode "$SANDBOX_NAME" || true
fi

printf 'Prepared %s with opencode default model %s\n' "$SANDBOX_NAME" "$MODEL_ID"
