"""Weak supervision: labelling functions that turn trace rows into workload classes.

No public trace says "this is a memory-heavy service". Each source carries a coarse
*side signal* that isn't in the feature vector:

- Azure Functions: the trigger (timer, http, queue, event, storage, orchestration)
- Azure VMs: vmcategory (Interactive / Delay-insensitive / Unknown)
- Alibaba v2018: offline batch instances vs. online containers
- Google Borg 2019: priority band (free and best-effort batch vs. production)

`kind` is that signal reduced to batch / interactive / io_event / None. The rules
below combine it with *within-source* quantiles of the features, because units differ
between traces (normalised CPU, normalised memory) and absolute thresholds would put
almost every row of one source into one class. Rows with no usable signal are dropped.

Rule order matters: resource-dominant classes win over the scheduling class, so a
nightly job that pins four cores is `compute_heavy`, not `batch_cron`.

The classifier is then trained on the features only. Held-out scores measure how well
it reproduces these rules from features (plus smoothing across sources), not ground
truth. See docs/research/load-estimation.md.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _q(s: pd.Series, q: float) -> float:
    s = s.dropna()
    return float(s.quantile(q)) if len(s) else float("inf")


def label_source(df: pd.DataFrame) -> pd.Series:
    """Label the rows of one source. Needs FEATURES columns plus `kind`."""
    out = pd.Series(pd.NA, index=df.index, dtype="object")
    util = df["cpu_util_mean"]
    has_cpu = util.notna()

    # memory_heavy: lots of memory for the CPU it uses (or, with no CPU signal, top memory).
    mpc = df["mem_per_core_gb"]
    mem_rule = (mpc >= max(_q(mpc, 0.88), 4.0)) & (~has_cpu | (util <= 0.5))
    no_cpu_mem = ~has_cpu & (df["mem_peak_gb"] > _q(df["mem_peak_gb"], 0.92))
    out[mem_rule | no_cpu_mem] = "memory_heavy"

    # compute_heavy: sustained high utilisation of more than one core. A single busy
    # thread (most Python jobs) isn't enough; that's ordinary batch or service work.
    comp = has_cpu & (util >= max(_q(util, 0.85), 0.55)) & (df["cpu_mean_cores"] >= 1.5)
    out[out.isna() & comp] = "compute_heavy"

    # io_bound: much more I/O per CPU-second than its peers while the CPU sits idle,
    # or an event/queue/storage-triggered function that runs long (waiting on I/O).
    ipc = df["io_per_cpu_s"]
    io = ipc.notna() & (ipc > _q(ipc, 0.88)) & (~has_cpu | (util <= 0.4))
    ev = df["kind"].eq("io_event")
    io_ev = ev & (df["duration_s"] >= _q(df.loc[ev, "duration_s"], 0.6))
    out[out.isna() & (io | io_ev)] = "io_bound"

    out[out.isna() & df["kind"].eq("batch")] = "batch_cron"

    # Interactive (and the remaining event-driven rows): bursty vs. steady.
    inter = out.isna() & df["kind"].isin(["interactive", "io_event"])
    burst = df["invocation_cv"].where(df["invocation_cv"].notna(), df["cpu_cv"])
    burst = burst.where(burst.notna(), df["cpu_peak_to_mean"])
    cut = _q(burst[inter], 0.5)
    idle = df["idle_fraction"].fillna(0)
    bursty = inter & ((burst >= cut) | (idle >= 0.6))
    out[bursty] = "bursty_interactive"
    out[inter & ~bursty & burst.notna()] = "steady_service"
    return out


def label(df: pd.DataFrame) -> pd.Series:
    parts = [label_source(g) for _, g in df.groupby("src", sort=False)]
    return pd.concat(parts).reindex(df.index)


def mask_augment(X: pd.DataFrame, y: pd.Series, copies: int = 1, p: float = 0.3,
                 seed: int = 0) -> tuple[pd.DataFrame, pd.Series]:
    """Add copies of each row with ~p of its features blanked out.

    The profiler often lacks features a trace has (and vice versa), so the model
    shouldn't depend on any single feature being present.
    """
    rng = np.random.default_rng(seed)
    xs, ys = [X], [y]
    for _ in range(copies):
        Xc = X.copy()
        mask = rng.random(Xc.shape) < p
        Xc = Xc.mask(mask)
        xs.append(Xc)
        ys.append(y)
    return pd.concat(xs, ignore_index=True), pd.concat(ys, ignore_index=True)
