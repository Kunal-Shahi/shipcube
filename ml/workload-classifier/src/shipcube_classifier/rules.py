"""Physical thresholds and the rule-based classifier.

The same thresholds label the traces (weak_labels.py) and, at runtime, classify an app
when the model's confidence is below its gate: the fallback applies the labelling rules
directly to the profiled features, with the app's shape (script / web) standing in
for the traces' side signal. Pure Python, no pandas.
"""

from __future__ import annotations

import math

# Physical thresholds, the same for every source. An earlier version used
# within-source quantiles, which balanced the classes but made "memory-heavy" mean
# something different in each trace, so the model couldn't transfer between them
# (leave-one-source-out macro-F1 0.19-0.44). Fixed thresholds keep the labels'
# meaning constant; that's what an app profiled by ShipCube is judged against too.
THRESHOLDS = {
    "memory_min_peak_gb": 1.0,       # memory_heavy: at least 1 GiB resident ...
    "memory_per_core_gb": 4.0,       # ... and >= 4 GiB per core it actually uses ...
    "memory_max_util": 0.5,          # ... while the CPU is mostly idle
    "compute_min_cores": 1.5,        # compute_heavy: >1 core (one busy thread isn't enough) ...
    "compute_min_util": 0.6,         # ... kept busy
    "io_per_cpu_s": 150.0,           # io_bound: >= 150 MiB of disk+network per CPU-second ...
    "io_max_util": 0.4,              # ... with the CPU mostly waiting
    "io_event_min_duration_s": 5.0,  # queue/storage/event functions that run >= 5 s
    "bursty_idle_fraction": 0.5,     # bursty: idle at least half the minutes of a day, or
    "bursty_invocation_cv": 3.0,     #   per-minute invocation CV >= 3, or
    "bursty_cpu_cv": 0.6,            #   CPU CV >= 0.6 over time, or
    "bursty_peak_to_mean": 4.0,      #   (no CV available) peak/mean CPU >= 4
}


def _known(v) -> bool:
    return v is not None and not (isinstance(v, float) and math.isnan(v))


def rule_class(f: dict[str, float], shape: str | None, t: dict = THRESHOLDS) -> str:
    """Classify one feature dict by the labelling rules. shape: "script", "web" or "worker"."""
    g = lambda k: f.get(k)  # noqa: E731
    util = g("cpu_util_mean")
    has_cpu = _known(util)
    mpc, peak = g("mem_per_core_gb"), g("mem_peak_gb")
    if (_known(peak) and peak >= t["memory_min_peak_gb"]
            and (not _known(mpc) or mpc >= t["memory_per_core_gb"])
            and (not has_cpu or util <= t["memory_max_util"])):
        return "memory_heavy"
    if has_cpu and util >= t["compute_min_util"] and (g("cpu_mean_cores") or 0) >= t["compute_min_cores"]:
        return "compute_heavy"
    ipc = g("io_per_cpu_s")
    if _known(ipc) and ipc >= t["io_per_cpu_s"] and (not has_cpu or util <= t["io_max_util"]):
        return "io_bound"
    if shape == "script":
        return "batch_cron"
    # Web apps and long-running workers: traffic shape first (it comes from the
    # traffic model, which is what the user told us), then CPU variation.
    idle, cv, ccv, p2m = g("idle_fraction"), g("invocation_cv"), g("cpu_cv"), g("cpu_peak_to_mean")
    if _known(idle) or _known(cv):
        bursty = ((_known(idle) and idle >= t["bursty_idle_fraction"])
                  or (_known(cv) and cv >= t["bursty_invocation_cv"]))
    else:
        bursty = ((_known(ccv) and ccv >= t["bursty_cpu_cv"])
                  or (not _known(ccv) and _known(p2m) and p2m >= t["bursty_peak_to_mean"]))
    return "bursty_interactive" if bursty else "steady_service"
