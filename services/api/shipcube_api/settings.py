"""Runtime settings, all from the environment. SHIPCUBE_BACKEND=mock (default) keeps everything
in memory; SHIPCUBE_BACKEND=gcp deploys for real with Application Default Credentials."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    backend: str = field(default_factory=lambda: os.environ.get("SHIPCUBE_BACKEND", "mock"))
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
    # The workspace owner's Google account (the mock identity until Identity Platform lands).
    owner_email: str = field(default_factory=lambda: os.environ.get("SHIPCUBE_OWNER_EMAIL", "owner@example.com"))
    builder_image: str = field(default_factory=lambda: os.environ.get("SHIPCUBE_BUILDER", "gcr.io/buildpacks/builder:latest"))
    purge_check_s: int = field(default_factory=lambda: int(os.environ.get("SHIPCUBE_PURGE_CHECK_S", "3600")))


settings = Settings()
