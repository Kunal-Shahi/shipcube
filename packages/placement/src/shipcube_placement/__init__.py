"""Load estimation for ShipCube: estimate(source, hints) -> EstimateResult.

    from shipcube_placement import estimate
    result = estimate({"kind": "inline", "files": [...]}, {"expected_users": 25})
    result.model_dump()   # the estimate_load operation result
"""

from shipcube_profiler import AbuseRefused

from .estimate import estimate
from .explain import FallbackExplainer, MuseExplainer, RuleExplainer
from .types import Alternative, Caps, CostRange, EstimateResult, Resources

__all__ = ["AbuseRefused", "Alternative", "Caps", "CostRange", "EstimateResult", "FallbackExplainer",
           "MuseExplainer", "Resources", "RuleExplainer", "estimate"]
