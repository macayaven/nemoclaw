"""
Pydantic models for validating command outputs in the NemoClaw test suite.

Every model that carries a version string provides a ``parsed_version``
property that returns a ``packaging.version.Version`` object, allowing
callers to write assertions like::

    assert prereqs.ollama_version_parsed >= Version("0.3.0")

Models are defined in strict mode where the shape of the data is well-known
(e.g. API responses), and in lax mode where we parse raw shell output that
may include extra whitespace or minor formatting variations.
"""

from __future__ import annotations

import re
import time
from typing import Any, Optional

from packaging.version import InvalidVersion, Version
from pydantic import (
    BaseModel,
    Field,
    field_validator,
    model_validator,
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_VERSION_RE = re.compile(r"(\d+\.\d+(?:\.\d+)*)")


def _extract_version(raw: str) -> str:
    """Pull the first semver-ish fragment from a raw shell string.

    Examples::

        "ollama version 0.3.12"  -> "0.3.12"
        "Docker version 26.1.3"  -> "26.1.3"
        "v20.11.0"               -> "20.11.0"
        "node v20.11.0"          -> "20.11.0"
    """
    raw = raw.strip().lstrip("v")
    m = _VERSION_RE.search(raw)
    if m:
        return m.group(1)
    return raw  # return as-is; Version() will raise InvalidVersion if invalid


def _parse_version(raw: str) -> Version:
    candidate = _extract_version(raw)
    try:
        return Version(candidate)
    except InvalidVersion as exc:
        raise ValueError(f"Cannot parse version from {raw!r}: {exc}") from exc


# ---------------------------------------------------------------------------
# Core primitives
# ---------------------------------------------------------------------------


class CommandResult(BaseModel):
    """Captures the outcome of a remote shell command."""

    stdout: str = Field(description="Standard output of the command, stripped.")
    stderr: str = Field(default="", description="Standard error output, stripped.")
    return_code: int = Field(description="Exit status returned by the command.")
    duration_ms: float = Field(
        description="Wall-clock time the command took to complete, in milliseconds."
    )

    @field_validator("stdout", "stderr", mode="before")
    @classmethod
    def _strip_whitespace(cls, v: Any) -> str:
        if v is None:
            return ""
        return str(v).strip()

    @property
    def succeeded(self) -> bool:
        """True when return_code is 0."""
        return self.return_code == 0

    @property
    def failed(self) -> bool:
        return not self.succeeded

    @classmethod
    def timed(cls, stdout: str, stderr: str, return_code: int, start_ns: int) -> "CommandResult":
        """Construct a CommandResult using a start timestamp from time.perf_counter_ns."""
        elapsed_ms = (time.perf_counter_ns() - start_ns) / 1_000_000
        return cls(
            stdout=stdout,
            stderr=stderr,
            return_code=return_code,
            duration_ms=elapsed_ms,
        )


# ---------------------------------------------------------------------------
# Pre-flight prerequisite models
# ---------------------------------------------------------------------------


class SparkPrereqs(BaseModel):
    """Pre-flight checks for the DGX Spark node (phase 0)."""

    # Container runtime
    docker_version: str = Field(description="Raw Docker version string (e.g. '26.1.3').")
    docker_running: bool = Field(description="True when the Docker daemon is active.")

    # Local inference
    ollama_version: str = Field(description="Raw Ollama version string (e.g. '0.3.12').")
    models_available: list[str] = Field(
        description="Names of models currently pulled in Ollama."
    )

    # Storage
    disk_free_gb: float = Field(
        description="Free disk space in GiB on the primary partition."
    )
    disk_inodes_free: int = Field(
        description="Free inodes on the primary partition."
    )

    # Node.js / npm (required by NemoClaw)
    node_version: str = Field(description="Raw Node.js version string (e.g. 'v20.11.0').")
    npm_version: str = Field(description="Raw npm version string (e.g. '10.2.4').")

    # Kernel security features required for sandbox isolation
    landlock_supported: bool = Field(
        description="True when the kernel exposes Landlock LSM."
    )
    seccomp_supported: bool = Field(
        description="True when seccomp filtering is available."
    )
    cgroup_v2: bool = Field(
        description="True when cgroup v2 (unified hierarchy) is active."
    )

    # Networking
    tailscale_connected: bool = Field(
        description="True when Tailscale reports the node as connected."
    )
    tailscale_ip: Optional[str] = Field(
        default=None,
        description="Tailscale-assigned IPv4 address, if connected.",
    )

    # ------------------------------------------------------------------
    # Version validators
    # ------------------------------------------------------------------

    @field_validator("docker_version", "ollama_version", "node_version", "npm_version", mode="before")
    @classmethod
    def _validate_version_string(cls, v: Any) -> str:
        raw = str(v).strip()
        _parse_version(raw)  # raises ValueError on unparseable input
        return raw

    # ------------------------------------------------------------------
    # Parsed version properties
    # ------------------------------------------------------------------

    @property
    def docker_version_parsed(self) -> Version:
        return _parse_version(self.docker_version)

    @property
    def ollama_version_parsed(self) -> Version:
        return _parse_version(self.ollama_version)

    @property
    def node_version_parsed(self) -> Version:
        return _parse_version(self.node_version)

    @property
    def npm_version_parsed(self) -> Version:
        return _parse_version(self.npm_version)


class MacPrereqs(BaseModel):
    """Pre-flight checks for the Mac Studio node (phase 0 / phase 2)."""

    ollama_version: str = Field(description="Raw Ollama version string.")
    ollama_listening: bool = Field(
        description="True when Ollama's HTTP API is accepting connections on port 11434."
    )
    models_available: list[str] = Field(
        description="Names of models currently pulled in Ollama on the Mac."
    )

    # Networking / Tailscale
    tailscale_connected: bool = Field(
        description="True when Tailscale reports the Mac as connected."
    )
    tailscale_ip: Optional[str] = Field(
        default=None,
        description="Tailscale-assigned IPv4 address, if connected.",
    )

    # macOS service management
    launchd_ollama_active: bool = Field(
        description="True when the Ollama launchd plist is loaded and running."
    )

    @field_validator("ollama_version", mode="before")
    @classmethod
    def _validate_ollama_version(cls, v: Any) -> str:
        raw = str(v).strip()
        _parse_version(raw)
        return raw

    @property
    def ollama_version_parsed(self) -> Version:
        return _parse_version(self.ollama_version)


class PiPrereqs(BaseModel):
    """Pre-flight checks for the Raspberry Pi infrastructure node (phase 0 / phase 3)."""

    free_ram_mb: int = Field(
        description="Free + buffers/cache RAM available on the Pi in MiB."
    )
    python3_version: str = Field(
        description="Raw Python 3 version string (e.g. '3.11.9')."
    )

    # Networking / Tailscale
    tailscale_connected: bool = Field(
        description="True when Tailscale reports the Pi as connected."
    )
    tailscale_ip: Optional[str] = Field(
        default=None,
        description="Tailscale-assigned IPv4 address, if connected.",
    )

    @field_validator("python3_version", mode="before")
    @classmethod
    def _validate_python_version(cls, v: Any) -> str:
        raw = str(v).strip()
        _parse_version(raw)
        return raw

    @property
    def python3_version_parsed(self) -> Version:
        return _parse_version(self.python3_version)


# ---------------------------------------------------------------------------
# Ollama API response models
# ---------------------------------------------------------------------------


class OllamaModelInfo(BaseModel):
    """A single model entry from the Ollama /api/tags response."""

    name: str = Field(description="Full model name including tag, e.g. 'llama3:8b'.")
    size_gb: float = Field(
        description="Model size on disk in gibibytes."
    )
    family: str = Field(
        default="unknown",
        description="Model architecture family (e.g. 'llama', 'mistral').",
    )
    parameter_size: str = Field(
        default="",
        description="Human-readable parameter count string (e.g. '8B', '70B').",
    )
    quantization_level: str = Field(
        default="",
        description="Quantization descriptor (e.g. 'Q4_K_M', 'F16').",
    )

    @field_validator("size_gb", mode="before")
    @classmethod
    def _coerce_size(cls, v: Any) -> float:
        """Accept size in bytes (int) and convert, or a pre-converted float."""
        if isinstance(v, (int, float)) and v > 1_000_000:
            # Likely raw bytes from the Ollama API
            return round(v / (1024**3), 3)
        return float(v)

    @field_validator("name", mode="before")
    @classmethod
    def _require_nonempty_name(cls, v: Any) -> str:
        s = str(v).strip()
        if not s:
            raise ValueError("Model name must not be empty.")
        return s

    @property
    def short_name(self) -> str:
        """Return the name without the tag, e.g. 'llama3:8b' -> 'llama3'."""
        return self.name.split(":")[0]

    @property
    def tag(self) -> str:
        """Return the tag portion, e.g. 'llama3:8b' -> '8b', defaulting to 'latest'."""
        parts = self.name.split(":", 1)
        return parts[1] if len(parts) == 2 else "latest"


class OllamaTagsResponse(BaseModel):
    """Top-level response from GET /api/tags on an Ollama server."""

    models: list[OllamaModelInfo] = Field(
        default_factory=list,
        description="List of models available on this Ollama instance.",
    )

    @property
    def model_names(self) -> list[str]:
        """Return the list of full model name strings."""
        return [m.name for m in self.models]

    def find(self, name: str) -> Optional[OllamaModelInfo]:
        """Return the first model whose name contains ``name``, or None."""
        for m in self.models:
            if name in m.name:
                return m
        return None


# ---------------------------------------------------------------------------
# NemoClaw / OpenShell configuration models
# ---------------------------------------------------------------------------


class OpenShellProvider(BaseModel):
    """A configured provider entry in the NemoClaw OpenShell configuration."""

    name: str = Field(description="Logical name for this provider (e.g. 'spark-ollama').")
    type: str = Field(
        description="Provider type: 'ollama', 'openai', 'anthropic', 'litellm', etc."
    )
    base_url: Optional[str] = Field(
        default=None,
        description="Base URL for providers that expose an HTTP API (e.g. Ollama, LiteLLM).",
    )

    @field_validator("name", "type", mode="before")
    @classmethod
    def _require_nonempty(cls, v: Any) -> str:
        s = str(v).strip()
        if not s:
            raise ValueError("Field must not be empty.")
        return s


class OpenShellInferenceRoute(BaseModel):
    """Maps a logical route to a provider + model pair in the OpenShell config."""

    provider: str = Field(description="Name of the provider this route uses.")
    model: str = Field(description="Model identifier forwarded to the provider.")

    @field_validator("provider", "model", mode="before")
    @classmethod
    def _require_nonempty(cls, v: Any) -> str:
        s = str(v).strip()
        if not s:
            raise ValueError("Field must not be empty.")
        return s


# ---------------------------------------------------------------------------
# Sandbox / coding-agent models
# ---------------------------------------------------------------------------


class SandboxInfo(BaseModel):
    """Runtime state of a NemoClaw coding-agent sandbox container."""

    name: str = Field(description="Sandbox identifier (Docker container name).")
    status: str = Field(
        description="Container status as reported by Docker (e.g. 'running', 'exited')."
    )
    image: str = Field(description="Docker image the sandbox was created from.")
    ports: list[int] = Field(
        default_factory=list,
        description="Host-side TCP ports exposed by this sandbox.",
    )
    keep: bool = Field(
        default=False,
        description="True when the sandbox has the 'nemoclaw.keep=true' label set.",
    )
    policies: list[str] = Field(
        default_factory=list,
        description="Security policy names applied to this sandbox (e.g. 'landlock', 'seccomp').",
    )

    @field_validator("status", mode="before")
    @classmethod
    def _lowercase_status(cls, v: Any) -> str:
        return str(v).strip().lower()

    @property
    def running(self) -> bool:
        return self.status == "running"

    @property
    def exited(self) -> bool:
        return self.status == "exited"


# ---------------------------------------------------------------------------
# OpenAI-compatible inference response models
# ---------------------------------------------------------------------------


class MessageContent(BaseModel):
    """The message object inside a chat completion choice."""

    role: str = Field(default="assistant")
    content: str = Field(description="Text content of the message.")

    @field_validator("content", mode="before")
    @classmethod
    def _coerce_content(cls, v: Any) -> str:
        if v is None:
            return ""
        return str(v)


class InferenceChoice(BaseModel):
    """A single choice in an OpenAI-compatible completion response."""

    index: int = Field(default=0)
    message: MessageContent
    finish_reason: Optional[str] = Field(default=None)


class InferenceUsage(BaseModel):
    """Token usage statistics from an inference response."""

    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)
    total_tokens: int = Field(default=0)

    @model_validator(mode="after")
    def _validate_total(self) -> "InferenceUsage":
        expected = self.prompt_tokens + self.completion_tokens
        if self.total_tokens == 0 and expected > 0:
            # Some providers omit total_tokens; compute it ourselves.
            object.__setattr__(self, "total_tokens", expected)
        return self


class InferenceResponse(BaseModel):
    """Top-level OpenAI-compatible chat completion response.

    Used to validate responses from NemoClaw, LiteLLM, and direct Ollama
    /v1/chat/completions calls.
    """

    model: str = Field(description="Model identifier echoed by the provider.")
    choices: list[InferenceChoice] = Field(
        description="One or more completion choices."
    )
    usage: Optional[InferenceUsage] = Field(default=None)

    @field_validator("choices", mode="before")
    @classmethod
    def _require_at_least_one_choice(cls, v: Any) -> Any:
        if isinstance(v, list) and len(v) == 0:
            raise ValueError("InferenceResponse must contain at least one choice.")
        return v

    @property
    def first_content(self) -> str:
        """Shortcut to the text of the first choice's message."""
        return self.choices[0].message.content if self.choices else ""


# ---------------------------------------------------------------------------
# LiteLLM proxy models
# ---------------------------------------------------------------------------


class LiteLLMModelEntry(BaseModel):
    """A single entry from the LiteLLM /models list response.

    LiteLLM returns an OpenAI-compatible ``GET /models`` response where
    ``object`` is ``"model"`` and ``id`` is the routable model string.
    """

    id: str = Field(description="Routable model identifier (e.g. 'ollama/llama3:8b').")
    object_type: str = Field(
        alias="object",
        default="model",
        description="Always 'model' for this endpoint.",
    )
    owned_by: str = Field(
        default="",
        description="Provider or owner string (e.g. 'ollama', 'openai').",
    )

    model_config = {"populate_by_name": True}

    @field_validator("id", mode="before")
    @classmethod
    def _require_nonempty_id(cls, v: Any) -> str:
        s = str(v).strip()
        if not s:
            raise ValueError("LiteLLMModelEntry.id must not be empty.")
        return s

    @property
    def provider(self) -> str:
        """Return the provider prefix from a 'provider/model' id, or owned_by."""
        if "/" in self.id:
            return self.id.split("/", 1)[0]
        return self.owned_by or "unknown"

    @property
    def model_name(self) -> str:
        """Return the model name without the provider prefix."""
        if "/" in self.id:
            return self.id.split("/", 1)[1]
        return self.id


class LiteLLMModelsResponse(BaseModel):
    """Top-level response from GET /models on the LiteLLM proxy."""

    object: str = Field(default="list")
    data: list[LiteLLMModelEntry] = Field(default_factory=list)

    @property
    def model_ids(self) -> list[str]:
        return [entry.id for entry in self.data]
