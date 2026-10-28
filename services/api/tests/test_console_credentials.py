"""The console's credential: an Identity Platform ID token or a session cookie (apps/web sends the
session cookie). Each must verify against its own key set and issuer."""

from __future__ import annotations

import pytest

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
