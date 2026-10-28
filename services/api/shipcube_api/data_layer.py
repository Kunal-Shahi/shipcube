"""The console's Data page (/v1/data/*), served by Kunal-Shahi/shipcube-data (`shipcube_data.http`).

The router is mounted from the optional `data` extra. Without it, the same routes answer 501
data_unavailable, so the contract (openapi.yaml) doesn't depend on what's installed.

What stays on the API's side:
- the verified caller (the `caller` dependency): the router only reads it;
- the secret store and `secret_refs`: warehouse credentials go through the same store as BYOK AI
  keys, and the reference is recorded in the caller's RLS-scoped transaction (`record_secret`).
The data layer's catalog is its own database (SHIPCUBE_DATA_CATALOG_URL, a schema per workspace,
provisioned by the migration role); this API never creates those schemas at request time.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Callable

from fastapi import APIRouter, FastAPI, HTTPException

from . import db

log = logging.getLogger("shipcube.api.data")
PATHS = [
    ("GET", "/warehouse"), ("PUT", "/warehouse"), ("POST", "/warehouse:test"),
    ("GET", "/cubes"), ("GET", "/cubes/{name}"),
    ("GET", "/migrations"), ("GET", "/migrations/{id}"), ("POST", "/migrations/{id}:apply"), ("POST", "/migrations/{id}:reject"),
]


def record_secret(caller: Any, *, kind: str, label: str, resource_name: str, version: str, last4: str, fingerprint: bytes) -> str:
    """Record a credential reference in secret_refs (never the value); retire the previous one with the same label."""
    ref_id = uuid.uuid4()
    with db.tx(caller) as conn:
        conn.execute("update secret_refs set destroyed_at = now() where workspace_id = %s and app_id is null and label = %s"
                     " and destroyed_at is null", (caller.workspace_id, label))
        conn.execute(
            "insert into secret_refs (id, workspace_id, kind, label, resource_name, version, last4, fingerprint, created_by)"
            " values (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (ref_id, caller.workspace_id, kind, label, resource_name, version, last4, fingerprint, caller.user_id))
    return str(ref_id)


def _secret_store() -> Any | None:
    try:  # the API's shared store (BYOK keys too), from the console endpoints (#35)
        from .workspace import secret_store

        return secret_store()
    except ImportError:
        return None  # PUT /v1/data/warehouse answers 501 until the store is available


def _verified_projects() -> Callable[[Any], list[str]]:
    try:  # the workspace's active linked GCP projects, read under the caller's RLS (#35)
        from .workspace import verified_projects

        return verified_projects
    except ImportError:
        return lambda _caller: []  # no GCP linking yet: only Postgres warehouses can be connected


def unavailable_router() -> APIRouter:
    router = APIRouter(tags=["data"])

    def handler() -> None:
        raise HTTPException(501, {"code": "data_unavailable",
                                  "message": "the data layer isn't installed on this server (services/api extra `data`)"})

    for method, path in PATHS:
        router.add_api_route(path, handler, methods=[method], name=f"data_unavailable_{method}_{path}")
    return router


def mount(app: FastAPI, caller: Callable[..., Any]) -> None:
    try:
        from shipcube_data.http import data_router
    except ImportError:
        log.warning("shipcube-data not installed; /v1/data/* answers 501")
        app.include_router(unavailable_router(), prefix="/v1/data")
        return
    # BigQuery warehouses must be in one of the workspace's verified GCP projects (workspace.verified_projects).
    app.include_router(data_router(caller_dependency=caller, secret_store=_secret_store(), record_secret=record_secret,
                                   verified_projects=_verified_projects()), prefix="/v1/data")
