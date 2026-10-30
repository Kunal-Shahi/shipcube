"""Workspace-level console data: the approvals inbox, AI providers (BYOK), billing and linked GCP
projects. Request-side logic in the caller's transaction, like store.py."""

from __future__ import annotations

import calendar
import hashlib
import ipaddress
import json
import uuid
from datetime import UTC, datetime, timedelta
from functools import cache
from typing import Any
from urllib.parse import urlparse

from . import db
from .auth import Caller
from .deploy import isolation
from .deploy.base import Deployer
from .models import Operation
from .secret_store import SecretStore, make_secret_store
from .settings import settings
from .store import Conflict, NotFound, _iso, _slug, op_view

PROVIDERS: dict[str, dict[str, Any]] = {
    "meta": {"label": "Meta Muse", "model": "muse-spark-1.3", "key": False,
             "description": "ShipCube's own key through the AI gateway. Usage counts against your workspace's caps."},
    "openai-compatible": {"label": "OpenAI-compatible endpoint", "model": None, "key": True,
                          "description": "Any server that speaks the OpenAI chat API (vLLM, Ollama, Together, Groq, ...). Needs a base URL."},
    "openai": {"label": "OpenAI", "model": "gpt-4o-mini", "key": True, "description": "Your own OpenAI API key."},
    "anthropic": {"label": "Anthropic", "model": "claude-haiku-5-5", "key": True, "description": "Your own Anthropic API key."},
}
COMPUTE = ("requests", "vcpu_seconds", "gib_seconds", "build_minutes")
LLM = ("ai_input_tokens", "ai_output_tokens")
DATA = ("egress_bytes",)


@cache
def secret_store() -> SecretStore:
    return make_secret_store(settings)


def _label(pid: str) -> str:
    return f"ai-provider-{pid}"  # also the key_secret name PUT /v1/apps/{app}/ai accepts (store.provider_key_label)


def key_payload(pid: str, key: str, base_url: str | None) -> str:
    """What's stored in Secret Manager for a BYOK key: the key bound to its provider and, for
    openai-compatible, the endpoint a person saved it with. The gateway refuses to send the key
    anywhere else (services/gateway, boundKey)."""
    return json.dumps({"shipcube_ai_key": 1, "provider": pid.replace("-", "_"), "base_url": base_url, "key": key})


# --- approvals inbox --------------------------------------------------------------------


def list_operations(caller: Caller, status: str | None, limit: int) -> list[Operation]:
    with db.tx(caller) as conn:
        rows = conn.execute("select * from operations where workspace_id = %s and (%s::text is null or status = %s)"
                            " order by created_at desc limit %s", (caller.workspace_id, status, status, limit)).fetchall()
        return [op_view(r, _slug(conn, r["app_id"])) for r in rows]


# --- AI providers ---------------------------------------------------------------------------


def list_providers(caller: Caller) -> list[dict[str, Any]]:
    with db.tx(caller) as conn:
        default = conn.execute("select provider, model, base_url, updated_at from provider_configs where workspace_id = %s"
                               " and app_id is null", (caller.workspace_id,)).fetchone()
        keys = {r["label"]: r for r in conn.execute(
            "select label, last4, created_at from secret_refs where workspace_id = %s and app_id is null and kind = 'ai_key'"
            " and destroyed_at is null", (caller.workspace_id,)).fetchall()}
    current = default["provider"].replace("_", "-") if default else "meta"
    out = []
    for pid, p in PROVIDERS.items():
        key = keys.get(_label(pid))
        is_default = pid == current
        out.append({
            "id": pid, "label": p["label"], "description": p["description"],
            "connected": not p["key"] or key is not None, "default": is_default,
            "key_hint": f"…{key['last4']}" if key and key["last4"] else None,
            "base_url": default["base_url"] if is_default and default else None,
            "model": (default["model"] if is_default and default else None) or p["model"],
            "added_at": _iso(key["created_at"]) if key else None,
        })
    return out


def _check_base_url(url: str) -> str:
    u = urlparse(url)
    if u.scheme not in ("https", "http") or not u.hostname or u.username or u.password:
        raise Conflict("base_url must be an https URL")
    if not settings.dev_mode:  # local stacks may point at Ollama on http://localhost
        if u.scheme == "http":
            raise Conflict("base_url must use https")
        host = u.hostname.lower()
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = None
        if host == "localhost" or host.endswith((".localhost", ".internal", ".local")) or (ip is not None and not ip.is_global):
            raise Conflict("base_url must be a public endpoint")  # the gateway also checks the resolved address
    return url.rstrip("/")


def save_provider(caller: Caller, pid: str, key: str | None, base_url: str | None, model: str | None) -> dict[str, Any]:
    """Store the key (if given) and make this provider the workspace default for apps."""
    if pid not in PROVIDERS:
        raise NotFound(f"no AI provider {pid}; one of {', '.join(PROVIDERS)}")
    p = PROVIDERS[pid]
    if not p["key"] and key:
        raise Conflict(f"{pid} uses ShipCube's key; don't send one")
    if pid == "openai-compatible":
        if not base_url:
            raise Conflict("openai-compatible needs base_url")
        base_url = _check_base_url(base_url)
        if not key:  # the stored key is bound to the endpoint it was saved with
            with db.tx(caller) as conn:
                saved = conn.execute("select base_url from provider_configs where workspace_id = %s and app_id is null"
                                     " and provider = 'openai_compatible'", (caller.workspace_id,)).fetchone()
            if saved is None or saved["base_url"] != base_url:
                raise Conflict("re-enter the key to use a different endpoint: a key only goes to the endpoint it was saved with")
    else:
        base_url = None
    model = model or p["model"]
    if not model:
        raise Conflict(f"{pid} needs a model name")

    new_ref = None
    if key:
        # ws-ai-*: the gateway may read only these (#64's IAM condition), never warehouse credentials
        secret_id = f"ws-ai-{caller.workspace_id}-{uuid.uuid4()}"
        resource, version = secret_store().put(secret_id, key_payload(pid, key, base_url),
                                               {"workspace": caller.workspace_id, "kind": "ai_key"})
        new_ref = (uuid.uuid4(), resource, version)
    replaced = []
    try:
        with db.tx(caller) as conn:
            if new_ref:
                replaced = conn.execute(
                    "update secret_refs set destroyed_at = now() where workspace_id = %s and app_id is null and label = %s"
                    " and destroyed_at is null returning id, resource_name, version",
                    (caller.workspace_id, _label(pid))).fetchall()
                conn.execute(
                    "insert into secret_refs (id, workspace_id, kind, label, resource_name, version, last4, fingerprint,"
                    " created_by) values (%s,%s,'ai_key',%s,%s,%s,%s,%s,%s)",
                    (new_ref[0], caller.workspace_id, _label(pid), new_ref[1], new_ref[2], key[-4:],
                     hashlib.sha256(key.encode()).digest(), caller.user_id))
                if replaced:  # apps pinned to the old key move to the new one
                    conn.execute("update provider_configs set secret_ref_id = %s where secret_ref_id = any(%s)",
                                 (new_ref[0], [r["id"] for r in replaced]))
                secret_id = new_ref[0]
            elif p["key"]:
                ref = conn.execute("select id from secret_refs where workspace_id = %s and app_id is null and label = %s"
                                   " and destroyed_at is null", (caller.workspace_id, _label(pid))).fetchone()
                if ref is None:
                    raise Conflict(f"{pid} needs an API key")
                secret_id = ref["id"]
            else:
                secret_id = None
            conn.execute(
                "insert into provider_configs (id, workspace_id, app_id, provider, model, base_url, secret_ref_id, updated_by)"
                " values (%s,%s,null,%s,%s,%s,%s,%s) on conflict (workspace_id, coalesce(app_id, '00000000-0000-0000-0000-000000000000'::uuid))"
                " do update set provider = excluded.provider, model = excluded.model, base_url = excluded.base_url,"
                " secret_ref_id = excluded.secret_ref_id, updated_by = excluded.updated_by, updated_at = now()",
                (uuid.uuid4(), caller.workspace_id, pid.replace("-", "_"), model, base_url, secret_id, caller.user_id))
    except Exception:
        if new_ref:
            secret_store().destroy(new_ref[1], new_ref[2])  # don't leave an unreferenced key behind
        raise
    for r in replaced:
        secret_store().destroy(r["resource_name"], r["version"])
    return next(x for x in list_providers(caller) if x["id"] == pid)


def remove_provider(caller: Caller, pid: str) -> None:
    if pid not in PROVIDERS:
        raise NotFound(f"no AI provider {pid}")
    if not PROVIDERS[pid]["key"]:
        raise Conflict("ShipCube's Muse key can't be removed; it's the fallback for every app")
    with db.tx(caller) as conn:
        ref = conn.execute("select id, resource_name, version from secret_refs where workspace_id = %s and app_id is null"
                           " and label = %s and destroyed_at is null", (caller.workspace_id, _label(pid))).fetchone()
        if ref is None:
            raise NotFound(f"{pid} has no key stored")
        apps = [r["slug"] for r in conn.execute("select a.slug from provider_configs p join apps a on a.id = p.app_id"
                                                " where p.secret_ref_id = %s", (ref["id"],)).fetchall()]
        if apps:
            raise Conflict(f"apps {', '.join(apps)} use this key; switch them to another provider first")
        conn.execute("delete from provider_configs where workspace_id = %s and app_id is null and secret_ref_id = %s",
                     (caller.workspace_id, ref["id"]))  # the workspace falls back to Muse
        conn.execute("update secret_refs set destroyed_at = now() where id = %s", (ref["id"],))
    secret_store().destroy(ref["resource_name"], ref["version"])


# --- linked GCP projects ----------------------------------------------------------------------


def _link_view(r: dict[str, Any]) -> dict[str, Any]:
    return {"id": str(r["id"]), "project_id": r["project_id"], "region": r["region"], "status": r["status"],
            "deployer_sa": r["deployer_sa"], "runtime_sa": r["runtime_sa"],
            "missing_permissions": list(r["missing_permissions"] or []),
            "linked_at": _iso(r["created_at"]), "verified_at": _iso(r["verified_at"])}


def gcp_targets(deployer: Deployer, caller: Caller) -> dict[str, Any]:
    """The workspace's links, and the deployer SA link-project.sh grants roles to. The SA is created
    here, so it exists by the time the console shows the script."""
    deployer_sa = isolation.link_deployer_sa(settings, caller.workspace_id)
    deployer.ensure_link_deployer(caller.workspace_id, deployer_sa)
    with db.tx(caller) as conn:
        rows = conn.execute("select * from gcp_links where workspace_id = %s and status <> 'revoked' order by created_at",
                            (caller.workspace_id,)).fetchall()
    return {"deployer_sa": deployer_sa, "targets": [_link_view(r) for r in rows]}


def verified_projects(caller: Caller) -> list[str]:
    """Project ids of the workspace's active links (testIamPermissions passed); for shipcube-data,
    which only accepts BigQuery warehouses in these projects."""
    with db.tx(caller) as conn:
        return [r["project_id"] for r in conn.execute(
            "select project_id from gcp_links where workspace_id = %s and status = 'active' order by created_at",
            (caller.workspace_id,)).fetchall()]


def link_project(deployer: Deployer, caller: Caller, project_id: str, region: str) -> dict[str, Any]:
    """Record the link and check it as the workspace's deployer. Missing permissions leave it
    'broken' with the list, so the console can show what link-project.sh still has to grant."""
    deployer_sa = isolation.link_deployer_sa(settings, caller.workspace_id)
    deployer.ensure_link_deployer(caller.workspace_id, deployer_sa)
    missing = deployer.verify_link(project_id, deployer_sa)
    status = "active" if not missing else "broken"
    with db.tx(caller) as conn:
        prior = conn.execute("select id from gcp_links where workspace_id = %s and project_id = %s and status <> 'revoked'",
                             (caller.workspace_id, project_id)).fetchone()
        if prior:
            row = conn.execute("update gcp_links set status = %s, missing_permissions = %s, region = %s,"
                               " verified_at = case when %s then now() else verified_at end where id = %s returning *",
                               (status, missing, region, not missing, prior["id"])).fetchone()
        else:
            row = conn.execute(
                "insert into gcp_links (id, workspace_id, project_id, deployer_sa, runtime_sa, region, status,"
                " missing_permissions, linked_by, verified_at) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning *",
                (uuid.uuid4(), caller.workspace_id, project_id, deployer_sa,
                 f"shipcube-apps@{project_id}.iam.gserviceaccount.com", region, status, missing, caller.user_id,
                 datetime.now(UTC) if not missing else None)).fetchone()
    return _link_view(row)


# --- billing ------------------------------------------------------------------------------------


def billing(caller: Caller) -> dict[str, Any]:
    today = datetime.now(UTC).date()
    month_start = today.replace(day=1)
    with db.tx(caller) as conn:
        by_metric = conn.execute(
            "select a.slug, a.gcp_link_id, u.metric, sum(u.cost_usd) as cost from usage_daily u join apps a on a.id = u.app_id"
            " where u.workspace_id = %s and u.day >= %s group by 1, 2, 3", (caller.workspace_id, month_start)).fetchall()
        daily = {r["day"]: float(r["cost"]) for r in conn.execute(
            "select day, sum(cost_usd) as cost from usage_daily where workspace_id = %s and day > %s group by day",
            (caller.workspace_id, today - timedelta(days=30))).fetchall()}
        used = conn.execute("select coalesce(sum(u.cost_usd), 0) as cost from usage_daily u join apps a on a.id = u.app_id"
                            " where u.workspace_id = %s and a.gcp_link_id is null", (caller.workspace_id,)).fetchone()["cost"]
        apps = conn.execute("select slug, gcp_link_id from apps where workspace_id = %s and purged_at is null order by slug",
                            (caller.workspace_id,)).fetchall()
        link = conn.execute("select * from gcp_links where workspace_id = %s and status = 'active' order by created_at limit 1",
                            (caller.workspace_id,)).fetchone()
    per_app: dict[str, dict[str, Any]] = {
        a["slug"]: {"app": a["slug"], "billing": "own-gcp" if a["gcp_link_id"] else "shipcube",
                    "compute_usd": 0.0, "data_usd": 0.0, "llm_usd": 0.0} for a in apps}
    for r in by_metric:
        bucket = "compute_usd" if r["metric"] in COMPUTE else "llm_usd" if r["metric"] in LLM else "data_usd"
        per_app.setdefault(r["slug"], {"app": r["slug"], "billing": "own-gcp" if r["gcp_link_id"] else "shipcube",
                                       "compute_usd": 0.0, "data_usd": 0.0, "llm_usd": 0.0})[bucket] += float(r["cost"])
    mtd = sum(float(r["cost"]) for r in by_metric)
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    return {
        "credits": {"granted_usd": settings.credit_grant_usd, "used_usd": round(float(used), 4),
                    "expires_at": settings.credit_expires_at},
        "month_to_date_usd": round(mtd, 4),
        "forecast_usd": round(mtd / today.day * days_in_month, 4),
        "by_app": [{**v, **{k: round(v[k], 4) for k in ("compute_usd", "data_usd", "llm_usd")}} for v in per_app.values()],
        "daily": [{"date": (d := today - timedelta(days=i)).isoformat(), "cost_usd": round(daily.get(d, 0.0), 4)}
                  for i in range(29, -1, -1)],
        "gcp": _link_view(link) if link else None,
        "source": "usage_daily (Cloud Monitoring + gateway usage, written by the worker)",
    }

