"""
helpers.py — Shared utility functions for the NemoClaw TDD test suite.

Provides SSH command execution, polling, JSON extraction, version parsing,
HTTP health checks, and Pydantic response validation.
"""

from __future__ import annotations

import json
import re
import shlex
import time
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING

import httpx
from packaging.version import Version
from pydantic import BaseModel, ValidationError
from tenacity import RetryError, retry, stop_after_delay, wait_fixed

if TYPE_CHECKING:
    from fabric import Connection

from .models import CommandResult, OpenShellInferenceRoute, OpenShellProvider

# ---------------------------------------------------------------------------
# Remote command execution
# ---------------------------------------------------------------------------


def run_remote(conn: Connection, cmd: str, timeout: int = 30) -> CommandResult:
    """Run a shell command on a remote host via Fabric and return a CommandResult.

    Captures stdout, stderr, return_code, and wall-clock duration in
    milliseconds.  When the remote command exceeds *timeout* seconds the
    underlying socket raises a socket.timeout and we re-raise it as
    TimeoutError with a clear message.

    Args:
        conn: An open :class:`fabric.Connection` to the target host.
        cmd: Shell command to execute remotely.
        timeout: Seconds to wait before giving up (default 30).

    Returns:
        A :class:`~tests.models.CommandResult` with all captured fields.

    Raises:
        TimeoutError: If the remote command does not complete within *timeout*
            seconds.
    """
    # Run commands inside a login-like bash shell so operator-installed tools
    # from profiles (for example nvm-managed node/npm) are visible over SSH in
    # the same way they are during normal interactive administration.
    shell_script = (
        "source ~/.bashrc >/dev/null 2>&1 || true; "
        "source ~/.profile >/dev/null 2>&1 || true; "
        f"{cmd}"
    )
    wrapped_cmd = f"bash -lc {shlex.quote(shell_script)}"

    start = time.monotonic()
    try:
        result = conn.run(
            wrapped_cmd,
            hide=True,  # suppress output to the console
            warn=True,  # do not raise on non-zero exit
            timeout=timeout,
            in_stream=False,  # avoid pytest capture / stdin forwarding issues
        )
    except Exception as exc:
        # Fabric wraps socket.timeout in various ways depending on the version;
        # normalise anything that looks like a timeout into TimeoutError.
        exc_str = str(exc).lower()
        if "timed out" in exc_str or "timeout" in exc_str:
            raise TimeoutError(
                f"Remote command timed out after {timeout}s on {conn.host!r}: {cmd!r}"
            ) from exc
        raise

    duration_ms = int((time.monotonic() - start) * 1000)

    return CommandResult(
        stdout=result.stdout or "",
        stderr=result.stderr or "",
        return_code=result.return_code,
        duration_ms=duration_ms,
    )


def run_in_sandbox(
    conn: Connection,
    sandbox_name: str,
    command: str,
    timeout: int = 60,
) -> CommandResult:
    """Run *command* inside an existing OpenShell sandbox over its SSH proxy.

    The current OpenShell CLI no longer exposes ``sandbox run`` / ``sandbox exec``.
    The supported non-interactive path is:
    1. Generate the sandbox SSH config via ``openshell sandbox ssh-config``
    2. Invoke ``ssh`` through the generated proxy

    Args:
        conn: Fabric connection to the gateway host.
        sandbox_name: OpenShell sandbox name, e.g. ``"nemoclaw-main"``.
        command: Shell command to execute inside the sandbox.
        timeout: Seconds to wait before failing the remote command.

    Returns:
        A :class:`CommandResult` describing the sandbox command execution.
    """
    remote_cmd = (
        "tmp_cfg=$(mktemp) && "
        f'openshell sandbox ssh-config {sandbox_name} > "$tmp_cfg" && '
        f"printf '%s\\n' {shlex.quote(command)} | "
        f'ssh -F "$tmp_cfg" -o BatchMode=yes openshell-{sandbox_name} sh; '
        'status=$?; rm -f "$tmp_cfg"; exit $status'
    )
    return run_remote(conn, remote_cmd, timeout=timeout)


# ---------------------------------------------------------------------------
# Polling / eventually-consistent infrastructure
# ---------------------------------------------------------------------------


def poll_until_ready(
    check_fn: Callable[[], bool],
    timeout: int = 120,
    interval: int = 5,
    description: str = "",
) -> None:
    """Poll *check_fn* every *interval* seconds until it returns True.

    Uses :mod:`tenacity` for the retry loop so that transient exceptions inside
    *check_fn* are automatically retried rather than aborting the wait.

    Args:
        check_fn: Zero-argument callable that returns ``True`` when the
            condition is satisfied and ``False`` (or raises) otherwise.
        timeout: Maximum number of seconds to wait before giving up (default
            120).
        interval: Seconds between consecutive calls to *check_fn* (default 5).
        description: Human-readable description of the condition being waited
            for, included in the TimeoutError message.

    Raises:
        TimeoutError: When *check_fn* has not returned ``True`` within
            *timeout* seconds.
    """
    label = description or repr(check_fn)

    @retry(
        stop=stop_after_delay(timeout),
        wait=wait_fixed(interval),
        reraise=False,
    )
    def _attempt() -> None:
        if not check_fn():
            raise RuntimeError(f"Condition not yet satisfied: {label}")

    try:
        _attempt()
    except RetryError as exc:
        raise TimeoutError(f"Condition not satisfied within {timeout}s: {label}") from exc
    except RuntimeError as exc:
        # tenacity re-raises the last RuntimeError when reraise=True; map it
        # to TimeoutError for a consistent interface.
        raise TimeoutError(f"Condition not satisfied within {timeout}s: {label}") from exc


# ---------------------------------------------------------------------------
# JSON extraction from mixed command output
# ---------------------------------------------------------------------------


def parse_json_output(output: str) -> dict | list:
    """Extract and parse the first JSON object or array embedded in *output*.

    Many CLI tools (e.g. ``ollama list --json``, ``openshell provider list
    --json``) prepend human-readable text before the JSON payload.  This
    function scans *output* for the first ``{`` or ``[`` character and attempts
    to parse from that position forward, expanding the window until it finds a
    valid JSON value.

    Args:
        output: Raw string output from a remote (or local) command.

    Returns:
        Parsed JSON as a ``dict`` or ``list``.

    Raises:
        ValueError: When no valid JSON can be found anywhere in *output*.
    """
    # Attempt to parse each position that starts with { or [
    for match in re.finditer(r"[{\[]", output):
        candidate = output[match.start() :]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # Fallback: try the whole string stripped of leading/trailing whitespace
    stripped = output.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    raise ValueError(f"No valid JSON found in output (first 200 chars): {output[:200]!r}")


_ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def strip_ansi(text: str) -> str:
    """Remove ANSI color/control sequences from CLI output."""
    return _ANSI_ESCAPE_RE.sub("", text)


def extract_tagged_int(output: str, tag: str) -> int | None:
    """Extract an integer emitted as ``TAG:<value>`` from command output."""
    match = re.search(rf"\b{re.escape(tag)}:(\d+)\b", output)
    if match is None:
        return None
    return int(match.group(1))


def curl_attempt_was_blocked(
    output: str,
    *,
    denied_http_statuses: set[int] | None = None,
) -> bool:
    """Return True when wrapped curl output indicates policy blocking.

    The current OpenShell proxy can deny egress by returning an HTTP status
    such as 403 while curl itself still exits 0. Tests should therefore treat
    explicit deny statuses as blocked, not as successful outbound access.
    """
    if denied_http_statuses is None:
        denied_http_statuses = {401, 403, 407}

    curl_exit = extract_tagged_int(output, "CURL_EXIT")
    http_status = extract_tagged_int(output, "HTTP_STATUS")

    if curl_exit is not None and curl_exit != 0:
        return True
    return http_status in denied_http_statuses


def parse_openshell_provider_output(output: str) -> OpenShellProvider:
    """Parse the current human-readable ``openshell provider get`` output."""
    clean = strip_ansi(output)
    fields: dict[str, str | None] = {"base_url": None}

    for raw_line in clean.splitlines():
        line = raw_line.strip()
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "name":
            fields["name"] = value
        elif key == "type":
            fields["type"] = value
        elif key in {"base url", "base_url"}:
            fields["base_url"] = value

    return OpenShellProvider.model_validate(fields)


def parse_openshell_inference_route_output(output: str) -> OpenShellInferenceRoute:
    """Parse the current human-readable ``openshell inference get`` output."""
    clean = strip_ansi(output)
    section: str | None = None
    fields: dict[str, str] = {}

    for raw_line in clean.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line == "Gateway inference:":
            section = "gateway"
            continue
        if line == "System inference:":
            section = "system"
            continue
        if section != "gateway" or ":" not in line:
            continue

        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "provider":
            fields["provider"] = value
        elif key == "model":
            fields["model"] = value

    return OpenShellInferenceRoute.model_validate(fields)


# ---------------------------------------------------------------------------
# Version string parsing
# ---------------------------------------------------------------------------

# Suffixes produced by OS packaging systems that confuse packaging.version
_VERSION_SUFFIX_RE = re.compile(
    r"[~+\-](?:ubuntu|debian|focal|jammy|noble|ce|ee|cu\d+|rc\d*|alpha|beta)"
    r".*$",
    re.IGNORECASE,
)
# Strip leading non-numeric junk (e.g. "v1.2.3", "Docker version 28.0.4,")
_VERSION_LEADING_RE = re.compile(r"^[^\d]*")


def parse_version(version_string: str) -> Version:
    """Robustly parse a version string into a :class:`packaging.version.Version`.

    Strips common OS packaging suffixes (``~ubuntu``, ``-ce``, ``+cu12``,
    etc.) and leading non-numeric characters (``v``, ``Docker version ``)
    before handing the string to :func:`packaging.version.parse`.

    Args:
        version_string: Raw version string as returned by a tool (e.g.
            ``"Docker version 28.0.4, build abcdef"``).

    Returns:
        A :class:`packaging.version.Version` suitable for comparison.

    Raises:
        ValueError: If the string cannot be reduced to a parseable version.

    Examples:
        >>> parse_version("Docker version 28.0.4, build abcdef")
        <Version('28.0.4')>
        >>> parse_version("1.21.1+k3s1")
        <Version('1.21.1')>
        >>> parse_version("v0.6.0~ubuntu20.04.1")
        <Version('0.6.0')>
    """
    # Take only the first token (handles "Docker version 28.0.4, build …")
    first_token = version_string.strip().split()[0] if version_string.strip() else ""

    # Strip leading "v" or other non-numeric prefix
    cleaned = _VERSION_LEADING_RE.sub("", first_token)

    # Strip OS-specific suffixes
    cleaned = _VERSION_SUFFIX_RE.sub("", cleaned)

    # Remove any trailing commas or dots
    cleaned = cleaned.rstrip(",.")

    if not cleaned:
        raise ValueError(f"Could not extract a version number from: {version_string!r}")

    return Version(cleaned)


# ---------------------------------------------------------------------------
# HTTP health assertions
# ---------------------------------------------------------------------------


def assert_http_healthy(
    url: str,
    timeout: int = 10,
    expected_status: set[int] | None = None,
) -> httpx.Response:
    """GET *url* and assert it returns one of the *expected_status* codes.

    Args:
        url: Full URL to request (e.g. ``"http://10.0.0.1:11434/api/tags"``).
        timeout: Request timeout in seconds (default 10).
        expected_status: Set of acceptable HTTP status codes.  Defaults to
            ``{200}`` when not specified.

    Returns:
        The :class:`httpx.Response` object for further assertions by the
        caller.

    Raises:
        AssertionError: When the response status is not in *expected_status*.
        httpx.ConnectError: When the host is unreachable.
        httpx.TimeoutException: When the request times out.
    """
    if expected_status is None:
        expected_status = {200}

    response = httpx.get(url, timeout=timeout, follow_redirects=True)
    assert response.status_code in expected_status, (
        f"GET {url} returned HTTP {response.status_code}, "
        f"expected one of {sorted(expected_status)}. "
        f"Body (first 300 chars): {response.text[:300]!r}"
    )
    return response


# ---------------------------------------------------------------------------
# Pydantic response validation
# ---------------------------------------------------------------------------


def assert_json_schema(
    response: httpx.Response,
    model_class: type[BaseModel],
) -> BaseModel:
    """Parse an httpx JSON response into *model_class* and assert it is valid.

    Args:
        response: An :class:`httpx.Response` whose body is expected to be JSON.
        model_class: A Pydantic :class:`~pydantic.BaseModel` subclass to
            validate against.

    Returns:
        A validated instance of *model_class*.

    Raises:
        AssertionError: On JSON decode failure or Pydantic validation error,
            with the raw body and validation errors included in the message.
    """
    try:
        raw = response.json()
    except Exception as exc:
        raise AssertionError(
            f"Response body is not valid JSON. "
            f"Status={response.status_code}, "
            f"body={response.text[:300]!r}"
        ) from exc

    try:
        return model_class.model_validate(raw)
    except ValidationError as exc:
        raise AssertionError(
            f"Response JSON does not match {model_class.__name__}.\n"
            f"Validation errors:\n{exc}\n"
            f"Raw JSON: {raw!r}"
        ) from exc


# ---------------------------------------------------------------------------
# Unique identifiers for test isolation
# ---------------------------------------------------------------------------


def generate_unique_id() -> str:
    """Return a short, URL-safe unique identifier for temporary resource names.

    Uses the first 8 hex characters of a random UUID4, which gives ~4 billion
    unique values — sufficient for test isolation without cluttering names.

    Returns:
        An 8-character lowercase hex string, e.g. ``"a3f9c120"``.
    """
    return uuid.uuid4().hex[:8]
