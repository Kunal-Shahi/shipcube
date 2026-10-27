"""Runners execute an app and return a Profile.

- LocalRunner: subprocess + psutil sampling + rlimits. For development and tests.
  It is NOT a security boundary (no container, no network namespace).
- CloudRunJobRunner: the production design, a Cloud Run job (gen2 sandbox, no egress).
  Stubbed: job_spec() is real, run() raises until the deployer can launch jobs.
- StaticRunner: no execution; typical numbers from the code summary and hints.
"""

from __future__ import annotations

import json
import os
import re
import resource
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import psutil

from .detect import CodeSummary
from .profile import Hints, Profile, RequestStats

SANDBOX_DIR = Path(__file__).parent / "_sandbox"
GIB = 2**30


class Runner(Protocol):
    name: str

    def run(self, app_dir: Path, summary: CodeSummary, hints: Hints) -> Profile: ...


@dataclass
class Limits:
    timeout_s: float = 60.0         # wall clock for a script; startup + load for a server
    memory_mb: int = 2048           # RSS of the whole process tree
    max_processes: int = 32
    max_file_mb: int = 512
    sample_interval_s: float = 0.1


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _preexec(limits: Limits):
    def fn():
        os.setsid()
        cpu = int(limits.timeout_s * 4) + 5
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        fsize = limits.max_file_mb * 2**20
        resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))
        if sys.platform.startswith("linux"):  # macOS doesn't enforce RLIMIT_AS
            mem = limits.memory_mb * 2**20 * 2
            resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
    return fn


class _Sampler(threading.Thread):
    """Samples CPU, RSS, I/O and process count of a process tree."""

    def __init__(self, pid: int, limits: Limits):
        super().__init__(daemon=True)
        self.root = psutil.Process(pid)
        self.limits = limits
        self.procs: dict[int, psutil.Process] = {}
        self.cpu: list[float] = []
        self.rss: list[float] = []
        self.nprocs = 1
        self.io = {}  # pid -> (read, write)
        self.io_supported = hasattr(psutil.Process, "io_counters")
        self.kill_reason: str | None = None
        self.window: tuple[int, int] | None = None   # sample indices of the active window
        self._halt = threading.Event()

    def mark(self) -> int:
        return len(self.cpu)

    def _tree(self) -> list[psutil.Process]:
        try:
            kids = self.root.children(recursive=True)
        except psutil.Error:
            return []
        out = []
        for p in [self.root, *kids]:
            if p.pid not in self.procs:
                self.procs[p.pid] = p
                try:
                    p.cpu_percent(None)  # prime
                except psutil.Error:
                    continue
            out.append(self.procs[p.pid])
        return out

    def run(self):
        while not self._halt.is_set():
            tree = self._tree()
            if not tree:
                break
            cpu = rss = 0.0
            for p in tree:
                try:
                    with p.oneshot():
                        cpu += p.cpu_percent(None) / 100.0
                        rss += p.memory_info().rss
                        if self.io_supported:
                            c = p.io_counters()
                            self.io[p.pid] = (c.read_bytes, c.write_bytes)
                except (psutil.Error, AttributeError):
                    continue
            self.cpu.append(cpu)
            self.rss.append(rss)
            self.nprocs = max(self.nprocs, len(tree))
            if rss > self.limits.memory_mb * 2**20:
                self.kill_reason = f"memory over {self.limits.memory_mb} MB"
            elif len(tree) > self.limits.max_processes:
                self.kill_reason = f"more than {self.limits.max_processes} processes"
            if self.kill_reason:
                _killpg(self.root.pid)
                break
            time.sleep(self.limits.sample_interval_s)

    def stop(self):
        self._halt.set()


def _killpg(pid: int, sig=signal.SIGKILL):
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _sandbox_env(app_dir: Path, home: Path, stats_file: Path, hints: Hints, port: int | None) -> dict:
    """A minimal environment: nothing from the parent (no API keys, no cloud creds)."""
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        "HOME": str(home),
        "TMPDIR": str(home),
        "LANG": "C.UTF-8",
        "PYTHONPATH": f"{SANDBOX_DIR}{os.pathsep}{app_dir}",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "SHIPCUBE_PROFILE": "1",
        "SHIPCUBE_NO_EGRESS": "1",
        "SHIPCUBE_STATS_FILE": str(stats_file),
    }
    if port:
        env["PORT"] = str(port)
    if hints.sample_input:
        env["SHIPCUBE_SAMPLE_DIR"] = str(Path(hints.sample_input).resolve())
    return env


def _command(python: str, app_dir: Path, summary: CodeSummary, port: int | None) -> list[str]:
    entry = summary.entrypoint or "main.py"
    if summary.kind != "web":
        return [python, entry]
    text = (app_dir / entry).read_text(errors="replace")
    has_main = "__main__" in text
    module = entry[:-3].replace("/", ".")
    if summary.framework == "fastapi" and not has_main:
        m = re.search(r"^(\w+)\s*=\s*FastAPI\(", text, re.M)
        return [python, "-m", "uvicorn", f"{module}:{m.group(1) if m else 'app'}",
                "--host", "127.0.0.1", "--port", str(port)]
    if summary.framework == "flask" and not has_main:
        return [python, "-m", "flask", "--app", module, "run", "--host", "127.0.0.1", "--port", str(port)]
    return [python, entry]


def _wait_port(port: int, proc: subprocess.Popen, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if proc.poll() is not None:
            return False
        with socket.socket() as s:
            s.settimeout(0.2)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.1)
    return False


def _load(port: int, routes: list[str], bursts: int, per_burst: int, concurrency: int,
          gap_s: float) -> RequestStats:
    """Bursty synthetic traffic: `bursts` bursts of `per_burst` GETs with idle gaps."""
    paths = [r for r in routes if "<" not in r and "{" not in r] or ["/"]
    lat: list[float] = []
    ok = 0

    def hit(i: int):
        t = time.perf_counter()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{paths[i % len(paths)]}", timeout=10) as r:
                r.read()
                good = r.status < 500
        except Exception:
            good = False
        return time.perf_counter() - t, good

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    urllib.request.install_opener(opener)
    with ThreadPoolExecutor(concurrency) as pool:
        for b in range(bursts):
            for dt, good in pool.map(hit, range(per_burst)):
                lat.append(dt)
                ok += good
            if b < bursts - 1:
                time.sleep(gap_s)
    lat.sort()
    return RequestStats(sent=len(lat), ok=ok, latency_mean_s=statistics.fmean(lat) if lat else 0.0,
                        latency_p95_s=lat[int(0.95 * (len(lat) - 1))] if lat else 0.0,
                        concurrency=concurrency)


class LocalRunner:
    """Run the app in a subprocess on this machine and sample it with psutil."""

    name = "local"

    def __init__(self, limits: Limits | None = None, python: str | None = None,
                 bursts: int = 3, per_burst: int = 40, concurrency: int = 8, gap_s: float = 1.5):
        self.limits = limits or Limits()
        # The app's interpreter. Default: this one (its packages must cover the app's
        # imports). Production builds the app image first, so deps are always present.
        self.python = python or sys.executable
        self.load = dict(bursts=bursts, per_burst=per_burst, concurrency=concurrency, gap_s=gap_s)

    def run(self, app_dir: Path, summary: CodeSummary, hints: Hints) -> Profile:
        work = Path(tempfile.mkdtemp(prefix="shipcube-prof-"))
        try:
            return self._run(app_dir, summary, hints, work)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _run(self, app_dir: Path, summary: CodeSummary, hints: Hints, work: Path) -> Profile:
        port = _free_port() if summary.kind == "web" else None
        stats_file = work / "netstats"
        env = _sandbox_env(app_dir, work, stats_file, hints, port)
        cmd = _command(self.python, app_dir, summary, port)
        ru0 = resource.getrusage(resource.RUSAGE_CHILDREN)
        t0 = time.monotonic()
        proc = subprocess.Popen(cmd, cwd=app_dir, env=env, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, preexec_fn=_preexec(self.limits))
        sampler = _Sampler(proc.pid, self.limits)
        sampler.start()
        prof = Profile(mode="measured", runner=self.name, kind=summary.kind)
        win_start = 0
        try:
            if summary.kind == "web":
                if not _wait_port(port, proc, min(20.0, self.limits.timeout_s)):
                    prof.notes.append("server did not open its port; profile covers startup only")
                else:
                    win_start = sampler.mark()
                    ta = time.monotonic()
                    prof.requests = _load(port, summary.routes, **self.load)
                    prof.active_s = time.monotonic() - ta
                _killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    _killpg(proc.pid)
            else:
                try:
                    proc.wait(timeout=self.limits.timeout_s)
                except subprocess.TimeoutExpired:
                    prof.timed_out = True
                    _killpg(proc.pid)
            proc.wait()
        finally:
            sampler.stop()
            sampler.join(timeout=2)
            stderr = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        wall = time.monotonic() - t0
        ru1 = resource.getrusage(resource.RUSAGE_CHILDREN)

        prof.wall_s = wall
        if summary.kind != "web":
            prof.active_s = wall
        prof.exit_code = proc.returncode
        prof.killed_reason = sampler.kill_reason
        prof.processes_max = sampler.nprocs
        cpu = sampler.cpu[win_start:] or sampler.cpu
        rss = sampler.rss[win_start:] or sampler.rss
        prof.samples = len(sampler.cpu)
        prof.cpu_seconds = (ru1.ru_utime - ru0.ru_utime) + (ru1.ru_stime - ru0.ru_stime)
        if cpu:
            prof.cpu_mean_cores = statistics.fmean(cpu)
            prof.cpu_p95_cores = sorted(cpu)[int(0.95 * (len(cpu) - 1))]
            prof.cpu_peak_cores = max(cpu)
            prof.cpu_cv = statistics.pstdev(cpu) / prof.cpu_mean_cores if prof.cpu_mean_cores > 0 else 0.0
        if summary.kind != "web" and wall > 0:
            # rusage is exact for CPU time; sampling misses very short runs.
            prof.cpu_mean_cores = max(prof.cpu_mean_cores, prof.cpu_seconds / wall)
            prof.cpu_p95_cores = max(prof.cpu_p95_cores, prof.cpu_mean_cores)
            prof.cpu_peak_cores = max(prof.cpu_peak_cores, prof.cpu_p95_cores)
        if rss:
            prof.mem_mean_gb = statistics.fmean(rss) / GIB
            prof.mem_peak_gb = max(rss) / GIB
        else:  # too short to sample: ru_maxrss is bytes on macOS, KiB on Linux
            scale = 1 if sys.platform == "darwin" else 1024
            prof.mem_peak_gb = prof.mem_mean_gb = ru1.ru_maxrss * scale / GIB
        if sampler.io_supported and sampler.io:
            prof.io_read_mb = sum(r for r, _ in sampler.io.values()) / 2**20
            prof.io_write_mb = sum(w for _, w in sampler.io.values()) / 2**20
            prof.io_source = "psutil"
        else:
            blocks = (ru1.ru_inblock - ru0.ru_inblock) + (ru1.ru_oublock - ru0.ru_oublock)
            prof.io_write_mb = (ru1.ru_oublock - ru0.ru_oublock) * 4096 / 2**20
            prof.io_read_mb = (ru1.ru_inblock - ru0.ru_inblock) * 4096 / 2**20
            prof.io_source = "rusage"
            if blocks == 0:
                prof.notes.append("disk I/O from rusage block counts (approximate on macOS)")
        for f in work.glob("netstats.*"):
            try:
                s = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            prof.net_calls += s["connect_attempts"]
            prof.net_blocked += s["blocked"]
            prof.net_bytes += s["bytes_sent"] + s["bytes_recv"]
            prof.remote_hosts = sorted(set(prof.remote_hosts) | set(s["remote_hosts"]))[:20]
        if prof.net_blocked:
            prof.notes.append(f"{prof.net_blocked} outbound connection(s) blocked (no egress while profiling)")
        if prof.timed_out:
            prof.notes.append(f"stopped after {self.limits.timeout_s:.0f}s; duration is a lower bound")
        if proc.returncode not in (0, None) and summary.kind != "web" and not prof.timed_out:
            tail = stderr.strip().splitlines()[-1:] if stderr.strip() else []
            prof.notes.append(f"exited with code {proc.returncode}" + (f": {tail[0][:200]}" if tail else ""))
        return prof


class CloudRunJobRunner:
    """Production profiler: one Cloud Run job execution per estimate.

    Matches security's profiler rule (security-tenancy.md §4): project shipcube-apps,
    a service account with no roles, no secrets, Direct VPC egress (all-traffic) into
    a VPC whose only firewall rule denies egress, gen2, 1 vCPU / 2 GiB, 10-minute task
    timeout, max-retries 0. The image is the app image built by Cloud Build (which has
    egress for dependencies) plus this package; the job runs
    `python -m shipcube_profiler.agent` which uses LocalRunner inside the sandbox and
    writes the Profile JSON to stdout / the operation's GCS prefix.
    """

    name = "gcp"

    def __init__(self, project: str = "shipcube-apps", region: str = "us-central1",
                 network: str = "profiler-no-egress", subnet: str = "profiler",
                 service_account: str = "profiler@shipcube-apps.iam.gserviceaccount.com"):
        self.project, self.region = project, region
        self.network, self.subnet, self.sa = network, subnet, service_account

    def job_spec(self, image: str, estimate_id: str) -> dict:
        """Cloud Run Admin API v2 Job body (projects.locations.jobs.create)."""
        return {
            "labels": {"shipcube-role": "profiler", "estimate": estimate_id},
            "template": {
                "taskCount": 1,
                "parallelism": 1,
                "template": {
                    "maxRetries": 0,
                    "timeout": "600s",
                    "serviceAccount": self.sa,
                    "executionEnvironment": "EXECUTION_ENVIRONMENT_GEN2",
                    "vpcAccess": {
                        "egress": "ALL_TRAFFIC",
                        "networkInterfaces": [{"network": self.network, "subnetwork": self.subnet}],
                    },
                    "containers": [{
                        "image": image,
                        "command": ["python", "-m", "shipcube_profiler.agent"],
                        "env": [{"name": "SHIPCUBE_ESTIMATE_ID", "value": estimate_id}],
                        "resources": {"limits": {"cpu": "1", "memory": "2Gi"}},
                    }],
                },
            },
        }

    def run(self, app_dir: Path, summary: CodeSummary, hints: Hints) -> Profile:
        raise NotImplementedError("GCP profiling needs the deployer's image build; use LocalRunner or StaticRunner")


class StaticRunner:
    """No execution: typical numbers by app shape. Used for images, code that fails to
    start, or when the caller only has hints."""

    name = "static"

    def run(self, app_dir: Path | None, summary: CodeSummary, hints: Hints) -> Profile:
        p = Profile(mode="static", runner=self.name, kind=summary.kind)
        if summary.kind == "web":
            p.cpu_mean_cores, p.cpu_p95_cores, p.cpu_peak_cores, p.cpu_cv = 0.05, 0.4, 0.6, 2.5
            p.mem_mean_gb = p.mem_peak_gb = 0.15 + (0.25 if summary.uses_data_libs else 0)
            p.requests = RequestStats(latency_mean_s=0.08, latency_p95_s=0.3)
            p.active_s = 60.0
        else:
            p.cpu_mean_cores, p.cpu_p95_cores, p.cpu_peak_cores, p.cpu_cv = 0.8, 1.0, 1.0, 0.3
            p.mem_mean_gb, p.mem_peak_gb = (0.6, 1.0) if summary.uses_data_libs else (0.1, 0.2)
            p.active_s = p.wall_s = 120.0
        if summary.uses_gpu_libs:
            p.cpu_mean_cores, p.cpu_p95_cores, p.cpu_peak_cores = 3.0, 4.0, 4.0
            p.mem_mean_gb, p.mem_peak_gb = 8.0, 12.0
        p.notes.append("not executed: numbers are typical values for this app shape, not measurements")
        return p
