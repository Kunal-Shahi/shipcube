"""Pydantic models for the control-plane contract (openapi.yaml).

The MCP tools reuse these models for their input and output schemas, so the agent-facing
contract and the HTTP contract cannot drift apart.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


INLINE_MAX_FILES = 100
INLINE_MAX_BYTES = 2 * 1024 * 1024  # a ~20-file, ~1 MB demo app fits with room to spare


class InlineFile(_Model):
    path: str = Field(max_length=300)
    content: str
    encoding: Literal["utf-8", "base64"] = "utf-8"


class InlineSource(_Model):
    kind: Literal["inline"]
    files: list[InlineFile] = Field(
        min_length=1, max_length=INLINE_MAX_FILES,
        description="Up to 100 files and 2 MB in total (content length). Use upload for anything bigger.",
    )

    @model_validator(mode="after")
    def _total_size(self) -> InlineSource:
        total = sum(len(f.content) for f in self.files)
        if total > INLINE_MAX_BYTES:
            raise ValueError(f"inline source is {total} bytes; the limit is {INLINE_MAX_BYTES}. Use create_upload instead")
        return self


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

# Same values as security's `operations` table. A rejected or expired approval cancels the operation.
OpKind = Literal["estimate", "deploy", "rollback", "delete", "restore", "purge", "set_caps", "set_access"]
OpStatus = Literal["awaiting_approval", "queued", "running", "succeeded", "failed", "cancelled"]
ApprovalAction = Literal["first_linked_deploy", "cap_raise", "domain_grant", "delete_app"]


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
    approval_url: str | None = Field(None, description="Set while status is awaiting_approval: a human approves or rejects in the console")
    approval_reasons: list[str] = Field(default_factory=list, description="Why this operation needs approval")
    approval_actions: list[ApprovalAction] = Field(default_factory=list, description="security's approvals.action values")
    approval_expires_at: str | None = Field(None, description="Unanswered approvals expire after 72 hours and cancel the operation")
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


class RollbackRequest(_Model):
    to: str = Field("previous", description='"previous", or a revision name from inspect_app status.revisions')


class Grant(_Model):
    principal: Principal
    role: Literal["viewer", "editor"] = "viewer"


class AccessChange(_Model):
    add: list[Grant] = Field(default_factory=list)
    remove: list[Principal] = Field(default_factory=list)


class ApprovalDecision(_Model):
    comment: str | None = Field(None, max_length=500)


class AiConfigRequest(_Model):
    provider: Literal["meta", "openai_compatible", "openai", "anthropic"] = "meta"
    model: str = "muse-spark"
    key_secret: str | None = Field(None, description="Secret name holding the BYOK API key")
    base_url: str | None = Field(None, description="For openai_compatible providers")


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
    role: Literal["owner", "admin", "developer", "viewer"] = Field(description="Workspace membership role")
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
    sync_state: str | None = Field(None, description="pending until the IAP binding is applied")


class AccessList(BaseModel):
    access: list[AccessBinding]


class AccessResult(AccessList):
    operation: Operation | None = Field(None, description="Set when some changes (domain: grants) await approval")


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
    deleted_at: str | None = None
    restorable_until: str | None = Field(None, description="Soft-deleted apps can be restored from the console until then")
    runtime: dict[str, Any] | None = Field(None, description="Live state from Cloud Run: ready, URL, revision, IAP, scaling")
    revisions: list[dict[str, Any]] = Field(default_factory=list, description="Recent deployments, newest first (for rollback)")


class EstimateResult(BaseModel):
    """Operation.result for kind=estimate. The classifier fills the numbers; the explainer
    (Muse Spark, or rules as fallback) fills reasoning, alternatives and recommended_caps."""

    estimate_id: str
    workload_class: str
    confidence: float
    tier: str
    target: str
    resources: dict[str, Any]
    monthly_cost_usd: dict[str, float]
    profile: dict[str, Any]
    reasoning: str
    alternatives: list[dict[str, str]]
    recommended_caps: dict[str, Any]
    explained_by: Literal["muse-spark", "rules"]


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
    operation: Operation | None = Field(None, description="Set when a raise above the workspace default awaits approval")


class ErrorBody(BaseModel):
    code: str
    message: str


class Error(BaseModel):
    error: ErrorBody
