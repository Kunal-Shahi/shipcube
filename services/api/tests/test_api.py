import re
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from shipcube_api.app import app
from shipcube_api.auth import tokens
from shipcube_api.explain import RuleBasedExplainer, explain
from shipcube_api.store import WORKSPACE_ID, store

client = TestClient(app)
USER = store.me["user_id"]
PAT = tokens.mint(USER, WORKSPACE_ID, ["apps:read", "apps:deploy", "access:write", "caps:write"])
CONSOLE = tokens.mint(USER, WORKSPACE_ID, ["apps:read"], kind="console")
AUTH = {"Authorization": f"Bearer {PAT}"}
HUMAN = {"Authorization": f"Bearer {CONSOLE}"}
SPEC = yaml.safe_load((Path(__file__).parents[1] / "openapi.yaml").read_text())
GIT = {"kind": "git", "url": "https://github.com/example/app"}


def _routes(paths: dict) -> set[tuple[str, str]]:
    verbs = {"get", "post", "put", "patch", "delete"}
    return {(m.upper(), re.sub(r"\{[^}]+\}", "{}", p)) for p, ops in paths.items() for m in ops if m in verbs}


def _wait(op_id: str, timeout: float = 15) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        op = client.get(f"/v1/operations/{op_id}", headers=AUTH).json()
        if op["done"] or time.monotonic() > deadline:
            return op
        time.sleep(0.2)


def test_routes_match_openapi_contract():
    assert _routes(app.openapi()["paths"]) == _routes(SPEC["paths"])


def test_requires_token():
    assert client.get("/v1/me").status_code == 401
    assert client.get("/v1/me", headers={"Authorization": "Bearer sc_pat_deadbeef_" + "x" * 43}).status_code == 401


def test_scope_enforced():
    r = client.put("/v1/apps/regional-revenue/ai", json={"provider": "meta"}, headers=AUTH)
    assert r.status_code == 403 and r.json()["error"]["code"] == "permission_denied"


def test_deploy_idempotency():
    body = {"source": GIT}
    a = client.post("/v1/apps/demo-app/deployments", json=body, headers={**AUTH, "Idempotency-Key": "k1"})
    b = client.post("/v1/apps/demo-app/deployments", json=body, headers={**AUTH, "Idempotency-Key": "k1"})
    assert a.status_code == b.status_code == 202
    assert a.json()["id"] == b.json()["id"] and a.json()["status"] in ("queued", "running")
    assert a.headers["Location"] == f"/v1/operations/{a.json()['id']}"
    other = {"source": {**GIT, "url": "https://github.com/example/other"}}
    assert client.post("/v1/apps/demo-app/deployments", json=other, headers={**AUTH, "Idempotency-Key": "k1"}).status_code == 409


@pytest.mark.parametrize("name", ["Bad_Name", "a", "-x"])
def test_app_name_validated(name):
    r = client.get(f"/v1/apps/{name}", headers=AUTH)
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_argument"


def test_inline_source_fits_demo_app_and_rejects_oversize():
    demo = {"kind": "inline", "files": [{"path": f"src/f{i}.py", "content": "x" * 50_000} for i in range(20)]}  # ~1 MB
    assert client.post("/v1/estimates", json={"source": demo}, headers=AUTH).status_code == 202
    big = {"kind": "inline", "files": [{"path": f"f{i}", "content": "x" * 300_000} for i in range(8)]}  # 2.4 MB
    r = client.post("/v1/estimates", json={"source": big}, headers=AUTH)
    assert r.status_code == 400 and "create_upload" in r.json()["error"]["message"]


def test_first_linked_deploy_needs_approval_then_flows():
    r = client.post("/v1/apps/linked-app/deployments", json={"source": GIT, "target": "linked-1"}, headers=AUTH)
    op = r.json()
    assert op["status"] == "awaiting_approval" and op["approval_url"].endswith(op["id"])
    assert "first deploy to linked GCP project linked-1" in op["approval_reasons"]
    # An agent's PAT cannot approve its own request.
    assert client.post(f"/v1/operations/{op['id']}/approve", json={}, headers=AUTH).status_code == 403
    assert client.post(f"/v1/operations/{op['id']}/approve", json={}, headers=HUMAN).json()["status"] in ("queued", "running")
    assert _wait(op["id"])["status"] == "succeeded"
    # The second deploy to the same linked project goes straight through.
    again = client.post("/v1/apps/linked-app/deployments", json={"source": GIT, "target": "linked-1"}, headers=AUTH)
    assert again.json()["status"] in ("queued", "running")


def test_cap_raise_needs_approval_reduction_does_not():
    down = client.patch("/v1/apps/regional-revenue/caps", json={"max_instances": 1}, headers=AUTH).json()
    assert down["operation"] is None and down["caps"]["max_instances"] == 1
    up = client.patch("/v1/apps/regional-revenue/caps", json={"max_instances": 5, "memory": "1Gi"}, headers=AUTH).json()
    assert up["operation"]["status"] == "awaiting_approval" and up["caps"]["max_instances"] == 1
    assert len(up["operation"]["approval_reasons"]) == 2 and up["operation"]["approval_actions"] == ["cap_raise"]
    client.post(f"/v1/operations/{up['operation']['id']}/approve", json={}, headers=HUMAN)
    assert _wait(up["operation"]["id"])["result"]["caps"]["max_instances"] == 5


def test_domain_grant_needs_approval_user_grant_does_not():
    r = client.patch("/v1/apps/regional-revenue/access", headers=AUTH, json={
        "add": [{"principal": "user:ana@acme.com"}, {"principal": "domain:acme.com"}]}).json()
    principals = [b["principal"] for b in r["access"]]
    assert "user:ana@acme.com" in principals and "domain:acme.com" not in principals
    assert r["operation"]["kind"] == "set_access" and r["operation"]["status"] == "awaiting_approval"
    assert r["operation"]["approval_actions"] == ["domain_grant"] and r["operation"]["approval_expires_at"]
    rejected = client.post(f"/v1/operations/{r['operation']['id']}/reject", json={}, headers=HUMAN).json()
    assert rejected["status"] == "cancelled" and rejected["done"] and rejected["error"]["code"] == "approval_rejected"
    gone = client.patch("/v1/apps/regional-revenue/access", headers=AUTH, json={"remove": ["user:ana@acme.com"]}).json()
    assert gone["operation"] is None and "user:ana@acme.com" not in [b["principal"] for b in gone["access"]]


def test_delete_is_approved_soft_delete_and_restorable():
    client.post("/v1/apps/temp-app/deployments", json={"source": GIT}, headers=AUTH)
    deploy = client.post("/v1/apps/temp-app/deployments", json={"source": GIT}, headers=AUTH).json()
    _wait(deploy["id"])
    op = client.delete("/v1/apps/temp-app", headers=AUTH).json()
    assert op["status"] == "awaiting_approval"
    client.post(f"/v1/operations/{op['id']}/approve", json={}, headers=HUMAN)
    done = _wait(op["id"])
    assert done["result"]["deleted"] and done["result"]["restorable_until"]
    assert client.get("/v1/apps/temp-app/logs", headers=AUTH).status_code == 404
    assert client.post("/v1/apps/temp-app/restore", headers=AUTH).status_code == 403
    assert client.post("/v1/apps/temp-app/restore", headers=HUMAN).json()["status"] == "serving"


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
