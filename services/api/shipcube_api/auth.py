"""Personal access tokens, following the api_tokens table in docs/research/database-design.md.

Format: ``sc_pat_<id>_<secret>``. Only HMAC-SHA-256(pepper, secret) is stored, looked up by a
unique index, so verification is one equality lookup. The pepper lives in Secret Manager in
production; locally it comes from SHIPCUBE_TOKEN_PEPPER.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import time
from dataclasses import dataclass, field

TOKEN_RE = re.compile(r"^sc_pat_([A-Za-z0-9]{8})_([A-Za-z0-9_-]{32,})$")

# Scopes from security-tenancy.md, plus the two data scopes networking proposes for the data layer.
SCOPES = ("apps:read", "apps:deploy", "access:write", "caps:write", "secrets:write", "data:read", "data:migrate")

_PEPPER = os.environ.get("SHIPCUBE_TOKEN_PEPPER", "dev-pepper-not-secret").encode()


@dataclass(frozen=True)
class Caller:
    """Who is calling, as the API's authorization layer sees it."""

    token_id: str
    user_id: str
    workspace_id: str
    scopes: frozenset[str]
    expires_at: int


@dataclass
class _TokenRow:
    id: str
    user_id: str
    workspace_id: str
    token_hmac: bytes
    scopes: list[str]
    expires_at: int
    revoked_at: int | None = None
    last_used_at: int | None = None


@dataclass
class TokenStore:
    """In-memory stand-in for the api_tokens table."""

    rows: dict[bytes, _TokenRow] = field(default_factory=dict)

    @staticmethod
    def digest(secret: str) -> bytes:
        return hmac.new(_PEPPER, secret.encode(), hashlib.sha256).digest()

    def mint(self, user_id: str, workspace_id: str, scopes: list[str], ttl_days: int = 30) -> str:
        token_id = secrets.token_hex(4)
        secret = secrets.token_urlsafe(32)
        self.add(f"sc_pat_{token_id}_{secret}", user_id, workspace_id, scopes, ttl_days)
        return f"sc_pat_{token_id}_{secret}"

    def add(self, token: str, user_id: str, workspace_id: str, scopes: list[str], ttl_days: int = 30) -> None:
        m = TOKEN_RE.match(token)
        if not m:
            raise ValueError("token must look like sc_pat_<8 alphanumerics>_<32+ url-safe chars>")
        if ttl_days > 90:
            raise ValueError("tokens expire after at most 90 days")
        row = _TokenRow(m[1], user_id, workspace_id, self.digest(m[2]), scopes, int(time.time()) + ttl_days * 86400)
        self.rows[row.token_hmac] = row

    def verify(self, token: str) -> Caller | None:
        m = TOKEN_RE.match(token)
        if not m:
            return None
        row = self.rows.get(self.digest(m[2]))
        now = int(time.time())
        if row is None or row.id != m[1] or row.revoked_at is not None or row.expires_at <= now:
            return None
        row.last_used_at = now
        return Caller(row.id, row.user_id, row.workspace_id, frozenset(row.scopes), row.expires_at)


tokens = TokenStore()

# Local development: register one token from the environment with every scope.
DEV_TOKEN = os.environ.get("SHIPCUBE_DEV_TOKEN")
if DEV_TOKEN:
    tokens.add(DEV_TOKEN, user_id="usr_demo", workspace_id="ws_demo", scopes=list(SCOPES))
