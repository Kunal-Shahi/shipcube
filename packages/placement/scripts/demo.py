"""Print an estimate for the two sample apps.

    python packages/placement/scripts/demo.py           # Muse Spark if META_API_KEY is set
    python packages/placement/scripts/demo.py --rules   # rule-based explanations only
    python packages/placement/scripts/demo.py --json    # full EstimateResult JSON

Reads META_API_KEY / META_BASE_URL from the environment or the repo's .env (never printed).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from shipcube_placement import FallbackExplainer, RuleExplainer, estimate

SAMPLES = Path(__file__).resolve().parents[2] / "profiler" / "samples"
APPS = [
    ("dashboard", {"expected_users": 25}),
    ("nightly_batch", {"schedule": "0 2 * * *"}),
]


def inline(app_dir: Path) -> dict:
    files = [{"path": str(p.relative_to(app_dir)), "content": p.read_text()}
             for p in sorted(app_dir.rglob("*")) if p.is_file() and "__pycache__" not in p.parts]
    return {"kind": "inline", "files": files}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", action="store_true", help="skip Muse Spark")
    ap.add_argument("--json", action="store_true", help="print the full result")
    a = ap.parse_args()
    load_dotenv(find_dotenv(usecwd=True))
    for name, hints in APPS:
        explainer = RuleExplainer() if a.rules else FallbackExplainer()
        r = estimate(inline(SAMPLES / name), hints, explainer=explainer)
        if a.json:
            print(json.dumps(r.model_dump(), indent=2))
            continue
        c = r.monthly_cost_usd
        print(f"\n=== {name}  hints={hints}")
        print(f"class    {r.workload_class}  (confidence {r.confidence:.2f})")
        print(f"tier     {r.tier} -> {r.target}")
        res = r.resources
        print(f"size     cpu {res.cpu}, memory {res.memory}, instances {res.min_instances}-{res.max_instances}"
              + (f", concurrency {res.concurrency}" if res.concurrency else "")
              + (f", schedule '{res.schedule}'" if res.schedule else "") + f", timeout {res.timeout_s}s")
        print(f"cost     ${c.low:.2f} / ${c.expected:.2f} / ${c.high:.2f} per month (low/expected/high)")
        print(f"caps     {r.recommended_caps.model_dump(exclude_none=True)}")
        print(f"why      [{r.explained_by}] {r.reasoning}")
        for alt in r.alternatives:
            ac = alt.monthly_cost_usd
            print(f"alt      {alt.tier} {alt.target}: ~${ac.expected:.2f}/mo. {alt.tradeoff}")
        if r.profile.get("explainer_error"):
            print(f"note     Muse unavailable, used rules ({r.profile['explainer_error']})")


if __name__ == "__main__":
    main()
