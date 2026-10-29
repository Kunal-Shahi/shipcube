"""Postgres access: a connection pool, versioned migrations and per-transaction caller context.

Schema: migrations/*.sql, applied in order and recorded in `schema_migrations`. 0001 is security's
design verbatim (docs/research/database-design.md); later files only add to it.

Row-level security (security's design, migrations 0001 and 0003):
- API requests run in `tx(caller)`: it sets `app.user_id`, `app.workspace_id` and `app.via` for
  the transaction and runs as `shipcube_app`, so every tenant table is filtered to the caller.
- PAT verification runs as `shipcube_app` with only `app.token_hmac` set (migration 0004).
- The worker and the operator CLI run as `shipcube_worker` (BYPASSRLS): `tx(role=WORKER)`.

On Cloud SQL each service signs in as its own IAM database user whose session already starts in
its role (infra/db/bootstrap.sql): sc-api -> shipcube_app, sc-worker -> shipcube_worker. Locally
and in tests the login is a superuser, so with SHIPCUBE_DB_SET_ROLE=1 (default) each transaction
also runs `SET LOCAL ROLE` to get the same behaviour.

Migrations never run on service startup in production: `infra/db/migrate.sh` applies them as
sc-migrator (`python -m shipcube_api.admin migrate`). Services only check that the schema is
current (`check_schema`) and refuse to start otherwise. DEV_MODE may migrate at startup.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

log = logging.getLogger(__name__)
MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
_pool: ConnectionPool | None = None


def dsn() -> str:
    url = os.environ.get("SHIPCUBE_DATABASE_URL")
    if not url:
        raise RuntimeError("SHIPCUBE_DATABASE_URL is not set (postgresql://user@host:port/db)")
    return url


class IamAuthConnection(psycopg.Connection):
    """Cloud SQL IAM database authentication without a proxy sidecar: each new connection sends a
    fresh OAuth token for the service's own account as the password. Used on Cloud Run with the
    built-in Cloud SQL socket (SHIPCUBE_DB_IAM_AUTH=1). Tokens only matter at connect time, and the
    pool recycles connections hourly (max_lifetime), so expiry never interrupts a session."""

    _creds: Any = None

    @classmethod
    def connect(cls, conninfo: str = "", **kwargs: Any) -> IamAuthConnection:  # type: ignore[override]
        import google.auth
        import google.auth.transport.requests

        if cls._creds is None:
            cls._creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/sqlservice.login"])
        if not cls._creds.valid:
            cls._creds.refresh(google.auth.transport.requests.Request())
        kwargs["password"] = cls._creds.token
        return super().connect(conninfo, **kwargs)  # type: ignore[return-value]


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        iam = os.environ.get("SHIPCUBE_DB_IAM_AUTH") == "1"
        _pool = ConnectionPool(dsn(), min_size=1, max_size=int(os.environ.get("SHIPCUBE_DB_POOL", "10")),
                               kwargs={"row_factory": dict_row, "autocommit": False}, open=True,
                               name="shipcube", max_lifetime=3000,
                               connection_class=IamAuthConnection if iam else psycopg.Connection)
    return _pool


def close() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


APP = "shipcube_app"
WORKER = "shipcube_worker"
_ROLES = {APP, WORKER}


@contextmanager
def tx(caller: Any | None = None, *, role: str | None = None, settings: dict[str, str] | None = None
       ) -> Iterator[psycopg.Connection]:
    """One transaction. With a caller, row-level security sees that caller (as shipcube_app)."""
    role = APP if caller is not None else role
    with pool().connection() as conn:
        with conn.transaction():
            if role and os.environ.get("SHIPCUBE_DB_SET_ROLE", "1") == "1":
                if role not in _ROLES:
                    raise ValueError(f"unknown role {role}")
                conn.execute(f"set local role {role}")  # fixed role names only
            if caller is not None:
                conn.execute("select set_config('app.user_id', %s, true), set_config('app.workspace_id', %s, true),"
                             " set_config('app.via', %s, true)",
                             (str(caller.user_id), str(caller.workspace_id), caller.via))
            for key, value in (settings or {}).items():
                conn.execute("select set_config(%s, %s, true)", (key, value))
            yield conn


def worker_tx() -> Any:
    return tx(role=WORKER)


class SchemaNotCurrent(RuntimeError):
    pass


def pending_migrations() -> list[str]:
    """Migration files not yet applied (or applied with different contents)."""
    with tx() as conn:  # the login's own role: roles may not exist yet on a fresh database
        exists = conn.execute("select to_regclass('public.schema_migrations') is not null as ok").fetchone()["ok"]
        done = {r["version"]: r["sha256"] for r in conn.execute("select version, sha256 from schema_migrations")} if exists else {}
    out = []
    for f in sorted(MIGRATIONS.glob("*.sql")):
        if done.get(f.stem) != hashlib.sha256(f.read_bytes()).hexdigest():
            out.append(f.stem if f.stem not in done else f"{f.stem} (changed after it was applied)")
    return out


def check_schema(*, migrate_if_dev: bool) -> None:
    pending = pending_migrations()
    if not pending:
        return
    if migrate_if_dev:
        log.warning("DEV_MODE: applying migrations at startup", extra={"pending": pending})
        migrate()
        return
    raise SchemaNotCurrent(f"database schema is not current; pending: {', '.join(pending)}. "
                           "Apply them with infra/db/migrate.sh (python -m shipcube_api.admin migrate as sc-migrator).")


def migrate(conn_str: str | None = None) -> list[str]:
    """Apply pending migrations. Each file runs in its own transaction, guarded by an advisory lock."""
    applied: list[str] = []
    with psycopg.connect(conn_str or dsn(), autocommit=True) as conn:
        conn.execute("select pg_advisory_lock(72031)")
        try:
            conn.execute("create table if not exists schema_migrations (version text primary key, sha256 text not null,"
                         " applied_at timestamptz not null default now())")
            done = {r[0]: r[1] for r in conn.execute("select version, sha256 from schema_migrations")}
            for f in sorted(MIGRATIONS.glob("*.sql")):
                sql = f.read_text()
                digest = hashlib.sha256(f.read_bytes()).hexdigest()
                if f.stem in done:
                    if done[f.stem] != digest:
                        raise RuntimeError(f"migration {f.name} changed after it was applied; add a new migration instead")
                    continue
                with conn.transaction():
                    conn.execute(sql)
                    conn.execute("insert into schema_migrations (version, sha256) values (%s, %s)", (f.stem, digest))
                applied.append(f.stem)
                log.info("applied migration", extra={"migration": f.stem})
        finally:
            conn.execute("select pg_advisory_unlock(72031)")
    return applied


if __name__ == "__main__":
    print("applied:", migrate() or "nothing")
