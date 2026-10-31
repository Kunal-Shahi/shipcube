"""Explanations: Muse Spark when available, rule-based text otherwise.

Muse Spark writes three things: `reasoning`, the `tradeoff` text of each alternative,
and `recommended_caps`. It never touches the classifier's or placement's numbers:
- it only sees them as facts and returns text;
- alternatives must name tiers we already priced (costs are ours, not the model's);
- caps are validated against the Caps schema and clamped to [0.5x, 2x] of the rule
  caps, and `max_instances` can only go down;
- anything that fails validation falls back to the rules, field by field.

Muse never sees user code or anything copied from it (no source, route paths, file
names, hostnames the app tried, or its output). muse_facts() rebuilds what it gets from an
allowlist: the classifier's output, numbers, fixed enums and flags, and assumption codes.
The rule-based explanation, written locally, still uses the full facts.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import ValidationError

from shipcube_classifier import obs
from shipcube_profiler import STATIC_REASONS

from .types import Alternative, Caps

log = logging.getLogger("shipcube.placement")

DEFAULT_MODEL = "muse-spark-1.3"
DEFAULT_BASE_URL = "https://api.meta.ai/v1"

CLASS_TEXT = {
    "batch_cron": "a scheduled batch job: it runs to completion, then nothing runs until the next run",
    "bursty_interactive": "a bursty interactive app: idle most of the day, with short bursts of requests when people use it",
    "steady_service": "a steady service: traffic all day, so instances rarely go idle",
    "memory_heavy": "memory-heavy: it needs a lot of memory for the CPU it uses",
    "io_bound": "I/O-bound: it spends most of its time waiting on disk or network, not CPU",
    "compute_heavy": "compute-heavy: it keeps the CPU busy while it runs",
}
TIER_TEXT = {
    "T0": "T0 (snapshot: precomputed page refreshed by a scheduled job)",
    "T1": "T1 (Cloud Run, scales to zero)",
    "T2": "T2 (Cloud Run job + Cloud Scheduler)",
    "T3": "T3 (always-on: Cloud Run with min instances, or GKE Autopilot)",
    "T4": "T4 (GPU)",
}


@dataclass
class Explanation:
    reasoning: str
    alternatives: list[Alternative]
    recommended_caps: Caps
    explained_by: str
    fallback_reason: str | None = None   # why Muse wasn't used (redacted), if it wasn't


class Explainer(Protocol):
    def explain(self, facts: dict, alternatives: list[Alternative], caps: Caps) -> Explanation: ...


class RuleExplainer:
    def explain(self, facts: dict, alternatives: list[Alternative], caps: Caps) -> Explanation:
        return Explanation(rule_reasoning(facts), alternatives, caps, "rules")


def rule_reasoning(f: dict) -> str:
    cls, tier = f["workload_class"], f["tier"]
    p = f["measured"]
    if f.get("decided_by") == "rules":
        parts = [f"This looks like {CLASS_TEXT[cls]}. The classifier wasn't confident about this app, "
                 f"so the class comes from ShipCube's rule set: treat it as a best guess and check the "
                 f"measurements below."]
    else:
        parts = [f"The classifier reads this as {CLASS_TEXT[cls]} (confidence {f['confidence']:.0%})."]
    if p.get("mode") == "measured":
        if p["kind"] == "web":
            parts.append(
                f"Under a short bursty load test it used {p['cpu_mean_cores']:.2f} vCPU on average "
                f"(p95 {p['cpu_p95_cores']:.2f}) and peaked at {p['mem_peak_gb'] * 1024:.0f} MiB, "
                f"with {p['latency_mean_s'] * 1000:.0f} ms mean latency.")
        else:
            parts.append(
                f"A sample run took {p['wall_s']:.1f} s, used {p['cpu_mean_cores']:.2f} vCPU on average "
                f"and peaked at {p['mem_peak_gb'] * 1024:.0f} MiB.")
    else:
        why = STATIC_REASONS.get(p.get("static_reason") or "")
        parts.append(f"The code wasn't executed ({why}), so these are typical values for this kind of app."
                     if why else "The code wasn't executed, so these are typical values for this kind of app.")
    parts.append(f"That points to {TIER_TEXT[tier]} with {f['resources']['cpu']} vCPU and "
                 f"{f['resources']['memory']} per instance.")
    c = f["monthly_cost_usd"]
    parts.append(f"Expected cost is about ${c['expected']:.2f}/month (range ${c['low']:.2f}–${c['high']:.2f}), "
                 "before the free tier.")
    if p.get("net_blocked"):
        parts.append(f"It tried to reach the network ({', '.join(p.get('remote_hosts', [])[:3])}); "
                     "outbound calls were blocked while profiling, so allow them at deploy time if they're needed.")
    for n in f.get("assumptions", [])[:2]:
        parts.append(f"Assumption: {n}.")
    return " ".join(parts)


SYSTEM_PROMPT = """You explain infrastructure placement decisions for ShipCube, which deploys small internal apps to Google Cloud.

You get FACTS as JSON: a workload classifier's output, measurements from a sandboxed test run (or, when the app wasn't run, typical values for its shape), the chosen tier, resources, a monthly cost range, candidate alternative tiers with their costs, rule-based caps, and short assumption codes. You never see the app's code, file names or route paths: refer to routes by rank ("the heaviest route"), not by name.

Rules:
- Never change or invent numbers. Use only the numbers in FACTS. Don't claim a different class, tier, cost, CPU or memory.
- Write for a developer deciding whether to deploy: plain, specific, no marketing. Use human units (MiB, ms, vCPU, $/month, percent) rather than raw JSON field names or long decimals.
- class_probabilities already include a structural prior (script vs. web server); don't second-guess the class.
- If FACTS has "typical_values_not_measured" instead of "measured", the app was NOT run. Call those numbers "typical values for this app shape". Never write "measured", "observed", "test run", "sample run", "load test" or "lasted" about them.
- If FACTS.low_confidence is true, the class came from rules because the classifier was unsure: say so plainly in one sentence and point at the measurement that matters most.
- Reply with ONE JSON object and nothing else:
{"reasoning": "<=120 words: why this class and tier fit, citing numbers from FACTS",
 "alternatives": [{"tier": "<one of the candidate tiers in FACTS>", "tradeoff": "<=40 words"}],
 "recommended_caps": {"max_instances": int, "concurrency": int|null, "requests_per_minute": int|null, "monthly_spend_usd": number, "cpu": "0.5"|"1"|"2"|"4", "memory": "<n>Mi|<n>Gi", "timeout_s": int}}
- recommended_caps should start from the rule caps in FACTS; adjust only with a reason grounded in FACTS."""


def _is_static(facts: dict) -> bool:
    return (facts.get("measured") or {}).get("mode") != "measured"


def _for_muse(facts: dict) -> dict:
    """For a static estimate, hand Muse the numbers under a key that says what they are."""
    if not _is_static(facts):
        return facts
    out = {k: v for k, v in facts.items() if k != "measured"}
    out["typical_values_not_measured"] = facts.get("measured") or {}
    return out


_NEGATED = re.compile(r"\b(?:not|never|wasn't|weren't|isn't|aren't|no|without|nothing\s+(?:was|is|has\s+been|got))\s+(?:been\s+)?"
                      r"(?:measured|measurements?|observed|a\s+test\s+run|test\s+run|load\s+test(?:ed)?)\b", re.I)
_MEASURED = re.compile(r"\b(?:measur\w*|observed|test\s+run|sample\s+run|load\s+test\w*|benchmark\w*|lasted|"
                       r"profiling\s+run|during\s+the\s+run|in\s+the\s+run)\b", re.I)


def _claims_measurement(text: str) -> bool:
    return bool(_MEASURED.search(_NEGATED.sub("", text)))


# --- what Muse may see: derived facts only (no user code, names or output) ------------------

_MEASURED_NUMBERS = ("wall_s", "cpu_mean_cores", "cpu_p95_cores", "mem_peak_gb", "latency_mean_s", "net_calls",
                     "net_blocked")
_ENUM = re.compile(r"^[A-Za-z0-9_]{1,40}$")
_CRON = re.compile(r"^[0-9*/,\- @a-z]{1,40}$", re.I)
# Our own assumption templates -> codes. Anything else (e.g. a note quoting the app's stderr) is dropped.
_ASSUMPTION_CODES = (
    (re.compile(r"^traffic model: office hours"), "traffic_office_hours"),
    (re.compile(r"^traffic model: always on"), "traffic_always_on"),
    (re.compile(r"^expected_users not given"), "expected_users_assumed"),
    (re.compile(r"^not run, so the class comes"), "not_run_shape_only"),
    (re.compile(r"^the rule set alone would call this ([a-z_]+)"), "rules_would_say_{0}"),
    (re.compile(r"^route .* kept the sandbox's .* on its own"), "heaviest_route_saturates_sandbox_cpu"),
    (re.compile(r"^kept the sandbox's .* busy"), "app_saturates_sandbox_cpu"),
    (re.compile(r"^peaked at .* so treated as memory-heavy"), "app_uses_half_sandbox_memory"),
    (re.compile(r"^the profiler failed at '([a-z]+)'"), "profiler_failed_at_{0}"),
    (re.compile(r"^the app didn't run cleanly"), "app_did_not_run_cleanly"),
    (re.compile(r"^prices for .* not checked"), "region_prices_not_checked"),
)


def _num(v):
    return round(v, 4) if isinstance(v, float) else v if isinstance(v, (int, bool)) or v is None else None


def _enum(v):
    return v if isinstance(v, str) and _ENUM.match(v) else None


def muse_facts(facts: dict) -> dict:
    """The facts Muse gets, rebuilt from an allowlist (no-raw-code rule): numbers, the
    classifier's and placement's own enums, flags, counts and assumption codes. Never a string
    that came from the user's files or the app's behaviour."""
    m = facts.get("measured") or {}
    code = facts.get("code") or {}
    hints = facts.get("hints") or {}
    measured = {k: _num(m.get(k)) for k in _MEASURED_NUMBERS}
    measured.update(mode=_enum(m.get("mode")), kind=_enum(m.get("kind")), static_reason=_enum(m.get("static_reason")),
                    remote_host_count=len(m.get("remote_hosts") or []))
    if m.get("routes"):   # heaviest first, by rank: route paths can carry names
        measured["routes"] = [{"rank": i + 1, **{k: _num(r.get(k)) for k in ("cpu_mean_cores", "latency_mean_ms", "ok", "sent")}}
                              for i, r in enumerate(m["routes"])]
    codes = []
    for a in facts.get("assumptions") or []:
        for rx, code_t in _ASSUMPTION_CODES:
            hit = rx.search(str(a))
            if hit:
                c = code_t.format(*hit.groups())
                if _enum(c) and c not in codes:
                    codes.append(c)
                break
    out = {
        "workload_class": _enum(facts.get("workload_class")), "confidence": _num(facts.get("confidence")),
        "low_confidence": bool(facts.get("low_confidence")), "decided_by": _enum(facts.get("decided_by")),
        "class_probabilities": {k: _num(v) for k, v in (facts.get("class_probabilities") or {}).items() if _enum(k)},
        "tier": _enum(facts.get("tier")), "target": facts.get("target") if facts.get("target") in TARGETS else None,
        "resources": facts.get("resources"), "monthly_cost_usd": facts.get("monthly_cost_usd"),
        "measured": measured,
        "code": {"framework": _enum(code.get("framework")), "kind": _enum(code.get("kind")),
                 "api_only": bool(code.get("api_only")), "files": _num(code.get("files")), "lines": _num(code.get("lines")),
                 "route_count": len(code.get("routes") or []),
                 **{k: bool(code.get(k)) for k in ("uses_data_libs", "uses_gpu_libs", "uses_network_libs", "accepts_input")}},
        "hints": {"expected_users": _num(hints.get("expected_users")), "traffic": _enum(hints.get("traffic")),
                  "schedule": hints.get("schedule") if isinstance(hints.get("schedule"), str)
                  and _CRON.match(hints["schedule"]) else None},
        "candidate_alternatives": [{"tier": _enum(a.get("tier")), "target": a.get("target") if a.get("target") in TARGETS else None,
                                    "monthly_cost_usd": a.get("monthly_cost_usd")}
                                   for a in facts.get("candidate_alternatives") or []],
        "rule_caps": facts.get("rule_caps"),
        "assumption_codes": codes,
    }
    return out


TARGETS = {"cloud-run-service", "cloud-run-job", "cloud-run-gpu", "gke-autopilot", "static-snapshot"}


def _parse_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in reply")
    return json.loads(m.group(0))


def _clamp_caps(proposed: dict, rule: Caps) -> Caps:
    """Validate Muse's caps field by field. Only numeric caps the rules set can move, and
    only within [0.5x, 2x] of the rule value (max_instances can only go down). cpu and
    memory come from the profile, so they're never taken from the model."""
    out = rule.model_dump()
    for k, v in (proposed or {}).items():
        r = out.get(k)
        if k in ("cpu", "memory") or v is None or isinstance(v, bool) or r is None:
            continue
        if not isinstance(v, (int, float)) or not isinstance(r, (int, float)):
            continue
        try:
            Caps.model_validate({k: v})
        except ValidationError:
            continue
        hi = r if k == "max_instances" else r * 2
        if r * 0.5 <= v <= hi:
            out[k] = type(r)(v)
    return Caps.model_validate(out)


class MuseError(Exception):
    """A Muse call that didn't produce a usable explanation.

    kind: not_configured | auth | rate_limited | timeout | unavailable | bad_request | bad_response
    """

    RETRYABLE = {"rate_limited", "timeout", "unavailable"}

    def __init__(self, kind: str, message: str, attempts: int = 1):
        super().__init__(f"{kind}: {message}")
        self.kind, self.attempts = kind, attempts


def _classify_error(e: Exception) -> str:
    try:
        import openai
    except ImportError:  # pragma: no cover
        return "unavailable"
    if isinstance(e, openai.APITimeoutError) or isinstance(e, TimeoutError):
        return "timeout"
    if isinstance(e, openai.RateLimitError):
        return "rate_limited"
    if isinstance(e, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return "auth"
    if isinstance(e, openai.BadRequestError):
        return "bad_request"
    if isinstance(e, (openai.APIConnectionError, openai.InternalServerError, ConnectionError)):
        return "unavailable"
    if isinstance(e, openai.APIStatusError):
        return "unavailable" if getattr(e, "status_code", 500) >= 500 else "bad_request"
    return "unavailable"


class MuseExplainer:
    """OpenAI-compatible client against the Meta Model API.

    Each attempt has `timeout_s`; transient failures (timeouts, 429, 5xx, connection
    errors) are retried with exponential backoff up to `max_attempts`, within an overall
    `deadline_s`. Bad replies (empty, not JSON, failed validation) are not retried.
    """

    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 model: str | None = None, timeout_s: float | None = None, client: Any = None,
                 max_tokens: int = 6000, reasoning_effort: str | None = "low",
                 max_attempts: int = 3, deadline_s: float | None = None, backoff_s: float = 1.0):
        # Muse Spark is a reasoning model: its hidden reasoning tokens count against
        # max_tokens, so leave plenty of room for them plus the JSON answer.
        self.max_tokens = max_tokens
        self.reasoning_effort = reasoning_effort
        self.model = model or os.environ.get("SHIPCUBE_EXPLAINER_MODEL", DEFAULT_MODEL)
        self.timeout_s = timeout_s or float(os.environ.get("SHIPCUBE_MUSE_TIMEOUT_S", "30"))
        self.deadline_s = deadline_s or float(os.environ.get("SHIPCUBE_MUSE_DEADLINE_S", "60"))
        self.max_attempts, self.backoff_s = max_attempts, backoff_s
        if client is None:
            from openai import OpenAI
            key = api_key or os.environ.get("META_API_KEY")
            if not key:
                raise MuseError("not_configured", "META_API_KEY is not set")
            # Retries are ours (logged, deadline-aware), so the SDK's are off.
            client = OpenAI(api_key=key, base_url=base_url or os.environ.get("META_BASE_URL", DEFAULT_BASE_URL),
                            timeout=self.timeout_s, max_retries=0)
        self.client = client

    def _messages(self, facts: dict) -> list[dict]:
        """System prompt plus muse_facts() only: never code, file contents, names or app output."""
        user = "FACTS:\n" + json.dumps(_for_muse(muse_facts(facts)), indent=1, default=str)
        return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]

    def _call(self, messages: list[dict]) -> tuple[str, int]:
        extra = {"reasoning_effort": self.reasoning_effort} if self.reasoning_effort else {}
        start = time.monotonic()
        for attempt in range(1, self.max_attempts + 1):
            t = time.perf_counter()
            remaining = self.deadline_s - (time.monotonic() - start)
            try:
                resp = self.client.chat.completions.create(
                    model=self.model, messages=messages, temperature=0.2, max_tokens=self.max_tokens,
                    timeout=max(1.0, min(self.timeout_s, remaining)), **extra)
            except Exception as e:
                kind = _classify_error(e)
                obs.event(log, "muse.call", logging.WARNING, model=self.model, attempt=attempt, outcome=kind,
                          duration_ms=round((time.perf_counter() - t) * 1000, 1))
                delay = self.backoff_s * 2 ** (attempt - 1) * (1 + random.random() * 0.25)
                out_of_time = time.monotonic() - start + delay >= self.deadline_s
                if kind not in MuseError.RETRYABLE or attempt == self.max_attempts or out_of_time:
                    raise MuseError(kind, f"{type(e).__name__}: {_redact(str(e))[:200]}", attempt) from None
                time.sleep(delay)
                continue
            choice = resp.choices[0]
            usage = getattr(resp, "usage", None)
            obs.event(log, "muse.call", model=self.model, attempt=attempt, outcome="ok",
                      duration_ms=round((time.perf_counter() - t) * 1000, 1),
                      finish_reason=choice.finish_reason,
                      completion_tokens=getattr(usage, "completion_tokens", None),
                      prompt_tokens=getattr(usage, "prompt_tokens", None))
            if not choice.message.content:
                raise MuseError("bad_response", f"empty reply (finish_reason={choice.finish_reason})", attempt)
            return choice.message.content, attempt
        raise MuseError("unavailable", "no attempts made")  # pragma: no cover

    def explain(self, facts: dict, alternatives: list[Alternative], caps: Caps) -> Explanation:
        content, _ = self._call(self._messages(facts))
        try:
            data = _parse_json(content)
        except ValueError as e:
            raise MuseError("bad_response", str(e)) from None
        reasoning = str(data.get("reasoning", "")).strip()
        if not (40 <= len(reasoning) <= 1500):
            raise MuseError("bad_response", "reasoning missing or out of length bounds")
        tradeoffs = {}
        for a in data.get("alternatives") or []:
            if isinstance(a, dict) and isinstance(a.get("tradeoff"), str) and 10 <= len(a["tradeoff"]) <= 400:
                tradeoffs[a.get("tier")] = a["tradeoff"].strip()
        if _is_static(facts) and any(_claims_measurement(t) for t in [reasoning, *tradeoffs.values()]):
            # Nothing ran, so "measured 100 ms" or "the test run lasted 120 s" would be false.
            raise MuseError("bad_response", "described a static (not run) estimate as measured")
        alts = [a.model_copy(update={"tradeoff": tradeoffs.get(a.tier, a.tradeoff)}) for a in alternatives]
        new_caps = _clamp_caps(data.get("recommended_caps") or {}, caps)
        return Explanation(reasoning, alts, new_caps, self.model)


def _redact(msg: str) -> str:
    key = os.environ.get("META_API_KEY")
    return msg.replace(key, "[redacted]") if key else msg


class FallbackExplainer:
    """Try Muse; on any failure (not configured, auth, timeout after retries, bad reply)
    use the rules. The fallback is logged and reported in Explanation.fallback_reason.
    Stateless, so one instance can serve concurrent estimates."""

    def __init__(self, primary: Explainer | None = None):
        self.primary = primary

    def explain(self, facts: dict, alternatives: list[Alternative], caps: Caps) -> Explanation:
        reason: str
        try:
            primary = self.primary or MuseExplainer()
            return primary.explain(facts, alternatives, caps)
        except MuseError as e:
            reason = f"{e.kind}: {_redact(str(e))[:200]}"
        except Exception as e:  # validation bugs etc. must not fail the estimate
            reason = f"error: {type(e).__name__}: {_redact(str(e))[:200]}"
        obs.event(log, "explainer.fallback", logging.WARNING, reason=reason)
        ex = RuleExplainer().explain(facts, alternatives, caps)
        ex.fallback_reason = reason
        return ex
