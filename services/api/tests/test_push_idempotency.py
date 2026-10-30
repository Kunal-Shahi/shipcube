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


def test_the_same_key_with_different_removals_alongside_a_domain_grant_is_a_conflict(client, ws):
    _running(client, ws, "s4-domain")
    key = {**ws["agent"], "Idempotency-Key": "share-acme"}
    first = client.patch("/v1/apps/s4-domain/access", headers=key,
                         json={"add": [{"principal": "domain:acme.com"}], "remove": ["user:bob@example.com"]})
    assert first.status_code == 200, first.text
    other = client.patch("/v1/apps/s4-domain/access", headers=key,
                         json={"add": [{"principal": "domain:acme.com"}], "remove": ["user:eve@example.com"]})
    assert other.status_code == 409, "a different change must not replay the first request"
    same = client.patch("/v1/apps/s4-domain/access", headers=key,
                        json={"remove": ["user:bob@example.com"], "add": [{"principal": "domain:acme.com"}]})
    assert same.status_code == 200 and same.json()["operation"]["id"] == first.json()["operation"]["id"]


def test_changes_to_one_app_wait_for_each_other_and_are_ordered_by_when_they_ran(client, ws):
    import threading
    import time

    _running(client, ws, "s4-lock")
    with db.tx() as conn:
        app_id = conn.execute("select id from apps where workspace_id = %s and slug = 's4-lock'",
                              (ws["workspace_id"],)).fetchone()["id"]
    done = {}

    def second():
        r = client.patch("/v1/apps/s4-lock/access", json={"add": [{"principal": "user:dan@example.com"}]},
                         headers={**ws["agent"], "Idempotency-Key": "add-dan"})
        done["op"] = r.json()["operation"]["id"]

    with db.worker_tx() as conn:  # an overlapping change that holds the app
        conn.execute("select 1 from apps where id = %s for update", (app_id,))
        t = threading.Thread(target=second)
        t.start()
        time.sleep(0.5)
        assert t.is_alive(), "set_access must wait for the app row"
        first = conn.execute("insert into operations (id, workspace_id, app_id, kind, target_project, status, idem_key,"
                             " request_hash, input, created_at) values (gen_random_uuid(), %s, %s, 'set_access', 'p',"
                             " 'cancelled', 'held', '\\x00', '{}', clock_timestamp()) returning created_at",
                             (ws["workspace_id"], app_id)).fetchone()["created_at"]
    t.join(10)
    with db.tx() as conn:
        later = conn.execute("select created_at from operations where id = %s", (done["op"],)).fetchone()["created_at"]
    assert later > first  # the waiting request's operation sorts after the one it waited for
