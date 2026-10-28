"""The local IAP proxy: private by default, the access list decides, identity headers can't be spoofed."""

import json
from dataclasses import replace

import httpx
import pytest
from starlette.testclient import TestClient

from shipcube_api.local_iap import LocalIap
from shipcube_api.settings import Settings

TOKENS = {"sc_pat_a_secret": {"email": "alice@acme.com", "user_id": "u1"},
          "sc_pat_b_secret": {"email": "bob@other.com", "user_id": "u2"}}


@pytest.fixture
def iap(tmp_path):
    seen = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"path": request.url.path, "user": request.headers.get("x-goog-authenticated-user-email")})

    async def resolve(token):
        return TOKENS.get(token)

    proxy = LocalIap(replace(Settings(), local_state_dir=str(tmp_path)), resolve=resolve,
                     upstream=lambda rev: f"http://{rev}:8080", transport=httpx.MockTransport(upstream))
    state = {"service": "tracker-abc123", "traffic": "tracker-abc123-00001-aaaa", "ingress": "all",
             "access": ["user:alice@acme.com"], "caps": {"concurrency": 2, "timeout_s": 5}}
    (tmp_path / "tracker-abc123.json").write_text(json.dumps(state))
    client = TestClient(proxy, base_url="http://tracker-abc123.localhost:8088")
    client.state_path, client.seen = tmp_path / "tracker-abc123.json", seen
    return client


def test_unauthenticated_is_401_and_browsers_go_to_sign_in(iap):
    assert iap.get("/").status_code == 401
    r = iap.get("/", headers={"Accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/_iap/login"
    assert iap.seen == []


def test_only_principals_on_the_access_list_get_through(iap):
    assert iap.get("/x", headers={"Authorization": "Bearer sc_pat_b_secret"}).status_code == 403
    r = iap.get("/x?q=1", headers={"Authorization": "Bearer sc_pat_a_secret",
                                   "X-Goog-Authenticated-User-Email": "accounts.google.com:boss@acme.com"})
    assert r.status_code == 200 and r.json() == {"path": "/x", "user": "accounts.google.com:alice@acme.com"}
    fwd = iap.seen[-1]
    assert "authorization" not in fwd.headers and fwd.url.query == b"q=1"  # the app never sees the ShipCube token


def test_domain_grants_and_cookie_sign_in(iap):
    st = json.loads(iap.state_path.read_text())
    iap.state_path.write_text(json.dumps({**st, "access": ["domain:other.com"]}))
    r = iap.post("/_iap/login", data={"token": "sc_pat_b_secret"}, follow_redirects=False)
    assert r.status_code == 303
    r = iap.get("/", cookies={"__shipcube_iap": "sc_pat_b_secret", "app": "1"})
    assert r.status_code == 200 and r.json()["user"] == "accounts.google.com:bob@other.com"
    assert "__shipcube_iap" not in iap.seen[-1].headers.get("cookie", "") and "app=1" in iap.seen[-1].headers["cookie"]


def test_soft_deleted_and_unknown_apps_are_404(iap):
    st = json.loads(iap.state_path.read_text())
    iap.state_path.write_text(json.dumps({**st, "ingress": "internal"}))
    assert iap.get("/", headers={"Authorization": "Bearer sc_pat_a_secret"}).status_code == 404
    other = TestClient(iap.app, base_url="http://nope.localhost:8088")
    assert other.get("/", headers={"Authorization": "Bearer sc_pat_a_secret"}).status_code == 404
