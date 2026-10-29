"""Data-layer tools, mounted in-process with a `data_` prefix.

The tools come from Kunal-Shahi/shipcube-data (`shipcube_data.tools.DataTools`: `specs()` and
`call(name, args, caller)`). Only five are exposed; `apply_migration` never is, because agents
only propose model changes and a human applies them in the console.

`DataBackend` is the seam. `ShipcubeDataBackend` wraps the real library (install the `data`
extra). `StubDataBackend` returns canned results with the same names and scopes, so the MCP
server runs and tests pass without it.
"""

from __future__ import annotations

import inspect
import json
import logging
import time
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import anyio
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from .backend import Api, ApiError

# Where approval links point. Set per environment; the default is the local console.
CONSOLE_URL = os.environ.get("SHIPCUBE_CONSOLE_URL", "http://localhost:3000").rstrip("/")

log = logging.getLogger(__name__)

EXPOSED = ("list_cubes", "describe_cube", "query", "search_values", "propose_migration")
PREFIX = "data_"
# Security's token scopes (database-design.md §6) name the propose scope data:migrate; shipcube-data
# PR #1 still calls it data:write. Translate until the library renames it.
LIBRARY_SCOPE = {"data:migrate": "data:write"}
OUR_SCOPE = {v: k for k, v in LIBRARY_SCOPE.items()}
READ = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
PROPOSE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)


@dataclass(frozen=True)
class CallerInfo:
    """The verified caller, resolved by the API from the bearer token."""

    user_id: str
    email: str
    role: str
    workspace_id: str
    token_id: str | None
    scopes: frozenset[str]


class DataBackend(Protocol):
    name: str

    def specs(self) -> list[dict[str, Any]]:
        """[{name, description, inputSchema, scope}] for every tool the library offers."""

    def call(self, name: str, args: dict[str, Any], caller: CallerInfo) -> dict[str, Any]:
        """Run one tool. Raises DataToolError on a tool-level failure."""


class DataToolError(Exception):
    def __init__(self, code: str, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.payload = {"code": code, "message": message, **({"hint": hint} if hint else {})}


# --- real library ---------------------------------------------------------------------


class ShipcubeDataBackend:
    """shipcube-data on its demo warehouse (DuckDB in memory, Olist migrations).

    The library's catalog is a sqlite3 connection, which is bound to the thread that opened it,
    so one dedicated worker thread builds the layer and runs every call.

    The build (about 4.6 s on one Cloud Run vCPU) runs in the background so the server starts
    listening at once. The worker thread is FIFO, so a call that arrives first simply waits for it."""

    name = "shipcube-data"

    def __init__(self, workspace_id: str, migrations: Path) -> None:
        from shipcube_data.tools import DataTools

        self._layer: Any = None
        self._build_error: str | None = None
        self._tools = DataTools(lambda _ws: self._require_layer())  # specs need no layer
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="shipcube-data")
        self._worker.submit(self._build_logged, workspace_id, migrations)

    def _build_logged(self, workspace_id: str, migrations: Path) -> None:
        started = time.monotonic()
        try:
            self._build(workspace_id, migrations)
        except Exception as e:  # noqa: BLE001 — reported on every data call instead of crashing the server
            self._build_error = f"{type(e).__name__}: {e}"
            log.exception("data layer build failed")
            return
        log.info("data layer ready", extra={"seconds": round(time.monotonic() - started, 2)})

    def _require_layer(self) -> Any:
        if self._layer is None:
            raise DataToolError("data_unavailable", f"the data layer failed to start: {self._build_error}",
                                "Check the MCP server's logs for 'data layer build failed'.")
        return self._layer

    def _build(self, workspace_id: str, migrations: Path) -> None:
        import duckdb
        from shipcube_data import demo
        from shipcube_data.catalog import Catalog
        from shipcube_data.engine import DataLayer, duckdb_executor
        from shipcube_data.migrate import Runner, load_dir
        warehouse = duckdb.connect()
        demo.build(warehouse)
        runner = Runner(Catalog(workspace_id=workspace_id))
        runner.apply_all(load_dir(migrations), actor="shipcube-mcp")
        # Route callers' Google domains to the demo warehouse's tenant schemas. In production the console's
        # "Data access" page writes these attributes.
        for pair in os.environ.get("SHIPCUBE_DATA_DEMO_TENANTS", "example.com=acme").split(","):
            domain, _, tenant = pair.partition("=")
            runner.catalog.set_attribute("domain", domain.strip(), "tenant", tenant.strip(), by="system:shipcube-mcp")
        self._layer = DataLayer(runner.catalog, duckdb_executor(warehouse))

    def specs(self) -> list[dict[str, Any]]:
        return self._tools.specs()

    def call(self, name: str, args: dict[str, Any], caller: CallerInfo) -> dict[str, Any]:
        return self._worker.submit(self._call, name, args, caller).result()

    def _call(self, name: str, args: dict[str, Any], caller: CallerInfo) -> dict[str, Any]:
        from shipcube_data.identity import Caller
        from shipcube_data.tools import ToolError as LibError

        c = Caller(
            workspace_id=uuid.UUID(caller.workspace_id), via="mcp", email=caller.email,
            user_id=uuid.UUID(caller.user_id), membership_role=caller.role,
            # api_tokens.id is a UUID in security's schema; the skeleton's short ids map onto one.
            token_id=uuid.uuid5(uuid.NAMESPACE_URL, f"shipcube-token:{caller.token_id}") if caller.token_id else None,
            scopes=sorted(caller.scopes | {LIBRARY_SCOPE[s] for s in caller.scopes if s in LIBRARY_SCOPE}),
        )
        try:
            return self._tools.call(name, args, c)
        except LibError as e:
            raise DataToolError(e.code, e.message, e.hint) from e


# --- stub -------------------------------------------------------------------------------


class StubDataBackend:
    """Same tool names, scopes and rough shapes as shipcube-data, with canned results."""

    name = "stub"
    _SPECS = [
        ("list_cubes", "data:read", "List the cubes you can query, with their measure and dimension names. Start here.", {}),
        ("describe_cube", "data:read", "Full metadata for one cube: members, joins, partitioning.",
         {"cube": {"type": "string"}}),
        ("query", "data:read", "Run a typed semantic query (measures, dimensions, time, filters). mode=sql compiles only.",
         {"query": {"type": "object"}, "mode": {"type": "string", "enum": ["run", "sql"], "default": "run"}}),
        ("search_values", "data:read", "Distinct values of a dimension containing `text`.",
         {"dimension": {"type": "string"}, "text": {"type": "string", "default": ""}}),
        ("propose_migration", "data:migrate", "Propose a model change. Returns the diff and an approval_url; never applies it.",
         {"description": {"type": "string"}, "ops": {"type": "array", "items": {"type": "object"}}}),
        ("apply_migration", "data:migrate", "Apply an approved migration.", {"id": {"type": "string"}}),
    ]

    def specs(self) -> list[dict[str, Any]]:
        out = []
        for name, scope, desc, props in self._SPECS:
            required = [k for k, v in props.items() if "default" not in v]
            schema = {"type": "object", "properties": props, "required": required, "additionalProperties": False}
            out.append({"name": name, "description": desc, "inputSchema": schema, "scope": scope})
        return out

    def call(self, name: str, args: dict[str, Any], caller: CallerInfo) -> dict[str, Any]:
        if name == "list_cubes":
            return {"model_version": 5, "cubes": [{"name": "orders", "measures": ["revenue", "count"],
                                                   "dimensions": ["region", "created_at"], "joins": ["customers"]}]}
        if name == "describe_cube":
            if args["cube"] != "orders":
                raise DataToolError("not_found", f"no cube named {args['cube']}", "call data_list_cubes")
            return {"name": "orders", "measures": [{"name": "revenue", "type": "sum", "format": "currency"}],
                    "dimensions": [{"name": "region", "type": "string"}], "partitioning": {"by": "created_at", "grain": "month"}}
        if name == "query":
            return {"sql": "SELECT region, SUM(amount) AS revenue FROM orders GROUP BY 1", "level": "L0",
                    "columns": ["orders.region", "orders.revenue"], "rows": [["SP", 1284220.5], ["RJ", 512904.1]], "warnings": []}
        if name == "search_values":
            return {"dimension": args["dimension"], "values": ["SP", "RJ", "MG"]}
        if name == "propose_migration":
            mid = f"mig_{uuid.uuid4().hex[:8]}"
            return {"id": mid, "class": "additive", "diff": args["ops"],
                    "approval_url": f"{CONSOLE_URL}/w/{caller.workspace_id}/data/migrations/{mid}",
                    "auto_approved": False}
        raise DataToolError("unknown_tool", f"no tool named {name}")


def default_backend() -> DataBackend | None:
    """The real library, or None (data tools not mounted). The stub only in SHIPCUBE_DEV_MODE.

    shipcube-data PR #1 serves one catalog per process (its demo warehouse), bound to the workspace in
    SHIPCUBE_DATA_WORKSPACE. Per-workspace catalogs in Postgres are the data layer's next step."""
    dev = os.environ.get("SHIPCUBE_DEV_MODE") == "1"
    workspace_id = os.environ.get("SHIPCUBE_DATA_WORKSPACE")
    try:
        from shipcube_data import demo
    except ImportError:
        log.warning("shipcube-data not installed; %s", "using the DEV_MODE stub" if dev else "data_* tools are not mounted")
        return StubDataBackend() if dev else None
    migrations = Path(os.environ.get("SHIPCUBE_DATA_MIGRATIONS", demo.MIGRATIONS))
    if not migrations.is_dir():
        log.warning("shipcube-data migrations not found at %s; %s", migrations,
                    "using the DEV_MODE stub" if dev else "data_* tools are not mounted")
        return StubDataBackend() if dev else None
    if not workspace_id:
        log.warning("SHIPCUBE_DATA_WORKSPACE is not set; %s", "using the DEV_MODE stub" if dev else "data_* tools are not mounted")
        return StubDataBackend() if dev else None
    return ShipcubeDataBackend(workspace_id, migrations)


# --- mounting ---------------------------------------------------------------------------


async def resolve_caller(api: Api, scope: str) -> CallerInfo:
    t = get_access_token()
    try:
        me = await api.call("GET", "/v1/me", token=t.token if t else None)
    except ApiError as e:
        raise ToolError(str(e)) from e
    tok = me.get("token") or {}
    info = CallerInfo(me["user_id"], me["email"], me["role"], me["workspace"]["id"], tok.get("id"),
                      frozenset(tok.get("scopes", [])))
    if scope not in info.scopes:
        raise ToolError(f"permission_denied (403): token lacks scope {scope}")
    return info


def _make_handler(api: Api, backend: DataBackend, spec: dict[str, Any]):
    name, scope = spec["name"], OUR_SCOPE.get(spec["scope"], spec["scope"])
    props: dict[str, Any] = spec["inputSchema"].get("properties", {})
    required = set(spec["inputSchema"].get("required", []))

    async def handler(**kwargs: Any) -> dict[str, Any]:
        caller = await resolve_caller(api, scope)
        args = {k: v for k, v in kwargs.items() if v is not None or k in required}
        try:
            return await anyio.to_thread.run_sync(backend.call, name, args, caller)
        except DataToolError as e:
            raise ToolError(json.dumps(e.payload)) from e

    # The SDK builds its argument model from the signature; the library validates the real schema.
    handler.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        [inspect.Parameter(p, inspect.Parameter.KEYWORD_ONLY, default=inspect.Parameter.empty if p in required else None,
                           annotation=Any) for p in props],
        return_annotation=dict[str, Any],
    )
    handler.__name__ = PREFIX + name
    return handler


def mount_data_tools(server: MCPServer, api: Api, backend: DataBackend | None) -> list[str]:
    mounted: list[str] = []
    if backend is None:
        return mounted
    for spec in backend.specs():
        if spec["name"] not in EXPOSED:
            continue  # apply_migration (and anything new) stays off until reviewed
        name = PREFIX + spec["name"]
        server.add_tool(_make_handler(api, backend, spec), name=name, title=f"Data: {spec['name'].replace('_', ' ')}",
                        description=spec["description"], annotations=PROPOSE if spec["name"] == "propose_migration" else READ)
        server._tool_manager.get_tool(name).parameters = spec["inputSchema"]  # advertise the library's own schema
        mounted.append(name)
    return mounted
