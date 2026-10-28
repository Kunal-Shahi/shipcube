import re
from pathlib import Path

import pytest
import yaml

from shipcube_api.app import app
from shipcube_api.explain import RuleBasedExplainer, explain

from .conftest import new_workspace, wait_op

SPEC = yaml.safe_load((Path(__file__).parents[1] / "openapi.yaml").read_text())
GIT = {"kind": "git", "url": "https://github.com/example/app"}


@pytest.fixture
def ws():
    return new_workspace()


def deploy(client, ws, name, **body):
    r = client.post(f"/v1/apps/{name}/deployments", json={"source": GIT, **body}, headers=ws["agent"])
    assert r.status_code == 202, r.text
    return r.json()


def _routes(paths: dict) -> set[tuple[str, str]]:
    verbs = {"get", "post", "put", "patch", "delete"}
    return {(m.upper(), re.sub(r"\{[^}]+\}", "{}", p)) for p, ops in paths.items() for m in ops if m in verbs}


def test_routes_match_openapi_contract():
    # /v1/data/* belongs to shipcube-data (shipcube_data.http, documented in its docs/MCP.md) and is mounted
    # only when that package is installed and configured.
    ours = {r for r in _routes(app.openapi()["paths"]) if not r[1].startswith("/v1/data")}
    assert ours == _routes(SPEC["paths"])


def test_requires_token(client):
    assert client.get("/v1/me").status_code == 401
    assert client.get("/v1/me", headers={"Authorization": "Bearer sc_pat_deadbeef_" + "x" * 43}).status_code == 401


def test_me_reflects_the_database(client, ws):
    me = client.get("/v1/me", headers=ws["agent"]).json()
    assert me["email"] == ws["email"] and me["role"] == "owner" and me["workspace"]["id"] == ws["workspace_id"]
    assert me["gcp_targets"][0]["id"] == "shipcube" and "apps:deploy" in me["token"]["scopes"]


def test_scope_enforced(client):
    ro = new_workspace(scopes=["apps:read"])
    r = client.post("/v1/apps/demo-app/deployments", json={"source": GIT}, headers=ro["agent"])
    assert r.status_code == 403 and r.json()["error"]["code"] == "permission_denied"


def test_deploy_runs_on_the_durable_worker_and_is_idempotent(client, ws):
    h = {**ws["agent"], "Idempotency-Key": "k1"}
    a = client.post("/v1/apps/demo-app/deployments", json={"source": GIT}, headers=h)
    b = client.post("/v1/apps/demo-app/deployments", json={"source": GIT}, headers=h)
    assert a.status_code == b.status_code == 202 and a.json()["id"] == b.json()["id"]
    assert a.headers["Location"] == f"/v1/operations/{a.json()['id']}"
    other = client.post("/v1/apps/demo-app/deployments", json={"source": {**GIT, "url": "https://github.com/x/y"}}, headers=h)
    assert other.status_code == 409
    op = wait_op(client, ws["agent"], a.json()["id"])
    assert op["status"] == "succeeded", op
    assert [s["status"] for s in op["steps"]] == ["succeeded"] * 6
    assert set(op["result"]["timings_s"]) == {"validate", "build", "push", "deploy", "configure_access", "health_check"}
    app_ = client.get("/v1/apps/demo-app", headers=ws["agent"]).json()
    assert app_["status"] == "running" and app_["url"] and app_["revision"]
    assert app_["access"][0]["principal"] == f"user:{ws['email']}" and app_["access"][0]["sync_state"] == "applied"


def test_redeploy_creates_a_revision_and_rollback_returns_to_the_previous_one(client, ws):
    first = wait_op(client, ws["agent"], deploy(client, ws, "roll-app")["id"])
    second = wait_op(client, ws["agent"], deploy(client, ws, "roll-app")["id"])
    assert first["result"]["revision"] != second["result"]["revision"]
    revs = client.get("/v1/apps/roll-app", headers=ws["agent"]).json()["revisions"]
    assert [r["revision"] for r in revs[:2]] == [second["result"]["revision"], first["result"]["revision"]]
    rb = client.post("/v1/apps/roll-app/rollbacks", json={"to": "previous"}, headers=ws["agent"])
    assert rb.status_code == 202
    done = wait_op(client, ws["agent"], rb.json()["id"])
    assert done["status"] == "succeeded" and done["result"]["revision"] == first["result"]["revision"]
    app_ = client.get("/v1/apps/roll-app", headers=ws["agent"]).json()
    assert app_["revision"] == first["result"]["revision"]
    assert {r["revision"]: r["status"] for r in app_["revisions"]}[second["result"]["revision"]] == "rolled_back"


def test_rollback_needs_an_earlier_revision(client, ws):
    wait_op(client, ws["agent"], deploy(client, ws, "one-rev")["id"])
    r = client.post("/v1/apps/one-rev/rollbacks", json={}, headers=ws["agent"])
    assert r.status_code == 409


@pytest.mark.parametrize("name", ["Bad_Name", "a", "-x"])
def test_app_name_validated(client, ws, name):
    r = client.get(f"/v1/apps/{name}", headers=ws["agent"])
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_argument"


def test_inline_source_fits_demo_app_and_rejects_oversize(client, ws):
    demo = {"kind": "inline", "files": [{"path": f"src/f{i}.py", "content": "x" * 50_000} for i in range(20)]}
    assert client.post("/v1/estimates", json={"source": demo}, headers=ws["agent"]).status_code == 202
    big = {"kind": "inline", "files": [{"path": f"f{i}", "content": "x" * 300_000} for i in range(8)]}
    r = client.post("/v1/estimates", json={"source": big}, headers=ws["agent"])
    assert r.status_code == 400 and "create_upload" in r.json()["error"]["message"]


def test_workspace_limits_are_enforced(client):
    w = new_workspace()
    from shipcube_api import db

    with db.tx() as conn:
        conn.execute("update caps set limit_value = 1 where workspace_id = %s and app_id is null and kind = 'max_apps'",
                     (w["workspace_id"],))
    deploy(client, w, "only-app")
    r = client.post("/v1/apps/second-app/deployments", json={"source": GIT}, headers=w["agent"])
    assert r.status_code == 429 and r.json()["error"]["code"] == "resource_exhausted"


def test_workspaces_are_isolated(client, ws):
    wait_op(client, ws["agent"], deploy(client, ws, "private-app")["id"])
    other = new_workspace()
    assert client.get("/v1/apps/private-app", headers=other["agent"]).status_code == 404
    assert all(a["name"] != "private-app" for a in client.get("/v1/apps", headers=other["agent"]).json()["apps"])


def test_first_linked_deploy_needs_approval_then_flows(client, ws):
    from shipcube_api import admin

    admin.main(["link", "--workspace", ws["slug"], "--project", "acme-prod", "--deployer-sa", "ws-x-deployer@c.iam.gserviceaccount.com",
                "--runtime-sa", "shipcube-apps@acme-prod.iam.gserviceaccount.com"])
    target = next(t for t in client.get("/v1/me", headers=ws["agent"]).json()["gcp_targets"] if t["kind"] == "linked")["id"]
    op = deploy(client, ws, "linked-app", target=target)
    assert op["status"] == "awaiting_approval" and op["approval_url"].endswith(op["id"])
    assert op["approval_actions"] == ["first_linked_deploy"]
    assert client.post(f"/v1/operations/{op['id']}/approve", json={}, headers=ws["agent"]).status_code == 403
    assert client.post(f"/v1/operations/{op['id']}/approve", json={}, headers=ws["human"]).json()["status"] == "queued"
    assert wait_op(client, ws["agent"], op["id"])["status"] == "succeeded"
    again = deploy(client, ws, "linked-app", target=target)
    assert again["status"] == "queued"


def test_cap_raise_needs_approval_reduction_does_not(client, ws):
    wait_op(client, ws["agent"], deploy(client, ws, "caps-app")["id"])
    down = client.patch("/v1/apps/caps-app/caps", json={"max_instances": 1}, headers=ws["agent"]).json()
    assert down["caps"]["max_instances"] == 1 and down["operation"]["status"] == "queued"
    up = client.patch("/v1/apps/caps-app/caps", json={"max_instances": 5, "memory": "1Gi"}, headers=ws["agent"]).json()
    assert up["operation"]["status"] == "awaiting_approval" and up["caps"]["max_instances"] == 1
    assert up["operation"]["approval_actions"] == ["cap_raise"] and len(up["operation"]["approval_reasons"]) == 2
    client.post(f"/v1/operations/{up['operation']['id']}/approve", json={}, headers=ws["human"])
    assert wait_op(client, ws["agent"], up["operation"]["id"])["result"]["caps"]["max_instances"] == 5


def test_domain_grant_needs_approval_user_grant_does_not(client, ws):
    wait_op(client, ws["agent"], deploy(client, ws, "share-app")["id"])
    r = client.patch("/v1/apps/share-app/access", headers=ws["agent"],
                     json={"add": [{"principal": "user:ana@acme.com"}, {"principal": "domain:acme.com"}]}).json()
    principals = [b["principal"] for b in r["access"]]
    assert "user:ana@acme.com" in principals and "domain:acme.com" not in principals
    assert r["operation"]["kind"] == "set_access" and r["operation"]["status"] == "awaiting_approval"
    rejected = client.post(f"/v1/operations/{r['operation']['id']}/reject", json={"comment": "no"}, headers=ws["human"]).json()
    assert rejected["status"] == "cancelled" and rejected["error"]["code"] == "approval_rejected"
    gone = client.patch("/v1/apps/share-app/access", headers=ws["agent"], json={"remove": ["user:ana@acme.com"]}).json()
    assert "user:ana@acme.com" not in [b["principal"] for b in gone["access"]]
    assert client.patch("/v1/apps/share-app/access", headers=ws["agent"],
                        json={"remove": [f"user:{ws['email']}"]}).status_code == 409


def test_delete_is_approved_soft_delete_and_restorable(client, ws):
    wait_op(client, ws["agent"], deploy(client, ws, "temp-app")["id"])
    op = client.delete("/v1/apps/temp-app", headers=ws["agent"]).json()
    assert op["status"] == "awaiting_approval" and op["approval_actions"] == ["delete_app"]
    client.post(f"/v1/operations/{op['id']}/approve", json={}, headers=ws["human"])
    done = wait_op(client, ws["agent"], op["id"])
    assert done["result"]["deleted"] and done["result"]["restorable_until"]
    assert client.get("/v1/apps/temp-app/logs", headers=ws["agent"]).status_code == 404
    assert client.post("/v1/apps/temp-app/restore", headers=ws["agent"]).status_code == 403
    restore = client.post("/v1/apps/temp-app/restore", headers=ws["human"])
    assert restore.status_code == 202
    assert wait_op(client, ws["agent"], restore.json()["id"])["status"] == "succeeded"
    assert client.get("/v1/apps/temp-app", headers=ws["agent"]).json()["status"] == "running"


def test_estimate_explained_by_muse_with_rule_fallback(monkeypatch):
    numbers = {"workload_class": "bursty_interactive", "confidence": 0.87, "tier": "T1",
               "resources": {"cpu": "1", "memory": "512Mi", "concurrency": 40, "min_instances": 0, "max_instances": 2},
               "monthly_cost_usd": {"low": 0.0, "expected": 1.8, "high": 6.5},
               "profile": {"peak_rss_mb": 210, "cpu_seconds": 3.4, "wall_seconds": 5.1}}
    assert explain(numbers).explained_by == "muse-spark"
    monkeypatch.setenv("SHIPCUBE_MUSE_UNAVAILABLE", "1")
    fallback = explain(numbers)
    assert fallback.explained_by == "rules" and "bursty_interactive" in fallback.reasoning
    assert RuleBasedExplainer().explain(numbers).recommended_caps["max_instances"] == 2


def test_request_ids_are_returned(client, ws):
    r = client.get("/v1/me", headers={**ws["agent"], "X-Request-Id": "req-123"})
    assert r.headers["X-Request-Id"] == "req-123"


def test_row_level_security_hides_other_workspaces_even_without_a_where_clause(client, ws):
    from shipcube_api import auth, db

    wait_op(client, ws["agent"], deploy(client, ws, "rls-app")["id"])
    other = new_workspace()
    caller = auth.verify(other["agent"]["Authorization"].split()[1])
    with db.tx(caller) as conn:
        assert conn.execute("select current_user").fetchone()["current_user"] == "shipcube_app"
        slugs = {r["slug"] for r in conn.execute("select slug from apps").fetchall()}
        grants = conn.execute("select count(*) as n from access_grants").fetchone()["n"]
    assert "rls-app" not in slugs and grants == 0
