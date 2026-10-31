"""Runtime settings, all from the environment (engineering standard: one artifact, config by env).

Production (default): SHIPCUBE_BACKEND=gcp with Application Default Credentials (on Cloud Run, the
service's own service account), state in Postgres (SHIPCUBE_DATABASE_URL)."""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

log = logging.getLogger("shipcube.settings")

# Set by Cloud Run on every service and job. Their presence means production: dev mode and local
# adapters are refused at startup (#29).
CLOUD_RUN_VARS = ("K_SERVICE", "K_REVISION", "CLOUD_RUN_JOB")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"}
_warned_alias = False


def on_cloud_run(env: Mapping[str, str] = os.environ) -> bool:
    return any(env.get(k) for k in CLOUD_RUN_VARS)


def _dev_mode() -> bool:
    """SHIPCUBE_DEV_MODE=1. DEV_MODE=1 (the gateway's and console's name) still counts, with a warning."""
    global _warned_alias
    if os.environ.get("SHIPCUBE_DEV_MODE", "") == "1":
        return True
    if os.environ.get("DEV_MODE", "") == "1":
        if not _warned_alias:
            log.warning("DEV_MODE=1 is deprecated here; use SHIPCUBE_DEV_MODE=1")
            _warned_alias = True
        return True
    return False


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
    # Mediated egress (red-team N-2, networking.md §5.5): every hosted app routes all outbound through
    # this VPC/subnet (Direct VPC egress, all-traffic) so the egress firewall can allow only 443/80 and
    # deny raw TCP (stratum, spam relay, C2). Names are infra/terraform/modules/app_egress's outputs;
    # set them empty to deploy without a VPC (leaves open egress — only for a transition). Linked
    # projects need their own VPC/NAT and are skipped for now (documented follow-up).
    app_egress_network: str | None = field(default_factory=lambda: os.environ.get("SHIPCUBE_APP_EGRESS_NETWORK", "app-egress") or None)
    app_egress_subnet: str | None = field(default_factory=lambda: os.environ.get("SHIPCUBE_APP_EGRESS_SUBNET", "app-egress") or None)
    # Where approval links point (the console). Set per environment; the default is the local console.
    console_url: str = field(default_factory=lambda: os.environ.get("SHIPCUBE_CONSOLE_URL", "http://localhost:3000").rstrip("/"))
    # The AI gateway's URL, injected into every deployed app as SHIPCUBE_AI_GATEWAY. Unset: not injected.
    ai_gateway_url: str | None = field(default_factory=lambda: os.environ.get("SHIPCUBE_AI_GATEWAY_URL") or None)
    # The AI gateway's identity and the audience its ID tokens carry for this API (/internal/gateway/*,
    # shared caps). Unset audience: the internal routes refuse every caller.
    gateway_sa: str = field(default_factory=lambda: os.environ.get(
        "SHIPCUBE_GATEWAY_SA", "sc-gateway@shipcube-control-ks.iam.gserviceaccount.com"))
    internal_audience: str | None = field(default_factory=lambda: os.environ.get("SHIPCUBE_INTERNAL_AUDIENCE") or None)
    # Public waitlist (waitlist.py). Platform admins review it: a comma-separated allowlist of verified
    # Google emails, never a workspace role. Approvals stop at max_workspaces workspaces in total.
    platform_admins: frozenset[str] = field(default_factory=lambda: frozenset(
        e.strip().lower() for e in os.environ.get("SHIPCUBE_PLATFORM_ADMINS", "").split(",") if e.strip()))
    # Browser origins the landing page's waitlist form may post from (CORS): the console's run.app URL
    # and any other address it's served on (Firebase Hosting). Comma-separated; default the console URL.
    console_origins: frozenset[str] = field(default_factory=lambda: frozenset(
        o.strip().rstrip("/") for o in (os.environ.get("SHIPCUBE_CONSOLE_ORIGINS")
                                        or os.environ.get("SHIPCUBE_CONSOLE_URL", "http://localhost:3000")).split(",")
        if o.strip()))
    max_workspaces: int = field(default_factory=lambda: int(os.environ.get("SHIPCUBE_MAX_WORKSPACES", "50")))
    waitlist_per_ip_per_hour: int = field(default_factory=lambda: int(os.environ.get("SHIPCUBE_WAITLIST_PER_IP_PER_HOUR", "5")))
    waitlist_per_hour: int = field(default_factory=lambda: int(os.environ.get("SHIPCUBE_WAITLIST_PER_HOUR", "200")))
    # "Request access" from people already signed in (verified Google accounts): a separate budget.
    waitlist_signed_in_per_hour: int = field(default_factory=lambda: int(os.environ.get("SHIPCUBE_WAITLIST_SIGNED_IN_PER_HOUR", "200")))
    # The Terms/Privacy version a waitlist sign-up consents to. The API — not the request — records this
    # against the entry, and refuses a sign-up that doesn't carry the current version (a stale page or a
    # forged body). Keep in sync with the console's TERMS_VERSION (apps/web/src/lib/legal.ts).
    terms_version: str = field(default_factory=lambda: os.environ.get("SHIPCUBE_TERMS_VERSION", "2026-10-31"))
    # On Cloud Run, Google's front end appends the client's address to X-Forwarded-For; the last entry
    # is the only one a client can't forge. Off (local), the socket's peer address is used.
    trust_forwarded_for: bool = field(default_factory=lambda: os.environ.get("SHIPCUBE_TRUST_FORWARDED_FOR") == "1")
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


def _is_local_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host in LOCAL_HOSTS or host.endswith(".localhost") or host.endswith(".local")


def production_problems(s: Settings, env: Mapping[str, str] = os.environ) -> list[str]:
    """Why this process must not run on Cloud Run; empty when it may (#29). Every local adapter is
    behind dev mode or an explicit local setting, so this lists both, plus local URLs and secrets."""
    out = []
    if s.dev_mode:
        out.append("dev mode is on (SHIPCUBE_DEV_MODE=1 or DEV_MODE=1)")
    if s.backend != "gcp":
        out.append(f"SHIPCUBE_BACKEND={s.backend}; production deploys only through gcp")
    if s.secret_store != "gcp":
        out.append(f"SHIPCUBE_SECRET_STORE={s.secret_store}; production keys live only in Secret Manager")
    for name, bad in (("SHIPCUBE_EMBEDDED_WORKER", "1"), ("SHIPCUBE_ESTIMATOR", "dev"), ("SHIPCUBE_ENV", "local")):
        if env.get(name) == bad:
            out.append(f"{name}={bad} is for local development only")
    # Profiler: empty (no profiling) or gcp (the sandboxed Cloud Run job); `local` runs tenant code
    # in-process. Monitor: unset resolves to gcp in shipcube_monitor; local/mock read Docker or fake data.
    profiler = (env.get("SHIPCUBE_PROFILER") or "").strip().lower()
    if profiler not in ("", "gcp"):
        out.append(f"SHIPCUBE_PROFILER={profiler}; production profiles only in the gcp sandbox")
    monitor = (env.get("SHIPCUBE_MONITOR") or "gcp").strip().lower()
    if monitor != "gcp":
        out.append(f"SHIPCUBE_MONITOR={monitor}; production reads only Cloud Monitoring")
    if (env.get("SHIPCUBE_TOKEN_PEPPER") or "").startswith("sclocal_"):
        out.append("SHIPCUBE_TOKEN_PEPPER is a local value (infra/local/localsecrets.py)")
    # Approval links, the MCP OAuth issuer and the gateway URL handed to apps must be real.
    urls = {"SHIPCUBE_CONSOLE_URL": env.get("SHIPCUBE_CONSOLE_URL"), "SHIPCUBE_AI_GATEWAY_URL": env.get("SHIPCUBE_AI_GATEWAY_URL")}
    if env.get("K_SERVICE") and not urls["SHIPCUBE_CONSOLE_URL"]:
        out.append("SHIPCUBE_CONSOLE_URL is unset (the default is the local console)")
    for name, url in urls.items():
        if url and (_is_local_url(url) or urlsplit(url).scheme != "https"):
            out.append(f"{name}={url} is not a public https URL")
    return out


def refuse_unsafe_production(s: Settings, env: Mapping[str, str] = os.environ) -> None:
    """On Cloud Run, exit at startup if anything local is switched on. Elsewhere, do nothing."""
    if not on_cloud_run(env):
        return
    problems = production_problems(s, env)
    if problems:
        for p in problems:
            log.critical("refusing to start on Cloud Run: %s", p)
        sys.exit("refusing to start on Cloud Run: " + "; ".join(problems))


settings = Settings()
refuse_unsafe_production(settings)
