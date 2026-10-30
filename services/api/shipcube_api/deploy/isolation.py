"""Per-workspace build isolation: every name a build reads or writes is derived from the caller's
workspace (the token's, never the request's), and anything outside it is refused.

- uploads and inline sources live under  gs://<uploads bucket>/ws/<workspace uuid>/
- each workspace builds as its own builder SA, which can only read that prefix
- each workspace pushes to its own Artifact Registry repo, and deploys pin the digest

Names come from Settings templates ({ws} = workspace uuid, {ws_short} = its first 20 hex digits,
{apps_project}, {region}), so infra can rename without a code change.
"""

from __future__ import annotations

import os
import re
import uuid
from typing import Any

from ..settings import Settings
from .base import DeployError

LOCAL_REGISTRY = "shipcube.local"  # the local backend's image namespace (deploy/local.py)
UPLOAD_ID = re.compile(r"^upl_[0-9a-f]{8,32}$")
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")


def ws_short(workspace_id: str) -> str:
    return uuid.UUID(str(workspace_id)).hex[:20]


def _fill(template: str, s: Settings, workspace_id: str) -> str:
    return template.format(ws=uuid.UUID(str(workspace_id)), ws_short=ws_short(workspace_id),
                           ws12=uuid.UUID(str(workspace_id)).hex[:12], apps_project=s.apps_project,
                           control_project=s.control_project, region=s.region)


def link_deployer_sa(s: Settings, workspace_id: str) -> str:
    return _fill(s.link_deployer_sa_template, s, workspace_id)


def builder_sa(s: Settings, workspace_id: str) -> str:
    return _fill(s.builder_sa_template, s, workspace_id)


def ar_repo(s: Settings, workspace_id: str) -> str:
    return _fill(s.ar_repo_template, s, workspace_id)


def image_prefix(s: Settings, workspace_id: str) -> str:
    return f"{s.region}-docker.pkg.dev/{s.apps_project}/{ar_repo(s, workspace_id)}/"


def upload_prefix(s: Settings, workspace_id: str) -> str:
    return _fill(s.upload_prefix_template, s, workspace_id)


def build_logs_folder(s: Settings, workspace_id: str) -> str:
    """Where the workspace's hosted builds write their logs (GCS_ONLY): a managed folder inside its
    uploads folder that only its builder can write. Builds never get logging.logWriter (red-team #28)."""
    return f"{upload_prefix(s, workspace_id)}build-logs/"


def upload_object(s: Settings, workspace_id: str, upload_id: str) -> str:
    if not isinstance(upload_id, str) or not UPLOAD_ID.match(upload_id):
        raise DeployError("invalid_argument", "upload_id must be the id create_upload returned (upl_<hex>)")
    return f"{upload_prefix(s, workspace_id)}uploads/{upload_id}.tgz"


def inline_object(s: Settings, workspace_id: str, op_id: str) -> str:
    return f"{upload_prefix(s, workspace_id)}inline/{op_id}.tgz"


def check_object(s: Settings, workspace_id: str, bucket: str, name: str) -> None:
    if bucket != s.upload_bucket or not name.startswith(upload_prefix(s, workspace_id)) or "/../" in f"/{name}/":
        raise DeployError("permission_denied", f"gs://{bucket}/{name} is outside this workspace's uploads")


# Images a hosted app may run besides its own workspace's builds: public samples, pinned by
# digest. SHIPCUBE_IMAGE_ALLOWLIST (comma-separated repository prefixes) replaces the default.
DEFAULT_IMAGE_ALLOWLIST = ("us-docker.pkg.dev/cloudrun/container/",)


def image_allowlist() -> tuple[str, ...]:
    raw = os.environ.get("SHIPCUBE_IMAGE_ALLOWLIST")
    if raw is None:
        return DEFAULT_IMAGE_ALLOWLIST
    return tuple(p.strip().lower().rstrip("/") + "/" for p in raw.split(",") if p.strip())


def check_image(s: Settings, workspace_id: str, image: str, *, linked: bool = False) -> None:
    """Which container images an app may run (red-team A-1, #21).

    - ShipCube's own registries (local, or the apps/control projects): only the caller's
      workspace repo, pinned by digest. Never another workspace's or the shared repo.
    - Hosted apps (ShipCube's project and credit): nothing else, except an image under
      image_allowlist(), pinned by digest. Arbitrary public images (a miner, say) are refused.
    - Linked apps run in the user's own project, on their bill: any image outside ShipCube's
      registries is theirs to choose, as before.
    """
    image = image.strip()
    if "/../" in f"/{image}/" or any(c.isspace() for c in image):
        raise DeployError("invalid_argument", f"image {image!r} is malformed")
    host, _, path = image.lower().partition("/")
    if host == LOCAL_REGISTRY:
        prefix = f"{LOCAL_REGISTRY}/{ar_repo(s, workspace_id)}/"
        if not image.startswith(prefix):
            raise DeployError("permission_denied", f"image {image} is not in this workspace's namespace ({prefix.rstrip('/')})")
        if not DIGEST.search(image):
            raise DeployError("invalid_argument", f"image {image} must be pinned by image id (@sha256:...)")
        return
    project = path.split("/", 1)[0]
    registry = host.split(":")[0]
    ours = registry.endswith("docker.pkg.dev") or registry == "gcr.io" or registry.endswith(".gcr.io")
    if ours and project in (s.apps_project, s.control_project):
        if not image.startswith(image_prefix(s, workspace_id)):
            raise DeployError("permission_denied", f"image {image} is not in this workspace's registry "
                                                   f"({image_prefix(s, workspace_id).rstrip('/')})")
        if not DIGEST.search(image):
            raise DeployError("invalid_argument", f"image {image} must be pinned by digest (@sha256:...)")
        return
    if linked:
        return
    if not any(image.lower().startswith(p) for p in image_allowlist()):
        raise DeployError("permission_denied",
                          f"hosted apps run only images ShipCube built for this workspace "
                          f"({image_prefix(s, workspace_id).rstrip('/')}) or allow-listed images; deploy from source, "
                          f"or use a linked project for other images")
    if not DIGEST.search(image):
        raise DeployError("invalid_argument", f"allow-listed image {image} must be pinned by digest (@sha256:...)")


def check_source(s: Settings, workspace_id: str, source: dict[str, Any], *, linked: bool = False,
                 estimate: bool = False) -> None:
    """Request-time checks; stage_source repeats them before anything is read. Estimates never
    pull an image (they're static for image sources), so only deploys check the image."""
    if source.get("kind") == "upload":
        upload_object(s, workspace_id, source.get("upload_id"))
    elif source.get("kind") == "image" and not estimate:
        check_image(s, workspace_id, source.get("uri", ""), linked=linked)
