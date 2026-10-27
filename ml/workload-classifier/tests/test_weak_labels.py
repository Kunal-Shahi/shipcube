import numpy as np
import pytest

pd = pytest.importorskip("pandas")

from shipcube_classifier.features import FEATURES  # noqa: E402
from shipcube_classifier.weak_labels import label, mask_augment  # noqa: E402


def _rows(rows):
    df = pd.DataFrame(rows)
    for f in FEATURES:
        if f not in df:
            df[f] = np.nan
    df["src"] = "test"
    return df


def test_rules_assign_each_class():
    base = dict(cpu_mean_cores=0.3, cpu_p95_cores=0.4, cpu_util_mean=0.3, mem_peak_gb=0.5,
                mem_per_core_gb=1.25, io_per_cpu_s=0.1, cpu_cv=0.2)
    rows = []
    for _ in range(20):
        rows.append({**base, "kind": "batch"})
        rows.append({**base, "kind": "interactive", "cpu_cv": 0.1})
        rows.append({**base, "kind": "interactive", "cpu_cv": 3.0})
    rows.append({**base, "kind": "batch", "mem_peak_gb": 64, "mem_per_core_gb": 160})
    rows.append({**base, "kind": "batch", "cpu_mean_cores": 6, "cpu_util_mean": 0.95})
    rows.append({**base, "kind": "interactive", "io_per_cpu_s": 500, "cpu_util_mean": 0.05})
    rows.append({**base, "kind": None})
    y = label(_rows(rows))
    assert set(y.iloc[:60].dropna()) == {"batch_cron", "steady_service", "bursty_interactive"}
    assert list(y.iloc[60:63]) == ["memory_heavy", "compute_heavy", "io_bound"]
    assert pd.isna(y.iloc[63])  # no side signal -> dropped


def test_single_busy_core_is_not_compute_heavy():
    rows = [dict(kind="batch", cpu_mean_cores=0.95, cpu_p95_cores=1.0, cpu_util_mean=0.95,
                 mem_peak_gb=0.5, mem_per_core_gb=0.5) for _ in range(10)]
    assert set(label(_rows(rows))) == {"batch_cron"}


def test_mask_augment_doubles_rows_and_blanks_some():
    X = pd.DataFrame(np.ones((100, len(FEATURES))), columns=FEATURES)
    y = pd.Series(["batch_cron"] * 100)
    X2, y2 = mask_augment(X, y, copies=1, p=0.3)
    assert len(X2) == len(y2) == 200
    assert X2.iloc[:100].notna().all().all()
    assert 0.2 < X2.iloc[100:].isna().mean().mean() < 0.4
