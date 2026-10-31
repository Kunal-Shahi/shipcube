"""Class + profile + hints → tier, target, resources, monthly cost range, caps.

All rules are deterministic. Muse Spark only explains the result; it never feeds back
into these numbers.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from shipcube_profiler import CodeSummary, Hints, Profile, runs_per_day

from .types import Alternative, Caps, CostRange, Resources

SECONDS_PER_MONTH = 730 * 3600
DAYS_PER_MONTH = 30.4
CPU_OPTIONS = [0.5, 1, 2, 4]
MEM_OPTIONS_GIB = [0.5, 1, 2, 4, 8, 16, 32]
# Cloud Run: <1 vCPU allows ≤512 MiB; >4 GiB needs ≥2 vCPU; >8 GiB needs ≥4 vCPU.
MIN_CPU_FOR_MEM = [(0.5, 0.5), (4, 1), (8, 2), (16, 4), (32, 8)]


@lru_cache(maxsize=1)
def pricing() -> dict:
    return json.loads((Path(__file__).parent / "pricing.json").read_text())


@dataclass
class Traffic:
    kind: str                       # "web" or "script"
    requests_per_month: float = 0.0
    peak_rps: float = 0.0
    latency_s: float = 0.1
    runs_per_month: float = 0.0
    run_s: float = 0.0
    assumptions: list[str] = field(default_factory=list)


def traffic(profile: Profile, features: dict, hints: Hints) -> Traffic:
    if profile.kind == "web":
        per_hour = features.get("invocations_per_hour") or 0.0
        users = hints.expected_users or 10
        lat = profile.requests.latency_mean_s if profile.requests and profile.requests.ok else 0.1
        # Peak: everyone active in the same minute, 3x the office-hours average.
        peak = users * 30 / 3600 * 3
        return Traffic("web", requests_per_month=per_hour * 730, peak_rps=peak, latency_s=lat)
    runs = runs_per_day(hints.schedule) if hints.schedule else 1.0
    return Traffic("script", runs_per_month=runs * DAYS_PER_MONTH,
                   run_s=max(profile.active_s or profile.wall_s, 1.0))


# --- tier -----------------------------------------------------------------------------


def choose_tier(cls: str, summary: CodeSummary, hints: Hints, profile: Profile,
                cpu_need: float, mem_need: float) -> tuple[str, str]:
    if summary.uses_gpu_libs:
        return "T4", "cloud-run-gpu"
    if profile.kind != "web":
        if hints.expected_users and any(s in summary.writes_outputs for s in ("html", "md")):
            return "T0", "static-snapshot"
        return "T2", "cloud-run-job"
    users = hints.expected_users or 10
    if cls == "steady_service" or users >= 300 or (cls == "compute_heavy" and users >= 100):
        if cpu_need > 4 or mem_need > 16:
            return "T3", "gke-autopilot"
        return "T3", "cloud-run-service"
    return "T1", "cloud-run-service"


# --- sizing ---------------------------------------------------------------------------


def _pick(options: list[float], need: float) -> float:
    return next((o for o in options if o >= need), options[-1])


def _mem_str(gib: float) -> str:
    return f"{int(gib * 1024)}Mi" if gib < 1 else f"{int(gib)}Gi"


def _cpu_str(c: float) -> str:
    return "0.5" if c == 0.5 else str(int(c))


def needs(profile: Profile) -> tuple[float, float]:
    """(vCPU, GiB) per instance with headroom.

    CPU: 1.3x the larger of p95 and 80% of peak (short request bursts often fall
    between samples, so p95 alone undersizes servers). A single process can't use more
    than one core (a Python process, at least), so it's capped at 1 vCPU."""
    cpu = max(profile.cpu_p95_cores, 0.8 * profile.cpu_peak_cores) * 1.3
    if profile.processes_max <= 1:
        cpu = min(cpu, 1.0)
    return cpu, profile.mem_peak_gb * 1.5 + 0.1


CONCURRENCY = {"bursty_interactive": 80, "steady_service": 80, "io_bound": 200,
               "memory_heavy": 20, "batch_cron": 40}


def size(tier: str, cls: str, profile: Profile, tr: Traffic, hints: Hints) -> Resources:
    cpu_need, mem_need = needs(profile)
    if tier == "T4":
        return Resources(cpu="4", memory="16Gi", min_instances=0, max_instances=1,
                         concurrency=4, timeout_s=900, gpu="nvidia-l4")
    mem = _pick(MEM_OPTIONS_GIB, mem_need)
    cpu = _pick(CPU_OPTIONS, cpu_need)
    cpu = max(cpu, next(c for m, c in MIN_CPU_FOR_MEM if mem <= m))
    cpu = min(cpu, 4)
    if tier in ("T2", "T0"):
        t = 3600 if profile.timed_out else int(min(3600, max(60, math.ceil(tr.run_s * 3))))
        return Resources(cpu=_cpu_str(cpu), memory=_mem_str(mem), max_instances=1, timeout_s=t,
                         schedule=hints.schedule or "0 2 * * *")
    conc = CONCURRENCY.get(cls) or max(1, int(cpu * 2))
    in_flight = tr.peak_rps * max(tr.latency_s, 0.01)
    max_inst = int(min(10, max(2, math.ceil(in_flight / conc * 2))))
    p95 = profile.requests.latency_p95_s if profile.requests else 1.0
    timeout = int(min(300, max(60, math.ceil(p95 * 20))))
    return Resources(cpu=_cpu_str(cpu), memory=_mem_str(mem),
                     min_instances=1 if tier == "T3" else 0, max_instances=max_inst,
                     concurrency=conc, timeout_s=timeout)


# --- cost -----------------------------------------------------------------------------


def _gib(mem: str) -> float:
    return int(mem[:-2]) / 1024 if mem.endswith("Mi") else float(mem[:-2])


def _spread(expected: float, low_f: float, high_f: float) -> CostRange:
    return CostRange(low=round(expected * low_f, 2), expected=round(expected, 2),
                     high=round(expected * high_f, 2))


def cost(tier: str, target: str, res: Resources, tr: Traffic, confidence: float) -> tuple[CostRange, dict]:
    """Monthly USD before the account-wide free tier (see pricing.json)."""
    p = pricing()
    cr = p["cloud_run"]
    cpu, mem = float(res.cpu), _gib(res.memory)
    hi = 2.0 + 2.0 * (1 - confidence)        # less confident → wider range
    basis: dict = {"pricing": f"{p['source'].split(' (')[0]}, fetched {p['fetched']}",
                   "free_tier_subtracted": False}
    if tier == "T1":
        rb = cr["request_based"]
        billable = tr.requests_per_month * max(tr.latency_s, 0.1)   # billed per 100 ms
        cold = tr.requests_per_month / 50 * 2.0                    # ~1 cold start per 50 requests, 2 s each
        compute = (billable + cold) * (cpu * rb["cpu_per_vcpu_s"] + mem * rb["memory_per_gib_s"])
        reqs = tr.requests_per_month / 1e6 * rb["requests_per_million"]
        basis.update(billing="request-based", billable_s=round(billable + cold), requests=round(tr.requests_per_month))
        return _spread(compute + reqs, 0.4, hi), basis
    if tier in ("T2", "T0"):
        jb = cr["jobs"]
        run = tr.runs_per_month * tr.run_s * (cpu * jb["cpu_per_vcpu_s"] + mem * jb["memory_per_gib_s"])
        sched = p["cloud_scheduler"]["per_job_month"]
        serve = 0.0
        if tier == "T0":   # static page served from a scale-to-zero service, ~10 ms per view
            rb = cr["request_based"]
            serve = tr.requests_per_month * 0.1 * (0.5 * rb["cpu_per_vcpu_s"] + 0.5 * rb["memory_per_gib_s"])
        basis.update(billing="jobs (instance-based)", runs=round(tr.runs_per_month, 1),
                     run_s=round(tr.run_s, 2), scheduler_job=sched)
        return _spread(run + sched + serve, 0.5, hi + 1), basis
    if tier == "T3" and target == "gke-autopilot":
        g = p["gke_autopilot"]
        pods = max(1, res.min_instances)
        per_h = pods * (cpu * g["pod_vcpu_per_hour"] + mem * g["pod_memory_per_gib_hour"])
        basis.update(billing="GKE Autopilot pod requests", pods=pods)
        return _spread(per_h * 730, 1.0, hi), basis
    if tier == "T3":
        ib = cr["instance_based"]
        inst = max(1, res.min_instances)
        always_on = inst * SECONDS_PER_MONTH * (cpu * ib["cpu_per_vcpu_s"] + mem * ib["memory_per_gib_s"])
        reqs = tr.requests_per_month / 1e6 * cr["request_based"]["requests_per_million"]
        basis.update(billing="instance-based, min instances always on", instances=inst)
        return _spread(always_on + reqs, 1.0, hi), basis
    # T4: GPU billed while serving; assume 2 active hours a day (1–8 for the range).
    ib = cr["instance_based"]
    per_s = cr["gpu_l4_per_s"] + cpu * ib["cpu_per_vcpu_s"] + mem * ib["memory_per_gib_s"]
    expected = 2 * 3600 * DAYS_PER_MONTH * per_s
    basis.update(billing="instance-based + L4 GPU", active_hours_per_day=2)
    return CostRange(low=round(expected / 2, 2), expected=round(expected, 2), high=round(expected * 4, 2)), basis


# --- caps -----------------------------------------------------------------------------


def rule_caps(tier: str, res: Resources, tr: Traffic, monthly: CostRange) -> Caps:
    rpm = None
    if tr.kind == "web":
        rpm = int(max(60, math.ceil(tr.peak_rps * 60 * 4)))
    spend = float(max(5, math.ceil(monthly.high * 2)))
    cpu = res.cpu if res.cpu in ("0.5", "1", "2", "4") else "4"
    return Caps(max_instances=res.max_instances, concurrency=res.concurrency,
                requests_per_minute=rpm, monthly_spend_usd=spend, cpu=cpu,
                memory=res.memory, timeout_s=res.timeout_s)


# --- alternatives ---------------------------------------------------------------------

TRADEOFFS = {
    "T0": "Precompute the page on a schedule and serve a static snapshot: cheapest and instant, but data is only as fresh as the last refresh.",
    "T1": "Cloud Run scaling to zero: pay only while requests run, with a cold start of a second or two after idle periods.",
    "T2": "Cloud Run job triggered by Cloud Scheduler: pay only while it runs; no HTTP endpoint.",
    "T3": "Always-on instance: no cold starts and steady latency, but you pay around the clock even when idle.",
    "T4": "GPU instance: needed for model inference or training, far more expensive per hour.",
}


def alternatives(tier: str, cls: str, summary: CodeSummary, profile: Profile, tr: Traffic,
                 hints: Hints, confidence: float) -> list[Alternative]:
    if profile.kind == "web":
        options = [("T1", "cloud-run-service"), ("T3", "cloud-run-service")]
        # A snapshot only works for read-only pages: no per-item routes and no input per request.
        if not summary.accepts_input and all("<" not in r and "{" not in r for r in summary.routes):
            options.append(("T0", "static-snapshot"))
    else:
        options = [("T2", "cloud-run-job"), ("T3", "cloud-run-service")]
        if any(s in summary.writes_outputs for s in ("html", "md", "csv", "json")):
            options.append(("T0", "static-snapshot"))
    out = []
    for t, target in options:
        if t == tier:
            continue
        alt_tr = tr
        if t == "T0" and tr.kind == "web":
            # A snapshot refreshes daily; viewers hit a static page.
            alt_tr = Traffic("script", requests_per_month=tr.requests_per_month,
                             runs_per_month=DAYS_PER_MONTH, run_s=max(10.0, tr.latency_s * 50))
        if t == "T3" and tr.kind == "script":
            alt_tr = Traffic("web", requests_per_month=0)
        res = size(t, cls, profile, alt_tr, hints)
        c, _ = cost(t, target, res, alt_tr, confidence)
        out.append(Alternative(tier=t, target=target, monthly_cost_usd=c, tradeoff=TRADEOFFS[t]))
    return out
