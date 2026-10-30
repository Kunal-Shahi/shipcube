"""#18: removals made in the same request as a domain: grant reach IAP at once, in their own
operation; the domain grant alone waits for approval, and rejecting it keeps the removals."""

import pytest

from shipcube_api import db, store_runtime

from .conftest import new_workspace, wait_op

GIT = {"kind": "git", "url": "https://github.com/example/app"}
BOB = "user:bob@example.com"


@pytest.fixture
def ws():
    return new_workspace()


def _running(client, ws, name):
    r = client.post(f"/v1/apps/{name}/deployments", json={"source": GIT}, headers=ws["agent"])
    assert wait_op(client, ws["agent"], r.json()["id"])["status"] == "succeeded"
    with db.tx() as conn:
        return conn.execute("select service_name from apps where workspace_id = %s and slug = %s",
                            (ws["workspace_id"], name)).fetchone()["service_name"]


def test_a_removal_with_a_domain_grant_is_pushed_without_waiting_for_approval(client, ws):
    service = _running(client, ws, "s3-app")
    pushed = store_runtime.deployer().access
    r = client.patch("/v1/apps/s3-app/access", json={"add": [{"principal": BOB}]}, headers=ws["agent"])
    wait_op(client, ws["agent"], r.json()["operation"]["id"])
    assert BOB in pushed[service]

    r = client.patch("/v1/apps/s3-app/access", headers={**ws["agent"], "Idempotency-Key": "rm-bob-add-acme"},
                     json={"remove": [BOB], "add": [{"principal": "domain:acme.com"}]})
    assert r.status_code == 200, r.text
    gated, applied = r.json()["operation"], r.json()["applied"]
    assert gated["status"] == "awaiting_approval" and applied and applied["id"] != gated["id"]
    assert wait_op(client, ws["agent"], applied["id"])["status"] == "succeeded"
    assert BOB not in pushed[service] and "domain:acme.com" not in pushed[service]

    # A retry returns both operations and creates nothing new.
    again = client.patch("/v1/apps/s3-app/access", headers={**ws["agent"], "Idempotency-Key": "rm-bob-add-acme"},
                         json={"remove": [BOB], "add": [{"principal": "domain:acme.com"}]}).json()
    assert (again["operation"]["id"], again["applied"]["id"]) == (gated["id"], applied["id"])

    # Rejecting the domain grant leaves the removal in place, in the database and in IAP.
    assert client.post(f"/v1/operations/{gated['id']}/reject", json={}, headers=ws["human"]).status_code == 200
    assert BOB not in pushed[service]
    access = [g["principal"] for g in client.get("/v1/apps/s3-app", headers=ws["agent"]).json()["access"]]
    assert BOB not in access and "domain:acme.com" not in access


def test_a_domain_grant_alone_still_waits_and_applies_on_approval(client, ws):
    service = _running(client, ws, "s3-domain")
    r = client.patch("/v1/apps/s3-domain/access", json={"add": [{"principal": "domain:acme.com"}]}, headers=ws["agent"])
    body = r.json()
    assert body["operation"]["status"] == "awaiting_approval" and body["applied"] is None
    assert client.post(f"/v1/operations/{body['operation']['id']}/approve", json={}, headers=ws["human"]).status_code == 200
    assert wait_op(client, ws["agent"], body["operation"]["id"])["status"] == "succeeded"
    assert "domain:acme.com" in store_runtime.deployer().access[service]
