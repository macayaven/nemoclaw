"""Orchestrator configuration via Pydantic settings.

Defines the settings hierarchy used across all orchestrator components.
Configuration can be overridden via environment variables prefixed with
``NEMOCLAW_``.

Example::

    from orchestrator.config import OrchestratorSettings

    settings = OrchestratorSettings()
    print(settings.agents["codex"].sandbox_name)  # "codex-dev"
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class AgentConfig(BaseModel):
    """Configuration for a single agent running inside an OpenShell sandbox.

    Attributes:
        sandbox_name: The OpenShell sandbox identifier passed to
            ``openshell sandbox connect <sandbox_name>``.
        specialization: Human-readable description of what this agent
            is optimised for.
        inference_type: Whether the agent performs inference locally on
            the DGX Spark or calls a remote cloud API.
        capabilities: List of task-type strings this agent handles well,
            used by the orchestrator for automatic routing.
    """

    sandbox_name: str
    specialization: str
    inference_type: Literal["local", "cloud"]
    capabilities: list[str] = Field(default_factory=list)


_DEFAULT_AGENTS: dict[str, AgentConfig] = {
    "openclaw": AgentConfig(
        sandbox_name="nemoclaw-main",
        specialization="general reasoning and orchestration",
        inference_type="local",
        capabilities=["analysis", "implementation", "planning", "reasoning"],
    ),
    "claude": AgentConfig(
        sandbox_name="claude-dev",
        specialization="code review and refactoring",
        inference_type="cloud",
        capabilities=["code_review", "analysis", "documentation"],
    ),
    "codex": AgentConfig(
        sandbox_name="codex-dev",
        specialization="code generation and implementation",
        inference_type="cloud",
        capabilities=["code_generation", "implementation", "debugging"],
    ),
    "gemini": AgentConfig(
        sandbox_name="gemini-dev",
        specialization="research and information synthesis",
        inference_type="cloud",
        capabilities=["research", "analysis", "summarisation"],
    ),
}


class OrchestratorSettings(BaseSettings):
    """Top-level settings for the NemoClaw orchestrator.

    All fields can be overridden via environment variables prefixed with
    ``NEMOCLAW_``. For example, ``NEMOCLAW_SANDBOX_TIMEOUT=60``.

    Attributes:
        shared_workspace: Path on the DGX Spark host that is mounted into
            all sandboxes as the shared MCP filesystem.
        sandbox_timeout: Default timeout in seconds for any single command
            executed inside a sandbox.
        max_delegation_depth: Maximum number of nested delegation hops
            allowed before the orchestrator raises a recursion error.
        agents: Mapping of logical agent name to its configuration.
    """

    shared_workspace: Path = Field(
        default_factory=lambda: Path.home() / "workspace" / "shared-agents",
        description="Host path of the shared MCP workspace mounted into all sandboxes.",
    )
    sandbox_timeout: int = Field(
        default=120,
        ge=1,
        description="Default command timeout in seconds.",
    )
    max_delegation_depth: int = Field(
        default=5,
        ge=1,
        description="Maximum recursion depth for chained delegations.",
    )
    agents: dict[str, AgentConfig] = Field(
        default_factory=lambda: dict(_DEFAULT_AGENTS),
        description="Map of agent name to its sandbox configuration.",
    )

    model_config = {
        "env_prefix": "NEMOCLAW_",
        "env_nested_delimiter": "__",
        "case_sensitive": False,
    }

    def get_agent(self, name: str) -> AgentConfig:
        """Return the AgentConfig for *name*, raising KeyError if absent.

        Args:
            name: Logical agent name (e.g. ``"codex"``).

        Returns:
            The corresponding AgentConfig.

        Raises:
            KeyError: If *name* is not present in ``self.agents``.
        """
        if name not in self.agents:
            available = ", ".join(sorted(self.agents))
            raise KeyError(f"Unknown agent {name!r}. Available agents: {available}")
        return self.agents[name]
