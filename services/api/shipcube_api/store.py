"""In-memory mock state. Operations advance with wall-clock time so that polling and progress
notifications behave like a real deploy without touching GCP."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .models import Operation, OpKind, Progress, Step

# (step, message, seconds)
STEPS: dict[str, list[tuple[str, str, float]]] = {
    "estimate": [
        ("fetch_source", "Fetching source", 1),
        ("sandbox_profile", "Running in sandbox on sample data", 3),
        ("classify", "Classifying workload", 1),
        ("reason", "Muse Spark reviewing code and profile", 2),
        ("price", "Pricing candidate tiers", 1),
    ],
    "deploy": [
        ("validate", "Validating source and caps", 1),
        ("build", "Cloud Build: building container", 4),
        ("push", "Pushing image to Artifact Registry", 1),
        ("deploy", "Creating Cloud Run revision", 2),
        ("configure_access", "Applying private-by-default access policy", 1),
        ("health_check", "Waiting for the revision to become ready", 1),
    ],
    "delete": [("teardown", "Deleting service, image and access bindings", 2)],
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


def _id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


@dataclass
class _OpRecord:
    op: Operation
    started_at: float
    input: dict[str, Any]
    cancelled_at: float | None = None


ME: dict[str, Any] = {
    "user_id": "usr_demo",
    "email": "owner@example.com",
    "workspace": {"id": "ws_demo", "name": "Demo workspace"},
    "gcp_targets": [
        {"id": "shipcube", "project": "shipcube-apps", "kind": "shipcube", "default": True},
        {"id": "linked-1", "project": "acme-analytics-prod", "kind": "linked", "default": False},
    ],
    "limits": {"max_apps": 3, "monthly_spend_usd": 20},
}


def _seed_app(name: str, tier: str) -> dict[str, Any]:
    return {
        "name": name,
        "url": f"https://{name}-a1b2c3d4e5-uc.a.run.app",
        "tier": tier,
        "target": "shipcube",
        "region": "us-central1",
        "status": "serving",
        "revision": f"{name}-00003-abc",
        "image": f"us-central1-docker.pkg.dev/shipcube-apps/apps/{name}@sha256:mock",
        "caps": {"max_instances": 2, "concurrency": 40, "requests_per_minute": 120, "monthly_spend_usd": 5,
                 "cpu": "1", "memory": "512Mi", "timeout_s": 60},
        "access": [{"principal": f"user:{ME['email']}", "role": "owner", "granted_at": "2026-10-26T12:00:00Z"}],
        "ai": {"provider": "meta", "model": "muse-spark", "key_secret": None},
        "updated_at": "2026-10-26T12:00:00Z",
    }


class Store:
    def __init__(self) -> None:
        self.me = ME
        self.apps: dict[str, dict[str, Any]] = {"regional-revenue": _seed_app("regional-revenue", "T1")}
        self._ops: dict[str, _OpRecord] = {}
        self._idem: dict[tuple[str, str], tuple[str, str]] = {}

    # --- operations --------------------------------------------------------------

    def start_operation(self, workspace: str, kind: OpKind, app: str | None, payload: dict[str, Any],
                        idem_key: str | None) -> Operation:
        body_hash = hashlib.sha256(json.dumps([kind, app, payload], sort_keys=True).encode()).hexdigest()
        if idem_key:
            prior = self._idem.get((workspace, idem_key))
            if prior:
                if prior[1] != body_hash:
                    raise Conflict("idempotency key reused with a different request")
                return self._materialize(self._ops[prior[0]])
        steps = STEPS[kind]
        op = Operation(
            id=_id("op"), kind=kind, app=app, status="queued", done=False,
            progress=Progress(step=steps[0][0], completed_steps=0, total_steps=len(steps), message="Queued"),
            steps=[Step(name=s[0], status="pending") for s in steps],
            poll_after_s=3 if kind == "deploy" else 2, created_at=now(), updated_at=now(),
        )
        self._ops[op.id] = _OpRecord(op, time.monotonic(), payload)
        if idem_key:
            self._idem[(workspace, idem_key)] = (op.id, body_hash)
        return op

    def get_operation(self, op_id: str) -> Operation | None:
        rec = self._ops.get(op_id)
        return self._materialize(rec) if rec else None

    def cancel_operation(self, op_id: str) -> Operation | None:
        rec = self._ops.get(op_id)
        if rec is None:
            return None
        if not rec.op.done:
            rec.cancelled_at = time.monotonic()
        return self._materialize(rec)

    def _materialize(self, rec: _OpRecord) -> Operation:
        op = rec.op
        if op.done:
            return op
        steps = STEPS[op.kind]
        elapsed = (rec.cancelled_at or time.monotonic()) - rec.started_at
        completed = 0
        for _, _, secs in steps:
            if elapsed < secs:
                break
            elapsed -= secs
            completed += 1
        finished = completed >= len(steps)
        cancelled = rec.cancelled_at is not None

        def status(i: int) -> str:
            if i < completed:
                return "succeeded"
            if i == completed and not finished:
                return "cancelled" if cancelled else "running"
            return "pending"

        op.steps = [Step(name=s[0], status=status(i)) for i, s in enumerate(steps)]
        cur = steps[min(completed, len(steps) - 1)]
        op.progress = Progress(step=cur[0], completed_steps=completed, total_steps=len(steps),
                               message="Done" if finished else cur[1])
        op.status = "cancelled" if cancelled else "succeeded" if finished else "running"
        op.done = cancelled or finished
        op.updated_at = now()
        if finished and not cancelled:
            op.result = self._complete(op, rec.input)
        return op

    def _complete(self, op: Operation, payload: dict[str, Any]) -> Any:
        if op.kind == "estimate":
            return {
                "estimate_id": f"est_{op.id[3:]}",
                "workload_class": "bursty_interactive",
                "confidence": 0.87,
                "tier": "T1",
                "target": "cloud_run_service",
                "resources": {"cpu": "1", "memory": "512Mi", "concurrency": 40, "min_instances": 0, "max_instances": 2},
                "monthly_cost_usd": {"low": 0.0, "expected": 1.8, "high": 6.5},
                "profile": {"peak_rss_mb": 210, "cpu_seconds": 3.4, "wall_seconds": 5.1, "queries": 12, "io_mb": 18},
                "recommended_caps": {"requests_per_minute": 120, "monthly_spend_usd": 5},
                "reasoning": "Short CPU bursts per request with long idle gaps and modest memory. Scale-to-zero "
                             "Cloud Run fits; two instances cover the expected 5 concurrent viewers.",
                "alternatives": [
                    {"tier": "T0", "why_not": "Data refreshes hourly and viewers filter interactively, so a static snapshot is too stale."},
                    {"tier": "T3", "why_not": "Always-on min instances would cost ~$15/month for no latency benefit at this load."},
                ],
            }
        if op.kind == "deploy":
            name = op.app or ""
            existing = self.apps.get(name)
            app = existing or _seed_app(name, payload.get("tier") or "T1")
            app["status"] = "serving"
            app["tier"] = payload.get("tier") or app["tier"]
            app["target"] = payload.get("target") or app["target"]
            if payload.get("caps"):
                app["caps"].update({k: v for k, v in payload["caps"].items() if v is not None})
            app["revision"] = f"{name}-0000{4 if existing else 1}-xyz"
            app["updated_at"] = now()
            self.apps[name] = app
            return {"app": name, "url": app["url"], "revision": app["revision"], "tier": app["tier"],
                    "target": app["target"], "access": "private (owner only, IAP)"}
        self.apps.pop(op.app or "", None)
        return {"app": op.app, "deleted": True}

    # --- reads -------------------------------------------------------------------

    def logs(self, app: str, limit: int, severity: str | None) -> list[dict[str, Any]]:
        lines = [
            ("INFO", "GET /api/revenue?region=SP 200 84ms"),
            ("INFO", "GET / 200 12ms"),
            ("WARNING", "semantic layer: query fell back to L3 raw SQL (unverified)"),
            ("INFO", "Cold start: 1.9s"),
            ("ERROR", "upstream warehouse timeout after 30s"),
        ]
        base = datetime.now(UTC)
        out = []
        for i in range(min(limit, 20)):
            sev, msg = lines[i % len(lines)]
            ts = (base - timedelta(seconds=37 * i)).isoformat(timespec="seconds").replace("+00:00", "Z")
            out.append({"timestamp": ts, "severity": sev, "message": msg, "revision": self.apps[app]["revision"]})
        return [e for e in out if not severity or e["severity"] == severity]

    def metrics(self, app: str, window: str) -> dict[str, Any]:
        return {
            "app": app, "window": window, "requests": 412, "error_rate": 0.012,
            "latency_ms": {"p50": 84, "p95": 410}, "instances": {"avg": 0.4, "max": 2}, "cold_starts": 6,
            "cpu_utilization": 0.21, "memory_utilization": 0.43,
            "cost_usd": {"compute": 0.03, "ai_tokens": 0.01, "warehouse": 0.0, "month_to_date": 0.62},
            "caps_hit": {"rate_limited_requests": 3, "max_instances_reached": 0},
            "data_freshness_minutes": 42,
        }

    def connect_data(self, body: dict[str, Any]) -> dict[str, Any]:
        return {
            "datasource_id": _id("ds"), "name": body["name"], "kind": body["kind"],
            "egress": "none (bundled)" if body["kind"] == "duckdb" else "static egress IP 34.0.0.1 via Cloud NAT (allowlist this)",
            "cubes_detected": ["orders", "customers"], "status": "connected",
        }

    def query(self, body: dict[str, Any]) -> dict[str, Any]:
        return {
            "level": "L0",
            "sql": "SELECT region, SUM(amount) AS revenue FROM orders GROUP BY 1 ORDER BY 2 DESC LIMIT 100",
            "columns": [{"name": "orders.region", "type": "string"}, {"name": "orders.revenue", "type": "number"}],
            "rows": [["SP", 1_284_220.5], ["RJ", 512_904.1], ["MG", 471_330.0]],
            "cached": False, "verified": True, "datasource": body.get("datasource") or "default",
        }


store = Store()
