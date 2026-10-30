"""#20: the AI gateway's caps are shared across its instances, through /internal/gateway/reserve and
/settle on rate_counters. Only sc-gateway's ID token may call them."""

import threading
import uuid
from datetime import UTC, datetime

import pytest

from shipcube_api import db, gateway_counters
from shipcube_api.gateway_counters import Limits, ReserveRequest, Reservation, SettleRequest, reserve, settle

from .conftest import new_workspace

NOON = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


def _req(app, ws, *, platform=False, in_tokens=10, want=400, rate=None, **limits):
    return ReserveRequest(app_id=app, workspace_id=ws, platform=platform, in_tokens=in_tokens, want_out=want,
                          rate=rate, limits=Limits(**limits))


def _ids():  # the gateway's registry names apps by Cloud Run service, not uuid
    return f"tracker-{uuid.uuid4().hex[:8]}", str(uuid.uuid4())


def test_reservations_share_one_budget_and_settle_back(client):
    app, ws = _ids()
    grants = []
    for _ in range(4):  # four requests in flight, from any number of gateway instances
        r = reserve(_req(app, ws, app_tokens=1000), NOON)
        grants.append(r.get("granted", r.get("cap")))
    assert grants == [400, 400, 170, "ai_tokens_per_day"]
    r = reserve(_req(app, ws, app_tokens=2000), NOON)  # a higher cap leaves room again
    settle(SettleRequest(reservation=Reservation(**r["reservation"]), tokens=60), NOON)
    with db.tx(role=db.APP, settings=gateway_counters.GATEWAY_TX) as conn:
        used = conn.execute("select count from rate_counters where app_id = %s and kind = 'ai_tokens_per_day'",
                            (gateway_counters.counter_key("app", app),)).fetchone()
    assert used["count"] == 410 + 410 + 180 + 60  # the last reservation became its actual 60


def test_concurrent_reservations_never_overshoot(client):
    app, ws = _ids()
    results = []
    lock = threading.Lock()

    def one():
        r = reserve(_req(app, ws, in_tokens=0, want=100, app_tokens=1000), NOON)
        with lock:
            results.append(r.get("granted", 0))

    threads = [threading.Thread(target=one) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(results) == 1000 and results.count(100) == 10


def test_workspace_caps_span_apps_and_requests_per_minute(client):
    a, ws = _ids()
    b = str(uuid.uuid4())
    assert reserve(_req(a, ws, platform=True, in_tokens=0, want=1000, ws_tokens=500), NOON)["granted"] == 500
    assert reserve(_req(b, ws, platform=True, in_tokens=0, want=10, ws_tokens=500), NOON)["cap"] == "workspace_ai_tokens_per_day"
    c, ws2 = _ids()
    assert "granted" in reserve(_req(c, ws2, platform=True, ws_rpm=1), NOON)
    assert reserve(_req(str(uuid.uuid4()), ws2, platform=True, ws_rpm=1), NOON)["cap"] == "workspace_requests_per_minute"


def test_spend_is_reserved_and_a_late_settle_refunds_the_right_period(client):
    app, ws = _ids()
    rate = (1.0, 100.0)  # USD per million tokens in / out
    got = [reserve(_req(app, ws, in_tokens=0, want=4000, rate=rate, spend_usd=1), NOON) for _ in range(4)]
    assert [g.get("granted", g.get("cap")) for g in got] == [4000, 4000, 2000, "spend_usd_per_month"]

    app2, ws2 = _ids()
    late = datetime(2026, 10, 30, 23, 59, tzinfo=UTC)
    r = reserve(_req(app2, ws2, in_tokens=10, want=900, app_tokens=1000), late)
    after = datetime(2026, 10, 31, 0, 1, tzinfo=UTC)
    early = reserve(_req(app2, ws2, in_tokens=10, want=300, app_tokens=1000), after)
    settle(SettleRequest(reservation=Reservation(**early["reservation"]), tokens=310), after)
    settle(SettleRequest(reservation=Reservation(**r["reservation"]), tokens=100), after)  # refund belongs to Oct 30
    assert reserve(_req(app2, ws2, in_tokens=10, want=1000, app_tokens=1000), after)["granted"] == 1000 - 310 - 10


def test_only_the_gateway_may_call_the_internal_routes(client, monkeypatch):
    body = {"app_id": str(uuid.uuid4()), "workspace_id": str(uuid.uuid4()), "platform": False, "in_tokens": 1,
            "want_out": 1, "limits": {}}
    ws = new_workspace()
    for headers in ({}, ws["agent"], ws["human"]):  # no token, a PAT, a console session
        assert client.post("/internal/gateway/reserve", json=body, headers=headers).status_code == 403

    from shipcube_api import settings as settings_mod
    import dataclasses

    monkeypatch.setattr(gateway_counters, "settings", dataclasses.replace(settings_mod.settings, internal_audience="https://api"))
    claims = {"email": "sc-gateway@shipcube-control-ks.iam.gserviceaccount.com", "email_verified": True}
    from google.oauth2 import id_token

    monkeypatch.setattr(id_token, "verify_oauth2_token", lambda tok, req, audience: dict(claims) if tok == "good" else
                        (_ for _ in ()).throw(ValueError("bad signature")))
    assert client.post("/internal/gateway/reserve", json=body, headers={"Authorization": "Bearer good"}).status_code == 200
    assert client.post("/internal/gateway/reserve", json=body, headers={"Authorization": "Bearer forged"}).status_code == 403
    claims["email"] = "someone-else@shipcube-control-ks.iam.gserviceaccount.com"
    assert client.post("/internal/gateway/reserve", json=body, headers={"Authorization": "Bearer good"}).status_code == 403


def test_api_callers_cannot_read_counters(client):
    ws = new_workspace()
    from shipcube_api.auth import Caller

    reserve(_req(str(uuid.uuid4()), str(uuid.uuid4()), app_tokens=10), NOON)
    caller = Caller(token_id=None, user_id=ws["user_id"], workspace_id=ws["workspace_id"], scopes=frozenset(), expires_at=0,
                    via="mcp", role="admin")
    with db.tx(caller) as conn:
        assert conn.execute("select count(*) as n from rate_counters").fetchone()["n"] == 0
