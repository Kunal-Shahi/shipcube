"""Local profiler sandbox: the production profiler image, run in a local Docker container.

`SHIPCUBE_PROFILER=local` selects it (runners.runner_from_env). It mirrors the Cloud Run
job (gcp_runner.py) so a local estimate exercises the same code path:

- **Same image.** The same deterministic build context and Dockerfile as Cloud Build:
  python:3.12-slim with the app's requirements, the profiler agent, and a non-root
  user (uid 10001). Requirements install at build time, which has network, as Cloud
  Build does. The image tag is a hash of the context, so unchanged code isn't rebuilt.
- **Same isolation, enforced by Docker:**
  - `--network none` (no interfaces but loopback, no DNS)
  - `--read-only` root with a size-capped, nosuid, nodev `/tmp` tmpfs
  - `--user 10001:10001`, `--cap-drop ALL`, `--security-opt no-new-privileges`
  - `--cpus`, `--memory` (swap disabled), `--pids-limit`, and a wall-clock timeout that kills the container
  - no environment from the host except the hints
- **Same measurements.** The agent (`python -m shipcube_profiler.agent`) runs the app
  with LocalRunner inside the container: psutil CPU, RSS, I/O and process count, wall
  time, and network attempts. It prints one `SHIPCUBE_PROFILE {json}` line on stdout.
  Here it is read from `docker run`'s stdout instead of Cloud Logging.

Configuration (environment, all optional):
  SHIPCUBE_PROFILER_DOCKER       docker CLI (default "docker"); DOCKER_HOST picks the daemon
  SHIPCUBE_PROFILER_CPUS         default "1"     (Cloud Run job: 1 vCPU)
  SHIPCUBE_PROFILER_MEMORY_MB    default 2048    (Cloud Run job: 2 GiB)
  SHIPCUBE_PROFILER_PIDS         default 256
  SHIPCUBE_PROFILER_TIMEOUT_S    default 600     (Cloud Run job: 600 s)
  SHIPCUBE_PROFILER_TMPFS_MB     default 512
  SHIPCUBE_PROFILER_DISK_MB      default 512     (agent kills the app past this much written)
  SHIPCUBE_PROFILER_BUILD_TIMEOUT_S  default 300
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from shipcube_classifier import obs

from .abuse import abuse_signals
from .agent import NONCE_ENV, parse_result
from .detect import CodeSummary
from .errors import ProfilerError
from .gcp_runner import SANDBOX_UID, _package
from .policy import BUILD_TIMEOUT_S, check_image_size
from .profile import Hints, Profile

log = logging.getLogger("shipcube.profiler")

IMAGE_REPO = "shipcube-profiler-app"
LABEL = "dev.shipcube.role=profiler"


@dataclass(frozen=True)
class DockerSandboxConfig:
    docker: str = "docker"
    cpus: str = "1"
    memory_mb: int = 2048
    pids: int = 256
    timeout_s: int = 600
    tmpfs_mb: int = 512
    disk_mb: int = 512
    build_timeout_s: int = BUILD_TIMEOUT_S

    @classmethod
    def from_env(cls, env: dict | None = None) -> "DockerSandboxConfig":
        e = os.environ if env is None else env
        d = cls()
        try:
            return cls(docker=e.get("SHIPCUBE_PROFILER_DOCKER", d.docker),
                       cpus=e.get("SHIPCUBE_PROFILER_CPUS", d.cpus),
                       memory_mb=int(e.get("SHIPCUBE_PROFILER_MEMORY_MB", d.memory_mb)),
                       pids=int(e.get("SHIPCUBE_PROFILER_PIDS", d.pids)),
                       timeout_s=int(e.get("SHIPCUBE_PROFILER_TIMEOUT_S", d.timeout_s)),
                       tmpfs_mb=int(e.get("SHIPCUBE_PROFILER_TMPFS_MB", d.tmpfs_mb)),
                       disk_mb=int(e.get("SHIPCUBE_PROFILER_DISK_MB", d.disk_mb)),
                       build_timeout_s=int(e.get("SHIPCUBE_PROFILER_BUILD_TIMEOUT_S", d.build_timeout_s)))
        except ValueError as err:
            raise ProfilerError("config", f"bad SHIPCUBE_PROFILER_* value: {err}") from err


def run_args(cfg: DockerSandboxConfig, image: str, name: str, hints: Hints, nonce: str = "") -> list[str]:
    """The `docker run` command line: the sandbox's isolation lives here."""
    h = {k: v for k, v in hints.__dict__.items() if v is not None and k != "sample_input"}
    # The agent keeps the app under the container's limits, so it can measure and
    # report instead of being OOM-killed or cut off mid-run.
    agent_timeout = max(10, cfg.timeout_s - 30)
    agent_memory = int(cfg.memory_mb * 0.9)
    return [
        cfg.docker, "run", "--rm", "--name", name, "--label", LABEL,
        "--network", "none",
        "--read-only", "--tmpfs", f"/tmp:rw,nosuid,nodev,size={cfg.tmpfs_mb}m",
        "--user", f"{SANDBOX_UID}:{SANDBOX_UID}",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--cpus", cfg.cpus, "--memory", f"{cfg.memory_mb}m", "--memory-swap", f"{cfg.memory_mb}m",
        "--pids-limit", str(cfg.pids), "--ulimit", "nofile=1024:1024",
        "--env", f"SHIPCUBE_HINTS={json.dumps(h)}",
        "--env", "SHIPCUBE_RUNNER_NAME=docker",
        "--env", f"SHIPCUBE_AGENT_TIMEOUT_S={agent_timeout}",
        "--env", f"SHIPCUBE_AGENT_MEMORY_MB={agent_memory}",
        "--env", f"SHIPCUBE_AGENT_NPROC={cfg.pids}",
        "--env", f"SHIPCUBE_AGENT_DISK_MB={cfg.disk_mb}",
        "--env", f"{NONCE_ENV}={nonce}",
        image,
    ]


class DockerRunner:
    name = "docker"

    def __init__(self, config: DockerSandboxConfig | None = None):
        self.config = config or DockerSandboxConfig.from_env()

    def _docker(self, *args: str, timeout: float, stdin: bytes | None = None) -> subprocess.CompletedProcess:
        try:
            return subprocess.run([self.config.docker, *args], input=stdin, capture_output=True, timeout=timeout)
        except FileNotFoundError as e:
            raise ProfilerError("config", f"docker CLI not found ({self.config.docker}); install Docker or set "
                                          "SHIPCUBE_PROFILER_DOCKER") from e

    def available(self) -> bool:
        try:
            return self._docker("version", "--format", "{{.Server.Version}}", timeout=15).returncode == 0
        except (ProfilerError, subprocess.TimeoutExpired):
            return False

    def build(self, app_dir: Path) -> str:
        """Build (or reuse) the sandbox image for this code; returns its tag."""
        context = _package(app_dir, compress=False)
        image = f"{IMAGE_REPO}:{hashlib.sha256(context).hexdigest()[:16]}"
        if self._docker("image", "inspect", image, timeout=30).returncode == 0:
            obs.event(log, "profile.docker_image", image=image, cached=True)
        else:
            try:
                r = self._docker("build", "--quiet", "--label", LABEL, "-t", image, "-",
                                 stdin=context, timeout=self.config.build_timeout_s)
            except subprocess.TimeoutExpired as e:
                raise ProfilerError("build", f"docker build timed out after {self.config.build_timeout_s}s",
                                    reason="build_failed") from e
            if r.returncode != 0:
                raise ProfilerError("build", _tail(r.stderr), reason="build_failed")
            obs.event(log, "profile.docker_image", image=image, cached=False)
        size = self._docker("image", "inspect", "-f", "{{.Size}}", image, timeout=30).stdout.strip()
        try:
            check_image_size(int(size or 0))
        except ProfilerError:
            self._docker("image", "rm", "-f", image, timeout=60)
            raise
        return image
        try:
            r = self._docker("build", "--quiet", "--label", LABEL, "-t", image, "-",
                             stdin=context, timeout=self.config.build_timeout_s)
        except subprocess.TimeoutExpired as e:
            raise ProfilerError("build", f"docker build timed out after {self.config.build_timeout_s}s",
                                reason="build_failed") from e
        if r.returncode != 0:
            raise ProfilerError("build", _tail(r.stderr), reason="build_failed")
        size = self._docker("image", "inspect", "-f", "{{.Size}}", image, timeout=30).stdout.strip()
        try:
            check_image_size(int(size or 0))
        except ProfilerError:
            self._docker("image", "rm", "-f", image, timeout=60)
            raise
        obs.event(log, "profile.docker_image", image=image, cached=False, size=int(size or 0))
        return image

    def run(self, app_dir: Path, summary: CodeSummary, hints: Hints) -> Profile:
        run_id = obs.context().get("estimate_id", f"r{uuid.uuid4().hex[:12]}")
        name = "prof-" + re.sub(r"[^a-z0-9-]", "-", run_id.lower())[:40].strip("-")
        nonce = secrets.token_hex(16)
        with obs.timed(log, "profile.docker", run_id=run_id) as extra:
            image = self.build(app_dir)
            args = run_args(self.config, image, name, hints, nonce)
            try:
                r = subprocess.run(args, capture_output=True, timeout=self.config.timeout_s + 30)
            except subprocess.TimeoutExpired as e:
                self._docker("kill", name, timeout=30)
                raise ProfilerError("run", f"sandbox exceeded {self.config.timeout_s}s; container killed") from e
            finally:
                # --rm removes it on exit; this covers a killed or orphaned container.
                self._docker("rm", "-f", name, timeout=30)
            extra.update(image=image, exit_code=r.returncode)
        result = parse_result(r.stdout.decode(errors="replace").splitlines(), nonce)
        if result is None:
            why = "out of memory (container killed)" if r.returncode == 137 else _tail(r.stderr)
            raise ProfilerError("run", f"no result from the sandbox (exit {r.returncode}): {why}")
        profile = Profile.from_dict(result["profile"])
        profile.runner = self.name
        profile.abuse_signals = abuse_signals(profile)
        return profile


def _tail(b: bytes, n: int = 400) -> str:
    return b.decode(errors="replace").strip()[-n:]


def prune_images(config: DockerSandboxConfig | None = None, keep_hours: int = 24) -> None:
    """Remove profiler images older than keep_hours (local equivalent of AR cleanup)."""
    cfg = config or DockerSandboxConfig.from_env()
    if shutil.which(cfg.docker):
        subprocess.run([cfg.docker, "image", "prune", "-af", "--filter", f"label={LABEL}",
                        "--filter", f"until={keep_hours}h"], capture_output=True, timeout=120)
