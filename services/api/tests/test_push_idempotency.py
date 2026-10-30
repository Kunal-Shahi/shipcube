"""#19: a reused idempotency key must never hide a change. Derived keys (the MCP derives them from the
arguments) repeat whenever the same change is asked for again, e.g. remove, add, remove; each of
those must still reach IAP and Cloud Run, while a genuine retry still returns its operation."""

import pytest

from shipcube_api import db, store_runtime

from .conftest import new_workspace, wait_op

GIT = {"kind": "git", "url": "https://github.com/example/app"}


@pytest.fixture
def ws():
    return new_workspace()


def _running(client, ws, name):
    r = client.post(f"/v1/apps/{name}/deployments", json={"source": GIT}, headers=ws["agent"])
    assert wait_op(client, ws["agent"], r.json()["id"])["status"] == "succeeded"
    with db.tx() as conn:
        return conn.execute("select service_name from apps where workspace_id = %s and slug = %s",
                            (ws["workspace_id"], name)).fetchone()["service_name"]


def _access(client, ws, name, body, key):
    r = client.patch(f"/v1/apps/{name}/access", json=body, headers={**ws["agent"], "Idempotency-Key": key})
    assert r.status_code == 200, r.text
    op = r.json()["operation"]
    assert wait_op(client, ws["agent"], op["id"])["status"] == "succeeded"
    return op["id"]


def test_remove_add_remove_with_repeated_keys_reaches_iap_every_time(client, ws):
    service = _running(client, ws, "s4-access")
    pushed = store_runtime.deployer().access
    bob = "user:bob@example.com"
    _access(client, ws, "s4-access", {"add": [{"principal": bob}]}, "add-bob")
    assert bob in pushed[service]

    first = _access(client, ws, "s4-access", {"remove": [bob]}, "rm-bob")
    assert bob not in pushed[service]
    _access(client, ws, "s4-access", {"add": [{"principal": bob}]}, "add-bob")  # same key as before
    assert bob in pushed[service]
    again = _access(client, ws, "s4-access", {"remove": [bob]}, "rm-bob")      # same key as `first`
    assert again != first, "a stale operation was returned instead of applying the change"
    assert bob not in pushed[service]
    access = client.get("/v1/apps/s4-access", headers=ws["agent"]).json()["access"]
    assert bob not in [g["principal"] for g in access]


def test_a_genuine_retry_returns_its_operation_and_writes_nothing(client, ws):
    _running(client, ws, "s4-retry")
    body, key = {"add": [{"principal": "user:carol@example.com"}]}, "add-carol"
    first = _access(client, ws, "s4-retry", body, key)
    assert _access(client, ws, "s4-retry", body, key) == first
    with db.tx() as conn:
        n = conn.execute("select count(*) as n from operations where workspace_id = %s and kind = 'set_access'",
                         (ws["workspace_id"],)).fetchone()["n"]
    assert n == 1
    # The same key for a different change is still a conflict.
    r = client.patch("/v1/apps/s4-retry/access", json={"remove": ["user:carol@example.com"]},
                     headers={**ws["agent"], "Idempotency-Key": key})
    assert r.status_code == 409


def _caps(client, ws, name, n, key):
    r = client.patch(f"/v1/apps/{name}/caps", json={"max_instances": n}, headers={**ws["agent"], "Idempotency-Key": key})
    assert r.status_code == 200, r.text
    op = r.json()["operation"]
    assert wait_op(client, ws["agent"], op["id"])["status"] == "succeeded"
    return op["id"]


def test_cap_reductions_with_repeated_keys_are_applied(client, ws):
    _running(client, ws, "s4-caps")
    ops = [_caps(client, ws, "s4-caps", n, f"caps-{n}") for n in (2, 1, 2, 1)]  # keys repeat: caps-2, caps-1
    assert len(set(ops)) == 4
    assert client.get("/v1/apps/s4-caps", headers=ws["agent"]).json()["caps"]["max_instances"] == 1
