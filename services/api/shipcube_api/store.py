"""Control-plane state in Postgres (security's schema, migrations/) and the request-side logic.

The API writes desired state and enqueues operations in one transaction; the durable worker
(worker.py) does everything that touches a cloud. Approvals: an operation that needs a human is
created as `awaiting_approval` with a row in `approvals`, and only reaches the queue once a console
user approves it (docs/research/networking.md §2.5).
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from psycopg.types.json import Jsonb

from . import db, profiler_guard
from .auth import Caller
from .deploy import isolation
from .deploy.base import AppRef, Deployer, DeployError, Target
from .models import OpError, Operation, Progress, Step
from .settings import settings

CONSOLE_URL = settings.console_url
HOSTED = "shipcube"
RESTORE_WINDOW = timedelta(days=7)
APPROVAL_TTL = timedelta(hours=72)
TERMINAL = ("succeeded", "failed", "cancelled")

STEPS: dict[str, list[tuple[str, str]]] = {
    "estimate": [("profile", "Profiling the app (sandbox, or static analysis)"),
                 ("place", "Classifying, sizing and pricing; Muse Spark explains")],
    "deploy": [
        ("validate", "Validating and staging the source"),
        ("build", "Cloud Build: building the container with buildpacks"),
        ("push", "Image published to Artifact Registry"),
        ("deploy", "Creating the Cloud Run revision (gen2, scale to zero, IAP on)"),
        ("configure_access", "Applying the access list as IAP bindings"),
        ("health_check", "Checking the revision is ready"),
    ],
    "rollback": [("shift_traffic", "Sending all traffic to the earlier revision"),
                 ("health_check", "Checking the revision is ready")],
    "delete": [("scale_to_zero", "Removing access and external ingress"),
               ("soft_delete", "Marking deleted; restorable for 7 days")],
    "restore": [("restore", "Restoring ingress and the access list")],
    "purge": [("purge", "Deleting the service, its service account and its images")],
    "set_caps": [("apply", "Applying caps to the Cloud Run service")],
    "set_access": [("apply", "Applying the access list as IAP bindings")],
}

# API cap name -> security's caps.kind, and back.
CAP_KIND = {"max_instances": "max_instances", "concurrency": "concurrency", "cpu": "cpu", "memory": "memory_mib",
            "timeout_s": "request_timeout_s", "requests_per_minute": "requests_per_minute",
            "monthly_spend_usd": "spend_usd_per_month"}
KIND_CAP = {v: k for k, v in CAP_KIND.items()}
CAP_PERIOD = {"requests_per_minute": "minute", "spend_usd_per_month": "month", "deploys_per_day": "day"}
WORKSPACE_DEFAULTS: dict[str, float] = {
    "max_instances": 2, "concurrency": 80, "cpu": 1, "memory_mib": 512, "request_timeout_s": 60,
    "requests_per_minute": 120, "spend_usd_per_month": 5, "max_apps": 3, "deploys_per_day": 20,
}
ENFORCED_BY = {
    "max_instances": "Cloud Run --max-instances",
    "concurrency": "Cloud Run --concurrency",
    "requests_per_minute": "Not enforced on app traffic while apps use IAP directly; enforced on AI gateway calls",
    "monthly_spend_usd": "ShipCube cost meter -> max-instances=0; billing budget as backstop",
    "cpu": "Cloud Run resource limits",
    "memory": "Cloud Run resource limits",
    "timeout_s": "Cloud Run request timeout",
}
# One approval per operation; `approvals.action` holds the most severe (database-design.md, Oct 28).
APPROVAL_PRECEDENCE = ["delete_app", "first_linked_deploy", "domain_grant", "cap_raise"]


class Conflict(Exception):
    pass


class NotFound(Exception):
    pass


class LimitExceeded(Exception):
    pass


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    return v.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def cap_to_db(key: str, v: Any) -> float:
    if key == "memory":
        s = str(v)
        return float(s[:-2]) * (1024 if s.endswith("Gi") else 1)
    return float(v)


def cap_from_db(kind: str, v: Any) -> Any:
    key = KIND_CAP.get(kind, kind)
    f = float(v)
    if key == "memory":
        return f"{int(f)}Mi"
    if key == "cpu":
        return "0.5" if f == 0.5 else str(int(f))
    if key == "monthly_spend_usd":
        return f
    return int(f)


def caps_above_default(conn, workspace_id: str, caps: dict[str, Any]) -> list[tuple[str, str]]:
    defaults = workspace_caps(conn, workspace_id)
    out = []
    for k, v in caps.items():
        kind = CAP_KIND.get(k)
        if v is None or kind is None or kind not in defaults:
            continue
        if cap_to_db(k, v) > defaults[kind]:
            out.append(("cap_raise", f"{k} {v} is above the workspace default {cap_from_db(kind, defaults[kind])}"))
    return out


def workspace_caps(conn, workspace_id: str) -> dict[str, float]:
    rows = conn.execute("select kind, limit_value from caps where workspace_id = %s and app_id is null",
                        (workspace_id,)).fetchall()
    return {**WORKSPACE_DEFAULTS, **{r["kind"]: float(r["limit_value"]) for r in rows}}


def app_caps(conn, workspace_id: str, app_id: str) -> dict[str, Any]:
    eff = workspace_caps(conn, workspace_id)
    for r in conn.execute("select kind, limit_value from caps where app_id = %s", (app_id,)).fetchall():
        eff[r["kind"]] = float(r["limit_value"])
    return {k: cap_from_db(CAP_KIND[k], eff[CAP_KIND[k]]) for k in CAP_KIND}


def write_app_caps(conn, workspace_id: str, app_id: str, caps: dict[str, Any], by: str | None) -> None:
    for k, v in caps.items():
        if v is None or k not in CAP_KIND:
            continue
        kind = CAP_KIND[k]
        conn.execute(
            "insert into caps (id, workspace_id, app_id, kind, limit_value, period, set_by) values (%s,%s,%s,%s,%s,%s,%s)"
            " on conflict (workspace_id, coalesce(app_id, '00000000-0000-0000-0000-000000000000'::uuid), kind)"
            " do update set limit_value = excluded.limit_value, set_by = excluded.set_by, updated_at = now()",
            (uuid.uuid4(), workspace_id, app_id, kind, cap_to_db(k, v), CAP_PERIOD.get(kind), by))


# --- targets ---------------------------------------------------------------------------


def hosted_target() -> str:
    """What "hosted" means to the console: ShipCube's GCP project, or Docker on this machine."""
    return "local" if settings.backend == "local" else HOSTED


def targets(conn, workspace_id: str) -> list[dict[str, Any]]:
    out = [{"id": HOSTED, "project": "local" if settings.backend == "local" else settings.apps_project,
            "kind": hosted_target(), "default": True}]
    for r in conn.execute("select id, project_id from gcp_links where workspace_id = %s and status = 'active'"
                          " order by created_at", (workspace_id,)).fetchall():
        out.append({"id": str(r["id"]), "project": r["project_id"], "kind": "linked", "default": False})
    return out


def target_for(conn, gcp_link_id: Any) -> Target:
    if gcp_link_id is None:
        return Target(id=HOSTED, kind="shipcube", project=settings.apps_project)
    r = conn.execute("select * from gcp_links where id = %s", (gcp_link_id,)).fetchone()
    if r is None or r["status"] != "active":
        raise Conflict("the linked GCP project is not active")
    return Target(id=str(r["id"]), kind="linked", project=r["project_id"], deployer_sa=r["deployer_sa"],
                  builder_sa=f"shipcube-builder@{r['project_id']}.iam.gserviceaccount.com",
                  shared_runtime_sa=r["runtime_sa"])


def app_ref(conn, app: dict[str, Any]) -> AppRef:
    return AppRef(name=app["slug"], workspace_id=str(app["workspace_id"]), target=target_for(conn, app["gcp_link_id"]),
                  region=app["region"])


# --- reads ----------------------------------------------------------------------------


def me(caller: Caller) -> dict[str, Any]:
    with db.tx(caller) as conn:
        ws = conn.execute("select id, name from workspaces where id = %s", (caller.workspace_id,)).fetchone()
        caps = workspace_caps(conn, caller.workspace_id)
        return {
            "user_id": caller.user_id, "email": caller.email, "role": caller.role,
            "workspace": {"id": str(ws["id"]), "name": ws["name"]},
            "gcp_targets": targets(conn, caller.workspace_id),
            "limits": {"max_apps": int(caps["max_apps"]), "monthly_spend_usd": caps["spend_usd_per_month"]},
            "token": {"id": caller.token_id or "console", "scopes": sorted(caller.scopes), "expires_at": caller.expires_at},
            "deployer_sa": isolation.link_deployer_sa(settings, caller.workspace_id),
        }


def _app_row(conn, workspace_id: str, slug: str, include_deleted: bool = False) -> dict[str, Any]:
    q = "select * from apps where workspace_id = %s and slug = %s and purged_at is null"
    if not include_deleted:
        q += " and deleted_at is null"
    row = conn.execute(q + " order by deleted_at nulls first, created_at desc limit 1", (workspace_id, slug)).fetchone()
    if row is None:
        raise NotFound(f"app {slug} not found")
    return row


def current_deployment(conn, app_id: Any) -> dict[str, Any] | None:
    return conn.execute("select * from deployments where app_id = %s and status = 'succeeded' order by finished_at desc"
                        " limit 1", (app_id,)).fetchone()


def grants(conn, app_id: Any) -> list[dict[str, Any]]:
    return conn.execute("select * from access_grants where app_id = %s and revoked_at is null order by created_at",
                        (app_id,)).fetchall()


def _app_view(conn, app: dict[str, Any], creator_email: str | None = None) -> dict[str, Any]:
    dep = current_deployment(conn, app["id"])
    creator = creator_email or conn.execute("select email from users where id = %s", (app["created_by"],)).fetchone()["email"]
    ai = conn.execute("select provider, model, s.label as key_secret from provider_configs p"
                      " left join secret_refs s on s.id = p.secret_ref_id where p.workspace_id = %s and"
                      " (p.app_id = %s or p.app_id is null) order by p.app_id nulls last limit 1",
                      (app["workspace_id"], app["id"])).fetchone()
    est = conn.execute("select * from load_estimates where app_id = %s order by created_at desc limit 1",
                       (app["id"],)).fetchone()
    history = conn.execute("select revision, status, created_at from deployments where app_id = %s and revision is not null"
                           " order by created_at desc limit 5", (app["id"],)).fetchall()
    return {
        "name": app["slug"], "url": app["url"] or "", "tier": app["tier"],
        "target": hosted_target() if app["gcp_link_id"] is None else str(app["gcp_link_id"]),
        # The local backend runs every app in Docker, linked ones included (deploy/local.py).
        "region": "local" if settings.backend == "local" else app["region"],
        "status": app["status"],
        "revision": (dep or {}).get("revision") or "", "image": (dep or {}).get("image_digest") or "",
        "caps": app_caps(conn, str(app["workspace_id"]), app["id"]),
        "access": [{"principal": f"{g['principal_type']}:{g['principal']}",
                    "role": "owner" if g["principal_type"] == "user" and g["principal"] == creator else "viewer",
                    "granted_at": _iso(g["created_at"]), "sync_state": g["sync_state"]} for g in grants(conn, app["id"])],
        "ai": {"provider": ai["provider"], "model": ai["model"], "key_secret": ai["key_secret"]} if ai
        else {"provider": "meta", "model": "muse-spark", "key_secret": None},
        "updated_at": _iso(dep["finished_at"] if dep and dep["finished_at"] else app["created_at"]),
        "deleted_at": _iso(app["deleted_at"]), "restorable_until": _iso(app["purge_after"]),
        "estimate": {"estimate_id": est["reasoning"].get("estimate_id"), "workload_class": est["workload_class"],
                     "tier": est["recommended_tier"], "confidence": float(est["confidence"]) if est["confidence"] is not None else None,
                     "monthly_cost_usd": est["reasoning"].get("monthly_cost_usd") or {"expected": float(est["monthly_cost_usd"] or 0)},
                     "explained_by": est["reasoning"].get("explained_by"), "created_at": _iso(est["created_at"])} if est else None,
        "revisions": [{"revision": h["revision"], "status": h["status"], "created_at": _iso(h["created_at"])} for h in history],
    }


def get_app(caller: Caller, slug: str, include_deleted: bool = False) -> dict[str, Any]:
    with db.tx(caller) as conn:
        return _app_view(conn, _app_row(conn, caller.workspace_id, slug, include_deleted))


def get_app_for_telemetry(caller: Caller, slug: str) -> dict[str, Any]:
    """The app view plus where it runs (project, Cloud Run service, id), for the monitoring provider."""
    with db.tx(caller) as conn:
        row = _app_row(conn, caller.workspace_id, slug)
        ref = app_ref(conn, row)
        return {**_app_view(conn, row), "gcp_project": ref.target.project,
                "service_name": row["service_name"] or ref.service, "app_id": str(row["id"])}


def app_ref_for(caller: Caller, slug: str, include_deleted: bool = True) -> tuple[AppRef, dict[str, Any]]:
    with db.tx(caller) as conn:
        row = _app_row(conn, caller.workspace_id, slug, include_deleted)
        return app_ref(conn, row), row


def list_apps(caller: Caller, include_deleted: bool = False) -> list[dict[str, Any]]:
    with db.tx(caller) as conn:
        rows = conn.execute("select a.*, d.finished_at from apps a left join lateral (select finished_at from deployments"
                            " where app_id = a.id and status = 'succeeded' order by finished_at desc limit 1) d on true"
                            " where a.workspace_id = %s and a.purged_at is null and (%s or a.deleted_at is null)"
                            " order by a.created_at", (caller.workspace_id, include_deleted)).fetchall()
        return [{"name": r["slug"], "url": r["url"] or "", "tier": r["tier"], "status": r["status"],
                 "updated_at": _iso(r["finished_at"] or r["created_at"]),
                 "target": hosted_target() if r["gcp_link_id"] is None else str(r["gcp_link_id"]),
                 "deleted_at": _iso(r["deleted_at"]), "restorable_until": _iso(r["purge_after"])} for r in rows]


# --- operations -------------------------------------------------------------------------


def op_view(row: dict[str, Any], app_slug: str | None) -> Operation:
    steps = row["steps"] or []
    done = row["status"] in TERMINAL
    cur = steps[min(row["step_index"], len(steps) - 1)]["name"] if steps else row["kind"]
    completed = sum(1 for s in steps if s["status"] == "succeeded")
    waiting = row["status"] == "awaiting_approval"
    return Operation(
        id=str(row["id"]), kind=row["kind"], app=app_slug, status=row["status"], done=done,
        progress=Progress(step=cur, completed_steps=completed, total_steps=len(steps),
                          message=row["progress_message"] or ("Done" if row["status"] == "succeeded" else row["status"])),
        steps=[Step(name=s["name"], status=s["status"]) for s in steps],
        result=row["result"], error=OpError(code=row["error_code"] or "error", message=row["error"]) if row["error"] else None,
        approval_url=row["approval_url"] if waiting else None,
        approval_reasons=row["approval_reasons"] or [], approval_actions=row["approval_actions"] or [],
        approval_expires_at=_iso(row["approval_expires_at"]) if waiting else None,
        poll_after_s=10 if waiting else 3 if row["kind"] == "deploy" else 2,
        created_at=_iso(row["created_at"]), updated_at=_iso(row["updated_at"]),
    )


def _slug(conn, app_id: Any) -> str | None:
    if app_id is None:
        return None
    r = conn.execute("select slug from apps where id = %s", (app_id,)).fetchone()
    return r["slug"] if r else None


def _request_hash(kind: str, app_id: Any, payload: dict[str, Any]) -> bytes:
    return hashlib.sha256(json.dumps([kind, str(app_id) if app_id else None, payload], sort_keys=True, default=str).encode()).digest()


def replay(conn, workspace_id: str, kind: str, app_id: Any, payload: dict[str, Any], idem: str | None
           ) -> tuple[Operation | None, str | None]:
    """Idempotency for operations that change an app's state (set_access, set_caps): (operation, key).

    A retry with the same key and body returns its operation, but only while that operation is still
    the app's latest of this kind. Once a later change has run (remove, add, remove again with the same
    derived key), the old operation describes a state the app no longer has, so the request is applied
    again as a new operation (key None) instead of returning a stale result (#19). The same key with a
    different body is a conflict, as in enqueue."""
    if idem is None:
        return None, None
    prior = conn.execute("select * from operations where workspace_id = %s and idem_key = %s", (workspace_id, idem)).fetchone()
    if prior is None:
        return None, idem
    if bytes(prior["request_hash"]) != _request_hash(kind, app_id, payload):
        raise Conflict("idempotency key reused with a different request")
    # Operations created in the prior's own transaction share its created_at and don't count as later.
    later = conn.execute("select 1 from operations where app_id = %s and kind = %s and created_at > %s limit 1",
                         (app_id, kind, prior["created_at"])).fetchone()
    if later is None:
        return op_view(prior, _slug(conn, prior["app_id"])), idem
    return None, None


def enqueue(conn, caller: Caller | None, workspace_id: str, kind: str, app_id: Any, target_project: str,
            payload: dict[str, Any], idem_key: str | None, approvals: list[tuple[str, str]] | None = None,
            summary: str | None = None, extra: dict[str, Any] | None = None) -> Operation:
    """Insert an operation (and its approval) in the caller's transaction. Same key + same body
    returns the existing operation; same key + different body is a conflict."""
    request_hash = _request_hash(kind, app_id, payload)
    key = idem_key or f"auto:{uuid.uuid4().hex}"
    prior = conn.execute("select * from operations where workspace_id = %s and idem_key = %s", (workspace_id, key)).fetchone()
    if prior is not None:
        if bytes(prior["request_hash"]) != request_hash:
            raise Conflict("idempotency key reused with a different request")
        return op_view(prior, _slug(conn, prior["app_id"]))
    op_id = uuid.uuid4()
    waiting = bool(approvals)
    steps = [{"name": n, "status": "pending"} for n, _ in STEPS[kind]]
    expires = datetime.now(UTC) + APPROVAL_TTL
    row = conn.execute(
        "insert into operations (id, workspace_id, app_id, kind, target_project, status, step, idem_key, request_hash,"
        " requested_by, token_id, input, steps, progress_message, approval_url, approval_reasons, approval_actions,"
        " approval_expires_at) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning *",
        (op_id, workspace_id, app_id, kind, target_project, "awaiting_approval" if waiting else "queued", steps[0]["name"],
         key, request_hash, caller.user_id if caller else None,
         caller.token_id if caller and caller.token_id else None, Jsonb({**payload, **(extra or {})}), Jsonb(steps),
         "Waiting for approval in the console" if waiting else "Queued",
         f"{CONSOLE_URL}/w/{workspace_id}/approvals/{op_id}" if waiting else None,
         [r for _, r in approvals or []], sorted({a for a, _ in approvals or []}), expires if waiting else None)).fetchone()
    if waiting:
        actions = {a for a, _ in approvals}
        action = next(a for a in APPROVAL_PRECEDENCE if a in actions)
        conn.execute(
            "insert into approvals (id, workspace_id, action, operation_id, app_id, summary, request, request_hash,"
            " requested_by, requested_via, requested_token, expires_at) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (uuid.uuid4(), workspace_id, action, op_id, app_id,
             summary or "; ".join(r for _, r in approvals), Jsonb({**payload, "actions": sorted(actions)}), request_hash,
             caller.user_id if caller else None, caller.via if caller else "console",
             caller.token_id if caller and caller.token_id else None, expires))
    return op_view(row, _slug(conn, app_id))


def get_operation(caller: Caller, op_id: str) -> Operation:
    with db.tx(caller) as conn:
        row = _op_row(conn, caller, op_id)
        return op_view(row, _slug(conn, row["app_id"]))


def _op_row(conn, caller: Caller, op_id: str) -> dict[str, Any]:
    try:
        uuid.UUID(op_id)
    except ValueError:
        raise NotFound(f"operation {op_id} not found") from None
    row = conn.execute("select * from operations where id = %s and workspace_id = %s", (op_id, caller.workspace_id)).fetchone()
    if row is None:
        raise NotFound(f"operation {op_id} not found")
    if row["status"] == "awaiting_approval" and row["approval_expires_at"] and row["approval_expires_at"] < datetime.now(UTC):
        row = _end_approval(conn, row, "expired", "approval_expired", "No decision within 72 hours", None)
    return row


def _end_approval(conn, row, approval_status: str, code: str, message: str, by: str | None) -> dict[str, Any]:
    conn.execute("update approvals set status = %s, decided_at = now(), decided_by = %s,"
                 " decided_via = case when %s::uuid is null then null else 'console' end"
                 " where operation_id = %s and status = 'pending'", (approval_status, by, by, row["id"]))
    return conn.execute("update operations set status = 'cancelled', error_code = %s, error = %s, progress_message = %s,"
                        " finished_at = now(), updated_at = now() where id = %s returning *",
                        (code, message, message, row["id"])).fetchone()


def cancel_operation(caller: Caller, op_id: str) -> Operation:
    with db.tx(caller) as conn:
        row = _op_row(conn, caller, op_id)
        if row["status"] == "awaiting_approval":
            row = _end_approval(conn, row, "cancelled", "approval_withdrawn", "Cancelled before a decision", None)
        elif row["status"] not in TERMINAL:
            row = conn.execute("update operations set cancel_requested = true, updated_at = now() where id = %s returning *",
                               (row["id"],)).fetchone()
        return op_view(row, _slug(conn, row["app_id"]))


def decide(caller: Caller, op_id: str, approve: bool, comment: str | None) -> Operation:
    with db.tx(caller) as conn:
        row = _op_row(conn, caller, op_id)
        if row["status"] != "awaiting_approval":
            raise Conflict(f"operation is {row['status']}, not awaiting_approval")
        if not approve:
            row = _end_approval(conn, row, "rejected", "approval_rejected", "Rejected in the console", caller.user_id)
            conn.execute("update approvals set decision_note = %s where operation_id = %s", (comment, row["id"]))
            return op_view(row, _slug(conn, row["app_id"]))
        conn.execute("update approvals set status = 'approved', decided_by = %s, decided_via = 'console', decided_at = now(),"
                     " decision_note = %s where operation_id = %s and status = 'pending'", (caller.user_id, comment, row["id"]))
        row = conn.execute("update operations set status = 'queued', approval_url = null, progress_message = 'Approved; queued',"
                           " next_attempt_at = now(), updated_at = now() where id = %s returning *", (row["id"],)).fetchone()
        return op_view(row, _slug(conn, row["app_id"]))


# --- request-side mutations ------------------------------------------------------------------


# The estimator reads the checkout inside the worker; git sources come back with the profiler
# sandbox's clone limits and symlink checks (#57). Uploads and images are unaffected.
GIT_ESTIMATES_PAUSED = True


def start_estimate(caller: Caller, payload: dict[str, Any], idem: str | None) -> Operation:
    source = payload.get("source") or {}
    if source.get("kind") == "git" and GIT_ESTIMATES_PAUSED:
        raise DeployError("invalid_argument", "estimates from git sources are paused; upload the code with create_upload instead")
    isolation.check_source(settings, caller.workspace_id, source)
    with db.tx(caller) as conn:
        replay = idem is not None and conn.execute("select 1 from operations where workspace_id = %s and idem_key = %s",
                                                   (caller.workspace_id, idem)).fetchone() is not None
        # Profiled or static (profiler_guard.admit), outside the idempotency hash. A replay returns the
        # operation that already exists, so it isn't admitted (or rate-limited) again.
        mode = {} if replay else profiler_guard.admit(conn, caller.workspace_id, payload)
        return enqueue(conn, caller, caller.workspace_id, "estimate", None, settings.apps_project, payload, idem, extra=mode)


def start_deploy(caller: Caller, slug: str, payload: dict[str, Any], idem: str | None) -> Operation:
    isolation.check_source(settings, caller.workspace_id, payload.get("source") or {})
    with db.tx(caller) as conn:
        target = payload.get("target") or HOSTED
        link_id = None if target == HOSTED else target
        if link_id is not None:
            try:
                uuid.UUID(link_id)
            except ValueError:
                raise Conflict(f"unknown target {target}; see whoami") from None
        tgt = target_for(conn, link_id)
        est = _finished_estimate(conn, caller, slug, payload.get("estimate_id"))
        tier = payload.get("tier") or (est["result"].get("tier") if est else None)
        caps_ws = workspace_caps(conn, caller.workspace_id)
        app = conn.execute("select * from apps where workspace_id = %s and slug = %s and deleted_at is null",
                           (caller.workspace_id, slug)).fetchone()
        if app is None:
            live = conn.execute("select count(*) as n from apps where workspace_id = %s and deleted_at is null",
                                (caller.workspace_id,)).fetchone()["n"]
            if live >= caps_ws["max_apps"]:
                raise LimitExceeded(f"workspace has {live} apps; the limit is {int(caps_ws['max_apps'])}")
            ref = AppRef(name=slug, workspace_id=caller.workspace_id, target=tgt, region=settings.region)
            app = conn.execute(
                "insert into apps (id, workspace_id, gcp_link_id, slug, name, tier, kind, service_name, region, runtime_sa,"
                " status, created_by) values (%s,%s,%s,%s,%s,%s,'service',%s,%s,%s,'draft',%s) returning *",
                (uuid.uuid4(), caller.workspace_id, link_id, slug, slug, tier or "T1", ref.service,
                 settings.region, ref.runtime_sa, caller.user_id)).fetchone()
            conn.execute("insert into access_grants (id, workspace_id, app_id, principal_type, principal, granted_by)"
                         " values (%s,%s,%s,'user',%s,%s)", (uuid.uuid4(), caller.workspace_id, app["id"], caller.email,
                                                            caller.user_id))
        elif (app["gcp_link_id"] and str(app["gcp_link_id"])) != link_id:
            raise Conflict(f"app {slug} lives on target {app['gcp_link_id'] or HOSTED}; delete it to move it")
        recent = conn.execute("select count(*) as n from deployments where workspace_id = %s and created_at > now() - interval '1 day'",
                              (caller.workspace_id,)).fetchone()["n"]
        if recent >= caps_ws["deploys_per_day"]:
            raise LimitExceeded(f"{recent} deploys in the last 24 hours; the limit is {int(caps_ws['deploys_per_day'])}")
        reasons = []
        if link_id and not conn.execute("select 1 from deployments d join apps a on a.id = d.app_id where a.gcp_link_id = %s"
                                        " and d.status = 'succeeded' limit 1", (link_id,)).fetchone():
            reasons.append(("first_linked_deploy", f"first deploy to linked GCP project {tgt.project}"))
        reasons += caps_above_default(conn, caller.workspace_id, payload.get("caps") or {})
        if idem and conn.execute("select 1 from operations where workspace_id = %s and idem_key = %s",
                                 (caller.workspace_id, idem)).fetchone():
            # A retry: enqueue() returns the original operation (or a conflict if the body changed).
            return enqueue(conn, caller, caller.workspace_id, "deploy", app["id"], tgt.project, payload, idem, reasons)
        dep_id = uuid.uuid4()
        estimate_row = _record_estimate(conn, caller, app["id"], est) if est else None
        config = {"caps": payload.get("caps") or {}, "env_names": sorted((payload.get("env") or {}).keys()),
                  "secret_names": sorted((payload.get("secrets") or {}).keys()), "source_kind": payload["source"]["kind"]}
        conn.execute("insert into deployments (id, workspace_id, app_id, config, status, requested_by, requested_via, token_id,"
                     " load_estimate_id) values (%s,%s,%s,%s,'queued',%s,%s,%s,%s)",
                     (dep_id, caller.workspace_id, app["id"], Jsonb(config), caller.user_id,
                      "mcp" if caller.via == "mcp" else "console", caller.token_id, estimate_row))
        # The estimate's tier goes in the unhashed part of the input, so a retry stays idempotent.
        return enqueue(conn, caller, caller.workspace_id, "deploy", app["id"], tgt.project, payload, idem, reasons,
                       extra={"deployment_id": str(dep_id), **({"tier": tier} if tier and not payload.get("tier") else {})})


def _finished_estimate(conn, caller: Caller, slug: str, estimate_id: str | None) -> dict[str, Any] | None:
    """The estimate a deploy names, or else the latest finished estimate for that app name."""
    if estimate_id:
        row = conn.execute("select id, result, created_at from operations where workspace_id = %s and kind = 'estimate'"
                           " and status = 'succeeded' and result->>'estimate_id' = %s", (caller.workspace_id, estimate_id)).fetchone()
        if row is None:
            raise Conflict(f"estimate {estimate_id} not found or not finished; poll it with operation first")
        return row
    return conn.execute("select id, result, created_at from operations where workspace_id = %s and kind = 'estimate'"
                        " and status = 'succeeded' and input->>'app' = %s order by created_at desc limit 1",
                        (caller.workspace_id, slug)).fetchone()


def _record_estimate(conn, caller: Caller, app_id: Any, est: dict[str, Any]) -> Any:
    """Store it against the app once (load_estimates); returns its id for deployments.load_estimate_id."""
    r = est["result"]
    prior = conn.execute("select id from load_estimates where app_id = %s and reasoning->>'estimate_id' = %s",
                         (app_id, r["estimate_id"])).fetchone()
    if prior:
        return prior["id"]
    res = r.get("resources") or {}
    mem = str(res.get("memory", ""))
    cost = r.get("monthly_cost_usd") or {}
    return conn.execute(
        "insert into load_estimates (id, workspace_id, app_id, workload_class, confidence, recommended_tier, cpu, memory_mib,"
        " monthly_cost_usd, profile, reasoning, created_at) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id",
        (uuid.uuid4(), caller.workspace_id, app_id, r.get("workload_class", "unknown"), r.get("confidence"), r.get("tier", "T1"),
         float(res["cpu"]) if res.get("cpu") else None,
         int(mem[:-2]) * (1024 if mem.endswith("Gi") else 1) if mem[-2:] in ("Mi", "Gi") else None,
         cost.get("expected"), Jsonb(r.get("profile") or {}),
         Jsonb({"estimate_id": r["estimate_id"], "operation_id": str(est["id"]), "monthly_cost_usd": cost,
                "explained_by": r.get("explained_by"), "reasoning": r.get("reasoning"),
                "recommended_caps": r.get("recommended_caps")}),
         est["created_at"])).fetchone()["id"]


def start_rollback(caller: Caller, slug: str, to: str, idem: str | None) -> Operation:
    with db.tx(caller) as conn:
        app = _app_row(conn, caller.workspace_id, slug)
        rows = conn.execute("select id, revision, image_digest from deployments where app_id = %s and status = 'succeeded'"
                            " and revision is not null order by finished_at desc", (app["id"],)).fetchall()
        if to == "previous":
            if len(rows) < 2:
                raise Conflict(f"app {slug} has no earlier revision to roll back to")
            target = rows[1]
        else:
            target = next((r for r in rows if r["revision"] == to), None)
            if target is None:
                raise NotFound(f"revision {to} is not a successful deployment of {slug}")
        return enqueue(conn, caller, caller.workspace_id, "rollback", app["id"], app_ref(conn, app).target.project,
                       {"to_revision": target["revision"], "to_deployment": str(target["id"]),
                        "from_deployment": str(rows[0]["id"])}, idem)


def set_caps(caller: Caller, slug: str, caps: dict[str, Any], idem: str | None) -> tuple[dict[str, Any], Operation | None]:
    with db.tx(caller) as conn:
        app = _app_row(conn, caller.workspace_id, slug)
        project = app_ref(conn, app).target.project
        reasons = caps_above_default(conn, caller.workspace_id, caps)
        payload = {"caps": caps} if reasons else {"caps": caps, "push_only": True}
        op, idem = replay(conn, caller.workspace_id, "set_caps", app["id"], payload, idem)
        if op is None:  # a new change: write (unless it waits for approval) and push it
            if reasons:  # the whole change waits for approval, so it never half-applies
                op = enqueue(conn, caller, caller.workspace_id, "set_caps", app["id"], project, payload, idem, reasons)
            else:
                write_app_caps(conn, caller.workspace_id, app["id"], caps, caller.user_id)
                op = enqueue(conn, caller, caller.workspace_id, "set_caps", app["id"], project, payload, idem) \
                    if app["status"] == "running" else None
        return app_caps(conn, caller.workspace_id, app["id"]), op


def _split(principal: str) -> tuple[str, str]:
    kind, _, value = principal.partition(":")
    return kind, value.lower()


def write_grants(conn, workspace_id: str, app_id: Any, add: list[str], remove: list[str], by: str | None) -> None:
    for p in remove:
        kind, value = _split(p)
        conn.execute("update access_grants set revoked_at = now(), revoked_by = %s, sync_state = 'removing'"
                     " where app_id = %s and principal_type = %s and principal = %s and revoked_at is null",
                     (by, app_id, kind, value))
    for p in add:
        kind, value = _split(p)
        conn.execute("insert into access_grants (id, workspace_id, app_id, principal_type, principal, granted_by)"
                     " values (%s,%s,%s,%s,%s,%s) on conflict do nothing", (uuid.uuid4(), workspace_id, app_id, kind, value, by))


def set_access(caller: Caller, slug: str, add: list[dict[str, Any]], remove: list[str], idem: str | None
               ) -> tuple[list[dict[str, Any]], Operation | None]:
    with db.tx(caller) as conn:
        app = _app_row(conn, caller.workspace_id, slug)
        if f"user:{caller.email}".lower() in [r.lower() for r in remove] and \
                conn.execute("select 1 from users where id = %s and email = %s", (app["created_by"], caller.email)).fetchone():
            raise Conflict("cannot remove the owner")
        project = app_ref(conn, app).target.project
        domain = [g["principal"] for g in add if g["principal"].startswith("domain:")]
        direct = [g["principal"] for g in add if not g["principal"].startswith("domain:")]
        # The whole change is part of the request hash, so different changes never share an operation.
        # The domain operation's worker reads only `add`; `with` is there for the hash.
        norm = lambda ps: sorted({p.strip().lower() for p in ps})  # noqa: E731 — as write_grants stores them
        push = {"push_only": True, "add": norm(direct), "remove": norm(remove)}
        payload = {"add": norm(domain), "with": {"add": push["add"], "remove": push["remove"]}} if domain else push
        op, idem = replay(conn, caller.workspace_id, "set_access", app["id"], payload, idem)
        if op is None:  # a new change: write it, then push it
            write_grants(conn, caller.workspace_id, app["id"], direct, remove, caller.user_id)
            if domain:
                reasons = [("domain_grant", f"grant to a whole domain ({p})") for p in domain]
                op = enqueue(conn, caller, caller.workspace_id, "set_access", app["id"], project, payload, idem, reasons)
            elif (direct or remove) and app["status"] == "running":
                op = enqueue(conn, caller, caller.workspace_id, "set_access", app["id"], project, payload, idem)
        return _app_view(conn, app)["access"], op


def delete_app(caller: Caller, slug: str, idem: str | None) -> Operation:
    with db.tx(caller) as conn:
        app = _app_row(conn, caller.workspace_id, slug)
        return enqueue(conn, caller, caller.workspace_id, "delete", app["id"], app_ref(conn, app).target.project, {}, idem,
                       [("delete_app", "delete_app always needs approval (soft delete, restorable for 7 days)")],
                       summary=f"Delete {slug} (restorable for 7 days)")


def restore_app(caller: Caller, slug: str) -> Operation:
    with db.tx(caller) as conn:
        app = _app_row(conn, caller.workspace_id, slug, include_deleted=True)
        if app["deleted_at"] is None:
            raise Conflict(f"app {slug} is not deleted")
        if app["purge_after"] < datetime.now(UTC):
            raise LimitExceeded(f"app {slug} can no longer be restored")
        if conn.execute("select 1 from apps where workspace_id = %s and slug = %s and deleted_at is null",
                        (caller.workspace_id, slug)).fetchone():
            raise Conflict(f"a new app already uses the name {slug}")
        return enqueue(conn, caller, caller.workspace_id, "restore", app["id"], app_ref(conn, app).target.project, {},
                       f"restore:{app['id']}:{int(app['deleted_at'].timestamp())}")


def set_ai(caller: Caller, slug: str, provider: str, model: str, key_secret: str | None, base_url: str | None) -> dict[str, Any]:
    with db.tx(caller) as conn:
        app = _app_row(conn, caller.workspace_id, slug)
        secret_id = None
        if provider != "meta":
            if not key_secret:
                raise Conflict("BYOK providers need key_secret: the name of a secret stored in the console")
            ref = conn.execute("select id from secret_refs where workspace_id = %s and label = %s and destroyed_at is null",
                               (caller.workspace_id, key_secret)).fetchone()
            if ref is None:
                raise NotFound(f"secret {key_secret} not found; add it in the console first")
            secret_id = ref["id"]
        conn.execute(
            "insert into provider_configs (id, workspace_id, app_id, provider, model, base_url, secret_ref_id, updated_by)"
            " values (%s,%s,%s,%s,%s,%s,%s,%s) on conflict (workspace_id, coalesce(app_id, '00000000-0000-0000-0000-000000000000'::uuid))"
            " do update set provider = excluded.provider, model = excluded.model, base_url = excluded.base_url,"
            " secret_ref_id = excluded.secret_ref_id, updated_by = excluded.updated_by, updated_at = now()",
            (uuid.uuid4(), caller.workspace_id, app["id"], provider, model, base_url, secret_id, caller.user_id))
        return {"provider": provider, "model": model, "key_secret": key_secret}


def live_status(deployer: Deployer, caller: Caller, slug: str) -> dict[str, Any] | None:
    ref, row = app_ref_for(caller, slug)
    if row["status"] in ("draft",) and not row["url"]:
        return None
    return deployer.status(ref)


# --- API tokens (console only; row-level security limits them to the caller's own) ---------------


def _token_view(r: dict[str, Any]) -> dict[str, Any]:
    return {"id": str(r["id"]), "name": r["name"], "prefix": r["prefix"], "scopes": list(r["scopes"]),
            "created_at": _iso(r["created_at"]), "expires_at": _iso(r["expires_at"]),
            "last_used_at": _iso(r["last_used_at"])}


def list_tokens(caller: Caller) -> list[dict[str, Any]]:
    with db.tx(caller) as conn:
        rows = conn.execute("select id, name, prefix, scopes, created_at, expires_at, last_used_at from api_tokens"
                            " where user_id = %s and kind = 'pat' and revoked_at is null and expires_at > now()"
                            " order by created_at desc", (caller.user_id,)).fetchall()
    return [_token_view(r) for r in rows]


def create_token(caller: Caller, name: str, scopes: list[str], days: int) -> tuple[dict[str, Any], str]:
    from . import auth

    if unknown := set(scopes) - set(auth.SCOPES):
        raise ValueError(f"unknown scopes: {', '.join(sorted(unknown))}")
    if "data:raw" in scopes and caller.role not in ("owner", "admin"):
        raise PermissionError("data:raw needs the admin role")
    secret = f"sc_pat_{secrets.token_hex(4)}_{secrets.token_urlsafe(32)}"
    with db.tx(caller) as conn:
        row = auth.insert_pat(conn, secret, caller.user_id, caller.workspace_id, sorted(set(scopes)), name, days)
    return _token_view(row), secret


def revoke_token(caller: Caller, token_id: str) -> bool:
    with db.tx(caller) as conn:
        r = conn.execute("update api_tokens set revoked_at = now() where id = %s and revoked_at is null"
                         " and (user_id = %s or %s) returning id",
                         (token_id, caller.user_id, caller.role in ("owner", "admin"))).fetchone()
    return r is not None
