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
    # The profiler sandbox job's runtime SA for estimate_load: `profiler-sandbox@<apps project>` from
    # infra/terraform/modules/profiler (no roles, no secrets). Unset = static analysis only.
    profiler_job: str | None = field(default_factory=lambda: os.environ.get("SHIPCUBE_PROFILER_JOB") or None)
    worker_id: str = field(default_factory=lambda: os.environ.get("K_REVISION", "") + "/" + os.environ.get("HOSTNAME", "local") + f"/{os.getpid()}")
    # Hosted apps run here (security's `shipcube-apps` project).
    apps_project: str = field(default_factory=lambda: os.environ.get("SHIPCUBE_APPS_PROJECT", "shipcube-apps-ks"))
    control_project: str = field(default_factory=lambda: os.environ.get("SHIPCUBE_CONTROL_PROJECT", "shipcube-control-ks"))
    region: str = field(default_factory=lambda: os.environ.get("SHIPCUBE_REGION", "asia-south1"))
    # Registry repo in linked projects, and the shared repo hosted apps used before per-workspace repos.
    repo: str = field(default_factory=lambda: os.environ.get("SHIPCUBE_REPO", "shipcube"))
    upload_bucket: str = field(default_factory=lambda: os.environ.get("SHIPCUBE_UPLOAD_BUCKET", "shipcube-uploads-ks"))
    # Signs upload URLs through IAM signBlob (ADC user credentials cannot sign by themselves).
    signer_sa: str = field(default_factory=lambda: os.environ.get(
        "SHIPCUBE_SIGNER_SA", "sc-api@shipcube-control-ks.iam.gserviceaccount.com"))
    # Hosted builds run as the workspace's own builder SA, read only the workspace's upload prefix
    # and push only to its own registry repo (deploy/isolation.py). {ws} = workspace uuid,
    # {ws_short} = its first 20 hex digits. Names agreed with security (infra/builder-isolation).
    builder_sa_template: str = field(default_factory=lambda: os.environ.get(
        "SHIPCUBE_BUILDER_SA_TEMPLATE", "sc-build-{ws_short}@{apps_project}.iam.gserviceaccount.com"))
    ar_repo_template: str = field(default_factory=lambda: os.environ.get("SHIPCUBE_AR_REPO_TEMPLATE", "ws-{ws_short}"))
    upload_prefix_template: str = field(default_factory=lambda: os.environ.get("SHIPCUBE_UPLOAD_PREFIX_TEMPLATE", "ws/{ws}/"))
    # The worker impersonates this SA to create a workspace's builder SA, repo and upload folder.
    provisioner_sa: str = field(default_factory=lambda: os.environ.get(
        "SHIPCUBE_PROVISIONER_SA", "sc-provisioner@shipcube-control-ks.iam.gserviceaccount.com"))
    # Granted on each builder SA: the worker mints its tokens, the hosted deployer submits builds as it.
    worker_sa: str = field(default_factory=lambda: os.environ.get(
        "SHIPCUBE_WORKER_SA", "sc-worker@shipcube-control-ks.iam.gserviceaccount.com"))
    build_submitter_sa: str = field(default_factory=lambda: os.environ.get(
        "SHIPCUBE_BUILD_SUBMITTER_SA", "sc-hosted-deployer@shipcube-control-ks.iam.gserviceaccount.com"))
    # Optional: impersonate this deployer for hosted deploys; unset means ADC acts directly.
    hosted_deployer_sa: str | None = field(default_factory=lambda: os.environ.get("SHIPCUBE_HOSTED_DEPLOYER_SA") or None)
    # Where approval links point (the console). Set per environment; the default is the local console.
    console_url: str = field(default_factory=lambda: os.environ.get("SHIPCUBE_CONSOLE_URL", "http://localhost:3000").rstrip("/"))
    # The AI gateway's URL, injected into every deployed app as SHIPCUBE_AI_GATEWAY. Unset: not injected.
    ai_gateway_url: str | None = field(default_factory=lambda: os.environ.get("SHIPCUBE_AI_GATEWAY_URL") or None)
    # The AI gateway's identity and the audience its ID tokens carry for this API (/internal/gateway/*,
    # shared caps). Unset audience: the internal routes refuse every caller.
    gateway_sa: str = field(default_factory=lambda: os.environ.get(
        "SHIPCUBE_GATEWAY_SA", "sc-gateway@shipcube-control-ks.iam.gserviceaccount.com"))
    internal_audience: str | None = field(default_factory=lambda: os.environ.get("SHIPCUBE_INTERNAL_AUDIENCE") or None)
    # SHIPCUBE_BACKEND=local (deploy/local.py, local_iap.py): Docker + pack + fake-gcs-server.
    local_state_dir: str = field(default_factory=lambda: os.environ.get("SHIPCUBE_LOCAL_STATE", ".shipcube-local"))
    local_gcs_url: str = field(default_factory=lambda: os.environ.get("SHIPCUBE_LOCAL_GCS_URL", "http://localhost:4443"))
    # What a client PUTs to (the signed URL's host); differs from local_gcs_url inside compose.
    local_gcs_public_url: str = field(default_factory=lambda: os.environ.get(
        "SHIPCUBE_LOCAL_GCS_PUBLIC_URL", os.environ.get("SHIPCUBE_LOCAL_GCS_URL", "http://localhost:4443")))
    local_network: str = field(default_factory=lambda: os.environ.get("SHIPCUBE_LOCAL_NETWORK", "shipcube-local"))
    # How the worker and local_iap reach app containers: "network" (by alias, when they run on that
    # Docker network, as in compose) or "published" (the 127.0.0.1 port, when they run on the host).
    local_upstream: str = field(default_factory=lambda: os.environ.get("SHIPCUBE_LOCAL_UPSTREAM", "published"))
    local_iap_port: int = field(default_factory=lambda: int(os.environ.get("SHIPCUBE_LOCAL_IAP_PORT", "8088")))
    local_ready_timeout_s: int = field(default_factory=lambda: int(os.environ.get("SHIPCUBE_LOCAL_READY_TIMEOUT_S", "90")))
    # BYOK keys: Secret Manager in the control project, or a local JSON file (DEV_MODE; the format
    # infra/local/secretmanager.py serves).
    secret_store: str = field(default_factory=lambda: os.environ.get("SHIPCUBE_SECRET_STORE", "local" if _dev_mode() else "gcp"))
    local_secrets_file: str = field(default_factory=lambda: os.environ.get("SHIPCUBE_LOCAL_SECRETS_FILE",
                                                                           ".shipcube-local/secrets.json"))
    # Linked projects: the per-workspace deployer the user grants roles to (infra/scripts/link-project.sh).
    # Created in a project of their own (infra/terraform/links.tf), where the API can create SAs and
    # the API and worker can act as them, and nothing else (#15).
    links_project: str = field(default_factory=lambda: os.environ.get("SHIPCUBE_LINKS_PROJECT", "shipcube-links-ks"))
    link_deployer_sa_template: str = field(default_factory=lambda: os.environ.get(
        "SHIPCUBE_LINK_DEPLOYER_SA_TEMPLATE", "ws-{ws12}-deployer@{links_project}.iam.gserviceaccount.com"))
    # The API's own SA: it creates each workspace's deployer SA and checks links by impersonating it.
    api_sa: str = field(default_factory=lambda: os.environ.get(
        "SHIPCUBE_API_SA", "sc-api@shipcube-control-ks.iam.gserviceaccount.com"))
    # Billing page: the credit grant on ShipCube's billing account (GCP credits), shown against usage.
    credit_grant_usd: float = field(default_factory=lambda: float(os.environ.get("SHIPCUBE_CREDIT_GRANT_USD", "0")))
    credit_expires_at: str | None = field(default_factory=lambda: os.environ.get("SHIPCUBE_CREDIT_EXPIRES_AT") or None)
    builder_image: str = field(default_factory=lambda: os.environ.get("SHIPCUBE_BUILDER", "gcr.io/buildpacks/builder:latest"))


settings = Settings()
