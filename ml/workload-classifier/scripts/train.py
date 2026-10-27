"""Train the workload classifier, evaluate transfer, calibrate, and export a versioned model.

    python scripts/train.py

Inputs:
  data/dataset.csv        trace rows (scripts/build_dataset.py)
  calibration/runs.csv    profiler runs of the calibration suite (scripts/calibrate.py)
Outputs (src/shipcube_classifier/artefacts/):
  model.json              compact trees + temperature + min_confidence + version
  manifest.json           version, sha256 of model.json, metrics summary
  metrics.json            full evaluation report

Evaluation, from most to least optimistic:
  1. trace held-out split (random 20% of trace rows)
  2. leave one trace source out (train on 4 traces + profiler runs, test on the 5th)
  3. trace -> profiler transfer (train on traces only, test on profiled apps)
Number 3 is the closest to "a new user app": the model never sees a profiled app in
training. The confidence temperature and the low-confidence threshold are fitted on
those predictions (temperature cross-fitted by app family for the reported ECE).
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from shipcube_classifier.features import CLASSES, FEATURES  # noqa: E402
from shipcube_classifier.model import STRUCTURAL_PRIOR, Model, export_lightgbm  # noqa: E402
from shipcube_classifier.rules import THRESHOLDS, rule_class  # noqa: E402
from shipcube_classifier.weak_labels import mask_augment  # noqa: E402

ART = ROOT / "src" / "shipcube_classifier" / "artefacts"
SEED = 7
ROUNDS = 220
TARGET_PRECISION = 0.85    # min_confidence keeps accuracy above this on profiled apps
MIN_CONFIDENCE_FLOOR = 0.5
PARAMS = dict(objective="multiclass", num_class=len(CLASSES), learning_rate=0.08,
              num_leaves=15, min_data_in_leaf=20, feature_fraction=0.8,
              bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, verbose=-1, seed=SEED)


def weights(df: pd.DataFrame) -> np.ndarray:
    """Class-balanced within each source, and each source weighs the same in total, so
    no single trace (and its units) dominates."""
    w = np.zeros(len(df))
    sources = df["src"].unique()
    for s in sources:
        m = (df["src"] == s).to_numpy()
        y = df.loc[m, "label"]
        cw = y.map(1.0 / y.value_counts()).to_numpy() / y.nunique()
        w[m] = cw
    return w * len(df) / w.sum()


def fit(df: pd.DataFrame, rounds: int = ROUNDS) -> lgb.Booster:
    X, y = df[FEATURES], df["label"].map(CLASSES.index)
    w = weights(df)
    Xa, ya = mask_augment(X.reset_index(drop=True), y.reset_index(drop=True), copies=1, p=0.3, seed=SEED)
    wa = np.concatenate([w, w])
    return lgb.train(PARAMS, lgb.Dataset(Xa, ya, weight=wa), rounds)


def apply_prior(p: np.ndarray, kinds: pd.Series) -> np.ndarray:
    p = p.copy()
    for i, k in enumerate(kinds):
        if k in STRUCTURAL_PRIOR:
            p[i] *= np.array([STRUCTURAL_PRIOR[k][c] for c in CLASSES])
    return p / p.sum(axis=1, keepdims=True)


def summary(y, p: np.ndarray) -> dict:
    pred = [CLASSES[i] for i in p.argmax(axis=1)]
    present = [c for c in CLASSES if c in set(y)]
    return {"n": int(len(y)), "accuracy": round(accuracy_score(y, pred), 4),
            "macro_f1": round(f1_score(y, pred, labels=present, average="macro", zero_division=0), 4),
            "per_class_f1": {c: round(v, 4) for c, v in zip(
                present, f1_score(y, pred, labels=present, average=None, zero_division=0))}}


def fit_temperature(raw: np.ndarray, y_idx: np.ndarray, prior: np.ndarray) -> float:
    """Temperature on raw scores (before the prior) minimising NLL of the final probs."""
    best, best_t = np.inf, 1.0
    for t in np.exp(np.linspace(np.log(0.25), np.log(8), 70)):
        z = raw / t
        p = np.exp(z - z.max(axis=1, keepdims=True)) * prior
        p /= p.sum(axis=1, keepdims=True)
        nll = -np.log(p[np.arange(len(y_idx)), y_idx] + 1e-12).mean()
        if nll < best:
            best, best_t = nll, float(t)
    return round(best_t, 3)


def ece(conf: np.ndarray, correct: np.ndarray, bins: int = 5) -> float:
    edges = np.linspace(0, 1, bins + 1)
    e = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (conf > lo) & (conf <= hi)
        if m.any():
            e += m.mean() * abs(conf[m].mean() - correct[m].mean())
    return round(float(e), 4)


def main() -> None:
    traces = pd.read_csv(ROOT / "data" / "dataset.csv")
    prof = pd.read_csv(ROOT / "calibration" / "runs.csv")
    prof = prof[prof["ran_ok"]].reset_index(drop=True)
    cols = ["src", "label", *FEATURES]
    allrows = pd.concat([traces[cols], prof[cols]], ignore_index=True)
    rng = np.random.default_rng(SEED)

    # 1. Trace held-out split.
    test_mask = rng.random(len(traces)) < 0.2
    b = fit(traces[~test_mask][cols])
    te = traces[test_mask]
    p_te = b.predict(te[FEATURES])
    held = summary(te["label"], p_te)
    cm = confusion_matrix(te["label"], [CLASSES[i] for i in p_te.argmax(1)], labels=CLASSES)

    # 2. Leave one trace source out.
    loso = {}
    for s in sorted(traces["src"].unique()):
        b = fit(traces[traces["src"] != s][cols])
        t = traces[traces["src"] == s]
        loso[s] = {k: v for k, v in summary(t["label"], b.predict(t[FEATURES])).items() if k != "per_class_f1"}

    # 3. Traces -> profiled apps. The final model is trained on traces only, so every
    # profiled app is out of domain and unseen; this is the headline number.
    # (Adding profiler runs to training was tried: with one app family held out,
    # macro-F1 fell to ~0.65, because the model learns "a profiled script that isn't
    # batch is compute/memory/io" shortcuts from the other families.)
    final = fit(traces[cols])
    raw = final.predict(prof[FEATURES], raw_score=True)
    pri = np.array([[STRUCTURAL_PRIOR[k][c] for c in CLASSES] for k in prof["kind"]])
    y_idx = prof["label"].map(CLASSES.index).to_numpy()

    def probs(r, t, prior=pri):
        z = r / t
        p = np.exp(z - z.max(axis=1, keepdims=True)) * prior
        return p / p.sum(axis=1, keepdims=True)

    p1 = probs(raw, 1.0)

    # Temperature: cross-fitted by app family for an honest ECE, then fitted on all.
    family = prof["app"].str.split("-").str[0]
    conf_cf = np.zeros(len(prof))
    for fam in family.unique():
        m = (family == fam).to_numpy()
        t = fit_temperature(raw[~m], y_idx[~m], pri[~m])
        conf_cf[m] = probs(raw[m], t, pri[m]).max(axis=1)
    T_fit = fit_temperature(raw, y_idx, pri)
    correct1 = p1.argmax(axis=1) == y_idx
    # conf_cf's argmax can differ from p1's; score each against its own predictions.
    pred_cf = np.zeros(len(prof), dtype=int)
    for fam in family.unique():
        m = (family == fam).to_numpy()
        pred_cf[m] = probs(raw[m], fit_temperature(raw[~m], y_idx[~m], pri[~m]), pri[m]).argmax(axis=1)
    ece_none, ece_cf = ece(p1.max(1), correct1), ece(conf_cf, pred_cf == y_idx)
    # Keep the temperature only if it helps out of sample (the dev suite is small), and
    # never below 1: sharpening on a few dozen runs made held-out mistakes look certain.
    T = max(1.0, T_fit) if ece_cf < ece_none else 1.0
    p_cal = probs(raw, T)
    conf = p_cal.max(axis=1)
    correct = p_cal.argmax(axis=1) == y_idx
    transfer = summary(prof["label"], p_cal)
    calib = {"temperature": T, "temperature_fitted": T_fit, "ece_uncalibrated": ece_none,
             "ece_temperature_cross_fitted": ece_cf, "ece_final_in_sample": ece(conf, correct)}

    # Lowest threshold whose accepted predictions are at least TARGET_PRECISION accurate.
    min_conf = 1.0
    for tau in np.round(np.arange(0.30, 0.96, 0.01), 2):
        m = conf >= tau
        if m.sum() >= 5 and correct[m].mean() >= TARGET_PRECISION:
            min_conf = float(tau)
            break
    # Policy floor: never act on a class the model gives less than even odds.
    min_conf = max(min_conf, MIN_CONFIDENCE_FLOOR)
    accepted = conf >= min_conf
    gate = {"min_confidence": min_conf,
            "coverage": round(float(accepted.mean()), 4),
            "accuracy_above": round(float(correct[accepted].mean()), 4) if accepted.any() else None,
            "accuracy_below": round(float(correct[~accepted].mean()), 4) if (~accepted).any() else None}

    def decide(df, p):
        """Runtime policy: the model decides when it clears the confidence gate; below
        it the rules decide and the estimate is flagged low-confidence."""
        rules = [rule_class({k: r[k] for k in FEATURES}, r["kind"]) for _, r in df.iterrows()]
        model = [CLASSES[i] for i in p.argmax(axis=1)]
        flag = list(p.max(axis=1) < min_conf)
        final = [r if f else m for m, r, f in zip(model, rules, flag)]
        y = df["label"].tolist()

        def acc(pred, mask=None):
            pairs = [(a, b) for a, b, k in zip(pred, y, mask or [True] * len(y)) if k]
            return round(float(np.mean([a == b for a, b in pairs])), 4) if pairs else None
        agree = [m == r for m, r in zip(model, rules)]
        return {"model_accuracy": acc(model), "rules_accuracy": acc(rules), "policy_accuracy": acc(final),
                "flagged_rate": round(float(np.mean(flag)), 4),
                "model_rules_disagree_rate": round(1 - float(np.mean(agree)), 4),
                "model_accuracy_when_disagreeing": acc(model, [not a for a in agree])}

    policy_dev = decide(prof, p_cal)

    # 5. Held-out suite: different programs, never used to tune anything.
    hold = pd.read_csv(ROOT / "calibration" / "holdout.csv")
    hold = hold[hold["ran_ok"]].reset_index(drop=True)
    pri_h = np.array([[STRUCTURAL_PRIOR[k][c] for c in CLASSES] for k in hold["kind"]])
    p_h = probs(final.predict(hold[FEATURES], raw_score=True), T, pri_h)
    holdout = summary(hold["label"], p_h)
    holdout["low_confidence_rate"] = round(float((p_h.max(axis=1) < min_conf).mean()), 4)
    holdout["policy"] = decide(hold, p_h)
    holdout["predictions"] = [
        {"app": a, "label": l, "predicted": CLASSES[i], "confidence": round(float(c), 3)}
        for a, l, i, c in zip(hold["app"], hold["label"], p_h.argmax(axis=1), p_h.max(axis=1))]

    # Export the trace-only model.
    gain = dict(zip(FEATURES, final.feature_importance("gain").round(1).tolist()))
    spec = export_lightgbm(final, meta={})
    spec["temperature"] = T
    spec["min_confidence"] = min_conf
    payload = json.dumps({k: spec[k] for k in ("classes", "features", "trees", "temperature", "min_confidence")},
                         separators=(",", ":"), sort_keys=True).encode()
    version = f"wc-{date.today():%Y%m%d}-{hashlib.sha256(payload).hexdigest()[:8]}"
    spec["meta"] = {"version": version, "trained_on": str(date.today()),
                    "trace_held_out_macro_f1": held["macro_f1"]}
    body = json.dumps(spec, separators=(",", ":")).encode()
    ART.mkdir(parents=True, exist_ok=True)
    (ART / "model.json").write_bytes(body)

    metrics = {
        "version": version,
        "trained_on": str(date.today()),
        "rows": {"traces": int(len(traces)), "profiler_runs": int(len(prof)),
                 "profiler_apps": int(prof["app"].nunique())},
        "rows_by_source": allrows["src"].value_counts().to_dict(),
        "label_counts": allrows["label"].value_counts().to_dict(),
        "thresholds": THRESHOLDS,
        "params": PARAMS, "rounds": ROUNDS,
        "trace_held_out": held,
        "leave_one_source_out": loso,
        "traces_to_profiler": transfer,
        "policy_dev_suite": policy_dev,
        "profiler_holdout": holdout,
        "calibration": calib,
        "low_confidence_gate": gate,
        "confusion_matrix_trace_held_out": {"labels": CLASSES, "rows_true_cols_pred": cm.tolist()},
        "confusion_matrix_profiler": {"labels": CLASSES, "rows_true_cols_pred": confusion_matrix(
            prof["label"], [CLASSES[i] for i in p_cal.argmax(1)], labels=CLASSES).tolist()},
        "feature_gain": gain,
    }
    (ART / "metrics.json").write_text(json.dumps(metrics, indent=2))
    manifest = {"name": "workload-classifier", "version": version, "file": "model.json",
                "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body),
                "created": str(date.today()), "temperature": T, "min_confidence": min_conf,
                "metrics": {"trace_held_out_macro_f1": held["macro_f1"],
                            "loso_macro_f1": {k: v["macro_f1"] for k, v in loso.items()},
                            "traces_to_profiler_macro_f1": transfer["macro_f1"],
                            "profiler_holdout_accuracy": holdout["accuracy"],
                            "profiler_holdout_policy_accuracy": holdout["policy"]["policy_accuracy"]}}
    (ART / "manifest.json").write_text(json.dumps(manifest, indent=2))

    # Parity: exported pure-Python model vs LightGBM raw scores.
    py = Model(spec)
    sample = allrows.sample(min(300, len(allrows)), random_state=SEED)[FEATURES].to_numpy(dtype=float)
    diff = np.abs(final.predict(sample, raw_score=True) - np.array([py.raw_scores(r.tolist()) for r in sample])).max()

    print(f"version {version}  ({len(body) / 1024:.0f} KB, {len(spec['trees'])} trees, max |raw diff| {diff:.1e})")
    print(f"trace held-out        acc {held['accuracy']}  macro-F1 {held['macro_f1']}")
    print("leave one source out ", {k: v["macro_f1"] for k, v in loso.items()})
    print(f"traces -> profiler    acc {transfer['accuracy']}  macro-F1 {transfer['macro_f1']}")
    print(f"calibration           {calib}")
    print(f"low-confidence gate   {gate}")
    print(f"held-out apps         acc {holdout['accuracy']}  macro-F1 {holdout['macro_f1']}  "
          f"low-confidence {holdout['low_confidence_rate']}")
    print(f"policy (dev / held-out) {policy_dev} / {holdout['policy']}")
    for r in holdout["predictions"]:
        print(f"   {r['app']:10s} {r['label']:20s} -> {r['predicted']:20s} {r['confidence']}")


if __name__ == "__main__":
    main()
