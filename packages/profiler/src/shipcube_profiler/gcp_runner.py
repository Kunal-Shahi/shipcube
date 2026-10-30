"""Production profiler: run the app in a Cloud Run job sandbox (gen2, no egress).

Flow for one estimate (all REST, Application Default Credentials, every call with a
timeout; polls and other idempotent calls retried with backoff):

1. Package the app plus this profiler (and the classifier's feature code) with a
   generated Dockerfile into a tarball; upload it to gs://<bucket>/profiler-src/.
2. Cloud Build builds and pushes <region>-docker.pkg.dev/<project>/<repo>/prof-<id>.
   The build has egress (it installs the app's requirements); the job doesn't.
3. Create Cloud Run job prof-<id> from job_spec(): gen2, the zero-role sandbox service
   account, Direct VPC egress ALL_TRAFFIC into a VPC whose only firewall rule denies
   egress, 1 vCPU / 2 GiB, 600 s task timeout, maxRetries 0.
4. Run it and wait for the execution to finish.
5. Read the agent's one-line result (prefix SHIPCUBE_PROFILE) from the execution's
   Cloud Logging entries.
6. Delete the job and the source object (images expire by the repository's cleanup
   policy).

Configuration comes from the environment (GcpProfilerConfig.from_env).
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import secrets
import tarfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import shipcube_classifier
from shipcube_classifier import obs
from shipcube_classifier.gcp import GcpClient, GcpError

import shipcube_profiler

from .abuse import abuse_signals
from .agent import NONCE_ENV, RESULT_PREFIX, parse_result
from .errors import ProfilerError
from .policy import (BUILD_TIMEOUT_S, MAX_IMAGE_BYTES, PYPI_INDEX, check_source_size,
                     sanitize_requirements)
from .detect import CodeSummary
from .profile import Hints, Profile

log = logging.getLogger("shipcube.profiler")

RUN = "https://run.googleapis.com/v2"
BUILD = "https://cloudbuild.googleapis.com/v1"
LOGGING = "https://logging.googleapis.com/v2/entries:list"
STORAGE_UPLOAD = "https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o"
STORAGE_OBJ = "https://storage.googleapis.com/storage/v1/b/{bucket}/o/{name}"

LOG_POLL_S = 5.0
BUILD_TERMINAL = {"SUCCESS", "FAILURE", "INTERNAL_ERROR", "TIMEOUT", "CANCELLED", "EXPIRED"}

# Shared by the Cloud Run job (built by Cloud Build) and the local Docker sandbox
# (docker_runner.py), so both profile the app in the same image. Requirements install
# at build time (with network); the profiling run itself has none. They are the
# sanitized list (policy.sanitize_requirements), installed wheels-only from pypi.org, so
# no package code runs during the build.
#
# The agent's interpreter stays pristine, because the app's wheels come from anyone on PyPI:
# - The app's deps install as an unprivileged user (uid APPDEPS_UID) with --target into
#   /opt/appdeps. That isn't a site dir, so a wheel's .pth files never run there, and
#   the installer can't write the stdlib or site-packages (made read-only first).
#   /opt/appdeps is on the app subprocess's PYTHONPATH only (SHIPCUBE_APP_DEPS).
# - The agent runs as `python -S -P` (no site, so no .pth or sitecustomize; no cwd on
#   sys.path) from /opt/shipcube, which bundles its own psutil. WORKDIR is /, so the
#   upload in /app is never importable by the agent; the app's subprocess runs in /app.
# The agent runs as root (inside the container) and starts the app as SANDBOX_UID,
# which then holds no capabilities: it can't signal, ptrace or read the agent, which is
# also non-dumpable. RLIMIT_NPROC counts only the app's uid. /app is read-only; only
# /tmp is writable.
SANDBOX_UID = 10001
APPDEPS_UID = 10002
REQUIREMENTS_FILE = "shipcube-requirements.txt"
APP_INSTALL = f"--index-url {PYPI_INDEX} -r /opt/{REQUIREMENTS_FILE}"


def render_dockerfile(app_install: str = APP_INSTALL, extra: str = "") -> str:
    """The sandbox Dockerfile. `app_install` and `extra` exist for tests that install a
    local wheel through the same layer as production; production uses the defaults."""
    return f"""\
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_INPUT=1 \\
    PIP_ONLY_BINARY=:all: HOME=/tmp TMPDIR=/tmp PYTHONPATH=/opt/shipcube SHIPCUBE_APP_DEPS=/opt/appdeps \\
    SHIPCUBE_APP_UID={SANDBOX_UID}
RUN pip install --only-binary=:all: --index-url {PYPI_INDEX} --target /opt/shipcube "psutil>=6.0,<8" \\
 && useradd --uid {SANDBOX_UID} --no-create-home --shell /usr/sbin/nologin sandbox \\
 && useradd --uid {APPDEPS_UID} --no-create-home --shell /usr/sbin/nologin appdeps \\
 && chmod -R a-w /usr/local/lib/python3.12 /opt/shipcube \\
 && mkdir /opt/appdeps && chown {APPDEPS_UID}:{APPDEPS_UID} /opt/appdeps
COPY {REQUIREMENTS_FILE} /opt/{REQUIREMENTS_FILE}
{extra}USER {APPDEPS_UID}:{APPDEPS_UID}
RUN pip install --only-binary=:all: --target /opt/appdeps {app_install}
USER root
RUN chown -R root:root /opt/appdeps && chmod -R a-w /opt/appdeps
COPY app/ /app/
COPY shipcube/ /opt/shipcube/
RUN chmod -R a-w /app /opt/shipcube
WORKDIR /
ENTRYPOINT ["python", "-S", "-P", "-m", "shipcube_profiler.agent", "/app"]
"""


DOCKERFILE = render_dockerfile()


@dataclass(frozen=True)
class GcpProfilerConfig:
    project: str
    region: str
    bucket: str                      # source tarballs (lifecycle: delete after 1 day)
    repository: str                  # Artifact Registry Docker repo
    network: str
    subnet: str
    service_account: str             # job runtime SA: no roles, no secrets
    build_service_account: str | None = None
    job_timeout_s: int = 600
    build_timeout_s: int = BUILD_TIMEOUT_S

    @classmethod
    def from_env(cls, env: dict | None = None) -> "GcpProfilerConfig":
        e = os.environ if env is None else env
        need = {"project": "SHIPCUBE_PROFILER_PROJECT", "region": "SHIPCUBE_PROFILER_REGION",
                "bucket": "SHIPCUBE_PROFILER_BUCKET", "repository": "SHIPCUBE_PROFILER_REPOSITORY",
                "network": "SHIPCUBE_PROFILER_NETWORK", "subnet": "SHIPCUBE_PROFILER_SUBNET",
                "service_account": "SHIPCUBE_PROFILER_SERVICE_ACCOUNT"}
        missing = [v for v in need.values() if not e.get(v)]
        if missing:
            raise ProfilerError("config", f"missing environment variables: {', '.join(missing)}")
        return cls(**{k: e[v] for k, v in need.items()},
                   build_service_account=e.get("SHIPCUBE_PROFILER_BUILD_SERVICE_ACCOUNT") or None)


def _package(app_dir: Path, compress: bool = True, *, dockerfile: str = DOCKERFILE,
             extra_files: dict[str, bytes] | None = None) -> bytes:
    """Tarball: app/ (the user's code), shipcube/ (profiler + classifier features), the
    sanitized requirements and the Dockerfile.

    Deterministic (fixed mtimes and owners), so the uncompressed bytes hash the same
    for the same code: the local sandbox uses that hash as its image cache key.
    Raises ProfilerError (reason requirements_policy or too_large) for apps the sandbox
    won't build."""
    buf = io.BytesIO()

    def skip(rel: Path) -> bool:
        return bool({".git", ".venv", "venv", "node_modules", "__pycache__"} & set(rel.parts))

    def norm(ti: tarfile.TarInfo) -> tarfile.TarInfo:
        ti.mtime, ti.uid, ti.gid, ti.uname, ti.gname = 0, 0, 0, "", ""
        ti.mode = 0o644 if ti.isfile() else ti.mode
        return ti

    files = [p for p in sorted(app_dir.rglob("*"))
             if p.is_file() and not p.is_symlink() and not skip(p.relative_to(app_dir))]
    check_source_size(files)
    req = app_dir / "requirements.txt"
    reqs = sanitize_requirements(req.read_text(errors="replace")) if req.is_file() else []

    def add_bytes(tar: tarfile.TarFile, name: str, data: bytes) -> None:
        ti = tarfile.TarInfo(name)
        ti.size = len(data)
        tar.addfile(ti, io.BytesIO(data))

    with tarfile.open(fileobj=buf, mode="w:gz" if compress else "w") as tar:
        add_bytes(tar, "Dockerfile", dockerfile.encode())
        for name, data in sorted((extra_files or {}).items()):   # tests only
            add_bytes(tar, name, data)
        add_bytes(tar, REQUIREMENTS_FILE, "".join(r + "\n" for r in reqs).encode())
        for p in files:
            tar.add(p, arcname=f"app/{p.relative_to(app_dir)}", recursive=False, filter=norm)
        for pkg in (shipcube_profiler, shipcube_classifier):
            root = Path(pkg.__file__).parent
            for p in sorted(root.rglob("*.py")):
                rel = p.relative_to(root)
                if not skip(rel):
                    tar.add(p, arcname=f"shipcube/{root.name}/{rel}", recursive=False, filter=norm)
    return buf.getvalue()


class CloudRunJobRunner:
    name = "gcp"

    def __init__(self, config: GcpProfilerConfig | None = None, client: GcpClient | None = None,
                 log_wait_s: float = 150.0, poll_s: float = 3.0, **overrides: str):
        """overrides (project, region, service_account, ...) replace single fields of the
        environment config, for callers that already know e.g. the apps project."""
        if config is None:
            env = dict(os.environ)
            for k, v in overrides.items():
                if v:
                    env[f"SHIPCUBE_PROFILER_{k.upper()}"] = v
            config = GcpProfilerConfig.from_env(env)
        self.config = config
        self._client = client
        self.log_wait_s, self.poll_s = log_wait_s, poll_s

    @property
    def client(self) -> GcpClient:
        if self._client is None:
            self._client = GcpClient(timeout_s=30, quota_project=self.config.project)
        return self._client

    # --- specs ------------------------------------------------------------------------

    def image(self, run_id: str) -> str:
        c = self.config
        return f"{c.region}-docker.pkg.dev/{c.project}/{c.repository}/prof-{run_id}:latest"

    def build_spec(self, object_name: str, image: str) -> dict:
        c = self.config
        spec = {
            "source": {"storageSource": {"bucket": c.bucket, "object": object_name}},
            "steps": [
                {"id": "build", "name": "gcr.io/cloud-builders/docker", "args": ["build", "-t", image, "."]},
                # Refuse oversized images before they're pushed ($$ escapes Cloud Build substitution).
                {"id": "size-check", "name": "gcr.io/cloud-builders/docker", "entrypoint": "bash", "args": [
                    "-c", f"s=$$(docker image inspect -f '{{{{.Size}}}}' {image}) && echo \"image size $$s\" "
                          f"&& test \"$$s\" -le {MAX_IMAGE_BYTES} || {{ echo \"image too large\" >&2; exit 3; }}"]},
            ],
            "images": [image],
            "timeout": f"{c.build_timeout_s}s",
            "options": {"logging": "CLOUD_LOGGING_ONLY"},
            "tags": ["shipcube-profiler"],
        }
        if c.build_service_account:
            spec["serviceAccount"] = (f"projects/{c.project}/serviceAccounts/{c.build_service_account}")
        return spec

    def job_spec(self, image: str, run_id: str, hints: Hints | None = None, nonce: str = "") -> dict:
        """Cloud Run Admin API v2 Job body (projects.locations.jobs.create)."""
        c = self.config
        h = hints or Hints()
        env = [{"name": "SHIPCUBE_RUN_ID", "value": run_id},
               {"name": NONCE_ENV, "value": nonce},
               {"name": "SHIPCUBE_HINTS", "value": json.dumps(
                   {k: v for k, v in h.__dict__.items() if v is not None and k != "sample_input"})}]
        return {
            "labels": {"shipcube-role": "profiler", "run": run_id},
            "launchStage": "GA",
            "template": {
                "taskCount": 1,
                "parallelism": 1,
                "template": {
                    "maxRetries": 0,
                    "timeout": f"{c.job_timeout_s}s",
                    "serviceAccount": c.service_account,
                    "executionEnvironment": "EXECUTION_ENVIRONMENT_GEN2",
                    "vpcAccess": {
                        "egress": "ALL_TRAFFIC",
                        "networkInterfaces": [{"network": c.network, "subnetwork": c.subnet}],
                    },
                    "containers": [{
                        "image": image,
                        "env": env,
                        "resources": {"limits": {"cpu": "1", "memory": "2Gi"}},
                    }],
                },
            },
        }

    # --- steps --------------------------------------------------------------------------

    def _upload(self, run_id: str, body: bytes) -> str:
        name = f"profiler-src/{run_id}.tar.gz"
        self.client.post(STORAGE_UPLOAD.format(bucket=self.config.bucket), data=body,
                         params={"uploadType": "media", "name": name, "ifGenerationMatch": "0"},
                         headers={"Content-Type": "application/gzip"}, idempotent=True)
        return name

    def _build(self, object_name: str, image: str) -> str:
        c = self.config
        base = f"{BUILD}/projects/{c.project}/locations/{c.region}/builds"
        op = self.client.post(base, json=self.build_spec(object_name, image))
        # The returned operation name is global-style ("operations/build/<project>/...")
        # even for a regional build, and GET on it 404s. Poll the regional build instead.
        build_id = op.get("metadata", {}).get("build", {}).get("id")
        if not build_id:
            raise ProfilerError("build", f"no build id in create response: {str(op)[:200]}")
        end = time.monotonic() + c.build_timeout_s + 60
        while True:
            build = self.client.get(f"{base}/{build_id}")
            if build.get("status") in BUILD_TERMINAL:
                break
            if time.monotonic() > end:
                raise ProfilerError("build", f"build {build_id} not finished after {c.build_timeout_s + 60}s")
            time.sleep(self.poll_s)
        if build.get("status") != "SUCCESS":
            failed = [st.get("id") for st in build.get("steps", []) if st.get("status") in ("FAILURE", "TIMEOUT")]
            raise ProfilerError("build", f"status {build.get('status')} at {failed or '?'}: "
                                         f"{build.get('statusDetail', '')[:200]} (build {build.get('id')})",
                                reason="too_large" if "size-check" in failed else "build_failed")
        imgs = build.get("results", {}).get("images", [])
        digest = imgs[0].get("digest") if imgs else None
        return f"{image.rsplit(':', 1)[0]}@{digest}" if digest else image

    def _job_parent(self) -> str:
        return f"{RUN}/projects/{self.config.project}/locations/{self.config.region}/jobs"

    def _create_job(self, job_id: str, spec: dict) -> None:
        try:
            op = self.client.post(self._job_parent(), params={"jobId": job_id}, json=spec)
        except GcpError as e:
            if not e.conflict:
                raise
            # Same run id, left by an earlier attempt: it carries that attempt's nonce,
            # so replace it rather than reuse it.
            gone = self.client.delete(f"{self._job_parent()}/{job_id}")
            if isinstance(gone, dict) and gone.get("name"):
                self.client.wait_operation(f"{RUN}/{gone['name']}", timeout_s=120, poll_s=self.poll_s)
            op = self.client.post(self._job_parent(), params={"jobId": job_id}, json=spec)
        self.client.wait_operation(f"{RUN}/{op['name']}", timeout_s=120, poll_s=self.poll_s)

    def _run_job(self, job_id: str) -> dict:
        op = self.client.post(f"{self._job_parent()}/{job_id}:run", json={})
        done = self.client.wait_operation(f"{RUN}/{op['name']}", timeout_s=self.config.job_timeout_s + 120,
                                          poll_s=self.poll_s)
        execution = done.get("response", {})
        if execution.get("succeededCount", 0) < 1:
            raise ProfilerError("run", f"execution {execution.get('name', '?').rsplit('/', 1)[-1]} "
                                       f"failed (failedCount={execution.get('failedCount')})")
        return execution

    def _read_result(self, job_id: str, execution: dict, nonce: str = "") -> dict:
        """Find the agent's result line in the execution's logs.

        Cloud Logging's entries.list has a low per-project read quota (it returned 429 in
        the first real run, when this polled every 3 s), so: one query per LOG_POLL_S at
        most, a timestamp bound to keep each query small, and a 429 means wait and try
        again until the deadline rather than fail.
        """
        c = self.config
        ex_name = execution.get("name", "").rsplit("/", 1)[-1]
        since = execution.get("createTime") or execution.get("startTime")
        flt = (f'resource.type="cloud_run_job" AND resource.labels.job_name="{job_id}" '
               f'AND labels."run.googleapis.com/execution_name"="{ex_name}" '
               f'AND textPayload:"{RESULT_PREFIX.strip()}"')
        if since:
            flt += f' AND timestamp>="{since}"'
        end = time.monotonic() + self.log_wait_s
        wait = max(self.poll_s, LOG_POLL_S)
        while True:   # log ingestion lags execution completion by a few seconds
            try:
                r = self.client.request("POST", LOGGING, idempotent=False, json={
                    "resourceNames": [f"projects/{c.project}"], "filter": flt,
                    "orderBy": "timestamp desc", "pageSize": 20})
            except GcpError as e:
                if e.status != 429:
                    raise
                obs.event(log, "profile.logs_throttled", logging.WARNING, wait_s=wait)
                r = {}
                wait = min(wait * 2, 30.0)
            result = parse_result([e.get("textPayload", "") for e in r.get("entries", [])], nonce)
            if result is not None:
                return result
            if time.monotonic() + wait > end:
                raise ProfilerError("logs", f"no result line for execution {ex_name} after {self.log_wait_s:.0f}s")
            time.sleep(wait)

    def _cleanup(self, job_id: str, object_name: str | None) -> None:
        for url in ([f"{self._job_parent()}/{job_id}"]
                    + ([STORAGE_OBJ.format(bucket=self.config.bucket, name=quote(object_name, safe=""))]
                       if object_name else [])):
            try:
                self.client.delete(url)
            except GcpError as e:
                obs.event(log, "profile.cleanup_failed", logging.WARNING, url=url, error=str(e)[:200])

    # --- Runner ---------------------------------------------------------------------------

    def run(self, app_dir: Path, summary: CodeSummary, hints: Hints) -> Profile:
        run_id = obs.context().get("estimate_id", f"r{uuid.uuid4().hex[:12]}")
        run_id = re.sub(r"[^a-z0-9-]", "-", run_id.lower())[:40].strip("-")
        job_id = f"prof-{run_id}"[:49]
        nonce = secrets.token_hex(16)
        package = _package(app_dir)   # policy errors surface before anything is created
        object_name = None
        stage = "upload"
        try:
            with obs.timed(log, "profile.gcp", run_id=run_id) as extra:
                object_name = self._upload(run_id, package)
                stage = "build"
                image = self._build(object_name, self.image(run_id))
                stage = "job"
                self._create_job(job_id, self.job_spec(image, run_id, hints, nonce))
                stage = "run"
                execution = self._run_job(job_id)
                stage = "logs"
                result = self._read_result(job_id, execution, nonce)
                extra.update(image=image.rsplit("/", 1)[-1][:80])
        except GcpError as e:
            raise ProfilerError(stage, str(e)[:300]) from e
        finally:
            self._cleanup(job_id, object_name)
        profile = Profile.from_dict(result["profile"])
        profile.runner = self.name
        profile.abuse_signals = abuse_signals(profile)
        return profile
