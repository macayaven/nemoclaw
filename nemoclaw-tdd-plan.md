# NemoClaw TDD Implementation Plan

*Test-Driven Deployment & Validation Framework*
*Python • Type Safety • Rust (Codex) • uv • pytest*
*March 2026 • carlos@mac-studio.local*

---

## Approach

Every phase follows the same cycle:

```
1. Write tests that define the expected state (RED)
2. Execute deployment steps to make tests pass (GREEN)
3. Refactor/harden once green (REFACTOR)
```

Tests are the source of truth. If the tests pass, the phase is done. If they don't, the deployment is incomplete — no exceptions.

### Two-Layer TDD Flow

Each phase separates tests into two layers:

- **Layer A — Contract tests**: Validate config files, command output schemas, state models. Fast, deterministic, no network. These catch structural errors.
- **Layer B — Behavioral tests**: Hit real endpoints, measure latency, verify end-to-end flows. Slower, may need retries. These catch runtime failures.

### Tooling

| Tool | Role |
|------|------|
| **uv** | Python project manager — fast, Rust-backed, replaces pip/venv |
| **pytest** | Test runner with fixtures, parametrize, markers |
| **pytest-testinfra** | Host-level assertions (service running, package installed, file exists) |
| **pytest-timeout** | Prevent hung tests |
| **pytest-xdist** | Parallel test execution (Phase 0 across all machines) |
| **pytest-rerunfailures** | Retry transient infra failures |
| **pydantic-settings** | Type-safe config from env vars with validation |
| **pydantic** | Type-safe models for command output and API responses |
| **fabric** | SSH-based remote command execution |
| **httpx** | Async HTTP client for API health checks |
| **tenacity** | Retry/poll with backoff for eventually-consistent infra |
| **packaging** | Robust version comparison (no string splitting) |

### Project Structure

```
nemoclaw/
├── nemoclaw-technical-spec.docx   # Original spec
├── nemoclaw-architecture.md       # Conceptual guide
├── nemoclaw-action-plan.md        # Step-by-step action plan
├── nemoclaw-tdd-plan.md           # This document
├── openshell-env/                 # OpenShell virtualenv (Spark)
├── image.png                      # NemoClaw workflow diagram
│
└── tests/                         # TDD test suite
    ├── pyproject.toml             # uv project config
    ├── conftest.py                # Shared fixtures, SSH wrapper, host configs
    ├── settings.py                # Pydantic BaseSettings — validated env config
    ├── models.py                  # Pydantic models for command output validation
    ├── helpers.py                 # poll_until_ready(), parse_json_output(), etc.
    │
    ├── phase6_orchestrator/
    │   ├── __init__.py
    │   ├── test_cli.py
    │   ├── test_orchestrator.py
    │   ├── test_sandbox_bridge.py
    │   ├── test_shared_workspace.py
    │   └── test_task_manager.py
    │
    ├── phase0_preflight/
    │   ├── __init__.py
    │   ├── test_spark_prerequisites.py
    │   ├── test_mac_prerequisites.py
    │   └── test_pi_prerequisites.py
    │
    ├── phase1_core/
    │   ├── __init__.py
    │   ├── test_ollama_config.py
    │   ├── test_gateway.py
    │   ├── test_provider.py
    │   ├── test_inference_routing.py
    │   ├── test_sandbox_openclaw.py
    │   └── test_idempotency.py
    │
    ├── phase2_mac/
    │   ├── __init__.py
    │   ├── test_mac_ollama.py
    │   ├── test_mac_provider.py
    │   └── test_provider_switching.py
    │
    ├── phase3_pi/
    │   ├── __init__.py
    │   ├── test_litellm_proxy.py
    │   ├── test_litellm_degraded.py
    │   ├── test_dns.py
    │   ├── test_monitoring.py
    │   └── test_tailscale_routing.py
    │
    ├── phase4_agents/
    │   ├── __init__.py
    │   ├── test_claude_sandbox.py
    │   ├── test_codex_sandbox.py
    │   ├── test_gemini_sandbox.py
    │   ├── test_multi_sandbox.py
    │   ├── test_sandbox_isolation.py
    │   └── test_secret_hygiene.py
    │
    └── phase5_mobile/
        ├── __init__.py
        ├── test_tailscale_gateway.py
        └── test_remote_access.py
```

---

## Phase 0 — Pre-flight Validation

### Preconditions
- SSH access to all three machines (already configured)
- Test machine can reach all hosts on the network
- API keys available as environment variables (or in `.env`)

### Success Criteria
All prerequisite software is installed, running, and at the required versions on each machine.

### Definition of Done
`pytest tests/phase0_preflight/ -v` — all tests pass.

### Use Cases Covered
| UC | Description |
|----|-------------|
| UC-0.1 | DGX Spark has Docker ≥28.04 running |
| UC-0.2 | DGX Spark has Ollama installed with required models downloaded |
| UC-0.3 | DGX Spark has Node.js ≥20 and npm ≥10 |
| UC-0.4 | DGX Spark has sufficient disk space (≥100GB free) |
| UC-0.5 | DGX Spark kernel supports Landlock and seccomp |
| UC-0.6 | DGX Spark has cgroup v2 enabled |
| UC-0.7 | Mac Studio has Ollama installed |
| UC-0.8 | Mac Studio can SSH to Spark |
| UC-0.9 | Raspberry Pi has ≥2GB free RAM |
| UC-0.10 | All machines have Tailscale connected |

### Edge Cases (test)
| Edge | Test? | Rationale |
|------|-------|-----------|
| Docker daemon not running but installed | **Yes** | Common after reboot — test must distinguish installed vs running |
| Ollama model partially downloaded | **Yes** | Interrupted downloads leave corrupt state — verify model is loadable |
| Disk space exactly at threshold | **Yes** | Boundary condition — test with parametrized threshold |
| Tailscale connected but not approved | **Yes** | Tailscale can be online but routes not approved in admin |

### Test Signatures

```python
# tests/phase0_preflight/test_spark_prerequisites.py

import pytest
from pydantic import BaseModel

class SparkPrereqs(BaseModel):
    docker_version: str
    docker_running: bool
    ollama_version: str
    models_available: list[str]
    disk_free_gb: float
    node_version: str
    landlock_supported: bool
    seccomp_supported: bool
    cgroup_v2: bool
    tailscale_connected: bool

@pytest.fixture
def spark(spark_host) -> SparkPrereqs:
    """Gather all prereq state from the Spark via SSH."""
    ...

class TestSparkDocker:
    def test_docker_installed(self, spark: SparkPrereqs):
        assert spark.docker_version != ""

    def test_docker_version_minimum(self, spark: SparkPrereqs):
        major, minor, _ = spark.docker_version.split(".")
        assert int(major) >= 28 or (int(major) == 28 and int(minor) >= 4)

    def test_docker_running(self, spark: SparkPrereqs):
        assert spark.docker_running is True

class TestSparkOllama:
    def test_ollama_installed(self, spark: SparkPrereqs):
        assert spark.ollama_version != ""

    @pytest.mark.parametrize("model", [
        "nemotron-3-super:120b",
        "nemotron-3-super:120b",
    ])
    def test_model_downloaded(self, spark: SparkPrereqs, model: str):
        assert model in spark.models_available

class TestSparkDisk:
    def test_sufficient_disk_space(self, spark: SparkPrereqs):
        assert spark.disk_free_gb >= 100.0, (
            f"Need ≥100GB free, have {spark.disk_free_gb:.1f}GB"
        )

class TestSparkKernel:
    def test_landlock_supported(self, spark: SparkPrereqs):
        assert spark.landlock_supported

    def test_seccomp_supported(self, spark: SparkPrereqs):
        assert spark.seccomp_supported

    def test_cgroup_v2(self, spark: SparkPrereqs):
        assert spark.cgroup_v2

class TestSparkTailscale:
    def test_tailscale_connected(self, spark: SparkPrereqs):
        assert spark.tailscale_connected
```

```python
# tests/phase0_preflight/test_mac_prerequisites.py

class TestMacOllama:
    def test_ollama_installed(self, mac: MacPrereqs):
        assert mac.ollama_version != ""

class TestMacSSH:
    def test_can_ssh_to_spark(self, mac_host, spark_host):
        """Mac can reach Spark via SSH."""
        result = mac_host.run(f"ssh {spark_host.hostname} echo OK")
        assert result.stdout.strip() == "OK"

class TestMacTailscale:
    def test_tailscale_connected(self, mac: MacPrereqs):
        assert mac.tailscale_connected
```

```python
# tests/phase0_preflight/test_pi_prerequisites.py

class TestPiResources:
    def test_sufficient_ram(self, pi: PiPrereqs):
        assert pi.free_ram_mb >= 2000, (
            f"Need ≥2GB free RAM, have {pi.free_ram_mb}MB"
        )

    def test_python3_available(self, pi: PiPrereqs):
        assert pi.python3_version.startswith("3.")

class TestPiNetwork:
    def test_can_reach_spark(self, pi_host, spark_host):
        result = pi_host.run(f"ping -c 1 -W 3 {spark_host.hostname}")
        assert result.return_code == 0

    def test_can_reach_mac(self, pi_host, mac_host):
        result = pi_host.run(f"ping -c 1 -W 3 {mac_host.hostname}")
        assert result.return_code == 0
```

---

## Phase 1 — Core NemoClaw on Spark

### Preconditions
- Phase 0 passes completely
- Ollama models already downloaded on Spark

### Success Criteria
NemoClaw is running on the Spark: OpenShell gateway is healthy, Ollama is reachable from sandboxes, the inference route is set to Nemotron 120B, and the OpenClaw sandbox responds to chat requests.

### Definition of Done
`pytest tests/phase1_core/ -v` — all tests pass.

### Use Cases Covered
| UC | Description |
|----|-------------|
| UC-1.1 | Ollama listens on 0.0.0.0:11434 (all interfaces) |
| UC-1.2 | Ollama keep-alive is set to infinite (-1) |
| UC-1.3 | OpenShell gateway starts and reports "Connected" |
| UC-1.4 | Provider `local-ollama` is registered and points to Ollama |
| UC-1.5 | Inference route is set to `nemotron-3-super:120b` via `local-ollama` |
| UC-1.6 | `inference.local` resolves inside sandbox and returns model response |
| UC-1.7 | OpenClaw sandbox is created with `--keep` and port 18789 forwarded |
| UC-1.8 | OpenClaw UI returns HTTP 200 on port 18789 |
| UC-1.9 | End-to-end inference: send prompt → receive completion |
| UC-1.10 | Nemotron is pre-warmed (model loaded in GPU memory) |

### Edge Cases (test)
| Edge | Test? | Rationale |
|------|-------|-----------|
| Ollama on 127.0.0.1 instead of 0.0.0.0 | **Yes** | Most common misconfiguration — sandbox can't reach it |
| Gateway not ready within 3 minutes | **Yes** | k3s bootstrap can hang — timeout must be explicit |
| Provider created with wrong IP | **Yes** | `hostname -I` can return unexpected interface — validate |
| Sandbox create with port already in use | **Yes** | Previous sandbox not cleaned up — detect and report |
| Nemotron cold start timeout (>60s) | **Yes** | First inference can take 30-60s — test with generous timeout |
| `inference.local` returns 502 (provider unreachable) | **Yes** | Ollama crashed or model not loaded |

### Test Signatures

```python
# tests/phase1_core/test_ollama_config.py

class TestOllamaBinding:
    def test_listens_on_all_interfaces(self, spark_ssh):
        """Ollama must bind 0.0.0.0:11434, not 127.0.0.1."""
        result = spark_ssh.run("ss -tlnp | grep 11434")
        assert "0.0.0.0:11434" in result.stdout or "*:11434" in result.stdout
        assert "127.0.0.1:11434" not in result.stdout

    def test_keep_alive_set(self, spark_ssh):
        """OLLAMA_KEEP_ALIVE=-1 prevents model unloading."""
        result = spark_ssh.run(
            "cat /etc/systemd/system/ollama.service.d/override.conf"
        )
        assert "OLLAMA_KEEP_ALIVE=-1" in result.stdout

    def test_ollama_responds(self, spark_ollama_url: str):
        """Ollama API returns 200 on health endpoint."""
        resp = httpx.get(f"{spark_ollama_url}/api/tags", timeout=10)
        assert resp.status_code == 200

    def test_ollama_reachable_from_other_host(self, spark_ip: str):
        """Ollama is reachable from a different machine (not just localhost)."""
        resp = httpx.get(f"http://{spark_ip}:11434/api/tags", timeout=10)
        assert resp.status_code == 200
```

```python
# tests/phase1_core/test_gateway.py

class TestGateway:
    @pytest.mark.timeout(180)  # 3 min for k3s bootstrap
    def test_gateway_starts(self, spark_ssh):
        result = spark_ssh.run("openshell status")
        assert "Connected" in result.stdout

    def test_gateway_port_open(self, spark_ip: str):
        resp = httpx.get(
            f"https://{spark_ip}:8080",
            verify=False,
            timeout=10,
        )
        # Gateway responds (may be 401/403 without auth, but not connection refused)
        assert resp.status_code in (200, 401, 403)
```

```python
# tests/phase1_core/test_provider.py

class TestProvider:
    def test_local_ollama_registered(self, spark_ssh):
        result = spark_ssh.run("openshell provider list")
        assert "local-ollama" in result.stdout

    def test_provider_points_to_ollama(self, spark_ssh):
        result = spark_ssh.run("openshell provider get local-ollama")
        assert "11434" in result.stdout
```

```python
# tests/phase1_core/test_inference_routing.py

class TestInferenceRoute:
    def test_route_set_to_nemotron(self, spark_ssh):
        result = spark_ssh.run("openshell inference get")
        assert "nemotron-3-super" in result.stdout
        assert "local-ollama" in result.stdout

    @pytest.mark.timeout(90)  # Cold start can take 60s
    def test_inference_local_returns_completion(self, spark_ssh):
        """End-to-end: sandbox curl to inference.local gets a model response."""
        result = spark_ssh.run(
            "openshell sandbox create -- "
            "curl -s https://inference.local/v1/chat/completions "
            "--json '{\"messages\":[{\"role\":\"user\",\"content\":\"say hello\"}],"
            "\"max_tokens\":10}'"
        )
        assert '"choices"' in result.stdout or '"content"' in result.stdout

    def test_inference_local_not_502(self, spark_ssh):
        """inference.local must not return 502 (provider unreachable)."""
        result = spark_ssh.run(
            "openshell sandbox create -- "
            "curl -s -o /dev/null -w '%{http_code}' "
            "https://inference.local/v1/chat/completions "
            "--json '{\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],"
            "\"max_tokens\":5}'"
        )
        assert result.stdout.strip() != "502"
```

```python
# tests/phase1_core/test_sandbox_openclaw.py

class TestOpenClawSandbox:
    def test_sandbox_exists(self, spark_ssh):
        result = spark_ssh.run("openshell sandbox list")
        assert "nemoclaw-main" in result.stdout

    def test_sandbox_has_keep_flag(self, spark_ssh):
        result = spark_ssh.run("openshell sandbox get nemoclaw-main")
        assert "keep" in result.stdout.lower()

    def test_ui_returns_200(self, spark_ip: str):
        resp = httpx.get(f"http://{spark_ip}:18789", timeout=15)
        assert resp.status_code == 200

    @pytest.mark.timeout(120)
    def test_end_to_end_chat(self, spark_ip: str):
        """Send a real chat message through OpenClaw and get a response."""
        resp = httpx.post(
            f"http://{spark_ip}:11434/v1/chat/completions",
            json={
                "model": "nemotron-3-super:120b",
                "messages": [{"role": "user", "content": "Say hello in one word"}],
                "max_tokens": 10,
            },
            timeout=90,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["choices"]) > 0
        assert len(data["choices"][0]["message"]["content"]) > 0
```

---

## Phase 2 — Mac Studio Integration

### Preconditions
- Phase 1 passes completely
- Mac Studio is reachable from Spark via network

### Success Criteria
Mac Ollama is running with `gemma4:27b`, registered as a provider on the Spark, provider switching works, and the OpenClaw UI is accessible from the Mac browser.

### Definition of Done
`pytest tests/phase2_mac/ -v` — all tests pass.

### Use Cases Covered
| UC | Description |
|----|-------------|
| UC-2.1 | Mac Ollama listens on 0.0.0.0:11434 |
| UC-2.2 | `gemma4:27b` model is downloaded and loadable on Mac |
| UC-2.3 | Provider `mac-ollama` registered on Spark pointing to Mac's IP |
| UC-2.4 | Switching to `mac-ollama` provider works without error |
| UC-2.5 | Inference through Mac provider returns a completion |
| UC-2.6 | Switching back to `local-ollama` restores Nemotron |
| UC-2.7 | OpenClaw UI accessible from Mac browser |
| UC-2.8 | Ollama auto-starts on Mac via launchd (optional) |

### Edge Cases (test)
| Edge | Test? | Rationale |
|------|-------|-----------|
| Mac Ollama on 127.0.0.1 only | **Yes** | Same issue as Spark — must be 0.0.0.0 |
| Mac sleeping / Ollama not running | **Yes** | Provider should fail gracefully, not hang |
| Provider switch while sandbox has active session | **Yes** | Must not crash the sandbox |
| Mac IP changes after DHCP renewal | **No** | Low probability in short session — document as known risk |

### Test Signatures

```python
# tests/phase2_mac/test_mac_ollama.py

class TestMacOllamaBinding:
    def test_ollama_reachable_from_spark(self, mac_ip: str):
        resp = httpx.get(f"http://{mac_ip}:11434/api/tags", timeout=10)
        assert resp.status_code == 200

    def test_gemma4_8b_available(self, mac_ip: str):
        resp = httpx.get(f"http://{mac_ip}:11434/api/tags", timeout=10)
        models = [m["name"] for m in resp.json()["models"]]
        assert any("gemma4" in m for m in models)

# tests/phase2_mac/test_provider_switching.py

class TestProviderSwitching:
    def test_switch_to_mac(self, spark_ssh):
        result = spark_ssh.run(
            "openshell inference set --provider mac-ollama --model gemma4:27b"
        )
        assert result.return_code == 0

    def test_mac_inference_works(self, spark_ssh):
        result = spark_ssh.run("openshell inference get")
        assert "mac-ollama" in result.stdout
        assert "gemma4" in result.stdout

    def test_switch_back_to_spark(self, spark_ssh):
        result = spark_ssh.run(
            "openshell inference set --provider local-ollama "
            "--model nemotron-3-super:120b"
        )
        assert result.return_code == 0
        result = spark_ssh.run("openshell inference get")
        assert "local-ollama" in result.stdout

    @pytest.mark.timeout(30)
    def test_mac_provider_timeout_when_offline(self, spark_ssh):
        """If Mac is unreachable, provider switch should fail, not hang."""
        # This test runs only when Mac is intentionally offline
        ...
```

---

## Phase 3 — Raspberry Pi Infrastructure Plane

### Preconditions
- Phase 1 passes (Spark Ollama reachable)
- Phase 2 passes (Mac Ollama reachable)
- Pi has Python 3 and sufficient RAM

### Success Criteria
LiteLLM proxy routes requests to both Spark and Mac by model name, DNS resolves lab hostnames, monitoring is active, and Tailscale subnet routing works.

### Definition of Done
`pytest tests/phase3_pi/ -v` — all tests pass.

### Use Cases Covered
| UC | Description |
|----|-------------|
| UC-3.1 | LiteLLM proxy is running on Pi port 4000 |
| UC-3.2 | LiteLLM routes `nemotron-3-super:120b` → Spark Ollama |
| UC-3.3 | LiteLLM routes `gemma4:27b` → Mac Ollama |
| UC-3.4 | LiteLLM `/v1/models` returns models from both backends |
| UC-3.5 | DNS resolves `spark.lab` → Spark IP |
| UC-3.6 | DNS resolves `mac.lab` → Mac IP |
| UC-3.7 | DNS resolves `ai.lab` → Pi IP |
| UC-3.8 | Uptime Kuma is running and monitoring all endpoints |
| UC-3.9 | Tailscale advertises subnet route |

### Edge Cases (test)
| Edge | Test? | Rationale |
|------|-------|-----------|
| LiteLLM with one backend down | **Yes** | Should return error for that model, not crash |
| LiteLLM with invalid model name | **Yes** | Should return 404/400, not proxy to random backend |
| DNS for non-existent hostname | **Yes** | Should return NXDOMAIN, not hang |
| Pi runs out of RAM under load | **Yes** | Monitor RSS of LiteLLM under concurrent requests |

### Test Signatures

```python
# tests/phase3_pi/test_litellm_proxy.py

class TestLiteLLMHealth:
    def test_proxy_running(self, pi_ip: str):
        resp = httpx.get(f"http://{pi_ip}:4000/health", timeout=10)
        assert resp.status_code == 200

    def test_models_endpoint(self, pi_ip: str):
        resp = httpx.get(f"http://{pi_ip}:4000/v1/models", timeout=10)
        assert resp.status_code == 200
        models = resp.json()
        model_ids = [m["id"] for m in models["data"]]
        assert "nemotron-3-super:120b" in model_ids
        assert "gemma4:27b" in model_ids

class TestLiteLLMRouting:
    @pytest.mark.timeout(90)
    def test_routes_nemotron_to_spark(self, pi_ip: str):
        resp = httpx.post(
            f"http://{pi_ip}:4000/v1/chat/completions",
            json={
                "model": "nemotron-3-super:120b",
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 5,
            },
            timeout=90,
        )
        assert resp.status_code == 200

    @pytest.mark.timeout(30)
    def test_routes_qwen_to_mac(self, pi_ip: str):
        resp = httpx.post(
            f"http://{pi_ip}:4000/v1/chat/completions",
            json={
                "model": "gemma4:27b",
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 5,
            },
            timeout=30,
        )
        assert resp.status_code == 200

    def test_invalid_model_returns_error(self, pi_ip: str):
        resp = httpx.post(
            f"http://{pi_ip}:4000/v1/chat/completions",
            json={
                "model": "nonexistent-model",
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 5,
            },
            timeout=10,
        )
        assert resp.status_code in (400, 404, 500)

# tests/phase3_pi/test_dns.py

class TestDNS:
    @pytest.mark.parametrize("hostname,expected_ip_var", [
        ("spark.lab", "spark_ip"),
        ("mac.lab", "mac_ip"),
        ("ai.lab", "pi_ip"),
    ])
    def test_dns_resolves(self, hostname: str, expected_ip_var: str, request):
        expected_ip = request.getfixturevalue(expected_ip_var)
        import socket
        resolved = socket.gethostbyname(hostname)
        assert resolved == expected_ip

# tests/phase3_pi/test_monitoring.py

class TestUptimeKuma:
    def test_dashboard_accessible(self, pi_ip: str):
        resp = httpx.get(f"http://{pi_ip}:3001", timeout=10)
        assert resp.status_code == 200
```

---

## Phase 4 — Coding Agent Sandboxes

### Preconditions
- Phase 1 passes (OpenShell gateway + Ollama working)
- API keys available: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`

### Success Criteria
Claude Code, Codex, and Gemini CLI each run in separate OpenShell sandboxes with appropriate policies. Codex uses local Ollama for inference. All sandboxes are monitored via `openshell term`.

### Definition of Done
`pytest tests/phase4_agents/ -v` — all tests pass.

### Use Cases Covered
| UC | Description |
|----|-------------|
| UC-4.1 | Claude Code sandbox exists and is healthy |
| UC-4.2 | Claude Code sandbox has full policy coverage (no custom policy needed) |
| UC-4.3 | Codex sandbox exists and is healthy |
| UC-4.4 | Codex sandbox has custom policy for OpenAI + Ollama endpoints |
| UC-4.5 | Codex can reach Ollama via `host.openshell.internal:11434` |
| UC-4.6 | Gemini CLI sandbox exists with Gemini CLI installed |
| UC-4.7 | Gemini CLI sandbox has custom policy for Google API endpoints |
| UC-4.8 | All four sandboxes run simultaneously without conflict |
| UC-4.9 | `openshell term` shows activity from all sandboxes |
| UC-4.10 | Each sandbox has isolated filesystem (writes don't leak) |

### Edge Cases (test)
| Edge | Test? | Rationale |
|------|-------|-----------|
| Sandbox with missing API key | **Yes** | Should fail at creation with clear error, not inside sandbox |
| Two sandboxes on same port | **Yes** | Port conflict must be detected |
| Policy denies required endpoint | **Yes** | Verify TUI shows denial and agent can't proceed |
| Codex Ollama config missing inside sandbox | **Yes** | Codex should fall back to OpenAI or error clearly |
| Gemini CLI not installed in base image | **Yes** | Custom sandbox must install it — verify binary exists |

### Test Signatures

```python
# tests/phase4_agents/test_claude_sandbox.py

class TestClaudeSandbox:
    def test_sandbox_exists(self, spark_ssh):
        result = spark_ssh.run("openshell sandbox list")
        assert "claude-dev" in result.stdout

    def test_claude_binary_exists(self, spark_ssh):
        result = spark_ssh.run(
            "openshell sandbox connect claude-dev -- which claude"
        )
        assert "/usr/local/bin/claude" in result.stdout

    def test_anthropic_policy_present(self, spark_ssh):
        result = spark_ssh.run("openshell policy get claude-dev --full")
        assert "api.anthropic.com" in result.stdout

# tests/phase4_agents/test_codex_sandbox.py

class TestCodexSandbox:
    def test_sandbox_exists(self, spark_ssh):
        result = spark_ssh.run("openshell sandbox list")
        assert "codex-dev" in result.stdout

    def test_codex_binary_exists(self, spark_ssh):
        result = spark_ssh.run(
            "openshell sandbox connect codex-dev -- which codex"
        )
        assert "codex" in result.stdout

    def test_ollama_reachable_from_sandbox(self, spark_ssh):
        result = spark_ssh.run(
            "openshell sandbox connect codex-dev -- "
            "curl -s http://host.openshell.internal:11434/api/tags"
        )
        assert "models" in result.stdout

    def test_codex_config_has_ollama(self, spark_ssh):
        result = spark_ssh.run(
            "openshell sandbox connect codex-dev -- "
            "cat ~/.codex/config.toml"
        )
        assert "ollama" in result.stdout.lower()
        assert "host.openshell.internal" in result.stdout

# tests/phase4_agents/test_gemini_sandbox.py

class TestGeminiSandbox:
    def test_sandbox_exists(self, spark_ssh):
        result = spark_ssh.run("openshell sandbox list")
        assert "gemini-dev" in result.stdout

    def test_gemini_binary_exists(self, spark_ssh):
        result = spark_ssh.run(
            "openshell sandbox connect gemini-dev -- which gemini"
        )
        assert "gemini" in result.stdout

    def test_google_api_policy_present(self, spark_ssh):
        result = spark_ssh.run("openshell policy get gemini-dev --full")
        assert "generativelanguage.googleapis.com" in result.stdout

# tests/phase4_agents/test_multi_sandbox.py

class TestMultiSandbox:
    def test_four_sandboxes_running(self, spark_ssh):
        result = spark_ssh.run("openshell sandbox list")
        for name in ["nemoclaw-main", "claude-dev", "codex-dev", "gemini-dev"]:
            assert name in result.stdout, f"Sandbox {name} not found"

    def test_no_port_conflicts(self, spark_ssh):
        """Each sandbox should have unique port forwards (or none)."""
        result = spark_ssh.run("openshell sandbox list --json")
        # Parse and verify no duplicate port forwards
        ...

    def test_filesystem_isolation(self, spark_ssh):
        """File created in one sandbox must not appear in another."""
        spark_ssh.run(
            "openshell sandbox connect claude-dev -- "
            "touch /sandbox/isolation-test-claude"
        )
        result = spark_ssh.run(
            "openshell sandbox connect codex-dev -- "
            "ls /sandbox/isolation-test-claude 2>&1"
        )
        assert "No such file" in result.stdout or result.return_code != 0
```

---

## Phase 5 — Mobile Access & Tailscale Hardening

### Preconditions
- Phase 1 passes (OpenClaw UI on Spark)
- Tailscale active on Spark and mobile device

### Success Criteria
OpenClaw UI is accessible via Tailscale from outside the local network. Gateway binds to Tailscale interface.

### Definition of Done
`pytest tests/phase5_mobile/ -v` — all tests pass.

### Use Cases Covered
| UC | Description |
|----|-------------|
| UC-5.1 | Spark gateway is reachable via Tailscale IP on port 18789 |
| UC-5.2 | Gateway NOT reachable from public internet (no port forwarding) |
| UC-5.3 | Tailscale ACLs allow mobile device to reach Spark |

### Edge Cases (test)
| Edge | Test? | Rationale |
|------|-------|-----------|
| Tailscale disconnected | **Yes** | Gateway should still work on LAN |
| Multiple Tailscale IPs on Spark | **No** | Unlikely — single Tailscale interface |

### Test Signatures

```python
# tests/phase5_mobile/test_tailscale_gateway.py

class TestTailscaleAccess:
    def test_ui_via_tailscale_ip(self, spark_tailscale_ip: str):
        resp = httpx.get(
            f"http://{spark_tailscale_ip}:18789",
            timeout=15,
        )
        assert resp.status_code == 200

    def test_ollama_via_tailscale(self, spark_tailscale_ip: str):
        resp = httpx.get(
            f"http://{spark_tailscale_ip}:11434/api/tags",
            timeout=10,
        )
        assert resp.status_code == 200
```

---

## Shared Test Infrastructure

### `conftest.py` — Fixtures

```python
# tests/conftest.py

import os
import pytest
from dataclasses import dataclass
from fabric import Connection

@dataclass(frozen=True)
class HostConfig:
    hostname: str
    ip: str
    user: str = "carlos"
    ssh_key: str | None = None

@pytest.fixture(scope="session")
def spark_host() -> HostConfig:
    return HostConfig(
        hostname="spark-caeb.local",
        ip=os.environ.get("SPARK_IP", "192.168.1.150"),
    )

@pytest.fixture(scope="session")
def mac_host() -> HostConfig:
    return HostConfig(
        hostname="mac-studio.local",
        ip=os.environ.get("MAC_IP", "192.168.1.100"),
    )

@pytest.fixture(scope="session")
def pi_host() -> HostConfig:
    return HostConfig(
        hostname="raspi.local",
        ip=os.environ.get("PI_IP", "192.168.1.200"),
    )

@pytest.fixture(scope="session")
def spark_ssh(spark_host: HostConfig) -> Connection:
    return Connection(
        host=spark_host.hostname,
        user=spark_host.user,
        connect_kwargs={"key_filename": spark_host.ssh_key} if spark_host.ssh_key else {},
    )

@pytest.fixture
def spark_ip(spark_host: HostConfig) -> str:
    return spark_host.ip

@pytest.fixture
def mac_ip(mac_host: HostConfig) -> str:
    return mac_host.ip

@pytest.fixture
def pi_ip(pi_host: HostConfig) -> str:
    return pi_host.ip

@pytest.fixture
def spark_ollama_url(spark_host: HostConfig) -> str:
    return f"http://{spark_host.ip}:11434"

@pytest.fixture
def spark_tailscale_ip() -> str:
    return os.environ.get("SPARK_TAILSCALE_IP", "100.x.x.x")
```

### `models.py` — Pydantic Models

```python
# tests/models.py

from pydantic import BaseModel, Field

class SparkPrereqs(BaseModel):
    docker_version: str = ""
    docker_running: bool = False
    ollama_version: str = ""
    models_available: list[str] = Field(default_factory=list)
    disk_free_gb: float = 0.0
    node_version: str = ""
    landlock_supported: bool = False
    seccomp_supported: bool = False
    cgroup_v2: bool = False
    tailscale_connected: bool = False

class MacPrereqs(BaseModel):
    ollama_version: str = ""
    ollama_listening: bool = False
    models_available: list[str] = Field(default_factory=list)
    tailscale_connected: bool = False

class PiPrereqs(BaseModel):
    free_ram_mb: float = 0.0
    python3_version: str = ""
    tailscale_connected: bool = False

class InferenceResponse(BaseModel):
    model: str
    choices: list[dict]
    usage: dict | None = None

class SandboxInfo(BaseModel):
    name: str
    status: str
    image: str = ""
    ports: list[int] = Field(default_factory=list)
    keep: bool = False
```

### `pyproject.toml` — uv Project Config

```toml
# tests/pyproject.toml

[project]
name = "nemoclaw-tests"
version = "0.1.0"
description = "TDD test suite for NemoClaw multi-node deployment"
requires-python = ">=3.12"
dependencies = [
    "nemoclaw",
    "pytest>=8.0",
    "pytest-timeout>=2.3",
    "pytest-xdist>=3.5",
    "pytest-rerunfailures>=14.0",
    "pytest-testinfra>=10.0",
    "httpx>=0.27",
    "pydantic>=2.9",
    "pydantic-settings>=2.5",
    "fabric>=3.2",
    "tenacity>=9.0",
    "packaging>=24.0",
    "python-dotenv>=1.0",
    "ruff>=0.8",
    "mypy>=1.13",
    "isort>=5.13",
    "pydocstyle>=6.3",
]

[tool.uv.sources]
nemoclaw = { path = "..", editable = true }

[tool.pytest.ini_options]
testpaths = ["."]
markers = [
    "phase0: Pre-flight checks",
    "phase1: Core NemoClaw on Spark",
    "phase2: Mac Studio integration",
    "phase3: Raspberry Pi infrastructure",
    "phase4: Coding agent sandboxes",
    "phase5: Mobile and Tailscale",
    "phase6: Orchestrator and inter-agent coordination",
]
timeout = 60
```

---

## Execution

### Run All Phases Sequentially

```bash
cd /home/carlos/workspace/nemoclaw/tests

# Initialize project
uv init --no-readme
uv sync

# Phase 0 — must pass before proceeding
uv run pytest phase0_preflight/ -v --tb=short

# Phase 1 — deploy, then test
uv run pytest phase1_core/ -v --tb=short

# Phase 2
uv run pytest phase2_mac/ -v --tb=short

# Phase 3
uv run pytest phase3_pi/ -v --tb=short

# Phase 4
uv run pytest phase4_agents/ -v --tb=short

# Phase 5
uv run pytest phase5_mobile/ -v --tb=short

# Phase 6
uv run pytest phase6_orchestrator/ -v --tb=short

# Run everything
uv run pytest -v --tb=short
```

### Run a Single Phase

```bash
uv run pytest phase1_core/ -v --tb=long -x  # stop on first failure
```

### Run with Environment Variables

```bash
# Create .env file
cat > .env << 'EOF'
SPARK_IP=192.168.1.150
MAC_IP=192.168.1.100
PI_IP=192.168.1.200
SPARK_TAILSCALE_IP=100.x.x.x
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=...
EOF

uv run pytest -v
```
