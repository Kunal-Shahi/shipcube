"""estimate(source, hints) -> EstimateResult: the engine behind the MCP tool estimate_load."""

from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path

from shipcube_classifier import classify
from shipcube_profiler import (CodeSummary, Hints, LocalRunner, NotRunnable, Runner,
                               StaticRunner, materialize, summarize, to_features)
from shipcube_profiler.sources import UploadFetcher

from . import placement as pl
from .explain import Explainer, FallbackExplainer
from .types import EstimateResult


def _measured(profile, tr: pl.Traffic) -> dict:
    d = {k: profile.__dict__[k] for k in ("mode", "kind", "wall_s", "cpu_mean_cores", "cpu_p95_cores",
                                          "mem_peak_gb", "net_calls", "net_blocked", "remote_hosts")}
    d["latency_mean_s"] = tr.latency_s
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in d.items()}


def estimate(source: dict, hints: Hints | dict | None = None, *, runner: Runner | None = None,
             explainer: Explainer | None = None, fetch_upload: UploadFetcher | None = None,
             workdir: Path | None = None) -> EstimateResult:
    """Profile `source`, classify it, place it, price it and explain it.

    source:    estimate_load Source dict (inline | git | upload | image)
    hints:     {expected_users, schedule, region, sample_input}
    runner:    LocalRunner by default; CloudRunJobRunner in production; StaticRunner
               is used automatically when the code can't run.
    explainer: FallbackExplainer (Muse Spark, then rules) by default.
    """
    h = Hints.from_any(hints)
    runner = runner or LocalRunner()
    explainer = explainer or FallbackExplainer()
    own_dir = workdir is None
    work = Path(tempfile.mkdtemp(prefix="shipcube-est-")) if own_dir else workdir
    notes: list[str] = []
    try:
        summary = CodeSummary()
        app_dir = None
        try:
            app_dir = materialize(source, work / "src", fetch_upload=fetch_upload)
            summary = summarize(app_dir)
        except NotRunnable as e:
            notes.append(str(e))
        if app_dir is not None and summary.entrypoint:
            profile = runner.run(app_dir, summary, h)
            failed = profile.mode == "measured" and (
                (summary.kind == "web" and not (profile.requests and profile.requests.ok))
                or (summary.kind != "web" and profile.exit_code not in (0, None) and not profile.timed_out))
            if failed:
                notes.append("the app didn't run cleanly under the profiler; fell back to static estimates")
                notes += profile.notes
                profile = StaticRunner().run(app_dir, summary, h)
        else:
            if h.schedule and summary.kind == "web" and not summary.framework:
                summary.kind = "script"
            profile = StaticRunner().run(app_dir, summary, h)

        features, assumptions = to_features(profile, h)
        raw = classify(features)
        pred = classify(features, kind=summary.kind)
        tr = pl.traffic(profile, features, h)
        cpu_need, mem_need = pl.needs(profile)
        tier, target = pl.choose_tier(pred.workload_class, summary, h, profile, cpu_need, mem_need)
        res = pl.size(tier, pred.workload_class, profile, tr, h)
        monthly, basis = pl.cost(tier, target, res, tr, pred.confidence)
        caps = pl.rule_caps(tier, res, tr, monthly)
        alts = pl.alternatives(tier, pred.workload_class, summary, profile, tr, h, pred.confidence)
        if h.region not in pl.pricing()["regions_checked"]:
            assumptions.append(f"prices for {h.region} not checked; used us-central1 list prices")

        facts = {
            "workload_class": pred.workload_class,
            "confidence": pred.confidence,
            "class_probabilities": pred.probabilities,
            "tier": tier,
            "target": target,
            "resources": res.model_dump(exclude_none=True),
            "monthly_cost_usd": monthly.model_dump(),
            "measured": _measured(profile, tr),
            "code": {k: summary.to_dict()[k] for k in ("framework", "kind", "entrypoint", "routes",
                                                       "files", "lines", "uses_data_libs",
                                                       "uses_gpu_libs", "uses_network_libs")},
            "hints": {"expected_users": h.expected_users, "schedule": h.schedule},
            "candidate_alternatives": [a.model_dump() for a in alts],
            "rule_caps": caps.model_dump(exclude_none=True),
            "assumptions": assumptions + notes,
        }
        ex = explainer.explain(facts, alts, caps, summary.excerpt)

        return EstimateResult(
            estimate_id=f"est_{uuid.uuid4().hex[:16]}",
            workload_class=pred.workload_class,
            confidence=pred.confidence,
            tier=tier,
            target=target,
            resources=res,
            monthly_cost_usd=monthly,
            profile={
                "measurements": profile.to_dict(),
                "features": features,
                "class_probabilities": pred.probabilities,
                "classifier_raw": {"workload_class": raw.workload_class, "confidence": raw.confidence},
                "code": facts["code"],
                "cost_basis": basis,
                "assumptions": assumptions + notes,
                "explainer_error": getattr(explainer, "last_error", None),
            },
            recommended_caps=ex.recommended_caps,
            reasoning=ex.reasoning,
            alternatives=ex.alternatives,
            explained_by=ex.explained_by,
        )
    finally:
        if own_dir:
            shutil.rmtree(work, ignore_errors=True)
