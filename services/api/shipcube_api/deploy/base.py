"""The deploy seam: what the control plane needs from a cloud. GcpDeployer does it for real;
MockDeployer keeps the old in-memory behaviour for tests and demos without GCP."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

# Cloud Run (gen2) needs at least 512 MiB; below 1 vCPU, Cloud Run requires concurrency 1.
MIN_MEMORY_MIB = 512


@dataclass(frozen=True)
class Target:
    """Where an app runs: ShipCube's apps project, or a user's linked project."""

    id: str
    kind: str  # "shipcube" | "linked"
    project: str
    # Linked projects: the per-workspace deployer SA we impersonate (security §3). None = ADC directly
    # (or Settings.hosted_deployer_sa for the hosted project).
    deployer_sa: str | None = None
    # Linked projects use the two SAs link-project.sh creates; hosted apps get one SA each.
    builder_sa: str | None = None
    shared_runtime_sa: str | None = None


@dataclass(frozen=True)
class AppRef:
    name: str
    workspace_id: str
    target: Target
    region: str

    @property
    def short(self) -> str:
        return hashlib.sha256(f"{self.workspace_id}/{self.target.project}/{self.name}".encode()).hexdigest()[:8]

    @property
    def service(self) -> str:
        """Cloud Run service name: the app name plus a short hash, unique across workspaces sharing a project."""
        return f"{self.name}-{self.short[:6]}"

    @property
    def runtime_sa(self) -> str:
        if self.target.shared_runtime_sa:
            return self.target.shared_runtime_sa
        return f"app-{self.short}@{self.target.project}.iam.gserviceaccount.com"


# IAP applies a new grant to its access checks only after a few minutes (up to ~7 observed). Until then
# the app's URL answers "You don't have access" even to its owner. Nothing can confirm propagation for
# the person themselves, so results say so while a grant is that fresh.
IAP_PROPAGATION_S = 10 * 60
ACCESS_NOTE = ("Access can take a few minutes to apply. If you see \"You don't have access\", "
               "wait a minute and retry in a fresh tab.")


@dataclass
class Deployed:
    url: str
    revision: str
    image: str
    extra: dict[str, Any] = field(default_factory=dict)


class DeployError(Exception):
    """Typed cloud error. Codes: unavailable, timeout, rate_limited (retryable); invalid_argument,
    not_found, permission_denied, conflict, build_failed, cancelled, estimator_unavailable, gcp_error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


ShouldStop = Callable[[], bool]


class Deployer(Protocol):
    name: str

    def create_upload(self, workspace_id: str, content_type: str) -> dict[str, Any]:
        """Signed PUT URL for a source tarball: {upload_id, method, url, headers, max_bytes, expires_at}."""

    def stage_source(self, app: AppRef, source: dict[str, Any], op_id: str) -> dict[str, Any]:
        """Turn an API Source into something the builder can read ({gcs}, {git} or {image})."""

    def build(self, app: AppRef, staged: dict[str, Any], op_id: str, should_stop: ShouldStop,
              on_build: Callable[[str], None], existing_build_id: str | None = None) -> str:
        """Build and push an image; return its reference. Blocks until done. With
        existing_build_id (a retry), wait for that build instead of starting another."""

    def route_traffic(self, app: AppRef, revision: str) -> None:
        """Send 100% of traffic to an existing revision (rollback)."""

    def download_upload(self, workspace_id: str, upload_id: str, dest: Any) -> Any:
        """Fetch an uploaded source tarball to a local path (for the estimator)."""

    def deploy(self, app: AppRef, image: str, caps: dict[str, Any], env: dict[str, str]) -> Deployed:
        """Create or update the Cloud Run service (private, IAP on) and wait until it's ready."""

    def set_access(self, app: AppRef, principals: list[str]) -> None:
        """Make the IAP accessor list exactly `principals` (user:/group:/domain:)."""

    def update_caps(self, app: AppRef, caps: dict[str, Any]) -> None: ...

    def status(self, app: AppRef) -> dict[str, Any]: ...

    def soft_delete(self, app: AppRef) -> None:
        """Make the app unreachable but keep everything needed to restore it."""

    def restore(self, app: AppRef, principals: list[str]) -> None: ...

    def purge(self, app: AppRef) -> None:
        """Delete the service, its runtime SA and its images."""

    def verify_link(self, project_id: str, deployer_sa: str) -> list[str]:
        """Check a linked project as its deployer SA; returns what's missing (empty = ready)."""

    def ensure_link_deployer(self, workspace_id: str, deployer_sa: str) -> None:
        """Create the workspace's deployer SA (the one link-project.sh grants roles to) if it's missing,
        and let the API and worker impersonate it. Idempotent."""


def normalize_caps(caps: dict[str, Any]) -> dict[str, Any]:
    """Apply Cloud Run's combination rules to ShipCube caps."""
    out = dict(caps)
    mem = str(out.get("memory", "512Mi"))
    mib = int(mem[:-2]) * (1024 if mem.endswith("Gi") else 1)
    if mib < MIN_MEMORY_MIB:
        out["memory"] = f"{MIN_MEMORY_MIB}Mi"
    if float(out.get("cpu", "1")) < 1:
        out["concurrency"] = 1
    return out
