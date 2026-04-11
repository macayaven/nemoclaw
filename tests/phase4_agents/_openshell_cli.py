"""Shared helpers for Phase 4 OpenShell CLI assertions."""

from __future__ import annotations

import re
import shlex

from ..helpers import run_remote
from ..models import CommandResult

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def strip_ansi(text: str) -> str:
    """Remove ANSI color/control sequences from CLI output."""
    return _ANSI_ESCAPE_RE.sub("", text)


def run_sandbox_command(
    spark_ssh, sandbox_name: str, command: str, timeout: int = 30
) -> CommandResult:
    """Run a command inside a sandbox via its supported SSH config.

    OpenShell now exposes sandbox access through ``openshell sandbox ssh-config``
    rather than direct Docker container names.  This helper materializes the
    config on the Spark host, runs the requested command over SSH, and cleans
    up the temporary config file afterward.
    """
    sandbox_alias = f"openshell-{sandbox_name}"
    script = "\n".join(
        [
            "set -euo pipefail",
            'config="$(mktemp)"',
            "trap 'rm -f \"$config\"' EXIT",
            f'openshell sandbox ssh-config {shlex.quote(sandbox_name)} > "$config"',
            f"printf '%s\\n' {shlex.quote(command)} | ssh -F \"$config\" {sandbox_alias} sh",
        ]
    )
    return run_remote(spark_ssh, f"bash -lc {shlex.quote(script)}", timeout=timeout)
