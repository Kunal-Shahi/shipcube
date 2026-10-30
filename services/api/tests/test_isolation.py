"""Builds and deploys stay inside the caller's workspace (red-team C-1): uploads, source objects
and image references are derived from the token's workspace, and anything else is refused."""

import pytest

from shipcube_api.deploy import isolation
from shipcube_api.settings import settings

from .conftest import new_workspace, wait_op

DIGEST = "sha256:" + "b" * 64


@pytest.fixture(scope="module")
def two(client):
    return new_workspace(), new_workspace()


def test_upload_urls_are_signed_under_the_callers_prefix(client, two):
    a, _ = two
    r = client.post("/v1/uploads", json={}, headers=a["agent"])
    assert r.status_code == 201, r.text
    assert f"/ws/{a['workspace_id']}/uploads/{r.json()['upload_id']}.tgz" in r.json()["url"]


def test_another_workspaces_upload_id_is_not_found_in_mine(client, two):
    a, b = two
    upload_id = client.post("/v1/uploads", json={}, headers=a["agent"]).json()["upload_id"]
    r = client.post("/v1/apps/steal-app/deployments", json={"source": {"kind": "upload", "upload_id": upload_id}},
                    headers=b["agent"])
    assert r.status_code == 202, r.text
    op = wait_op(client, b["agent"], r.json()["id"])
    assert op["status"] == "failed" and op["error"]["code"] == "invalid_argument" and "not found" in op["error"]["message"]
    # The owner can deploy it.
    r = client.post("/v1/apps/own-app/deployments", json={"source": {"kind": "upload", "upload_id": upload_id}},
                    headers=a["agent"])
    assert wait_op(client, a["agent"], r.json()["id"])["status"] == "succeeded"


@pytest.mark.parametrize("upload_id", ["../x", "upl_../../a", "ws/other/uploads/upl_0123", "upl_ZZZZZZZZ"])
def test_malformed_upload_ids_are_rejected_at_request_time(client, two, upload_id):
    a, _ = two
    for path, body in (("/v1/apps/x-app/deployments", {}), ("/v1/estimates", {})):
        r = client.post(path, json={**body, "source": {"kind": "upload", "upload_id": upload_id}}, headers=a["agent"])
        assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_argument", r.text


def test_another_workspaces_image_is_refused(client, two):
    a, b = two
    victim = f"{isolation.image_prefix(settings, a['workspace_id'])}victim-123456@{DIGEST}"
    shared = f"{settings.region}-docker.pkg.dev/{settings.apps_project}/{settings.repo}/victim-123456:op-1"
    for image in (victim, shared):
        r = client.post("/v1/apps/img-app/deployments", json={"source": {"kind": "image", "uri": image}}, headers=b["agent"])
        assert r.status_code == 403 and r.json()["error"]["code"] == "permission_denied", r.text
    own = f"{isolation.image_prefix(settings, b['workspace_id'])}img-app-123456@{DIGEST}"
    r = client.post("/v1/apps/img-app/deployments", json={"source": {"kind": "image", "uri": own}}, headers=b["agent"])
    assert wait_op(client, b["agent"], r.json()["id"])["status"] == "succeeded"


def test_built_images_are_pinned_in_the_workspace_repo(client, two):
    a, _ = two
    r = client.post("/v1/apps/pin-app/deployments", json={"source": {"kind": "git", "url": "https://github.com/example/app"}},
                    headers=a["agent"])
    op = wait_op(client, a["agent"], r.json()["id"])
    assert op["status"] == "succeeded", op
    image = op["result"]["image"]
    assert image.startswith(isolation.image_prefix(settings, a["workspace_id"])) and isolation.DIGEST.search(image)


def test_git_estimates_are_paused(client, two):
    a, _ = two
    r = client.post("/v1/estimates", json={"source": {"kind": "git", "url": "https://github.com/example/app"}}, headers=a["agent"])
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_argument", r.text
    assert "paused" in r.json()["error"]["message"]
