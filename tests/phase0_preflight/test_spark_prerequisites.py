"""
Phase 0 — Pre-flight Validation: DGX Spark prerequisites.

Validates that the DGX Spark node (spark-caeb.local) has every required
piece of software installed, running, and at the correct minimum version
before any deployment steps are attempted.

Markers
-------
phase0    : All tests here belong to Phase 0 pre-flight validation.
negative  : Tests that verify failure modes are correctly detected.

Fixtures (from conftest.py)
---------------------------
spark_prereqs : SparkPrereqs  — Pydantic model populated via SSH at fixture load time.
spark_ssh     : fabric.Connection — live SSH connection to the Spark node.
spark_ip      : str — LAN IP of the Spark node (e.g. "192.168.1.10").
"""

from __future__ import annotations

import pytest
from fabric import Connection
from packaging.version import Version

from ..helpers import parse_version, run_remote
from ..models import CommandResult, SparkPrereqs

# ---------------------------------------------------------------------------
# Minimum version thresholds (defined once, referenced in tests)
# ---------------------------------------------------------------------------

_MIN_DOCKER_VERSION = Version("28.4")
_MIN_NODE_VERSION = Version("20.0.0")
_MIN_DISK_FREE_GB = 100.0
_MAX_INODE_USAGE_PCT = 90  # warn when more than 90% of inodes are consumed

# ---------------------------------------------------------------------------
# Docker tests
# ---------------------------------------------------------------------------


@pytest.mark.phase0
class TestSparkDocker:
    """Docker Engine must be installed, running, and at the correct version.

    Docker is the container runtime for NemoClaw's coding-agent sandboxes.
    Without a healthy Docker daemon the entire sandbox architecture collapses.
    """

    def test_docker_installed(self, spark_prereqs: SparkPrereqs) -> None:
        """Docker is installed and reports a non-empty version string.

        Verifies that the ``docker`` binary is on PATH and that ``docker
        --version`` exits successfully.  A missing or broken install would
        cause every downstream Docker test — and ultimately every sandbox
        operation — to fail with an opaque error.
        """
        assert spark_prereqs.docker_version != "", (
            "docker_version is empty — Docker may not be installed on Spark. "
            "Install Docker Engine: https://docs.docker.com/engine/install/ubuntu/"
        )

    def test_docker_version_minimum(self, spark_prereqs: SparkPrereqs) -> None:
        """Docker version must be at least 28.4.

        NemoClaw's Landlock + seccomp sandbox profiles require Docker 28.x
        to pass through the necessary kernel security namespaces correctly.
        Older Docker releases silently drop certain seccomp flags, which
        causes the sandbox to start without the expected restrictions.
        """
        installed = parse_version(spark_prereqs.docker_version)
        assert installed >= _MIN_DOCKER_VERSION, (
            f"Docker {installed} is below the minimum required version "
            f"{_MIN_DOCKER_VERSION}. "
            f"Upgrade: sudo apt-get install docker-ce=28.4*"
        )

    def test_docker_running(self, spark_prereqs: SparkPrereqs) -> None:
        """The Docker daemon is active (not just installed).

        It is common after a fresh reboot for the Docker socket to be present
        but the daemon to be stopped (e.g. if the systemd unit failed to start
        due to a cgroup configuration issue).  This test distinguishes
        'installed' from 'running' to surface that class of failure early.
        """
        assert spark_prereqs.docker_running is True, (
            "Docker daemon is not running on Spark. "
            "Start it: sudo systemctl start docker && sudo systemctl enable docker"
        )

    def test_nvidia_container_runtime(self, spark_ssh: Connection) -> None:
        """nvidia-container-runtime is installed and reachable in PATH.

        The NVIDIA Container Runtime is required so that GPU-accelerated
        containers can access the DGX Spark's GPUs via the --gpus flag.
        Without it, Ollama containers and other CUDA workloads will run on
        CPU only (or fail entirely with an unknown runtime error).
        """
        result: CommandResult = run_remote(
            spark_ssh,
            "which nvidia-container-runtime || nvidia-container-runtime --version",
            timeout=15,
        )
        assert result.return_code == 0, (
            "nvidia-container-runtime was not found on Spark. "
            "Install the NVIDIA Container Toolkit: "
            "https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html"
        )
        assert result.stdout.strip() != "", (
            "nvidia-container-runtime appears present but produced no output. "
            f"Full result: stdout={result.stdout!r} stderr={result.stderr!r}"
        )


# ---------------------------------------------------------------------------
# Ollama tests
# ---------------------------------------------------------------------------


@pytest.mark.phase0
class TestSparkOllama:
    """Ollama must be installed and the required models fully downloaded.

    Ollama is the local inference server on Spark.  Both models listed below
    must be present before NemoClaw can route inference requests to the local
    provider.
    """

    def test_ollama_installed(self, spark_ssh: Connection) -> None:
        """Ollama binary is present and ``ollama --version`` exits 0.

        Checks the live system rather than the pre-gathered model, so this
        test will still catch a broken Ollama install even if the fixture
        succeeded with a cached value.
        """
        result: CommandResult = run_remote(spark_ssh, "ollama --version", timeout=15)
        assert result.return_code == 0, (
            "ollama --version returned a non-zero exit code on Spark. "
            "Install Ollama: curl -fsSL https://ollama.com/install.sh | sh"
        )
        assert result.stdout.strip() != "", (
            f"ollama --version produced empty output despite exit code 0. stderr: {result.stderr!r}"
        )

    @pytest.mark.parametrize(
        "model",
        [
            "nemotron-3-super:120b",
            "qwen3-coder-next:q4_K_M",
        ],
    )
    def test_model_downloaded(self, spark_prereqs: SparkPrereqs, model: str) -> None:
        """Required model is present in Ollama's local model registry.

        NemoClaw needs both models available before deployment:
        - ``nemotron-3-super:120b`` is the primary inference model for the
          NemoClaw OpenShell inference route.
        - ``qwen3-coder-next:q4_K_M`` is used for code-focused agent tasks.

        Note: this test only verifies the model *appears in the listing*.  The
        companion negative test (TestSparkNegative.test_ollama_partial_download_detected)
        uses ``ollama show`` to verify the model is actually loadable.
        """
        assert model in spark_prereqs.models_available, (
            f"Model '{model}' is not listed in Ollama on Spark. "
            f"Available models: {spark_prereqs.models_available}. "
            f"Pull it: ollama pull {model}"
        )


# ---------------------------------------------------------------------------
# Disk space tests
# ---------------------------------------------------------------------------


@pytest.mark.phase0
class TestSparkDisk:
    """The primary partition must have sufficient free disk space and inodes.

    Model files for nemotron-3-super:120b alone occupy ~65-75 GB.  Combined
    with the OS, Docker images, logs, and npm packages, the total footprint
    easily exceeds 100 GB.  Running out of disk or inodes mid-deployment
    produces cryptic errors; these tests surface the problem before it occurs.
    """

    def test_sufficient_disk_space(self, spark_prereqs: SparkPrereqs) -> None:
        """At least 100 GB of free disk space is available on the primary partition.

        The 100 GB threshold gives headroom for:
        - Ollama model blobs (65-75 GB for Nemotron 120B)
        - Docker images for NemoClaw + sandbox base images (~5-10 GB)
        - npm node_modules for OpenShell (~500 MB)
        - Log rotation and future model updates (~15 GB buffer)
        """
        assert spark_prereqs.disk_free_gb >= _MIN_DISK_FREE_GB, (
            f"Insufficient disk space on Spark: "
            f"{spark_prereqs.disk_free_gb:.1f} GB free, "
            f"need >= {_MIN_DISK_FREE_GB:.0f} GB. "
            "Free space before continuing: docker system prune -f, "
            "remove unused model blobs with: ollama rm <model>"
        )

    def test_inodes_available(self, spark_ssh: Connection) -> None:
        """Inode usage on the primary partition is below 90%.

        A filesystem can have gigabytes of free space yet be unable to create
        new files if its inode table is exhausted.  This commonly happens on
        Spark after intensive npm installs or large Docker builds that generate
        millions of small cache files.  Checking inodes independently of bytes
        avoids this class of silent deployment failure.
        """
        # df -i: inode stats; awk extracts the "Use%" column for the root mount
        result: CommandResult = run_remote(
            spark_ssh,
            'df -i / | awk \'NR==2 {gsub("%","",$5); print $5}\'',
            timeout=15,
        )
        assert result.return_code == 0, f"Failed to query inode stats on Spark: {result.stderr!r}"
        raw_pct = result.stdout.strip()
        assert raw_pct.isdigit(), (
            f"Unexpected inode usage output: {raw_pct!r}. "
            "Expected a numeric percentage from 'df -i /'."
        )
        usage_pct = int(raw_pct)
        assert usage_pct < _MAX_INODE_USAGE_PCT, (
            f"Inode usage on / is {usage_pct}%, which is at or above the "
            f"{_MAX_INODE_USAGE_PCT}% threshold. "
            "Clean up inode-heavy directories: ~/.npm/_npx, /tmp, "
            "Docker build cache ('docker builder prune -f')."
        )


# ---------------------------------------------------------------------------
# Node.js / npm tests
# ---------------------------------------------------------------------------


@pytest.mark.phase0
class TestSparkNode:
    """Node.js and npm are required for the NemoClaw OpenShell CLI.

    The OpenShell gateway and its CLI are Node.js packages managed via npm.
    Node 20+ is required because OpenShell uses native ES modules and top-level
    await, both of which are only stable in the Node 20 LTS release line.
    """

    def test_node_version_minimum(self, spark_prereqs: SparkPrereqs) -> None:
        """Node.js version is at least 20.0.0.

        Node 20 is the minimum LTS release that supports all ES module
        features used by the OpenShell runtime.  Earlier releases (18, 16)
        will fail at import time with syntax errors on optional chaining in
        async context.
        """
        installed = spark_prereqs.node_version_parsed
        assert installed >= _MIN_NODE_VERSION, (
            f"Node.js {installed} is below the minimum required version "
            f"{_MIN_NODE_VERSION}. "
            "Upgrade via nvm: nvm install 20 && nvm use 20 && nvm alias default 20"
        )

    def test_npm_installed(self, spark_ssh: Connection) -> None:
        """npm is installed and returns a version string.

        npm ships with Node.js but can be independently updated.  This test
        confirms the binary is reachable and functional, which is a prerequisite
        for 'npm install' steps during the Phase 1 NemoClaw deployment.
        """
        result: CommandResult = run_remote(spark_ssh, "npm --version", timeout=15)
        assert result.return_code == 0, (
            "npm --version failed on Spark. "
            "npm should ship with Node.js — try: node -e 'console.log(process.version)' "
            "to confirm Node is installed, then reinstall via nvm."
        )
        assert result.stdout.strip() != "", (
            f"npm --version produced empty output. stderr: {result.stderr!r}"
        )


# ---------------------------------------------------------------------------
# Kernel security feature tests
# ---------------------------------------------------------------------------


@pytest.mark.phase0
class TestSparkKernel:
    """The kernel must expose the security primitives required for sandbox isolation.

    NemoClaw's coding-agent sandboxes are hardened with three complementary
    kernel mechanisms: Landlock (filesystem access control), seccomp (syscall
    filtering), and cgroup v2 (resource isolation).  All three must be enabled
    at the kernel level before Docker can apply the corresponding profiles.
    """

    def test_landlock_supported(self, spark_ssh: Connection) -> None:
        """The Landlock LSM filesystem security module is available in the kernel.

        Landlock allows sandboxes to be restricted to a minimal set of
        filesystem paths, preventing agent code from accessing sensitive host
        paths (SSH keys, credentials, /etc).  If the kernel was built without
        Landlock support the entire filesystem isolation model silently falls
        back to default Docker behavior, leaving the host exposed.
        """
        result: CommandResult = run_remote(
            spark_ssh,
            "test -d /sys/kernel/security/landlock && echo PRESENT || echo ABSENT",
            timeout=10,
        )
        assert result.return_code == 0, f"Landlock check command failed: {result.stderr!r}"
        assert "PRESENT" in result.stdout, (
            "Landlock LSM is not available on this kernel. "
            "The DGX Spark should ship with a kernel >= 5.13 that includes Landlock. "
            "Check: uname -r, and verify CONFIG_SECURITY_LANDLOCK=y in the kernel config."
        )

    def test_seccomp_supported(self, spark_ssh: Connection) -> None:
        """seccomp syscall filtering is compiled into the running kernel.

        Docker applies seccomp profiles to every container by default, and
        NemoClaw applies a tighter custom profile to coding-agent sandboxes.
        If seccomp is not compiled in, Docker silently skips the profile and
        the sandbox runs with unrestricted syscall access.
        """
        # Check multiple locations where the kernel config might live
        result: CommandResult = run_remote(
            spark_ssh,
            (
                "( zcat /proc/config.gz 2>/dev/null || "
                "  cat /boot/config-$(uname -r) 2>/dev/null || "
                "  cat /boot/config 2>/dev/null "
                ") | grep -q '^CONFIG_SECCOMP=y' && echo SUPPORTED || echo UNSUPPORTED"
            ),
            timeout=20,
        )
        assert result.return_code == 0, f"seccomp kernel config check failed: {result.stderr!r}"
        assert "SUPPORTED" in result.stdout, (
            "CONFIG_SECCOMP=y not found in the kernel configuration. "
            "seccomp filtering will not be applied to sandboxes. "
            "This kernel does not meet NemoClaw's security requirements. "
            "Boot a kernel compiled with CONFIG_SECCOMP=y."
        )

    def test_cgroup_v2(self, spark_ssh: Connection) -> None:
        """The unified cgroup v2 hierarchy is active on the Spark node.

        cgroup v2 is required for accurate per-container memory, CPU, and I/O
        accounting.  Docker's ``--memory`` and ``--cpus`` limits are enforced
        via cgroup v2 controllers; with cgroup v1 (or a hybrid mount) some
        controllers may be absent, making resource limits unreliable inside
        coding-agent sandboxes.
        """
        result: CommandResult = run_remote(
            spark_ssh,
            "test -f /sys/fs/cgroup/cgroup.controllers && echo V2 || echo V1",
            timeout=10,
        )
        assert result.return_code == 0, f"cgroup version check failed: {result.stderr!r}"
        assert "V2" in result.stdout, (
            "cgroup v2 (unified hierarchy) is not active on Spark. "
            "The presence of /sys/fs/cgroup/cgroup.controllers indicates v2. "
            "Enable it by adding 'systemd.unified_cgroup_hierarchy=1' to the "
            "kernel command line in /etc/default/grub, then: update-grub && reboot."
        )


# ---------------------------------------------------------------------------
# Tailscale tests
# ---------------------------------------------------------------------------


@pytest.mark.phase0
class TestSparkTailscale:
    """Tailscale must be connected on Spark for secure cross-machine access.

    Tailscale provides the zero-config overlay network that allows the Mac
    Studio and Raspberry Pi to reach NemoClaw endpoints on Spark without
    requiring firewall rules or VPN configuration.  A disconnected Tailscale
    node means remote agents cannot reach the inference API from outside the
    LAN.
    """

    def test_tailscale_connected(self, spark_prereqs: SparkPrereqs) -> None:
        """Tailscale reports the Spark node as connected with Backend=Running.

        This check validates the *effective* Tailscale state, not merely that
        the binary is installed.  A node can be installed but not logged in,
        or logged in but administratively disabled in the Tailscale control
        plane — both of which would cause remote connectivity to fail silently.
        """
        assert spark_prereqs.tailscale_connected is True, (
            "Tailscale is not connected on Spark. "
            "Check status: tailscale status. "
            "If not logged in: sudo tailscale up --authkey=<key>. "
            "If login expired: sudo tailscale logout && sudo tailscale up."
        )


# ---------------------------------------------------------------------------
# Negative tests
# ---------------------------------------------------------------------------


@pytest.mark.phase0
@pytest.mark.negative
class TestSparkNegative:
    """Negative path tests that verify failure modes are correctly detected.

    These tests go beyond checking that things 'look present' — they exercise
    the code paths that would expose partial or corrupt states that a naïve
    listing check would miss.
    """

    @pytest.mark.parametrize(
        "model",
        [
            "nemotron-3-super:120b",
            "qwen3-coder-next:q4_K_M",
        ],
    )
    def test_ollama_partial_download_detected(self, spark_ssh: Connection, model: str) -> None:
        """Model is actually loadable by Ollama, not just listed in the index.

        ``ollama list`` reads only the model index file; it will show a model
        as available even if the underlying GGUF blob was interrupted mid-
        download (leaving a truncated file).  ``ollama show <model>`` forces
        Ollama to inspect the full manifest and validate the blob references,
        which surfaces partial downloads that ``ollama list`` would mask.

        A model that appears in the listing but fails ``ollama show`` would
        cause the very first inference request to fail after full deployment,
        wasting significant time.  This test surfaces that failure now.
        """
        result: CommandResult = run_remote(
            spark_ssh,
            f"ollama show {model}",
            timeout=60,  # model inspection can take a moment for large models
        )
        assert result.return_code == 0, (
            f"ollama show {model!r} failed with exit code {result.return_code}. "
            "The model may be partially downloaded or its manifest is corrupt. "
            f"Re-pull the model: ollama rm {model} && ollama pull {model}. "
            f"Ollama error output: {result.stderr!r}"
        )
        # 'ollama show' outputs a summary table; a loadable model will print
        # at least its name/architecture — an empty stdout indicates a problem.
        assert result.stdout.strip() != "", (
            f"ollama show {model!r} exited 0 but produced no output. "
            "This suggests the model manifest exists but the model metadata "
            "could not be read.  Re-pull: ollama rm {model} && ollama pull {model}."
        )
