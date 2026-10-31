"""The console's credential: an Identity Platform ID token or a session cookie (apps/web sends the
session cookie). Each must verify against its own key set and issuer."""

from __future__ import annotations

import dataclasses
import uuid

import pytest

from shipcube_api import admin, auth, db
from shipcube_api.auth import SESSION_COOKIE_CERTS, _verify_console_jwt

P = "shipcube-control-ks"


class FakeIdToken:
    def __init__(self, id_ok=False, cookie_claims=None):
        self.id_ok, self.cookie_claims, self.calls = id_ok, cookie_claims, []

    def verify_firebase_token(self, token, request, audience):
        self.calls.append(("id", audience))
        if self.id_ok:
            return {"sub": "uid-1", "iss": f"https://securetoken.google.com/{P}"}
        raise ValueError("Certificate for key id KgkHcg not found.")

    def verify_token(self, token, request, audience, certs_url):
        self.calls.append(("cookie", audience, certs_url))
        if self.cookie_claims is None:
            raise ValueError("bad signature")
        return self.cookie_claims


def test_id_token_accepted():
    assert _verify_console_jwt("t", P, FakeIdToken(id_ok=True), None)["sub"] == "uid-1"


def test_session_cookie_accepted_with_its_own_keys_and_issuer():
    fake = FakeIdToken(cookie_claims={"sub": "uid-2", "iss": f"https://session.firebase.google.com/{P}"})
    assert _verify_console_jwt("t", P, fake, None)["sub"] == "uid-2"
    assert fake.calls[1] == ("cookie", P, SESSION_COOKIE_CERTS)


def test_session_cookie_from_another_project_rejected():
    fake = FakeIdToken(cookie_claims={"sub": "x", "iss": "https://session.firebase.google.com/other"})
    with pytest.raises(ValueError, match="issuer"):
        _verify_console_jwt("t", P, fake, None)


def test_neither_reports_the_id_token_error():
    with pytest.raises(ValueError, match="KgkHcg"):
        _verify_console_jwt("t", P, FakeIdToken(), None)


# --- mapping a verified console sign-in to a workspace member ------------------------------


@pytest.fixture
def idp(monkeypatch, client):
    """Identity Platform on, with _verify_console_jwt returning whatever claims a test sets.
    `client` starts the app, which migrates the test database."""
    monkeypatch.setattr(auth, "settings", dataclasses.replace(auth.settings, idp_project=P))
    box: dict = {}
    monkeypatch.setattr(auth, "_verify_console_jwt", lambda token, project, id_token, request: box["claims"])
    return box


def _pending_member():
    email = f"Invitee-{uuid.uuid4().hex[:8]}@example.com"
    user_id, ws_id, _ = admin.bootstrap(email, f"ws-{uuid.uuid4().hex[:8]}", "Test", None)
    return email, user_id, ws_id


def test_first_sign_in_claims_the_pending_user_by_verified_email(idp):
    email, user_id, ws_id = _pending_member()
    uid = f"uid-{uuid.uuid4().hex[:8]}"
    idp["claims"] = {"sub": uid, "email": email.lower(), "email_verified": True}
    c = auth.verify("session-cookie", None)
    assert c is not None and c.user_id == user_id and c.workspace_id == ws_id and c.via == "console"
    with db.worker_tx() as conn:
        assert conn.execute("select firebase_uid from users where id = %s", (user_id,)).fetchone()["firebase_uid"] == uid
    # Later sign-ins find the user by uid.
    idp["claims"] = {"sub": uid}
    assert auth.verify("session-cookie", None).user_id == user_id


def test_unverified_email_claims_nothing(idp):
    email, user_id, _ = _pending_member()
    idp["claims"] = {"sub": f"uid-{uuid.uuid4().hex[:8]}", "email": email, "email_verified": False}
    assert auth.verify("session-cookie", None) is None
    with db.worker_tx() as conn:
        assert conn.execute("select firebase_uid from users where id = %s", (user_id,)).fetchone()["firebase_uid"].startswith("pending:")


def test_a_bound_user_is_never_reclaimed_by_email(idp):
    email, user_id, _ = _pending_member()
    first = f"uid-{uuid.uuid4().hex[:8]}"
    idp["claims"] = {"sub": first, "email": email, "email_verified": True}
    assert auth.verify("session-cookie", None).user_id == user_id
    # Another Google account with the same verified email can't take the row over.
    idp["claims"] = {"sub": f"uid-{uuid.uuid4().hex[:8]}", "email": email, "email_verified": True}
    assert auth.verify("session-cookie", None) is None


# --- Google's signing keys are fetched once, not on every request -------------------------------


class _Resp:
    def __init__(self, status, cache_control=""):
        self.status, self.headers, self.data = status, {"cache-control": cache_control}, b"{}"


def test_signing_keys_are_cached_for_their_max_age(monkeypatch):
    r = auth._CertCachingRequest()
    calls = []
    answers = iter([_Resp(503), _Resp(200, "public, max-age=2"), _Resp(200)])
    r._inner = lambda url, method="GET", **kw: calls.append((method, url)) or next(answers)
    assert r("https://keys").status == 503 and r("https://keys").status == 200  # errors aren't cached
    assert r("https://keys").status == 200 and len(calls) == 2  # cached
    now = auth.time.monotonic()
    monkeypatch.setattr(auth.time, "monotonic", lambda: now + 3)  # max-age=2 has passed
    r("https://keys")
    assert len(calls) == 3


def test_an_unknown_key_id_refetches_the_keys_once(monkeypatch, client):
    monkeypatch.setattr(auth, "settings", dataclasses.replace(auth.settings, idp_project=P))
    req = auth._CertCachingRequest()
    req._cache["https://keys"] = (auth.time.monotonic() + 3600, _Resp(200))
    monkeypatch.setattr(auth, "_cert_request", req)
    attempts = []

    def verify(token, project, id_token, request):
        attempts.append(dict(request._cache))
        if len(attempts) == 1:
            raise ValueError("Certificate for key id abc not found.")
        return {"sub": f"uid-{uuid.uuid4().hex[:8]}", "email": "nobody@example.com", "email_verified": True}

    monkeypatch.setattr(auth, "_verify_console_jwt", verify)
    ident = auth.console_identity("cookie")
    assert ident is not None and ident.user_id is None and len(attempts) == 2 and attempts[1] == {}
