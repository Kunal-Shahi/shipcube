"""The console's Data page (/v1/data/*), served by Kunal-Shahi/shipcube-data (`shipcube_data.http`).

The router is mounted from the optional `data` extra. Without it, the same routes answer 501
data_unavailable, so the contract (openapi.yaml) doesn't depend on what's installed.

What stays on the API's side:
- the verified caller (the `caller` dependency): the router only reads it;
- the secret store and `secret_refs`: warehouse credentials go through the same store as BYOK AI
  keys, and the reference is recorded in the caller's RLS-scoped transaction (`record_secret`).
The catalog lives in this database, schema `catalog`, with FORCE row-level security on
workspace_id = app_workspace() (security's design). Every data request (these routes and the MCP
data_* tools) runs in db.tx(caller) as shipcube_app; the catalog's DDL is applied by the migration
role together with the API's own migrations (`migrate_catalog`, from `admin migrate`).
"""

from __future__ import annotations

import functools
import logging
import os
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


@functools.cache
def tools() -> Any:
    """The data layer's tools for this process: every call runs in db.tx(caller) (the caller's RLS),
    on the shared catalog. Used by the /v1/data routes and by the MCP server's data_* tools."""
    from shipcube_data.local import tools_from_env

    return tools_from_env(catalog_session=lambda caller: db.tx(caller))


def migrate_catalog(conn_str: str) -> list[str]:
    """Apply the shared catalog's DDL (shipcube-data's catalog_migrations/postgres_shared), as the
    migration role, after the API's own migrations. A no-op without the `data` extra."""
    try:
        from shipcube_data.db import migrate_shared_catalog
    except ImportError:
        if os.environ.get("SHIPCUBE_DATA_OPTIONAL") == "1":
            return []
        raise RuntimeError("shipcube-data is not installed, so the data catalog can't be migrated: run with the `data` extra "
                           "(uv run --extra data), or set SHIPCUBE_DATA_OPTIONAL=1 for a deployment without the data layer") from None
    return migrate_shared_catalog(conn_str)


def check_catalog(*, migrate_if_dev: bool) -> None:
    """Like db.check_schema: DEV_MODE applies pending catalog DDL; production expects the migrate step."""
    try:
        from shipcube_data.db import Db
        from shipcube_data.model import ModelError
    except ImportError:
        return
    try:
        probe = Db(db.dsn(), shared=True, create_schema=False)
        pending = probe.pending_schema_migrations()
        probe.close()
    except ModelError:  # no catalog schema yet
        pending = ["all"]
    if not pending:
        return
    if migrate_if_dev:
        log.warning("DEV_MODE: applying the data catalog's migrations at startup", extra={"pending": pending})
        migrate_catalog(db.dsn())
        return
    log.error("the data catalog's schema is not current; run infra/db/migrate.sh", extra={"pending": pending})


def mount(app: FastAPI, caller: Callable[..., Any]) -> None:
    try:
        from shipcube_data.http import data_router
    except ImportError:
        log.warning("shipcube-data not installed; /v1/data/* answers 501")
        app.include_router(unavailable_router(), prefix="/v1/data")
        return
    # BigQuery warehouses must be in one of the workspace's verified GCP projects (workspace.verified_projects).
    app.include_router(data_router(caller_dependency=caller, tools=tools(), secret_store=_secret_store(),
                                   record_secret=record_secret, verified_projects=_verified_projects()), prefix="/v1/data")
