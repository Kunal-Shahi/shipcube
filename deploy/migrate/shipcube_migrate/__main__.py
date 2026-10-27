"""shipcube-migrate [up|status|verify] [--dir DIR]

Connection, from the environment:
- DATABASE_URL, a libpq URL or DSN (local and CI), or
- PGHOST (e.g. /cloudsql/PROJECT:REGION:INSTANCE), PGDATABASE, PGUSER and PG_IAM_AUTH=1: the password
  is an OAuth access token for the job's service account from the metadata server (Cloud SQL IAM
  database authentication; no stored password).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.request
from pathlib import Path

import psycopg

from . import MigrationError, applied, discover, run, verify


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"severity": record.levelname, "message": record.getMessage(), "logger": record.name}
        for k in ("version", "migration"):
            if hasattr(record, k):
                out[k] = getattr(record, k)
        return json.dumps(out)


def dsn() -> str:
    if url := os.environ.get("DATABASE_URL"):
        return url
    if os.environ.get("PG_IAM_AUTH") == "1":
        req = urllib.request.Request(
            "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
            headers={"Metadata-Flavor": "Google"})
        with urllib.request.urlopen(req, timeout=5) as r:
            token = json.load(r)["access_token"]
        return psycopg.conninfo.make_conninfo(password=token, sslmode="disable")  # the unix socket is the proxy
    return ""  # libpq PG* variables


def main() -> None:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[h])
    p = argparse.ArgumentParser(prog="shipcube-migrate")
    p.add_argument("command", choices=["up", "status", "verify"])
    p.add_argument("--dir", default=os.environ.get("MIGRATIONS_DIR", "db/migrations"), type=Path)
    a = p.parse_args()
    log = logging.getLogger("shipcube_migrate")
    try:
        if a.command == "up":
            done = run(dsn(), a.dir)
            log.info(f"applied {len(done)} migration(s)")
        else:
            migrations = discover(a.dir)
            with psycopg.connect(dsn()) as conn:
                pending = verify(migrations, applied(conn))
            log.info(f"{len(migrations) - len(pending)} applied, {len(pending)} pending: "
                     + ", ".join(f"{m.version:04d}_{m.name}" for m in pending))
            if a.command == "verify" and pending:
                sys.exit(3)
    except (MigrationError, psycopg.Error) as e:
        log.error(str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
