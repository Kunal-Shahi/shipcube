"""Profile the calibration suite with the real profiler and save target-domain rows.

    SHIPCUBE_DEV_MODE=1 python scripts/calibrate.py [--repeat 2]

Writes calibration/runs.csv (features + intended label + kind). The file is small,
generated from our own apps, and committed so training is reproducible without
re-running the profiler. Needs shipcube-profiler and flask installed.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from calibration.suite import apps, holdout_apps  # noqa: E402
from shipcube_classifier.features import FEATURES  # noqa: E402
from shipcube_classifier.weak_labels import label_source  # noqa: E402
from shipcube_profiler import Hints, Limits, LocalRunner, materialize, summarize, to_features  # noqa: E402

OUT = {"dev": ROOT / "calibration" / "runs.csv", "holdout": ROOT / "calibration" / "holdout.csv"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=2, help="runs per app (measurements vary)")
    ap.add_argument("--only", help="substring filter on app names")
    ap.add_argument("--suite", choices=OUT, default="dev")
    a = ap.parse_args()
    runner = LocalRunner(Limits(timeout_s=90, memory_mb=4096))
    rows = []
    for app in (apps() if a.suite == "dev" else holdout_apps()):
        if a.only and a.only not in app.name:
            continue
        for rep in range(a.repeat):
            with tempfile.TemporaryDirectory() as d:
                root = materialize(app.source(), Path(d))
                s = summarize(root)
                h = Hints.from_any(app.hints)
                p = runner.run(root, s, h)
                f, _ = to_features(p, h, api_only=s.api_only)
            ok = (p.requests.ok > 0) if s.kind == "web" else (p.exit_code == 0)
            rows.append({"app": app.name, "rep": rep, "src": "profiler", "kind": s.kind,
                         "label": app.label, "ran_ok": ok, **f})
            print(f"{app.name:12s} rep{rep} ok={ok} cpu={f['cpu_mean_cores']:.2f}/{f['cpu_p95_cores']:.2f} "
                  f"mem={f['mem_peak_gb']:.2f}G io/cpu={f['io_per_cpu_s']:.1f} idle={f['idle_fraction']:.2f}")
    df = pd.DataFrame(rows)
    # Same rules as the traces, with `kind` from the code structure. Disagreement
    # means the app didn't behave as designed on this machine; those rows are kept
    # but reported.
    kind = df["kind"].map({"web": "interactive", "script": "batch"})
    df["rule_label"] = label_source(df.assign(kind=kind))
    agree = (df["rule_label"] == df["label"]).mean()
    print(f"\n{len(df)} runs; intended label == rule label for {agree:.0%}")
    print(df.loc[df["rule_label"] != df["label"], ["app", "label", "rule_label"]].to_string())
    df[["app", "rep", "src", "kind", "label", "rule_label", "ran_ok", *FEATURES]].to_csv(OUT[a.suite], index=False)
    print(f"-> {OUT[a.suite].relative_to(ROOT)}")


if __name__ == "__main__":
    main()
