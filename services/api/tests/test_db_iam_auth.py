"""IamAuthConnection: concurrent first connections never go out without a token (pool startup race)."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import google.auth
import psycopg
import pytest

from shipcube_api import db


class SlowCreds:
    """Like Cloud Run's metadata credentials: refresh takes a while, token stays None until it ends."""

    def __init__(self):
        self.token = None
        self.refreshes = 0

    @property
    def valid(self):
        return self.token is not None

    def refresh(self, _request):
        self.refreshes += 1
        time.sleep(0.05)
        self.token = "tok"


@pytest.fixture
def fake(monkeypatch):
    creds = SlowCreds()
    monkeypatch.setattr(google.auth, "default", lambda scopes=None: (creds, "p"))
    monkeypatch.setattr(db.IamAuthConnection, "_creds", None)
    seen = []
    lock = threading.Lock()

    def fake_connect(conninfo="", **kwargs):
        with lock:
            seen.append(kwargs.get("password"))
        return object()

    monkeypatch.setattr(psycopg.Connection, "connect", classmethod(lambda cls, conninfo="", **kw: fake_connect(conninfo, **kw)))
    return creds, seen


def test_concurrent_first_connections_all_get_a_token(fake):
    creds, seen = fake
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(lambda _: db.IamAuthConnection.connect("host=/x"), range(8)))
    assert seen == ["tok"] * 8
    assert creds.refreshes == 1


def test_missing_token_fails_loudly(fake, monkeypatch):
    creds, seen = fake
    monkeypatch.setattr(SlowCreds, "refresh", lambda self, _r: None)
    with pytest.raises(psycopg.OperationalError, match="no OAuth token"):
        db.IamAuthConnection.connect("host=/x")
    assert seen == []
