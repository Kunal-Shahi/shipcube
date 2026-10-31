"""Uploads are deleted once they've been used: after a deploy's build (or its final failure), an hour
after an estimate unless a deploy still needs them, and on purge. Only the built image is kept."""

import uuid

import pytest
from psycopg.types.json import Jsonb

from shipcube_api import db, store_runtime
from shipcube_api.deploy import isolation
from shipcube_api.deploy.base import DeployError
from shipcube_api.settings import settings
from shipcube_api.worker import Worker

from .conftest import new_workspace, wait_op


@pytest.fixture
def ws(client):
    return new_workspace()


def _upload(client, ws) -> tuple[str, str]:
    up = client.post("/v1/uploads", json={}, headers=ws["agent"]).json()
    name = isolation.upload_object(settings, ws["workspace_id"], up["upload_id"])
    assert name in store_runtime.deployer().uploads
    return up["upload_id"], name


def _deploy(client, ws, app, upload_id):
    r = client.post(f"/v1/apps/{app}/deployments", json={"source": {"kind": "upload", "upload_id": upload_id}},
                    headers=ws["agent"])
    assert r.status_code == 202, r.text
    return wait_op(client, ws["agent"], r.json()["id"])


def test_a_successful_deploy_deletes_its_upload_and_keeps_the_image(client, ws):
    upload_id, name = _upload(client, ws)
    op = _deploy(client, ws, "keep-image", upload_id)
    assert op["status"] == "succeeded" and op["result"]["image"]
    assert name not in store_runtime.deployer().uploads
    # Deploying the same upload again explains why it's gone.
    again = _deploy(client, ws, "keep-image", upload_id)
    assert again["status"] == "failed" and "create a new upload" in again["error"]["message"]


def test_a_failed_deploy_deletes_its_upload(client, ws, monkeypatch):
    upload_id, name = _upload(client, ws)

    def broken(*a, **kw):
        raise DeployError("invalid_argument", "the image doesn't start")

    monkeypatch.setattr(store_runtime.deployer(), "deploy", broken)
    op = _deploy(client, ws, "fails", upload_id)
    assert op["status"] == "failed"
    assert name not in store_runtime.deployer().uploads


def _finished_estimate(ws, upload_id: str, minutes_ago: int) -> uuid.UUID:
    op_id = uuid.uuid4()
    with db.worker_tx() as conn:
        conn.execute("insert into operations (id, workspace_id, kind, status, input, target_project, idem_key, request_hash,"
                     " finished_at) values (%s, %s, 'estimate', 'succeeded', %s, %s, %s, '\\x00', now() - make_interval(mins => %s))",
                     (op_id, ws["workspace_id"], Jsonb({"source": {"kind": "upload", "upload_id": upload_id}}),
                      settings.apps_project, f"test-{op_id}", minutes_ago))
    return op_id


def test_an_estimates_upload_waits_for_the_deploy_then_goes(client, ws):
    worker = Worker(store_runtime.deployer(), worker_id="sweep-test")
    fresh_id, fresh = _upload(client, ws)
    _finished_estimate(ws, fresh_id, 10)  # the agent is about to deploy it
    old_id, old = _upload(client, ws)
    _finished_estimate(ws, old_id, 90)    # nobody deployed it within the hour
    worker.sweep_estimate_uploads()
    uploads = store_runtime.deployer().uploads
    assert fresh in uploads and old not in uploads
    # The usual flow still works: the deploy after the estimate finds its upload, and then deletes it.
    assert _deploy(client, ws, "after-estimate", fresh_id)["status"] == "succeeded"
    assert fresh not in store_runtime.deployer().uploads


def test_an_upload_a_pending_deploy_names_is_kept(client, ws):
    worker = Worker(store_runtime.deployer(), worker_id="sweep-test-2")
    upload_id, name = _upload(client, ws)
    _finished_estimate(ws, upload_id, 90)
    with db.worker_tx() as conn:  # a deploy waiting for approval still needs it
        op_id = uuid.uuid4()
        conn.execute("insert into operations (id, workspace_id, kind, status, input, target_project, idem_key, request_hash)"
                     " values (%s, %s, 'deploy', 'awaiting_approval', %s, %s, %s, '\\x00')",
                     (op_id, ws["workspace_id"], Jsonb({"source": {"kind": "upload", "upload_id": upload_id}}),
                      settings.apps_project, f"test-{op_id}"))
    worker.sweep_estimate_uploads()
    assert name in store_runtime.deployer().uploads


def test_restore_still_works_and_purge_removes_leftovers(client, ws):
    upload_id, name = _upload(client, ws)
    assert _deploy(client, ws, "restorable", upload_id)["status"] == "succeeded"
    op = client.delete("/v1/apps/restorable", headers=ws["agent"]).json()
    client.post(f"/v1/operations/{op['id']}/approve", json={}, headers=ws["human"])
    assert wait_op(client, ws["agent"], op["id"])["status"] == "succeeded"
    restore = client.post("/v1/apps/restorable/restore", headers=ws["human"])
    assert wait_op(client, ws["agent"], restore.json()["id"])["status"] == "succeeded"  # needs no source
    assert client.get("/v1/apps/restorable", headers=ws["agent"]).json()["status"] == "running"

    # A source left behind (say, the delete failed earlier) goes with the app at purge.
    store_runtime.deployer().uploads.add(name)
    with db.worker_tx() as conn:
        conn.execute("update apps set deleted_at = now(), purge_after = now() - interval '1 minute'"
                     " where workspace_id = %s and slug = 'restorable'", (ws["workspace_id"],))
        app_id = conn.execute("select id from apps where workspace_id = %s and slug = 'restorable'",
                              (ws["workspace_id"],)).fetchone()["id"]
    Worker(store_runtime.deployer(), worker_id="purge-test").housekeeping()  # enqueues the purge
    with db.worker_tx() as conn:
        purge = conn.execute("select id from operations where app_id = %s and kind = 'purge'", (app_id,)).fetchone()
    assert wait_op(client, ws["agent"], str(purge["id"]))["status"] == "succeeded"
    assert name not in store_runtime.deployer().uploads


def test_objects_outside_the_workspace_are_never_deleted(client, ws):
    other = new_workspace()
    _, theirs = _upload(client, other)
    with pytest.raises(DeployError):
        store_runtime.deployer().delete_objects(ws["workspace_id"], [theirs])
    assert theirs in store_runtime.deployer().uploads
