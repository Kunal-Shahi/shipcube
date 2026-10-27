"""The Profile a runner produces, and how it (plus hints) maps onto classifier features."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from shipcube_classifier.features import empty

from .schedule import runs_per_day

# Assumptions for turning `expected_users` into traffic; reported in profile.notes.
REQUESTS_PER_ACTIVE_USER_HOUR = 30
ACTIVE_HOURS_PER_DAY = 9


@dataclass
class Hints:
    expected_users: int | None = None
    schedule: str | None = None          # cron expression
    region: str = "us-central1"
    sample_input: str | None = None      # path mounted read-only as SHIPCUBE_SAMPLE_DIR

    @classmethod
    def from_any(cls, h: "Hints | dict | None") -> "Hints":
        if h is None:
            return cls()
        if isinstance(h, cls):
            return h
        return cls(**{k: v for k, v in h.items() if k in cls.__dataclass_fields__ and v is not None})


@dataclass
class RequestStats:
    sent: int = 0
    ok: int = 0
    latency_mean_s: float = 0.0
    latency_p95_s: float = 0.0
    concurrency: int = 1


@dataclass
class Profile:
    mode: str                        # "measured" or "static"
    runner: str                      # "local", "gcp" or "static"
    kind: str                        # "web" or "script"
    wall_s: float = 0.0
    active_s: float = 0.0            # script: run time; web: load-test window
    cpu_seconds: float = 0.0
    cpu_mean_cores: float = 0.0
    cpu_p95_cores: float = 0.0
    cpu_peak_cores: float = 0.0
    cpu_cv: float = 0.0
    mem_mean_gb: float = 0.0
    mem_peak_gb: float = 0.0
    io_read_mb: float = 0.0
    io_write_mb: float = 0.0
    io_source: str = "none"          # "psutil", "rusage" (approximate) or "none"
    net_bytes: int = 0
    net_calls: int = 0               # outbound connection attempts (non-loopback)
    net_blocked: int = 0
    remote_hosts: list[str] = field(default_factory=list)
    processes_max: int = 1
    samples: int = 0
    exit_code: int | None = None
    timed_out: bool = False
    killed_reason: str | None = None
    requests: RequestStats | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _round(d: dict) -> dict:
    return {k: (round(v, 6) if isinstance(v, float) and not math.isnan(v) else v) for k, v in d.items()}


def _diurnal_traffic(users: int) -> tuple[float, float, float]:
    """(invocations/hour, per-minute CV, idle fraction) for office-hours traffic."""
    lam = users * REQUESTS_PER_ACTIVE_USER_HOUR / 60.0           # requests per active minute
    active = ACTIVE_HOURS_PER_DAY * 60
    per_hour = lam * active / (24 * 60) * 60
    # Per-minute counts: Poisson(lam) during active minutes, ~0 otherwise.
    p = active / 1440
    mean = p * lam
    var = p * (lam + lam**2) - mean**2
    cv = math.sqrt(max(var, 0)) / mean if mean > 0 else float("nan")
    idle = (1 - p) + p * math.exp(-lam)
    return per_hour, cv, idle


def to_features(profile: Profile, hints: Hints | dict | None = None) -> tuple[dict[str, float], list[str]]:
    """Map a profile and hints onto FEATURES. Returns (features, assumptions)."""
    h = Hints.from_any(hints)
    f = empty()
    notes: list[str] = []
    f["cpu_mean_cores"] = profile.cpu_mean_cores
    f["cpu_p95_cores"] = profile.cpu_p95_cores
    alloc = max(1.0, math.ceil(profile.cpu_p95_cores or 0))
    f["cpu_util_mean"] = min(1.0, profile.cpu_mean_cores / alloc)
    f["cpu_cv"] = profile.cpu_cv
    if profile.cpu_mean_cores > 0:
        f["cpu_peak_to_mean"] = profile.cpu_p95_cores / profile.cpu_mean_cores
    f["mem_mean_gb"] = profile.mem_mean_gb
    f["mem_peak_gb"] = profile.mem_peak_gb
    f["mem_per_core_gb"] = profile.mem_peak_gb / max(profile.cpu_p95_cores, 0.05)
    active = max(profile.active_s, 1e-3)
    if profile.io_source != "none":
        f["io_mb_per_s"] = (profile.io_read_mb + profile.io_write_mb) / active
    f["net_mb_per_s"] = profile.net_bytes / 2**20 / active
    io = (0 if math.isnan(f["io_mb_per_s"]) else f["io_mb_per_s"]) + f["net_mb_per_s"]
    f["io_per_cpu_s"] = io / max(profile.cpu_mean_cores, 0.01)

    if profile.kind == "web":
        users = h.expected_users or 10
        if not h.expected_users:
            notes.append("expected_users not given; assumed 10")
        f["invocations_per_hour"], f["invocation_cv"], f["idle_fraction"] = _diurnal_traffic(users)
        notes.append(f"traffic model: {REQUESTS_PER_ACTIVE_USER_HOUR} requests per active user-hour, "
                     f"{ACTIVE_HOURS_PER_DAY} active hours a day")
        if profile.requests and profile.requests.ok:
            f["duration_s"] = profile.requests.latency_mean_s
    else:
        runs = runs_per_day(h.schedule) if h.schedule else 1.0
        if not h.schedule:
            notes.append("no schedule given; assumed one run a day")
        f["invocations_per_hour"] = runs / 24
        f["duration_s"] = profile.active_s or profile.wall_s
        busy_min = min(1440.0, runs * max(f["duration_s"], 60) / 60)
        f["idle_fraction"] = 1 - busy_min / 1440
        f["invocation_cv"] = math.sqrt(1440 / max(runs, 1e-9) - 1) if runs < 1440 else 0.0
    return _round(f), notes
