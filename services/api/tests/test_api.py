import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from shipcube_api.app import app
from shipcube_api.auth import tokens

client = TestClient(app)
TOKEN = tokens.mint("usr_demo", "ws_demo", ["apps:read", "apps:deploy", "access:write", "caps:write"])
AUTH = {"Authorization": f"Bearer {TOKEN}"}
SPEC = yaml.safe_load((Path(__file__).parents[1] / "openapi.yaml").read_text())


def _routes(paths: dict) -> set[tuple[str, str]]:
    verbs = {"get", "post", "put", "patch", "delete"}
    return {(m.upper(), re.sub(r"\{[^}]+\}", "{}", p)) for p, ops in paths.items() for m in ops if m in verbs}


def test_routes_match_openapi_contract():
    assert _routes(app.openapi()["paths"]) == _routes(SPEC["paths"])


def test_requires_token():
    assert client.get("/v1/me").status_code == 401
    assert client.get("/v1/me", headers={"Authorization": "Bearer sc_pat_deadbeef_" + "x" * 43}).status_code == 401


def test_scope_enforced():
    r = client.post("/v1/query", json={"query": {"measures": ["orders.revenue"]}}, headers=AUTH)
    assert r.status_code == 403 and r.json()["error"]["code"] == "permission_denied"


def test_deploy_idempotency():
    body = {"source": {"kind": "git", "url": "https://github.com/example/app"}}
    a = client.post("/v1/apps/demo-app/deployments", json=body, headers={**AUTH, "Idempotency-Key": "k1"})
    b = client.post("/v1/apps/demo-app/deployments", json=body, headers={**AUTH, "Idempotency-Key": "k1"})
    assert a.status_code == b.status_code == 202
    assert a.json()["id"] == b.json()["id"]
    assert a.headers["Location"] == f"/v1/operations/{a.json()['id']}"
    other = {"source": {"kind": "git", "url": "https://github.com/example/other"}}
    c = client.post("/v1/apps/demo-app/deployments", json=other, headers={**AUTH, "Idempotency-Key": "k1"})
    assert c.status_code == 409


@pytest.mark.parametrize("name", ["Bad_Name", "a", "-x"])
def test_app_name_validated(name):
    r = client.get(f"/v1/apps/{name}", headers=AUTH)
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_argument"
