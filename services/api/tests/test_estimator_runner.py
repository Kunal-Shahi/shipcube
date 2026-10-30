"""Profiled builds run as the workspace's own builder (#22 phase 2, PR #69): the estimator gives the
profiler's CloudRunJobRunner the workspace's builder SA, uploads folder, repo and logs folder, and
provisions the builder first. shipcube_profiler is stubbed: the API tests don't install it."""

import dataclasses
import sys
import types

import pytest

from shipcube_api import estimator as est_mod
from shipcube_api.deploy import isolation

WS = "3c1d5e2a-7b4f-4e8a-9c6d-2f1e0a9b8c7d"
SHORT = WS.replace("-", "")[:20]


def _profiler(with_phase2: bool, seen: dict):
    fields = {"project": str, "bucket": str, "build_service_account": str}
    if with_phase2:
        fields |= {"source_prefix": str, "build_logs": str}
    Config = dataclasses.make_dataclass("GcpProfilerConfig", list(fields.items()))

    class CloudRunJobRunner:
        def __init__(self, **kw):
            seen["runner"] = kw

    class StaticRunner:
        def __init__(self, reason=None):
            seen["static"] = reason

    return types.SimpleNamespace(GcpProfilerConfig=Config, CloudRunJobRunner=CloudRunJobRunner, StaticRunner=StaticRunner)


@pytest.fixture
def estimator(monkeypatch):
    seen = {}

    def install(with_phase2: bool):
        monkeypatch.setitem(sys.modules, "shipcube_profiler", _profiler(with_phase2, seen))
        monkeypatch.setitem(sys.modules, "shipcube_placement", types.SimpleNamespace(
            estimate=lambda source, hints, runner, fetch_upload: types.SimpleNamespace(tier="T1")))
        monkeypatch.setattr(est_mod, "settings", dataclasses.replace(est_mod.settings, profiler_job="profiler-sandbox@p.iam"))
        monkeypatch.delenv("SHIPCUBE_PROFILER", raising=False)

        class Deployer:
            provisioned = []

            def ensure_builder(self, ws):
                self.provisioned.append(ws)

        d = Deployer()
        return est_mod.PlacementEstimator(d), d, seen

    return install


def test_profiled_builds_run_as_the_workspaces_builder(estimator):
    est, d, seen = estimator(with_phase2=True)
    est.estimate({"kind": "inline", "files": []}, {}, workspace_id=WS, op_id="op-1")
    s = est_mod.settings
    assert d.provisioned == [WS]  # the builder, its repo, folders and mirror grants exist first
    assert seen["runner"] == {
        "project": s.apps_project, "region": s.region, "service_account": "profiler-sandbox@p.iam",
        "build_service_account": f"sc-build-{SHORT}@{s.apps_project}.iam.gserviceaccount.com",
        "bucket": s.upload_bucket, "source_prefix": f"ws/{WS}/profiler/", "repository": f"ws-{SHORT}",
        "build_logs": f"gs://{s.upload_bucket}/ws/{WS}/build-logs",
    }
    # The builder can read that source: it's inside the workspace's uploads folder.
    assert seen["runner"]["source_prefix"].startswith(isolation.upload_prefix(s, WS))


def test_an_older_profiler_keeps_the_shared_build_identity(estimator):
    est, d, seen = estimator(with_phase2=False)
    est.estimate({"kind": "inline", "files": []}, {}, workspace_id=WS, op_id="op-2")
    assert d.provisioned == [] and set(seen["runner"]) == {"project", "region", "service_account"}


def test_a_static_estimate_never_provisions_or_builds(estimator):
    est, d, seen = estimator(with_phase2=True)
    est.estimate({"kind": "inline", "files": []}, {}, workspace_id=WS, op_id="op-3", static_reason="quota")
    assert d.provisioned == [] and "runner" not in seen and seen["static"] == "quota"
