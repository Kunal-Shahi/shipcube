"""IAP applies a new grant only after a few minutes; until then the owner sees "You don't have access".
Results that hand out a URL say so while a grant is that fresh (deploy/base.ACCESS_NOTE)."""

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest

from shipcube_api import db, store, store_runtime
from shipcube_api.deploy.base import ACCESS_NOTE

from .conftest import new_workspace, wait_op

GIT = {"kind": "git", "url": "https://github.com/example/app"}


@pytest.fixture
def iap(monkeypatch, client):
    """The running deployer behaves like GCP's: access changes take minutes to apply."""
    monkeypatch.setattr(store_runtime.deployer(), "access_propagates", True, raising=False)


def _deploy(client, ws, name):
    r = client.post(f"/v1/apps/{name}/deployments", json={"source": GIT}, headers=ws["agent"])
    return wait_op(client, ws["agent"], r.json()["id"])


def test_deploy_and_access_results_carry_the_note_on_iap(client, iap):
    ws = new_workspace()
    op = _deploy(client, ws, "note-app")
    assert op["status"] == "succeeded" and op["result"]["url"] and op["result"]["access_note"] == ACCESS_NOTE
    r = client.patch("/v1/apps/note-app/access", json={"add": [{"principal": "user:bob@example.com"}]}, headers=ws["agent"])
    assert wait_op(client, ws["agent"], r.json()["operation"]["id"])["result"]["access_note"] == ACCESS_NOTE
    # Only removing access needs no wait to explain.
    r = client.patch("/v1/apps/note-app/access", json={"remove": ["user:bob@example.com"]}, headers=ws["agent"])
    assert "access_note" not in wait_op(client, ws["agent"], r.json()["operation"]["id"])["result"]


def test_no_note_where_access_applies_at_once(client):
    ws = new_workspace()
    op = _deploy(client, ws, "local-app")  # the mock (and local) deployer apply access immediately
    assert op["status"] == "succeeded" and "access_note" not in op["result"]


def test_app_view_shows_the_note_only_while_a_grant_is_fresh(client, monkeypatch):
    ws = new_workspace()
    assert _deploy(client, ws, "view-app")["status"] == "succeeded"
    assert client.get("/v1/apps/view-app", headers=ws["agent"]).json()["access_note"] is None  # mock backend
    monkeypatch.setattr(store, "settings", dataclasses.replace(store.settings, backend="gcp"))
    assert client.get("/v1/apps/view-app", headers=ws["agent"]).json()["access_note"] == ACCESS_NOTE
    with db.tx() as conn:  # the grant was pushed long enough ago that IAP applies it
        conn.execute("update access_grants set last_synced_at = %s where app_id = (select id from apps where"
                     " workspace_id = %s and slug = 'view-app')", (datetime.now(UTC) - timedelta(minutes=11), ws["workspace_id"]))
    assert client.get("/v1/apps/view-app", headers=ws["agent"]).json()["access_note"] is None
