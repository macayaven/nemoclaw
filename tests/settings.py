"""
NemoClaw test suite settings.

Loaded from environment variables and an optional .env file in the tests/
directory (or any parent). All fields have sensible defaults matching the
live three-node deployment:

  spark-caeb.local  — DGX Spark, primary inference + NemoClaw host
  mac-studio.local  — Mac Studio, secondary inference + dev workstation
  raspi.local       — Raspberry Pi, infrastructure plane
"""

from __future__ import annotations

from ipaddress import IPv4Address
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TESTS_DIR = Path(__file__).parent
_ENV_FILE = _TESTS_DIR / ".env"


# ---------------------------------------------------------------------------
# Per-host base
# ---------------------------------------------------------------------------


class HostSettings(BaseSettings):
    """Common fields shared by every machine in the cluster."""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    hostname: str
    ip: IPv4Address
    user: str = "carlos"
    ssh_key: Path | None = Field(
        default=None,
        description="Path to the SSH private key used to connect to this host. "
        "Falls back to ssh-agent / ~/.ssh/id_* when None.",
    )

    # ------------------------------------------------------------------
    # Convenience helpers used by fixtures
    # ------------------------------------------------------------------

    @property
    def ssh_host(self) -> str:
        """Return user@hostname string suitable for fabric / subprocess."""
        return f"{self.user}@{self.hostname}"

    @property
    def ip_str(self) -> str:
        return str(self.ip)


# ---------------------------------------------------------------------------
# Machine-specific settings
# ---------------------------------------------------------------------------


class SparkSettings(HostSettings):
    """DGX Spark — primary inference node and NemoClaw host."""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        env_prefix="SPARK_",
        extra="ignore",
    )

    hostname: str = "spark-caeb.local"
    ip: IPv4Address = IPv4Address("192.168.1.10")
    user: str = "carlos"
    ssh_key: Path | None = None

    # NemoClaw / OpenShell service
    nemoclaw_port: int = Field(default=4000, description="NemoClaw HTTP port")
    nemoclaw_base_url: str = Field(
        default="http://spark-caeb.local:4000",
        description="Base URL for the NemoClaw OpenShell API",
    )

    # Ollama on Spark
    ollama_port: int = Field(default=11434, description="Ollama API port on Spark")
    ollama_base_url: str = Field(
        default="http://spark-caeb.local:11434",
        description="Base URL for the Ollama API on Spark",
    )

    # Docker / container runtime
    docker_socket: str = Field(
        default="/var/run/docker.sock",
        description="Path to the Docker socket on Spark",
    )

    # Minimum free disk for pre-flight check (GiB)
    min_disk_free_gb: float = Field(
        default=50.0,
        description="Minimum free disk space in GiB required on Spark",
    )

    # Tailscale
    tailscale_ip: IPv4Address | None = Field(
        default=None,
        description="Tailscale IP of the Spark node (populated at runtime if "
        "SPARK_TAILSCALE_IP is set in the environment)",
    )


class MacSettings(HostSettings):
    """Mac Studio — secondary inference node and developer workstation."""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        env_prefix="MAC_",
        extra="ignore",
    )

    hostname: str = "mac-studio.local"
    ip: IPv4Address = IPv4Address("192.168.1.20")
    user: str = "carlos"
    ssh_key: Path | None = None

    # Ollama on Mac Studio
    ollama_port: int = Field(default=11434, description="Ollama API port on Mac Studio")
    ollama_base_url: str = Field(
        default="http://mac-studio.local:11434",
        description="Base URL for the Ollama API on Mac Studio",
    )

    # launchd service name for Ollama
    launchd_service: str = Field(
        default="com.ollama.ollama",
        description="launchd service label used to manage Ollama on macOS",
    )

    # Tailscale
    tailscale_ip: IPv4Address | None = Field(
        default=None,
        description="Tailscale IP of the Mac Studio node",
    )


class PiSettings(HostSettings):
    """Raspberry Pi — infrastructure plane (LiteLLM, DNS, monitoring)."""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        env_prefix="PI_",
        extra="ignore",
    )

    hostname: str = "raspi.local"
    ip: IPv4Address = IPv4Address("192.168.1.30")
    user: str = "carlos"
    ssh_key: Path | None = None

    # LiteLLM proxy
    litellm_port: int = Field(default=4000, description="LiteLLM proxy HTTP port")
    litellm_base_url: str = Field(
        default="http://raspi.local:4000",
        description="Base URL for the LiteLLM proxy running on the Pi",
    )

    # Monitoring (Prometheus + Grafana)
    prometheus_port: int = Field(default=9090, description="Prometheus HTTP port")
    grafana_port: int = Field(default=3000, description="Grafana HTTP port")

    # Minimum free RAM for pre-flight check (MiB)
    min_free_ram_mb: int = Field(
        default=128,
        description="Minimum free RAM in MiB required on the Pi",
    )

    # Tailscale
    tailscale_ip: IPv4Address | None = Field(
        default=None,
        description="Tailscale IP of the Raspberry Pi node",
    )


# ---------------------------------------------------------------------------
# Root test settings
# ---------------------------------------------------------------------------


class TestSettings(BaseSettings):
    """Root settings object consumed by pytest fixtures."""

    __test__ = False

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- cluster nodes ----
    spark: SparkSettings = Field(default_factory=SparkSettings)
    mac: MacSettings = Field(default_factory=MacSettings)
    pi: PiSettings = Field(default_factory=PiSettings)

    # ---- API keys (optional — tests that need them are skipped when absent) ----
    anthropic_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="ANTHROPIC_API_KEY",
        description="Anthropic API key for Claude integration tests",
    )
    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="OPENAI_API_KEY",
        description="OpenAI API key for GPT integration tests",
    )
    gemini_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="GEMINI_API_KEY",
        description="Google Gemini API key for Gemini integration tests",
    )

    # ---- Tailscale overlay network ----
    # These mirror the per-host tailscale_ip fields but are exposed at the top
    # level for tests that need to iterate over all Tailscale addresses.
    tailscale_spark_ip: IPv4Address | None = Field(
        default=None,
        validation_alias="TAILSCALE_SPARK_IP",
        description="Tailscale IP of spark-caeb",
    )
    tailscale_mac_ip: IPv4Address | None = Field(
        default=None,
        validation_alias="TAILSCALE_MAC_IP",
        description="Tailscale IP of mac-studio",
    )
    tailscale_pi_ip: IPv4Address | None = Field(
        default=None,
        validation_alias="TAILSCALE_PI_IP",
        description="Tailscale IP of raspi",
    )

    # ---- Global timeouts / retries ----
    http_timeout: float = Field(
        default=30.0,
        description="Default HTTP request timeout in seconds",
    )
    ssh_connect_timeout: float = Field(
        default=15.0,
        description="SSH connection timeout in seconds",
    )

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    @property
    def all_hosts(self) -> list[HostSettings]:
        """Return every host settings object for iteration."""
        return [self.spark, self.mac, self.pi]

    @property
    def tailscale_ips(self) -> dict[str, IPv4Address | None]:
        """Return a mapping of node name -> Tailscale IP."""
        return {
            "spark": self.tailscale_spark_ip or self.spark.tailscale_ip,
            "mac": self.tailscale_mac_ip or self.mac.tailscale_ip,
            "pi": self.tailscale_pi_ip or self.pi.tailscale_ip,
        }

    def has_api_key(self, provider: str) -> bool:
        """Return True when the named provider's API key is configured.

        Args:
            provider: One of ``"anthropic"``, ``"openai"``, or ``"gemini"``.
        """
        mapping = {
            "anthropic": self.anthropic_api_key,
            "openai": self.openai_api_key,
            "gemini": self.gemini_api_key,
        }
        return mapping.get(provider.lower()) is not None


# ---------------------------------------------------------------------------
# Module-level singleton — import and use directly in fixtures / tests
# ---------------------------------------------------------------------------

settings = TestSettings()
