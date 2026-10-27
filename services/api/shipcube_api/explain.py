"""Explaining a load estimate.

The classifier produces the numbers (workload class, tier, resources, cost). An Explainer writes
the human-facing parts: `reasoning`, `alternatives` and `recommended_caps`. Muse Spark is the
default; if it is unavailable or errors, rule-based reasoning is used, and the result's
`explained_by` says which one ran.

MuseSparkExplainer is a mock until Meta Model API access lands. Replace `_complete` with the real
call (via packages/providers) and keep the output schema.
"""

from __future__ import annotations

import os
from typing import Any, Literal, Protocol

from pydantic import BaseModel

ExplainedBy = Literal["muse-spark", "rules"]


class Alternative(BaseModel):
    tier: str
    why_not: str


class Explanation(BaseModel):
    reasoning: str
    alternatives: list[Alternative]
    recommended_caps: dict[str, Any]
    explained_by: ExplainedBy


class Explainer(Protocol):
    name: ExplainedBy

    def explain(self, estimate: dict[str, Any]) -> Explanation: ...


class ExplainerUnavailable(Exception):
    pass


class RuleBasedExplainer:
    name: ExplainedBy = "rules"

    def explain(self, e: dict[str, Any]) -> Explanation:
        r, p, cost = e["resources"], e["profile"], e["monthly_cost_usd"]
        reasoning = (
            f"Classified as {e['workload_class']} ({e['confidence']:.0%} confidence): peak memory {p['peak_rss_mb']} MB, "
            f"{p['cpu_seconds']} CPU-seconds over {p['wall_seconds']} s. Tier {e['tier']} with {r['cpu']} vCPU / {r['memory']}, "
            f"scale {r['min_instances']}–{r['max_instances']}, expected ${cost['expected']:.2f}/month."
        )
        alternatives = [
            Alternative(tier=t, why_not=why)
            for t, why in (("T0", "Not chosen: output depends on interactive filters."),
                           ("T3", "Not chosen: idle most of the time, so always-on instances cost more."))
            if t != e["tier"]
        ]
        caps = {"max_instances": r["max_instances"], "requests_per_minute": 60 * max(1, r["concurrency"] // 20),
                "monthly_spend_usd": max(5, round(cost["high"] * 1.5))}
        return Explanation(reasoning=reasoning, alternatives=alternatives, recommended_caps=caps, explained_by=self.name)


class MuseSparkExplainer:
    """Mock of Muse Spark reading the code summary, profile and classifier output."""

    name: ExplainedBy = "muse-spark"

    def explain(self, e: dict[str, Any]) -> Explanation:
        if os.environ.get("SHIPCUBE_MUSE_UNAVAILABLE"):
            raise ExplainerUnavailable("Muse Spark is not configured")
        return Explanation(
            reasoning=(
                "Short CPU bursts per request with long idle gaps and modest memory. Scale-to-zero Cloud Run fits; "
                "two instances cover the expected 5 concurrent viewers."
            ),
            alternatives=[
                Alternative(tier="T0", why_not="Data refreshes hourly and viewers filter interactively, so a static snapshot is too stale."),
                Alternative(tier="T3", why_not="Always-on min instances would cost ~$15/month for no latency benefit at this load."),
            ],
            recommended_caps={"max_instances": 2, "requests_per_minute": 120, "monthly_spend_usd": 5},
            explained_by=self.name,
        )


def explain(estimate: dict[str, Any], primary: Explainer | None = None) -> Explanation:
    try:
        return (primary or MuseSparkExplainer()).explain(estimate)
    except Exception:  # Muse down, quota, bad output: never fail the estimate over prose
        return RuleBasedExplainer().explain(estimate)
