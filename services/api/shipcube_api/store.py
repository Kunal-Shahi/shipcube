"""Control-plane state (in memory until security's Postgres schema is wired) and the operation
step plans. Operations run on the background runner (runner.py); each step calls the deployer
(deploy/gcp.py for real GCP, deploy/mock.py otherwise).

Approvals: an operation that needs a human starts in `awaiting_approval` with an `approval_url`
and does not run until it is approved in the console. See docs/research/networking.md §2.5.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from .deploy.base import AppRef, Deployer, DeployError, Target
from .explain import explain
from .models import OpError, Operation, OpKind, Progress, Step
from .runner import runner
from .settings import settings

CONSOLE_URL = "https://console.shipcube.dev"
WORKSPACE_ID = "0192f3a0-0000-7000-8000-000000000001"
USER_ID = "0192f3a0-0000-7000-8000-0000000000a1"
RESTORE_WINDOW = timedelta(days=7)
APPROVAL_TTL = timedelta(hours=72)
MOCK_SPEED = float(os.environ.get("SHIPCUBE_MOCK_SPEED", "1"))

# Messages shown while each step runs. The work itself is in Store._plan.
STEP_MESSAGES: dict[str, list[tuple[str, str]]] = {
    "estimate": [
        ("fetch_source", "Fetching source"),
        ("sandbox_profile", "Running in sandbox on sample data"),
        ("classify", "Classifying workload"),
        ("explain", "Muse Spark explaining the placement"),
        ("price", "Pricing candidate tiers"),
    ],
    "deploy": [
        ("validate", "Validating and staging the source"),
        ("build", "Cloud Build: building the container with buildpacks"),
        ("push", "Image published to Artifact Registry"),
        ("deploy", "Creating the Cloud Run revision (gen2, scale to zero, IAP on)"),
        ("configure_access", "Applying the access list as IAP bindings"),
        ("health_check", "Checking the revision is ready"),
    ],
    "delete": [
        ("scale_to_zero", "Removing access and external ingress"),
        ("soft_delete", "Marking deleted; restorable for 7 days"),
    ],
    "set_caps": [("apply", "Applying caps to the Cloud Run service")],
    "set_access": [("apply", "Applying access grants (IAP bindings)")],
}

# Workspace defaults (security's `caps` table, app_id = null). Raising a cap above these needs approval.
WORKSPACE_DEFAULT_CAPS: dict[str, Any] = {
    "max_instances": 2, "concurrency": 80, "requests_per_minute": 120, "monthly_spend_usd": 5,
    "cpu": "1", "memory": "512Mi", "timeout_s": 60,
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


class Conflict(Exception):
    pass


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def _cap_value(key: str, v: Any) -> float:
    if key == "memory":
        n = float(str(v)[:-2])
        return n * 1024 if str(v).endswith("Gi") else n
    return float(v)


def caps_above_default(caps: dict[str, Any]) -> list[tuple[str, str]]:
    """(cap_raise, reason) for every cap that would be raised above the workspace default."""
    return [
        ("cap_raise", f"{k} {v} is above the workspace default {WORKSPACE_DEFAULT_CAPS[k]}")
        for k, v in caps.items()
        if v is not None and k in WORKSPACE_DEFAULT_CAPS and _cap_value(k, v) > _cap_value(k, WORKSPACE_DEFAULT_CAPS[k])
    ]


def make_deployer() -> Deployer:
    if settings.backend == "gcp":
        from .deploy.gcp import GcpDeployer

        return GcpDeployer(settings)
    from .deploy.mock import MockDeployer

    return MockDeployer(speed=MOCK_SPEED)


@dataclass
class _OpRecord:
    op: Operation
    input: dict[str, Any]
    workspace: str
    cancel_requested: bool = False
    build_id: str | None = None
    started: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


ME: dict[str, Any] = {
    "user_id": USER_ID,
    "email": settings.owner_email,
    "role": "owner",
    "workspace": {"id": WORKSPACE_ID, "name": "Demo workspace"},
    "gcp_targets": [
        {"id": "shipcube", "project": settings.apps_project, "kind": "shipcube", "default": True},
        {"id": "linked-1", "project": "acme-analytics-prod", "kind": "linked", "default": False},
    ],
    "limits": {"max_apps": 3, "monthly_spend_usd": 20},
}

# Linked targets, as security's link-project.sh sets them up: a per-workspace deployer SA in the
# control project, and shipcube-builder@ / shipcube-apps@ in the user's project.
TARGETS: dict[str, Target] = {
    "shipcube": Target(id="shipcube", kind="shipcube", project=settings.apps_project),
    "linked-1": Target(
        id="linked-1", kind="linked", project="acme-analytics-prod",
        deployer_sa=f"ws-{WORKSPACE_ID[:8]}-deployer@{settings.control_project}.iam.gserviceaccount.com",
        builder_sa="shipcube-builder@acme-analytics-prod.iam.gserviceaccount.com",
        shared_runtime_sa="shipcube-apps@acme-analytics-prod.iam.gserviceaccount.com",
    ),
}


def _new_app(name: str, tier: str, target: str) -> dict[str, Any]:
    return {
        "name": name, "url": "", "tier": tier, "target": target, "region": settings.region,
        "status": "deploying", "revision": "", "image": "",
        "caps": {**WORKSPACE_DEFAULT_CAPS, "concurrency": 40},
        "access": [{"principal": f"user:{ME['email']}", "role": "owner", "granted_at": now()}],
        "ai": {"provider": "meta", "model": "muse-spark", "key_secret": None},
        "updated_at": now(), "deleted_at": None, "restorable_until": None,
    }


class Store:
    def __init__(self, deployer: Deployer | None = None) -> None:
        self.me = ME
        self.deployer = deployer or make_deployer()
        self.apps: dict[str, dict[str, Any]] = {}
        if self.deployer.name == "mock":  # a demo app so a fresh mock server has something to show
            demo = _new_app("regional-revenue", "T1", "shipcube")
            demo.update(status="serving", url="https://regional-revenue-a1b2c3-el.a.run.app",
                        revision="regional-revenue-00003-abc", updated_at="2026-10-26T12:00:00Z")
            self.apps["regional-revenue"] = demo
        self.approved_targets: set[tuple[str, str]] = {(WORKSPACE_ID, "shipcube")}
        self._ops: dict[str, _OpRecord] = {}
        self._idem: dict[tuple[str, str], tuple[str, str]] = {}
        self._lock = threading.RLock()
        runner.every(settings.purge_check_s, self.purge_expired)

    def target_kind(self, target_id: str) -> str | None:
        return next((t["kind"] for t in self.me["gcp_targets"] if t["id"] == target_id), None)

    def app_ref(self, name: str, workspace: str = WORKSPACE_ID) -> AppRef:
        a = self.apps[name]
        return AppRef(name=name, workspace_id=workspace, target=TARGETS[a["target"]], region=a["region"])

    # --- operations --------------------------------------------------------------

    def start_operation(self, workspace: str, kind: OpKind, app: str | None, payload: dict[str, Any],
                        idem_key: str | None, approvals: list[tuple[str, str]] | None = None) -> Operation:
        """`approvals` is [(action, reason)], action being one of security's approvals.action values."""
        body_hash = hashlib.sha256(json.dumps([kind, app, payload], sort_keys=True).encode()).hexdigest()
        with self._lock:
            if idem_key:
                prior = self._idem.get((workspace, idem_key))
                if prior:
                    if prior[1] != body_hash:
                        raise Conflict("idempotency key reused with a different request")
                    return self.get_operation(prior[0])  # type: ignore[return-value]
            steps = STEP_MESSAGES[kind]
            op_id = _id("op")
            waiting = bool(approvals)
            op = Operation(
                id=op_id, kind=kind, app=app, status="awaiting_approval" if waiting else "queued", done=False,
                progress=Progress(step=steps[0][0], completed_steps=0, total_steps=len(steps),
                                  message="Waiting for approval in the console" if waiting else "Queued"),
                steps=[Step(name=s[0], status="pending") for s in steps],
                approval_url=f"{CONSOLE_URL}/w/{workspace}/approvals/{op_id}" if waiting else None,
                approval_reasons=[r for _, r in approvals or []],
                approval_actions=sorted({a for a, _ in approvals or []}),
                approval_expires_at=_iso(datetime.now(UTC) + APPROVAL_TTL) if waiting else None,
                poll_after_s=10 if waiting else 3 if kind == "deploy" else 2,
                created_at=now(), updated_at=now(),
            )
            rec = _OpRecord(op, payload, workspace)
            self._ops[op.id] = rec
            if idem_key:
                self._idem[(workspace, idem_key)] = (op.id, body_hash)
        if not waiting:
            self._launch(rec)
        return op

    def get_operation(self, op_id: str) -> Operation | None:
        rec = self._ops.get(op_id)
        if rec is None:
            return None
        op = rec.op
        if op.status == "awaiting_approval" and op.approval_expires_at and op.approval_expires_at < now():
            self._end_approval(op, "approval_expired", "No decision within 72 hours")
        return op

    def cancel_operation(self, op_id: str) -> Operation | None:
        rec = self._ops.get(op_id)
        if rec is None:
            return None
        if rec.op.status == "awaiting_approval":
            self._end_approval(rec.op, "approval_withdrawn", "Cancelled before a decision")
        elif not rec.op.done:
            rec.cancel_requested = True  # the runner stops before its next step (and cancels a running build)
        return rec.op

    def decide(self, op_id: str, approve: bool) -> Operation | None:
        rec = self._ops.get(op_id)
        if rec is None:
            return None
        op = rec.op
        if op.status != "awaiting_approval":
            raise Conflict(f"operation is {op.status}, not awaiting_approval")
        if not approve:
            self._end_approval(op, "approval_rejected", "Rejected in the console")
            return op
        if op.kind == "deploy":
            self.approved_targets.add((rec.workspace, rec.input.get("target") or "shipcube"))
        op.approval_url = None
        op.status = "queued"
        op.updated_at = now()
        self._launch(rec)
        return op

    @staticmethod
    def _end_approval(op: Operation, code: str, message: str) -> None:
        op.status, op.done, op.approval_url = "cancelled", True, None
        op.error = OpError(code=code, message=message)
        op.progress = Progress(step=op.progress.step, completed_steps=0, total_steps=op.progress.total_steps, message=message)
        op.updated_at = now()

    # --- running ------------------------------------------------------------------

    def _launch(self, rec: _OpRecord) -> None:
        if rec.started:
            return
        rec.started = True
        if rec.op.kind == "deploy" and rec.op.app not in self.apps:
            self.apps[rec.op.app] = _new_app(rec.op.app, rec.input.get("tier") or "T1", rec.input.get("target") or "shipcube")
        runner.submit(self._run(rec))

    async def _run(self, rec: _OpRecord) -> None:
        op = rec.op
        plan = self._plan(rec)
        names = [n for n, _ in STEP_MESSAGES[op.kind]]
        timings: dict[str, float] = {}
        try:
            for i, (name, message) in enumerate(STEP_MESSAGES[op.kind]):
                if rec.cancel_requested:
                    raise DeployError("cancelled", "cancelled by the caller")
                op.status = "running"
                op.steps = [Step(name=n, status="succeeded" if j < i else "running" if j == i else "pending")
                            for j, n in enumerate(names)]
                op.progress = Progress(step=name, completed_steps=i, total_steps=len(names), message=message)
                op.updated_at = now()
                t0 = time.monotonic()
                result = await asyncio.to_thread(plan[name])
                timings[name] = round(time.monotonic() - t0, 1)
                if result is not None:
                    op.result = result
            op.steps = [Step(name=n, status="succeeded") for n in names]
            op.progress = Progress(step=names[-1], completed_steps=len(names), total_steps=len(names), message="Done")
            op.status, op.done = "succeeded", True
            if isinstance(op.result, dict):
                op.result = {**op.result, "timings_s": timings}
        except Exception as e:  # noqa: BLE001 — every failure must end the operation
            code = e.code if isinstance(e, DeployError) else "internal"
            cancelled = code == "cancelled"
            failed_at = op.progress.step
            op.steps = [Step(name=s.name, status=("cancelled" if cancelled else "failed") if s.name == failed_at else s.status)
                        for s in op.steps]
            op.status, op.done = ("cancelled" if cancelled else "failed"), True
            op.error = OpError(code=code, message=str(e)[:1000])
            op.progress = Progress(step=failed_at, completed_steps=op.progress.completed_steps,
                                   total_steps=op.progress.total_steps, message=f"{'Cancelled' if cancelled else 'Failed'}: {e}"[:300])
            if op.kind == "deploy" and op.app in self.apps and not self.apps[op.app]["revision"]:
                self.apps[op.app]["status"] = "failed"
        op.result = op.result if op.result is not None else None
        op.updated_at = now()

    def _plan(self, rec: _OpRecord) -> dict[str, Callable[[], Any]]:
        op, payload, d = rec.op, rec.input, self.deployer

        def pause(s: float) -> Callable[[], None]:
            return lambda: time.sleep(s * MOCK_SPEED)

        if op.kind == "estimate":
            def explain_step() -> dict[str, Any]:
                classified = {  # the classifier's numbers (mock until the load-estimator lands)
                    "estimate_id": f"est_{op.id[3:]}", "workload_class": "bursty_interactive", "confidence": 0.87,
                    "tier": "T1", "target": "cloud_run_service",
                    "resources": {"cpu": "1", "memory": "512Mi", "concurrency": 40, "min_instances": 0, "max_instances": 2},
                    "monthly_cost_usd": {"low": 0.0, "expected": 1.8, "high": 6.5},
                    "profile": {"peak_rss_mb": 210, "cpu_seconds": 3.4, "wall_seconds": 5.1, "queries": 12, "io_mb": 18},
                }
                return {**classified, **explain(classified).model_dump(mode="json")}

            return {"fetch_source": pause(1), "sandbox_profile": pause(3), "classify": pause(1),
                    "explain": explain_step, "price": pause(1)}

        name = op.app or ""
        if op.kind == "deploy":
            app = self.apps[name]
            app["target"] = payload.get("target") or app["target"]
            ref = self.app_ref(name, rec.workspace)
            caps = {**app["caps"], **{k: v for k, v in (payload.get("caps") or {}).items() if v is not None}}

            def validate() -> None:
                rec.extra["staged"] = d.stage_source(ref, payload["source"], op.id)

            def build() -> None:
                def on_build(build_id: str) -> None:
                    rec.build_id = build_id

                rec.extra["image"] = d.build(ref, rec.extra["staged"], op.id, lambda: rec.cancel_requested, on_build)

            def push() -> dict[str, Any]:
                return {"app": name, "image": rec.extra["image"], "build_id": rec.build_id}

            def deploy() -> None:
                rec.extra["deployed"] = d.deploy(ref, rec.extra["image"], caps, payload.get("env") or {})

            def configure_access() -> None:
                d.set_access(ref, [b["principal"] for b in app["access"]])

            def health_check() -> dict[str, Any]:
                st = d.status(ref)
                if not st.get("ready"):
                    raise DeployError("unhealthy", f"service not ready: {st.get('condition')}")
                dep = rec.extra["deployed"]
                app.update(status="serving", url=dep.url, revision=dep.revision, image=dep.image, caps=caps,
                           tier=payload.get("tier") or app["tier"], updated_at=now())
                return {"app": name, "url": dep.url, "revision": dep.revision, "image": dep.image, "tier": app["tier"],
                        "target": app["target"], "access": "private (IAP): " + ", ".join(b["principal"] for b in app["access"]),
                        "build_id": rec.build_id}

            return {"validate": validate, "build": build, "push": push, "deploy": deploy,
                    "configure_access": configure_access, "health_check": health_check}
        if op.kind == "set_caps":
            return {"apply": lambda: {"caps": self.apply_caps(name, payload["caps"])}}
        if op.kind == "set_access":
            return {"apply": lambda: {"access": self.apply_access(name, payload.get("add", []), payload.get("remove", []))}}

        # delete: soft, restorable for 7 days
        def scale_to_zero() -> None:
            d.soft_delete(self.app_ref(name, rec.workspace))

        def soft_delete() -> dict[str, Any]:
            deleted = datetime.now(UTC)
            self.apps[name].update(status="deleted", deleted_at=_iso(deleted), restorable_until=_iso(deleted + RESTORE_WINDOW))
            return {"app": name, "deleted": True, "restorable_until": self.apps[name]["restorable_until"]}

        return {"scale_to_zero": scale_to_zero, "soft_delete": soft_delete}

    # --- mutations shared by direct calls and approved operations ---------------------

    def apply_caps(self, app: str, caps: dict[str, Any]) -> dict[str, Any]:
        a = self.apps[app]
        new = {**a["caps"], **{k: v for k, v in caps.items() if v is not None}}
        if a["revision"]:
            self.deployer.update_caps(self.app_ref(app), new)
        a["caps"], a["updated_at"] = new, now()
        return new

    def apply_access(self, app: str, add: list[dict[str, Any]], remove: list[str]) -> list[dict[str, Any]]:
        a = self.apps[app]
        owner = f"user:{self.me['email']}"
        keep = [b for b in a["access"] if b["principal"] == owner or b["principal"] not in remove]
        for g in add:
            keep = [b for b in keep if b["principal"] != g["principal"]]
            keep.append({"principal": g["principal"], "role": g.get("role", "viewer"), "granted_at": now()})
        if a["revision"] and not a["deleted_at"]:
            self.deployer.set_access(self.app_ref(app), [b["principal"] for b in keep])
        a["access"] = keep
        return keep

    def restore(self, app: str) -> dict[str, Any]:
        a = self.apps[app]
        self.deployer.restore(self.app_ref(app), [b["principal"] for b in a["access"]])
        a.update(status="serving", deleted_at=None, restorable_until=None, updated_at=now())
        return a

    def purge_expired(self) -> list[str]:
        """Hard-delete soft-deleted apps whose 7-day window has passed (runs on the runner)."""
        purged = []
        for name, a in list(self.apps.items()):
            if a["deleted_at"] and a["restorable_until"] and a["restorable_until"] < now():
                self.deployer.purge(self.app_ref(name))
                a.update(status="purged")
                del self.apps[name]
                purged.append(name)
        return purged

    def live_status(self, app: str) -> dict[str, Any] | None:
        try:
            return self.deployer.status(self.app_ref(app))
        except DeployError as e:
            return {"error": str(e)}


store = Store()
