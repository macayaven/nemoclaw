#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

usage() {
    cat <<'EOF'
Usage: scripts/run-codex-overnight.sh [options]

Launch an unattended Codex overnight session for this repository inside tmux.

Options:
  --model MODEL          Codex model to use (default: gpt-5.4)
  --session NAME         tmux session name (default: codex-night)
  --repo DIR             Repository root (default: current git toplevel)
  --prompt-file PATH     Prompt file to write and use
                         (default: /tmp/nemoclaw-autonomous.prompt)
  --json-log PATH        JSONL event log path
                         (default: /tmp/nemoclaw-autonomous.jsonl)
  --last-message PATH    Last agent message output path
                         (default: /tmp/nemoclaw-autonomous-last.txt)
  --unsafe               Disable approvals and sandbox entirely
  --no-search            Disable live web search
  -h, --help             Show this help text
EOF
}

MODEL="gpt-5.4"
SESSION_NAME="codex-night"
REPO_ROOT=""
PROMPT_FILE="/tmp/nemoclaw-autonomous.prompt"
JSON_LOG="/tmp/nemoclaw-autonomous.jsonl"
LAST_MESSAGE="/tmp/nemoclaw-autonomous-last.txt"
ENABLE_SEARCH=1
UNSAFE=0

while [ "$#" -gt 0 ]; do
    case "$1" in
        --model)
            MODEL="${2:?missing value for --model}"
            shift 2
            ;;
        --session)
            SESSION_NAME="${2:?missing value for --session}"
            shift 2
            ;;
        --repo)
            REPO_ROOT="${2:?missing value for --repo}"
            shift 2
            ;;
        --prompt-file)
            PROMPT_FILE="${2:?missing value for --prompt-file}"
            shift 2
            ;;
        --json-log)
            JSON_LOG="${2:?missing value for --json-log}"
            shift 2
            ;;
        --last-message)
            LAST_MESSAGE="${2:?missing value for --last-message}"
            shift 2
            ;;
        --unsafe)
            UNSAFE=1
            shift
            ;;
        --no-search)
            ENABLE_SEARCH=0
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

if ! command -v codex >/dev/null 2>&1; then
    echo "codex CLI not found in PATH" >&2
    exit 1
fi

if ! command -v tmux >/dev/null 2>&1; then
    echo "tmux not found in PATH" >&2
    exit 1
fi

if [ -z "$REPO_ROOT" ]; then
    REPO_ROOT="$(git rev-parse --show-toplevel)"
fi

if [ ! -d "$REPO_ROOT" ]; then
    echo "Repository path does not exist: $REPO_ROOT" >&2
    exit 1
fi

mkdir -p "$(dirname "$PROMPT_FILE")" "$(dirname "$JSON_LOG")" "$(dirname "$LAST_MESSAGE")"

cat >"$PROMPT_FILE" <<EOF
You are working autonomously overnight in the NemoClaw repository.

Repository:
- Root: $REPO_ROOT
- Project: NVIDIA NemoClaw
- Follow the repo instructions in AGENTS.md and CONTRIBUTING.md
- Respect existing user changes; do not revert unrelated edits
- Do not use destructive git commands like git reset --hard or git checkout --
- Prefer rg for search
- Keep edits minimal, correct, and production-quality

Primary goal:
Continuously improve the repository overnight by selecting and completing a sequence of high-value, low-risk tasks end-to-end. Do not stop after the first fix if more safe, meaningful work remains.

Task selection priority:
1. Fix real failing tests, lint errors, typecheck failures, or obvious bugs
2. Fix small correctness, reliability, security, UX, CLI, or documentation issues that are well-bounded
3. Add missing tests for behavior that is currently underprotected
4. Tighten docs, scripts, or validation where there is clear local evidence of a problem
5. If no safe changes are available, leave a precise findings report with ranked next steps

Constraints:
- Stay within this repository unless clearly necessary
- Prefer safe autonomous operation over aggressive changes
- Do not invent requirements
- Do not make broad speculative refactors
- Do not rewrite large areas unless forced by a concrete defect
- Update docs if user-facing behavior changes
- Add or adjust tests when code changes warrant it
- Preserve SPDX headers and existing style conventions
- Follow Conventional Commit thinking for summaries, but do not create commits unless explicitly needed
- You may use web search if needed for unstable or current facts, but prefer local repo context first

Working loop:
1. Inspect git status and understand any existing local changes
2. Read enough surrounding code and tests before editing
3. Run targeted validation to discover the highest-value task:
   - npm test
   - make check
   - npm run typecheck:cli
   - cd nemoclaw && npm test
   Use judgment: run the cheapest informative checks first, then go deeper as needed
4. Pick one bounded task with a strong value/risk ratio
5. Implement the fix completely
6. Run focused verification for the changed area, plus broader cheap checks when useful
7. Reassess the repository and continue with the next best task if:
   - there is no blocker
   - the next task is still well-bounded
   - confidence is high that the change is safe
8. Repeat until you hit a real blocker, diminishing returns, or the session ends

Task budgeting:
- Prefer multiple small verified improvements over one large risky change
- Avoid getting stuck for a long time on one speculative issue
- If a task starts expanding, either narrow it or stop and document it
- When in doubt, choose the smaller fix with clear validation

Deliverable requirements:
- Make the actual code/doc/test changes directly in the repo
- Maintain a running awareness of what changed and why
- At the end, provide:
  - completed tasks in execution order
  - why each change mattered
  - verification run for each change or batch of changes
  - blockers, skipped items, and recommended next steps

Stop conditions:
- Stop only when one of these is true:
  - no more safe, meaningful, well-bounded tasks remain
  - a real blocker prevents further progress
  - further changes would require product decisions or human clarification
  - validation becomes too expensive relative to the likely value
- Do not stop merely because one issue was fixed

Quality bar:
- Favor small verified wins over ambitious incomplete work
- If you touch security-sensitive paths, be extra conservative and add tests where practical
- If the repo is already healthy, spend the time on the best small improvement you can justify from local evidence
- If nothing safe should be changed, produce a high-signal findings summary instead of forcing a patch
EOF

if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    echo "tmux session already exists: $SESSION_NAME" >&2
    echo "Attach with: tmux attach -t $SESSION_NAME" >&2
    exit 1
fi

CODEX_ARGS=()
if [ "$ENABLE_SEARCH" -eq 1 ]; then
    CODEX_ARGS+=(--search)
fi

if [ "$UNSAFE" -eq 1 ]; then
    CODEX_ARGS+=(exec -m "$MODEL" --dangerously-bypass-approvals-and-sandbox)
else
    CODEX_ARGS+=(-a never -s workspace-write exec -m "$MODEL")
fi

CODEX_ARGS+=(
    -C "$REPO_ROOT"
    -o "$LAST_MESSAGE"
    --json
    -
)

printf -v CODEX_CMD 'cd %q && codex' "$REPO_ROOT"
for arg in "${CODEX_ARGS[@]}"; do
    printf -v CODEX_CMD '%s %q' "$CODEX_CMD" "$arg"
done
printf -v CODEX_CMD '%s < %q | tee %q' "$CODEX_CMD" "$PROMPT_FILE" "$JSON_LOG"

tmux new-session -d -s "$SESSION_NAME" \
    "$CODEX_CMD"

printf 'Started tmux session: %s\n' "$SESSION_NAME"
printf 'Prompt file: %s\n' "$PROMPT_FILE"
printf 'JSON log: %s\n' "$JSON_LOG"
printf 'Last message: %s\n' "$LAST_MESSAGE"
printf '\n'
printf 'Attach: tmux attach -t %s\n' "$SESSION_NAME"
printf 'Watch log: tail -f %s\n' "$JSON_LOG"
printf 'Read final summary: cat %s\n' "$LAST_MESSAGE"
