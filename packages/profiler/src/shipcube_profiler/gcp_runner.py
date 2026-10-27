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

from .agent import RESULT_PREFIX
from .detect import CodeSummary
from .profile import Hints, Profile

log = logging.getLogger("shipcube.profiler")

RUN = "https://run.googleapis.com/v2"
BUILD = "https://cloudbuild.googleapis.com/v1"
LOGGING = "https://logging.googleapis.com/v2/entries:list"
STORAGE_UPLOAD = "https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o"
STORAGE_OBJ = "https://storage.googleapis.com/storage/v1/b/{bucket}/o/{name}"

DOCKERFILE = """\
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install "psutil>=6.0,<8"
COPY app/ /app/
RUN if [ -f /app/requirements.txt ]; then pip install -r /app/requirements.txt; fi
COPY shipcube/ /opt/shipcube/
ENV PYTHONPATH=/opt/shipcube
WORKDIR /app
ENTRYPOINT ["python", "-m", "shipcube_profiler.agent", "/app"]
"""


class ProfilerError(RuntimeError):
    """The GCP profiler failed at `stage` (config, upload, build, job, run, logs)."""

    def __init__(self, stage: str, message: str):
        super().__init__(f"{stage}: {message}")
        self.stage = stage


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
    build_timeout_s: int = 900

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


def _package(app_dir: Path) -> bytes:
    """Tarball: app/ (the user's code), shipcube/ (profiler + classifier features), Dockerfile."""
    buf = io.BytesIO()

    def skip(rel: Path) -> bool:
        return bool({".git", ".venv", "venv", "node_modules", "__pycache__"} & set(rel.parts))

    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = DOCKERFILE.encode()
        ti = tarfile.TarInfo("Dockerfile")
        ti.size = len(data)
        tar.addfile(ti, io.BytesIO(data))
        for p in sorted(app_dir.rglob("*")):
            rel = p.relative_to(app_dir)
            if p.is_file() and not p.is_symlink() and not skip(rel):
                tar.add(p, arcname=f"app/{rel}", recursive=False)
        for pkg in (shipcube_profiler, shipcube_classifier):
            root = Path(pkg.__file__).parent
            for p in sorted(root.rglob("*.py")):
                rel = p.relative_to(root)
                if not skip(rel):
                    tar.add(p, arcname=f"shipcube/{root.name}/{rel}", recursive=False)
    return buf.getvalue()


class CloudRunJobRunner:
    name = "gcp"

    def __init__(self, config: GcpProfilerConfig | None = None, client: GcpClient | None = None,
                 log_wait_s: float = 90.0, poll_s: float = 3.0):
        self.config = config or GcpProfilerConfig.from_env()
        self._client = client
        self.log_wait_s, self.poll_s = log_wait_s, poll_s

    @property
    def client(self) -> GcpClient:
        if self._client is None:
            self._client = GcpClient(timeout_s=30)
        return self._client

    # --- specs ------------------------------------------------------------------------

    def image(self, run_id: str) -> str:
        c = self.config
        return f"{c.region}-docker.pkg.dev/{c.project}/{c.repository}/prof-{run_id}:latest"

    def build_spec(self, object_name: str, image: str) -> dict:
        c = self.config
        spec = {
            "source": {"storageSource": {"bucket": c.bucket, "object": object_name}},
            "steps": [{"name": "gcr.io/cloud-builders/docker", "args": ["build", "-t", image, "."]}],
            "images": [image],
            "timeout": f"{c.build_timeout_s}s",
            "options": {"logging": "CLOUD_LOGGING_ONLY"},
            "tags": ["shipcube-profiler"],
        }
        if c.build_service_account:
            spec["serviceAccount"] = (f"projects/{c.project}/serviceAccounts/{c.build_service_account}")
        return spec

    def job_spec(self, image: str, run_id: str, hints: Hints | None = None) -> dict:
        """Cloud Run Admin API v2 Job body (projects.locations.jobs.create)."""
        c = self.config
        h = hints or Hints()
        env = [{"name": "SHIPCUBE_RUN_ID", "value": run_id},
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
        op = self.client.post(f"{BUILD}/projects/{c.project}/locations/{c.region}/builds",
                              json=self.build_spec(object_name, image))
        done = self.client.wait_operation(f"{BUILD}/{op['name']}", timeout_s=c.build_timeout_s + 60,
                                          poll_s=self.poll_s)
        build = done.get("response", {})
        if build.get("status") != "SUCCESS":
            raise ProfilerError("build", f"status {build.get('status')}: {build.get('statusDetail', '')[:200]} "
                                         f"(build {build.get('id')})")
        imgs = build.get("results", {}).get("images", [])
        digest = imgs[0].get("digest") if imgs else None
        return f"{image.rsplit(':', 1)[0]}@{digest}" if digest else image

    def _job_parent(self) -> str:
        return f"{RUN}/projects/{self.config.project}/locations/{self.config.region}/jobs"

    def _create_job(self, job_id: str, spec: dict) -> None:
        try:
            op = self.client.post(self._job_parent(), params={"jobId": job_id}, json=spec)
        except GcpError as e:
            if not e.conflict:   # same run id: the job already exists, reuse it
                raise
            return
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

    def _read_result(self, job_id: str, execution: dict) -> dict:
        c = self.config
        ex_name = execution.get("name", "").rsplit("/", 1)[-1]
        flt = (f'resource.type="cloud_run_job" AND resource.labels.job_name="{job_id}" '
               f'AND labels."run.googleapis.com/execution_name"="{ex_name}" '
               f'AND textPayload:"{RESULT_PREFIX.strip()}"')
        end = time.monotonic() + self.log_wait_s
        while True:   # log ingestion lags execution completion by a few seconds
            r = self.client.post(LOGGING, json={"resourceNames": [f"projects/{c.project}"], "filter": flt,
                                                "orderBy": "timestamp desc", "pageSize": 5}, idempotent=True)
            for entry in r.get("entries", []):
                text = entry.get("textPayload", "")
                if text.startswith(RESULT_PREFIX):
                    return json.loads(text[len(RESULT_PREFIX):])
            if time.monotonic() > end:
                raise ProfilerError("logs", f"no result line for execution {ex_name} after {self.log_wait_s:.0f}s")
            time.sleep(self.poll_s)

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
        object_name = None
        stage = "upload"
        try:
            with obs.timed(log, "profile.gcp", run_id=run_id) as extra:
                object_name = self._upload(run_id, _package(app_dir))
                stage = "build"
                image = self._build(object_name, self.image(run_id))
                stage = "job"
                self._create_job(job_id, self.job_spec(image, run_id, hints))
                stage = "run"
                execution = self._run_job(job_id)
                stage = "logs"
                result = self._read_result(job_id, execution)
                extra.update(image=image.rsplit("/", 1)[-1][:80])
        except GcpError as e:
            raise ProfilerError(stage, str(e)[:300]) from e
        finally:
            self._cleanup(job_id, object_name)
        profile = Profile.from_dict(result["profile"])
        profile.runner = self.name
        return profile
