import json
import math
import random

import pytest

from shipcube_classifier import CLASSES, FEATURES, Model, classify, default_model, derive, empty
from shipcube_classifier.model import ARTEFACT, export_lightgbm

EXAMPLES = {
    "batch_cron": (dict(cpu_mean_cores=0.9, cpu_p95_cores=1.0, cpu_cv=0.2, mem_mean_gb=0.3, mem_peak_gb=0.4,
                        invocations_per_hour=1 / 24, invocation_cv=37.9, idle_fraction=0.999, duration_s=600),
                   "script"),
    "bursty_interactive": (dict(cpu_mean_cores=0.03, cpu_p95_cores=0.3, cpu_cv=3, mem_mean_gb=0.05,
                                mem_peak_gb=0.06, invocations_per_hour=280, invocation_cv=1.4,
                                idle_fraction=0.63, duration_s=0.01), "web"),
    "steady_service": (dict(cpu_mean_cores=0.4, cpu_p95_cores=0.5, cpu_cv=0.1, mem_mean_gb=0.5, mem_peak_gb=0.6,
                            invocations_per_hour=20000, invocation_cv=0.1, idle_fraction=0.0, duration_s=0.05),
                       "web"),
    "memory_heavy": (dict(cpu_mean_cores=0.2, cpu_p95_cores=0.3, cpu_cv=0.3, mem_mean_gb=10, mem_peak_gb=12),
                     "script"),
    "compute_heavy": (dict(cpu_mean_cores=3.6, cpu_p95_cores=4.0, cpu_util_mean=0.9, cpu_cv=0.1,
                           mem_mean_gb=2, mem_peak_gb=2.5, duration_s=1800), "script"),
    "io_bound": (dict(cpu_mean_cores=0.05, cpu_p95_cores=0.1, cpu_util_mean=0.05, mem_mean_gb=0.3,
                      mem_peak_gb=0.4, io_mb_per_s=80, net_mb_per_s=20), "web"),
}


def test_artefact_is_small_and_matches_feature_list():
    assert ARTEFACT.stat().st_size < 3 * 2**20
    m = default_model()
    assert m.classes == CLASSES
    assert len(m.trees) % len(CLASSES) == 0


@pytest.mark.parametrize("expected", list(EXAMPLES))
def test_canonical_examples(expected):
    features, kind = EXAMPLES[expected]
    p = classify(features, kind)
    assert p.workload_class == expected
    assert p.confidence > 0.5


def test_probabilities_sum_to_one_and_all_missing_is_ok():
    p = classify(empty())
    assert set(p.probabilities) == set(CLASSES)
    assert math.isclose(sum(p.probabilities.values()), 1.0, abs_tol=1e-3)


def test_structural_prior_moves_probability_mass():
    f, _ = EXAMPLES["bursty_interactive"]
    assert classify(f, "script").probabilities["bursty_interactive"] < classify(f, "web").probabilities["bursty_interactive"]


def test_derive_fills_ratios():
    d = derive({"cpu_mean_cores": 0.5, "cpu_p95_cores": 1.0, "mem_peak_gb": 4.0, "io_mb_per_s": 5.0})
    assert d["cpu_peak_to_mean"] == 2.0
    assert d["mem_per_core_gb"] == 8.0      # per core of *mean* CPU
    assert d["io_per_cpu_s"] == 10.0
    assert math.isnan(d["duration_s"])


def test_tree_walker_missing_value_semantics():
    nan = float("nan")
    # One split on feature 0 at 1.0; NaN goes right (default_left False) for missing_type NaN.
    tree = {"f": [0], "t": [1.0], "d": [False], "m": [2], "l": [~0], "r": [~1], "v": [-1.0, 1.0]}
    spec = {"classes": CLASSES, "features": FEATURES,
            "trees": [tree] + [{"f": [], "t": [], "d": [], "m": [], "l": [], "r": [], "v": [0.0]}] * 5}
    m = Model(spec)
    x = [nan] * len(FEATURES)
    assert m.raw_scores(x)[0] == 1.0            # NaN -> default (right)
    x[0] = 0.5
    assert m.raw_scores(x)[0] == -1.0           # 0.5 <= 1.0 -> left
    tree["m"] = [0]                             # missing_type None: NaN treated as 0 -> left
    assert Model(spec).raw_scores([nan] * len(FEATURES))[0] == -1.0


def test_export_matches_lightgbm_on_synthetic_data():
    lgb = pytest.importorskip("lightgbm")
    np = pytest.importorskip("numpy")
    rng = np.random.default_rng(0)
    X = rng.normal(size=(600, len(FEATURES)))
    X[rng.random(X.shape) < 0.2] = np.nan
    y = (np.nan_to_num(X[:, 0]) + np.nan_to_num(X[:, 5]) > 0).astype(int) * 2 + (rng.random(600) < 0.3)
    y = y % len(CLASSES)
    b = lgb.train({"objective": "multiclass", "num_class": len(CLASSES), "verbose": -1, "num_leaves": 7},
                  lgb.Dataset(X, y), 20)
    m = Model(export_lightgbm(b))
    # Compare raw scores: predict_proba() also fills derived ratio features, which
    # random synthetic columns don't respect.
    want = b.predict(X[:200], raw_score=True)
    got = np.array([m.raw_scores(row.tolist()) for row in X[:200]])
    assert np.abs(got - want).max() < 1e-5


def test_metrics_report_is_present():
    metrics = json.loads((ARTEFACT.parent / "metrics.json").read_text())
    for key in ("trace_held_out", "leave_one_source_out", "traces_to_profiler", "profiler_holdout",
                "calibration", "low_confidence_gate", "policy_dev_suite"):
        assert key in metrics
    assert metrics["traces_to_profiler"]["macro_f1"] > 0.7
    cm = metrics["confusion_matrix_trace_held_out"]
    assert cm["labels"] == CLASSES and len(cm["rows_true_cols_pred"]) == len(CLASSES)


def test_model_carries_version_and_gate():
    m = default_model()
    manifest = json.loads((ARTEFACT.parent / "manifest.json").read_text())
    assert m.version == manifest["version"] and m.version.startswith("wc-")
    assert m.temperature >= 1.0 and 0.5 <= m.min_confidence <= 1.0
    p = classify(EXAMPLES["batch_cron"][0], "script")
    assert p.model_version == m.version


def test_inference_is_fast():
    import time
    f, kind = EXAMPLES["batch_cron"]
    t = time.perf_counter()
    for _ in range(20):
        classify({k: v * random.uniform(0.9, 1.1) for k, v in f.items()}, kind)
    assert (time.perf_counter() - t) / 20 < 0.05
