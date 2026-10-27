"""Runtime settings, all from the environment (engineering standard: one artifact, config by env).

Production (default): SHIPCUBE_BACKEND=gcp with Application Default Credentials (on Cloud Run, the
service's own service account), state in Postgres (SHIPCUBE_DATABASE_URL)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _dev_mode() -> bool:
    return os.environ.get("SHIPCUBE_DEV_MODE", "") == "1"


@dataclass(frozen=True)
class Settings:
    # DEV_MODE is the only place mocks are allowed (engineering standard, docs/WORKSTREAMS.md):
    # mock deployer and telemetry, mock estimator, dev tokens and dev console sessions.
    # Without it, ShipCube refuses to start with anything mocked.
    dev_mode: bool = field(default_factory=_dev_mode)
    backend: str = field(default_factory=lambda: os.environ.get("SHIPCUBE_BACKEND", "mock" if _dev_mode() else "gcp"))
    # Identity Platform project whose ID tokens authenticate console users (approvals, restore).
    idp_project: str | None = field(default_factory=lambda: os.environ.get("SHIPCUBE_IDP_PROJECT") or None)
    # Profiler sandbox SA for estimate_load (security's sc-profiler). Unset = static analysis only.
    profiler_job: str | None = field(default_factory=lambda: os.environ.get("SHIPCUBE_PROFILER_JOB") or None)
    worker_id: str = field(default_factory=lambda: os.environ.get("K_REVISION", "") + "/" + os.environ.get("HOSTNAME", "local") + f"/{os.getpid()}")
    # Hosted apps run here (security's `shipcube-apps` project).
    apps_project: str = field(default_factory=lambda: os.environ.get("SHIPCUBE_APPS_PROJECT", "shipcube-apps-ks"))
    control_project: str = field(default_factory=lambda: os.environ.get("SHIPCUBE_CONTROL_PROJECT", "shipcube-control-ks"))
    region: str = field(default_factory=lambda: os.environ.get("SHIPCUBE_REGION", "asia-south1"))
    repo: str = field(default_factory=lambda: os.environ.get("SHIPCUBE_REPO", "shipcube"))
    upload_bucket: str = field(default_factory=lambda: os.environ.get("SHIPCUBE_UPLOAD_BUCKET", "shipcube-uploads-ks"))
    # Signs upload URLs through IAM signBlob (ADC user credentials cannot sign by themselves).
    signer_sa: str = field(default_factory=lambda: os.environ.get(
        "SHIPCUBE_SIGNER_SA", "sc-api@shipcube-control-ks.iam.gserviceaccount.com"))
    # Cloud Build runs as this account in the apps project (security's sc-builder).
    builder_sa: str = field(default_factory=lambda: os.environ.get(
        "SHIPCUBE_BUILDER_SA", "sc-builder@shipcube-apps-ks.iam.gserviceaccount.com"))
    # Optional: impersonate this deployer for hosted deploys; unset means ADC acts directly.
    hosted_deployer_sa: str | None = field(default_factory=lambda: os.environ.get("SHIPCUBE_HOSTED_DEPLOYER_SA") or None)
    # Where approval links point (the console) and the AI gateway apps call. Set per environment;
    # the defaults are the local dev servers.
    console_url: str = field(default_factory=lambda: os.environ.get("SHIPCUBE_CONSOLE_URL", "http://localhost:3000").rstrip("/"))
    gateway_url: str = field(default_factory=lambda: os.environ.get("SHIPCUBE_GATEWAY_URL", "http://localhost:8090").rstrip("/"))
    builder_image: str = field(default_factory=lambda: os.environ.get("SHIPCUBE_BUILDER", "gcr.io/buildpacks/builder:latest"))


settings = Settings()
