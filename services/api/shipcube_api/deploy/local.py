"""Real deploys on this machine (SHIPCUBE_BACKEND=local): the GCP flow with local stand-ins.

  uploads       fake-gcs-server, V4 signed PUT URLs from the same storage client as GCP, under the
                workspace's prefix (ws/<workspace>/), derived from the token as on GCP
  build         Cloud Native Buildpacks (`pack build`, the gcr.io/buildpacks builder Cloud Build
                uses), or `docker build` when the source has a Dockerfile. Images are named
                shipcube.local/ws-<ws_short>/<service>:<tag> and deployed by image id (sha256)
  deploy        one container per revision on the stack's Docker network with the caps' cpu and
                memory, env, PORT=8080; private: only reachable through local_iap.py, which checks
                the caller against the access list like IAP does
  state         one JSON file per service in SHIPCUBE_LOCAL_STATE (revisions, traffic, ingress,
                access, caps), shared by the worker and the local IAP proxy

Isolation: there is one Docker daemon and no per-workspace identity locally, so the workspace
boundary is enforced by the deployer itself (the same isolation.py checks as GCP): a build reads
only the source object under the caller's prefix and tags only into the caller's namespace, and a
deploy runs only an image id tagged in that namespace. The IAM half of C-1 exists only on GCP.
"""

from __future__ import annotations

import contextlib
import fcntl
import io
import json
import logging
import os
import secrets
import shutil
import subprocess
import tarfile
import tempfile
import time
from base64 import b64decode
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import httpx

from ..settings import Settings
from . import isolation
from .base import AppRef, Deployed, DeployError, ShouldStop, normalize_caps

log = logging.getLogger(__name__)
UPLOAD_MAX_BYTES = 100 * 1024 * 1024
KEEP_REVISIONS = 5
PORT = 8080


class Docker:
    """The docker/pack/git CLIs. Tests replace it with a recorder."""

    def run(self, args: list[str], *, timeout: float = 900, cwd: str | None = None, check: bool = True) -> str:
        try:
            p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        except FileNotFoundError as e:
            raise DeployError("unavailable", f"{args[0]} is not installed (the local backend needs docker and pack)") from e
        except subprocess.TimeoutExpired as e:
            raise DeployError("timeout", f"{' '.join(args[:3])} took more than {timeout:.0f}s") from e
        if check and p.returncode != 0:
            raise DeployError("gcp_error", f"{' '.join(args[:3])} failed ({p.returncode}): {(p.stderr or p.stdout)[-1500:]}")
        return p.stdout.strip()


class LocalDeployer:
    name = "local"

    def __init__(self, s: Settings, docker: Docker | None = None) -> None:
        self.s = s
        self.docker = docker or Docker()
        self.state_dir = Path(s.local_state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._client = None
        self._signer = None

    # --- object store (fake-gcs-server) -------------------------------------------------

    def _bucket(self):
        from google.auth.credentials import AnonymousCredentials
        from google.cloud import storage

        if self._client is None:
            self._client = storage.Client(project=self.s.apps_project, credentials=AnonymousCredentials(),
                                          client_options={"api_endpoint": self.s.local_gcs_url})
            try:
                self._client.create_bucket(self.s.upload_bucket)
            except Exception as e:  # noqa: BLE001 — 409 when it exists; anything else shows up on first use
                if "409" not in str(e) and "already" not in str(e).lower():
                    log.warning("could not create local bucket %s: %s", self.s.upload_bucket, e)
        return self._client.bucket(self.s.upload_bucket)

    def _signing_credentials(self):
        """A throwaway service-account key: the URL is signed exactly as on GCP (V4). fake-gcs-server
        doesn't verify signatures, so the workspace boundary is the object name, chosen here."""
        if self._signer is None:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
            from google.oauth2 import service_account

            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()).decode()
            self._signer = service_account.Credentials.from_service_account_info({
                "type": "service_account", "client_email": f"local-signer@{self.s.apps_project}.iam.gserviceaccount.com",
                "private_key": pem, "private_key_id": "local", "token_uri": "https://oauth2.googleapis.com/token",
                "project_id": self.s.apps_project})
        return self._signer

    def create_upload(self, workspace_id: str, content_type: str) -> dict[str, Any]:
        upload_id = f"upl_{secrets.token_hex(6)}"
        name = isolation.upload_object(self.s, workspace_id, upload_id)
        url = self._bucket().blob(name).generate_signed_url(
            version="v4", expiration=timedelta(minutes=15), method="PUT", content_type=content_type,
            credentials=self._signing_credentials(), api_access_endpoint=self.s.local_gcs_public_url)
        expires = (datetime.now(UTC) + timedelta(minutes=15)).isoformat(timespec="seconds").replace("+00:00", "Z")
        return {"upload_id": upload_id, "method": "PUT", "url": url, "headers": {"Content-Type": content_type},
                "max_bytes": UPLOAD_MAX_BYTES, "expires_at": expires}

    def download_upload(self, workspace_id: str, upload_id: str, dest: Any) -> Any:
        blob = self._bucket().blob(isolation.upload_object(self.s, workspace_id, upload_id))
        if not blob.exists():
            raise DeployError("invalid_argument", f"upload {upload_id} was not found")
        blob.download_to_filename(str(dest))
        return dest

    def stage_source(self, app: AppRef, source: dict[str, Any], op_id: str) -> dict[str, Any]:
        kind = source["kind"]
        if kind == "image":
            self.check_image(app, source["uri"])
            return {"image": source["uri"]}
        if kind == "git":
            return {"git": {k: v for k, v in {"url": source["url"], "ref": source.get("ref"),
                                              "subdir": source.get("subdir")}.items() if v}}
        bucket = self._bucket()
        if kind == "upload":
            name = isolation.upload_object(self.s, app.workspace_id, source["upload_id"])
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
            name = isolation.inline_object(self.s, app.workspace_id, op_id)
            bucket.blob(name).upload_from_string(buf.getvalue(), content_type="application/gzip")
        return {"storageSource": {"bucket": self.s.upload_bucket, "object": name}}

    # --- build --------------------------------------------------------------------------

    def namespace(self, app: AppRef) -> str:
        return f"{isolation.LOCAL_REGISTRY}/{isolation.ar_repo(self.s, app.workspace_id)}/"

    def check_image(self, app: AppRef, image: str) -> None:
        isolation.check_image(self.s, app.workspace_id, image)
        if image.startswith(f"{isolation.LOCAL_REGISTRY}/"):
            # The digest is a local image id: it must exist and carry a tag in this workspace's namespace.
            image_id = image.rsplit("@", 1)[1]
            tags = self.docker.run(["docker", "image", "inspect", "-f", "{{json .RepoTags}}", image_id], check=False)
            try:
                ok = any(t.startswith(self.namespace(app)) for t in json.loads(tags or "[]"))
            except ValueError:
                ok = False
            if not ok:
                raise DeployError("permission_denied", f"image {image} is not tagged in this workspace's namespace")

    def build(self, app: AppRef, staged: dict[str, Any], op_id: str, should_stop: ShouldStop,
              on_build: Callable[[str], None], existing_build_id: str | None = None) -> str:
        if "image" in staged:
            return staged["image"]
        tag = f"{self.namespace(app)}{app.service}:{op_id.replace('_', '-')}"
        if existing_build_id:  # a retry after a crash: reuse the image if the build got that far
            image_id = self.docker.run(["docker", "image", "inspect", "-f", "{{.Id}}", tag], check=False)
            if image_id.startswith("sha256:"):
                return f"{tag.rsplit(':', 1)[0]}@{image_id}"
        on_build(f"local-{op_id}")
        work = Path(tempfile.mkdtemp(prefix="shipcube-build-"))
        try:
            src = self._fetch_source(app, staged, work)
            if should_stop():
                raise DeployError("cancelled", "build cancelled")
            if (src / "Dockerfile").is_file():
                self.docker.run(["docker", "build", "--pull", "-t", tag, str(src)], timeout=900)
            else:
                self.docker.run(["pack", "build", tag, "--builder", self.s.builder_image, "--path", str(src),
                                 "--pull-policy", "if-not-present"], timeout=900)
        except DeployError as e:
            if e.code == "gcp_error":
                raise DeployError("build_failed", str(e)) from e
            raise
        finally:
            shutil.rmtree(work, ignore_errors=True)
        image_id = self.docker.run(["docker", "image", "inspect", "-f", "{{.Id}}", tag])
        if not image_id.startswith("sha256:"):
            raise DeployError("build_failed", f"no image id for {tag}")
        return f"{tag.rsplit(':', 1)[0]}@{image_id}"

    def _fetch_source(self, app: AppRef, staged: dict[str, Any], work: Path) -> Path:
        src = work / "src"
        src.mkdir()
        if "git" in staged:
            g = staged["git"]
            self.docker.run(["git", "clone", "--depth", "50", "--", g["url"], str(src)], timeout=300)
            if g.get("ref"):
                self.docker.run(["git", "-C", str(src), "checkout", g["ref"]], timeout=60)
            return src / g["subdir"] if g.get("subdir") else src
        loc = staged["storageSource"]
        isolation.check_object(self.s, app.workspace_id, loc["bucket"], loc["object"])
        archive = work / "source.tgz"
        self._bucket().blob(loc["object"]).download_to_filename(str(archive))
        with tarfile.open(archive) as tar:
            tar.extractall(src, filter="data")  # no absolute paths, .., devices or links out of src
        return src

    # --- state ----------------------------------------------------------------------------

    def _path(self, app: AppRef) -> Path:
        return self.state_dir / f"{app.service}.json"

    @contextlib.contextmanager
    def _state(self, app: AppRef):
        path = self._path(app)
        with open(self.state_dir / ".lock", "a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            st = json.loads(path.read_text()) if path.exists() else {
                "service": app.service, "app": app.name, "workspace_id": app.workspace_id, "revisions": [],
                "traffic": None, "ingress": "all", "access": [], "caps": {}}
            yield st
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(st, indent=1))
            os.replace(tmp, path)

    def _read(self, app: AppRef) -> dict[str, Any] | None:
        path = self._path(app)
        return json.loads(path.read_text()) if path.exists() else None

    def url(self, app: AppRef) -> str:
        return f"http://{app.service}.localhost:{self.s.local_iap_port}"

    # --- deploy -------------------------------------------------------------------------

    def _run_container(self, app: AppRef, revision: str, image: str, caps: dict[str, Any], env: dict[str, str]) -> None:
        c = normalize_caps(caps)
        mem = str(c.get("memory", "512Mi"))
        mib = int(mem[:-2]) * (1024 if mem.endswith("Gi") else 1)
        args = ["docker", "run", "-d", "--name", f"sc-{revision}", "--network", self.s.local_network,
                "--network-alias", revision, "-p", f"127.0.0.1::{PORT}",
                "--cpus", str(c.get("cpu", "1")), "--memory", f"{mib}m", "--pids-limit", "512",
                "--security-opt", "no-new-privileges", "--restart", "no",
                "--label", "managed-by=shipcube", "--label", f"shipcube.app={app.name}",
                "--label", f"shipcube.service={app.service}",
                "--label", f"shipcube.workspace={app.workspace_id}", "--label", f"shipcube.revision={revision}"]
        base = {"PORT": str(PORT), "K_SERVICE": app.service, "K_REVISION": revision}
        if self.s.ai_gateway_url:
            base["SHIPCUBE_AI_GATEWAY"] = self.s.ai_gateway_url
        for k, v in {**base, **env}.items():
            args += ["-e", f"{k}={v}"]
        args.append(image.rsplit("@", 1)[1] if image.startswith(f"{isolation.LOCAL_REGISTRY}/") else image)
        self.docker.run(args, timeout=120)

    def upstream(self, revision: str) -> str:
        """Base URL of a revision's container, from wherever this process runs (settings.local_upstream)."""
        if self.s.local_upstream == "network":
            return f"http://{revision}:{PORT}"
        out = self.docker.run(["docker", "port", f"sc-{revision}", f"{PORT}/tcp"])
        return f"http://127.0.0.1:{int(out.splitlines()[0].rsplit(':', 1)[1])}"

    def _wait_ready(self, revision: str, timeout_s: float) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            running = self.docker.run(["docker", "inspect", "-f", "{{.State.Running}}", f"sc-{revision}"], check=False)
            if running != "true":
                logs = self.docker.run(["docker", "logs", "--tail", "30", f"sc-{revision}"], check=False)
                raise DeployError("unavailable", f"revision {revision} exited during startup: {logs[-1500:]}")
            # Any HTTP answer counts. A bare TCP connect isn't enough: Docker Desktop and Colima accept
            # on the published port before the container listens.
            with contextlib.suppress(httpx.HTTPError, ValueError, IndexError, DeployError):
                httpx.get(self.upstream(revision) + "/", timeout=2)
                return
            time.sleep(0.5)
        raise DeployError("timeout", f"revision {revision} did not listen on port {PORT} within {timeout_s:.0f}s")

    def deploy(self, app: AppRef, image: str, caps: dict[str, Any], env: dict[str, str]) -> Deployed:
        self.check_image(app, image)
        with self._state(app) as st:
            revision = f"{app.service}-{len(st['revisions']) + 1:05d}-{secrets.token_hex(2)}"
        self._run_container(app, revision, image, caps, env)
        try:
            self._wait_ready(revision, float(self.s.local_ready_timeout_s))
        except DeployError:
            self.docker.run(["docker", "rm", "-f", f"sc-{revision}"], check=False)
            raise
        with self._state(app) as st:
            old = st["traffic"]
            st["revisions"].append({"name": revision, "image": image, "created": time.time()})
            st.update(traffic=revision, ingress="all", caps=normalize_caps(caps))
            stale = [r["name"] for r in st["revisions"][:-KEEP_REVISIONS]]
            st["revisions"] = st["revisions"][-KEEP_REVISIONS:]
        if old:
            self.docker.run(["docker", "stop", "-t", "5", f"sc-{old}"], check=False)
        for r in stale:
            self.docker.run(["docker", "rm", "-f", f"sc-{r}"], check=False)
        return Deployed(url=self.url(app), revision=revision, image=image,
                        extra={"service": app.service, "container": f"sc-{revision}"})

    def set_access(self, app: AppRef, principals: list[str]) -> None:
        with self._state(app) as st:
            st["access"] = sorted(set(principals))

    def update_caps(self, app: AppRef, caps: dict[str, Any]) -> None:
        """Cloud Run makes a new revision; locally cpu and memory change in place, and the proxy
        reads concurrency and timeout from the state file."""
        c = normalize_caps(caps)
        st = self._read(app)
        if not st or not st["traffic"]:
            raise DeployError("not_found", f"{app.service} has no revision")
        image = next(r["image"] for r in st["revisions"] if r["name"] == st["traffic"])
        self.check_image(app, image)
        mem = str(c.get("memory", "512Mi"))
        mib = int(mem[:-2]) * (1024 if mem.endswith("Gi") else 1)
        self.docker.run(["docker", "update", "--cpus", str(c.get("cpu", "1")), "--memory", f"{mib}m",
                         "--memory-swap", f"{mib}m", f"sc-{st['traffic']}"])
        with self._state(app) as st:
            st["caps"] = c

    def route_traffic(self, app: AppRef, revision: str) -> None:
        st = self._read(app)
        if not st or revision not in [r["name"] for r in st["revisions"]]:
            raise DeployError("not_found", f"revision {revision} is not kept locally")
        self.docker.run(["docker", "start", f"sc-{revision}"])
        self._wait_ready(revision, float(self.s.local_ready_timeout_s))
        with self._state(app) as st:
            old, st["traffic"] = st["traffic"], revision
        if old and old != revision:
            self.docker.run(["docker", "stop", "-t", "5", f"sc-{old}"], check=False)

    def status(self, app: AppRef) -> dict[str, Any]:
        st = self._read(app)
        if not st or not st["traffic"]:
            return {"ready": False, "exists": False, "backend": "local"}
        rev = st["traffic"]
        running = self.docker.run(["docker", "inspect", "-f", "{{.State.Running}}", f"sc-{rev}"], check=False) == "true"
        image = next((r["image"] for r in st["revisions"] if r["name"] == rev), None)
        return {"backend": "local", "exists": True, "service": app.service, "ready": running and st["ingress"] == "all",
                "condition": "running" if running else "stopped", "url": self.url(app), "latest_ready_revision": rev,
                "iap_enabled": True, "ingress": st["ingress"], "image": image, "container": f"sc-{rev}",
                "access": st["access"], "caps": st["caps"], "revisions": [r["name"] for r in st["revisions"]],
                "traffic": [{"revision": rev, "percent": 100}]}

    def logs(self, revision: str, limit: int) -> list[dict[str, Any]]:
        if not revision:
            return []
        out = self.docker.run(["docker", "logs", "--timestamps", "--tail", str(limit), f"sc-{revision}"], check=False)
        entries = []
        for line in out.splitlines():
            ts, _, msg = line.partition(" ")
            entries.append({"timestamp": ts[:19] + "Z", "severity": "ERROR" if "error" in msg.lower() else "INFO",
                            "message": msg, "revision": revision})
        return entries[::-1]

    def soft_delete(self, app: AppRef) -> None:
        with self._state(app) as st:
            st.update(access=[], ingress="internal")
            rev = st["traffic"]
        if rev:
            self.docker.run(["docker", "stop", "-t", "5", f"sc-{rev}"], check=False)

    def restore(self, app: AppRef, principals: list[str]) -> None:
        st = self._read(app)
        if st and st["traffic"]:
            self.docker.run(["docker", "start", f"sc-{st['traffic']}"])
            self._wait_ready(st["traffic"], float(self.s.local_ready_timeout_s))  # like deploy: succeeded = answering
        with self._state(app) as st:
            st.update(ingress="all", access=sorted(set(principals)))

    def purge(self, app: AppRef) -> None:
        st = self._read(app) or {"revisions": []}
        for r in st["revisions"]:
            self.docker.run(["docker", "rm", "-f", f"sc-{r['name']}"], check=False)
        images = self.docker.run(["docker", "images", "-q", f"{self.namespace(app)}{app.service}"], check=False)
        for image_id in sorted(set(images.split())):
            self.docker.run(["docker", "rmi", "-f", image_id], check=False)
        self._path(app).unlink(missing_ok=True)
        log.info("purged %s locally", app.service)


    def verify_link(self, project_id: str, deployer_sa: str) -> list[str]:
        raise DeployError("invalid_argument", "linking a GCP project needs SHIPCUBE_BACKEND=gcp; the local backend "
                                              "deploys only to Docker on this machine")


class LocalTelemetry:
    """inspect_app logs from `docker logs`; metrics aren't collected locally."""

    def __init__(self, deployer: LocalDeployer) -> None:
        self.d = deployer

    def logs(self, app: dict[str, Any], limit: int, severity: str | None, page_token: str | None) -> dict[str, Any]:
        entries = self.d.logs(app.get("revision") or "", limit)
        return {"entries": [e for e in entries if not severity or e["severity"] == severity], "next_page_token": None}

    def metrics(self, app: dict[str, Any], window: str) -> dict[str, Any]:
        from ..telemetry import TelemetryUnavailable

        raise TelemetryUnavailable("metrics are not collected by the local backend; use logs")
