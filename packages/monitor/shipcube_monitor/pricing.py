"""Per-app cost estimate from usage x public Cloud Run list prices.

Prices are Cloud Run *request-based billing* list prices in USD, read from the Cloud Billing Catalog
API on 2026-10-27 (service 152E-C115-5142, "Cloud Run"):

| SKU id          | Description                               | Price               | Regions     |
|-----------------|-------------------------------------------|---------------------|-------------|
| 4856-B847-F1EB  | Services CPU (Request-based billing)      | $0.000024 / vCPU-s  | Tier 1      |
| 02A2-9231-36A6  | Services Memory (Request-based billing)   | $0.0000025 / GiB-s  | Tier 1      |
| 085C-A237-027A  | Services CPU Tier 2 (Request-based)       | $0.0000336 / vCPU-s | Tier 2      |
| 600C-3782-6708  | Services Memory Tier 2 (Request-based)    | $0.0000035 / GiB-s  | Tier 2      |
| 2DA5-55D3-E679  | Requests                                  | $0.40 / million, first 2M per month free | global |

asia-south1 (ShipCube's region) is in the Tier 1 SKUs. Human-readable source:
https://cloud.google.com/run/pricing

This is an estimate: it ignores the monthly free tier for CPU and memory (shared across the whole
billing account), committed-use discounts and network egress. The real number comes from the billing
export; the console labels this one "estimated".
"""

from __future__ import annotations

TIER1_REGIONS = {
    "asia-east1", "asia-northeast1", "asia-northeast2", "asia-south1", "europe-north1", "europe-southwest1",
    "europe-west1", "europe-west4", "europe-west8", "europe-west9", "me-west1", "us-central1", "us-east1",
    "us-east4", "us-east5", "us-south1", "us-west1",
}

PRICES = {
    1: {"vcpu_second": 0.000024, "gib_second": 0.0000025},
    2: {"vcpu_second": 0.0000336, "gib_second": 0.0000035},
}
REQUEST_PRICE_PER_MILLION = 0.40
FREE_REQUESTS_PER_MONTH = 2_000_000

SOURCE = "Cloud Billing Catalog, Cloud Run request-based billing SKUs (2026-10-27); cloud.google.com/run/pricing"


def region_tier(region: str) -> int:
    return 1 if region in TIER1_REGIONS else 2


def compute_cost(billable_instance_seconds: float, cpu: float, memory_mib: int, region: str) -> float:
    """Cost of the time instances were billable, at the app's configured CPU and memory."""
    p = PRICES[region_tier(region)]
    gib = memory_mib / 1024
    return billable_instance_seconds * (cpu * p["vcpu_second"] + gib * p["gib_second"])


def request_cost(requests: int, apply_free_tier: bool = False) -> float:
    billable = max(0, requests - FREE_REQUESTS_PER_MONTH) if apply_free_tier else requests
    return billable / 1_000_000 * REQUEST_PRICE_PER_MILLION
