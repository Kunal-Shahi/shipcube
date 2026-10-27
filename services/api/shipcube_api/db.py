"""Postgres access: a connection pool, versioned migrations and per-transaction caller context.

Schema: migrations/*.sql, applied in order and recorded in `schema_migrations`. 0001 is security's
design verbatim (docs/research/database-design.md); later files only add to it.

Row-level security: API requests run in `tx(caller)`, which sets `app.user_id`,
`app.workspace_id` and `app.via` for the transaction and (when SHIPCUBE_DB_SET_ROLE=1, the
default) switches to the `shipcube_app` role so the policies apply even when the connection user
owns the tables. The worker uses `tx()` without a caller, as the `shipcube_worker` role (BYPASSRLS).
On Cloud SQL each service connects as its own IAM database user mapped to those roles.
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


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(dsn(), min_size=1, max_size=int(os.environ.get("SHIPCUBE_DB_POOL", "10")),
                               kwargs={"row_factory": dict_row, "autocommit": False}, open=True,
                               name="shipcube")
    return _pool


def close() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def tx(caller: Any | None = None, *, role: str | None = None) -> Iterator[psycopg.Connection]:
    """One transaction. With a caller, row-level security sees that caller."""
    with pool().connection() as conn:
        with conn.transaction():
            if caller is not None:
                conn.execute("select set_config('app.user_id', %s, true), set_config('app.workspace_id', %s, true),"
                             " set_config('app.via', %s, true)",
                             (str(caller.user_id), str(caller.workspace_id), caller.via))
                if os.environ.get("SHIPCUBE_DB_SET_ROLE", "1") == "1":
                    conn.execute("set local role shipcube_app")
            elif role:
                conn.execute(f"set local role {role}")  # noqa: S608 — fixed role names only
            yield conn


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
                digest = hashlib.sha256(sql.encode()).hexdigest()
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
