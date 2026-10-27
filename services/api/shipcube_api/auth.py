"""Personal access tokens, following the api_tokens table in docs/research/database-design.md.

Format: ``sc_pat_<id>_<secret>``. Only HMAC-SHA-256(pepper, secret) is stored, looked up by a
unique index, so verification is one equality lookup. The pepper lives in Secret Manager in
production; locally it comes from SHIPCUBE_TOKEN_PEPPER.

Console sessions (``via="console"``) stand in for Identity Platform sessions in the skeleton
(``sc_ses_<id>_<secret>``). Only a console session can approve or reject operations; a PAT can
never carry that power, so an agent cannot approve its own request.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import time
from dataclasses import dataclass, field

from .store import USER_ID, WORKSPACE_ID

TOKEN_RE = re.compile(r"^sc_(pat|ses)_([A-Za-z0-9]{8})_([A-Za-z0-9_-]{32,})$")

# Security's scope list (database-design.md §6). data:migrate allows proposing model changes only.
SCOPES = ("apps:read", "apps:deploy", "access:write", "caps:write", "secrets:write", "data:read", "data:migrate", "data:raw")

_PEPPER = os.environ.get("SHIPCUBE_TOKEN_PEPPER", "dev-pepper-not-secret").encode()


@dataclass(frozen=True)
class Caller:
    """Who is calling, as the API's authorization layer sees it."""

    token_id: str
    user_id: str
    workspace_id: str
    scopes: frozenset[str]
    expires_at: int
    via: str = "mcp"  # "mcp" for a PAT, "console" for a human console session


@dataclass
class _TokenRow:
    id: str
    user_id: str
    workspace_id: str
    token_hmac: bytes
    scopes: list[str]
    expires_at: int
    kind: str = "pat"
    revoked_at: int | None = None
    last_used_at: int | None = None


@dataclass
class TokenStore:
    """In-memory stand-in for the api_tokens table."""

    rows: dict[bytes, _TokenRow] = field(default_factory=dict)

    @staticmethod
    def digest(secret: str) -> bytes:
        return hmac.new(_PEPPER, secret.encode(), hashlib.sha256).digest()

    def mint(self, user_id: str, workspace_id: str, scopes: list[str], ttl_days: int = 30, kind: str = "pat") -> str:
        prefix = "sc_pat" if kind == "pat" else "sc_ses"
        token = f"{prefix}_{secrets.token_hex(4)}_{secrets.token_urlsafe(32)}"
        self.add(token, user_id, workspace_id, scopes, ttl_days)
        return token

    def add(self, token: str, user_id: str, workspace_id: str, scopes: list[str], ttl_days: int = 30) -> None:
        m = TOKEN_RE.match(token)
        if not m:
            raise ValueError("token must look like sc_pat_<8 alphanumerics>_<32+ url-safe chars>")
        if ttl_days > 90:
            raise ValueError("tokens expire after at most 90 days")
        if unknown := set(scopes) - set(SCOPES):
            raise ValueError(f"unknown scopes {sorted(unknown)}")
        kind = "pat" if m[1] == "pat" else "console"
        row = _TokenRow(m[2], user_id, workspace_id, self.digest(m[3]), scopes, int(time.time()) + ttl_days * 86400, kind)
        if row.token_hmac in self.rows:  # token_hmac is a unique index in api_tokens
            raise ValueError("a token with this secret already exists")
        self.rows[row.token_hmac] = row

    def verify(self, token: str) -> Caller | None:
        m = TOKEN_RE.match(token)
        if not m:
            return None
        row = self.rows.get(self.digest(m[3]))
        now = int(time.time())
        if row is None or row.id != m[2] or row.revoked_at is not None or row.expires_at <= now:
            return None
        if (row.kind == "pat") != (m[1] == "pat"):
            return None
        row.last_used_at = now
        via = "mcp" if row.kind == "pat" else "console"
        return Caller(row.id, row.user_id, row.workspace_id, frozenset(row.scopes), row.expires_at, via)


tokens = TokenStore()

# Local development: one PAT and one console session from the environment, with every scope.
DEV_TOKEN = os.environ.get("SHIPCUBE_DEV_TOKEN")
DEV_CONSOLE_TOKEN = os.environ.get("SHIPCUBE_DEV_CONSOLE_TOKEN")
for _t in (DEV_TOKEN, DEV_CONSOLE_TOKEN):
    if _t:
        tokens.add(_t, user_id=USER_ID, workspace_id=WORKSPACE_ID, scopes=list(SCOPES))
