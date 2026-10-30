"""Cleans up what a crashed profiler run leaves behind (docs/research/profiler-sandbox.md §8).

The profiler runner (packages/profiler, CloudRunJobRunner) deletes its job and source tarball in a
`finally`, but a worker that dies between creating them and that `finally` leaves them behind. The
worker's housekeeping (every minute) runs this:

- delete Cloud Run jobs labelled `shipcube-role=profiler` older than 30 minutes;
- cancel Cloud Build builds tagged `shipcube-profiler` that are still running after 10 minutes;
- delete `profiler-src/` objects older than 1 hour (the bucket's 1-day lifecycle rule is the backstop);
- the global brake: if more profiler job executions are running than the ceiling allows (5, against
  a ceiling of 3), something bypassed admission. Set the brake, so new estimates run static for
  15 minutes, and log an error.

It runs only where the GCP profiler is configured (SHIPCUBE_PROFILER_JOB), as sc-worker, in the
profiler's project (SHIPCUBE_PROFILER_PROJECT, else the apps project) and bucket
(SHIPCUBE_PROFILER_BUCKET).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import quote

from . import db, profiler_guard
from .settings import settings

log = logging.getLogger("shipcube.profiler_janitor")

JOB_LABEL = ("shipcube-role", "profiler")
BUILD_TAG = "shipcube-profiler"
SRC_PREFIX = "profiler-src/"
JOB_MAX_AGE = timedelta(minutes=30)
BUILD_MAX_AGE = timedelta(minutes=10)
SRC_MAX_AGE = timedelta(hours=1)
BRAKE_EXECUTIONS = 5
BUILD_ACTIVE = {"QUEUED", "PENDING", "WORKING"}


@dataclass
class Leftovers:
    jobs: list[str] = field(default_factory=list)
    builds: list[str] = field(default_factory=list)
    objects: list[str] = field(default_factory=list)
    running_executions: int = 0


class ProfilerCloud(Protocol):
    def jobs(self) -> list[dict[str, Any]]: ...            # {name, created: datetime, running_executions: int}
    def delete_job(self, name: str) -> None: ...
    def builds(self) -> list[dict[str, Any]]: ...          # {id, created: datetime, status}
    def cancel_build(self, build_id: str) -> None: ...
    def objects(self) -> list[dict[str, Any]]: ...         # {name, created: datetime}
    def delete_object(self, name: str) -> None: ...


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class GcpProfilerCloud:
    """Cloud Run Admin v2, Cloud Build v1 and Cloud Storage JSON APIs, with the worker's own credentials."""

    RUN = "https://run.googleapis.com/v2"
    BUILD = "https://cloudbuild.googleapis.com/v1"
    STORAGE = "https://storage.googleapis.com/storage/v1"

    def __init__(self, project: str, region: str, bucket: str) -> None:
        import google.auth
        from google.auth.transport.requests import AuthorizedSession

        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        self.sess = AuthorizedSession(creds)
        self.project, self.region, self.bucket = project, region, bucket

    def _get(self, url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        r = self.sess.get(url, params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def _pages(self, url: str, key: str, params: dict[str, str] | None = None) -> list[dict[str, Any]]:
        out, params = [], dict(params or {})
        while True:
            body = self._get(url, params)
            out += body.get(key, [])
            if not (token := body.get("nextPageToken")):
                return out
            params["pageToken"] = token

    def jobs(self) -> list[dict[str, Any]]:
        parent = f"{self.RUN}/projects/{self.project}/locations/{self.region}/jobs"
        out = []
        for j in self._pages(parent, "jobs"):
            if (j.get("labels") or {}).get(JOB_LABEL[0]) != JOB_LABEL[1]:
                continue
            out.append({"name": j["name"], "created": _ts(j["createTime"]),
                        "running_executions": int(j.get("runningExecutionCount") or 0)})
        return out

    def delete_job(self, name: str) -> None:
        r = self.sess.delete(f"{self.RUN}/{name}", timeout=30)
        if r.status_code not in (200, 404):
            r.raise_for_status()

    def builds(self) -> list[dict[str, Any]]:
        url = f"{self.BUILD}/projects/{self.project}/locations/{self.region}/builds"
        flt = f'tags="{BUILD_TAG}" AND (status="QUEUED" OR status="PENDING" OR status="WORKING")'
        return [{"id": b["id"], "created": _ts(b["createTime"]), "status": b.get("status")}
                for b in self._pages(url, "builds", {"filter": flt})]

    def cancel_build(self, build_id: str) -> None:
        url = f"{self.BUILD}/projects/{self.project}/locations/{self.region}/builds/{build_id}:cancel"
        r = self.sess.post(url, json={}, timeout=30)
        if r.status_code not in (200, 400, 404):  # 400: it finished meanwhile
            r.raise_for_status()

    def objects(self) -> list[dict[str, Any]]:
        return [{"name": o["name"], "created": _ts(o["timeCreated"])}
                for o in self._pages(f"{self.STORAGE}/b/{self.bucket}/o", "items", {"prefix": SRC_PREFIX})]

    def delete_object(self, name: str) -> None:
        r = self.sess.delete(f"{self.STORAGE}/b/{self.bucket}/o/{quote(name, safe='')}", timeout=30)
        if r.status_code not in (200, 204, 404):
            r.raise_for_status()


class Janitor:
    def __init__(self, cloud: ProfilerCloud) -> None:
        self.cloud = cloud

    def sweep(self, now: datetime | None = None) -> Leftovers:
        now = now or datetime.now(UTC)
        done = Leftovers()
        for j in self.cloud.jobs():
            done.running_executions += j["running_executions"]
            if now - j["created"] > JOB_MAX_AGE:
                self.cloud.delete_job(j["name"])
                done.jobs.append(j["name"])
        for b in self.cloud.builds():
            if b["status"] in BUILD_ACTIVE and now - b["created"] > BUILD_MAX_AGE:
                self.cloud.cancel_build(b["id"])
                done.builds.append(b["id"])
        for o in self.cloud.objects():
            if now - o["created"] > SRC_MAX_AGE:
                self.cloud.delete_object(o["name"])
                done.objects.append(o["name"])
        if done.running_executions > BRAKE_EXECUTIONS:
            log.error("profiler brake: more sandbox executions than the ceiling allows; estimates run static for 15 min",
                      extra={"running_executions": done.running_executions, "ceiling": profiler_guard.Limits.from_env().global_runs})
            with db.worker_tx() as conn:
                profiler_guard.set_brake(conn, f"{done.running_executions} executions running at once")
        if done.jobs or done.builds or done.objects:
            log.warning("profiler janitor cleaned up leftovers", extra={"jobs": done.jobs, "builds": done.builds,
                                                                         "objects": len(done.objects)})
        return done


def from_env() -> Janitor | None:
    """The janitor for this deployment, or None where the GCP profiler isn't configured."""
    if not settings.profiler_job:
        return None
    bucket = os.environ.get("SHIPCUBE_PROFILER_BUCKET")
    if not bucket:
        log.warning("SHIPCUBE_PROFILER_BUCKET is not set; the profiler janitor is off")
        return None
    project = os.environ.get("SHIPCUBE_PROFILER_PROJECT") or settings.apps_project
    region = os.environ.get("SHIPCUBE_PROFILER_REGION") or settings.region
    return Janitor(GcpProfilerCloud(project, region, bucket))
