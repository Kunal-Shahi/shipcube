"""estimate(source, hints) -> EstimateResult: the engine behind the MCP tool estimate_load."""

from __future__ import annotations

import logging
import shutil
import tempfile
import uuid
from pathlib import Path

from shipcube_classifier import load_model, obs, rule_class
from shipcube_profiler import (AbuseRefused, CodeSummary, Hints, NotRunnable, ProfilerError, Runner, StaticRunner,
                               Verdict, abuse_signals, default_runner, materialize, prescreen, summarize,
                               to_features)
from shipcube_profiler.sources import UploadFetcher

from . import placement as pl
from .explain import Explainer, FallbackExplainer
from .types import EstimateResult

log = logging.getLogger("shipcube.placement")


def _measured(profile, tr: pl.Traffic) -> dict:
    d = {k: profile.__dict__[k] for k in ("mode", "kind", "wall_s", "cpu_mean_cores", "cpu_p95_cores",
                                          "mem_peak_gb", "net_calls", "net_blocked", "remote_hosts",
                                          "static_reason")}
    d["latency_mean_s"] = tr.latency_s
    if profile.routes:   # heaviest first, so the explanation can name the route that drives sizing
        d["routes"] = [{"path": r.path, "cpu_mean_cores": round(r.cpu_mean_cores, 3),
                        "latency_mean_ms": round(r.latency_mean_s * 1000, 1), "ok": r.ok, "sent": r.sent}
                       for r in sorted(profile.routes, key=lambda r: -r.cpu_mean_cores)[:5]]
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in d.items()}


STATIC_MAX_CONFIDENCE = 0.4   # below the model's 0.5 gate: a static estimate is always low confidence
SANDBOX_CONFIDENCE = 0.75
# Relative to the sandbox the app ran in (1 vCPU / 2 GiB on Cloud Run). The trace thresholds
# (compute_heavy: >= 1.5 cores) can't be reached by a single process in a 1-vCPU sandbox.
SATURATED_CPU_P95 = 0.9       # x the CPU limit
SATURATED_CPU_MEAN = 0.6      # x the CPU limit, over the measured window
HEAVY_MEMORY = 0.5            # x the memory limit


def sandbox_class(profile) -> tuple[str, str] | None:
    """compute_heavy / memory_heavy for a web app, judged against the sandbox's own limits, or None.

    A measured app that pins the sandbox's CPU, or uses half its memory (or was stopped for
    memory), is CPU- or memory-bound whatever the trace-trained classifier says: it never
    saw an app that hit a 1-vCPU ceiling."""
    if profile.mode != "measured" or not profile.cpu_limit_cores or profile.kind != "web":
        return None   # scripts: batch_cron is about when they run; their sizing already uses the numbers
    lim_cpu, lim_mem = profile.cpu_limit_cores, profile.mem_limit_gb or 2.0
    if (profile.killed_reason or "").startswith("memory") or profile.mem_peak_gb >= HEAVY_MEMORY * lim_mem:
        return "memory_heavy", (f"peaked at {profile.mem_peak_gb * 1024:.0f} MiB of the sandbox's "
                                f"{lim_mem * 1024:.0f} MiB, so treated as memory-heavy")
    if profile.cpu_p95_cores >= SATURATED_CPU_P95 * lim_cpu and profile.cpu_mean_cores >= SATURATED_CPU_MEAN * lim_cpu:
        return "compute_heavy", (f"kept the sandbox's {lim_cpu:g} vCPU busy (mean {profile.cpu_mean_cores:.2f}, "
                                 f"p95 {profile.cpu_p95_cores:.2f}), so treated as compute-heavy")
    # The mixed load test averages every route; a CPU-heavy route next to idle ones (a sleep, a
    # health check, a static page) can stay under the line. Each route probed on its own can't.
    top = profile.heaviest_route()
    if top and top.cpu_p95_cores >= SATURATED_CPU_P95 * lim_cpu and top.cpu_mean_cores >= SATURATED_CPU_MEAN * lim_cpu:
        return "compute_heavy", (f"route {top.path} kept the sandbox's {lim_cpu:g} vCPU busy on its own "
                                 f"(mean {top.cpu_mean_cores:.2f}, p95 {top.cpu_p95_cores:.2f}, "
                                 f"{top.latency_mean_s * 1000:.0f} ms per request), so treated as compute-heavy")
    return None


def decide(features: dict, shape: str):
    """Model + rules policy (docs/research/load-estimation.md §3.5).

    The classifier decides when its confidence clears the model's gate
    (min_confidence, fitted on profiled apps). Below the gate the labelling rules,
    applied to this app's features, decide and the estimate is flagged low_confidence.
    Returns (class, confidence, decided_by, low_confidence, pred, rules_class).
    """
    model = load_model().model
    pred = model.predict(features, None if shape == "worker" else shape)
    rules = rule_class(features, shape)
    if pred.low_confidence:
        # Confidence reported = the model's probability for the class we go with.
        return rules, pred.probabilities[rules], "rules", True, pred, rules
    return pred.workload_class, pred.confidence, "classifier", False, pred, rules


def estimate(source: dict, hints: Hints | dict | None = None, *, runner: Runner | None = None,
             explainer: Explainer | None = None, fetch_upload: UploadFetcher | None = None,
             workdir: Path | None = None, operation_id: str | None = None, request_id: str | None = None,
             traceparent: str | None = None) -> EstimateResult:
    """Profile `source`, classify it, place it, price it and explain it.

    source:    estimate_load Source dict (inline | git | upload | image)
    hints:     {expected_users, schedule, region, sample_input, traffic}
    runner:    default_runner(): CloudRunJobRunner from env, LocalRunner if SHIPCUBE_DEV_MODE=1.
               StaticRunner is used (and flagged) when the code can't run.
    explainer: FallbackExplainer (Muse Spark, then rules) by default.
    operation_id / request_id / traceparent: copied into every log line for this estimate.
    """
    estimate_id = f"est_{uuid.uuid4().hex[:16]}"
    with obs.scope(estimate_id=estimate_id, operation_id=operation_id, request_id=request_id,
                   traceparent=traceparent), obs.timed(log, "estimate.completed") as done:
        result = _estimate(estimate_id, source, Hints.from_any(hints), runner, explainer, fetch_upload, workdir)
        done.update(tier=result.tier, workload_class=result.workload_class, decided_by=result.decided_by,
                    low_confidence=result.low_confidence, explained_by=result.explained_by,
                    model_version=result.model_version, profile_mode=result.profile["measurements"]["mode"],
                    cost_expected_usd=result.monthly_cost_usd.expected)
        return result


def _estimate(estimate_id, source, h: Hints, runner, explainer, fetch_upload, workdir) -> EstimateResult:
    explainer = explainer or FallbackExplainer()
    own_dir = workdir is None
    work = Path(tempfile.mkdtemp(prefix="shipcube-est-")) if own_dir else workdir
    notes: list[str] = []
    try:
        summary = CodeSummary()
        app_dir = None
        reason = None   # shipcube_profiler.STATIC_REASONS code when the estimate is static
        verdict = Verdict()
        try:
            app_dir = materialize(source, work / "src", fetch_upload=fetch_upload)
            summary = summarize(app_dir)
            # Before anything is built or run: high-confidence abuse markers refuse the estimate
            # (AbuseRefused); low-confidence ones become abuse signals below.
            verdict = prescreen(app_dir)
            if verdict.blocked:
                obs.event(log, "prescreen.blocked", logging.WARNING, reasons=verdict.reasons)
                raise AbuseRefused(verdict)
        except NotRunnable as e:
            notes.append(str(e))
            reason = "image_source"

        profile = None
        signals: list[str] = []   # kept when a measured run is discarded, for the control plane's strikes
        if app_dir is not None and summary.entrypoint and summary.uses_gpu_libs:
            reason = "gpu"   # the sandbox has no GPU; a CPU run would mislead
        elif app_dir is not None and summary.entrypoint:
            runner = runner or default_runner()
            try:
                with obs.timed(log, "profile.completed", runner=runner.name, kind=summary.kind):
                    profile = runner.run(app_dir, summary, h)
            except ProfilerError as e:
                reason = e.reason
                obs.event(log, "profile.failed", logging.WARNING, stage=e.stage, reason=e.reason, error=str(e)[:300])
                notes.append(f"the profiler failed at '{e.stage}'; fell back to static estimates")
            if profile is not None and profile.mode == "measured" and (
                    (summary.kind == "web" and not (profile.requests and profile.requests.ok))
                    or (summary.kind != "web" and profile.exit_code not in (0, None) and not profile.timed_out)):
                notes.append("the app didn't run cleanly under the profiler; fell back to static estimates")
                notes += profile.notes
                reason = "did_not_start" if summary.kind == "web" else "app_failed"
                signals = profile.abuse_signals
                profile = None
        elif h.schedule and summary.kind == "web" and not summary.framework:
            summary.kind = "script"
        if profile is None and reason is None and app_dir is not None and not summary.entrypoint:
            reason = "no_entrypoint"
        if profile is None:
            profile = StaticRunner(reason=reason).run(app_dir, summary, h)
            profile.abuse_signals = signals or abuse_signals(profile)
            obs.event(log, "profile.static", logging.WARNING, reason=reason, notes=notes[:3])

        if verdict.signals:
            profile.abuse_signals = sorted(set(profile.abuse_signals) | set(verdict.signals))
            obs.event(log, "prescreen.flagged", logging.WARNING, signals=verdict.signals)
        features, assumptions = to_features(profile, h, api_only=summary.api_only)
        shape = "worker" if summary.kind == "script" and profile.timed_out else summary.kind
        cls, confidence, decided_by, low_conf, pred, rules_cls = decide(features, shape)
        if rules_cls != cls:
            assumptions.append(f"the rule set alone would call this {rules_cls}")
        saturated = sandbox_class(profile)
        if saturated and cls != saturated[0]:
            cls, confidence, decided_by, low_conf = saturated[0], SANDBOX_CONFIDENCE, "rules", False
            assumptions.append(saturated[1])
        if profile.mode != "measured":
            # Nothing ran: the classifier only saw typical numbers for this app's shape, so its
            # certainty about those numbers says nothing about the app itself.
            confidence, low_conf = min(confidence, STATIC_MAX_CONFIDENCE), True
            assumptions.append("not run, so the class comes from the app's shape only; "
                               "a CPU- or memory-heavy app can look like any other web app")
        tr = pl.traffic(profile, features, h)
        cpu_need, mem_need = pl.needs(profile)
        tier, target = pl.choose_tier(cls, summary, h, profile, cpu_need, mem_need)
        res = pl.size(tier, cls, profile, tr, h)
        monthly, basis = pl.cost(tier, target, res, tr, confidence)
        caps = pl.rule_caps(tier, res, tr, monthly)
        alts = pl.alternatives(tier, cls, summary, profile, tr, h, confidence)
        if h.region not in pl.pricing()["regions_checked"]:
            assumptions.append(f"prices for {h.region} not checked; used us-central1 list prices")

        facts = {
            "workload_class": cls,
            "confidence": confidence,
            "low_confidence": low_conf,
            "decided_by": decided_by,
            "class_probabilities": pred.probabilities,
            "tier": tier,
            "target": target,
            "resources": res.model_dump(exclude_none=True),
            "monthly_cost_usd": monthly.model_dump(),
            "measured": _measured(profile, tr),
            "code": {k: summary.to_dict()[k] for k in ("framework", "kind", "entrypoint", "routes", "api_only",
                                                       "files", "lines", "uses_data_libs",
                                                       "uses_gpu_libs", "uses_network_libs", "accepts_input")},
            "hints": {"expected_users": h.expected_users, "schedule": h.schedule, "traffic": h.traffic},
            "candidate_alternatives": [a.model_dump() for a in alts],
            "rule_caps": caps.model_dump(exclude_none=True),
            "assumptions": assumptions + notes,
        }
        with obs.timed(log, "explain.completed") as ex_log:
            ex = explainer.explain(facts, alts, caps)   # no code: Muse gets muse_facts() only
            ex_log.update(explained_by=ex.explained_by, fallback=bool(ex.fallback_reason))

        return EstimateResult(
            estimate_id=estimate_id,
            workload_class=cls,
            confidence=round(confidence, 4),
            tier=tier,
            target=target,
            resources=res,
            monthly_cost_usd=monthly,
            profile={
                "measurements": profile.to_dict(),
                "prescreen": verdict.to_dict(),
                "features": features,
                "class_probabilities": pred.probabilities,
                "classifier": {"workload_class": pred.workload_class, "confidence": pred.confidence,
                               "low_confidence": pred.low_confidence},
                "rules_class": rules_cls,
                "code": facts["code"],
                "cost_basis": basis,
                "assumptions": assumptions + notes,
                "explainer_fallback_reason": ex.fallback_reason,
            },
            recommended_caps=ex.recommended_caps,
            reasoning=ex.reasoning,
            alternatives=ex.alternatives,
            explained_by=ex.explained_by,
            decided_by=decided_by,
            low_confidence=low_conf,
            model_version=pred.model_version,
        )
    finally:
        if own_dir:
            shutil.rmtree(work, ignore_errors=True)
