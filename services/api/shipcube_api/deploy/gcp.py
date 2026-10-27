"""Real deploys on GCP, over the REST APIs with google-auth (no per-service client libraries).

Flow for one deploy (docs/research/networking.md §4):
  stage_source  inline -> tar.gz in the uploads bucket; upload -> check the object exists
  build         Cloud Build, buildpacks (no Dockerfile), runs as the builder SA, publishes to
                Artifact Registry  <region>-docker.pkg.dev/<project>/shipcube/<app>:<op id>
  deploy        per-app runtime SA with no roles; Cloud Run v2 service, gen2, scale to zero, caps,
                --no-allow-unauthenticated + IAP; IAP service agent gets run.invoker; wait until ready
  set_access    IAP accessor bindings (roles/iap.httpsResourceAccessor) = the app's access list

Linked projects use the same calls with credentials impersonating the workspace's deployer SA.
"""

from __future__ import annotations

import io
import logging
import secrets
import tarfile
import time
from base64 import b64decode
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

import google.auth
import requests
from google.auth import impersonated_credentials
from google.auth.transport.requests import AuthorizedSession, Request

from ..settings import Settings
from .base import AppRef, Deployed, DeployError, ShouldStop, normalize_caps

log = logging.getLogger(__name__)
SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
RUN = "https://run.googleapis.com/v2"
BUILD = "https://cloudbuild.googleapis.com/v1"
IAM = "https://iam.googleapis.com/v1"
IAP = "https://iap.googleapis.com/v1"
AR = "https://artifactregistry.googleapis.com/v1"
INVOKER = "roles/run.invoker"
ACCESSOR = "roles/iap.httpsResourceAccessor"
UPLOAD_MAX_BYTES = 100 * 1024 * 1024


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class GcpDeployer:
    name = "gcp"

    def __init__(self, s: Settings) -> None:
        self.s = s
        self._adc, _ = google.auth.default(scopes=SCOPES)
        # User ADC without a quota project shares the gcloud client's quotas (Resource Manager
        # returned 429 in testing). Bill API quota to our control project instead.
        if hasattr(self._adc, "with_quota_project") and not getattr(self._adc, "quota_project_id", None):
            self._adc = self._adc.with_quota_project(s.control_project)
        self._sessions: dict[str | None, AuthorizedSession] = {}
        self._project_numbers: dict[str, str] = {}
        self._iap_agents: dict[str, str] = {}

    # --- credentials ------------------------------------------------------------------

    def _session(self, deployer_sa: str | None) -> AuthorizedSession:
        if deployer_sa not in self._sessions:
            creds = self._adc
            if deployer_sa:
                creds = impersonated_credentials.Credentials(
                    source_credentials=self._adc, target_principal=deployer_sa, target_scopes=SCOPES, lifetime=3600)
            self._sessions[deployer_sa] = AuthorizedSession(creds)
        return self._sessions[deployer_sa]

    def _for(self, app: AppRef) -> AuthorizedSession:
        sa = app.target.deployer_sa if app.target.kind == "linked" else self.s.hosted_deployer_sa
        return self._session(sa)

    def _call(self, sess: AuthorizedSession, method: str, url: str, ok: tuple[int, ...] = (200,), **kw: Any) -> Any:
        try:
            r = sess.request(method, url, timeout=60, **kw)
        except (requests.Timeout, requests.ConnectionError) as e:
            raise DeployError("unavailable", f"{method} {url.split('?')[0]}: {type(e).__name__}") from e
        if r.status_code not in ok:
            try:
                err = r.json().get("error", {})
                msg = err.get("message", r.text)
            except ValueError:
                msg = r.text
            code = {400: "invalid_argument", 403: "permission_denied", 404: "not_found", 409: "conflict",
                    429: "rate_limited"}.get(r.status_code, "unavailable" if r.status_code >= 500 else "gcp_error")
            raise DeployError(code, f"{method} {url.split('?')[0]} -> {r.status_code}: {msg[:400]}")
        return r.json() if r.content else {}

    def _wait_op(self, sess: AuthorizedSession, base: str, op: dict[str, Any], timeout_s: int = 600) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        while not op.get("done"):
            if time.monotonic() > deadline:
                raise DeployError("timeout", f"operation {op.get('name')} did not finish in {timeout_s}s")
            time.sleep(2)
            op = self._call(sess, "GET", f"{base}/{op['name']}")
        if "error" in op:
            raise DeployError("gcp_error", f"{op['name']}: {op['error'].get('message')}")
        return op.get("response", {})

    def _project_number(self, sess: AuthorizedSession, project: str) -> str:
        agent = self._iap_agents.get(project, "")
        if project not in self._project_numbers and agent.startswith("service-"):
            self._project_numbers[project] = agent.split("@")[0].removeprefix("service-")
        if project not in self._project_numbers:
            p = self._call(sess, "GET", f"https://cloudresourcemanager.googleapis.com/v1/projects/{project}")
            self._project_numbers[project] = p["projectNumber"]
        return self._project_numbers[project]

    # --- uploads ------------------------------------------------------------------------

    def _bucket(self):
        from google.cloud import storage

        return storage.Client(project=self.s.apps_project, credentials=self._adc).bucket(self.s.upload_bucket)

    @staticmethod
    def _object(workspace_id: str, upload_id: str) -> str:
        return f"uploads/{workspace_id}/{upload_id}.tgz"

    def create_upload(self, workspace_id: str, content_type: str) -> dict[str, Any]:
        upload_id = f"upl_{secrets.token_hex(6)}"
        self._adc.refresh(Request())
        url = self._bucket().blob(self._object(workspace_id, upload_id)).generate_signed_url(
            version="v4", expiration=timedelta(minutes=15), method="PUT", content_type=content_type,
            service_account_email=self.s.signer_sa, access_token=self._adc.token,
        )
        expires = (datetime.now(UTC) + timedelta(minutes=15)).isoformat(timespec="seconds").replace("+00:00", "Z")
        return {"upload_id": upload_id, "method": "PUT", "url": url, "headers": {"Content-Type": content_type},
                "max_bytes": UPLOAD_MAX_BYTES, "expires_at": expires}

    def stage_source(self, app: AppRef, source: dict[str, Any], op_id: str) -> dict[str, Any]:
        kind = source["kind"]
        if kind == "image":
            return {"image": source["uri"]}
        if kind == "git":
            return {"gitSource": {k: v for k, v in {"url": source["url"], "revision": source.get("ref") or "HEAD",
                                                    "dir": source.get("subdir")}.items() if v}}
        bucket = self._bucket()
        if kind == "upload":
            name = self._object(app.workspace_id, source["upload_id"])
            blob = bucket.blob(name)
            if not blob.exists():
                raise DeployError("invalid_argument", f"upload {source['upload_id']} was not found; PUT the tarball first")
            blob.reload()
            if (blob.size or 0) > UPLOAD_MAX_BYTES:
                raise DeployError("invalid_argument", "upload is larger than 100 MB")
        else:  # inline
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz") as tar:
                for f in source["files"]:
                    data = b64decode(f["content"]) if f.get("encoding") == "base64" else f["content"].encode()
                    info = tarfile.TarInfo(f["path"].lstrip("/"))
                    info.size, info.mtime, info.mode = len(data), int(time.time()), 0o644
                    tar.addfile(info, io.BytesIO(data))
            name = f"inline/{app.workspace_id}/{op_id}.tgz"
            bucket.blob(name).upload_from_string(buf.getvalue(), content_type="application/gzip")
        return {"storageSource": {"bucket": self.s.upload_bucket, "object": name}}

    # --- build --------------------------------------------------------------------------

    def image_for(self, app: AppRef, tag: str) -> str:
        return f"{app.region}-docker.pkg.dev/{app.target.project}/{self.s.repo}/{app.service}:{tag}"

    def build(self, app: AppRef, staged: dict[str, Any], op_id: str, should_stop: ShouldStop,
              on_build: Callable[[str], None], existing_build_id: str | None = None) -> str:
        if "image" in staged:
            return staged["image"]
        sess = self._for(app)
        project, region = app.target.project, app.region
        image = self.image_for(app, op_id.replace("_", "-"))
        builder_sa = app.target.builder_sa or self.s.builder_sa
        body = {
            "source": staged,
            "steps": [{
                "name": "gcr.io/k8s-skaffold/pack", "entrypoint": "pack",
                "args": ["build", image, "--builder", self.s.builder_image, "--network", "cloudbuild", "--publish"],
            }],
            "serviceAccount": f"projects/{project}/serviceAccounts/{builder_sa}",
            "options": {"logging": "CLOUD_LOGGING_ONLY"},
            "timeout": "900s",
            "tags": ["shipcube", app.service],
        }
        base = f"{BUILD}/projects/{project}/locations/{region}/builds"
        build_id = existing_build_id
        if build_id is None:
            op = self._call(sess, "POST", base, json=body)
            build_id = op["metadata"]["build"]["id"]
            on_build(build_id)
            log.info("build started", extra={"build_id": build_id, "image": image})
        while True:
            b = self._call(sess, "GET", f"{base}/{build_id}")
            if b["status"] in ("SUCCESS",):
                return image
            if b["status"] in ("FAILURE", "INTERNAL_ERROR", "TIMEOUT", "CANCELLED", "EXPIRED"):
                raise DeployError("build_failed", f"Cloud Build {build_id} {b['status']}: {b.get('statusDetail', '')} "
                                                  f"(logs: {b.get('logUrl', '')})")
            if should_stop():
                self._call(sess, "POST", f"{base}/{build_id}:cancel", json={})
                raise DeployError("cancelled", f"Cloud Build {build_id} cancelled")
            time.sleep(3)

    # --- deploy -------------------------------------------------------------------------

    def _ensure_runtime_sa(self, sess: AuthorizedSession, app: AppRef) -> str:
        sa = app.runtime_sa
        if app.target.shared_runtime_sa:
            return sa
        account_id = sa.split("@")[0]
        self._call(sess, "POST", f"{IAM}/projects/{app.target.project}/serviceAccounts", ok=(200, 409),
                   json={"accountId": account_id,
                         "serviceAccount": {"displayName": f"ShipCube app {app.name}"[:100],
                                            "description": f"Runtime for {app.service}; no project roles"}})
        # A just-created SA can take a few seconds to become usable by Cloud Run.
        for _ in range(10):
            r = sess.get(f"{IAM}/projects/{app.target.project}/serviceAccounts/{sa}", timeout=30)
            if r.status_code == 200:
                break
            time.sleep(2)
        return sa

    def _iap_agent(self, sess: AuthorizedSession, project: str) -> str:
        if project not in self._iap_agents:
            op = self._call(sess, "POST", f"https://serviceusage.googleapis.com/v1beta1/projects/{project}/services/"
                                          "iap.googleapis.com:generateServiceIdentity", json={})
            email = (op.get("response") or {}).get("email")
            if not email:
                email = f"service-{self._project_number(sess, project)}@gcp-sa-iap.iam.gserviceaccount.com"
            self._iap_agents[project] = email
        return self._iap_agents[project]

    def _service_url(self, app: AppRef) -> str:
        return f"{RUN}/projects/{app.target.project}/locations/{app.region}/services/{app.service}"

    def _service_body(self, app: AppRef, image: str, caps: dict[str, Any], env: dict[str, str], sa: str) -> dict[str, Any]:
        c = normalize_caps(caps)
        return {
            "labels": {"shipcube-app": app.name, "shipcube-workspace": app.workspace_id[:8], "managed-by": "shipcube"},
            "ingress": "INGRESS_TRAFFIC_ALL",
            "invokerIamDisabled": False,
            # A redeploy always serves the newest revision, also after a rollback pinned an older one.
            "traffic": [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST", "percent": 100}],
            "iapEnabled": True,
            "template": {
                "serviceAccount": sa,
                "executionEnvironment": "EXECUTION_ENVIRONMENT_GEN2",
                "timeout": f"{int(c.get('timeout_s', 60))}s",
                "maxInstanceRequestConcurrency": int(c.get("concurrency", 80)),
                "scaling": {"minInstanceCount": 0, "maxInstanceCount": int(c.get("max_instances", 2))},
                "containers": [{
                    "image": image,
                    "resources": {"limits": {"cpu": str(c.get("cpu", "1")), "memory": str(c.get("memory", "512Mi"))},
                                  "cpuIdle": True, "startupCpuBoost": True},
                    "env": [{"name": k, "value": v} for k, v in sorted(env.items())],
                }],
            },
        }

    def deploy(self, app: AppRef, image: str, caps: dict[str, Any], env: dict[str, str]) -> Deployed:
        sess = self._for(app)
        sa = self._ensure_runtime_sa(sess, app)
        body = self._service_body(app, image, caps, env, sa)
        op = self._call(sess, "PATCH", f"{self._service_url(app)}?allowMissing=true", json=body)
        svc = self._wait_op(sess, RUN, op)
        self._grant_iap_invoker(sess, app)
        return Deployed(url=svc.get("uri", ""), revision=svc.get("latestReadyRevision", "").rsplit("/", 1)[-1],
                        image=image, extra={"service": app.service, "runtime_sa": sa})

    def _grant_iap_invoker(self, sess: AuthorizedSession, app: AppRef) -> None:
        agent = f"serviceAccount:{self._iap_agent(sess, app.target.project)}"
        url = self._service_url(app)
        policy = self._call(sess, "GET", f"{url}:getIamPolicy")
        bindings = policy.get("bindings", [])
        inv = next((b for b in bindings if b["role"] == INVOKER), None)
        if inv and agent in inv["members"]:
            return
        if inv:
            inv["members"].append(agent)
        else:
            bindings.append({"role": INVOKER, "members": [agent]})
        policy["bindings"] = bindings
        self._call(sess, "POST", f"{url}:setIamPolicy", json={"policy": policy})

    # --- access (IAP accessor bindings) ---------------------------------------------------

    def _iap_resource(self, sess: AuthorizedSession, app: AppRef) -> str:
        num = self._project_number(sess, app.target.project)
        return f"{IAP}/projects/{num}/iap_web/cloud_run-{app.region}/services/{app.service}"

    def set_access(self, app: AppRef, principals: list[str]) -> None:
        sess = self._for(app)
        self._iap_agent(sess, app.target.project)  # also learns the project number
        res = self._iap_resource(sess, app)
        policy = self._call(sess, "POST", f"{res}:getIamPolicy", json={})
        others = [b for b in policy.get("bindings", []) if b["role"] != ACCESSOR]
        members = sorted(set(principals))
        policy["bindings"] = others + ([{"role": ACCESSOR, "members": members}] if members else [])
        self._call(sess, "POST", f"{res}:setIamPolicy", json={"policy": policy})

    # --- day-2 ---------------------------------------------------------------------------

    def update_caps(self, app: AppRef, caps: dict[str, Any]) -> None:
        sess = self._for(app)
        svc = self._call(sess, "GET", self._service_url(app))
        c = normalize_caps(caps)
        t = svc["template"]
        t["scaling"] = {**t.get("scaling", {}), "minInstanceCount": 0, "maxInstanceCount": int(c.get("max_instances", 2))}
        t["maxInstanceRequestConcurrency"] = int(c.get("concurrency", 80))
        t["timeout"] = f"{int(c.get('timeout_s', 60))}s"
        t["containers"][0]["resources"]["limits"] = {"cpu": str(c.get("cpu", "1")), "memory": str(c.get("memory", "512Mi"))}
        t.pop("revision", None)
        op = self._call(sess, "PATCH", self._service_url(app), json={"template": t},
                        params={"updateMask": "template"})
        self._wait_op(sess, RUN, op)

    def status(self, app: AppRef) -> dict[str, Any]:
        sess = self._for(app)
        try:
            svc = self._call(sess, "GET", self._service_url(app))
        except DeployError as e:
            if e.code == "not_found":
                return {"ready": False, "exists": False, "backend": "gcp"}
            raise
        cond = svc.get("terminalCondition", {})
        t = svc.get("template", {})
        return {
            "backend": "gcp", "exists": True, "project": app.target.project, "service": app.service,
            "ready": cond.get("state") == "CONDITION_SUCCEEDED" and not svc.get("reconciling"),
            "condition": cond.get("message") or cond.get("state"),
            "url": svc.get("uri"), "latest_ready_revision": svc.get("latestReadyRevision", "").rsplit("/", 1)[-1],
            "iap_enabled": bool(svc.get("iapEnabled")), "ingress": svc.get("ingress"),
            "image": (t.get("containers") or [{}])[0].get("image"),
            "scaling": t.get("scaling"), "concurrency": t.get("maxInstanceRequestConcurrency"),
            "execution_environment": t.get("executionEnvironment"), "runtime_sa": t.get("serviceAccount"),
            "updated_at": svc.get("updateTime"),
            "traffic": [{"revision": (t.get("revision") or "LATEST"), "percent": t.get("percent", 0)}
                        for t in svc.get("trafficStatuses", [])],
        }

    def route_traffic(self, app: AppRef, revision: str) -> None:
        sess = self._for(app)
        op = self._call(sess, "PATCH", self._service_url(app), params={"updateMask": "traffic"},
                        json={"traffic": [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": revision, "percent": 100}]})
        self._wait_op(sess, RUN, op)

    def download_upload(self, workspace_id: str, upload_id: str, dest: Any) -> Any:
        blob = self._bucket().blob(self._object(workspace_id, upload_id))
        if not blob.exists():
            raise DeployError("invalid_argument", f"upload {upload_id} was not found")
        blob.download_to_filename(str(dest))
        return dest

    def soft_delete(self, app: AppRef) -> None:
        """Unreachable but restorable: no IAP accessors and internal-only ingress. Instances already
        scale to zero when idle, and nothing external can wake them."""
        self.set_access(app, [])
        sess = self._for(app)
        op = self._call(sess, "PATCH", self._service_url(app), json={"ingress": "INGRESS_TRAFFIC_INTERNAL_ONLY"},
                        params={"updateMask": "ingress"})
        self._wait_op(sess, RUN, op)

    def restore(self, app: AppRef, principals: list[str]) -> None:
        sess = self._for(app)
        op = self._call(sess, "PATCH", self._service_url(app), json={"ingress": "INGRESS_TRAFFIC_ALL"},
                        params={"updateMask": "ingress"})
        self._wait_op(sess, RUN, op)
        self.set_access(app, principals)

    def purge(self, app: AppRef) -> None:
        sess = self._for(app)
        try:
            self._wait_op(sess, RUN, self._call(sess, "DELETE", self._service_url(app)))
        except DeployError as e:
            if e.code != "not_found":
                raise
        if not app.target.shared_runtime_sa:
            self._call(sess, "DELETE", f"{IAM}/projects/{app.target.project}/serviceAccounts/{app.runtime_sa}", ok=(200, 404))
        pkg = f"{AR}/projects/{app.target.project}/locations/{app.region}/repositories/{self.s.repo}/packages/{app.service}"
        try:
            self._wait_op(sess, AR, self._call(sess, "DELETE", pkg))
        except DeployError as e:
            if e.code != "not_found":
                raise
        log.info("purged %s in %s", app.service, app.target.project)


def describe_resources(s: Settings) -> list[str]:
    """Human-readable list of what a GCP deploy touches (for approval and the README)."""
    return [
        f"GCS bucket gs://{s.upload_bucket} ({s.region}), private, objects deleted after 1 day",
        f"Artifact Registry repo {s.repo} in {s.apps_project} ({s.region}), keep 5 versions",
        f"Cloud Build builds in {s.apps_project}, as {s.builder_sa}",
        f"Cloud Run services in {s.apps_project}: gen2, min 0 / max 2 instances, 1 vCPU, 512 MiB, IAP on",
        "One runtime service account per app (app-<hash>@...), no project roles",
    ]

