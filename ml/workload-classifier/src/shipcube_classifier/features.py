"""The shared feature space.

Both the trace pipeline (scripts/build_dataset.py) and the profiler
(shipcube_profiler) produce a dict keyed by FEATURES. A feature a source can't
observe is left as NaN; the model handles missing values natively.
"""

from __future__ import annotations

import math

CLASSES = [
    "batch_cron",
    "bursty_interactive",
    "steady_service",
    "memory_heavy",
    "io_bound",
    "compute_heavy",
]

FEATURES = [
    "cpu_mean_cores",       # average CPU used, in cores
    "cpu_p95_cores",        # 95th percentile CPU, in cores
    "cpu_util_mean",        # cpu_mean / allocated cores (0..1); 1 core allocated when unknown
    "cpu_cv",               # coefficient of variation of CPU over time
    "cpu_peak_to_mean",     # max (or p95) CPU over mean CPU
    "mem_mean_gb",          # average resident memory, GiB
    "mem_peak_gb",          # peak resident memory, GiB
    "mem_per_core_gb",      # mem_peak_gb / max(cpu_mean_cores, 0.05): memory held per core of sustained work
    "io_mb_per_s",          # disk read+write while active, MiB/s
    "net_mb_per_s",         # network in+out while active, MiB/s
    "io_per_cpu_s",         # (disk+net MiB) per CPU-second: high means it waits on I/O
    "invocations_per_hour", # requests or runs per hour, averaged over a day
    "invocation_cv",        # CV of per-minute invocation counts (burstiness)
    "idle_fraction",        # share of minutes with no invocations / no activity
    "duration_s",           # seconds per invocation (request, run or task)
]

NAN = float("nan")


def empty() -> dict[str, float]:
    return {f: NAN for f in FEATURES}


def is_missing(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def derive(f: dict[str, float]) -> dict[str, float]:
    """Fill the ratio features from the base ones when they are missing."""
    out = {k: f.get(k, NAN) for k in FEATURES}
    out = {k: (NAN if is_missing(v) else float(v)) for k, v in out.items()}
    p95, mean = out["cpu_p95_cores"], out["cpu_mean_cores"]
    if is_missing(out["cpu_peak_to_mean"]) and not is_missing(p95) and not is_missing(mean) and mean > 0:
        out["cpu_peak_to_mean"] = p95 / mean
    mem = out["mem_peak_gb"] if not is_missing(out["mem_peak_gb"]) else out["mem_mean_gb"]
    cpu = mean
    if is_missing(out["mem_per_core_gb"]) and not is_missing(mem) and not is_missing(cpu):
        out["mem_per_core_gb"] = mem / max(cpu, 0.05)
    if is_missing(out["io_per_cpu_s"]) and not is_missing(mean):
        io = sum(v for v in (out["io_mb_per_s"], out["net_mb_per_s"]) if not is_missing(v))
        if not (is_missing(out["io_mb_per_s"]) and is_missing(out["net_mb_per_s"])):
            out["io_per_cpu_s"] = io / max(mean, 0.01)
    return out


def vector(f: dict[str, float]) -> list[float]:
    d = derive(f)
    return [d[k] for k in FEATURES]
