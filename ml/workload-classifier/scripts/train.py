"""Train the LightGBM workload classifier and export it for pure-Python inference.

    python scripts/train.py

Reads data/dataset.csv (scripts/build_dataset.py), writes
src/shipcube_classifier/artefacts/model.json and metrics.json.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score
from sklearn.model_selection import train_test_split

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from shipcube_classifier.features import CLASSES, FEATURES  # noqa: E402
from shipcube_classifier.model import Model, export_lightgbm  # noqa: E402
from shipcube_classifier.weak_labels import mask_augment  # noqa: E402

ART = ROOT / "src" / "shipcube_classifier" / "artefacts"
SEED = 7
PARAMS = dict(objective="multiclass", num_class=len(CLASSES), learning_rate=0.08,
              num_leaves=15, min_data_in_leaf=40, feature_fraction=0.8,
              bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, verbose=-1, seed=SEED)


def _weights(y: pd.Series) -> np.ndarray:
    counts = y.value_counts()
    w = len(y) / (len(counts) * counts)
    return y.map(w).to_numpy()


def fit(X: pd.DataFrame, y: pd.Series, rounds: int = 400) -> lgb.Booster:
    yi = y.map(CLASSES.index)
    Xt, Xv, yt, yv = train_test_split(X, yi, test_size=0.15, stratify=yi, random_state=SEED)
    Xt, yt = mask_augment(Xt, yt, copies=1, p=0.3, seed=SEED)
    dt = lgb.Dataset(Xt, yt, weight=_weights(yt))
    dv = lgb.Dataset(Xv, yv, weight=_weights(yv), reference=dt)
    return lgb.train(PARAMS, dt, rounds, valid_sets=[dv],
                     callbacks=[lgb.early_stopping(30, verbose=False)])


def scores(booster: lgb.Booster, X: pd.DataFrame, y: pd.Series) -> dict:
    pred = [CLASSES[i] for i in booster.predict(X).argmax(axis=1)]
    present = [c for c in CLASSES if c in set(y)]
    return {
        "n": int(len(y)),
        "accuracy": round(accuracy_score(y, pred), 4),
        "macro_f1": round(f1_score(y, pred, labels=present, average="macro"), 4),
        "per_class_f1": {c: round(v, 4) for c, v in
                         zip(present, f1_score(y, pred, labels=present, average=None))},
        "_pred": pred,
    }


def main() -> None:
    df = pd.read_csv(ROOT / "data" / "dataset.csv")
    X, y = df[FEATURES], df["label"]
    Xtr, Xte, ytr, yte, src_tr, src_te = train_test_split(
        X, y, df["src"], test_size=0.2, stratify=y, random_state=SEED)

    booster = fit(Xtr, ytr)
    test = scores(booster, Xte, yte)
    cm = confusion_matrix(yte, test.pop("_pred"), labels=CLASSES)

    # Same test rows with 30% of features blanked: closer to what the profiler sees.
    Xm, _ = mask_augment(Xte.reset_index(drop=True), yte.reset_index(drop=True), copies=1, p=0.3, seed=1)
    masked = scores(booster, Xm.iloc[len(Xte):], yte.reset_index(drop=True))
    masked.pop("_pred")

    by_source = {}
    for s in sorted(src_te.unique()):
        m = (src_te == s).to_numpy()
        r = scores(booster, Xte[m], yte[m])
        r.pop("_pred")
        by_source[s] = {k: r[k] for k in ("n", "accuracy", "macro_f1")}

    # Leave one source out: train on the other four, test on the held-out trace.
    loso = {}
    for s in sorted(df["src"].unique()):
        tr, te = df["src"] != s, df["src"] == s
        b = fit(X[tr], y[tr], rounds=200)
        r = scores(b, X[te], y[te])
        r.pop("_pred")
        loso[s] = {k: r[k] for k in ("n", "accuracy", "macro_f1")}

    importance = dict(zip(FEATURES, booster.feature_importance("gain").round(1).tolist()))
    metrics = {
        "trained_on": str(date.today()),
        "rows": int(len(df)),
        "rows_by_source": df["src"].value_counts().to_dict(),
        "label_counts": df["label"].value_counts().to_dict(),
        "iterations": booster.best_iteration or booster.current_iteration(),
        "params": PARAMS,
        "test": test,
        "test_masked_30pct": masked,
        "test_by_source": by_source,
        "leave_one_source_out": loso,
        "confusion_matrix": {"labels": CLASSES, "rows_true_cols_pred": cm.tolist()},
        "feature_gain": importance,
    }

    spec = export_lightgbm(booster, meta={"trained_on": metrics["trained_on"],
                                          "test_accuracy": test["accuracy"],
                                          "test_macro_f1": test["macro_f1"]})
    ART.mkdir(parents=True, exist_ok=True)
    (ART / "model.json").write_text(json.dumps(spec, separators=(",", ":")))
    (ART / "metrics.json").write_text(json.dumps(metrics, indent=2))

    # Parity check: the exported pure-Python model must match LightGBM.
    py = Model(spec)
    sample = Xte.sample(min(500, len(Xte)), random_state=SEED)
    lgb_pred = booster.predict(sample).argmax(axis=1)
    py_pred = [CLASSES.index(py.predict(dict(zip(FEATURES, row))).workload_class)
               for row in sample.to_numpy(dtype=float)]
    agree = float(np.mean(lgb_pred == np.array(py_pred)))

    size_kb = (ART / "model.json").stat().st_size / 1024
    print(f"test acc {test['accuracy']}  macro-F1 {test['macro_f1']}  "
          f"(masked: {masked['accuracy']} / {masked['macro_f1']})")
    print("leave-one-source-out:", {k: v["macro_f1"] for k, v in loso.items()})
    print(f"model.json {size_kb:.0f} KB, {len(spec['trees'])} trees, python/lightgbm agreement {agree:.3f}")
    print(pd.DataFrame(cm, index=CLASSES, columns=CLASSES).to_string())


if __name__ == "__main__":
    main()
