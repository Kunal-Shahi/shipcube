"""ShipCube control-plane API: FastAPI skeleton over in-memory mock state.

The HTTP contract in openapi.yaml is the stable part (tests/test_contract.py checks the routes
match it). The API is the only place that makes authorization decisions: every route needs a
verified token with the right scope.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .auth import Caller, tokens
from .models import (
    AccessChange, AccessResult, AiConfigRequest, AiConfigResult, App, AppList, AppName, AppSummary,
    ApprovalDecision, Caps, CapsResult, DeployRequest, EstimateRequest, Logs, Me, Metrics, Operation, Upload,
    UploadRequest,
)
from .deploy.base import DeployError
from .store import ENFORCED_BY, Conflict, caps_above_default, store
from .telemetry import telemetry

app = FastAPI(title="ShipCube control-plane API", version="v1")

# Approvals (docs/research/networking.md §2.5). Agents act directly on reads, cap reductions, access
# removals and ShipCube-project deploys within default caps. These wait for a human in the console:
# the first deploy to a linked GCP project, a cap raise above the workspace default, a domain: grant,
# and delete_app (a soft delete, restorable for 7 days).


class ApiError(HTTPException):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(status, {"code": code, "message": message})


@app.exception_handler(HTTPException)
async def _http_error(_: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
    return JSONResponse({"error": detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    msg = "; ".join(f"{'.'.join(str(p) for p in e['loc'][1:])}: {e['msg']}" for e in exc.errors())
    return JSONResponse({"error": {"code": "invalid_argument", "message": msg}}, status_code=400)


@app.exception_handler(DeployError)
async def _deploy_error(_: Request, exc: DeployError) -> JSONResponse:
    status = {"not_found": 404, "invalid_argument": 400, "permission_denied": 502}.get(exc.code, 502)
    return JSONResponse({"error": {"code": exc.code, "message": str(exc)}}, status_code=status)


@app.exception_handler(Conflict)
async def _conflict(_: Request, exc: Conflict) -> JSONResponse:
    return JSONResponse({"error": {"code": "conflict", "message": str(exc)}}, status_code=409)


# --- auth ------------------------------------------------------------------------------


def caller(authorization: Annotated[str | None, Header()] = None) -> Caller:
    if not authorization or not authorization.startswith("Bearer "):
        raise ApiError(401, "unauthenticated", "missing bearer token")
    c = tokens.verify(authorization.removeprefix("Bearer ").strip())
    if c is None:
        raise ApiError(401, "unauthenticated", "invalid, expired or revoked token")
    return c


def scope(required: str):
    def check(c: Annotated[Caller, Depends(caller)]) -> Caller:
        if required not in c.scopes:
            raise ApiError(403, "permission_denied", f"token lacks scope {required}")
        return c

    return check


IdemKey = Annotated[str | None, Header(alias="Idempotency-Key", max_length=64)]


def get_app(name: str, include_deleted: bool = False) -> dict:
    a = store.apps.get(name)
    if a is None or (a["deleted_at"] and not include_deleted):
        raise ApiError(404, "not_found", f"app {name} not found")
    return a


def console_session(c: Annotated[Caller, Depends(caller)]) -> Caller:
    if c.via != "console":
        raise ApiError(403, "permission_denied", "only a human console session can do this; approve at the approval_url")
    return c


def accepted(op: Operation, response: Response) -> Operation:
    response.status_code = 202
    response.headers["Location"] = f"/v1/operations/{op.id}"
    response.headers["Retry-After"] = str(op.poll_after_s)
    return op


# --- routes ----------------------------------------------------------------------------


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict:
    return {"ok": True}


@app.get("/v1/me", response_model=Me)
def me(c: Annotated[Caller, Depends(caller)]):
    # No scope needed: any valid token may see who it belongs to and what it can do.
    return {**store.me, "token": {"id": c.token_id, "scopes": sorted(c.scopes), "expires_at": c.expires_at}}


@app.post("/v1/uploads", response_model=Upload, status_code=201)
def create_upload(body: UploadRequest, c: Annotated[Caller, Depends(scope("apps:deploy"))]):
    # GCP: a V4 signed PUT URL for one object in the private uploads bucket, valid 15 minutes.
    return store.deployer.create_upload(c.workspace_id, body.content_type)


@app.post("/v1/estimates", response_model=Operation, status_code=202)
def start_estimate(body: EstimateRequest, response: Response, c: Annotated[Caller, Depends(scope("apps:read"))],
                   idem: IdemKey = None):
    op = store.start_operation(c.workspace_id, "estimate", body.app, body.model_dump(mode="json"), idem)
    return accepted(op, response)


@app.post("/v1/apps/{app}/deployments", response_model=Operation, status_code=202)
def deploy(app: AppName, body: DeployRequest, response: Response,
           c: Annotated[Caller, Depends(scope("apps:deploy"))], idem: IdemKey = None):
    target = body.target or next(t["id"] for t in store.me["gcp_targets"] if t["default"])
    kind = store.target_kind(target)
    if kind is None:
        raise ApiError(400, "invalid_argument", f"unknown target {target}; see whoami")
    reasons = []
    if kind == "linked" and (c.workspace_id, target) not in store.approved_targets:
        reasons.append(("first_linked_deploy", f"first deploy to linked GCP project {target}"))
    reasons += caps_above_default(body.caps.model_dump(exclude_none=True) if body.caps else {})
    payload = {**body.model_dump(mode="json"), "target": target}
    op = store.start_operation(c.workspace_id, "deploy", app, payload, idem, reasons)
    return accepted(op, response)


@app.get("/v1/operations/{op_id}", response_model=Operation)
def get_operation(op_id: str, c: Annotated[Caller, Depends(scope("apps:read"))]):
    op = store.get_operation(op_id)
    if op is None:
        raise ApiError(404, "not_found", f"operation {op_id} not found")
    return op


@app.post("/v1/operations/{op_id}/cancel", response_model=Operation)
def cancel_operation(op_id: str, c: Annotated[Caller, Depends(scope("apps:deploy"))]):
    op = store.cancel_operation(op_id)
    if op is None:
        raise ApiError(404, "not_found", f"operation {op_id} not found")
    return op


@app.post("/v1/operations/{op_id}/approve", response_model=Operation)
def approve(op_id: str, body: ApprovalDecision, c: Annotated[Caller, Depends(console_session)]):
    op = store.decide(op_id, approve=True)
    if op is None:
        raise ApiError(404, "not_found", f"operation {op_id} not found")
    return op


@app.post("/v1/operations/{op_id}/reject", response_model=Operation)
def reject(op_id: str, body: ApprovalDecision, c: Annotated[Caller, Depends(console_session)]):
    op = store.decide(op_id, approve=False)
    if op is None:
        raise ApiError(404, "not_found", f"operation {op_id} not found")
    return op


@app.get("/v1/apps", response_model=AppList)
def list_apps(c: Annotated[Caller, Depends(scope("apps:read"))]):
    return {"apps": [AppSummary.model_validate(a) for a in store.apps.values()]}


@app.get("/v1/apps/{app}", response_model=App)
def app_status(app: AppName, c: Annotated[Caller, Depends(scope("apps:read"))]):
    a = get_app(app, include_deleted=True)
    return {**a, "runtime": store.live_status(app) if a["revision"] else None}


@app.delete("/v1/apps/{app}", response_model=Operation, status_code=202)
def delete_app(app: AppName, response: Response, c: Annotated[Caller, Depends(scope("apps:deploy"))],
               idem: IdemKey = None):
    get_app(app)
    reasons = [("delete_app", "delete_app always needs approval (soft delete, restorable for 7 days)")]
    return accepted(store.start_operation(c.workspace_id, "delete", app, {}, idem, reasons), response)


@app.post("/v1/apps/{app}/restore", response_model=App)
def restore(app: AppName, c: Annotated[Caller, Depends(console_session)]):
    a = get_app(app, include_deleted=True)
    if not a["deleted_at"]:
        raise ApiError(409, "conflict", f"app {app} is not deleted")
    if a["restorable_until"] < datetime.now(UTC).isoformat():
        raise ApiError(410, "gone", f"app {app} can no longer be restored")
    return store.restore(app)


@app.get("/v1/apps/{app}/logs", response_model=Logs)
def logs(app: AppName, c: Annotated[Caller, Depends(scope("apps:read"))],
         severity: Literal["DEBUG", "INFO", "WARNING", "ERROR"] | None = None,
         limit: Annotated[int, Query(ge=1, le=500)] = 50, page_token: str | None = None):
    return telemetry.logs(get_app(app), limit, severity, page_token)


@app.get("/v1/apps/{app}/metrics", response_model=Metrics)
def metrics(app: AppName, c: Annotated[Caller, Depends(scope("apps:read"))],
            window: Literal["15m", "1h", "24h", "7d", "30d"] = "1h"):
    return telemetry.metrics(get_app(app), window)


@app.patch("/v1/apps/{app}/caps", response_model=CapsResult)
def set_caps(app: AppName, body: Caps, c: Annotated[Caller, Depends(scope("caps:write"))], idem: IdemKey = None):
    a = get_app(app)
    change = body.model_dump(exclude_none=True)
    op = None
    if reasons := caps_above_default(change):
        # The whole change waits, so the caps never end up half-applied.
        op = store.start_operation(c.workspace_id, "set_caps", app, {"caps": change}, idem, reasons)
    else:
        store.apply_caps(app, change)
    return {"caps": a["caps"], "enforced_by": {k: ENFORCED_BY.get(k, "not enforced (unknown cap)") for k in a["caps"]},
            "operation": op}


@app.patch("/v1/apps/{app}/access", response_model=AccessResult)
def set_access(app: AppName, body: AccessChange, c: Annotated[Caller, Depends(scope("access:write"))],
               idem: IdemKey = None):
    a = get_app(app)
    if f"user:{store.me['email']}" in body.remove:
        raise ApiError(400, "invalid_argument", "cannot remove the owner")
    domain = [g.model_dump() for g in body.add if g.principal.startswith("domain:")]
    direct = [g.model_dump() for g in body.add if not g.principal.startswith("domain:")]
    store.apply_access(app, direct, body.remove)  # removals and user/group grants apply now
    op = None
    if domain:
        reasons = [("domain_grant", f"grant to a whole domain ({g['principal']})") for g in domain]
        op = store.start_operation(c.workspace_id, "set_access", app, {"add": domain}, idem, reasons)
    return {"access": a["access"], "operation": op}


@app.put("/v1/apps/{app}/ai", response_model=AiConfigResult)
def ai_config(app: AppName, body: AiConfigRequest, c: Annotated[Caller, Depends(scope("secrets:write"))]):
    a = get_app(app)
    a["ai"] = {"provider": body.provider, "model": body.model, "key_secret": body.key_secret}
    return {"ai": a["ai"], "gateway_url": f"https://ai.shipcube.dev/v1/apps/{app}"}
