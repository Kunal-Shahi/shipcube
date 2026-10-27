"""Who is calling.

- Agents (MCP): personal access tokens `sc_pat_<id>_<secret>` in security's `api_tokens` table. Only
  HMAC-SHA-256(pepper, secret) is stored, looked up through its unique index. The pepper comes
  from Secret Manager in production (SHIPCUBE_TOKEN_PEPPER, injected by Cloud Run).
- Humans (console): Identity Platform ID tokens, verified against SHIPCUBE_IDP_PROJECT. Only a
  console session can approve, reject or restore; a PAT never can, whatever its scopes.
- DEV_MODE only: `sc_ses_<id>_<secret>` stands in for a console session, registered from
  SHIPCUBE_DEV_CONSOLE_TOKEN for the bootstrap user.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from . import db
from .settings import settings

log = logging.getLogger(__name__)
TOKEN_RE = re.compile(r"^sc_(pat|ses)_([A-Za-z0-9]{8})_([A-Za-z0-9_-]{32,})$")

# Security's scope list (database-design.md §6). data:migrate allows proposing model changes only.
SCOPES = ("apps:read", "apps:deploy", "access:write", "caps:write", "secrets:write", "data:read", "data:migrate", "data:raw")
CONSOLE_SCOPES = frozenset(SCOPES) - {"data:raw"}


def _pepper() -> bytes:
    pepper = os.environ.get("SHIPCUBE_TOKEN_PEPPER")
    if not pepper:
        if not settings.dev_mode:
            raise RuntimeError("SHIPCUBE_TOKEN_PEPPER is required outside DEV_MODE")
        pepper = "dev-pepper-not-secret"
    return pepper.encode()


def digest(secret: str) -> bytes:
    return hmac.new(_pepper(), secret.encode(), hashlib.sha256).digest()


@dataclass(frozen=True)
class Caller:
    token_id: str | None
    user_id: str
    workspace_id: str
    scopes: frozenset[str]
    expires_at: int
    via: str = "mcp"  # "mcp" for a PAT, "console" for a human session
    email: str = ""
    role: str = "viewer"


def mint_pat(user_id: str, workspace_id: str, scopes: list[str], name: str = "mcp", ttl_days: int = 30,
             token: str | None = None) -> str:
    """Create a PAT and return it. The secret is shown once and never stored."""
    if unknown := set(scopes) - set(SCOPES):
        raise ValueError(f"unknown scopes {sorted(unknown)}")
    if not 1 <= ttl_days <= 90:
        raise ValueError("tokens expire after 1 to 90 days")
    token = token or f"sc_pat_{secrets.token_hex(4)}_{secrets.token_urlsafe(32)}"
    m = TOKEN_RE.match(token)
    if not m or m[1] != "pat":
        raise ValueError("token must look like sc_pat_<8 alphanumerics>_<32+ url-safe chars>")
    with db.tx() as conn:
        conn.execute(
            "insert into api_tokens (id, workspace_id, user_id, kind, name, prefix, token_hmac, scopes, expires_at)"
            " values (%s, %s, %s, 'pat', %s, %s, %s, %s, now() + make_interval(days => %s))",
            (uuid.uuid4(), workspace_id, user_id, name, f"sc_pat_{m[2]}", digest(m[3]), list(scopes), ttl_days))
    return token


def _membership(conn, user_id: str, workspace_id: str | None) -> dict | None:
    q = ("select m.workspace_id, m.role, u.email from memberships m join users u on u.id = m.user_id"
         " where m.user_id = %s and u.disabled_at is null")
    args: tuple = (user_id,)
    if workspace_id:
        q += " and m.workspace_id = %s"
        args += (workspace_id,)
    return conn.execute(q + " order by m.created_at limit 1", args).fetchone()


def verify_pat(token: str) -> Caller | None:
    m = TOKEN_RE.match(token)
    if not m or m[1] != "pat":
        return None
    with db.tx() as conn:
        row = conn.execute(
            "update api_tokens t set last_used_at = now() from users u"
            " where t.token_hmac = %s and t.prefix = %s and t.revoked_at is null and t.expires_at > now()"
            " and u.id = t.user_id and u.disabled_at is null"
            " returning t.id, t.user_id, t.workspace_id, t.scopes, extract(epoch from t.expires_at)::bigint as exp, u.email",
            (digest(m[3]), f"sc_pat_{m[2]}")).fetchone()
        if row is None:
            return None
        mem = _membership(conn, row["user_id"], row["workspace_id"])
    if mem is None:  # the user left the workspace: the token is dead
        return None
    return Caller(str(row["id"]), str(row["user_id"]), str(row["workspace_id"]), frozenset(row["scopes"]), row["exp"],
                  "mcp", row["email"], mem["role"])


_dev_sessions: dict[str, tuple[str, str]] = {}  # token -> (user_id, workspace_id); DEV_MODE only


def register_dev_session(token: str, user_id: str, workspace_id: str) -> None:
    if not settings.dev_mode:
        raise RuntimeError("dev console sessions exist only in DEV_MODE")
    m = TOKEN_RE.match(token)
    if not m or m[1] != "ses":
        raise ValueError("dev console tokens look like sc_ses_<8 alphanumerics>_<32+ chars>")
    _dev_sessions[token] = (user_id, workspace_id)


def verify_console(token: str, workspace_hint: str | None) -> Caller | None:
    user_id = None
    workspace_id = workspace_hint
    if token in _dev_sessions and settings.dev_mode:
        user_id, workspace_id = _dev_sessions[token]
    elif settings.idp_project and not token.startswith("sc_"):
        from google.auth.transport.requests import Request
        from google.oauth2 import id_token

        try:
            claims = id_token.verify_firebase_token(token, Request(), audience=settings.idp_project)
        except ValueError as e:
            log.info("console token rejected", extra={"reason": str(e)[:200]})
            return None
        with db.tx() as conn:
            u = conn.execute("select id from users where firebase_uid = %s and disabled_at is null",
                             (claims["sub"],)).fetchone()
        user_id = str(u["id"]) if u else None
    if user_id is None:
        return None
    with db.tx() as conn:
        mem = _membership(conn, user_id, workspace_id)
    if mem is None:
        return None
    exp = int((datetime.now(UTC) + timedelta(hours=1)).timestamp())
    return Caller(None, user_id, str(mem["workspace_id"]), CONSOLE_SCOPES, exp, "console", mem["email"], mem["role"])


def verify(token: str, workspace_hint: str | None = None) -> Caller | None:
    if token.startswith("sc_pat_"):
        return verify_pat(token)
    return verify_console(token, workspace_hint)
