"""Download small samples of public cluster traces into ml/workload-classifier/data/.

Nothing here is committed: data/ is gitignored. Every source is *streamed* and we stop
reading as soon as we have enough rows, so we never pull the multi-GB archives.

    python scripts/sample_traces.py                # all sources
    python scripts/sample_traces.py --only alibaba # one source
    python scripts/sample_traces.py --skip google  # no BigQuery (needs gcloud auth)

Sources and licences are listed in docs/research/load-estimation.md.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import random
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"
SEED = 7

AZURE_FUNCTIONS = (
    "https://github.com/Azure/AzurePublicDataset/releases/download/"
    "dataset-functions-2019/azurefunctions_dataset2019_azurefunctions-dataset2019.tar.xz"
)
AZURE_VMTABLE = (
    "https://github.com/Azure/AzurePublicDataset/releases/download/"
    "dataset-v2/trace_data_vmtable_vmtable.csv.gz"
)
ALIBABA = "http://aliopentrace.oss-cn-beijing.aliyuncs.com/v2018Traces/{name}.tar.gz"

# Google cluster-data 2019, cell "a". TABLESAMPLE keeps the bytes billed small (the
# Oct 2026 run billed 0.8 GB), but bq checks --maximum_bytes_billed against a
# worst-case full-column scan *before* running, so the cap has to be set high
# (~400 GB) for the job to start. It's a guard, not the expected cost. Read as few
# columns as possible: every extra column raises that worst case by ~90 GB.
BORG_SQL = """
WITH usage AS (
  SELECT collection_id, instance_index,
    COUNT(*) AS windows,
    AVG(average_usage.cpus) AS cpu_mean, STDDEV(average_usage.cpus) AS cpu_std,
    MAX(maximum_usage.cpus) AS cpu_max, APPROX_QUANTILES(average_usage.cpus, 20)[OFFSET(19)] AS cpu_p95,
    AVG(average_usage.memory) AS mem_mean, MAX(average_usage.memory) AS mem_max
  FROM `google.com:google-cluster-data`.clusterdata_2019_a.instance_usage
    TABLESAMPLE SYSTEM (0.05 PERCENT)
  GROUP BY 1, 2
),
coll AS (
  SELECT collection_id, MAX(priority) AS priority, MAX(scheduling_class) AS scheduling_class,
    MAX(collection_type) AS collection_type
  FROM `google.com:google-cluster-data`.clusterdata_2019_a.collection_events
  GROUP BY 1
)
SELECT u.*, c.priority, c.scheduling_class, c.collection_type
FROM usage u JOIN coll c USING (collection_id)
WHERE u.windows >= 3
ORDER BY FARM_FINGERPRINT(CONCAT(CAST(u.collection_id AS STRING), '-', CAST(u.instance_index AS STRING)))
LIMIT 30000
"""


def _open(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "shipcube-trace-sampler"})
    return urllib.request.urlopen(req, timeout=60)


def _write(path: Path, header: list[str], rows) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)
            n += 1
    print(f"  wrote {n} rows -> {path.relative_to(DATA.parent)}")
    return n


def _lines(f, chunk: int = 1 << 20):
    """Decode a tar member line by line. Streamed tar members aren't seekable, which
    io.TextIOWrapper needs, so split chunks by hand."""
    buf = b""
    while True:
        block = f.read(chunk)
        if not block:
            break
        buf += block
        *lines, buf = buf.split(b"\n")
        for line in lines:
            yield line.decode("utf-8")
    if buf:
        yield buf.decode("utf-8")


def _reservoir(rows, k: int, rng: random.Random) -> list:
    out: list = []
    for i, r in enumerate(rows):
        if i < k:
            out.append(r)
        else:
            j = rng.randint(0, i)
            if j < k:
                out[j] = r
    return out


def azure_functions(n_functions: int = 12000) -> None:
    """Day 1 of the 2019 Functions trace: invocations per minute, durations, app memory."""
    print("Azure Functions 2019 (streaming tar.xz, stops after day-1 files)")
    wanted = {
        "invocations_per_function_md.anon.d01.csv": "invocations.csv",
        "function_durations_percentiles.anon.d01.csv": "durations.csv",
        "app_memory_percentiles.anon.d01.csv": "memory.csv",
    }
    out = DATA / "azure_functions"
    rng = random.Random(SEED)
    with _open(AZURE_FUNCTIONS) as resp, tarfile.open(fileobj=resp, mode="r|xz") as tar:
        for member in tar:
            name = Path(member.name).name
            if name not in wanted:
                continue
            f = tar.extractfile(member)
            assert f is not None
            reader = csv.reader(_lines(f))
            header = next(reader)
            rows = list(reader)
            if name.startswith("invocations"):
                # Keep functions with at least one call so the sample isn't all empty rows.
                rows = [r for r in rows if any(v not in ("0", "") for v in r[4:])]
                rows = _reservoir(rows, n_functions, rng)
            _write(out / wanted.pop(name), header, rows)
            if not wanted:
                break
    if wanted:
        print(f"  missing from archive: {sorted(wanted)}", file=sys.stderr)


def azure_vms(scan_rows: int = 400_000, keep: int = 20000) -> None:
    """vmtable.csv.gz: per-VM avg/max/p95 CPU, category (Interactive / Delay-insensitive)."""
    print(f"Azure VM 2019 vmtable (first {scan_rows} rows of the gzip stream)")
    header = ["vmid", "subscriptionid", "deploymentid", "vmcreated", "vmdeleted", "maxcpu",
              "avgcpu", "p95maxcpu", "vmcategory", "vmcorecountbucket", "vmmemorybucket"]
    rng = random.Random(SEED)
    with _open(AZURE_VMTABLE) as resp, gzip.open(resp, "rt") as f:
        def head():
            for i, r in enumerate(csv.reader(f)):
                if i >= scan_rows:
                    return
                yield r
        rows = _reservoir(head(), keep, rng)
    _write(DATA / "azure_vm" / "vmtable.csv", header, rows)


def _tar_csv_head(url: str, max_rows: int):
    with _open(url) as resp, tarfile.open(fileobj=resp, mode="r|gz") as tar:
        for member in tar:
            f = tar.extractfile(member)
            if f is None:
                continue
            for i, r in enumerate(csv.reader(_lines(f))):
                if i >= max_rows:
                    return
                yield r
            return


def alibaba(instance_rows: int = 1_500_000, usage_rows: int = 3_000_000) -> None:
    """cluster-trace-v2018: batch instances (offline) and container usage (online services)."""
    print("Alibaba cluster-trace-v2018 (streaming heads of the tar.gz files)")
    out = DATA / "alibaba"
    _write(out / "batch_instance.csv",
           ["instance_name", "task_name", "job_name", "task_type", "status", "start_time",
            "end_time", "machine_id", "seq_no", "total_seq_no", "cpu_avg", "cpu_max",
            "mem_avg", "mem_max"],
           _tar_csv_head(ALIBABA.format(name="batch_instance"), instance_rows))
    _write(out / "container_meta.csv",
           ["container_id", "machine_id", "time_stamp", "app_du", "status", "cpu_request",
            "cpu_limit", "mem_size"],
           _tar_csv_head(ALIBABA.format(name="container_meta"), 10_000_000))
    _write(out / "container_usage.csv",
           ["container_id", "machine_id", "time_stamp", "cpu_util_percent", "mem_util_percent",
            "cpi", "mem_gps", "mpki", "net_in", "net_out", "disk_io_percent"],
           _tar_csv_head(ALIBABA.format(name="container_usage"), usage_rows))


def google_borg() -> None:
    print("Google cluster-data 2019 (BigQuery, TABLESAMPLE 0.05%)")
    out = DATA / "google_borg" / "instances.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["bq", "query", "--use_legacy_sql=false", "--format=csv", "--max_rows=30000",
           "--maximum_bytes_billed=400000000000", BORG_SQL]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    out.write_text(res.stdout)
    print(f"  wrote {res.stdout.count(chr(10)) - 1} rows -> {out.relative_to(DATA.parent)}")


SOURCES = {"azure_functions": azure_functions, "azure_vm": azure_vms,
           "alibaba": alibaba, "google": google_borg}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--only", choices=SOURCES, action="append")
    p.add_argument("--skip", choices=SOURCES, action="append", default=[])
    a = p.parse_args()
    for name in a.only or SOURCES:
        if name in a.skip:
            continue
        try:
            SOURCES[name]()
        except Exception as e:  # keep going: each source is optional
            print(f"  {name} failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
