import hashlib
import io
import json
import logging

import pytest

from shipcube_classifier import THRESHOLDS, ModelLoadError, rule_class
from shipcube_classifier import obs
from shipcube_classifier.gcp import GcpError
from shipcube_classifier.model import ARTEFACT
from shipcube_classifier.registry import load_bundled, load_gcs, load_model

BODY = ARTEFACT.read_bytes()
MANIFEST = json.loads((ARTEFACT.parent / "manifest.json").read_text())
VERSION = MANIFEST["version"]


class FakeGcs:
    """Serves objects from a dict; records requests."""

    def __init__(self, objects):
        self.objects, self.calls = objects, []

    def get(self, url, params=None, raw=False, **kw):
        name = url.split("/o/", 1)[1].replace("%2F", "/")
        self.calls.append(name)
        if name not in self.objects:
            raise GcpError(404, "not found", method="GET", url=url)
        return self.objects[name]


def objects(body=BODY, manifest=MANIFEST):
    p = "models/workload-classifier"
    return {f"{p}/latest.json": json.dumps({"version": VERSION}).encode(),
            f"{p}/{VERSION}/manifest.json": json.dumps(manifest).encode(),
            f"{p}/{VERSION}/model.json": body}


def test_bundled_model_matches_its_manifest():
    lm = load_bundled()
    assert lm.version == VERSION and lm.sha256 == hashlib.sha256(BODY).hexdigest()


def test_bundled_pin_mismatch_fails():
    with pytest.raises(ModelLoadError):
        load_bundled(pin="0" * 64)


def test_gcs_latest_resolves_and_caches(tmp_path):
    fake = FakeGcs(objects())
    lm = load_gcs("gs://b/models/workload-classifier/latest", cache_dir=tmp_path, client=fake)
    assert lm.version == VERSION and lm.source.endswith(VERSION)
    assert (tmp_path / VERSION / "model.json").read_bytes() == BODY
    fake.calls.clear()
    load_gcs(f"gs://b/models/workload-classifier/{VERSION}", cache_dir=tmp_path, client=fake)
    assert not any(c.endswith("model.json") for c in fake.calls)   # served from the verified cache


def test_gcs_tampered_model_is_rejected(tmp_path):
    fake = FakeGcs(objects(body=BODY.replace(b'"classes"', b'"classes" ', 1)))
    with pytest.raises(ModelLoadError, match="sha256"):
        load_gcs(f"gs://b/models/workload-classifier/{VERSION}", cache_dir=tmp_path, client=fake)


def test_gcs_pin_and_missing_objects(tmp_path):
    with pytest.raises(ModelLoadError, match="SHIPCUBE_MODEL_SHA256"):
        load_gcs(f"gs://b/models/workload-classifier/{VERSION}", pin="f" * 64, cache_dir=tmp_path,
                 client=FakeGcs(objects()))
    with pytest.raises(ModelLoadError, match="could not fetch"):
        load_gcs("gs://b/models/workload-classifier/wc-nope", cache_dir=tmp_path, client=FakeGcs({}))


def test_bad_uri():
    with pytest.raises(ModelLoadError):
        load_model("s3://nope/x", refresh=True)


def test_rule_class_follows_thresholds():
    assert rule_class({"mem_peak_gb": 2.0, "mem_per_core_gb": 20, "cpu_util_mean": 0.1}, "script") == "memory_heavy"
    assert rule_class({"cpu_util_mean": 0.9, "cpu_mean_cores": 3.0}, "script") == "compute_heavy"
    assert rule_class({"io_per_cpu_s": THRESHOLDS["io_per_cpu_s"] * 2, "cpu_util_mean": 0.1}, "script") == "io_bound"
    assert rule_class({"cpu_util_mean": 0.9, "cpu_mean_cores": 0.9}, "script") == "batch_cron"
    assert rule_class({"idle_fraction": 0.63, "invocation_cv": 1.4}, "web") == "bursty_interactive"
    assert rule_class({"idle_fraction": 0.0, "invocation_cv": 0.05}, "web") == "steady_service"


def test_json_logs_carry_scope_and_trace(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj")
    buf = io.StringIO()
    obs.configure_logging("INFO", stream=buf)
    log = logging.getLogger("shipcube.test")
    tp = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
    with obs.scope(estimate_id="est_1", operation_id="op_1", traceparent=tp):
        with obs.timed(log, "stage.done", extra_field=1):
            pass
    rec = json.loads(buf.getvalue().strip().splitlines()[-1])
    assert rec["severity"] == "INFO" and rec["event"] == "stage.done" and rec["outcome"] == "ok"
    assert rec["estimate_id"] == "est_1" and rec["operation_id"] == "op_1"
    assert rec["logging.googleapis.com/trace"] == "projects/proj/traces/0af7651916cd43dd8448eb211c80319c"
    assert "duration_ms" in rec and rec["extra_field"] == 1
    assert "estimate_id" not in obs.context()   # scope is reset
