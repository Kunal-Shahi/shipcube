"""Pure-Python inference for the trained LightGBM model.

Training needs LightGBM (and libomp on macOS); inference doesn't. scripts/train.py
exports the booster into a compact JSON file (artefacts/model.json) that this module
walks directly, so the MCP server can classify without native dependencies.
tests/test_model.py checks the two agree.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .features import CLASSES, FEATURES, vector

ARTEFACT = Path(__file__).parent / "artefacts" / "model.json"

_MISSING_NONE, _MISSING_ZERO, _MISSING_NAN = 0, 1, 2
_ZERO = 1e-35


# Structural prior: the profiler knows whether the code is a long-running server
# ("web") or runs to completion ("script"). That's the same kind of side signal the
# weak labels came from (trigger type, VM category), and the features alone can't
# always recover it: a once-a-day cron and a rarely-opened HTTP endpoint both look
# idle 99.9% of the day. Probabilities are multiplied by this prior and renormalised.
STRUCTURAL_PRIOR = {
    "script": {"batch_cron": 1.0, "compute_heavy": 1.0, "memory_heavy": 1.0, "io_bound": 1.0,
               "bursty_interactive": 0.05, "steady_service": 0.05},
    "web": {"batch_cron": 0.05, "compute_heavy": 1.0, "memory_heavy": 1.0, "io_bound": 1.0,
            "bursty_interactive": 1.0, "steady_service": 1.0},
}


@dataclass(frozen=True)
class Prediction:
    workload_class: str
    confidence: float
    probabilities: dict[str, float]


class _Tree:
    __slots__ = ("feat", "thr", "dleft", "miss", "left", "right", "leaf")

    def __init__(self, t: dict):
        self.feat, self.thr, self.dleft = t["f"], t["t"], t["d"]
        self.miss, self.left, self.right, self.leaf = t["m"], t["l"], t["r"], t["v"]

    def value(self, x: list[float]) -> float:
        if not self.feat:  # single-leaf tree
            return self.leaf[0]
        node = 0
        while node >= 0:
            v = x[self.feat[node]]
            m = self.miss[node]
            nan = math.isnan(v)
            if m == _MISSING_NAN and nan:
                go_left = self.dleft[node]
            elif m == _MISSING_ZERO and (nan or abs(v) <= _ZERO):
                go_left = self.dleft[node]
            else:
                go_left = (0.0 if nan else v) <= self.thr[node]
            node = self.left[node] if go_left else self.right[node]
        return self.leaf[~node]


class Model:
    def __init__(self, spec: dict):
        if spec["features"] != FEATURES:
            raise ValueError("model was trained on a different feature list")
        self.classes: list[str] = spec["classes"]
        self.k = len(self.classes)
        self.trees = [_Tree(t) for t in spec["trees"]]
        self.meta: dict = spec.get("meta", {})

    @classmethod
    def load(cls, path: Path | str = ARTEFACT) -> "Model":
        return cls(json.loads(Path(path).read_text()))

    def raw_scores(self, x: list[float]) -> list[float]:
        s = [0.0] * self.k
        for i, tree in enumerate(self.trees):
            s[i % self.k] += tree.value(x)
        return s

    def predict_proba(self, features: dict[str, float]) -> dict[str, float]:
        s = self.raw_scores(vector(features))
        m = max(s)
        e = [math.exp(v - m) for v in s]
        z = sum(e)
        return {c: e[i] / z for i, c in enumerate(self.classes)}

    def predict(self, features: dict[str, float], kind: str | None = None) -> Prediction:
        """kind: "web" or "script" applies STRUCTURAL_PRIOR; None gives the raw model."""
        p = self.predict_proba(features)
        if kind in STRUCTURAL_PRIOR:
            prior = STRUCTURAL_PRIOR[kind]
            z = sum(p[c] * prior[c] for c in p)
            p = {c: p[c] * prior[c] / z for c in p}
        best = max(p, key=p.get)
        return Prediction(best, round(p[best], 4), {k: round(v, 4) for k, v in p.items()})


@lru_cache(maxsize=1)
def default_model() -> Model:
    return Model.load()


def classify(features: dict[str, float], kind: str | None = None) -> Prediction:
    return default_model().predict(features, kind)


def export_lightgbm(booster, meta: dict | None = None) -> dict:
    """Convert a trained lightgbm.Booster into the compact spec Model reads."""
    dump = booster.dump_model()
    if dump["num_class"] != len(CLASSES):
        raise ValueError("expected one output per class")
    miss_code = {"None": _MISSING_NONE, "Zero": _MISSING_ZERO, "NaN": _MISSING_NAN}
    trees = []
    for info in dump["tree_info"]:
        t = {"f": [], "t": [], "d": [], "m": [], "l": [], "r": [], "v": []}

        def walk(n: dict) -> int:
            if "leaf_value" in n or "split_feature" not in n:
                t["v"].append(round(n.get("leaf_value", 0.0), 7))
                return ~(len(t["v"]) - 1)
            if n["decision_type"] != "<=":
                raise ValueError("only numerical splits are supported")
            i = len(t["f"])
            t["f"].append(n["split_feature"])
            t["t"].append(n["threshold"])
            t["d"].append(bool(n["default_left"]))
            t["m"].append(miss_code[n["missing_type"]])
            t["l"].append(0)
            t["r"].append(0)
            t["l"][i] = walk(n["left_child"])
            t["r"][i] = walk(n["right_child"])
            return i

        walk(info["tree_structure"])
        trees.append(t)
    return {"classes": CLASSES, "features": FEATURES, "trees": trees, "meta": meta or {}}
