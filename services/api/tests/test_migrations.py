"""Schema and row-level security as security designed it (0001, 0003) plus networking's 0002 and 0004."""

import hashlib

import pytest

from shipcube_api import auth, db

from .conftest import new_workspace, wait_op

GIT = {"kind": "git", "url": "https://github.com/example/app"}


def test_every_migration_is_applied_with_its_checksum(client):
    with db.tx() as conn:
        applied = {r["version"]: r["sha256"] for r in conn.execute("select version, sha256 from schema_migrations")}
    files = sorted(db.MIGRATIONS.glob("*.sql"))
    assert [f.stem for f in files] == ["0001_security_schema", "0002_operations_runner",
                                       "0003_tenant_rls_and_rate_counters", "0004_api_token_lookup",
                                       "0005_lock_migration_ledger", "0006_profiler_guard", "0007_gateway_counters",
                                       "0008_waitlist", "0009_waitlist_consent"]
    for f in files:
        assert applied[f.stem] == hashlib.sha256(f.read_bytes()).hexdigest()
    assert db.pending_migrations() == []


def test_service_roles_can_read_but_not_forge_the_migration_ledger(client):
    import psycopg

    for role in (db.APP, db.WORKER):
        with db.tx(role=role) as conn:
            assert conn.execute("select count(*) as n from schema_migrations").fetchone()["n"] >= 5  # check_schema reads it
        for stmt in ("insert into schema_migrations (version, sha256) values ('9999_forged', 'x')",
                     "update schema_migrations set sha256 = 'x'", "delete from schema_migrations", "truncate schema_migrations"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege), db.tx(role=role) as conn:
                conn.execute(stmt)


def test_tenant_tables_are_isolated_for_the_api_role_and_open_to_the_worker(client):
    a, b = new_workspace(), new_workspace()
    op = client.post("/v1/apps/rls-ops/deployments", json={"source": GIT}, headers=a["agent"]).json()
    wait_op(client, a["agent"], op["id"])
    caller_b = auth.verify(b["agent"]["Authorization"].split()[1])
    with db.tx(caller_b) as conn:
        for table in ("operations", "deployments", "caps", "api_tokens", "gcp_links", "provider_configs"):
            n = conn.execute(f"select count(*) as n from {table} where workspace_id = %s", (a["workspace_id"],)).fetchone()["n"]
            assert n == 0, table
    with db.worker_tx() as conn:
        assert conn.execute("select current_user").fetchone()["current_user"] == "shipcube_worker"
        assert conn.execute("select count(*) as n from operations where id = %s", (op["id"],)).fetchone()["n"] == 1


def test_token_lookup_sees_only_the_presented_token(client):
    ws = new_workspace()
    secret = ws["agent"]["Authorization"].split()[1].split("_", 3)[3]
    with db.tx(role=db.APP) as conn:
        assert conn.execute("select count(*) as n from api_tokens").fetchone()["n"] == 0
    with db.tx(role=db.APP, settings={"app.token_hmac": auth.digest(secret).hex()}) as conn:
        assert conn.execute("select count(*) as n from api_tokens").fetchone()["n"] == 1
    with db.tx(role=db.APP, settings={"app.token_hmac": auth.digest(secret + "x").hex()}) as conn:
        assert conn.execute("select count(*) as n from api_tokens").fetchone()["n"] == 0


def test_production_refuses_to_start_with_pending_migrations(monkeypatch):
    monkeypatch.setattr(db, "pending_migrations", lambda: ["0005_future"])
    with pytest.raises(db.SchemaNotCurrent, match="migrate.sh"):
        db.check_schema(migrate_if_dev=False)
