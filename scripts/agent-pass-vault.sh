#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

PASS_PREFIX="${PASS_PREFIX:-nemoclaw/agents}"

usage() {
  cat <<'EOF'
Usage:
  agent-pass-vault.sh status [agent...]
  agent-pass-vault.sh capture <agent> [agent...]
  agent-pass-vault.sh capture-all
  agent-pass-vault.sh materialize <agent> [sandbox]
  agent-pass-vault.sh materialize-all

Agents:
  claude
  codex
  gemini
  opencode

Notes:
  - This script wraps the existing local CLI auth files with `pass`; it does
    not implement a custom vault.
  - `capture` stores the current local auth blobs into pass.
  - `materialize` writes the stored blob(s) into the target sandbox with
    owner-only permissions.
  - Sandbox defaults are `<agent>-dev`, except Codex/Gemini/Claude which match
    the same naming convention.
EOF
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

default_sandbox() {
  case "$1" in
    claude) printf 'claude-dev\n' ;;
    codex) printf 'codex-dev\n' ;;
    gemini) printf 'gemini-dev\n' ;;
    opencode) printf 'opencode-dev\n' ;;
    *) die "unknown agent: $1" ;;
  esac
}

agent_records() {
  case "$1" in
    claude)
      cat <<EOF
$HOME/.claude/.credentials.json|/sandbox/.claude/.credentials.json|${PASS_PREFIX}/claude/credentials.json
EOF
      ;;
    codex)
      cat <<EOF
$HOME/.codex/auth.json|/sandbox/.codex/auth.json|${PASS_PREFIX}/codex/auth.json
EOF
      ;;
    gemini)
      cat <<EOF
$HOME/.gemini/gemini-credentials.json|/sandbox/.gemini/gemini-credentials.json|${PASS_PREFIX}/gemini/gemini-credentials.json
$HOME/.gemini/google_accounts.json|/sandbox/.gemini/google_accounts.json|${PASS_PREFIX}/gemini/google_accounts.json
$HOME/.gemini/oauth_creds.json|/sandbox/.gemini/oauth_creds.json|${PASS_PREFIX}/gemini/oauth_creds.json
EOF
      ;;
    opencode)
      cat <<EOF
$HOME/.local/share/opencode/auth.json|/sandbox/.local/share/opencode/auth.json|${PASS_PREFIX}/opencode/auth.json
EOF
      ;;
    *)
      die "unknown agent: $1"
      ;;
  esac
}

with_ssh_config() {
  local sandbox="$1"
  local cfg
  cfg="$(mktemp)"
  openshell sandbox ssh-config "$sandbox" >"$cfg"
  printf '%s\n' "$cfg"
}

capture_agent() {
  local agent="$1"
  local record source _dest pass_entry

  while IFS= read -r record; do
    [ -n "$record" ] || continue
    IFS='|' read -r source _dest pass_entry <<<"$record"
    if [ ! -f "$source" ]; then
      printf 'skip  %s missing local file %s\n' "$agent" "$source" >&2
      continue
    fi
    pass insert -m -f "$pass_entry" <"$source"
    printf 'saved %s -> %s\n' "$source" "$pass_entry"
  done < <(agent_records "$agent")
}

materialize_agent() {
  local agent="$1"
  local sandbox="${2:-$(default_sandbox "$agent")}"
  local cfg
  cfg="$(with_ssh_config "$sandbox")"
  trap 'rm -f "$cfg"' RETURN

  local record _source dest pass_entry tmp
  while IFS= read -r record; do
    [ -n "$record" ] || continue
    IFS='|' read -r _source dest pass_entry <<<"$record"

    if ! pass show "$pass_entry" >/dev/null 2>&1; then
      printf 'skip  %s missing pass entry %s\n' "$agent" "$pass_entry" >&2
      continue
    fi

    tmp="$(mktemp)"
    chmod 600 "$tmp"
    pass show "$pass_entry" >"$tmp"

    ssh -F "$cfg" "openshell-${sandbox}" \
      "mkdir -p '$(dirname "$dest")' && chmod 700 '$(dirname "$dest")'"
    scp -q -F "$cfg" "$tmp" "openshell-${sandbox}:$dest"
    ssh -F "$cfg" "openshell-${sandbox}" "chmod 600 '$dest'"

    rm -f "$tmp"
    printf 'wrote %s -> %s:%s\n' "$pass_entry" "$sandbox" "$dest"
  done < <(agent_records "$agent")
}

status_agent() {
  local agent="$1"
  local record source dest pass_entry
  while IFS= read -r record; do
    [ -n "$record" ] || continue
    IFS='|' read -r source dest pass_entry <<<"$record"
    printf '%s\n' "agent=$agent"
    printf '  local: %s [%s]\n' "$source" "$( [ -f "$source" ] && printf present || printf missing )"
    printf '  pass : %s [%s]\n' "$pass_entry" "$( pass show "$pass_entry" >/dev/null 2>&1 && printf present || printf missing )"
    printf '  dest : %s\n' "$dest"
  done < <(agent_records "$agent")
}

main() {
  require_cmd pass

  local cmd="${1:-}"
  shift || true

  case "$cmd" in
    status)
      if [ "$#" -eq 0 ]; then
        set -- claude codex gemini opencode
      fi
      for agent in "$@"; do
        status_agent "$agent"
      done
      ;;
    capture)
      [ "$#" -gt 0 ] || die "capture requires at least one agent"
      for agent in "$@"; do
        capture_agent "$agent"
      done
      ;;
    capture-all)
      for agent in claude codex gemini opencode; do
        capture_agent "$agent"
      done
      ;;
    materialize)
      [ "$#" -ge 1 ] || die "materialize requires an agent"
      materialize_agent "$1" "${2:-}"
      ;;
    materialize-all)
      for agent in claude codex gemini opencode; do
        materialize_agent "$agent"
      done
      ;;
    -h|--help|help|'')
      usage
      ;;
    *)
      die "unknown command: $cmd"
      ;;
  esac
}

main "$@"
