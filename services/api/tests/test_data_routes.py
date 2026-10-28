"""The console's Data page endpoints (/v1/data/*), served by shipcube-data's router on this API.

Runs when shipcube-data is installed (the MCP image's `data` extra); real auth, real Postgres."""

import os
import uuid

import pytest

local = pytest.importorskip("shipcube_data.local")

from .conftest import new_workspace  # noqa: E402


@pytest.fixture
def seeded(tmp_path):
    w = new_workspace(scopes=["apps:read", "data:read", "data:migrate"])
    local.seed(local.Settings(catalog_url=os.environ["SHIPCUBE_DATA_CATALOG_URL"], workspace=str(w["workspace_id"]),
                              duckdb_path=str(tmp_path / "olist.duckdb"), tenants={"example.com": "acme"}))
    return w


def agent_caller(w):
    from shipcube_data.identity import Caller

    return Caller(workspace_id=uuid.UUID(str(w["workspace_id"])), via="mcp", email=w["email"], user_id=uuid.UUID(str(w["user_id"])),
                  membership_role="owner", token_id=uuid.uuid4(), scopes=["data:read", "data:migrate"])


def test_data_page_reads(client, seeded):
    h = seeded["human"]
    w = client.get("/v1/data/warehouse", headers=h).json()
    assert w["connected"] and w["model_version"] == 5
    cubes = client.get("/v1/data/cubes", headers=h).json()
    assert {c["name"] for c in cubes} == {"customers", "states", "orders", "products", "order_items"}
    assert client.get("/v1/data/cubes/orders", headers=h).json()["schema"] == "tenant_acme"
    migrations = client.get("/v1/data/migrations", headers=h).json()
    assert len(migrations) == 5 and {m["status"] for m in migrations} == {"applied"}
    # A PAT with data:read reads too.
    assert client.get("/v1/data/cubes", headers=seeded["agent"]).status_code == 200


def test_scopes_and_workspaces(client, seeded):
    other = new_workspace(scopes=["apps:read"])
    r = client.get("/v1/data/cubes", headers=other["agent"])
    assert r.status_code == 403 and r.json()["error"]["code"] == "permission_denied"
    # Another workspace has no catalog of its own and never sees this one's.
    r = client.get("/v1/data/migrations", headers=other["human"])
    assert r.status_code == 404 and r.json()["error"]["code"] == "catalog_not_migrated"


def test_only_a_different_person_applies(client, seeded):
    from shipcube_data.local import tools_from_env

    mid = tools_from_env().call("data_propose_migration", {
        "description": "Buyers", "ops": [{"op": "add_measure", "name": "orders.buyers", "sql": "customer_id", "agg": "count_distinct"}],
    }, agent_caller(seeded))["id"]
    m = client.get(f"/v1/data/migrations/{mid}", headers=seeded["human"]).json()
    assert m["status"] == "proposed" and m["diff"]["added"] == ["members:orders.buyers"]
    # The agent's PAT can't apply; the person behind it can't approve their own agent's proposal.
    assert client.post(f"/v1/data/migrations/{mid}:apply", headers=seeded["agent"]).status_code == 403
    r = client.post(f"/v1/data/migrations/{mid}:apply", headers=seeded["human"], json={"allow_breaking": False})
    assert r.status_code == 403 and "person who proposed" in r.json()["error"]["message"]
    r = client.post(f"/v1/data/migrations/{mid}:reject", headers=seeded["human"], json={"note": "not this week"})
    assert r.status_code == 200 and r.json()["status"] == "rejected" and r.json()["rejected"]["note"] == "not this week"


def test_record_secret_keeps_only_a_reference(client, seeded):
    from shipcube_api import auth, data_layer, db

    caller = auth.verify(seeded["human"]["Authorization"].removeprefix("Bearer "))
    ref = data_layer.record_secret(caller, kind="warehouse_credential", label="warehouse-primary",
                                   resource_name=f"projects/local/secrets/ws-x-warehouse_credential-{uuid.uuid4()}", version="1",
                                   last4="t0k3"[-4:], fingerprint=b"\x01" * 32)
    again = data_layer.record_secret(caller, kind="warehouse_credential", label="warehouse-primary",
                                     resource_name=f"projects/local/secrets/ws-x-warehouse_credential-{uuid.uuid4()}", version="1",
                                     last4="abcd", fingerprint=b"\x02" * 32)
    with db.tx(caller) as conn:
        rows = conn.execute("select id, kind, last4, destroyed_at from secret_refs where label = 'warehouse-primary'"
                            " order by created_at").fetchall()
    assert [str(r["id"]) for r in rows] == [ref, again]
    assert rows[0]["destroyed_at"] is not None and rows[1]["destroyed_at"] is None  # the old one is retired
    assert {r["kind"] for r in rows} == {"warehouse_credential"}


def test_put_warehouse_is_console_admin_only(client, seeded):
    body = {"kind": "postgres", "host": "10.1.2.3", "port": 5432, "database": "w", "user": "r", "password": "pw", "ssl": "verify-full"}
    assert client.put("/v1/data/warehouse", headers=seeded["agent"], json=body).status_code == 403
    r = client.post("/v1/data/warehouse:test", headers=seeded["human"], json=body)
    assert r.status_code == 403 and "non-public" in r.json()["error"]["message"]  # private hosts are refused



def test_put_warehouse_end_to_end_with_the_api_secret_store(client, tmp_path, monkeypatch):
    """Save a Postgres warehouse through the API's own secret store (#35) and secret_refs, then read it back."""
    workspace = pytest.importorskip("shipcube_api.workspace")
    from psycopg.conninfo import conninfo_to_dict

    from shipcube_api import auth, db
    from shipcube_api.settings import settings

    pg = conninfo_to_dict(os.environ["SHIPCUBE_DATABASE_URL"])
    host = pg.get("host") or "localhost"
    if host.startswith("/"):
        pytest.skip("needs a TCP Postgres host for the warehouse test")
    monkeypatch.setenv("SHIPCUBE_DATA_ALLOWED_DB_HOSTS", host)
    monkeypatch.setenv("SHIPCUBE_LOCAL_SECRETS_FILE", str(settings.local_secrets_file))  # where the API's store writes
    w = new_workspace(scopes=["apps:read", "data:read"])
    base = os.environ["SHIPCUBE_DATABASE_URL"].rsplit("/", 1)[0]
    warehouse_db = f"wh_{uuid.uuid4().hex[:8]}"
    local.seed(local.Settings(catalog_url=os.environ["SHIPCUBE_DATA_CATALOG_URL"], workspace=str(w["workspace_id"]), warehouse="postgres",
                              warehouse_url=f"{base}/{warehouse_db}?sslmode=disable", tenants={"example.com": "acme"}))
    body = {"kind": "postgres", "host": host, "port": int(pg.get("port") or 5432), "database": warehouse_db,
            "user": pg.get("user") or "postgres", "password": pg.get("password") or "local-pw", "ssl": "disable"}
    tested = client.post("/v1/data/warehouse:test", headers=w["human"], json=body)
    assert tested.status_code == 200 and tested.json()["tables"] > 0, tested.text
    saved = client.put("/v1/data/warehouse", headers=w["human"], json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["warehouse"]["connected"] and {"label": "Status", "value": "reachable"} in saved.json()["warehouse"]["details"]
    caller = auth.verify(w["human"]["Authorization"].removeprefix("Bearer "))
    with db.tx(caller) as conn:
        refs = conn.execute("select kind, resource_name, last4 from secret_refs where workspace_id = %s and destroyed_at is null"
                            " and kind = 'warehouse_credential'", (caller.workspace_id,)).fetchall()
    assert len(refs) == 1 and refs[0]["resource_name"].startswith(f"projects/local/secrets/ws-{w['workspace_id']}-warehouse_credential-")
    assert workspace.verified_projects(caller) == []  # nothing linked: BigQuery is refused
    bq = {"kind": "bigquery", "project": "p", "location": "US", "service_account_key": "{}"}
    assert client.post("/v1/data/warehouse:test", headers=w["human"], json=bq).status_code == 403
