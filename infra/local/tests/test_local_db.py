"""Tenant-isolation tests against the local stack (or CI's Postgres), with the real service logins.

Run through infra/local/test.sh, which points LOCAL_DB at the database and runs this with the
API's own environment (services/api). Covers what can run without GCP:

- PAT login: tokens minted as sc-worker, verified as sc-api through auth.verify_pat (RLS with only
  app.token_hmac set). A tampered secret fails; each token resolves to its own workspace.
- RLS: as workspace A's caller, B's rows are invisible.
Build isolation through the local deployer: test_local_build.py.
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PROJECT = "shipcube-control-ks"


def _local_env() -> dict[str, str]:
    path = Path(os.environ.get("SHIPCUBE_LOCAL_DIR", ROOT / ".local")) / "secrets.env"
    if not path.exists():
        pytest.skip("no .local/secrets.env: run `python3 infra/local/localsecrets.py init`")
    out = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k] = v
    return out


ENV = _local_env()
os.environ["SHIPCUBE_TOKEN_PEPPER"] = ENV["SHIPCUBE_TOKEN_PEPPER"]
os.environ.setdefault("SHIPCUBE_DEV_MODE", "1")  # mock deployer; never set in production


def _url(name: str) -> str:
    """A service's URL from secrets.env, re-pointed from compose's db:5432 to LOCAL_DB."""
    return re.sub(r"@db:5432/", f"@{os.environ.get('LOCAL_DB', '127.0.0.1:54329')}/", ENV[name])


from shipcube_api import admin, auth, db


def _as(login_url: str) -> None:
    db.close()
    os.environ["SHIPCUBE_DATABASE_URL"] = _url(login_url)


@pytest.fixture(scope="module")
def tenants():
    """Two workspaces, each with an owner and a PAT, created as sc-worker; removed afterwards."""
    _as("SHIPCUBE_WORKER_DATABASE_URL")
    tag = uuid.uuid4().hex[:8]
    out = {}
    for k in ("a", "b"):
        user_id, ws_id, _ = admin.bootstrap(f"iso-{k}-{tag}@example.test", f"iso-{k}-{tag}", f"Isolation {k}", None)
        out[k] = {"user": user_id, "ws": ws_id, "pat": auth.mint_pat(user_id, ws_id, ["apps:read"], name="isolation-test")}
    yield out
    _as("SHIPCUBE_WORKER_DATABASE_URL")
    with db.worker_tx() as conn:
        for t in out.values():
            for table in ("api_tokens", "caps", "memberships"):
                conn.execute(f"delete from {table} where workspace_id = %s", (t["ws"],))
            conn.execute("delete from workspaces where id = %s", (t["ws"],))
            conn.execute("delete from users where id = %s", (t["user"],))
    db.close()


def test_logins_start_in_their_roles():
    for name, role in (("SHIPCUBE_API_DATABASE_URL", "shipcube_app"), ("SHIPCUBE_WORKER_DATABASE_URL", "shipcube_worker"),
                       ("SHIPCUBE_MIGRATOR_DATABASE_URL", "shipcube_owner")):
        _as(name)
        with db.tx() as conn:
            row = conn.execute("select current_user as r, (select rolsuper from pg_roles where rolname = session_user) as su").fetchone()
        assert (row["r"], row["su"]) == (role, False), name


def test_pat_login_as_api(tenants):
    _as("SHIPCUBE_API_DATABASE_URL")
    for k in ("a", "b"):
        caller = auth.verify_pat(tenants[k]["pat"])
        assert caller is not None and caller.workspace_id == tenants[k]["ws"] and caller.user_id == tenants[k]["user"]
    pat = tenants["a"]["pat"]
    assert auth.verify_pat(pat[:-4] + ("AAAA" if not pat.endswith("AAAA") else "BBBB")) is None


def test_rls_hides_other_workspace(tenants):
    _as("SHIPCUBE_API_DATABASE_URL")
    caller = auth.verify_pat(tenants["a"]["pat"])
    with db.tx(caller) as conn:
        # Tenant tables only: users, workspaces and memberships have no RLS by design (§5); the API
        # filters those itself. rls_and_token.sql covers every RLS table with fixtures.
        for table in ("api_tokens", "caps"):
            own = conn.execute(f"select count(*) as n from {table} where workspace_id = %s", (tenants["a"]["ws"],)).fetchone()["n"]
            other = conn.execute(f"select count(*) as n from {table} where workspace_id = %s", (tenants["b"]["ws"],)).fetchone()["n"]
            assert own > 0 and other == 0, table
