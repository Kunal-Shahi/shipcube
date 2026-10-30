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
from typing import Any

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
    with db.worker_tx() as conn:  # operator tooling (admin CLI); the console mints through mint_pat_in
        insert_pat(conn, token, user_id, workspace_id, scopes, name, ttl_days)
    return token


def insert_pat(conn, token: str, user_id: str, workspace_id: str, scopes: list[str], name: str,
               ttl_days: int) -> dict:
    """Store a PAT (HMAC only) on the given connection and return its row (without the secret)."""
    m = TOKEN_RE.match(token)
    assert m and m[1] == "pat"
    return conn.execute(
        "insert into api_tokens (id, workspace_id, user_id, kind, name, prefix, token_hmac, scopes, expires_at)"
        " values (%s, %s, %s, 'pat', %s, %s, %s, %s, now() + make_interval(days => %s))"
        " returning id, name, prefix, scopes, created_at, expires_at, last_used_at",
        (uuid.uuid4(), workspace_id, user_id, name, f"sc_pat_{m[2]}", digest(m[3]), list(scopes), ttl_days)).fetchone()


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
    secret_hmac = digest(m[3])
    # Runs as shipcube_app under row-level security: migration 0004 exposes only the row whose
    # HMAC this request presents.
    with db.tx(role=db.APP, settings={"app.token_hmac": secret_hmac.hex()}) as conn:
        row = conn.execute(
            "update api_tokens t set last_used_at = now() from users u"
            " where t.token_hmac = %s and t.prefix = %s and t.revoked_at is null and t.expires_at > now()"
            " and u.id = t.user_id and u.disabled_at is null"
            " returning t.id, t.user_id, t.workspace_id, t.scopes, extract(epoch from t.expires_at)::bigint as exp, u.email",
            (secret_hmac, f"sc_pat_{m[2]}")).fetchone()
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


# The console sends its Identity Platform *session cookie* (apps/web identity-platform.ts
# apiCredential). Session cookies are signed with a different key set and carry a different issuer
# than ID tokens, so both are accepted here; each is checked against its own keys and issuer.
SESSION_COOKIE_CERTS = "https://www.googleapis.com/identitytoolkit/v3/relyingparty/publicKeys"


def _verify_console_jwt(token: str, project: str, id_token: Any, request: Any) -> dict[str, Any]:
    try:
        return id_token.verify_firebase_token(token, request, audience=project)
    except ValueError as id_err:
        try:
            claims = id_token.verify_token(token, request, audience=project, certs_url=SESSION_COOKIE_CERTS)
        except ValueError:
            raise id_err from None
        if claims.get("iss") != f"https://session.firebase.google.com/{project}":
            raise ValueError(f"unexpected session cookie issuer {claims.get('iss')!r}") from None
        return claims


def _claim_pending_user(conn: Any, claims: dict[str, Any]) -> dict | None:
    """A person added by email (admin bootstrap, before their first sign-in) is stored with
    firebase_uid 'pending:<email>'. Their first console sign-in, with that email verified by
    Identity Platform, binds the row to their uid. Nothing else is created here."""
    email = claims.get("email")
    if not email or claims.get("email_verified") is not True:
        return None
    u = conn.execute("update users set firebase_uid = %s, email_verified = true"
                     " where firebase_uid = 'pending:' || email and lower(email) = lower(%s) and disabled_at is null"
                     " returning id", (claims["sub"], email)).fetchone()
    if u:
        log.info("pending user claimed by first sign-in", extra={"user_id": str(u["id"])})
    return u


def verify_console(token: str, workspace_hint: str | None) -> Caller | None:
    user_id = None
    workspace_id = workspace_hint
    if token in _dev_sessions and settings.dev_mode:
        user_id, workspace_id = _dev_sessions[token]
    elif settings.idp_project and not token.startswith("sc_"):
        from google.auth.transport.requests import Request
        from google.oauth2 import id_token

        try:
            claims = _verify_console_jwt(token, settings.idp_project, id_token, Request())
        except ValueError as e:
            log.info("console token rejected", extra={"reason": str(e)[:200]})
            return None
        with db.tx(role=db.APP) as conn:
            u = conn.execute("select id from users where firebase_uid = %s and disabled_at is null",
                             (claims["sub"],)).fetchone()
            if u is None:
                u = _claim_pending_user(conn, claims)
        user_id = str(u["id"]) if u else None
    if user_id is None:
        return None
    with db.tx(role=db.APP) as conn:
        mem = _membership(conn, user_id, workspace_id)
    if mem is None:
        return None
    exp = int((datetime.now(UTC) + timedelta(hours=1)).timestamp())
    return Caller(None, user_id, str(mem["workspace_id"]), CONSOLE_SCOPES, exp, "console", mem["email"], mem["role"])


def verify(token: str, workspace_hint: str | None = None) -> Caller | None:
    if token.startswith("sc_pat_"):
        return verify_pat(token)
    return verify_console(token, workspace_hint)
