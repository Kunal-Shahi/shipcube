"""Versioned, forward-only SQL migrations for ShipCube's Postgres.

Files are `NNNN_snake_name.sql`, numbered from 0001 with no gaps. Each runs once, in its own
transaction, recorded in `schema_migrations` with a SHA-256 of its text. Applied files must never
change: a checksum mismatch stops the run (fix forward with a new migration instead). A session
advisory lock makes concurrent runs safe. A file whose first line is `-- migrate:no-transaction`
runs outside a transaction (for CREATE INDEX CONCURRENTLY and the like).
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

import psycopg

log = logging.getLogger("shipcube_migrate")

FILENAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")
LOCK_KEY = 0x5C0BE_0001  # pg_advisory_lock key reserved for migrations
NO_TX = "-- migrate:no-transaction"

TABLE = """
create table if not exists schema_migrations (
  version    integer primary key,
  name       text not null,
  checksum   text not null,
  applied_at timestamptz not null default now(),
  applied_by text not null default current_user,
  duration_ms integer not null
)
"""


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str
    checksum: str

    @property
    def transactional(self) -> bool:
        return not self.sql.lstrip().startswith(NO_TX)


def discover(directory: Path) -> list[Migration]:
    """Read and validate the migration files: naming, numbering from 1 without gaps, non-empty."""
    out: list[Migration] = []
    for p in sorted(directory.iterdir()):
        if p.suffix != ".sql":
            continue
        m = FILENAME.match(p.name)
        if not m:
            raise MigrationError(f"{p.name}: migration files must be named NNNN_snake_name.sql")
        sql = p.read_text()
        if not sql.strip():
            raise MigrationError(f"{p.name} is empty")
        out.append(Migration(int(m[1]), m[2], sql, hashlib.sha256(sql.encode()).hexdigest()))
    for expected, mig in enumerate(out, start=1):
        if mig.version != expected:
            raise MigrationError(f"expected migration {expected:04d}, found {mig.version:04d}_{mig.name}")
    return out


def applied(conn: psycopg.Connection) -> dict[int, tuple[str, str]]:
    conn.execute(TABLE)
    rows = conn.execute("select version, name, checksum from schema_migrations order by version").fetchall()
    return {v: (n, c) for v, n, c in rows}


def verify(migrations: list[Migration], done: dict[int, tuple[str, str]]) -> list[Migration]:
    """Check applied migrations are unchanged; return the pending ones."""
    files = {m.version: m for m in migrations}
    for version, (name, checksum) in done.items():
        mig = files.get(version)
        if mig is None:
            raise MigrationError(f"database has migration {version:04d}_{name}, which is missing from the files")
        if mig.checksum != checksum:
            raise MigrationError(
                f"{version:04d}_{mig.name} changed after it was applied; never edit an applied migration, "
                "add a new one instead")
    return [m for m in migrations if m.version not in done]


def _lock(conn: psycopg.Connection, timeout_s: float) -> None:
    """Take the migration lock by polling pg_try_advisory_lock. A blocking pg_advisory_lock would
    sit in an open statement whose snapshot CREATE INDEX CONCURRENTLY waits for: a deadlock."""
    deadline = time.monotonic() + timeout_s
    while not conn.execute("select pg_try_advisory_lock(%s)", (LOCK_KEY,)).fetchone()[0]:
        if time.monotonic() > deadline:
            raise MigrationError(f"another migration run held the lock for over {timeout_s:.0f}s")
        time.sleep(0.5)


def run(dsn: str, directory: Path, dry_run: bool = False, lock_timeout_s: float = 600) -> list[Migration]:
    """Apply pending migrations. Returns what was (or, with dry_run, would be) applied."""
    migrations = discover(directory)
    with psycopg.connect(dsn, autocommit=True) as conn:
        _lock(conn, lock_timeout_s)
        try:
            pending = verify(migrations, applied(conn))
            if dry_run:
                return pending
            for mig in pending:
                started = time.monotonic()
                log.info("applying migration", extra={"version": mig.version, "migration": mig.name})
                try:
                    if mig.transactional:
                        with conn.transaction():
                            conn.execute(mig.sql)
                            _record(conn, mig, started)
                    else:
                        conn.execute(mig.sql)
                        _record(conn, mig, started)
                except psycopg.Error as e:
                    raise MigrationError(f"{mig.version:04d}_{mig.name} failed: {e}") from e
            return pending
        finally:
            conn.execute("select pg_advisory_unlock(%s)", (LOCK_KEY,))


def _record(conn: psycopg.Connection, mig: Migration, started: float) -> None:
    conn.execute(
        "insert into schema_migrations (version, name, checksum, duration_ms) values (%s, %s, %s, %s)",
        (mig.version, mig.name, mig.checksum, int((time.monotonic() - started) * 1000)),
    )
