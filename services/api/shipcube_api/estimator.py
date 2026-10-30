"""estimate_load's engine.

Production uses the load-estimator workstream's `shipcube_placement.estimate` (PR #7): it profiles
the code (in the sandboxed Cloud Run job when SHIPCUBE_PROFILER_JOB names its service account, otherwise by static
analysis, never by running user code in this process), classifies, sizes, prices and explains
(Muse Spark with a rule-based fallback). If the package is not installed, estimates fail with a
typed `estimator_unavailable` error instead of returning made-up numbers. The canned estimate
exists only in DEV_MODE.
"""

from __future__ import annotations

import logging
import dataclasses
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Protocol

from .deploy.base import Deployer, DeployError
from . import upload_guard
from .deploy import isolation
from .settings import settings

log = logging.getLogger(__name__)


class Estimator(Protocol):
    def estimate(self, source: dict[str, Any], hints: dict[str, Any], *, workspace_id: str, op_id: str,
                 static_reason: str | None = None) -> dict[str, Any]: ...


class PlacementEstimator:
    def __init__(self, deployer: Deployer) -> None:
        from shipcube_placement import estimate  # noqa: F401 — fail fast if missing

        self.d = deployer

    def estimate(self, source: dict[str, Any], hints: dict[str, Any], *, workspace_id: str, op_id: str,
                 static_reason: str | None = None) -> dict[str, Any]:
        from shipcube_placement import estimate
        from shipcube_profiler import CloudRunJobRunner, StaticRunner

        if static_reason:
            # The profiler guard said no (quota, budget, suspended, busy, disabled, image_source): never run
            # the code. StaticRunner(reason=...) records it as profile.measurements.static_reason
            # (shipcube_profiler.STATIC_REASONS).
            try:
                runner = StaticRunner(reason=static_reason)
            except TypeError:  # shipcube_profiler before #57 has no reason codes: still static, just unlabelled
                runner = StaticRunner()
        elif os.environ.get("SHIPCUBE_PROFILER"):
            # Explicit sandbox choice (e.g. `local`: docker run --network none on the local stack);
            # shipcube_profiler.runner_from_env reads SHIPCUBE_PROFILER and its limits.
            from shipcube_profiler import runner_from_env

            runner = runner_from_env()
        elif settings.profiler_job:
            # The sandbox (security §4: no-egress VPC, roleless SA). Its image builds run as the
            # workspace's own builder where the profiler supports it (#69).
            runner = CloudRunJobRunner(project=settings.apps_project, region=settings.region,
                                       service_account=settings.profiler_job, **self.workspace_build(workspace_id))
        else:
            runner = StaticRunner()
        def fetch_upload(upload_id: str) -> Path:
            tarball = self.d.download_upload(workspace_id, upload_id, tmp / f"{upload_id}.tgz")
            upload_guard.check_tarball(tarball)  # before tarfile sees it: bounded memory and CPU
            return tarball

        # /tmp is memory on Cloud Run, shared with the deploy lane: limit how many sources are on disk
        # at once, and always remove the download.
        with upload_guard.source_slot(source):
            tmp = Path(tempfile.mkdtemp(prefix="shipcube-upload-"))
            try:
                res = estimate(source, {k: v for k, v in hints.items() if v is not None}, runner=runner,
                               fetch_upload=fetch_upload)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        out = res.model_dump(mode="json") if hasattr(res, "model_dump") else dict(res.__dict__)
        out.setdefault("estimate_id", f"est_{op_id.replace('-', '')[:12]}")
        return out


    def workspace_build(self, workspace_id: str) -> dict[str, str]:
        """CloudRunJobRunner overrides that build a workspace's profiled image as that workspace's own
        C-1 builder (sc-build-<ws>): its source in the workspace's uploads folder, the image in its
        repo, build logs in its logs folder (#22 phase 2, #69). Empty when the installed
        shipcube_profiler predates those fields: the shared profiler-build SA is used, as before."""
        from shipcube_profiler import GcpProfilerConfig

        if "source_prefix" not in {f.name for f in dataclasses.fields(GcpProfilerConfig)}:
            return {}
        ensure = getattr(self.d, "ensure_builder", None)
        if ensure is not None:
            ensure(workspace_id)  # the builder SA, repo, folders and mirror grants exist before the build
        s, bucket = settings, settings.upload_bucket
        return {"build_service_account": isolation.builder_sa(s, workspace_id), "bucket": bucket,
                "source_prefix": f"{isolation.upload_prefix(s, workspace_id)}profiler/",
                "repository": isolation.ar_repo(s, workspace_id),
                "build_logs": f"gs://{bucket}/{isolation.build_logs_folder(s, workspace_id)}".rstrip("/")}


class UnavailableEstimator:
    def estimate(self, source: dict[str, Any], hints: dict[str, Any], *, workspace_id: str, op_id: str,
                 static_reason: str | None = None) -> dict[str, Any]:
        raise DeployError("estimator_unavailable",
                          "the load estimator (shipcube_placement) is not installed on this server; deploy with an explicit tier")


class DevEstimator:
    """DEV_MODE only: the classifier's canned numbers plus the explainer."""

    def estimate(self, source: dict[str, Any], hints: dict[str, Any], *, workspace_id: str, op_id: str,
                 static_reason: str | None = None) -> dict[str, Any]:
        from .explain import explain

        classified = {
            "estimate_id": f"est_{op_id.replace('-', '')[:12]}", "workload_class": "bursty_interactive", "confidence": 0.87,
            "tier": "T1", "target": "cloud_run_service",
            "resources": {"cpu": "1", "memory": "512Mi", "concurrency": 40, "min_instances": 0, "max_instances": 2},
            "monthly_cost_usd": {"low": 0.0, "expected": 1.8, "high": 6.5},
            "profile": {"peak_rss_mb": 210, "cpu_seconds": 3.4, "wall_seconds": 5.1, "queries": 12, "io_mb": 18},
        }
        if static_reason:
            classified["profile"] = {**classified["profile"], "measurements": {"mode": "static", "abuse_signals": []}}
        return with_static_reason({**classified, **explain(classified).model_dump(mode="json"), "dev_mode": True}, static_reason)


def measurements(result: dict[str, Any] | None) -> dict[str, Any]:
    """`profile.measurements` of an estimate result: mode, static_reason, abuse_signals (load-estimator's contract)."""
    return ((result or {}).get("profile") or {}).get("measurements") or {}


def with_static_reason(result: dict[str, Any], reason: str | None) -> dict[str, Any]:
    """DevEstimator only: label a canned static estimate the way StaticRunner(reason=...) labels a real one,
    in `profile.measurements.static_reason`. The real estimator's field belongs to shipcube_profiler."""
    if reason:
        m = {"mode": "static", **measurements(result)}
        if not m.get("static_reason"):
            m["static_reason"] = reason
        result = {**result, "profile": {**(result.get("profile") or {}), "measurements": m}}
    return result


def make_estimator(deployer: Deployer) -> Estimator:
    # SHIPCUBE_ESTIMATOR=placement|dev picks explicitly (the local stack runs the real estimator in
    # DEV_MODE); unset keeps the default: dev in DEV_MODE, placement otherwise.
    choice = os.environ.get("SHIPCUBE_ESTIMATOR") or ("dev" if settings.dev_mode else "placement")
    if choice == "dev":
        if not settings.dev_mode:
            raise RuntimeError("SHIPCUBE_ESTIMATOR=dev is allowed only with SHIPCUBE_DEV_MODE=1")
        return DevEstimator()
    if choice != "placement":
        raise ValueError(f"SHIPCUBE_ESTIMATOR must be placement or dev, not {choice!r}")
    try:
        return PlacementEstimator(deployer)
    except ImportError:
        log.warning("shipcube_placement not installed; estimate_load will return estimator_unavailable")
        return UnavailableEstimator()
