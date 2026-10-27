"""Integration test against real GCP: build -> deploy -> IAP -> redeploy -> rollback -> access ->
soft delete -> restore -> purge. It creates and then deletes one Cloud Run service, its runtime
service account and its images in SHIPCUBE_APPS_PROJECT (about 2 build-minutes).

    SHIPCUBE_IT_GCP=1 SHIPCUBE_BACKEND=gcp uv run pytest tests/integration -s

Needs Application Default Credentials with the roles scripts/gcp_setup.sh sets up.
"""

import os
import time
import uuid

import pytest
import requests
from google.auth import default
from google.auth.transport.requests import AuthorizedSession

from ..conftest import new_workspace, wait_op

pytestmark = pytest.mark.skipif(os.environ.get("SHIPCUBE_IT_GCP") != "1" or os.environ.get("SHIPCUBE_BACKEND") != "gcp",
                                reason="set SHIPCUBE_IT_GCP=1 SHIPCUBE_BACKEND=gcp to run against real GCP")

APP = """
import os
from fastapi import FastAPI, Request
app = FastAPI()

@app.get("/")
def hello(request: Request):
    return {"version": VERSION, "user": request.headers.get("x-goog-authenticated-user-email"), "rev": os.environ.get("K_REVISION")}
"""


def source(version: int) -> dict:
    return {"kind": "inline", "files": [
        {"path": "main.py", "content": APP.replace("VERSION", str(version))},
        {"path": "requirements.txt", "content": "fastapi==0.115.6\nuvicorn==0.34.0\n"},
        {"path": "Procfile", "content": "web: uvicorn main:app --host 0.0.0.0 --port $PORT\n"},
    ]}


def iap_members(sess, project_number, region, service):
    url = (f"https://iap.googleapis.com/v1/projects/{project_number}/iap_web/cloud_run-{region}/services/{service}:getIamPolicy")
    policy = sess.post(url, json={}).json()
    return next((b["members"] for b in policy.get("bindings", []) if b["role"] == "roles/iap.httpsResourceAccessor"), [])


def test_build_deploy_iap_rollback_access_delete_restore_purge(client):
    from shipcube_api import db
    from shipcube_api.store_runtime import deployer

    ws = new_workspace(email=os.environ.get("SHIPCUBE_IT_OWNER", f"it-{uuid.uuid4().hex[:6]}@example.com"))
    name = f"it-{uuid.uuid4().hex[:6]}"
    sess = AuthorizedSession(default(scopes=["https://www.googleapis.com/auth/cloud-platform"])[0])
    timings = {}
    t0 = time.monotonic()
    try:
        first = wait_op(client, ws["agent"], client.post(f"/v1/apps/{name}/deployments", json={"source": source(1)},
                                                         headers=ws["agent"]).json()["id"], timeout=600)
        timings["deploy v1"] = round(time.monotonic() - t0, 1)
        assert first["status"] == "succeeded", first
        app = client.get(f"/v1/apps/{name}", headers=ws["agent"]).json()
        rt = app["runtime"]
        assert rt["ready"] and rt["iap_enabled"] and rt["execution_environment"] == "EXECUTION_ENVIRONMENT_GEN2"
        assert rt["scaling"].get("minInstanceCount", 0) == 0 and rt["runtime_sa"].startswith("app-")
        number = deployer()._project_number(deployer()._session(None), rt["project"])
        assert iap_members(sess, number, app["region"], rt["service"]) == [f"user:{ws['email']}"]
        r = requests.get(app["url"], allow_redirects=False, timeout=30)
        assert r.headers.get("x-goog-iap-generated-response") == "true" or r.status_code in (302, 401, 403), r.status_code

        t1 = time.monotonic()
        second = wait_op(client, ws["agent"], client.post(f"/v1/apps/{name}/deployments", json={"source": source(2)},
                                                          headers=ws["agent"]).json()["id"], timeout=600)
        timings["redeploy v2"] = round(time.monotonic() - t1, 1)
        assert second["status"] == "succeeded" and second["result"]["revision"] != first["result"]["revision"]

        t1 = time.monotonic()
        rb = wait_op(client, ws["agent"], client.post(f"/v1/apps/{name}/rollbacks", json={}, headers=ws["agent"]).json()["id"])
        timings["rollback"] = round(time.monotonic() - t1, 1)
        assert rb["status"] == "succeeded", rb
        traffic = client.get(f"/v1/apps/{name}", headers=ws["agent"]).json()["runtime"]["traffic"]
        assert traffic == [{"revision": first["result"]["revision"], "percent": 100}], traffic

        grantee = os.environ.get("SHIPCUBE_IT_GRANTEE", "it-viewer@example.com")
        t1 = time.monotonic()
        acc = client.patch(f"/v1/apps/{name}/access", json={"add": [{"principal": f"user:{grantee}"}]}, headers=ws["agent"]).json()
        wait_op(client, ws["agent"], acc["operation"]["id"])
        timings["grant access"] = round(time.monotonic() - t1, 1)
        assert sorted(iap_members(sess, number, app["region"], rt["service"])) == sorted([f"user:{ws['email']}", f"user:{grantee}"])

        delete = client.delete(f"/v1/apps/{name}", headers=ws["agent"]).json()
        client.post(f"/v1/operations/{delete['id']}/approve", json={}, headers=ws["human"])
        t1 = time.monotonic()
        assert wait_op(client, ws["agent"], delete["id"])["status"] == "succeeded"
        timings["soft delete"] = round(time.monotonic() - t1, 1)
        assert client.get(f"/v1/apps/{name}", headers=ws["agent"]).json()["runtime"]["ingress"] == "INGRESS_TRAFFIC_INTERNAL_ONLY"
        assert iap_members(sess, number, app["region"], rt["service"]) == []

        t1 = time.monotonic()
        restore = client.post(f"/v1/apps/{name}/restore", headers=ws["human"]).json()
        assert wait_op(client, ws["agent"], restore["id"])["status"] == "succeeded"
        timings["restore"] = round(time.monotonic() - t1, 1)
        assert client.get(f"/v1/apps/{name}", headers=ws["agent"]).json()["runtime"]["ingress"] == "INGRESS_TRAFFIC_ALL"
    finally:
        # Purge: soft delete (if needed) and fast-forward past the 7-day window. Also the cleanup.
        with db.tx() as conn:
            row = conn.execute("select id from apps where workspace_id = %s and slug = %s", (ws["workspace_id"], name)).fetchone()
            if row:
                conn.execute("update apps set status = 'deleted', deleted_at = coalesce(deleted_at, now()),"
                             " purge_after = now() - interval '1 minute' where id = %s", (row["id"],))
        from shipcube_api.store_runtime import _worker

        t1 = time.monotonic()
        _worker.housekeeping()
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            with db.tx() as conn:
                done = conn.execute("select purged_at from apps where id = %s", (row["id"],)).fetchone()["purged_at"] if row else True
            if done:
                break
            time.sleep(2)
        timings["purge"] = round(time.monotonic() - t1, 1)
        print("\nINTEGRATION TIMINGS (s):", timings)
    with db.tx() as conn:
        assert conn.execute("select purged_at from apps where id = %s", (row["id"],)).fetchone()["purged_at"] is not None
    gone = sess.get(f"https://run.googleapis.com/v2/projects/{rt['project']}/locations/{app['region']}/services/{rt['service']}")
    assert gone.status_code == 404, gone.text[:200]
    sa = sess.get(f"https://iam.googleapis.com/v1/projects/{rt['project']}/serviceAccounts/{rt['runtime_sa']}")
    assert sa.status_code == 404, sa.text[:200]
