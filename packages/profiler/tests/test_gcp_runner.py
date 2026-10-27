import io
import json
import os
import tarfile
from pathlib import Path

import pytest

from shipcube_classifier.gcp import GcpClient, GcpError
from shipcube_profiler import (CloudRunJobRunner, GcpProfilerConfig, Hints, LocalRunner, Profile, ProfilerError,
                               default_runner, summarize)
from shipcube_profiler.agent import RESULT_PREFIX
from shipcube_profiler.gcp_runner import _package

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
CFG = GcpProfilerConfig(project="p", region="asia-south1", bucket="b", repository="profiler",
                        network="profiler-no-egress", subnet="profiler", service_account="sandbox@p.iam.gserviceaccount.com")


class FakeClient:
    """Scripted GCP: answers each REST call the runner makes, records them."""

    def __init__(self, build_status="SUCCESS", succeeded=1, log_text=None, fail_at=None):
        self.calls = []
        self.build_status, self.succeeded, self.fail_at = build_status, succeeded, fail_at
        prof = Profile(mode="measured", runner="local", kind="script", wall_s=1.2, cpu_mean_cores=0.9)
        self.log_text = log_text if log_text is not None else RESULT_PREFIX + json.dumps(
            {"summary": {}, "profile": prof.to_dict()})

    def _maybe_fail(self, what):
        if self.fail_at == what:
            raise GcpError(500, f"{what} broke", method="X", url=what)

    def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        if "upload/storage" in url:
            self._maybe_fail("upload")
            return {}
        if "cloudbuild" in url:
            self._maybe_fail("build")
            return {"name": "projects/p/locations/asia-south1/operations/build-op"}
        if url.endswith(":run"):
            return {"name": "projects/p/locations/asia-south1/operations/run-op"}
        if "run.googleapis.com" in url:
            return {"name": "projects/p/locations/asia-south1/operations/create-op"}
        if "logging" in url:
            return {"entries": [{"textPayload": self.log_text}]}
        raise AssertionError(url)

    def wait_operation(self, url, **kw):
        self.calls.append(("WAIT", url, kw))
        if url.endswith("build-op"):
            return {"done": True, "response": {"status": self.build_status, "id": "b1",
                                               "results": {"images": [{"digest": "sha256:abc"}]}}}
        if url.endswith("run-op"):
            return {"done": True, "response": {"name": "projects/p/locations/r/jobs/j/executions/j-x1",
                                               "succeededCount": self.succeeded,
                                               "failedCount": 1 - self.succeeded}}
        return {"done": True}

    def delete(self, url, **kw):
        self.calls.append(("DELETE", url, kw))
        return {}


def run(fake, tmp_path=None):
    r = CloudRunJobRunner(CFG, client=fake, log_wait_s=0.1, poll_s=0.01)
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
