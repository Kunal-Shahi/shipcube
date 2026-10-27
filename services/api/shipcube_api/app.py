"""ShipCube control-plane API: FastAPI skeleton over in-memory mock state.

The HTTP contract in openapi.yaml is the stable part (tests/test_contract.py checks the routes
match it). The API is the only place that makes authorization decisions: every route needs a
verified token with the right scope.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .auth import Caller, tokens
from .models import (
    AccessBinding, AccessList, AiConfigRequest, AiConfigResult, App, AppList, AppName, AppSummary, Caps,
    CapsResult, DataSource, DataSourceRequest, DeployRequest, EstimateRequest, GrantRequest, Logs, Me, Metrics,
    Operation, QueryRequest, QueryResult, Upload, UploadRequest,
)
from .store import ENFORCED_BY, Conflict, now, store

app = FastAPI(title="ShipCube control-plane API", version="v1")


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


def get_app(name: str) -> dict:
    a = store.apps.get(name)
    if a is None:
        raise ApiError(404, "not_found", f"app {name} not found")
    return a


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
    upload_id = f"upl_{secrets.token_hex(4)}"
    return {
        "upload_id": upload_id,
        "method": "PUT",
        # Production: a V4 signed URL on a private GCS bucket, one object, 15-minute expiry.
        "url": f"https://storage.googleapis.com/shipcube-uploads/{upload_id}.tgz?X-Goog-Signature=mock",
        "headers": {"Content-Type": body.content_type},
        "max_bytes": 100 * 1024 * 1024,
        "expires_at": (datetime.now(UTC) + timedelta(minutes=15)).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }


@app.post("/v1/estimates", response_model=Operation, status_code=202)
def start_estimate(body: EstimateRequest, response: Response, c: Annotated[Caller, Depends(scope("apps:read"))],
                   idem: IdemKey = None):
    op = store.start_operation(c.workspace_id, "estimate", body.app, body.model_dump(mode="json"), idem)
    return accepted(op, response)


@app.post("/v1/apps/{app}/deployments", response_model=Operation, status_code=202)
def deploy(app: AppName, body: DeployRequest, response: Response,
           c: Annotated[Caller, Depends(scope("apps:deploy"))], idem: IdemKey = None):
    op = store.start_operation(c.workspace_id, "deploy", app, body.model_dump(mode="json"), idem)
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


@app.get("/v1/apps", response_model=AppList)
def list_apps(c: Annotated[Caller, Depends(scope("apps:read"))]):
    return {"apps": [AppSummary.model_validate(a) for a in store.apps.values()]}


@app.get("/v1/apps/{app}", response_model=App)
def app_status(app: AppName, c: Annotated[Caller, Depends(scope("apps:read"))]):
    return get_app(app)


@app.delete("/v1/apps/{app}", response_model=Operation, status_code=202)
def delete_app(app: AppName, response: Response, c: Annotated[Caller, Depends(scope("apps:deploy"))],
               idem: IdemKey = None):
    get_app(app)
    return accepted(store.start_operation(c.workspace_id, "delete", app, {}, idem), response)


@app.get("/v1/apps/{app}/logs", response_model=Logs)
def logs(app: AppName, c: Annotated[Caller, Depends(scope("apps:read"))],
         severity: Literal["DEBUG", "INFO", "WARNING", "ERROR"] | None = None,
         limit: Annotated[int, Query(ge=1, le=500)] = 50, page_token: str | None = None):
    get_app(app)
    return {"entries": store.logs(app, limit, severity), "next_page_token": None}


@app.get("/v1/apps/{app}/metrics", response_model=Metrics)
def metrics(app: AppName, c: Annotated[Caller, Depends(scope("apps:read"))],
            window: Literal["15m", "1h", "24h", "7d", "30d"] = "1h"):
    get_app(app)
    return store.metrics(app, window)


@app.patch("/v1/apps/{app}/caps", response_model=CapsResult)
def set_caps(app: AppName, body: Caps, c: Annotated[Caller, Depends(scope("caps:write"))]):
    a = get_app(app)
    a["caps"].update(body.model_dump(exclude_none=True))
    return {"caps": a["caps"], "enforced_by": {k: ENFORCED_BY.get(k, "not enforced (unknown cap)") for k in a["caps"]}}


@app.post("/v1/apps/{app}/access", response_model=AccessList, status_code=201)
def grant(app: AppName, body: GrantRequest, c: Annotated[Caller, Depends(scope("access:write"))]):
    a = get_app(app)
    b = AccessBinding(principal=body.principal, role=body.role, granted_at=now()).model_dump()
    a["access"] = [x for x in a["access"] if x["principal"] != body.principal] + [b]
    return {"access": a["access"]}


@app.delete("/v1/apps/{app}/access/{principal}", response_model=AccessList)
def revoke(app: AppName, principal: str, c: Annotated[Caller, Depends(scope("access:write"))]):
    a = get_app(app)
    if principal == f"user:{store.me['email']}":
        raise ApiError(400, "invalid_argument", "cannot revoke the owner")
    a["access"] = [x for x in a["access"] if x["principal"] != principal]
    return {"access": a["access"]}


@app.put("/v1/apps/{app}/ai", response_model=AiConfigResult)
def ai_config(app: AppName, body: AiConfigRequest, c: Annotated[Caller, Depends(scope("secrets:write"))]):
    a = get_app(app)
    a["ai"] = {"provider": body.provider, "model": body.model, "key_secret": body.key_secret}
    return {"ai": a["ai"], "gateway_url": f"https://ai.shipcube.dev/v1/apps/{app}"}


@app.post("/v1/datasources", response_model=DataSource, status_code=201)
def connect_data(body: DataSourceRequest, c: Annotated[Caller, Depends(scope("secrets:write"))]):
    return store.connect_data(body.model_dump())


@app.post("/v1/query", response_model=QueryResult)
def query(body: QueryRequest, c: Annotated[Caller, Depends(scope("data:read"))]):
    return store.query(body.model_dump())
