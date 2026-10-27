"""Unit tests for discovery, and integration tests against a real Postgres (DATABASE_URL).

CI starts a postgres:16 service container. Locally:
    DATABASE_URL=postgresql://localhost/migrate_test uv run pytest
"""

from __future__ import annotations

import os
import shutil
import threading
import uuid
from pathlib import Path

import psycopg
import pytest

from shipcube_migrate import MigrationError, discover, run

FIX = Path(__file__).parent / "fixtures"
DB = os.environ.get("DATABASE_URL")
needs_db = pytest.mark.skipif(not DB, reason="set DATABASE_URL to run against a real Postgres")


def test_discover_orders_and_checksums():
    ms = discover(FIX / "ok")
    assert [m.version for m in ms] == [1, 2, 3]
    assert ms[2].transactional is False and ms[0].transactional is True
    assert len(ms[0].checksum) == 64


def test_discover_rejects_gaps_and_bad_names(tmp_path):
    with pytest.raises(MigrationError, match="expected migration 0002"):
        discover(FIX / "bad")
    (tmp_path / "1_x.sql").write_text("select 1;")
    with pytest.raises(MigrationError, match="NNNN_snake_name"):
        discover(tmp_path)


@pytest.fixture
def db():
    """A fresh schema per test, so tests don't see each other's tables."""
    schema = f"t_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(DB, autocommit=True) as c:
        c.execute(f"create schema {schema}")
    yield psycopg.conninfo.make_conninfo(DB, options=f"-c search_path={schema}")
    with psycopg.connect(DB, autocommit=True) as c:
        c.execute(f"drop schema {schema} cascade")


@needs_db
def test_apply_is_idempotent(db):
    assert [m.version for m in run(db, FIX / "ok")] == [1, 2, 3]
    assert run(db, FIX / "ok") == []
    with psycopg.connect(db) as c:
        assert c.execute("select count(*) from schema_migrations").fetchone()[0] == 3
        c.execute("insert into widgets (id, name, color) values (1, 'a', 'red')")


@needs_db
def test_dry_run_changes_nothing(db):
    assert len(run(db, FIX / "ok", dry_run=True)) == 3
    assert len(run(db, FIX / "ok", dry_run=True)) == 3


@needs_db
def test_edited_migration_is_refused(db, tmp_path):
    shutil.copytree(FIX / "ok", tmp_path / "m")
    run(db, tmp_path / "m")
    (tmp_path / "m" / "0001_widgets.sql").write_text("create table widgets (id bigint primary key);\n")
    with pytest.raises(MigrationError, match="changed after it was applied"):
        run(db, tmp_path / "m")


@needs_db
def test_failed_migration_rolls_back_only_itself(db, tmp_path):
    d = tmp_path / "m"
    d.mkdir()
    (d / "0001_ok.sql").write_text("create table ok (id int);\n")
    (d / "0002_broken.sql").write_text("create table half (id int);\nselect * from no_such_table;\n")
    with pytest.raises(MigrationError, match="0002_broken failed"):
        run(db, d)
    with psycopg.connect(db) as c:
        assert c.execute("select version from schema_migrations").fetchall() == [(1,)]
        assert c.execute("select to_regclass('half')").fetchone()[0] is None


@needs_db
def test_concurrent_runs_apply_once(db):
    results, errors = [], []

    def go():
        try:
            results.append(len(run(db, FIX / "ok")))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=go) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and sorted(results) == [0, 0, 3]
