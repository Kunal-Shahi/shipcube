"""Public waitlist with manual approval (migration 0008).

- Sign-up (`POST /v1/waitlist`, no auth): always 202, whether the email is new, already listed or
  decided, or the honeypot was filled, so the endpoint never reveals who applied. Rate-limited per
  client IP and in total, on `rate_counters`.
- Platform admins (console session, verified email in SHIPCUBE_PLATFORM_ADMINS) list, approve and
  reject. Approving creates the requester's workspace with the default caps and adds their email as a
  pending owner (`firebase_uid = 'pending:<email>'`), which their first verified Google sign-in claims
  (auth._claim_pending_user). Approvals stop at SHIPCUBE_MAX_WORKSPACES workspaces in total.

Every transaction here marks itself with app.via, which 0008's policies check; nothing from a request
sets it.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import random
import logging
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from . import auth, db
from .settings import settings
from .store import CAP_PERIOD, WORKSPACE_DEFAULTS

log = logging.getLogger("shipcube.waitlist")
SIGNUP_TX = {"app.via": "waitlist"}
ADMIN_TX = {"app.via": "platform_admin"}
_NS = uuid.UUID("0b8f5c2e-31d4-4f6a-9e7b-5a2c1d0e9f83")
_GLOBAL = str(uuid.uuid5(_NS, "global"))
_SIGNED_IN = str(uuid.uuid5(_NS, "signed-in"))
_APPROVE_LOCK = 0x5C_57A1  # serialises approvals, so the workspace cap can't be passed by two at once
_COLUMNS = "id, email, name, use_case, status, created_at, decided_at, decided_by, workspace_id"


class RateLimited(Exception):
    """code: rate_limited (this client or account), or waitlist_busy (all anonymous sign-ups this hour:
    the form then offers signing in with Google, which has its own budget)."""

    def __init__(self, message: str, retry_after_s: int, code: str = "rate_limited") -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s
        self.code = code


class Conflict(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def client_net(ip: str) -> str:
    """What a client is limited by: an IPv4 address, or an IPv6 address's /64 (one subscriber's whole
    allocation, so rotating addresses within it doesn't buy more sign-ups)."""
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError:
        return ip.strip()
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped:
            return str(addr.ipv4_mapped)
        return str(ipaddress.IPv6Network(f"{addr}/64", strict=False))
    return str(addr)


def _key(kind: str, value: str) -> str:
    # Only a keyed digest of the address or email is stored, never the value itself.
    return str(uuid.uuid5(_NS, hmac.new(auth._pepper(), f"waitlist-{kind}:{value}".encode(), hashlib.sha256).hexdigest()))


def _count(conn, key: str, kind: str, window: datetime, limit: int) -> bool:
    """Add one to the counter unless it's at its limit; True if added. The row stays locked."""
    used = conn.execute(
        "insert into rate_counters (app_id, kind, window_start, count) values (%s, %s, %s, 0)"
        " on conflict (app_id, kind, window_start) do update set count = rate_counters.count returning count",
        (key, kind, window)).fetchone()["count"]
    if used >= limit:
        return False
    conn.execute("update rate_counters set count = count + 1 where app_id = %s and kind = %s and window_start = %s",
                 (key, kind, window))
    return True


def sign_up(email: str, name: str, use_case: str, honeypot: str, ip: str | None, now: datetime | None = None) -> None:
    """ip None: a verified console session asked. Signed-in requests have their own buckets (per account
    and in total), so an anonymous flood can't lock out people who are already signed in."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    hour = now.replace(minute=0, second=0, microsecond=0)
    retry = int((hour + timedelta(hours=1) - now).total_seconds()) + 1
    with db.tx(role=db.APP, settings=SIGNUP_TX) as conn:
        # The narrower limit first, so one client's burst doesn't spend the shared budget.
        if ip is None:
            if not _count(conn, _key("account", email.lower()), "waitlist_account_per_hour", hour,
                          settings.waitlist_per_ip_per_hour):
                raise RateLimited("too many requests from this account; try again later", retry)
            if not _count(conn, _SIGNED_IN, "waitlist_signed_in_per_hour", hour, settings.waitlist_signed_in_per_hour):
                raise RateLimited("the waitlist is busy; try again later", retry)
        else:
            if not _count(conn, _key("ip", client_net(ip)), "waitlist_ip_per_hour", hour, settings.waitlist_per_ip_per_hour):
                raise RateLimited("too many sign-ups from this address; try again later", retry)
            if not _count(conn, _GLOBAL, "waitlist_global_per_hour", hour, settings.waitlist_per_hour):
                raise RateLimited("the waitlist is busy; sign in with Google to request access", retry, "waitlist_busy")
        if random.random() < 0.02:  # past hours' counters, one row per client address, are never read again
            conn.execute("delete from rate_counters where kind like 'waitlist\\_%%' and window_start < %s",
                         (hour - timedelta(hours=1),))
        if honeypot:
            log.info("waitlist honeypot filled")
            return
        conn.execute("insert into waitlist (id, email, name, use_case) values (%s, %s, %s, %s)"
                     " on conflict do nothing", (uuid.uuid4(), email, name, use_case))  # no arbiter: one needs select under RLS


def mine(email: str) -> dict[str, Any]:
    """A signed-in person's own request: status none|pending|approved|rejected. `email` must be verified."""
    with db.tx(role=db.APP, settings={"app.via": "waitlist_self", "app.email": email}) as conn:
        row = conn.execute("select status, created_at from waitlist where lower(email) = lower(%s)", (email,)).fetchone()
    if row is None:
        return {"email": email, "status": "none", "requested_at": None}
    return {"email": email, "status": row["status"], "requested_at": row["created_at"].isoformat()}


def is_admin(email: str | None, verified: bool) -> bool:
    return bool(email) and verified and email.lower() in settings.platform_admins


def _entry(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "id": str(row["id"]), "workspace_id": str(row["workspace_id"]) if row["workspace_id"] else None,
            "created_at": row["created_at"].isoformat(),
            "decided_at": row["decided_at"].isoformat() if row["decided_at"] else None}


def list_entries(status: str, limit: int) -> dict[str, Any]:
    q = f"select {_COLUMNS} from waitlist"
    args: tuple = ()
    if status != "all":
        q += " where status = %s"
        args = (status,)
    with db.tx(role=db.APP, settings=ADMIN_TX) as conn:
        rows = conn.execute(q + " order by created_at desc, id limit %s", (*args, limit)).fetchall()
        count = conn.execute("select count(*) as n from workspaces").fetchone()["n"]
    entries = [_entry(r) for r in rows]
    # Both names: the console has read each while the contract settled.
    return {"items": entries, "requests": entries, "workspaces": {"count": count, "cap": settings.max_workspaces}}


def _audit(conn, admin_email: str, admin_user_id: str | None, action: str, entry_id: str, detail: dict) -> None:
    conn.execute("insert into audit_log (workspace_id, actor_user_id, actor_type, action, target_type, target_id, detail)"
                 " values (%s, %s, 'user', %s, 'waitlist', %s, %s)",
                 (detail.get("workspace_id"), admin_user_id, action, entry_id,
                  json.dumps({**detail, "admin": admin_email})))


def _pending(conn, entry_id: str, decision: str) -> dict[str, Any] | None:
    """The entry, locked, if it's still pending; None if it already has this decision (a repeated click);
    Conflict if it has the other one."""
    row = conn.execute(f"select {_COLUMNS} from waitlist where id = %s for update", (entry_id,)).fetchone()
    if row is None:
        raise LookupError("no such waitlist entry")
    if row["status"] == decision:
        return None
    if row["status"] != "pending":
        raise Conflict("conflict", f"this entry was already {row['status']}")
    return row


def _slug(email: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", email.split("@")[0].lower()).strip("-")[:28] or "ws"
    return f"{base}-{secrets.token_hex(3)}"


def approve(entry_id: str, admin_email: str, admin_user_id: str | None) -> dict[str, Any]:
    with db.tx(role=db.APP, settings=ADMIN_TX) as conn:
        conn.execute("select pg_advisory_xact_lock(%s)", (_APPROVE_LOCK,))
        row = _pending(conn, entry_id, "approved")
        if row is None:
            return _entry(conn.execute(f"select {_COLUMNS} from waitlist where id = %s", (entry_id,)).fetchone())
        if conn.execute("select count(*) as n from workspaces").fetchone()["n"] >= settings.max_workspaces:
            raise Conflict("workspace_cap_reached",
                           f"ShipCube is at its limit of {settings.max_workspaces} workspaces; raise SHIPCUBE_MAX_WORKSPACES to approve more")
        email = row["email"]
        # An existing person (already signed in, or added earlier) keeps their row; otherwise a pending
        # one that only a verified Google sign-in with this email can claim.
        user = conn.execute("select id from users where lower(email) = lower(%s)", (email,)).fetchone() or conn.execute(
            "insert into users (id, firebase_uid, email, email_verified) values (%s, %s, %s, false) returning id",
            (uuid.uuid4(), f"pending:{email}", email)).fetchone()
        ws_id = uuid.uuid4()
        conn.execute("insert into workspaces (id, slug, name, created_by) values (%s, %s, %s, %s)",
                     (ws_id, _slug(email), f"{row['name']}'s workspace"[:100], user["id"]))
        conn.execute("insert into memberships (workspace_id, user_id, role) values (%s, %s, 'owner')", (ws_id, user["id"]))
        for kind, value in WORKSPACE_DEFAULTS.items():
            conn.execute("insert into caps (id, workspace_id, app_id, kind, limit_value, period) values (%s,%s,null,%s,%s,%s)",
                         (uuid.uuid4(), ws_id, kind, value, CAP_PERIOD.get(kind)))
        out = conn.execute(f"update waitlist set status = 'approved', decided_at = now(), decided_by = %s, workspace_id = %s"
                           f" where id = %s returning {_COLUMNS}", (admin_email, ws_id, entry_id)).fetchone()
        _audit(conn, admin_email, admin_user_id, "waitlist.approve", entry_id,
               {"workspace_id": str(ws_id), "email": email})
    log.info("waitlist entry approved", extra={"entry_id": entry_id, "workspace_id": str(ws_id)})
    return _entry(out)


def reject(entry_id: str, admin_email: str, admin_user_id: str | None) -> dict[str, Any]:
    with db.tx(role=db.APP, settings=ADMIN_TX) as conn:
        row = _pending(conn, entry_id, "rejected")
        if row is None:
            return _entry(conn.execute(f"select {_COLUMNS} from waitlist where id = %s", (entry_id,)).fetchone())
        out = conn.execute(f"update waitlist set status = 'rejected', decided_at = now(), decided_by = %s"
                           f" where id = %s returning {_COLUMNS}", (admin_email, entry_id)).fetchone()
        _audit(conn, admin_email, admin_user_id, "waitlist.reject", entry_id, {"email": row["email"]})
    return _entry(out)
