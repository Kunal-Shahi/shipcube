"""Smoke check: python -m shipcube_monitor <project> <region> <service> [window]

Set SHIPCUBE_MONITOR=gcp to read real Cloud Monitoring and Cloud Logging with your ADC.
"""

from __future__ import annotations

import json
import sys

from . import AppTarget, provider_from_env


def main() -> None:
    project, region, service, *rest = sys.argv[1:]
    t = AppTarget(app=service, project_id=project, region=region, service=service)
    mon = provider_from_env()
    out = {"metrics": mon.metrics(t, rest[0] if rest else "24h"), "logs": mon.logs(t, limit=5)}
    json.dump(out, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
