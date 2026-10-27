"""Output contract of estimate_load (networking PR #1 §2.1 plus the Oct 28 decision
that adds `explained_by`). The MCP server returns EstimateResult.model_dump() as the
operation result."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Tier = Literal["T0", "T1", "T2", "T3", "T4"]
Target = Literal["cloud-run-service", "cloud-run-job", "cloud-run-gpu", "gke-autopilot",
                 "static-snapshot"]
WorkloadClass = Literal["batch_cron", "bursty_interactive", "steady_service", "memory_heavy",
                        "io_bound", "compute_heavy"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Caps(_Model):
    """Same fields and bounds as the control plane's Caps."""

    max_instances: int | None = Field(None, ge=0, le=10)
    concurrency: int | None = Field(None, ge=1, le=1000)
    requests_per_minute: int | None = Field(None, ge=1)
    monthly_spend_usd: float | None = Field(None, ge=0)
    cpu: Literal["0.5", "1", "2", "4"] | None = None
    memory: str | None = Field(None, pattern=r"^\d+(Mi|Gi)$")
    timeout_s: int | None = Field(None, ge=1, le=3600)


class Resources(_Model):
    cpu: str                         # vCPU per instance, Cloud Run notation ("0.5", "1", ...)
    memory: str                      # "512Mi", "2Gi", ...
    min_instances: int = 0
    max_instances: int = 1
    concurrency: int | None = None   # services only
    timeout_s: int = 300
    gpu: str | None = None           # "nvidia-l4"
    schedule: str | None = None      # cron, for jobs


class CostRange(_Model):
    low: float
    expected: float
    high: float


class Alternative(_Model):
    tier: Tier
    target: Target
    monthly_cost_usd: CostRange
    tradeoff: str


class EstimateResult(_Model):
    estimate_id: str
    workload_class: WorkloadClass
    confidence: float = Field(ge=0, le=1)
    tier: Tier
    target: Target
    resources: Resources
    monthly_cost_usd: CostRange
    profile: dict[str, Any]          # measurements, features, code summary, assumptions
    recommended_caps: Caps
    reasoning: str
    alternatives: list[Alternative]
    explained_by: str                # Muse model id, or "rules"
    decided_by: Literal["classifier", "rules"]   # who chose workload_class
    low_confidence: bool             # classifier under its gate or disagreeing with the rules
    model_version: str               # classifier version (registry), e.g. wc-20261027-ef8994a9
