#!/bin/sh
set -eu

LOG_PATH="${OPENCLAW_GATEWAY_LOG:-/tmp/gateway.log}"
RESTART_DELAY="${OPENCLAW_GATEWAY_RESTART_DELAY:-5}"

touch "$LOG_PATH"

while :; do
    printf '%s starting openclaw gateway\n' "$(date -Is)" >>"$LOG_PATH"

    if [ -n "${OPENCLAW_PROFILE:-}" ]; then
        if openclaw --profile "${OPENCLAW_PROFILE}" gateway run "$@" >>"$LOG_PATH" 2>&1; then
            rc=0
        else
            rc=$?
        fi
    elif openclaw gateway run "$@" >>"$LOG_PATH" 2>&1; then
        rc=0
    else
        rc=$?
    fi

    printf '%s openclaw gateway exited rc=%s; restarting in %ss\n' \
        "$(date -Is)" "$rc" "$RESTART_DELAY" >>"$LOG_PATH"
    sleep "$RESTART_DELAY"
done
