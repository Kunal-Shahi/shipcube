"""Build isolation through the local deployer: the local half of infra/tests/build-isolation.sh.

Workspace A can't build from B's upload, reference B's objects, or deploy B's image (or a tag, or
the old shared repo) through the deployer. Real IAM, where A's build SA can't read B's object or
pull B's image, stays in the GCP test (it needs GCP). Needs no database.
"""

from __future__ import annotations

import os
import uuid

import pytest

os.environ.setdefault("SHIPCUBE_DEV_MODE", "1")  # mock deployer; never set in production
isolation = pytest.importorskip("shipcube_api.deploy.isolation", reason="needs the C-1 deploy code (PR #26)")


from shipcube_api.deploy.base import AppRef, DeployError, Target
from shipcube_api.deploy.mock import MockDeployer
from shipcube_api.settings import settings

A, B = str(uuid.uuid4()), str(uuid.uuid4())
HOSTED = Target(id="shipcube", kind="shipcube", project=settings.apps_project)


def _app(ws: str) -> AppRef:
    return AppRef(name="hello", workspace_id=ws, target=HOSTED, region=settings.region)


def _image(ws: str, ref: str) -> str:
    return isolation.image_prefix(settings, ws) + "hello" + ref


DIGEST = "@sha256:" + "0" * 64


def test_a_builds_from_its_own_upload_only():
    d = MockDeployer(speed=0)
    up_a, up_b = d.create_upload(A, "application/gzip"), d.create_upload(B, "application/gzip")
    assert f"/ws/{A}/uploads/" in up_a["url"] and f"/ws/{B}/uploads/" in up_b["url"]
    d.stage_source(_app(A), {"kind": "upload", "upload_id": up_a["upload_id"]}, "op_1")
    with pytest.raises(DeployError):  # B's id resolves under A's prefix, where it doesn't exist
        d.stage_source(_app(A), {"kind": "upload", "upload_id": up_b["upload_id"]}, "op_2")
    for bad in ("../" + up_b["upload_id"], f"upl_x/../../{B}/uploads/{up_b['upload_id']}"):
        with pytest.raises(DeployError):
            d.stage_source(_app(A), {"kind": "upload", "upload_id": bad}, "op_3")


def test_a_cannot_reference_b_objects():
    b_obj = isolation.upload_object(settings, B, "upl_0123456789ab")
    with pytest.raises(DeployError):
        isolation.check_object(settings, A, settings.upload_bucket, b_obj)
    with pytest.raises(DeployError):
        isolation.check_object(settings, A, settings.upload_bucket, f"ws/{A}/../{B}/uploads/upl_0123456789ab.tgz")
    isolation.check_object(settings, A, settings.upload_bucket, isolation.upload_object(settings, A, "upl_0123456789ab"))


def test_a_cannot_deploy_b_image():
    d = MockDeployer(speed=0)
    d.stage_source(_app(A), {"kind": "image", "uri": _image(A, DIGEST)}, "op_4")
    with pytest.raises(DeployError, match="not in this workspace"):
        d.stage_source(_app(A), {"kind": "image", "uri": _image(B, DIGEST)}, "op_5")
    with pytest.raises(DeployError, match="digest"):  # a tag could be re-pushed after review
        d.stage_source(_app(A), {"kind": "image", "uri": _image(A, ":latest")}, "op_6")
    legacy = f"{settings.region}-docker.pkg.dev/{settings.apps_project}/shipcube/hello{DIGEST}"
    with pytest.raises(DeployError):  # the old shared repo is nobody's workspace repo
        d.stage_source(_app(A), {"kind": "image", "uri": legacy}, "op_7")


def test_build_pushes_into_own_repo():
    d = MockDeployer(speed=0)
    image = d.build(_app(A), {"mock": "upload"}, "op_8", lambda: False, lambda _id: None)
    assert image.startswith(isolation.image_prefix(settings, A)) and isolation.DIGEST.search(image)
