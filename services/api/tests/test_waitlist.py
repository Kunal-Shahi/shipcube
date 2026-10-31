"""Public waitlist and its review by platform admins (waitlist.py, migration 0008)."""

import dataclasses
import uuid
from datetime import UTC, datetime

import pytest

from shipcube_api import app as app_mod
from shipcube_api import auth, db, waitlist

from .conftest import new_workspace

P = "test-idp-project"
TERMS = "2026-10-31"
ADMIN = f"admin-{uuid.uuid4().hex[:6]}@example.com"


@pytest.fixture
def idp(monkeypatch, client):
    """Identity Platform on; a console token 'jwt:<sub>:<email>' verifies to those claims (email verified),
    'jwt-unverified:<sub>:<email>' to an unverified email. Waitlist settings: one admin, generous limits."""
    monkeypatch.setattr(auth, "settings", dataclasses.replace(auth.settings, idp_project=P))

    def verify(token, project, id_token, request):
        kind, sub, email = token.split(":", 2)
        return {"sub": sub, "email": email, "email_verified": kind == "jwt"}

    monkeypatch.setattr(auth, "_verify_console_jwt", verify)
    # Each test's X-Forwarded-For is its client address (the TestClient's own peer is always "testclient").
    monkeypatch.setattr(app_mod, "settings", dataclasses.replace(app_mod.settings, trust_forwarded_for=True))
    set_waitlist(monkeypatch, platform_admins=frozenset({ADMIN}), waitlist_per_ip_per_hour=1000,
                 waitlist_per_hour=100000, max_workspaces=10_000)
    return lambda email, sub=None, verified=True: {
        "Authorization": f"Bearer {'jwt' if verified else 'jwt-unverified'}:{sub or 'uid-' + uuid.uuid4().hex[:8]}:{email}"}


def set_waitlist(monkeypatch, **kw):
    monkeypatch.setattr(waitlist, "settings", dataclasses.replace(waitlist.settings, **kw))


def email() -> str:
    return f"w-{uuid.uuid4().hex[:8]}@example.com"


def join(client, addr, ip="203.0.113.7", **extra):
    body = {"email": addr, "name": "Ada", "use_case": "internal dashboards", "terms_version": TERMS, **extra}
    return client.post("/v1/waitlist", json=body, headers={"X-Forwarded-For": ip})


def row(addr):
    with db.worker_tx() as conn:
        return conn.execute("select * from waitlist where email = %s", (addr,)).fetchone()


def test_sign_up_is_202_and_idempotent_per_email(client, idp):
    addr = email()
    assert join(client, addr).status_code == 202
    first = row(addr)
    r = join(client, addr.upper(), use_case="something else")  # same email, different case and text
    assert r.status_code == 202 and r.json() == {"status": "received"}
    again = row(addr)
    assert again["id"] == first["id"] and again["use_case"] == "internal dashboards"


def test_honeypot_is_accepted_and_dropped(client, idp):
    addr = email()
    r = join(client, addr, website="http://spam.example")
    assert r.status_code == 202 and r.json() == {"status": "received"}
    assert row(addr) is None


def test_bad_input_is_400(client, idp):
    assert join(client, "not-an-email").status_code == 400
    assert join(client, email(), use_case="x" * 501).status_code == 400
    assert join(client, email(), name="   ").status_code == 400
    assert client.post("/v1/waitlist", json={"email": email(), "name": "a", "use_case": "b", "terms_version": TERMS, "role": "owner"}).status_code == 400


def test_rate_limit_per_ip(client, idp, monkeypatch):
    set_waitlist(monkeypatch, platform_admins=frozenset({ADMIN}), waitlist_per_ip_per_hour=3, waitlist_per_hour=100000)
    ip = f"198.51.100.{uuid.uuid4().int % 250}"
    for _ in range(3):
        assert join(client, email(), ip=ip).status_code == 202
    r = join(client, email(), ip=ip)
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited" and int(r.headers["Retry-After"]) > 0
    assert join(client, email(), ip="192.0.2.99").status_code == 202  # another address isn't affected


def test_rate_limit_global(client, idp, monkeypatch):
    with db.tx() as conn:  # this hour's global count so far, from the other tests (as the test superuser)
        n = conn.execute("select coalesce(sum(count), 0) as n from rate_counters where kind = 'waitlist_global_per_hour'"
                         " and window_start = %s", (datetime.now(UTC).replace(minute=0, second=0, microsecond=0),)).fetchone()["n"]
    set_waitlist(monkeypatch, platform_admins=frozenset({ADMIN}), waitlist_per_ip_per_hour=1000, waitlist_per_hour=n + 2)
    assert join(client, email(), ip="192.0.2.1").status_code == 202
    assert join(client, email(), ip="192.0.2.2").status_code == 202
    r = join(client, email(), ip="192.0.2.3")
    # Its own code (not the per-client rate_limited), so the form offers signing in with Google instead.
    assert r.status_code == 429 and r.json()["error"]["code"] == "waitlist_busy" and int(r.headers["Retry-After"]) > 0


def test_forwarded_for_uses_the_entry_googles_front_end_appended(client, idp, monkeypatch):
    set_waitlist(monkeypatch, platform_admins=frozenset({ADMIN}), waitlist_per_ip_per_hour=1, waitlist_per_hour=100000)
    real = f"203.0.113.{uuid.uuid4().int % 250}"
    assert join(client, email(), ip=f"1.1.1.1, {real}").status_code == 202
    # A client can't reset its limit by forging the leading entries.
    assert join(client, email(), ip=f"9.9.9.9, {real}").status_code == 429


def test_cors_only_for_the_console_origins(client, idp, monkeypatch):
    monkeypatch.setattr(app_mod, "settings", dataclasses.replace(
        app_mod.settings, trust_forwarded_for=True, console_origins=frozenset({"https://c.run.app", "https://shipcube.web.app"})))
    hosting = client.options("/v1/waitlist", headers={"Origin": "https://shipcube.web.app", "Access-Control-Request-Method": "POST"})
    assert hosting.status_code == 204 and hosting.headers["Access-Control-Allow-Origin"] == "https://shipcube.web.app"
    origin = "https://c.run.app"
    ok = client.options("/v1/waitlist", headers={"Origin": origin, "Access-Control-Request-Method": "POST"})
    assert ok.status_code == 204 and ok.headers["Access-Control-Allow-Origin"] == origin
    assert client.options("/v1/waitlist", headers={"Origin": "https://evil.example"}).status_code == 403
    r = client.post("/v1/waitlist", json={"email": "bad"}, headers={"Origin": origin})
    assert r.status_code == 400 and r.headers["Access-Control-Allow-Origin"] == origin
    assert "Access-Control-Allow-Origin" not in client.get("/v1/waitlist/me", headers={"Origin": origin}).headers


def test_admin_routes_refuse_non_admins_and_pats(client, idp):
    member = new_workspace()
    for headers in (member["agent"], member["human"], idp(email())):
        assert client.get("/v1/admin/waitlist", headers=headers).status_code == 403
    # The admin's email, but unverified, or as a PAT for the admin's own account, is not enough.
    assert client.get("/v1/admin/waitlist", headers=idp(ADMIN, verified=False)).status_code == 403
    admin_ws = new_workspace(ADMIN.replace("admin-", "admin2-"))
    assert client.get("/v1/admin/waitlist", headers=admin_ws["agent"]).status_code == 403
    assert client.get("/v1/admin/waitlist").status_code == 401
    entry = waitlist.list_entries("all", 1)["requests"]
    if entry:
        assert client.post(f"/v1/admin/waitlist/{entry[0]['id']}/approve", headers=member["human"]).status_code == 403


def test_approve_then_first_sign_in_claims_the_workspace(client, idp):
    addr = email()
    sub = f"uid-{uuid.uuid4().hex[:8]}"
    assert client.get("/v1/me", headers=idp(addr, sub)).status_code == 401  # signed in, no workspace
    mine = client.get("/v1/waitlist/me", headers=idp(addr, sub)).json()
    assert mine == {"email": addr, "status": "none", "requested_at": None, "platform_admin": False}
    # Signed in, one click: the session's verified email is used, whatever the body says.
    r = client.post("/v1/waitlist", json={"email": "someone-else@example.com", "name": "Ada", "use_case": "dashboards", "terms_version": TERMS},
                    headers=idp(addr, sub))
    assert r.status_code == 202
    mine = client.get("/v1/waitlist/me", headers=idp(addr, sub)).json()
    assert mine["status"] == "pending" and mine["requested_at"] and row("someone-else@example.com") is None

    admin = idp(ADMIN)
    assert client.get("/v1/waitlist/me", headers=admin).json()["platform_admin"] is True
    listed = client.get("/v1/admin/waitlist", headers=admin).json()
    assert listed["items"] == listed["requests"]
    reqs = listed["items"]
    assert [r["created_at"] for r in reqs] == sorted((r["created_at"] for r in reqs), reverse=True)
    entry = next(i for i in reqs if i["email"] == addr)
    r = client.post(f"/v1/admin/waitlist/{entry['id']}/approve", headers=admin)
    assert r.status_code == 200, r.text
    approved = r.json()
    assert approved["status"] == "approved" and approved["decided_by"] == ADMIN and approved["workspace_id"]
    # A repeated click returns the same result and creates nothing more; the other decision conflicts.
    again = client.post(f"/v1/admin/waitlist/{entry['id']}:approve", headers=admin)
    assert again.status_code == 200 and again.json() == approved
    assert client.post(f"/v1/admin/waitlist/{entry['id']}/reject", headers=admin).json()["error"]["code"] == "conflict"
    with db.worker_tx() as conn:
        assert conn.execute("select count(*) as n from memberships m join users u on u.id = m.user_id where u.email = %s",
                            (addr,)).fetchone()["n"] == 1

    # The requester's verified sign-in now lands in their workspace, as its owner, with the default limits.
    me = client.get("/v1/me", headers=idp(addr, sub))
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["workspace"]["id"] == approved["workspace_id"] and body["role"] == "owner"
    assert body["limits"] == {"max_apps": 3, "monthly_spend_usd": 5.0} and body["platform_admin"] is False
    assert client.get("/v1/waitlist/me", headers=idp(addr, sub)).json()["status"] == "approved"
    with db.worker_tx() as conn:
        audit = conn.execute("select * from audit_log where target_type = 'waitlist' and target_id = %s", (entry["id"],)).fetchall()
    assert len(audit) == 1 and audit[0]["action"] == "waitlist.approve" and audit[0]["detail"]["admin"] == ADMIN


def test_waitlist_me_needs_a_verified_console_session(client, idp):
    assert client.get("/v1/waitlist/me").status_code == 401
    assert client.get("/v1/waitlist/me", headers=idp(email(), verified=False)).status_code == 401
    assert client.get("/v1/waitlist/me", headers=new_workspace()["agent"]).status_code == 401
    # Signed-in sign-up likewise: no verified email, no entry.
    r = client.post("/v1/waitlist", json={"name": "a", "use_case": "b", "terms_version": TERMS}, headers=idp(email(), verified=False))
    assert r.status_code == 401
    assert client.post("/v1/waitlist", json={"name": "a", "use_case": "b", "terms_version": TERMS}).status_code == 400  # anonymous needs an email


def test_dev_mode_accepts_auth_emulator_tokens(client, monkeypatch):
    import base64
    import json
    import time

    monkeypatch.setattr(auth, "settings", dataclasses.replace(auth.settings, idp_project=P))
    monkeypatch.setenv("FIREBASE_AUTH_EMULATOR_HOST", "localhost:9099")

    def tok(**claims):
        enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
        return enc({"alg": "none"}) + "." + enc(claims) + "."

    addr = email()
    good = {"aud": P, "iss": f"https://securetoken.google.com/{P}", "exp": time.time() + 600, "sub": "emu-1",
            "email": addr, "email_verified": True}
    r = client.get("/v1/waitlist/me", headers={"Authorization": f"Bearer {tok(**good)}"})
    assert r.status_code == 200 and r.json()["email"] == addr
    for bad in ({"aud": "other"}, {"exp": time.time() - 1}, {"iss": "https://evil"}):
        assert client.get("/v1/waitlist/me", headers={"Authorization": f"Bearer {tok(**{**good, **bad})}"}).status_code == 401
    monkeypatch.setattr(auth, "settings", dataclasses.replace(auth.settings, dev_mode=False))
    assert client.get("/v1/waitlist/me", headers={"Authorization": f"Bearer {tok(**good)}"}).status_code == 401


def test_another_account_with_the_email_unverified_claims_nothing(client, idp):
    addr = email()
    join(client, addr)
    entry = next(i for i in waitlist.list_entries("pending", 500)["requests"] if i["email"] == addr)
    assert client.post(f"/v1/admin/waitlist/{entry['id']}:approve", headers=idp(ADMIN)).status_code == 200
    assert client.get("/v1/me", headers=idp(addr, verified=False)).status_code == 401
    with db.worker_tx() as conn:
        assert conn.execute("select firebase_uid from users where email = %s", (addr,)).fetchone()["firebase_uid"] == f"pending:{addr}"


def test_reject(client, idp):
    addr = email()
    join(client, addr)
    entry = next(i for i in waitlist.list_entries("pending", 500)["requests"] if i["email"] == addr)
    r = client.post(f"/v1/admin/waitlist/{entry['id']}/reject", headers=idp(ADMIN))
    assert r.status_code == 200 and r.json()["status"] == "rejected" and r.json()["workspace_id"] is None
    assert client.get("/v1/waitlist/me", headers=idp(addr)).json()["status"] == "rejected"
    assert client.post(f"/v1/admin/waitlist/{entry['id']}/reject", headers=idp(ADMIN)).status_code == 200  # repeated
    assert client.post(f"/v1/admin/waitlist/{entry['id']}/approve", headers=idp(ADMIN)).status_code == 409
    assert client.post(f"/v1/admin/waitlist/{uuid.uuid4()}/reject", headers=idp(ADMIN)).status_code == 404


def test_workspace_cap(client, idp, monkeypatch):
    with db.worker_tx() as conn:
        n = conn.execute("select count(*) as n from workspaces").fetchone()["n"]
    set_waitlist(monkeypatch, platform_admins=frozenset({ADMIN}), waitlist_per_ip_per_hour=1000,
                 waitlist_per_hour=100000, max_workspaces=n)
    addr = email()
    join(client, addr)
    lst = client.get("/v1/admin/waitlist", headers=idp(ADMIN)).json()
    assert lst["workspaces"] == {"count": n, "cap": n}
    entry = next(i for i in lst["requests"] if i["email"] == addr)
    r = client.post(f"/v1/admin/waitlist/{entry['id']}/approve", headers=idp(ADMIN))
    assert r.status_code == 409 and r.json()["error"]["code"] == "workspace_cap_reached"
    assert row(addr)["status"] == "pending"
    with db.worker_tx() as conn:
        assert conn.execute("select count(*) as n from workspaces").fetchone()["n"] == n


def test_api_callers_cant_read_the_waitlist(client, idp):
    """RLS: outside the API's own marked transactions, shipcube_app sees no entries and can't write any."""
    join(client, email())
    member = new_workspace()
    with db.tx(auth.verify(member["agent"]["Authorization"][7:])) as conn:
        assert conn.execute("select count(*) as n from waitlist").fetchone()["n"] == 0
    # The public sign-up's own transactions can insert, but not read or change, entries.
    with db.tx(role=db.APP, settings={"app.via": "waitlist"}) as conn:
        assert conn.execute("select count(*) as n from waitlist").fetchone()["n"] == 0
        assert conn.execute("update waitlist set status = 'approved'").rowcount == 0
    # Someone signed in sees only their own entry.
    with db.tx(role=db.APP, settings={"app.via": "waitlist_self", "app.email": "nobody@example.com"}) as conn:
        assert conn.execute("select count(*) as n from waitlist").fetchone()["n"] == 0


def test_client_net_groups_ipv6_by_64():
    assert waitlist.client_net("2001:db8:1:2:aaaa::1") == waitlist.client_net("2001:db8:1:2:ffff:ffff:ffff:ffff") == "2001:db8:1:2::/64"
    assert waitlist.client_net("2001:db8:1:3::1") == "2001:db8:1:3::/64"
    assert waitlist.client_net("::ffff:203.0.113.9") == waitlist.client_net("203.0.113.9") == "203.0.113.9"


def test_one_ipv6_64_shares_one_per_ip_limit(client, idp, monkeypatch):
    set_waitlist(monkeypatch, platform_admins=frozenset({ADMIN}), waitlist_per_ip_per_hour=2, waitlist_per_hour=100000)
    net = f"2001:db8:{uuid.uuid4().int % 65535:x}:{uuid.uuid4().int % 65535:x}"
    assert join(client, email(), ip=f"{net}::1").status_code == 202
    assert join(client, email(), ip=f"{net}:abcd::2").status_code == 202
    # A fresh address in the same /64 doesn't get a fresh budget...
    r = join(client, email(), ip=f"{net}:ffff:ffff:ffff:ffff")
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
    # ...while the neighbouring /64 does.
    assert join(client, email(), ip=f"2001:db8:{uuid.uuid4().int % 65535:x}:1::1").status_code == 202


def test_signed_in_requests_have_their_own_buckets(client, idp, monkeypatch):
    with db.tx() as conn:  # this hour's anonymous total so far: exhaust it exactly
        n = conn.execute("select coalesce(sum(count), 0) as n from rate_counters where kind = 'waitlist_global_per_hour'"
                         " and window_start = %s", (datetime.now(UTC).replace(minute=0, second=0, microsecond=0),)).fetchone()["n"]
    set_waitlist(monkeypatch, platform_admins=frozenset({ADMIN}), waitlist_per_ip_per_hour=2, waitlist_per_hour=n,
                 waitlist_signed_in_per_hour=100000)
    assert join(client, email(), ip="192.0.2.77").json()["error"]["code"] == "waitlist_busy"  # the anonymous budget is spent
    body = {"name": "Ada", "use_case": "dashboards", "terms_version": TERMS}
    addr = email()
    # Someone already signed in can still ask...
    assert client.post("/v1/waitlist", json=body, headers=idp(addr)).status_code == 202
    assert row(addr)["status"] == "pending"
    # ...but one account can't spend the signed-in budget for everyone (per-account limit, here 2).
    assert client.post("/v1/waitlist", json=body, headers=idp(addr)).status_code == 202
    r = client.post("/v1/waitlist", json=body, headers=idp(addr))
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
    assert client.post("/v1/waitlist", json=body, headers=idp(email())).status_code == 202


def test_signed_in_total_is_capped(client, idp, monkeypatch):
    with db.tx() as conn:
        n = conn.execute("select coalesce(sum(count), 0) as n from rate_counters where kind = 'waitlist_signed_in_per_hour'"
                         " and window_start = %s", (datetime.now(UTC).replace(minute=0, second=0, microsecond=0),)).fetchone()["n"]
    set_waitlist(monkeypatch, platform_admins=frozenset({ADMIN}), waitlist_per_ip_per_hour=1000, waitlist_per_hour=100000,
                 waitlist_signed_in_per_hour=n + 1)
    body = {"name": "Ada", "use_case": "dashboards", "terms_version": TERMS}
    assert client.post("/v1/waitlist", json=body, headers=idp(email())).status_code == 202
    assert client.post("/v1/waitlist", json=body, headers=idp(email())).status_code == 429
    assert join(client, email(), ip="192.0.2.78").status_code == 202  # anonymous sign-ups are unaffected


def test_consent_is_required_and_recorded(client, idp):
    addr = email()
    for terms in ({}, {"terms_version": ""}, {"terms_version": "   "}, {"terms_version": "x" * 33}):
        body = {"email": addr, "name": "Ada", "use_case": "dashboards", **terms}
        r = client.post("/v1/waitlist", json=body, headers={"X-Forwarded-For": "203.0.113.200"})
        assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_argument", terms
    assert row(addr) is None
    assert join(client, addr).status_code == 202
    entry = row(addr)
    assert entry["terms_version"] == TERMS and entry["terms_accepted_at"] is not None
    listed = next(i for i in client.get("/v1/admin/waitlist?status=all&limit=500", headers=idp(ADMIN)).json()["requests"]
                  if i["email"] == addr)
    assert listed["terms_version"] == TERMS and listed["terms_accepted_at"]
    # Signed in: the same field, recorded the same way.
    signed = email()
    r = client.post("/v1/waitlist", json={"name": "Ada", "use_case": "dashboards", "terms_version": TERMS}, headers=idp(signed))
    assert r.status_code == 202 and row(signed)["terms_version"] == TERMS
    assert client.post("/v1/waitlist", json={"name": "Ada", "use_case": "dashboards"}, headers=idp(email())).status_code == 400


def test_new_entries_must_record_consent_in_the_database(client):
    """0009's check: even a direct insert without consent is refused (entries from before it are kept)."""
    import psycopg

    with pytest.raises(psycopg.errors.CheckViolation):
        with db.tx(role=db.APP, settings={"app.via": "waitlist"}) as conn:
            conn.execute("insert into waitlist (id, email, name, use_case) values (%s, %s, 'a', 'b')",
                         (uuid.uuid4(), email()))
