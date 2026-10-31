import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from shipcube_placement import (Caps, EstimateResult, FallbackExplainer, MuseExplainer, RuleExplainer,
                                estimate)
from shipcube_placement import placement as pl
from shipcube_placement.explain import _clamp_caps
from shipcube_profiler import CodeSummary, Hints, Profile, RequestStats

SAMPLES = Path(__file__).resolve().parents[2] / "profiler" / "samples"
CONTRACT = {"estimate_id", "workload_class", "confidence", "tier", "target", "resources", "monthly_cost_usd",
            "profile", "recommended_caps", "reasoning", "alternatives", "explained_by", "decided_by",
            "low_confidence", "model_version"}


def inline(app_dir: Path) -> dict:
    return {"kind": "inline", "files": [{"path": str(p.relative_to(app_dir)), "content": p.read_text()}
                                        for p in sorted(app_dir.rglob("*")) if p.is_file()]}


def web_profile(cpu=0.05, p95=0.3, mem=0.1, lat=0.02) -> Profile:
    return Profile(mode="measured", runner="local", kind="web", cpu_mean_cores=cpu, cpu_p95_cores=p95,
                   cpu_peak_cores=p95, mem_mean_gb=mem, mem_peak_gb=mem, active_s=5,
                   requests=RequestStats(sent=10, ok=10, latency_mean_s=lat, latency_p95_s=lat * 2))


# --- tiers, sizing, cost --------------------------------------------------------------

def test_tier_rules():
    web, script = CodeSummary(kind="web", framework="flask"), CodeSummary(kind="script")
    p = web_profile()
    assert pl.choose_tier("bursty_interactive", web, Hints(expected_users=20), p, 0.4, 0.3) == ("T1", "cloud-run-service")
    assert pl.choose_tier("steady_service", web, Hints(expected_users=20), p, 0.4, 0.3) == ("T3", "cloud-run-service")
    assert pl.choose_tier("bursty_interactive", web, Hints(expected_users=1000), p, 0.4, 0.3)[0] == "T3"
    assert pl.choose_tier("steady_service", web, Hints(), p, 6, 24) == ("T3", "gke-autopilot")
    sp = Profile(mode="measured", runner="local", kind="script")
    assert pl.choose_tier("batch_cron", script, Hints(schedule="0 2 * * *"), sp, 1, 1) == ("T2", "cloud-run-job")
    report = CodeSummary(kind="script", writes_outputs=["html"])
    assert pl.choose_tier("batch_cron", report, Hints(expected_users=40), sp, 1, 1) == ("T0", "static-snapshot")
    gpu = CodeSummary(kind="web", uses_gpu_libs=True)
    assert pl.choose_tier("compute_heavy", gpu, Hints(), p, 4, 12) == ("T4", "cloud-run-gpu")


def test_sizing_respects_cloud_run_cpu_memory_rules():
    p = web_profile(mem=5.0)  # 5 GiB * 1.5 -> 8 GiB needs >= 2 vCPU... and > 8 needs 4
    tr = pl.traffic(p, {"invocations_per_hour": 100}, Hints(expected_users=10))
    r = pl.size("T1", "memory_heavy", p, tr, Hints())
    assert r.memory == "8Gi" and float(r.cpu) >= 2
    small = pl.size("T1", "bursty_interactive", web_profile(p95=0.1), tr, Hints())
    assert (small.cpu, small.memory) == ("0.5", "512Mi")
    assert 1 <= small.max_instances <= 10


def test_single_process_capped_at_one_vcpu():
    p = Profile(mode="measured", runner="local", kind="script", cpu_mean_cores=0.95, cpu_p95_cores=1.0,
                cpu_peak_cores=1.0, mem_peak_gb=0.1, processes_max=1)
    assert pl.needs(p)[0] == 1.0


def test_cost_ranges_are_ordered_and_scale_with_users():
    costs = []
    for users in (10, 100, 1000):
        p = web_profile()
        h = Hints(expected_users=users)
        from shipcube_profiler import to_features
        f, _ = to_features(p, h)
        tr = pl.traffic(p, f, h)
        res = pl.size("T1", "bursty_interactive", p, tr, h)
        c, basis = pl.cost("T1", "cloud-run-service", res, tr, 0.9)
        assert c.low <= c.expected <= c.high
        assert basis["billing"] == "request-based"
        costs.append(c.expected)
    assert costs[0] < costs[1] < costs[2]


def test_always_on_costs_match_published_rates():
    from shipcube_placement.types import Resources
    res = Resources(cpu="1", memory="512Mi", min_instances=1, max_instances=2)
    c, _ = pl.cost("T3", "cloud-run-service", res, pl.Traffic("web"), 1.0)
    # 730 h of 1 vCPU + 0.5 GiB at instance-based rates
    assert c.expected == pytest.approx(730 * 3600 * (0.000018 + 0.5 * 0.000002), abs=0.01)


def test_pricing_is_cited():
    p = pl.pricing()
    assert "cloudbilling.googleapis.com" in p["source"] and p["fetched"]


# --- explainer ------------------------------------------------------------------------

class FakeClient:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        msg = SimpleNamespace(content=self.reply)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")])


def test_caps_clamping():
    rule = Caps(max_instances=2, concurrency=80, requests_per_minute=150, monthly_spend_usd=5,
                cpu="1", memory="512Mi", timeout_s=60)
    out = _clamp_caps({"max_instances": 10, "concurrency": 60, "monthly_spend_usd": 1, "cpu": "4",
                       "memory": "32Gi", "timeout_s": 3600, "bogus": 1}, rule)
    assert out.max_instances == 2            # can't raise
    assert out.concurrency == 60             # within bounds -> accepted
    assert out.monthly_spend_usd == 5        # below 0.5x -> ignored
    assert (out.cpu, out.memory) == ("1", "512Mi")
    assert out.timeout_s == 60               # above 2x -> ignored


@pytest.fixture(scope="module")
def dashboard_rules():
    pytest.importorskip("flask")
    return estimate(inline(SAMPLES / "dashboard"), {"expected_users": 25}, explainer=RuleExplainer())


@pytest.fixture(scope="module")
def batch_rules():
    return estimate(inline(SAMPLES / "nightly_batch"), {"schedule": "0 2 * * *"}, explainer=RuleExplainer())


def test_dashboard_estimate(dashboard_rules):
    r = dashboard_rules
    assert set(r.model_dump()) == CONTRACT
    assert r.workload_class == "bursty_interactive"
    assert (r.tier, r.target) == ("T1", "cloud-run-service")
    assert r.resources.min_instances == 0
    assert r.monthly_cost_usd.expected < 5
    assert r.explained_by == "rules"
    assert {a.tier for a in r.alternatives} == {"T3", "T0"}
    assert r.profile["measurements"]["requests"]["ok"] > 0


def test_batch_estimate(batch_rules):
    r = batch_rules
    assert r.workload_class == "batch_cron"
    assert (r.tier, r.target) == ("T2", "cloud-run-job")
    assert r.resources.schedule == "0 2 * * *"
    assert r.recommended_caps.max_instances == 1
    assert "hooks.example.com" in r.reasoning   # blocked egress is called out
    assert r.estimate_id.startswith("est_")


def test_image_source_falls_back_to_static():
    r = estimate({"kind": "image", "uri": "us-docker.pkg.dev/p/r/a@sha256:1"}, {"expected_users": 5},
                 explainer=RuleExplainer())
    assert r.profile["measurements"]["mode"] == "static"
    assert any("images" in n for n in r.profile["assumptions"])


def test_crashing_app_falls_back_to_static():
    src = {"kind": "inline", "files": [{"path": "main.py", "content": "raise SystemExit(2)\n"}]}
    r = estimate(src, {}, explainer=RuleExplainer())
    assert r.profile["measurements"]["mode"] == "static"


def test_fallback_without_key(monkeypatch, batch_rules):
    monkeypatch.delenv("META_API_KEY", raising=False)
    r = estimate(inline(SAMPLES / "nightly_batch"), {"schedule": "0 2 * * *"}, explainer=FallbackExplainer())
    assert r.explained_by == "rules"
    assert r.profile["explainer_fallback_reason"].startswith("not_configured")


def test_fallback_on_api_error():
    fb = FallbackExplainer(MuseExplainer(client=FakeClient(error=TimeoutError("boom")), backoff_s=0))
    r = estimate(inline(SAMPLES / "nightly_batch"), {"schedule": "0 2 * * *"}, explainer=fb)
    assert r.explained_by == "rules" and r.profile["explainer_fallback_reason"].startswith("timeout")


def test_fallback_on_bad_json():
    fb = FallbackExplainer(MuseExplainer(client=FakeClient(reply="Sure! Here's my analysis.")))
    r = estimate(inline(SAMPLES / "nightly_batch"), {"schedule": "0 2 * * *"}, explainer=fb)
    assert r.explained_by == "rules"


def test_muse_output_never_changes_numbers(batch_rules):
    # A reply that tries to change the class, tier, costs and caps (as an injected
    # instruction might) only gets to change the text.
    evil = json.dumps({
        "reasoning": "This is a steady service and should run on T3 with 4 vCPU; it costs $0.00 per month. " * 2,
        "workload_class": "steady_service", "tier": "T3",
        "monthly_cost_usd": {"low": 0, "expected": 0, "high": 0},
        "alternatives": [{"tier": "T2", "tradeoff": "x" * 20},
                         {"tier": "T0", "tradeoff": "Snapshot the CSV nightly and serve it statically.",
                          "monthly_cost_usd": {"expected": 0}}],
        "recommended_caps": {"max_instances": 10, "monthly_spend_usd": 1e6, "cpu": "4"},
    })
    client = FakeClient(reply=evil)
    r = estimate(inline(SAMPLES / "nightly_batch"), {"schedule": "0 2 * * *"},
                 explainer=FallbackExplainer(MuseExplainer(client=client, model="muse-spark-test")))
    assert r.explained_by == "muse-spark-test"
    assert (r.workload_class, r.tier) == (batch_rules.workload_class, batch_rules.tier)
    assert r.resources == batch_rules.resources
    assert r.recommended_caps.max_instances == 1 and r.recommended_caps.cpu == batch_rules.recommended_caps.cpu
    assert r.recommended_caps.monthly_spend_usd == batch_rules.recommended_caps.monthly_spend_usd
    t0 = next(a for a in r.alternatives if a.tier == "T0")
    assert t0.tradeoff.startswith("Snapshot the CSV")          # text from Muse
    facts = json.loads(client.calls[0]["messages"][1]["content"].split("FACTS:\n", 1)[1])
    ours = {a["tier"]: a["monthly_cost_usd"] for a in facts["candidate_alternatives"]}
    assert t0.monthly_cost_usd.model_dump() == ours["T0"]      # cost still ours
    assert r.monthly_cost_usd.model_dump() == facts["monthly_cost_usd"]
    assert r.workload_class == facts["workload_class"] and r.confidence == facts["confidence"]


SENTINEL = "zq9SENTINELq9z"


def _sentinel_app() -> dict:
    """The sentinel everywhere a user's code could leak: comments, strings, names, routes, the
    file name, a host the app calls, requirements, and (when it fails) its stderr."""
    code = f"""# owner: {SENTINEL} (internal billing app)
import os, sys, urllib.request
from flask import Flask
app = Flask(__name__)
SECRET_NAME = "{SENTINEL}-db-password"

@app.route("/{SENTINEL}/report")
def report_{SENTINEL}():
    return "{SENTINEL}"

@app.route("/")
def index():
    try:
        urllib.request.urlopen("http://{SENTINEL.lower()}.example.com/x", timeout=1)
    except Exception:
        pass
    return "ok"

if __name__ == "__main__":
    print("{SENTINEL} starting", file=sys.stderr)
    app.run(port=int(os.environ.get("PORT", 8080)))
"""
    return {"kind": "inline", "files": [{"path": "main.py", "content": code},
                                        {"path": f"{SENTINEL}_helpers.py", "content": f"X = '{SENTINEL}'\n"},
                                        {"path": "requirements.txt", "content": "flask\n"}]}


def _muse_request(client) -> str:
    return json.dumps(client.calls[0], default=str)


def test_user_code_never_reaches_muse_static():
    client = FakeClient(reply="not json")
    estimate(_sentinel_app(), {}, runner=StaticRunner(reason="disabled"),
             explainer=FallbackExplainer(MuseExplainer(client=client)))
    body = _muse_request(client)
    assert SENTINEL.lower() not in body.lower()
    assert "untrusted_code" not in body and "main.py" not in body


def test_user_code_never_reaches_muse_measured_or_failed():
    """Measured runs carry the app's own strings in routes, remote hosts and (on failure) stderr notes."""
    class Measured:
        name = "fake"

        def __init__(self, exit_code):
            self.exit_code = exit_code

        def run(self, *a):
            p = Profile(mode="measured", runner="gcp", kind="web", cpu_mean_cores=0.2, cpu_p95_cores=0.5,
                        cpu_peak_cores=0.6, mem_mean_gb=0.1, mem_peak_gb=0.2, active_s=8, cpu_limit_cores=1.0,
                        mem_limit_gb=2.0, exit_code=self.exit_code, net_calls=1, net_blocked=1,
                        remote_hosts=[f"{SENTINEL.lower()}.example.com"],
                        requests=RequestStats(sent=120, ok=120 if self.exit_code == 0 else 0,
                                              latency_mean_s=0.02, latency_p95_s=0.05),
                        routes=[RouteStats(path=f"/{SENTINEL}/report", sent=10, ok=10, cpu_mean_cores=0.95,
                                           cpu_p95_cores=1.0, latency_mean_s=0.3, latency_p95_s=0.4)],
                        notes=[f"exited with code 1: Traceback ... {SENTINEL} ..."])
            return p
    bodies = {}
    for exit_code in (0, 1):
        client = FakeClient(reply="not json")
        r = estimate(_sentinel_app(), {"expected_users": 5}, runner=Measured(exit_code),
                     explainer=FallbackExplainer(MuseExplainer(client=client)))
        bodies[exit_code] = _muse_request(client)
        assert SENTINEL.lower() not in bodies[exit_code].lower(), exit_code
        if exit_code:   # the user still sees the app's own stderr in their result; Muse doesn't
            assert any(SENTINEL in a for a in r.profile["assumptions"])
    facts = json.loads(json.loads(bodies[0])["messages"][1]["content"].split("FACTS:\n", 1)[1])
    assert facts["code"]["route_count"] == 2 and "routes" not in facts["code"]
    assert facts["measured"]["remote_host_count"] == 1


def test_muse_gets_route_ranks_and_assumption_codes_not_text(batch_rules):
    from shipcube_placement.explain import muse_facts
    facts = {"workload_class": "compute_heavy", "confidence": 0.75, "tier": "T1", "target": "cloud-run-service",
             "measured": {"mode": "measured", "kind": "web", "cpu_mean_cores": 0.5, "remote_hosts": ["a", "b"],
                          "routes": [{"path": "/secret", "cpu_mean_cores": 0.9, "latency_mean_ms": 95, "ok": 10, "sent": 10}]},
             "code": {"framework": "flask", "kind": "web", "routes": ["/secret", "/"], "entrypoint": "secret.py"},
             "hints": {"expected_users": 5, "schedule": "0 2 * * *; rm -rf /", "traffic": "office_hours"},
             "assumptions": ["traffic model: office hours, 30 requests per active user-hour",
                             "route /secret kept the sandbox's 1 vCPU busy on its own (mean 0.9)",
                             "exited with code 1: /secret/token.txt not found",
                             "the rule set alone would call this bursty_interactive"]}
    out = muse_facts(facts)
    text = json.dumps(out)
    assert "secret" not in text and "rm -rf" not in text
    assert out["assumption_codes"] == ["traffic_office_hours", "heaviest_route_saturates_sandbox_cpu",
                                       "rules_would_say_bursty_interactive"]
    assert out["measured"]["routes"] == [{"rank": 1, "cpu_mean_cores": 0.9, "latency_mean_ms": 95, "ok": 10, "sent": 10}]
    assert out["hints"]["schedule"] is None and out["code"]["route_count"] == 2


def test_rule_explanation_is_unchanged_and_still_local(batch_rules):
    from shipcube_placement.explain import rule_reasoning
    assert batch_rules.explained_by == "rules" and batch_rules.reasoning


def test_api_key_is_never_in_the_result(monkeypatch):
    monkeypatch.setenv("META_API_KEY", "sk-test-sentinel-123")
    fb = FallbackExplainer(MuseExplainer(client=FakeClient(error=RuntimeError("401 sk-test-sentinel-123"))))
    r = estimate(inline(SAMPLES / "nightly_batch"), {}, explainer=fb)
    assert r.explained_by == "rules"
    assert "sk-test-sentinel-123" not in json.dumps(r.model_dump())
    assert "[redacted]" in r.profile["explainer_fallback_reason"]


@pytest.mark.skipif(os.environ.get("SHIPCUBE_LIVE_MUSE") != "1", reason="set SHIPCUBE_LIVE_MUSE=1 to call the real API")
def test_live_muse(batch_rules):
    from dotenv import find_dotenv, load_dotenv
    load_dotenv(find_dotenv(usecwd=True))
    fb = FallbackExplainer()
    r = estimate(inline(SAMPLES / "nightly_batch"), {"schedule": "0 2 * * *"}, explainer=fb)
    assert r.explained_by.startswith("muse-spark"), fb.last_error
    assert r.tier == batch_rules.tier


# --- low-confidence fallback, versioning, Muse retries --------------------------------

def test_result_carries_model_version(batch_rules):
    from shipcube_classifier import load_model
    assert batch_rules.model_version == load_model().version
    assert batch_rules.decided_by == "classifier" and batch_rules.low_confidence is False


def test_low_confidence_falls_back_to_rules_and_is_flagged(monkeypatch):
    from shipcube_classifier import load_model
    monkeypatch.setattr(load_model().model, "min_confidence", 1.01)   # nothing clears the gate
    r = estimate(inline(SAMPLES / "nightly_batch"), {"schedule": "0 2 * * *"}, explainer=RuleExplainer())
    assert r.decided_by == "rules" and r.low_confidence is True
    assert r.workload_class == r.profile["rules_class"] == "batch_cron"
    assert "wasn't confident" in r.reasoning
    assert r.confidence == r.profile["class_probabilities"]["batch_cron"]


def _openai_errors():
    import httpx2 as httpx
    import openai
    req = httpx.Request("POST", "https://api.meta.ai/v1/chat/completions")
    return {"timeout": openai.APITimeoutError(request=req),
            "auth": openai.AuthenticationError("bad key", response=httpx.Response(401, request=req), body=None),
            "rate": openai.RateLimitError("slow down", response=httpx.Response(429, request=req), body=None)}


class SequenceClient(FakeClient):
    """Raises the given errors in order, then returns `reply`."""

    def __init__(self, errors, reply):
        super().__init__(reply=reply)
        self.errors = list(errors)

    def _create(self, **kw):
        self.calls.append(kw)
        if self.errors:
            raise self.errors.pop(0)
        msg = SimpleNamespace(content=self.reply)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")])


GOOD = json.dumps({"reasoning": "A nightly job that runs for about a second on one core and then exits, so a "
                                "scheduled Cloud Run job fits.", "alternatives": [], "recommended_caps": {}})


def test_muse_retries_transient_errors_then_succeeds(batch_rules):
    errs = _openai_errors()
    client = SequenceClient([errs["timeout"], errs["rate"]], GOOD)
    m = MuseExplainer(client=client, model="muse-spark-test", backoff_s=0)
    r = estimate(inline(SAMPLES / "nightly_batch"), {"schedule": "0 2 * * *"}, explainer=FallbackExplainer(m))
    assert r.explained_by == "muse-spark-test"
    assert len(client.calls) == 3
    assert all(c["timeout"] <= m.timeout_s for c in client.calls)


def test_muse_does_not_retry_auth_errors():
    client = SequenceClient([_openai_errors()["auth"]], GOOD)
    r = estimate(inline(SAMPLES / "nightly_batch"), {}, explainer=FallbackExplainer(
        MuseExplainer(client=client, backoff_s=0)))
    assert r.explained_by == "rules" and len(client.calls) == 1
    assert r.profile["explainer_fallback_reason"].startswith("auth")


def test_muse_gives_up_after_max_attempts():
    errs = _openai_errors()
    client = SequenceClient([errs["timeout"]] * 5, GOOD)
    r = estimate(inline(SAMPLES / "nightly_batch"), {}, explainer=FallbackExplainer(
        MuseExplainer(client=client, backoff_s=0, max_attempts=3)))
    assert r.explained_by == "rules" and len(client.calls) == 3


def test_production_without_profiler_config_fails_loudly(monkeypatch):
    import os
    from shipcube_profiler import ProfilerError
    monkeypatch.delenv("SHIPCUBE_DEV_MODE")
    for k in list(os.environ):
        if k.startswith("SHIPCUBE_PROFILER_"):
            monkeypatch.delenv(k)
    with pytest.raises(ProfilerError):
        estimate(inline(SAMPLES / "nightly_batch"), {}, explainer=RuleExplainer())


def _docker_up() -> bool:
    import shutil
    import subprocess
    return bool(shutil.which("docker")) and subprocess.run(["docker", "version"], capture_output=True,
                                                            timeout=20).returncode == 0


@pytest.mark.skipif(not _docker_up(), reason="needs a Docker daemon")
def test_estimate_through_the_local_docker_sandbox(monkeypatch):
    """SHIPCUBE_PROFILER=local: estimate() picks the Docker sandbox by itself (no runner
    passed), measures in the container, and feeds the same classifier and explainer."""
    monkeypatch.setenv("SHIPCUBE_PROFILER", "local")
    monkeypatch.delenv("SHIPCUBE_DEV_MODE")
    monkeypatch.delenv("META_API_KEY", raising=False)
    r = estimate(inline(SAMPLES / "nightly_batch"), {"schedule": "0 2 * * *"}, explainer=FallbackExplainer())
    m = r.profile["measurements"]
    assert m["runner"] == "docker" and m["mode"] == "measured" and m["exit_code"] == 0
    assert m["net_blocked"] >= 1
    assert r.workload_class == "batch_cron" and r.tier == "T2"
    assert r.explained_by == "rules" and r.profile["explainer_fallback_reason"].startswith("not_configured")


# --- static reasons (docs/research/profiler-sandbox.md §9) ----------------------------------

def _reason(r) -> str | None:
    return r.profile["measurements"]["static_reason"]


class _Raises:
    name = "fake"

    def __init__(self, err):
        self.err = err

    def run(self, *a):
        raise self.err


class _Measured:
    name = "fake"

    def __init__(self, profile):
        self.profile = profile

    def run(self, *a):
        return self.profile



def _batch_src():
    return inline(SAMPLES / "nightly_batch")


def test_static_reasons_are_reported():
    from shipcube_profiler import ProfilerError, StaticRunner
    img = estimate({"kind": "image", "uri": "us-docker.pkg.dev/p/r/a@sha256:1"}, {}, explainer=RuleExplainer())
    assert _reason(img) == "image_source"
    for err, reason in ((ProfilerError("policy", "x", reason="requirements_policy"), "requirements_policy"),
                        (ProfilerError("build", "x", reason="too_large"), "too_large"),
                        (ProfilerError("logs", "x"), "profiler_error")):
        r = estimate(_batch_src(), {}, runner=_Raises(err), explainer=RuleExplainer())
        assert r.profile["measurements"]["mode"] == "static" and _reason(r) == reason
    rejected = estimate(_batch_src(), {}, runner=_Raises(ProfilerError("run", "x", reason="result_rejected")),
                        explainer=RuleExplainer())
    assert rejected.profile["measurements"]["abuse_signals"] == ["result_forged"]
    # The control plane's own decisions pass straight through.
    q = estimate(_batch_src(), {}, runner=StaticRunner(reason="quota"), explainer=RuleExplainer())
    assert _reason(q) == "quota"


def test_gpu_apps_are_not_run():
    src = {"kind": "inline", "files": [{"path": "main.py", "content": "import torch\nprint(torch.cuda.is_available())\n"}]}
    r = estimate(src, {}, runner=_Raises(AssertionError("must not run")), explainer=RuleExplainer())
    assert _reason(r) == "gpu"


def test_failed_runs_keep_their_abuse_signals():
    crashed = Profile(mode="measured", runner="fake", kind="script", exit_code=1, net_blocked=80,
                      abuse_signals=["egress_flood"])
    r = estimate(_batch_src(), {}, runner=_Measured(crashed), explainer=RuleExplainer())
    m = r.profile["measurements"]
    assert m["mode"] == "static" and m["static_reason"] == "app_failed" and m["abuse_signals"] == ["egress_flood"]
    dash = inline(SAMPLES / "dashboard")
    silent = Profile(mode="measured", runner="fake", kind="web", requests=RequestStats(sent=120, ok=0))
    assert _reason(estimate(dash, {}, runner=_Measured(silent), explainer=RuleExplainer())) == "did_not_start"


def test_measured_estimates_have_no_reason(batch_rules):
    assert _reason(batch_rules) is None


# --- static estimates aren't described as measured ----------------------------------------

def _muse_reply(reasoning: str, tradeoff: str = "Always on: no cold starts, but you pay while idle all month.") -> str:
    return json.dumps({"reasoning": reasoning, "alternatives": [{"tier": "T3", "tradeoff": tradeoff}],
                       "recommended_caps": {}})


IMAGE = {"kind": "image", "uri": "us-docker.pkg.dev/cloudrun/container/hello"}
WRONG = ("Measured 100 ms mean latency with low CPU, and the test run lasted 120 seconds, so this bursty "
         "dashboard fits a scale-to-zero service well.")
RIGHT = ("The app wasn't run, so these are typical values for this app shape: about 0.05 vCPU and 150 MiB, "
         "which fits a scale-to-zero service; nothing was measured.")


def test_static_estimate_prompt_says_nothing_ran():
    client = FakeClient(reply=_muse_reply(RIGHT))
    r = estimate(IMAGE, {"expected_users": 5}, explainer=FallbackExplainer(MuseExplainer(client=client, model="m")))
    assert r.explained_by == "m" and r.reasoning == RIGHT                 # negated "measured" is fine
    user = client.calls[0]["messages"][1]["content"]
    assert '"typical_values_not_measured"' in user and '"measured":' not in user
    assert '"static_reason": "image_source"' in user
    assert "typical values for this app shape" in client.calls[0]["messages"][0]["content"]


@pytest.mark.parametrize("reasoning,tradeoff", [
    (WRONG, None),
    ("These numbers fit a scale-to-zero service: during the run it observed about 0.05 vCPU and 150 MiB of memory.", None),
    (RIGHT, "Always on: the load test showed 80 ms latency, but you pay while idle all month."),
])
def test_static_estimate_that_claims_measurements_falls_back_to_rules(reasoning, tradeoff):
    reply = _muse_reply(reasoning, tradeoff) if tradeoff else _muse_reply(reasoning)
    r = estimate(IMAGE, {"expected_users": 5}, explainer=FallbackExplainer(MuseExplainer(client=FakeClient(reply=reply))))
    assert r.explained_by == "rules" and "measured" in r.profile["explainer_fallback_reason"]
    assert "typical values" in r.reasoning and "container images aren't run" in r.reasoning
    for a in r.alternatives:
        assert "load test" not in a.tradeoff


def test_measured_estimates_may_say_measured(batch_rules):
    client = FakeClient(reply=_muse_reply(WRONG.replace("dashboard", "job")))
    r = estimate(inline(SAMPLES / "nightly_batch"), {"schedule": "0 2 * * *"},
                 explainer=FallbackExplainer(MuseExplainer(client=client, model="m")))
    assert r.profile["measurements"]["mode"] == "measured" and r.explained_by == "m"
    assert '"measured":' in client.calls[0]["messages"][1]["content"]


# --- static confidence, read-only snapshots, sandbox-relative classes ----------------------

from shipcube_placement.estimate import STATIC_MAX_CONFIDENCE, sandbox_class
from shipcube_profiler import StaticRunner


def _web(**kw) -> Profile:
    base = dict(mode="measured", runner="gcp", kind="web", cpu_mean_cores=0.05, cpu_p95_cores=0.3, cpu_peak_cores=0.4,
                mem_mean_gb=0.1, mem_peak_gb=0.15, active_s=10, cpu_limit_cores=1.0, mem_limit_gb=2.0,
                requests=RequestStats(sent=120, ok=120, latency_mean_s=0.02, latency_p95_s=0.05))
    return Profile(**{**base, **kw})


FLASK_CPU = """from flask import Flask, request
app = Flask(__name__)

@app.route("/")
def index():
    return "<form><input type='range' name='ms'></form>"

@app.route("/burn")
def burn():
    ms = int(request.args.get("ms", 200))
    import time
    end = time.perf_counter() + ms / 1000
    n = 0
    while time.perf_counter() < end:
        n += 1
    return str(n)
"""


def test_static_estimates_are_never_confident():
    for src in (IMAGE, inline(SAMPLES / "dashboard"),
                {"kind": "inline", "files": [{"path": "app.py", "content": FLASK_CPU},
                                             {"path": "requirements.txt", "content": "flask\nnumpy\n"}]}):
        r = estimate(src, {"expected_users": 5}, runner=StaticRunner(reason="disabled"), explainer=RuleExplainer())
        assert r.profile["measurements"]["mode"] == "static"
        assert r.confidence <= STATIC_MAX_CONFIDENCE and r.low_confidence
        assert any("not run" in a for a in r.profile["assumptions"])


def test_measured_estimates_keep_the_classifier_confidence(dashboard_rules):
    if dashboard_rules.profile["measurements"]["mode"] == "measured":
        assert dashboard_rules.confidence > STATIC_MAX_CONFIDENCE


def test_snapshot_is_offered_only_for_read_only_apps():
    from shipcube_profiler import summarize
    interactive = {"kind": "inline", "files": [{"path": "app.py", "content": FLASK_CPU},
                                               {"path": "requirements.txt", "content": "flask\n"}]}
    r = estimate(interactive, {"expected_users": 5}, runner=StaticRunner(reason="disabled"), explainer=RuleExplainer())
    assert "T0" not in {a.tier for a in r.alternatives}
    read_only = {"kind": "inline", "files": [{"path": "app.py", "content":
                 "from flask import Flask\napp = Flask(__name__)\n@app.route('/')\ndef i():\n    return 'report'\n"},
                 {"path": "requirements.txt", "content": "flask\n"}]}
    r = estimate(read_only, {"expected_users": 5}, runner=StaticRunner(reason="disabled"), explainer=RuleExplainer())
    assert "T0" in {a.tier for a in r.alternatives}


@pytest.mark.parametrize("code,takes_input", [
    ("@app.route('/x', methods=['GET', 'POST'])\ndef x(): pass", True),
    ("@app.post('/items')\ndef add(): pass", True),
    ("v = request.args.get('n')", True),
    ("data = request.get_json()", True),
    ("def f(q: str = Query(None)): pass", True),
    ("st.slider('n', 0, 10)", True),
    ("@app.route('/')\ndef i(): return 'hi'", False),
])
def test_input_detection(tmp_path, code, takes_input):
    from shipcube_profiler import summarize
    (tmp_path / "app.py").write_text("from flask import Flask, request\napp = Flask(__name__)\n" + code + "\n")
    assert summarize(tmp_path).accepts_input is takes_input


def test_input_detection_reads_templates(tmp_path):
    from shipcube_profiler import summarize
    (tmp_path / "app.py").write_text("from flask import Flask, render_template\napp = Flask(__name__)\n"
                                     "@app.route('/')\ndef i(): return render_template('i.html')\n")
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "i.html").write_text("<form method=post><input name=q></form>")
    assert summarize(tmp_path).accepts_input


@pytest.mark.parametrize("kw,want", [
    (dict(cpu_mean_cores=0.85, cpu_p95_cores=0.99, cpu_peak_cores=1.0), "compute_heavy"),
    (dict(mem_peak_gb=1.2), "memory_heavy"),
    (dict(killed_reason="memory over 1843 MB"), "memory_heavy"),
    (dict(), None),                                                       # an ordinary light web app
    (dict(cpu_mean_cores=0.3, cpu_p95_cores=0.99, cpu_peak_cores=1.0), None),   # short spikes aren't saturation
    (dict(cpu_mean_cores=0.9, cpu_p95_cores=1.0, kind="script"), None),   # scripts stay batch_cron etc.
    (dict(cpu_mean_cores=0.9, cpu_p95_cores=1.0, mode="static"), None),
    (dict(cpu_mean_cores=0.9, cpu_p95_cores=1.0, cpu_limit_cores=0.0), None),    # limit unknown (in-process)
    (dict(cpu_mean_cores=1.3, cpu_p95_cores=1.9, cpu_limit_cores=2.0), "compute_heavy"),
])
def test_sandbox_relative_classes(kw, want):
    got = sandbox_class(_web(**kw))
    assert (got[0] if got else None) == want


def test_cpu_bound_web_app_is_sized_for_it():
    class Measured:
        name = "fake"

        def run(self, *a):
            return _web(cpu_mean_cores=0.9, cpu_p95_cores=1.0, cpu_peak_cores=1.0,
                        requests=RequestStats(sent=120, ok=120, latency_mean_s=2.0, latency_p95_s=5.0))
    src = {"kind": "inline", "files": [{"path": "app.py", "content": FLASK_CPU},
                                       {"path": "requirements.txt", "content": "flask\n"}]}
    r = estimate(src, {"expected_users": 50}, runner=Measured(), explainer=RuleExplainer())
    assert r.workload_class == "compute_heavy" and r.decided_by == "rules" and not r.low_confidence
    assert r.resources.concurrency <= 2                  # CPU-bound requests don't share a vCPU well
    assert r.resources.timeout_s >= 100                  # 20x the measured p95
    assert any("compute-heavy" in a for a in r.profile["assumptions"])
    light = estimate(src, {"expected_users": 50}, runner=StaticRunner(reason="disabled"), explainer=RuleExplainer())
    assert r.monthly_cost_usd.expected > light.monthly_cost_usd.expected


def test_cpu_bound_web_app_measured_in_the_docker_sandbox(tmp_path, monkeypatch):
    """The loadlab case end to end: a Flask app that burns CPU per request, profiled for real."""
    import shutil
    import subprocess
    if not shutil.which("docker") or subprocess.run(["docker", "version"], capture_output=True).returncode:
        pytest.skip("needs a Docker daemon")
    monkeypatch.setenv("SHIPCUBE_PROFILER", "local")
    monkeypatch.delenv("SHIPCUBE_DEV_MODE")
    burn = FLASK_CPU.replace('int(request.args.get("ms", 200))', '400').replace(
        '@app.route("/burn")', '@app.route("/api/burn")')
    src = {"kind": "inline", "files": [{"path": "app.py", "content": burn},
                                       {"path": "requirements.txt", "content": "flask==3.0.3\n"}]}
    r = estimate(src, {"expected_users": 50}, explainer=RuleExplainer())
    m = r.profile["measurements"]
    assert m["mode"] == "measured", r.profile["assumptions"]
    assert r.workload_class == "compute_heavy", (m, r.profile["assumptions"])
    assert "T0" not in {a.tier for a in r.alternatives}


# --- per-route probes: compute-heavy judged on the heaviest route, not the mix ----------------

from shipcube_profiler import RouteStats


def _routes(*specs):
    return [RouteStats(path=p, sent=10, ok=10, cpu_mean_cores=m, cpu_p95_cores=p95, latency_mean_s=lat,
                       latency_p95_s=lat * 1.5) for p, m, p95, lat in specs]


def test_a_cpu_heavy_route_counts_even_when_the_mix_averages_it_away():
    # loadlab, measured on GCP: mean 0.58 over the mixed load test, just under the 0.6 line.
    mixed = dict(cpu_mean_cores=0.58, cpu_p95_cores=1.29, cpu_peak_cores=1.57)
    p = _web(**mixed, routes=_routes(("/api/cpu", 0.97, 1.0, 0.21), ("/api/io", 0.01, 0.02, 0.5),
                                     ("/healthz", 0.01, 0.03, 0.002), ("/", 0.02, 0.05, 0.003)))
    cls, note = sandbox_class(p)
    assert cls == "compute_heavy" and "/api/cpu" in note
    assert sandbox_class(_web(**mixed)) is None                       # without probes: the old near miss
    light = _web(**mixed, routes=_routes(("/api/io", 0.01, 0.02, 0.5), ("/", 0.3, 0.95, 0.01)))
    assert sandbox_class(light) is None                               # a short spike on one route isn't saturation
    failed = _web(**mixed, routes=[RouteStats("/api/cpu", sent=10, ok=0, cpu_mean_cores=0.99, cpu_p95_cores=1.0)])
    assert sandbox_class(failed) is None                              # a route that never answered doesn't count


def test_routes_round_trip_and_drive_the_timeout():
    p = _web(routes=_routes(("/api/slow", 0.9, 1.0, 8.0), ("/", 0.01, 0.02, 0.003)))
    back = Profile.from_dict(p.to_dict())
    assert back.routes == p.routes and back.heaviest_route().path == "/api/slow"

    class Measured:
        name = "fake"

        def run(self, *a):
            return p
    src = {"kind": "inline", "files": [{"path": "app.py", "content": FLASK_CPU},
                                       {"path": "requirements.txt", "content": "flask\n"}]}
    r = estimate(src, {"expected_users": 10}, runner=Measured(), explainer=RuleExplainer())
    assert r.resources.timeout_s >= 240                               # 20x the slow route's p95 (12 s), capped at 300
    assert r.profile["measurements"]["routes"][0]["path"] == "/api/slow"


LOADLAB_LIKE = """import time
from flask import Flask, request, jsonify
app = Flask(__name__)

@app.route("/api/cpu")
def cpu():
    ms = int(request.args.get("ms", 200))
    t0, x = time.perf_counter(), 0
    while (time.perf_counter() - t0) * 1000 < ms:
        x += 1
    return jsonify(x=x)

@app.route("/api/io")
def io():
    time.sleep(0.5)
    return jsonify(ok=True)

@app.route("/api/stats")
def stats():
    return jsonify(ok=True)

@app.route("/healthz")
def healthz():
    return "ok"

@app.route("/")
def index():
    return "<input type=range>"

if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
"""


def test_mixed_route_app_measured_in_the_docker_sandbox(monkeypatch):
    """The loadlab case for real: the CPU route is diluted in the mixed load test, but its own probe
    pins the sandbox's vCPU, so the app is compute-heavy and the probe names the route."""
    import shutil
    import subprocess
    if not shutil.which("docker") or subprocess.run(["docker", "version"], capture_output=True).returncode:
        pytest.skip("needs a Docker daemon")
    monkeypatch.setenv("SHIPCUBE_PROFILER", "local")
    monkeypatch.delenv("SHIPCUBE_DEV_MODE")
    src = {"kind": "inline", "files": [{"path": "main.py", "content": LOADLAB_LIKE},
                                       {"path": "requirements.txt", "content": "flask==3.0.3\n"}]}
    r = estimate(src, {"expected_users": 25}, explainer=RuleExplainer())
    m = r.profile["measurements"]
    assert m["mode"] == "measured", r.profile["assumptions"]
    routes = {x["path"]: x for x in m["routes"]}
    assert set(routes) == {"/", "/api/cpu", "/api/io", "/api/stats", "/healthz"}
    top = max(routes.values(), key=lambda x: x["cpu_mean_cores"])
    assert top["path"] == "/api/cpu" and top["cpu_mean_cores"] > routes["/api/io"]["cpu_mean_cores"] + 0.3
    assert r.workload_class == "compute_heavy", (m["cpu_mean_cores"], top, r.profile["assumptions"])
    assert any("/api/cpu" in a for a in r.profile["assumptions"])


# --- abuse pre-screen in estimate() --------------------------------------------------------

def test_high_confidence_abuse_refuses_the_estimate_before_anything_runs():
    from shipcube_placement import AbuseRefused

    class MustNotRun:
        name = "fake"

        def run(self, *a):
            raise AssertionError("profiled an app the pre-screen refused")
    src = {"kind": "inline", "files": [{"path": "main.py", "content": "POOL = 'stratum+tcp://p.example:3333'\nprint(1)\n"}]}
    with pytest.raises(AbuseRefused) as e:
        estimate(src, {}, runner=MustNotRun(), explainer=RuleExplainer())
    assert e.value.codes == ["crypto_miner"]


def test_low_confidence_abuse_is_a_signal_on_the_result():
    src = {"kind": "inline", "files": [
        {"path": "app.py", "content": "from flask import Flask\nimport nmap\napp = Flask(__name__)\n"
                                      "@app.route('/')\ndef i():\n    return 'ok'\n"},
        {"path": "requirements.txt", "content": "flask\n"}]}
    r = estimate(src, {}, runner=StaticRunner(reason="disabled"), explainer=RuleExplainer())
    assert r.profile["prescreen"]["verdict"] == "flagged"
    assert "prescreen_network_scanner" in r.profile["measurements"]["abuse_signals"]


def test_clean_apps_carry_an_ok_verdict(batch_rules):
    assert batch_rules.profile["prescreen"] == {"verdict": "ok", "reasons": [], "signals": [], "hits": [],
                                                "incomplete": False}


@pytest.mark.parametrize("schedule,sent", [
    ("0 2 * * *", "0 2 * * *"), ("*/15 * * * *", "*/15 * * * *"), ("0 9 * * 1-5", "0 9 * * 1-5"),
    ("0 0,12 * * *", "0 0,12 * * *"), ("@Daily", "@daily"), ("  0   2 * * *  ", "0 2 * * *"),
    ("0 2 * * *; rm -rf /", None), ("every day at 2am", None), ("0 2 * * MON", None),
    ("0 2 * * * extra", None), ("0 2 * *", None), ("@reboot", None), ("99 99 * * *", None),
    ("a b c d e", None), ("0 2 * * * # acme-billing-prod", None), (None, None), (5, None),
])
def test_schedule_hint_reaches_muse_only_as_strict_cron(schedule, sent):
    from shipcube_placement.explain import muse_facts
    assert muse_facts({"hints": {"schedule": schedule}})["hints"]["schedule"] == sent
