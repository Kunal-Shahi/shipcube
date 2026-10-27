"""Pydantic models for the control-plane contract (openapi.yaml).

The MCP tools reuse these models for their input and output schemas, so the agent-facing
contract and the HTTP contract cannot drift apart.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

APP_NAME_PATTERN = r"^[a-z][a-z0-9-]{1,38}[a-z0-9]$"
AppName = Annotated[
    str,
    Field(pattern=APP_NAME_PATTERN, description="App name: lowercase letters, digits and hyphens. Becomes the Cloud Run service name."),
]
Principal = Annotated[
    str,
    Field(pattern=r"^(user|group|domain):.+$", description="user:alice@acme.com, group:analysts@acme.com or domain:acme.com"),
]
Tier = Literal["T0", "T1", "T2", "T3", "T4"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- sources -----------------------------------------------------------------------


class UploadSource(_Model):
    kind: Literal["upload"]
    upload_id: str = Field(description="From create_upload, after the tarball was PUT")


class GitSource(_Model):
    kind: Literal["git"]
    url: str
    ref: str | None = None
    subdir: str | None = None


class ImageSource(_Model):
    kind: Literal["image"]
    uri: str = Field(description="Container image, e.g. us-docker.pkg.dev/p/r/app@sha256:...")


class InlineFile(_Model):
    path: str
    content: str


class InlineSource(_Model):
    kind: Literal["inline"]
    files: list[InlineFile] = Field(max_length=50, description="Small apps only (< 256 KB total)")


Source = Annotated[
    UploadSource | GitSource | ImageSource | InlineSource,
    Field(discriminator="kind", description="Where the app code comes from. Prefer upload for local projects."),
]


# --- caps --------------------------------------------------------------------------


class Caps(_Model):
    """Per-app limits. Omitted fields keep their current value."""

    max_instances: int | None = Field(None, ge=0, le=10)
    concurrency: int | None = Field(None, ge=1, le=1000)
    requests_per_minute: int | None = Field(None, ge=1)
    monthly_spend_usd: float | None = Field(None, ge=0)
    cpu: Literal["0.5", "1", "2", "4"] | None = None
    memory: str | None = Field(None, pattern=r"^\d+(Mi|Gi)$")
    timeout_s: int | None = Field(None, ge=1, le=3600)


# --- operations --------------------------------------------------------------------

OpKind = Literal["estimate", "deploy", "delete"]
OpStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


class Progress(BaseModel):
    step: str
    completed_steps: int
    total_steps: int
    message: str


class Step(BaseModel):
    name: str
    status: str


class OpError(BaseModel):
    code: str
    message: str


class Operation(BaseModel):
    id: str
    kind: OpKind
    app: str | None
    status: OpStatus
    done: bool
    progress: Progress
    steps: list[Step]
    result: Any = None
    error: OpError | None = None
    poll_after_s: int
    created_at: str
    updated_at: str


# --- requests ----------------------------------------------------------------------


class UploadRequest(_Model):
    content_type: str = "application/gzip"


class EstimateRequest(_Model):
    source: Source
    app: AppName | None = None
    expected_users: int | None = Field(None, ge=1, description="Rough number of people who will open the app")
    schedule: str | None = Field(None, description="Cron expression if this is a scheduled job")


class DeployRequest(_Model):
    source: Source
    estimate_id: str | None = Field(None, description="Use the tier and sizing from a finished estimate_load")
    tier: Tier | None = Field(None, description="Override the estimated tier")
    target: str | None = Field(None, description="GCP target id from whoami; defaults to the workspace default")
    region: str = "us-central1"
    env: dict[str, str] | None = Field(None, description="Non-secret environment variables")
    secrets: dict[str, str] | None = Field(None, description="ENV_NAME -> secret name in ShipCube secrets (never values)")
    caps: Caps | None = None


class GrantRequest(_Model):
    principal: Principal
    role: Literal["viewer", "editor"] = "viewer"


class AiConfigRequest(_Model):
    provider: Literal["meta", "openai_compatible", "openai", "anthropic"] = "meta"
    model: str = "muse-spark"
    key_secret: str | None = Field(None, description="Secret name holding the BYOK API key")
    base_url: str | None = Field(None, description="For openai_compatible providers")


class DataSourceRequest(_Model):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,40}$")
    kind: Literal["postgres", "bigquery", "duckdb", "snowflake"]
    connection_secret: str | None = Field(None, description="Secret name holding the DSN or service-account key")
    model_yaml: str | None = Field(None, description="Semantic DSL (cubes) for this source; inferred from the schema if omitted")


class SemanticQuery(_Model):
    measures: list[Any] = Field(min_length=1)
    dimensions: list[str] | None = None
    time: dict[str, Any] | None = None
    filters: Any = None
    order: list[tuple[str, Literal["asc", "desc"]]] | None = None
    limit: int | None = Field(None, le=10_000)


class QueryRequest(_Model):
    datasource: str | None = None
    query: SemanticQuery


# --- responses ---------------------------------------------------------------------


class Workspace(BaseModel):
    id: str
    name: str


class GcpTarget(BaseModel):
    id: str
    project: str
    kind: Literal["shipcube", "linked"]
    default: bool


class Limits(BaseModel):
    max_apps: int
    monthly_spend_usd: float


class TokenInfo(BaseModel):
    id: str
    scopes: list[str]
    expires_at: int


class Me(BaseModel):
    user_id: str
    email: str
    workspace: Workspace
    gcp_targets: list[GcpTarget]
    limits: Limits
    token: TokenInfo | None = Field(None, description="The token used for this request")


class Upload(BaseModel):
    upload_id: str
    method: Literal["PUT"]
    url: str
    headers: dict[str, str]
    max_bytes: int
    expires_at: str


class AccessBinding(BaseModel):
    principal: str
    role: str
    granted_at: str


class AccessList(BaseModel):
    access: list[AccessBinding]


class AiConfig(BaseModel):
    provider: str
    model: str
    key_secret: str | None


class AiConfigResult(BaseModel):
    ai: AiConfig
    gateway_url: str


class AppSummary(BaseModel):
    name: str
    url: str
    tier: str
    status: str
    updated_at: str


class AppList(BaseModel):
    apps: list[AppSummary]


class App(AppSummary):
    target: str
    region: str
    revision: str
    image: str
    caps: dict[str, Any]
    access: list[AccessBinding]
    ai: AiConfig


class LogEntry(BaseModel):
    timestamp: str
    severity: str
    message: str
    revision: str | None = None


class Logs(BaseModel):
    entries: list[LogEntry]
    next_page_token: str | None


class Latency(BaseModel):
    p50: float
    p95: float


class Instances(BaseModel):
    avg: float
    max: float


class Metrics(BaseModel):
    app: str
    window: str
    requests: int
    error_rate: float
    latency_ms: Latency
    instances: Instances
    cold_starts: int
    cpu_utilization: float
    memory_utilization: float
    cost_usd: dict[str, float]
    caps_hit: dict[str, int]
    data_freshness_minutes: float | None


class CapsResult(BaseModel):
    caps: dict[str, Any]
    enforced_by: dict[str, str]


class DataSource(BaseModel):
    datasource_id: str
    name: str
    kind: str
    egress: str
    cubes_detected: list[str]
    status: str


class Column(BaseModel):
    name: str
    type: str


class QueryResult(BaseModel):
    level: Literal["L0", "L1", "L2", "L3"]
    sql: str
    columns: list[Column]
    rows: list[list[Any]]
    cached: bool
    verified: bool
    datasource: str


class ErrorBody(BaseModel):
    code: str
    message: str


class Error(BaseModel):
    error: ErrorBody
