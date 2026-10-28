"""Console-managed API tokens: create (secret shown once), list, use with MCP/API, revoke.
Agents (PATs) can't manage tokens, and row-level security keeps users to their own."""

from __future__ import annotations

from .conftest import new_workspace


def test_token_lifecycle(client):
    ws = new_workspace()
    r = client.post("/v1/tokens", json={"name": "laptop", "scopes": ["apps:read", "apps:deploy"], "days": 7},
                    headers=ws["human"])
    assert r.status_code == 201 and r.headers["cache-control"] == "no-store"
    body = r.json()
    secret, token = body["secret"], body["token"]
    assert secret.startswith(token["prefix"] + "_") and token["scopes"] == ["apps:deploy", "apps:read"]

    pat = {"Authorization": f"Bearer {secret}"}
    me = client.get("/v1/me", headers=pat).json()
    assert me["token"]["id"] == token["id"] and me["workspace"]["id"] == ws["workspace_id"]

    listed = client.get("/v1/tokens", headers=ws["human"]).json()["tokens"]
    assert token["id"] in [t["id"] for t in listed]
    assert all("secret" not in t for t in listed)

    assert client.delete(f"/v1/tokens/{token['id']}", headers=ws["human"]).status_code == 204
    assert client.get("/v1/me", headers=pat).status_code == 401
    assert client.delete(f"/v1/tokens/{token['id']}", headers=ws["human"]).status_code == 404


def test_agents_cannot_manage_tokens(client):
    ws = new_workspace()
    assert client.get("/v1/tokens", headers=ws["agent"]).status_code == 403
    r = client.post("/v1/tokens", json={"name": "x", "scopes": ["apps:read"]}, headers=ws["agent"])
    assert r.status_code == 403


def test_invalid_requests(client):
    ws = new_workspace()
    assert client.post("/v1/tokens", json={"name": "x", "scopes": ["root"]}, headers=ws["human"]).status_code == 400
    assert client.post("/v1/tokens", json={"name": "x", "scopes": ["apps:read"], "days": 365},
                       headers=ws["human"]).status_code == 400
    assert client.post("/v1/tokens", json={"name": "", "scopes": ["apps:read"]}, headers=ws["human"]).status_code == 400


def test_users_only_see_and_revoke_their_own(client):
    a, b = new_workspace(), new_workspace()
    tok = client.post("/v1/tokens", json={"name": "a", "scopes": ["apps:read"]}, headers=a["human"]).json()["token"]
    assert tok["id"] not in [t["id"] for t in client.get("/v1/tokens", headers=b["human"]).json()["tokens"]]
    assert client.delete(f"/v1/tokens/{tok['id']}", headers=b["human"]).status_code == 404
