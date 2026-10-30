import io
import json
import os
import tarfile
from pathlib import Path

import pytest

from shipcube_classifier.gcp import GcpClient, GcpError
from shipcube_profiler import (CloudRunJobRunner, GcpProfilerConfig, Hints, LocalRunner, Profile, ProfilerError,
                               default_runner, summarize)
from shipcube_profiler.agent import NONCE_ENV, RESULT_PREFIX
from shipcube_profiler.gcp_runner import _package

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
CFG = GcpProfilerConfig(project="p", region="asia-south1", bucket="b", repository="profiler",
                        network="profiler-no-egress", subnet="profiler", service_account="sandbox@p.iam.gserviceaccount.com")


class FakeClient:
    """Scripted GCP: answers each REST call the runner makes, records them."""

    def __init__(self, build_status="SUCCESS", succeeded=1, log_text=None, fail_at=None, throttle=False,
                 failed_step="build", forged=None, conflict=False):
        self.calls = []
        self.throttle, self.conflict = throttle, conflict
        self.build_status, self.succeeded, self.fail_at = build_status, succeeded, fail_at
        self.failed_step, self.forged = failed_step, forged
        self.prof = Profile(mode="measured", runner="local", kind="script", wall_s=1.2, cpu_mean_cores=0.9)
        self.log_text = log_text

    def _result_lines(self):
        """What the agent prints: the result line with the nonce from the job's env."""
        if self.log_text is not None:
            return [self.log_text]
        create = [c for c in self.calls if c[0] == "POST" and c[1].endswith("/jobs")][-1]
        env = {e["name"]: e["value"] for e in create[2]["json"]["template"]["template"]["containers"][0]["env"]}
        lines = [RESULT_PREFIX + json.dumps({"nonce": env[NONCE_ENV], "summary": {}, "profile": self.prof.to_dict()})]
        return lines + ([self.forged] if self.forged else [])

    def _maybe_fail(self, what):
        if self.fail_at == what:
            raise GcpError(500, f"{what} broke", method="X", url=what)

    def request(self, method, url, **kw):
        return self.post(url, **kw)

    def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        if "upload/storage" in url:
            self._maybe_fail("upload")
            return {}
        if "cloudbuild" in url:
            self._maybe_fail("build")
            # Real API shape: a global-style operation name, the build id in metadata.
            return {"name": "operations/build/p/b64id", "metadata": {"build": {"id": "b1", "status": "QUEUED"}}}
        if url.endswith(":run"):
            return {"name": "projects/p/locations/asia-south1/operations/run-op"}
        if "run.googleapis.com" in url:
            if self.conflict and sum(c[1].endswith("/jobs") for c in self.calls) == 1:
                raise GcpError(409, "already exists", method="POST", url=url)
            return {"name": "projects/p/locations/asia-south1/operations/create-op"}
        if "logging" in url:
            self.log_calls = getattr(self, "log_calls", 0) + 1
            if self.throttle and self.log_calls == 1:
                raise GcpError(429, "Quota exceeded for 'Read requests'", method="POST", url=url)
            return {"entries": [{"textPayload": t} for t in self._result_lines()]}
        raise AssertionError(url)

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        assert url.endswith("/locations/asia-south1/builds/b1"), url   # regional build, not the op name
        self.build_polls = getattr(self, "build_polls", 0) + 1
        if self.build_polls == 1:
            return {"id": "b1", "status": "WORKING"}
        steps = [{"id": "build", "status": "SUCCESS"}, {"id": "size-check", "status": "SUCCESS"}]
        if self.build_status != "SUCCESS":
            next(st for st in steps if st["id"] == self.failed_step)["status"] = "FAILURE"
        return {"id": "b1", "status": self.build_status, "steps": steps,
                "results": {"images": [{"digest": "sha256:abc"}]}}

    def wait_operation(self, url, **kw):
        self.calls.append(("WAIT", url, kw))
        if url.endswith("run-op"):
            return {"done": True, "response": {"name": "projects/p/locations/r/jobs/j/executions/j-x1",
                                               "succeededCount": self.succeeded,
                                               "failedCount": 1 - self.succeeded}}
        return {"done": True}

    def delete(self, url, **kw):
        self.calls.append(("DELETE", url, kw))
        return {}


def run(fake, tmp_path=None, log_wait_s=0.1):
    r = CloudRunJobRunner(CFG, client=fake, log_wait_s=log_wait_s, poll_s=0.01)
    return r.run(SAMPLES / "nightly_batch", summarize(SAMPLES / "nightly_batch"), Hints(schedule="0 2 * * *"))


def test_job_spec_matches_security_rule():
    spec = CloudRunJobRunner(CFG, client=FakeClient()).job_spec("img@sha256:1", "est-1", Hints(expected_users=5))
    t = spec["template"]["template"]
    assert t["executionEnvironment"] == "EXECUTION_ENVIRONMENT_GEN2"
    assert t["vpcAccess"] == {"egress": "ALL_TRAFFIC",
                              "networkInterfaces": [{"network": "profiler-no-egress", "subnetwork": "profiler"}]}
    assert t["maxRetries"] == 0 and t["timeout"] == "600s"
    assert t["serviceAccount"] == CFG.service_account
    c = t["containers"][0]
    assert c["resources"]["limits"] == {"cpu": "1", "memory": "2Gi"}
    env = {e["name"]: e["value"] for e in c["env"]}
    assert json.loads(env["SHIPCUBE_HINTS"])["expected_users"] == 5
    assert not any("KEY" in e["name"] or "SECRET" in e["name"] for e in c["env"])


def test_package_contains_app_profiler_and_dockerfile():
    with tarfile.open(fileobj=io.BytesIO(_package(SAMPLES / "dashboard")), mode="r:gz") as tar:
        names = tar.getnames()
    assert "Dockerfile" in names and "app/app.py" in names and "app/requirements.txt" in names
    assert "shipcube/shipcube_profiler/agent.py" in names
    assert "shipcube/shipcube_classifier/features.py" in names
    assert not any(n.endswith("model.json") for n in names)   # the job doesn't need the model


def test_run_happy_path_and_cleanup():
    fake = FakeClient()
    p = run(fake)
    assert p.runner == "gcp" and p.cpu_mean_cores == 0.9
    create = next(c for c in fake.calls if c[0] == "POST" and c[1].endswith("/jobs"))
    image = create[2]["json"]["template"]["template"]["containers"][0]["image"]
    assert image.endswith("@sha256:abc")                      # pinned by digest from the build
    upload = next(c for c in fake.calls if "upload/storage" in c[1])
    assert upload[2]["params"]["ifGenerationMatch"] == "0"
    deleted = [c[1] for c in fake.calls if c[0] == "DELETE"]
    assert any("/jobs/prof-" in u for u in deleted) and any("profiler-src" in u for u in deleted)


def test_log_read_waits_out_quota_429(monkeypatch):
    import shipcube_profiler.gcp_runner as gr
    monkeypatch.setattr(gr, "LOG_POLL_S", 0.01)
    fake = FakeClient(throttle=True)
    p = run(fake, log_wait_s=5)
    assert p.runner == "gcp" and fake.log_calls == 2


@pytest.mark.parametrize("kw,stage", [({"build_status": "FAILURE"}, "build"), ({"succeeded": 0}, "run"),
                                      ({"log_text": "unrelated"}, "logs"), ({"fail_at": "upload"}, "upload")])
def test_failures_are_typed_by_stage_and_still_clean_up(kw, stage):
    fake = FakeClient(**kw)
    with pytest.raises(ProfilerError) as e:
        run(fake)
    assert e.value.stage == stage
    assert any(c[0] == "DELETE" and "/jobs/" in c[1] for c in fake.calls)


def test_config_from_env_requires_everything():
    with pytest.raises(ProfilerError) as e:
        GcpProfilerConfig.from_env({"SHIPCUBE_PROFILER_PROJECT": "p"})
    assert e.value.stage == "config" and "SHIPCUBE_PROFILER_BUCKET" in str(e.value)


def test_default_runner_is_gcp_unless_dev_mode(monkeypatch):
    monkeypatch.setenv("SHIPCUBE_DEV_MODE", "1")
    assert isinstance(default_runner(), LocalRunner)
    monkeypatch.delenv("SHIPCUBE_DEV_MODE")
    for k in list(os.environ):
        if k.startswith("SHIPCUBE_PROFILER_"):
            monkeypatch.delenv(k)
    with pytest.raises(ProfilerError):
        default_runner()


class FlakySession:
    def __init__(self, statuses):
        self.statuses, self.n = list(statuses), 0

    def request(self, method, url, **kw):
        self.n += 1
        code = self.statuses.pop(0)
        return type("R", (), {"status_code": code, "content": b'{"ok": 1}' if code == 200 else b"x",
                              "text": "boom", "json": lambda self: {"ok": 1}})()


def test_gcp_client_retries_idempotent_calls_only():
    s = FlakySession([503, 429, 200])
    assert GcpClient(session=s, backoff_s=0).get("https://x") == {"ok": 1} and s.n == 3
    s = FlakySession([503, 200])
    with pytest.raises(GcpError):
        GcpClient(session=s, backoff_s=0).post("https://x", json={})   # not idempotent: no retry
    s = FlakySession([404])
    with pytest.raises(GcpError) as e:
        GcpClient(session=s, backoff_s=0).get("https://x")
    assert e.value.not_found and s.n == 1


@pytest.mark.skipif(os.environ.get("SHIPCUBE_GCP_IT") != "1",
                    reason="integration: set SHIPCUBE_GCP_IT=1 and SHIPCUBE_PROFILER_* (creates billable resources)")
def test_gcp_integration_profiles_the_batch_sample():
    p = CloudRunJobRunner().run(SAMPLES / "nightly_batch", summarize(SAMPLES / "nightly_batch"),
                                Hints(schedule="0 2 * * *"))
    assert p.runner == "gcp" and p.exit_code == 0 and p.cpu_seconds > 0
    assert p.net_blocked >= 1          # the webhook can't get out of the sandbox


# --- Phase 1 sandbox hardening (docs/research/profiler-sandbox.md) ----------------------

def test_result_line_needs_this_runs_nonce():
    forged = RESULT_PREFIX + json.dumps({"nonce": "guess", "summary": {}, "profile": {"mode": "measured"}})
    with pytest.raises(ProfilerError) as e:
        run(FakeClient(forged=forged))
    assert e.value.reason == "result_rejected"
    with pytest.raises(ProfilerError) as e:   # a line with no nonce at all
        run(FakeClient(log_text=RESULT_PREFIX + json.dumps({"summary": {}, "profile": {}})))
    assert e.value.reason == "result_rejected"


def test_each_run_gets_a_fresh_nonce_in_the_job_env():
    fake = FakeClient()
    run(fake)
    run(fake)
    creates = [c for c in fake.calls if c[0] == "POST" and c[1].endswith("/jobs")]
    nonces = [{e["name"]: e["value"] for e in c[2]["json"]["template"]["template"]["containers"][0]["env"]}[NONCE_ENV]
              for c in creates]
    assert len(set(nonces)) == 2 and all(len(n) == 32 for n in nonces)


def test_leftover_job_is_replaced_not_reused():
    fake = FakeClient(conflict=True)
    p = run(fake)
    assert p.mode == "measured"   # the result carries the new run's nonce
    i = [n for n, c in enumerate(fake.calls) if c[0] == "POST" and c[1].endswith("/jobs")]
    assert len(i) == 2 and any(c[0] == "DELETE" and "/jobs/prof-" in c[1] for c in fake.calls[i[0]:i[1]])


@pytest.mark.parametrize("step,reason", [("build", "build_failed"), ("size-check", "too_large")])
def test_build_failures_carry_a_reason(step, reason):
    with pytest.raises(ProfilerError) as e:
        run(FakeClient(build_status="FAILURE", failed_step=step))
    assert e.value.stage == "build" and e.value.reason == reason


def test_build_is_capped_and_checks_image_size():
    spec = CloudRunJobRunner(CFG, client=FakeClient()).build_spec("o", "img")
    assert spec["timeout"] == "300s"
    ids = [st["id"] for st in spec["steps"]]
    assert ids == ["build", "size-check"]
    assert "2147483648" in spec["steps"][1]["args"][1] and "$$(" in spec["steps"][1]["args"][1]


def test_policy_errors_happen_before_anything_is_created(tmp_path):
    (tmp_path / "main.py").write_text("print(1)\n")
    (tmp_path / "requirements.txt").write_text("evil @ https://x.example/evil.tar.gz\n")
    fake = FakeClient()
    with pytest.raises(ProfilerError) as e:
        CloudRunJobRunner(CFG, client=fake).run(tmp_path, summarize(tmp_path), Hints())
    assert e.value.reason == "requirements_policy" and fake.calls == []


def test_dockerfile_installs_wheels_only_from_pypi():
    from shipcube_profiler.gcp_runner import DOCKERFILE, REQUIREMENTS_FILE
    installs = [ln for ln in DOCKERFILE.splitlines() if "pip install" in ln]
    assert installs and all("--only-binary=:all:" in ln and "--index-url https://pypi.org/simple" in ln
                            for ln in installs)
    assert "/app/requirements.txt" not in DOCKERFILE and f"-r /opt/{REQUIREMENTS_FILE}" in DOCKERFILE
    # The agent's interpreter stays pristine (red team on #57).
    assert 'ENTRYPOINT ["python", "-S", "-P", "-m", "shipcube_profiler.agent", "/app"]' in DOCKERFILE
    assert "WORKDIR /\n" in DOCKERFILE and "WORKDIR /app" not in DOCKERFILE
    lines = DOCKERFILE.splitlines()
    app_install = next(i for i, ln in enumerate(lines) if f"-r /opt/{REQUIREMENTS_FILE}" in ln)
    assert "--target /opt/appdeps" in lines[app_install] and lines[app_install - 1] == "USER 10002:10002"
    assert "--target /opt/shipcube" in DOCKERFILE and "chmod -R a-w /usr/local/lib/python3.12" in DOCKERFILE
    with tarfile.open(fileobj=io.BytesIO(_package(SAMPLES / "dashboard")), mode="r:gz") as tar:
        reqs = tar.extractfile(REQUIREMENTS_FILE).read().decode()
    assert "flask" in reqs.lower()
