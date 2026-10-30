"""ShipCube control-plane API (FastAPI). Contract: openapi.yaml (tests/test_api.py keeps the routes
in sync). The API is the only place that makes authorization decisions: every route checks the
caller's scope, and Postgres row-level security (security's design) backs it up.

Requests only write desired state and enqueue operations; the durable worker (worker.py) talks to
GCP. Approvals (docs/research/networking.md §2.5): the first deploy to a linked project, a cap raise
above the workspace default, a domain: grant and delete_app wait for a human in the console.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import auth, data_layer, db, gateway_counters, obs, profiler_guard, store, waitlist, workspace
from .auth import Caller
from .deploy.base import DeployError
from .models import (
    AccessChange, AccessResult, AiConfigRequest, AiConfigResult, App, AppList, AppName, ApprovalDecision, Billing, Caps,
    CapsResult, DeployRequest, EstimateRequest, GcpLink, GcpTargets, LinkRequest, Logs, Me, Metrics, Operation,
    OperationList, OpStatus, Provider, ProviderId, ProviderKeyRequest, ProviderList, RollbackRequest, TokenCreate,
    TokenCreated, TokenList, Upload, UploadRequest, WaitlistEntry, WaitlistList, WaitlistMe, WaitlistRequest,
)
from .secret_store import SecretStoreError
from .settings import settings
from .store_runtime import deployer, start_embedded_worker, stop_embedded_worker
from .telemetry import TelemetryUnavailable, telemetry

log = logging.getLogger("shipcube.api")


def startup() -> None:
    """Also called by the combined MCP + API server, whose lifespan the API app doesn't get."""
    obs.setup_logging()
    db.check_schema(migrate_if_dev=settings.dev_mode)  # production: migrations run via infra/db/migrate.sh
    data_layer.check_catalog(migrate_if_dev=settings.dev_mode)
    deployer()  # fail at startup, not on the first request, if the backend is misconfigured
    if settings.dev_mode and (tok := os.environ.get("SHIPCUBE_DEV_CONSOLE_TOKEN")):
        with db.tx(role=db.APP) as conn:  # DEV_MODE stand-in for a console login, for the bootstrap user
            m = conn.execute("select m.user_id, m.workspace_id from memberships m join users u on u.id = m.user_id"
                             " where u.email = %s order by m.created_at limit 1",
                             (os.environ.get("SHIPCUBE_DEV_CONSOLE_EMAIL", ""),)).fetchone()
        if m:
            auth.register_dev_session(tok, str(m["user_id"]), str(m["workspace_id"]))
    start_embedded_worker()
    log.info("api started", extra={"dev_mode": settings.dev_mode, "backend": settings.backend})


def shutdown() -> None:
    stop_embedded_worker()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    startup()
    yield
    shutdown()


app = FastAPI(title="ShipCube control-plane API", version="v1", lifespan=lifespan)


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = obs.new_request_id(request.headers)
    t_req, t_trace = obs.request_id.set(rid), obs.trace_id.set(obs.trace_from_headers(request.headers))
    t0 = time.monotonic()
    try:
        response = await call_next(request)
    finally:
        obs.request_id.reset(t_req)
        obs.trace_id.reset(t_trace)
    response.headers["X-Request-Id"] = rid
    if request.url.path not in ("/healthz", "/health"):
        log.info("request", extra={"request_id": rid, "method": request.method, "path": request.url.path,
                                   "status": response.status_code, "ms": round((time.monotonic() - t0) * 1000)})
    return response


@app.middleware("http")
async def waitlist_cors(request: Request, call_next):
    """The landing page posts the waitlist form from the browser (so the per-IP limit sees the visitor,
    not the console server). CORS for that one route, from the console's origins only, no credentials."""
    if request.url.path != "/v1/waitlist":
        return await call_next(request)
    origin = request.headers.get("origin", "")
    allowed = origin in settings.console_origins
    if request.method == "OPTIONS":
        if not allowed:
            return Response(status_code=403)
        return Response(status_code=204, headers={
            "Access-Control-Allow-Origin": origin, "Access-Control-Allow-Methods": "POST",
            "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Max-Age": "600", "Vary": "Origin"})
    response = await call_next(request)
    if allowed:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    return response


class ApiError(HTTPException):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(status, {"code": code, "message": message})


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


@app.exception_handler(HTTPException)
async def _http_error(_: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
    return JSONResponse({"error": detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    msg = "; ".join(f"{'.'.join(str(p) for p in e['loc'][1:])}: {e['msg']}" for e in exc.errors())
    return _err(400, "invalid_argument", msg)


@app.exception_handler(store.Conflict)
async def _conflict(_: Request, exc: store.Conflict) -> JSONResponse:
    return _err(409, "conflict", str(exc))


@app.exception_handler(store.NotFound)
async def _not_found(_: Request, exc: store.NotFound) -> JSONResponse:
    return _err(404, "not_found", str(exc))


@app.exception_handler(store.LimitExceeded)
async def _limit(_: Request, exc: store.LimitExceeded) -> JSONResponse:
    return _err(429, "resource_exhausted", str(exc))


@app.exception_handler(profiler_guard.RateLimited)
async def _rate_limited(_: Request, exc: profiler_guard.RateLimited) -> JSONResponse:
    return JSONResponse({"error": {"code": "rate_limited", "message": str(exc), "retry_after": exc.retry_after_s}},
                        status_code=429, headers={"Retry-After": str(exc.retry_after_s)})


@app.exception_handler(TelemetryUnavailable)
async def _telemetry(_: Request, exc: TelemetryUnavailable) -> JSONResponse:
    return _err(501, "telemetry_unavailable", str(exc))


@app.exception_handler(SecretStoreError)
async def _secret_store(_: Request, exc: SecretStoreError) -> JSONResponse:
    log.error("secret store error", extra={"error": str(exc)[:500]})
    return _err(502, "secret_store_unavailable", "the key could not be stored; nothing was changed")


@app.exception_handler(DeployError)
async def _deploy_error(_: Request, exc: DeployError) -> JSONResponse:
    status = {"not_found": 404, "invalid_argument": 400, "permission_denied": 403, "rate_limited": 503,
              "unavailable": 503}.get(exc.code, 502)
    log.warning("cloud error on request path", extra={"code": exc.code, "error": str(exc)[:500]})
    return _err(status, exc.code, str(exc))


# --- auth ------------------------------------------------------------------------------


def caller(authorization: Annotated[str | None, Header()] = None,
           x_shipcube_workspace: Annotated[str | None, Header()] = None) -> Caller:
    if not authorization or not authorization.startswith("Bearer "):
        raise ApiError(401, "unauthenticated", "missing bearer token")
    c = auth.verify(authorization.removeprefix("Bearer ").strip(), x_shipcube_workspace)
    if c is None:
        raise ApiError(401, "unauthenticated", "invalid, expired or revoked token")
    return c


def scope(required: str):
    def check(c: Annotated[Caller, Depends(caller)]) -> Caller:
        if required not in c.scopes:
            raise ApiError(403, "permission_denied", f"token lacks scope {required}")
        return c

    return check


def console_session(c: Annotated[Caller, Depends(caller)]) -> Caller:
    if c.via != "console":
        raise ApiError(403, "permission_denied", "only a human console session can do this (approvals, restore, AI keys, linking projects); use the console")
    if c.role not in ("owner", "admin"):
        raise ApiError(403, "permission_denied", "this needs the admin role in this workspace")
    return c


IdemKey = Annotated[str | None, Header(alias="Idempotency-Key", max_length=64)]


def accepted(op: Operation, response: Response) -> Operation:
    response.status_code = 202
    response.headers["Location"] = f"/v1/operations/{op.id}"
    response.headers["Retry-After"] = str(op.poll_after_s)
    return op


# --- routes ----------------------------------------------------------------------------


# /health too: Cloud Run's front end reserves /healthz on run.app URLs, so external checks use /health.
@app.get("/healthz", include_in_schema=False)
@app.get("/health", include_in_schema=False)
def healthz() -> dict:
    with db.tx(role=db.APP) as conn:
        conn.execute("select 1")
    return {"ok": True}


# --- API tokens: console sessions only, so an agent can never mint or widen its own credentials ---


def console_user(c: Annotated[Caller, Depends(caller)]) -> Caller:
    if c.via != "console":
        raise ApiError(403, "permission_denied", "API tokens are managed in the console")
    return c


@app.get("/v1/tokens", response_model=TokenList)
def list_tokens(c: Annotated[Caller, Depends(console_user)]):
    return {"tokens": store.list_tokens(c)}


@app.post("/v1/tokens", response_model=TokenCreated, status_code=201)
def create_token(body: TokenCreate, response: Response, c: Annotated[Caller, Depends(console_user)]):
    try:
        token, secret = store.create_token(c, body.name, body.scopes, body.days)
    except ValueError as e:
        raise ApiError(400, "invalid_argument", str(e)) from None
    except PermissionError as e:
        raise ApiError(403, "permission_denied", str(e)) from None
    response.headers["Cache-Control"] = "no-store"
    log.info("token created", extra={"token_id": token["id"], "scopes": token["scopes"]})
    return {"token": token, "secret": secret}


@app.delete("/v1/tokens/{token_id}", status_code=204)
def revoke_token(token_id: uuid.UUID, c: Annotated[Caller, Depends(console_user)]) -> Response:
    if not store.revoke_token(c, str(token_id)):
        raise ApiError(404, "not_found", "no such active token")
    log.info("token revoked", extra={"token_id": str(token_id)})
    return Response(status_code=204)


@app.get("/v1/me", response_model=Me)
def me(authorization: Annotated[str | None, Header()] = None,
       x_shipcube_workspace: Annotated[str | None, Header()] = None):
    # No scope needed: any valid token may see who it belongs to and what it can do. A console sign-in
    # with no workspace gets 401 here and asks GET /v1/waitlist/me where its request stands (and
    # whether it's a platform admin).
    token = _bearer(authorization)
    if token.startswith("sc_pat_"):
        return store.me(caller(authorization, x_shipcube_workspace))
    ident = auth.console_identity(token)
    c = auth.console_caller(ident, x_shipcube_workspace) if ident else None
    if c is None:
        raise ApiError(401, "unauthenticated", "invalid, expired or revoked token")
    return {**store.me(c), "platform_admin": waitlist.is_admin(ident.email, ident.email_verified)}


# --- waitlist: public sign-up, reviewed by platform admins ------------------------------------


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise ApiError(401, "unauthenticated", "missing bearer token")
    return authorization.removeprefix("Bearer ").strip()


def _client_ip(request: Request) -> str:
    if settings.trust_forwarded_for:
        xff = request.headers.get("x-forwarded-for", "")
        if xff.strip():
            return xff.split(",")[-1].strip()  # appended by Google's front end; earlier entries are the client's
    return request.client.host if request.client else "unknown"


def platform_admin(authorization: Annotated[str | None, Header()] = None) -> auth.ConsoleIdentity:
    """A console session whose verified email is in SHIPCUBE_PLATFORM_ADMINS. Never a PAT, never a workspace role."""
    token = _bearer(authorization)
    if token.startswith("sc_pat_"):
        raise ApiError(403, "permission_denied", "the waitlist is reviewed in the console, not with an API token")
    ident = auth.console_identity(token)
    if ident is None:
        raise ApiError(401, "unauthenticated", "invalid, expired or revoked token")
    if not waitlist.is_admin(ident.email, ident.email_verified):
        raise ApiError(403, "permission_denied", "only ShipCube platform admins can review the waitlist")
    return ident


@app.exception_handler(waitlist.RateLimited)
async def _waitlist_rate_limited(_: Request, exc: waitlist.RateLimited) -> JSONResponse:
    return JSONResponse({"error": {"code": exc.code, "message": str(exc), "retry_after": exc.retry_after_s}},
                        status_code=429, headers={"Retry-After": str(exc.retry_after_s)})


@app.exception_handler(waitlist.Conflict)
async def _waitlist_conflict(_: Request, exc: waitlist.Conflict) -> JSONResponse:
    return _err(409, exc.code, str(exc))


def _verified_console(authorization: str | None) -> auth.ConsoleIdentity:
    token = _bearer(authorization)
    ident = None if token.startswith("sc_pat_") else auth.console_identity(token)
    if ident is None or not ident.email_verified or not ident.email:
        raise ApiError(401, "unauthenticated", "sign in to the console with a verified email")
    return ident


@app.post("/v1/waitlist", status_code=202)
def join_waitlist(body: WaitlistRequest, request: Request,
                  authorization: Annotated[str | None, Header()] = None) -> dict:
    # Always 202 for a well-formed request: new, already listed, already decided or a bot (honeypot).
    # Signed in (the console's one-click request), the email is the session's verified one, not the body's.
    if authorization:
        ident = _verified_console(authorization)
        waitlist.sign_up(ident.email, body.name, body.use_case, body.website, None)
    elif body.email:
        waitlist.sign_up(body.email, body.name, body.use_case, body.website, _client_ip(request))
    else:
        raise ApiError(400, "invalid_argument", "email: required")
    return {"status": "received"}


@app.get("/v1/waitlist/me", response_model=WaitlistMe)
def my_waitlist(authorization: Annotated[str | None, Header()] = None):
    """Where a signed-in person's own request stands; needs no workspace."""
    ident = _verified_console(authorization)
    return {**waitlist.mine(ident.email), "platform_admin": waitlist.is_admin(ident.email, ident.email_verified)}


@app.get("/v1/admin/waitlist", response_model=WaitlistList)
def list_waitlist(_: Annotated[auth.ConsoleIdentity, Depends(platform_admin)],
                  status: Literal["pending", "approved", "rejected", "all"] = "pending",
                  limit: Annotated[int, Query(ge=1, le=500)] = 100):
    return waitlist.list_entries(status, limit)


# Both spellings: /{id}/approve (the console) and /{id}:approve (the API's other actions' style).
@app.post("/v1/admin/waitlist/{entry_id}/approve", response_model=WaitlistEntry)
@app.post("/v1/admin/waitlist/{entry_id}:approve", response_model=WaitlistEntry, include_in_schema=False)
def approve_waitlist(entry_id: uuid.UUID, a: Annotated[auth.ConsoleIdentity, Depends(platform_admin)]):
    try:
        return waitlist.approve(str(entry_id), a.email, a.user_id)
    except LookupError as e:
        raise ApiError(404, "not_found", str(e)) from None


@app.post("/v1/admin/waitlist/{entry_id}/reject", response_model=WaitlistEntry)
@app.post("/v1/admin/waitlist/{entry_id}:reject", response_model=WaitlistEntry, include_in_schema=False)
def reject_waitlist(entry_id: uuid.UUID, a: Annotated[auth.ConsoleIdentity, Depends(platform_admin)]):
    try:
        return waitlist.reject(str(entry_id), a.email, a.user_id)
    except LookupError as e:
        raise ApiError(404, "not_found", str(e)) from None


@app.post("/v1/uploads", response_model=Upload, status_code=201)
def create_upload(body: UploadRequest, c: Annotated[Caller, Depends(scope("apps:deploy"))]):
    # A V4 signed PUT URL for one object in the private uploads bucket, valid 15 minutes.
    return deployer().create_upload(c.workspace_id, body.content_type)


@app.post("/v1/estimates", response_model=Operation, status_code=202)
def start_estimate(body: EstimateRequest, response: Response, c: Annotated[Caller, Depends(scope("apps:read"))],
                   idem: IdemKey = None):
    return accepted(store.start_estimate(c, body.model_dump(mode="json"), idem), response)


@app.post("/v1/apps/{app}/deployments", response_model=Operation, status_code=202)
def deploy(app: AppName, body: DeployRequest, response: Response,
           c: Annotated[Caller, Depends(scope("apps:deploy"))], idem: IdemKey = None):
    return accepted(store.start_deploy(c, app, body.model_dump(mode="json"), idem), response)


@app.post("/v1/apps/{app}/rollbacks", response_model=Operation, status_code=202)
def rollback(app: AppName, body: RollbackRequest, response: Response,
             c: Annotated[Caller, Depends(scope("apps:deploy"))], idem: IdemKey = None):
    return accepted(store.start_rollback(c, app, body.to, idem), response)


@app.get("/v1/operations/{op_id}", response_model=Operation)
def get_operation(op_id: str, c: Annotated[Caller, Depends(scope("apps:read"))]):
    return store.get_operation(c, op_id)


@app.post("/v1/operations/{op_id}/cancel", response_model=Operation)
def cancel_operation(op_id: str, c: Annotated[Caller, Depends(scope("apps:deploy"))]):
    return store.cancel_operation(c, op_id)


@app.post("/v1/operations/{op_id}/approve", response_model=Operation)
def approve(op_id: str, body: ApprovalDecision, c: Annotated[Caller, Depends(console_session)]):
    return store.decide(c, op_id, True, body.comment)


@app.post("/v1/operations/{op_id}/reject", response_model=Operation)
def reject(op_id: str, body: ApprovalDecision, c: Annotated[Caller, Depends(console_session)]):
    return store.decide(c, op_id, False, body.comment)


@app.get("/v1/operations", response_model=OperationList)
def list_operations(c: Annotated[Caller, Depends(scope("apps:read"))], status: OpStatus | None = None,
                    limit: Annotated[int, Query(ge=1, le=200)] = 50):
    # The approvals inbox is status=awaiting_approval; newest first.
    return {"operations": workspace.list_operations(c, status, limit)}


@app.get("/v1/apps", response_model=AppList)
def list_apps(c: Annotated[Caller, Depends(scope("apps:read"))], include_deleted: bool = False):
    return {"apps": store.list_apps(c, include_deleted)}


@app.get("/v1/apps/{app}", response_model=App)
def app_status(app: AppName, c: Annotated[Caller, Depends(scope("apps:read"))]):
    view = store.get_app(c, app, include_deleted=True)
    try:
        view["runtime"] = store.live_status(deployer(), c, app) if view["revision"] else None
    except DeployError as e:  # the record is still useful when Cloud Run can't be reached
        log.warning("live status unavailable", extra={"code": e.code, "error": str(e)[:300]})
        view["runtime"] = {"error": e.code, "message": str(e)[:300]}
    return view


@app.delete("/v1/apps/{app}", response_model=Operation, status_code=202)
def delete_app(app: AppName, response: Response, c: Annotated[Caller, Depends(scope("apps:deploy"))],
               idem: IdemKey = None):
    return accepted(store.delete_app(c, app, idem), response)


@app.post("/v1/apps/{app}/restore", response_model=Operation, status_code=202)
def restore(app: AppName, response: Response, c: Annotated[Caller, Depends(console_session)]):
    return accepted(store.restore_app(c, app), response)


@app.get("/v1/apps/{app}/logs", response_model=Logs)
def logs(app: AppName, c: Annotated[Caller, Depends(scope("apps:read"))],
         severity: Literal["DEBUG", "INFO", "WARNING", "ERROR"] | None = None,
         limit: Annotated[int, Query(ge=1, le=500)] = 50, page_token: str | None = None):
    return telemetry().logs(store.get_app_for_telemetry(c, app), limit, severity, page_token)


@app.get("/v1/apps/{app}/metrics", response_model=Metrics)
def metrics(app: AppName, c: Annotated[Caller, Depends(scope("apps:read"))],
            window: Literal["15m", "1h", "24h", "7d", "30d"] = "1h"):
    return telemetry().metrics(store.get_app_for_telemetry(c, app), window)


@app.patch("/v1/apps/{app}/caps", response_model=CapsResult)
def set_caps(app: AppName, body: Caps, c: Annotated[Caller, Depends(scope("caps:write"))], idem: IdemKey = None):
    caps, op = store.set_caps(c, app, body.model_dump(exclude_none=True), idem)
    return {"caps": caps, "enforced_by": {k: store.ENFORCED_BY.get(k, "not enforced") for k in caps}, "operation": op}


@app.patch("/v1/apps/{app}/access", response_model=AccessResult)
def set_access(app: AppName, body: AccessChange, c: Annotated[Caller, Depends(scope("access:write"))],
               idem: IdemKey = None):
    access, gated, pushed = store.set_access(c, app, [g.model_dump() for g in body.add], body.remove, idem)
    return {"access": access, "operation": gated or pushed, "applied": pushed if gated else None}


@app.put("/v1/apps/{app}/ai", response_model=AiConfigResult)
def ai_config(app: AppName, body: AiConfigRequest, c: Annotated[Caller, Depends(scope("secrets:write"))]):
    try:
        ai = store.set_ai(c, app, body.provider, body.model, body.key_secret, body.base_url)
    except PermissionError as e:
        raise ApiError(403, "permission_denied", str(e)) from None
    return {"ai": ai, "gateway_url": f"{(settings.ai_gateway_url or 'http://localhost:8090').rstrip('/')}/v1/apps/{app}"}


# --- internal: the AI gateway's shared caps (sc-gateway's ID token only) ---------------------


def gateway_only(authorization: Annotated[str | None, Header()] = None) -> None:
    try:
        gateway_counters.verify_gateway(authorization)
    except gateway_counters.Unauthorized as e:
        log.warning("internal gateway route refused", extra={"error": str(e)[:200]})
        raise ApiError(403, "permission_denied", "only the AI gateway may call this") from None


@app.post("/internal/gateway/reserve", include_in_schema=False, dependencies=[Depends(gateway_only)])
def gateway_reserve(body: gateway_counters.ReserveRequest):
    return gateway_counters.reserve(body)


@app.post("/internal/gateway/settle", status_code=204, include_in_schema=False, dependencies=[Depends(gateway_only)])
def gateway_settle(body: gateway_counters.SettleRequest) -> Response:
    gateway_counters.settle(body)
    return Response(status_code=204)


# --- console: AI providers, billing, linked GCP projects -----------------------------------


@app.get("/v1/ai/providers", response_model=ProviderList)
def list_providers(c: Annotated[Caller, Depends(scope("apps:read"))]):
    return {"providers": workspace.list_providers(c)}


@app.put("/v1/ai/providers/{provider}", response_model=Provider)
def save_provider(provider: ProviderId, body: ProviderKeyRequest, c: Annotated[Caller, Depends(console_session)]):
    # Keys are typed into the console by a person; they never pass through MCP.
    return workspace.save_provider(c, provider, body.key, body.base_url, body.model)


@app.delete("/v1/ai/providers/{provider}", status_code=204)
def remove_provider(provider: ProviderId, c: Annotated[Caller, Depends(console_session)]):
    workspace.remove_provider(c, provider)
    return Response(status_code=204)


@app.get("/v1/billing", response_model=Billing)
def get_billing(c: Annotated[Caller, Depends(scope("apps:read"))]):
    return workspace.billing(c)


@app.get("/v1/gcp-targets", response_model=GcpTargets)
def list_gcp_targets(c: Annotated[Caller, Depends(scope("apps:read"))]):
    return workspace.gcp_targets(deployer(), c)


@app.post("/v1/gcp-targets", response_model=GcpLink, status_code=201)
def link_gcp_project(body: LinkRequest, c: Annotated[Caller, Depends(console_session)]):
    return workspace.link_project(deployer(), c, body.project_id, body.region)


# --- data layer (Kunal-Shahi/shipcube-data): the console's Data page; see data_layer.py -----------------------
data_layer.mount(app, caller)
