"""The AI gateway's caps, shared across its instances (#20): reserve before a provider call, settle after.

The gateway (services/gateway, SharedLimiter) calls POST /internal/gateway/reserve and
/internal/gateway/settle with a Google ID token for sc-gateway; nothing else can call them. Each is one
transaction on `rate_counters` (migration 0007), with the counter rows locked, so concurrent requests
on any number of gateway instances can't pass a nearly spent cap together.

The gateway sends the limits that apply (the app's caps, or the platform-key defaults and workspace
caps); the API only counts. Counters:
- per app: ai_tokens_per_day, requests_per_minute (a fixed one-minute window), spend_microusd_per_month;
- per workspace, on the platform key: workspace_ai_tokens_per_day, workspace_requests_per_minute,
  keyed by the workspace id.

A reservation names the day and month it was counted in; settling after midnight (or month end)
doesn't refund into the new period, and only usage beyond the reservation is charged there.
"""

from __future__ import annotations

import logging
import random
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from . import db
from .settings import settings

log = logging.getLogger("shipcube.gateway_counters")
GATEWAY_TX = {"app.via": "gateway"}  # rate_counters' policy admits only these transactions (0007)
_NS = uuid.UUID("6f1c2d0e-5b7a-4c3e-9d8f-2a1b0c9d8e7f")


def counter_key(scope: str, ident: str) -> str:
    """rate_counters.app_id for an app or a workspace. The gateway's ids are whatever its registry
    holds (a Cloud Run service name, a uuid), so they're mapped to a stable uuid per scope."""
    return str(uuid.uuid5(_NS, f"{scope}:{ident}"))


class Limits(BaseModel):
    app_tokens: int = Field(0, ge=0)
    ws_tokens: int = Field(0, ge=0)
    app_rpm: int = Field(0, ge=0)
    ws_rpm: int = Field(0, ge=0)
    spend_usd: float = Field(0, ge=0)


class ReserveRequest(BaseModel):
    app_id: str
    workspace_id: str
    platform: bool
    in_tokens: int = Field(ge=0)
    want_out: int = Field(ge=0)
    rate: tuple[float, float] | None = None  # USD per million tokens, [in, out]
    limits: Limits


class Reservation(BaseModel):
    app_id: str
    workspace_id: str
    platform: bool
    tokens: int
    microusd: int
    day: str
    month: str


class SettleRequest(BaseModel):
    reservation: Reservation
    tokens: int = Field(ge=0)
    cost_usd: float = Field(0, ge=0)


class Unauthorized(Exception):
    pass


def verify_gateway(authorization: str | None) -> None:
    """Only sc-gateway's Google ID token, for this API's audience, may call the internal routes."""
    if not authorization or not authorization.startswith("Bearer "):
        raise Unauthorized("missing bearer token")
    if not settings.internal_audience:
        raise Unauthorized("internal routes are disabled (SHIPCUBE_INTERNAL_AUDIENCE is not set)")
    from google.auth.transport import requests as ga_requests
    from google.oauth2 import id_token

    try:
        claims = id_token.verify_oauth2_token(authorization[7:], ga_requests.Request(), audience=settings.internal_audience)
    except ValueError as e:
        raise Unauthorized(f"invalid ID token: {e}") from None
    if claims.get("email") != settings.gateway_sa or not claims.get("email_verified"):
        raise Unauthorized("not the AI gateway's identity")


def _windows(now: datetime) -> dict[str, datetime]:
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return {"minute": now.replace(second=0, microsecond=0), "day": day, "month": day.replace(day=1)}


def _next_day(d: datetime) -> datetime:
    return d + timedelta(days=1)


def _next_month(d: datetime) -> datetime:
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1)


def _lock(conn, key: str, kind: str, window: datetime) -> int:
    """The counter's current value, its row created if needed and locked until the transaction ends."""
    return conn.execute(
        "insert into rate_counters (app_id, kind, window_start, count) values (%s, %s, %s, 0)"
        " on conflict (app_id, kind, window_start) do update set count = rate_counters.count returning count",
        (key, kind, window)).fetchone()["count"]


def _add(conn, key: str, kind: str, window: datetime, delta: int) -> None:
    conn.execute("update rate_counters set count = greatest(0, count + %s) where app_id = %s and kind = %s"
                 " and window_start = %s", (delta, key, kind, window))


def reserve(req: ReserveRequest, now: datetime | None = None) -> dict[str, Any]:
    """{"granted", "reservation"} or {"cap", "limit", "resets_at"}."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    w = _windows(now)
    lim = req.limits
    app_key, ws_key = counter_key("app", req.app_id), counter_key("workspace", req.workspace_id)
    with db.tx(role=db.APP, settings=GATEWAY_TX) as conn:
        # Lock in a fixed order (app before workspace), so concurrent reservations never deadlock.
        app_tokens = _lock(conn, app_key, "ai_tokens_per_day", w["day"])
        app_minute = _lock(conn, app_key, "requests_per_minute", w["minute"])
        app_spend = _lock(conn, app_key, "spend_microusd_per_month", w["month"])
        ws_tokens = ws_minute = 0
        if req.platform:
            ws_tokens = _lock(conn, ws_key, "workspace_ai_tokens_per_day", w["day"])
            ws_minute = _lock(conn, ws_key, "workspace_requests_per_minute", w["minute"])

        def cap(name: str, limit: float, resets: datetime) -> dict[str, Any]:
            return {"cap": name, "limit": limit, "resets_at": resets.isoformat()}

        granted = req.want_out
        for used, limit, name in ((app_tokens, lim.app_tokens, "ai_tokens_per_day"),
                                  (ws_tokens, lim.ws_tokens if req.platform else 0, "workspace_ai_tokens_per_day")):
            if limit:
                room = limit - used - req.in_tokens
                if room <= 0:
                    return cap(name, limit, _next_day(w["day"]))
                granted = min(granted, room)
        microusd = 0
        if lim.spend_usd:
            room_micro = int(lim.spend_usd * 1e6) - app_spend
            if room_micro <= 0:
                return cap("spend_usd_per_month", lim.spend_usd, _next_month(w["month"]))
            if req.rate:
                in_micro = req.in_tokens * req.rate[0]  # USD per million tokens = micro-USD per token
                if in_micro >= room_micro:
                    return cap("spend_usd_per_month", lim.spend_usd, _next_month(w["month"]))
                if req.rate[1] > 0:
                    granted = min(granted, int((room_micro - in_micro) / req.rate[1] + 1e-6))
                    if granted <= 0:
                        return cap("spend_usd_per_month", lim.spend_usd, _next_month(w["month"]))
                microusd = int(in_micro + granted * req.rate[1] + 0.999999)
        for used, limit, name in ((app_minute, lim.app_rpm, "requests_per_minute"),
                                  (ws_minute, lim.ws_rpm if req.platform else 0, "workspace_requests_per_minute")):
            if limit and used >= limit:
                return cap(name, limit, w["minute"] + timedelta(minutes=1))

        tokens = req.in_tokens + granted
        _add(conn, app_key, "ai_tokens_per_day", w["day"], tokens)
        _add(conn, app_key, "requests_per_minute", w["minute"], 1)
        _add(conn, app_key, "spend_microusd_per_month", w["month"], microusd)
        if req.platform:
            _add(conn, ws_key, "workspace_ai_tokens_per_day", w["day"], tokens)
            _add(conn, ws_key, "workspace_requests_per_minute", w["minute"], 1)
        if random.random() < 0.01:  # minute windows pile up; days and months are kept a while for reading
            conn.execute("delete from rate_counters where (kind like '%%per_minute' and window_start < %s)"
                         " or window_start < %s", (now - timedelta(hours=1), now - timedelta(days=62)))
    res = Reservation(app_id=req.app_id, workspace_id=req.workspace_id, platform=req.platform, tokens=tokens,
                      microusd=microusd, day=w["day"].date().isoformat(), month=w["month"].date().isoformat())
    return {"granted": granted, "reservation": res.model_dump()}


def settle(req: SettleRequest, now: datetime | None = None) -> None:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    w = _windows(now)
    r = req.reservation
    same_day = w["day"].date().isoformat() == r.day
    same_month = w["month"].date().isoformat() == r.month
    tokens = req.tokens - r.tokens if same_day else max(0, req.tokens - r.tokens)
    micro = int(req.cost_usd * 1e6 + 0.5)
    spend = micro - r.microusd if same_month else max(0, micro - r.microusd)
    with db.tx(role=db.APP, settings=GATEWAY_TX) as conn:
        app_key, ws_key = counter_key("app", r.app_id), counter_key("workspace", r.workspace_id)
        for key, kind, window, delta in ((app_key, "ai_tokens_per_day", w["day"], tokens),
                                         (app_key, "spend_microusd_per_month", w["month"], spend),
                                         (ws_key if r.platform else None, "workspace_ai_tokens_per_day", w["day"], tokens)):
            if key is None or delta == 0:
                continue
            _lock(conn, key, kind, window)
            _add(conn, key, kind, window, delta)
