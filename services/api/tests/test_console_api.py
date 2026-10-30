"""Console endpoints (issue #9): approvals inbox, AI providers (BYOK), billing, linked GCP projects,
recently deleted apps, and the hosted target reported as `local` on the local backend."""

import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from shipcube_api import db, store
from shipcube_api.settings import settings

from .conftest import new_workspace, wait_op

GIT = {"kind": "git", "url": "https://github.com/example/app"}
KEY = "sk-test-0123456789abcdefWXYZ"


@pytest.fixture
def ws():
    return new_workspace()


def deployed(client, ws, name):
    r = client.post(f"/v1/apps/{name}/deployments", json={"source": GIT}, headers=ws["agent"])
    assert wait_op(client, ws["agent"], r.json()["id"])["status"] == "succeeded"


def secrets_file() -> dict:
    p = Path(settings.local_secrets_file)
    return json.loads(p.read_text()) if p.exists() else {}


def test_approvals_inbox_lists_pending_operations_newest_first(client, ws):
    deployed(client, ws, "inbox-app")
    first = client.patch("/v1/apps/inbox-app/access", headers=ws["agent"], json={"add": [{"principal": "domain:acme.com"}]})
    second = client.delete("/v1/apps/inbox-app", headers=ws["agent"])
    inbox = client.get("/v1/operations", params={"status": "awaiting_approval"}, headers=ws["human"]).json()["operations"]
    assert [o["id"] for o in inbox] == [second.json()["id"], first.json()["operation"]["id"]]
    assert all(o["approval_url"] and o["app"] == "inbox-app" for o in inbox)
    assert client.get("/v1/operations", params={"status": "awaiting_approval"}, headers=new_workspace()["agent"]).json() == {"operations": []}

    client.post(f"/v1/operations/{second.json()['id']}/reject", json={}, headers=ws["human"])
    inbox = client.get("/v1/operations", params={"status": "awaiting_approval"}, headers=ws["agent"]).json()["operations"]
    assert [o["id"] for o in inbox] == [first.json()["operation"]["id"]]
    assert len(client.get("/v1/operations", params={"limit": 2}, headers=ws["agent"]).json()["operations"]) == 2
    assert client.get("/v1/operations", params={"status": "nope"}, headers=ws["agent"]).status_code == 400


def test_byok_keys_are_stored_in_the_secret_store_and_never_returned(client, ws):
    providers = {p["id"]: p for p in client.get("/v1/ai/providers", headers=ws["agent"]).json()["providers"]}
    assert set(providers) == {"meta", "openai-compatible", "openai", "anthropic"}
    assert providers["meta"]["connected"] and providers["meta"]["default"] and not providers["openai"]["connected"]

    # Only a person in the console stores keys; an agent's token can't.
    assert client.put("/v1/ai/providers/openai", json={"key": KEY}, headers=ws["agent"]).status_code == 403
    r = client.put("/v1/ai/providers/openai", json={"key": KEY}, headers=ws["human"])
    assert r.status_code == 200, r.text
    assert KEY not in r.text and r.json()["key_hint"] == "…WXYZ" and r.json()["connected"] and r.json()["default"]
    assert KEY not in client.get("/v1/ai/providers", headers=ws["agent"]).text
    with db.tx() as conn:
        ref = conn.execute("select * from secret_refs where workspace_id = %s and label = 'ai-provider-openai'"
                           " and destroyed_at is null", (ws["workspace_id"],)).fetchone()
    assert KEY.encode() not in json.dumps({k: str(v) for k, v in ref.items()}).encode()
    secret_id = ref["resource_name"].rsplit("/", 1)[-1]
    assert secrets_file()[secret_id] == KEY

    # A new key replaces the old one, which is destroyed in the store.
    client.put("/v1/ai/providers/openai", json={"key": KEY[:-4] + "ABCD", "model": "gpt-4.1"}, headers=ws["human"])
    assert secret_id not in secrets_file()
    p = {x["id"]: x for x in client.get("/v1/ai/providers", headers=ws["agent"]).json()["providers"]}["openai"]
    assert p["key_hint"] == "…ABCD" and p["model"] == "gpt-4.1"

    # Removing it falls back to Muse.
    assert client.delete("/v1/ai/providers/openai", headers=ws["human"]).status_code == 204
    p = {x["id"]: x for x in client.get("/v1/ai/providers", headers=ws["agent"]).json()["providers"]}
    assert not p["openai"]["connected"] and p["meta"]["default"]
    assert KEY[:-4] + "ABCD" not in secrets_file().values()


def test_provider_rules(client, ws):
    h = ws["human"]
    assert client.put("/v1/ai/providers/openai-compatible", json={"key": KEY}, headers=h).status_code == 409  # base_url
    assert client.put("/v1/ai/providers/openai-compatible", json={"key": KEY, "base_url": "ftp://x"}, headers=h).status_code == 409
    r = client.put("/v1/ai/providers/openai-compatible", json={"key": KEY, "base_url": "https://llm.example.com/v1/",
                                                                "model": "llama-3.3"}, headers=h)
    assert r.status_code == 200 and r.json()["base_url"] == "https://llm.example.com/v1"
    assert client.put("/v1/ai/providers/anthropic", json={}, headers=h).status_code == 409  # no key stored yet
    assert client.put("/v1/ai/providers/meta", json={"key": KEY}, headers=h).status_code == 409
    assert client.put("/v1/ai/providers/meta", json={}, headers=h).json()["default"]
    assert client.delete("/v1/ai/providers/meta", headers=h).status_code == 409
    assert client.put("/v1/ai/providers/gemini", json={"key": KEY}, headers=h).status_code == 400

    # A key an app is pinned to can't be removed out from under it.
    deployed(client, ws, "byok-app")
    client.put("/v1/ai/providers/openai", json={"key": KEY}, headers=h)
    assert client.put("/v1/apps/byok-app/ai", json={"provider": "openai", "model": "gpt-4o-mini",
                                                    "key_secret": "ai-provider-openai"}, headers=ws["agent"]).status_code == 200
    r = client.delete("/v1/ai/providers/openai", headers=h)
    assert r.status_code == 409 and "byok-app" in r.json()["error"]["message"]


def test_billing_sums_usage(client, ws):
    deployed(client, ws, "bill-app")
    today = datetime.now(UTC).date()
    with db.tx() as conn:
        app_id = conn.execute("select id from apps where workspace_id = %s and slug = 'bill-app'", (ws["workspace_id"],)).fetchone()["id"]
        for metric, cost in (("vcpu_seconds", 1.25), ("ai_output_tokens", 0.5), ("egress_bytes", 0.05)):
            conn.execute("insert into usage_daily (workspace_id, app_id, day, metric, quantity, cost_usd) values (%s,%s,%s,%s,1,%s)",
                         (ws["workspace_id"], app_id, today, metric, cost))
    b = client.get("/v1/billing", headers=ws["agent"]).json()
    assert b["month_to_date_usd"] == 1.8 and b["credits"]["used_usd"] == 1.8
    assert b["by_app"] == [{"app": "bill-app", "billing": "shipcube", "compute_usd": 1.25, "data_usd": 0.05, "llm_usd": 0.5}]
    assert len(b["daily"]) == 30 and b["daily"][-1] == {"date": today.isoformat(), "cost_usd": 1.8}
    assert b["forecast_usd"] >= b["month_to_date_usd"] and b["gcp"] is None
    assert client.get("/v1/billing", headers=new_workspace()["agent"]).json()["month_to_date_usd"] == 0


def test_linking_a_gcp_project(client, ws):
    t = client.get("/v1/gcp-targets", headers=ws["agent"]).json()
    ws12 = uuid.UUID(ws["workspace_id"]).hex[:12]
    assert t == {"deployer_sa": f"ws-{ws12}-deployer@shipcube-links-ks.iam.gserviceaccount.com", "targets": []}
    assert client.get("/v1/me", headers=ws["agent"]).json()["deployer_sa"] == t["deployer_sa"]
    assert client.post("/v1/gcp-targets", json={"project_id": "my-proj-123"}, headers=ws["agent"]).status_code == 403
    assert client.post("/v1/gcp-targets", json={"project_id": "Bad_Project"}, headers=ws["human"]).status_code == 400

    broken = client.post("/v1/gcp-targets", json={"project_id": "broken-proj-1"}, headers=ws["human"]).json()
    assert broken["status"] == "broken" and broken["missing_permissions"] == ["run.services.create"]
    ok = client.post("/v1/gcp-targets", json={"project_id": "my-proj-123", "region": "europe-west1"}, headers=ws["human"])
    assert ok.status_code == 201 and ok.json()["status"] == "active" and ok.json()["verified_at"]
    again = client.post("/v1/gcp-targets", json={"project_id": "my-proj-123"}, headers=ws["human"]).json()
    assert again["id"] == ok.json()["id"]
    targets = client.get("/v1/gcp-targets", headers=ws["agent"]).json()["targets"]
    assert {x["project_id"] for x in targets} == {"broken-proj-1", "my-proj-123"}
    me = client.get("/v1/me", headers=ws["agent"]).json()
    assert [g["project"] for g in me["gcp_targets"] if g["kind"] == "linked"] == ["my-proj-123"]
    assert client.get("/v1/billing", headers=ws["agent"]).json()["gcp"]["project_id"] == "my-proj-123"
    from shipcube_api import auth, workspace

    caller = auth.verify(ws["agent"]["Authorization"].removeprefix("Bearer "), None)
    assert workspace.verified_projects(caller) == ["my-proj-123"]  # broken links don't count


def test_recently_deleted_apps(client, ws):
    deployed(client, ws, "gone-app")
    op = client.delete("/v1/apps/gone-app", headers=ws["agent"]).json()
    client.post(f"/v1/operations/{op['id']}/approve", json={}, headers=ws["human"])
    wait_op(client, ws["agent"], op["id"])
    assert "gone-app" not in [a["name"] for a in client.get("/v1/apps", headers=ws["agent"]).json()["apps"]]
    apps = client.get("/v1/apps", params={"include_deleted": True}, headers=ws["agent"]).json()["apps"]
    gone = next(a for a in apps if a["name"] == "gone-app")
    assert gone["deleted_at"] and datetime.fromisoformat(gone["restorable_until"].replace("Z", "+00:00")) > datetime.now(UTC) + timedelta(days=6)


def test_hosted_target_is_reported_as_local_on_the_local_backend(client, ws, monkeypatch):
    deployed(client, ws, "where-app")
    assert client.get("/v1/apps/where-app", headers=ws["agent"]).json()["target"] == "shipcube"
    monkeypatch.setattr(store, "settings", replace(settings, backend="local"))
    a = client.get("/v1/apps/where-app", headers=ws["agent"]).json()
    assert a["target"] == "local" and a["region"] == "local"
    assert client.get("/v1/apps", headers=ws["agent"]).json()["apps"][0]["target"] == "local"
    assert client.get("/v1/me", headers=ws["agent"]).json()["gcp_targets"][0]["kind"] == "local"


def test_deploy_records_its_load_estimate_on_the_app(client, ws):
    src = {"kind": "inline", "files": [{"path": "main.py", "content": "print(1)"}]}
    est = client.post("/v1/estimates", json={"source": src, "app": "est-app"}, headers=ws["agent"]).json()
    est = wait_op(client, ws["agent"], est["id"])
    assert est["status"] == "succeeded", est
    estimate_id = est["result"]["estimate_id"]

    r = client.post("/v1/apps/est-app/deployments", json={"source": GIT, "estimate_id": "est_000000000000"}, headers=ws["agent"])
    assert r.status_code == 409  # unknown estimates are refused, not ignored
    r = client.post("/v1/apps/est-app/deployments", json={"source": GIT, "estimate_id": estimate_id}, headers=ws["agent"])
    assert wait_op(client, ws["agent"], r.json()["id"])["status"] == "succeeded"
    a = client.get("/v1/apps/est-app", headers=ws["agent"]).json()
    e = a["estimate"]
    assert e["estimate_id"] == estimate_id and e["workload_class"] == est["result"]["workload_class"]
    assert e["tier"] == est["result"]["tier"] == a["tier"] and e["confidence"] == est["result"]["confidence"]
    assert e["monthly_cost_usd"] == est["result"]["monthly_cost_usd"] and e["explained_by"] == est["result"]["explained_by"]

    # Without estimate_id, the latest finished estimate for that app name is used; redeploys don't duplicate it.
    r = client.post("/v1/apps/est-app/deployments", json={"source": GIT}, headers=ws["agent"])
    wait_op(client, ws["agent"], r.json()["id"])
    with db.tx() as conn:
        n = conn.execute("select count(*) as n from load_estimates where workspace_id = %s", (ws["workspace_id"],)).fetchone()["n"]
        dep = conn.execute("select load_estimate_id from deployments where workspace_id = %s order by created_at desc limit 1",
                           (ws["workspace_id"],)).fetchone()
    assert n == 1 and dep["load_estimate_id"] is not None
    deployed(client, ws, "no-est-app")
    assert client.get("/v1/apps/no-est-app", headers=ws["agent"]).json()["estimate"] is None
