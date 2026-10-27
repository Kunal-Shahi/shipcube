"""Turn the sampled traces in data/ into one labelled feature table: data/dataset.csv.

    python scripts/build_dataset.py

Each source is mapped onto shipcube_classifier.FEATURES (missing → NaN), tagged with
`src` and a weak side signal `kind`, then labelled by shipcube_classifier.weak_labels.

Unit assumptions (the traces normalise resources differently; see the research doc):
- Google Borg 2019: 1.0 NCU = 64 vCPU, 1.0 NMU = 256 GiB.
- Alibaba 2018: CPU 100 = 1 core; memory 100 = one machine, assumed 256 GiB;
  disk_io_percent 100 = 200 MiB/s; net_in/out 100 = 10 Gbit/s.
- Azure VMs: core and memory buckets are allocations (">24" → 32, ">64" → 96).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from shipcube_classifier.features import FEATURES  # noqa: E402
from shipcube_classifier.weak_labels import label  # noqa: E402

DATA = ROOT / "data"

BORG_CPU_CORES = 64.0
BORG_MEM_GIB = 256.0
ALI_MEM_GIB_PER_UNIT = 2.56
ALI_DISK_MIB_PER_UNIT = 2.0
ALI_NET_MIB_PER_UNIT = 11.92


def _util(mean: pd.Series, p95: pd.Series) -> pd.Series:
    """Utilisation against an allocation of max(1, ceil(p95)) cores, used whenever the
    trace has no request/limit. The profiler uses the same rule."""
    alloc = np.maximum(1.0, np.ceil(p95.fillna(mean)))
    return (mean / alloc).clip(0, 1)


def _frame(**cols) -> pd.DataFrame:
    df = pd.DataFrame(cols)
    for f in FEATURES:
        if f not in df:
            df[f] = np.nan
    return df


def azure_functions() -> pd.DataFrame:
    d = DATA / "azure_functions"
    if not (d / "invocations.csv").exists():
        return pd.DataFrame()
    inv = pd.read_csv(d / "invocations.csv")
    dur = pd.read_csv(d / "durations.csv")
    mem = pd.read_csv(d / "memory.csv")
    minutes = inv[[str(i) for i in range(1, 1441)]].to_numpy(dtype=float)
    mean = minutes.mean(axis=1)
    std = minutes.std(axis=1)
    keys = ["HashOwner", "HashApp", "HashFunction"]
    base = inv[keys + ["Trigger"]].copy()
    base["invocations_per_hour"] = minutes.sum(axis=1) / 24
    base["invocation_cv"] = np.where(mean > 0, std / np.maximum(mean, 1e-9), np.nan)
    base["idle_fraction"] = (minutes == 0).mean(axis=1)
    base = base.merge(dur[keys + ["Average"]], on=keys, how="left")
    base = base.merge(mem[["HashOwner", "HashApp", "AverageAllocatedMb", "AverageAllocatedMb_pct99"]],
                      on=["HashOwner", "HashApp"], how="left")
    kind = base["Trigger"].map({"timer": "batch", "http": "interactive", "queue": "io_event",
                                "event": "io_event", "storage": "io_event",
                                "orchestration": "interactive"})
    df = _frame(
        invocations_per_hour=base["invocations_per_hour"],
        invocation_cv=base["invocation_cv"],
        idle_fraction=base["idle_fraction"],
        duration_s=base["Average"] / 1000.0,
        mem_mean_gb=base["AverageAllocatedMb"] / 1024.0,
        mem_peak_gb=base["AverageAllocatedMb_pct99"] / 1024.0,
    )
    df["src"], df["kind"], df["side"] = "azure_functions", kind, base["Trigger"]
    return df


def azure_vms() -> pd.DataFrame:
    f = DATA / "azure_vm" / "vmtable.csv"
    if not f.exists():
        return pd.DataFrame()
    v = pd.read_csv(f)
    cores = pd.to_numeric(v["vmcorecountbucket"].replace(">24", "32"), errors="coerce")
    mem = pd.to_numeric(v["vmmemorybucket"].replace(">64", "96"), errors="coerce")
    mean = v["avgcpu"] / 100 * cores
    p95 = v["p95maxcpu"] / 100 * cores
    df = _frame(
        cpu_mean_cores=mean,
        cpu_p95_cores=p95,
        cpu_util_mean=(v["avgcpu"] / 100).clip(0, 1),
        cpu_peak_to_mean=v["maxcpu"] / v["avgcpu"].clip(lower=0.1),
        mem_peak_gb=mem,
        mem_per_core_gb=mem / np.maximum(p95, 0.05),
        duration_s=(v["vmdeleted"] - v["vmcreated"]).astype(float),
    )
    df["src"] = "azure_vm"
    df["kind"] = v["vmcategory"].map({"Delay-insensitive": "batch", "Interactive": "interactive"})
    df["side"] = v["vmcategory"]
    return df


def alibaba_batch() -> pd.DataFrame:
    f = DATA / "alibaba" / "batch_instance.csv"
    if not f.exists():
        return pd.DataFrame()
    b = pd.read_csv(f, usecols=["task_name", "job_name", "status", "start_time", "end_time",
                                "cpu_avg", "cpu_max", "mem_avg", "mem_max"])
    b = b[(b["status"] == "Terminated") & (b["end_time"] > b["start_time"])].copy()
    b["dur"] = b["end_time"] - b["start_time"]
    g = b.groupby(["job_name", "task_name"]).agg(
        cpu_mean=("cpu_avg", "mean"), cpu_std=("cpu_avg", "std"), cpu_p95=("cpu_max", "median"),
        cpu_peak=("cpu_max", "max"), mem_mean=("mem_avg", "mean"), mem_peak=("mem_max", "max"),
        dur=("dur", "median"), n=("dur", "size"))
    g = g[g["cpu_mean"] > 0]
    mean, p95 = g["cpu_mean"] / 100, g["cpu_p95"] / 100
    df = _frame(
        cpu_mean_cores=mean,
        cpu_p95_cores=p95,
        cpu_util_mean=_util(mean, p95),
        cpu_cv=g["cpu_std"] / g["cpu_mean"],
        cpu_peak_to_mean=g["cpu_peak"] / g["cpu_mean"],
        mem_mean_gb=g["mem_mean"] * ALI_MEM_GIB_PER_UNIT,
        mem_peak_gb=g["mem_peak"] * ALI_MEM_GIB_PER_UNIT,
        duration_s=g["dur"].astype(float),
    ).reset_index(drop=True)
    df["src"], df["kind"], df["side"] = "alibaba_batch", "batch", "batch_instance"
    return df


def alibaba_online() -> pd.DataFrame:
    fu, fm = DATA / "alibaba" / "container_usage.csv", DATA / "alibaba" / "container_meta.csv"
    if not fu.exists():
        return pd.DataFrame()
    u = pd.read_csv(fu).dropna(subset=["container_id"])
    meta = (pd.read_csv(fm).sort_values("time_stamp")
            .groupby("container_id")[["cpu_request", "mem_size"]].last())
    u = u.join(meta, on="container_id", how="inner")
    cores = u["cpu_request"] / 100
    u["cpu"] = u["cpu_util_percent"] / 100 * cores
    u["mem"] = u["mem_util_percent"] / 100 * u["mem_size"] * ALI_MEM_GIB_PER_UNIT
    u["disk"] = u["disk_io_percent"] * ALI_DISK_MIB_PER_UNIT
    u["net"] = (u["net_in"] + u["net_out"]) * ALI_NET_MIB_PER_UNIT
    u["idle"] = u["cpu_util_percent"] < 1
    g = u.groupby("container_id").agg(
        cpu_mean=("cpu", "mean"), cpu_std=("cpu", "std"), cpu_p95=("cpu", lambda s: s.quantile(0.95)),
        util=("cpu_util_percent", "mean"), mem_mean=("mem", "mean"), mem_peak=("mem", "max"),
        disk=("disk", "mean"), net=("net", "mean"), idle=("idle", "mean"), n=("cpu", "size"))
    g = g[(g["n"] >= 30) & (g["cpu_mean"] > 0)]
    df = _frame(
        cpu_mean_cores=g["cpu_mean"],
        cpu_p95_cores=g["cpu_p95"],
        cpu_util_mean=(g["util"] / 100).clip(0, 1),
        cpu_cv=g["cpu_std"] / g["cpu_mean"],
        cpu_peak_to_mean=g["cpu_p95"] / g["cpu_mean"],
        mem_mean_gb=g["mem_mean"],
        mem_peak_gb=g["mem_peak"],
        io_mb_per_s=g["disk"],
        net_mb_per_s=g["net"],
        idle_fraction=g["idle"],
    ).reset_index(drop=True)
    df["src"], df["kind"], df["side"] = "alibaba_online", "interactive", "container"
    return df


def google_borg() -> pd.DataFrame:
    f = DATA / "google_borg" / "instances.csv"
    if not f.exists():
        return pd.DataFrame()
    g = pd.read_csv(f)
    g = g[g["cpu_mean"] > 0]
    mean, p95 = g["cpu_mean"] * BORG_CPU_CORES, g["cpu_p95"] * BORG_CPU_CORES
    df = _frame(
        cpu_mean_cores=mean,
        cpu_p95_cores=p95,
        cpu_util_mean=_util(mean, p95),
        cpu_cv=g["cpu_std"] / g["cpu_mean"],
        cpu_peak_to_mean=g["cpu_max"] / g["cpu_mean"],
        mem_mean_gb=g["mem_mean"] * BORG_MEM_GIB,
        mem_peak_gb=g["mem_max"] * BORG_MEM_GIB,
    ).reset_index(drop=True)
    pr = g["priority"].reset_index(drop=True)
    # Priority bands from the 2019 trace docs: free ≤99, best-effort batch 110–115,
    # mid 116–119, production 120–359, monitoring ≥360.
    df["kind"] = np.select([pr <= 115, (pr >= 120)], ["batch", "interactive"], default=None)
    df["kind"] = df["kind"].replace({None: np.nan})
    df["src"] = "google_borg"
    df["side"] = "priority_" + pr.astype(str)
    return df


def main() -> None:
    parts = [p() for p in (azure_functions, azure_vms, alibaba_batch, alibaba_online, google_borg)]
    df = pd.concat([p for p in parts if len(p)], ignore_index=True)
    df = df.replace([np.inf, -np.inf], np.nan)
    # Fill ratio features the sources didn't compute directly.
    m = df["mem_per_core_gb"].isna()
    cpu = df["cpu_p95_cores"].fillna(df["cpu_mean_cores"])
    df.loc[m, "mem_per_core_gb"] = df["mem_peak_gb"].fillna(df["mem_mean_gb"]) / np.maximum(cpu, 0.05)
    io = df[["io_mb_per_s", "net_mb_per_s"]].sum(axis=1, min_count=1)
    df["io_per_cpu_s"] = df["io_per_cpu_s"].fillna(io / np.maximum(df["cpu_mean_cores"], 0.01))
    df["label"] = label(df)
    print(pd.crosstab(df["src"], df["label"].fillna("(dropped)")))
    df = df.dropna(subset=["label"])
    out = DATA / "dataset.csv"
    df[["src", "side", "kind", *FEATURES, "label"]].to_csv(out, index=False)
    print(f"{len(df)} labelled rows -> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
