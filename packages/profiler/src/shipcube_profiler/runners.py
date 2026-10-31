"""Runners execute an app and return a Profile.

- LocalRunner: subprocess + psutil sampling + rlimits. Used inside the Cloud Run job
  sandbox (by agent.py), and directly only in DEV_MODE: on its own it is NOT a security
  boundary (no container, no network namespace).
- CloudRunJobRunner (gcp_runner.py): production, a Cloud Run job (gen2 sandbox, no egress).
- StaticRunner: no execution; typical numbers from the code summary and hints.
"""

from __future__ import annotations

import ctypes
import ctypes.util
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
from .errors import STATIC_REASONS
from .profile import Hints, Profile, RequestStats, RouteStats

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
    # Set by the agent inside the sandbox only. RLIMIT_NPROC counts every process and
    # thread of the uid, and the disk check watches whole filesystems, so neither makes
    # sense for an in-process run on a developer's machine.
    max_nproc: int | None = None
    max_disk_mb: int | None = None
    # Inside the sandbox the agent is root and runs the app as this uid (no capabilities
    # left after setuid): the app can't signal, ptrace or read the agent.
    app_uid: int | None = None
    stderr_tail_bytes: int = 64 * 1024


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PR_SET_NO_NEW_PRIVS = 38


def _no_new_privs() -> None:
    """Linux: execve can never grant this process (or its children) privileges, so a
    setuid binary runs without them. Cloud Run has no no-new-privileges flag of its
    own; the image also has its setuid bits stripped (gcp_runner.render_dockerfile)."""
    if sys.platform.startswith("linux"):
        if ctypes.CDLL(None, use_errno=True).prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "prctl(PR_SET_NO_NEW_PRIVS) failed")


def _preexec(limits: Limits):
    def fn():
        _no_new_privs()   # before the app's execve; Popen has already dropped to app_uid
        os.setsid()
        cpu = int(limits.timeout_s * 4) + 5
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        fsize = limits.max_file_mb * 2**20
        resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))
        if sys.platform.startswith("linux"):  # macOS doesn't enforce RLIMIT_AS
            mem = limits.memory_mb * 2**20 * 2
            resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
            if limits.max_nproc:
                resource.setrlimit(resource.RLIMIT_NPROC, (limits.max_nproc, limits.max_nproc))
    return fn


class _RusageInfoV2(ctypes.Structure):
    _fields_ = [("ri_uuid", ctypes.c_uint8 * 16)] + [(n, ctypes.c_uint64) for n in (
        "ri_user_time", "ri_system_time", "ri_pkg_idle_wkups", "ri_interrupt_wkups", "ri_pageins",
        "ri_wired_size", "ri_resident_size", "ri_phys_footprint", "ri_proc_start_abstime",
        "ri_proc_exit_abstime", "ri_child_user_time", "ri_child_system_time",
        "ri_child_pkg_idle_wkups", "ri_child_interrupt_wkups", "ri_child_pageins",
        "ri_child_elapsed_abstime", "ri_diskio_bytesread", "ri_diskio_byteswritten")]


def _darwin_disk_io():
    """macOS: per-process disk bytes via proc_pid_rusage (psutil has no io_counters there)."""
    if sys.platform != "darwin":
        return None
    try:
        lib = ctypes.CDLL(ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib")
    except OSError:
        return None

    def read(pid: int) -> tuple[int, int] | None:
        info = _RusageInfoV2()
        if lib.proc_pid_rusage(pid, 2, ctypes.byref(info)) != 0:  # RUSAGE_INFO_V2
            return None
        return info.ri_diskio_bytesread, info.ri_diskio_byteswritten
    return read


class _Sampler(threading.Thread):
    """Samples CPU, RSS, I/O and process count of a process tree."""

    def __init__(self, pid: int, limits: Limits, writable: list[Path] | None = None):
        super().__init__(daemon=True)
        self.root = psutil.Process(pid)
        self.limits = limits
        self.procs: dict[int, psutil.Process] = {}
        self.cpu: list[float] = []
        self.rss: list[float] = []
        self.nprocs = 1
        self.io = {}  # pid -> (read, write)
        self.io_supported = hasattr(psutil.Process, "io_counters")
        self.darwin_io = None if self.io_supported else _darwin_disk_io()
        self.kill_reason: str | None = None
        self.window: tuple[int, int] | None = None   # sample indices of the active window
        self._halt = threading.Event()
        self.disk = _DiskWatch(writable or []) if limits.max_disk_mb else None

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
        if self.limits.app_uid is not None:
            _set_thread_fsuid(self.limits.app_uid)
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
                        elif self.darwin_io:
                            c = self.darwin_io(p.pid)
                            if c:
                                self.io[p.pid] = c
                except (psutil.Error, AttributeError):
                    continue
            self.cpu.append(cpu)
            self.rss.append(rss)
            self.nprocs = max(self.nprocs, len(tree))
            # Processes first: forked children share pages, so a fork storm's summed RSS
            # passes the memory limit too, and "processes" is the accurate reason.
            if len(tree) > self.limits.max_processes:
                self.kill_reason = f"more than {self.limits.max_processes} processes"
            elif rss > self.limits.memory_mb * 2**20:
                self.kill_reason = f"memory over {self.limits.memory_mb} MB"
            elif self.disk and self.disk.written() > self.limits.max_disk_mb * 2**20:
                self.kill_reason = f"disk: more than {self.limits.max_disk_mb} MB written"
            if self.kill_reason:
                _killpg(self.root.pid)
                break
            time.sleep(self.limits.sample_interval_s)

    def stop(self):
        self._halt.set()


def _set_thread_fsuid(uid: int) -> None:
    """Linux: this thread reads /proc as `uid` (and gid). A root agent with no CAP_SYS_PTRACE can't
    read another uid's /proc/<pid>/io, but the app's own uid can (it's dumpable), and
    setfsuid is per-thread, so only the sampler changes. Needs CAP_SETUID, which the
    agent has to start the app anyway."""
    if sys.platform.startswith("linux"):
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            libc.setfsgid(uid)   # the kernel matches gid as well as uid (app gid == uid here)
            libc.setfsuid(uid)
        except (OSError, AttributeError):
            pass


class _DiskWatch:
    """Bytes added to the writable filesystems since the run started. Inside the
    sandbox these are in-memory (Cloud Run) or tmpfs (Docker), so this also guards
    memory that RSS doesn't show."""

    def __init__(self, paths: list[Path]):
        self.devs: dict[int, Path] = {}
        for p in paths:
            try:
                self.devs.setdefault(os.stat(p).st_dev, p)
            except OSError:
                continue
        self.base = {d: shutil.disk_usage(p).used for d, p in self.devs.items()}

    def written(self) -> int:
        total = 0
        for d, p in self.devs.items():
            try:
                total += max(0, shutil.disk_usage(p).used - self.base[d])
            except OSError:
                continue
        return total


class _Tail(threading.Thread):
    """Keeps the last `limit` bytes of a pipe. Reading continuously means a chatty app
    can't fill the pipe and stall, and the agent's memory stays bounded."""

    def __init__(self, stream, limit: int):
        super().__init__(daemon=True)
        self.stream, self.limit, self.buf, self.total = stream, limit, bytearray(), 0

    def run(self):
        for chunk in iter(lambda: self.stream.read1(65536), b""):
            self.total += len(chunk)
            self.buf += chunk
            if len(self.buf) > self.limit:
                del self.buf[:len(self.buf) - self.limit]

    def text(self) -> str:
        return self.buf.decode(errors="replace")


CV_BUCKET_S = 1.0


def _bucketed_cv(cpu: list[float], interval_s: float) -> float:
    """CPU coefficient of variation over 1 s buckets. The traces measure variation over
    10 s (Alibaba) to 5 min (Borg) windows; at 100 ms a lightly loaded server's CPU is
    mostly scheduler noise, which made steady APIs look bursty."""
    k = max(1, round(CV_BUCKET_S / interval_s))
    buckets = [statistics.fmean(cpu[i:i + k]) for i in range(0, len(cpu), k) if len(cpu[i:i + k]) == k]
    if len(buckets) < 2:
        buckets = cpu
    mean = statistics.fmean(buckets) if buckets else 0.0
    return statistics.pstdev(buckets) / mean if mean > 0 else 0.0


_OOM = re.compile(r"MemoryError|Cannot allocate memory|std::bad_alloc|out of memory|ENOMEM", re.I)


def _allocation_refused(returncode: int | None, stderr: str, limits: Limits) -> str | None:
    """The memory limit as seen on Linux: RLIMIT_AS makes the allocation itself fail
    (MemoryError, exit 1) before RSS grows enough for the sampler to kill anything.
    macOS doesn't enforce RLIMIT_AS, so there the sampler's RSS check fires instead."""
    if returncode not in (0, None) and _OOM.search(stderr[-4000:]):
        return f"memory over {limits.memory_mb} MB (allocation refused by RLIMIT_AS)"
    return None


_NPROC = re.compile(r"BlockingIOError|Resource temporarily unavailable|can't start new thread|EAGAIN")


def _nproc_refused(returncode: int | None, stderr: str, limits: Limits) -> str | None:
    """RLIMIT_NPROC makes fork/clone fail with EAGAIN instead of killing anything."""
    if limits.max_nproc and returncode not in (0, None) and _NPROC.search(stderr[-4000:]):
        return f"more than {limits.max_nproc} processes (fork refused by RLIMIT_NPROC)"
    return None


def _killpg(pid: int, sig=signal.SIGKILL):
    # PermissionError isn't swallowed: an agent that can't stop the app must fail loudly
    # (e.g. a root agent without CAP_KILL), not leave it running unwatched.
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        pass


def _sandbox_env(app_dir: Path, home: Path, stats_file: Path, hints: Hints, port: int | None) -> dict:
    """A minimal environment: nothing from the parent (no API keys, no cloud creds)."""
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        "HOME": str(home),
        "TMPDIR": str(home),
        "LANG": "C.UTF-8",
        # The instrumentation's sitecustomize first, then the app, then the app's deps
        # (installed with --target outside any site dir; see gcp_runner.render_dockerfile).
        "PYTHONPATH": os.pathsep.join(
            [str(SANDBOX_DIR), str(app_dir)] + ([os.environ["SHIPCUBE_APP_DEPS"]]
                                                 if os.environ.get("SHIPCUBE_APP_DEPS") else [])),
        "PYTHONNOUSERSITE": "1",   # no usercustomize to undo the instrumentation
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


def _paths(routes: list[str]) -> list[str]:
    return [r for r in routes if "<" not in r and "{" not in r] or ["/"]


def _get(port: int, path: str) -> tuple[float, bool]:
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as r:
            r.read()
            good = r.status < 500
    except Exception:
        good = False
    return time.perf_counter() - t, good


def _load(port: int, routes: list[str], bursts: int, per_burst: int, concurrency: int,
          gap_s: float) -> RequestStats:
    """Bursty synthetic traffic: `bursts` bursts of `per_burst` GETs with idle gaps."""
    paths = _paths(routes)
    lat: list[float] = []
    ok = 0

    def hit(i: int):
        return _get(port, paths[i % len(paths)])

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


PROBE_ROUTES = 8          # at most this many routes probed
PROBE_MIN_S = 1.0         # each probe runs at least this long (enough CPU samples) ...
PROBE_MAX_S = 4.0         # ... and at most this long or PROBE_MAX_REQUESTS requests
PROBE_MAX_REQUESTS = 40
PROBE_CONCURRENCY = 4


def _probe_routes(port: int, routes: list[str], sampler: "_Sampler", interval_s: float) -> list[RouteStats]:
    """Each route on its own, one after another, so its CPU window is its own: the mixed load test
    averages a CPU-heavy route with idle ones (a 500 ms sleep, a health check, a static page)."""
    out = []
    with ThreadPoolExecutor(PROBE_CONCURRENCY) as pool:
        for path in _paths(routes)[:PROBE_ROUTES]:
            start, t0, lat, ok = sampler.mark(), time.monotonic(), [], 0
            while (len(lat) < PROBE_MAX_REQUESTS and time.monotonic() - t0 < PROBE_MAX_S
                   and (time.monotonic() - t0 < PROBE_MIN_S or len(lat) < 2 * PROBE_CONCURRENCY)):
                for dt, good in pool.map(lambda _: _get(port, path), range(PROBE_CONCURRENCY)):
                    lat.append(dt)
                    ok += good
            time.sleep(2 * interval_s)              # let the sampler catch the tail of the window
            cpu = sampler.cpu[start:sampler.mark()]
            lat.sort()
            out.append(RouteStats(
                path=path, sent=len(lat), ok=ok,
                latency_mean_s=statistics.fmean(lat) if lat else 0.0,
                latency_p95_s=lat[int(0.95 * (len(lat) - 1))] if lat else 0.0,
                cpu_mean_cores=statistics.fmean(cpu) if cpu else 0.0,
                cpu_p95_cores=sorted(cpu)[int(0.95 * (len(cpu) - 1))] if cpu else 0.0))
    return out


class LocalRunner:
    """Run the app in a subprocess on this machine and sample it with psutil."""

    name = "local"

    def __init__(self, limits: Limits | None = None, python: str | None = None,
                 bursts: int = 3, per_burst: int = 40, concurrency: int = 8, gap_s: float = 1.5,
                 probe_routes: bool = True):
        self.limits = limits or Limits()
        self.probe_routes = probe_routes
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
        env["SHIPCUBE_GUARD_PIDS"] = ",".join(sorted({str(os.getpid()), "1"}))
        cmd = _command(self.python, app_dir, summary, port)
        as_user = {}
        if self.limits.app_uid is not None:
            uid = self.limits.app_uid
            if os.geteuid() != 0 or uid == 0:
                raise RuntimeError("app_uid needs a root agent and a non-root app uid")
            # The app's HOME/TMPDIR, where it also writes its netstats. 0755 so the agent
            # (root, but without CAP_DAC_OVERRIDE) can still list and read it.
            os.chmod(work, 0o755)
            os.chown(work, uid, uid)
            as_user = dict(user=uid, group=uid, extra_groups=[])
        ru0 = resource.getrusage(resource.RUSAGE_CHILDREN)
        t0 = time.monotonic()
        # Popen sets the uid before preexec_fn runs, so the rlimits apply to the app's uid.
        proc = subprocess.Popen(cmd, cwd=app_dir, env=env, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, preexec_fn=_preexec(self.limits), **as_user)
        tail = _Tail(proc.stderr, self.limits.stderr_tail_bytes)
        tail.start()
        writable = [work, Path(tempfile.gettempdir()), Path("/tmp"), Path("/var/tmp"), Path("/dev/shm")]
        sampler = _Sampler(proc.pid, self.limits, writable)
        sampler.start()
        prof = Profile(mode="measured", runner=self.name, kind=summary.kind)
        win_start, win_end = 0, None
        try:
            if summary.kind == "web":
                if not _wait_port(port, proc, min(20.0, self.limits.timeout_s)):
                    prof.notes.append("server did not open its port; profile covers startup only")
                else:
                    win_start = sampler.mark()
                    ta = time.monotonic()
                    load = dict(self.load)
                    if hints.traffic_for(summary.api_only) == "always_on":
                        # Steady callers: one continuous stream, no idle gaps.
                        load.update(bursts=1, per_burst=load["bursts"] * load["per_burst"],
                                    concurrency=max(1, load["concurrency"] // 2), gap_s=0)
                    prof.requests = _load(port, summary.routes, **load)
                    prof.active_s = time.monotonic() - ta
                    win_end = sampler.mark()   # the overall numbers cover the load test only
                    if self.probe_routes:
                        prof.routes = _probe_routes(port, summary.routes, sampler, self.limits.sample_interval_s)
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
            tail.join(timeout=2)
            stderr = tail.text()
        wall = time.monotonic() - t0
        ru1 = resource.getrusage(resource.RUSAGE_CHILDREN)

        prof.wall_s = wall
        if summary.kind != "web":
            prof.active_s = wall
        prof.exit_code = proc.returncode
        prof.killed_reason = (sampler.kill_reason or _allocation_refused(proc.returncode, stderr, self.limits)
                              or _nproc_refused(proc.returncode, stderr, self.limits))
        prof.processes_max = sampler.nprocs
        # CPU: the load test's window only (the route probes are separate). Memory: everything
        # from the load test on, probes included, so a heavy route's peak counts.
        cpu = sampler.cpu[win_start:win_end] or sampler.cpu
        rss = sampler.rss[win_start:] or sampler.rss
        prof.samples = len(sampler.cpu)
        prof.cpu_seconds = (ru1.ru_utime - ru0.ru_utime) + (ru1.ru_stime - ru0.ru_stime)
        if cpu:
            prof.cpu_mean_cores = statistics.fmean(cpu)
            prof.cpu_p95_cores = sorted(cpu)[int(0.95 * (len(cpu) - 1))]
            prof.cpu_peak_cores = max(cpu)
            prof.cpu_cv = _bucketed_cv(cpu, self.limits.sample_interval_s)
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
        if sampler.io:
            # Cumulative per-process counters as last sampled (I/O in the final
            # <100 ms before exit can be missed).
            prof.io_read_mb = sum(r for r, _ in sampler.io.values()) / 2**20
            prof.io_write_mb = sum(w for _, w in sampler.io.values()) / 2**20
            prof.io_source = "psutil" if sampler.io_supported else "proc_pid_rusage"
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
            prof.tamper_attempts += s.get("tamper_attempts", 0)
            prof.remote_hosts = sorted(set(prof.remote_hosts) | set(s["remote_hosts"]))[:20]
            prof.remote_ports = sorted(set(prof.remote_ports) | set(s.get("remote_ports", [])))[:20]
        if prof.net_blocked:
            prof.notes.append(f"{prof.net_blocked} outbound connection(s) blocked (no egress while profiling)")
        if prof.timed_out:
            prof.notes.append(f"stopped after {self.limits.timeout_s:.0f}s; duration is a lower bound")
        if proc.returncode not in (0, None) and summary.kind != "web" and not prof.timed_out:
            tail = stderr.strip().splitlines()[-1:] if stderr.strip() else []
            prof.notes.append(f"exited with code {proc.returncode}" + (f": {tail[0][:200]}" if tail else ""))
        return prof


def runner_from_env(env: dict | None = None) -> Runner:
    """The profiler selected by SHIPCUBE_PROFILER:

    - "gcp":    CloudRunJobRunner (production): Cloud Run job, gen2, no-egress VPC
    - "local":  DockerRunner: the same image in a local container (--network none, ...)
    - "inproc": LocalRunner in this process; allowed only with SHIPCUBE_DEV_MODE=1
    - unset:    "inproc" in DEV_MODE, otherwise "gcp"
    """
    e = os.environ if env is None else env
    choice = e.get("SHIPCUBE_PROFILER", "").strip().lower()
    dev = e.get("SHIPCUBE_DEV_MODE") == "1"
    if not choice:
        choice = "inproc" if dev else "gcp"
    # On Cloud Run (the API and worker) only the sandboxed Cloud Run job: never Docker, never this
    # process, never dev mode (#29). The sandbox's own agent runs LocalRunner directly, not this.
    if any(e.get(k) for k in ("K_SERVICE", "K_REVISION", "CLOUD_RUN_JOB")) and (choice != "gcp" or dev):
        from .gcp_runner import ProfilerError
        raise ProfilerError("config", f"refusing SHIPCUBE_PROFILER={choice}{' with SHIPCUBE_DEV_MODE=1' if dev else ''} "
                                      "on Cloud Run: production profiles only in the gcp sandbox")
    if choice == "local":
        from .docker_runner import DockerRunner, DockerSandboxConfig
        return DockerRunner(DockerSandboxConfig.from_env(e))
    if choice == "gcp":
        from .gcp_runner import CloudRunJobRunner, GcpProfilerConfig
        return CloudRunJobRunner(GcpProfilerConfig.from_env(e))
    if choice == "inproc":
        if not dev:
            from .gcp_runner import ProfilerError
            raise ProfilerError("config", "SHIPCUBE_PROFILER=inproc runs user code in this process; "
                                          "it needs SHIPCUBE_DEV_MODE=1 (use 'local' for the Docker sandbox)")
        return LocalRunner()
    from .gcp_runner import ProfilerError
    raise ProfilerError("config", f"SHIPCUBE_PROFILER must be local, gcp or inproc (got {choice!r})")


def default_runner() -> Runner:
    """Kept for callers of PR #7's API; same as runner_from_env()."""
    return runner_from_env()


class StaticRunner:
    """No execution: typical numbers by app shape. Used for images, code that fails to
    start, or when the caller only has hints. Results are marked mode="static" with a
    `static_reason` code from errors.STATIC_REASONS; the control plane passes
    StaticRunner(reason="quota" | "budget" | ...) when it decides not to profile."""

    name = "static"

    def __init__(self, reason: str | None = None):
        if reason is not None and reason not in STATIC_REASONS:
            raise ValueError(f"unknown static reason {reason!r}; one of {', '.join(STATIC_REASONS)}")
        self.reason = reason

    def run(self, app_dir: Path | None, summary: CodeSummary, hints: Hints) -> Profile:
        p = Profile(mode="static", runner=self.name, kind=summary.kind, static_reason=self.reason)
        if self.reason:
            p.notes.append(STATIC_REASONS[self.reason])
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
