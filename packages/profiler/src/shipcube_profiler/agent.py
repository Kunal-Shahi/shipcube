"""Entry point inside the GCP profiler job: profile the app at /app, print JSON.

    python -m shipcube_profiler.agent [APP_DIR]

Hints arrive as JSON in SHIPCUBE_HINTS. The job has no egress, so the result goes to
stdout as one line starting with RESULT_PREFIX; Cloud Run sends it to Cloud Logging and
CloudRunJobRunner reads it from the execution's log entries.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .detect import summarize
from .profile import Hints
from .runners import Limits, LocalRunner

RESULT_PREFIX = "SHIPCUBE_PROFILE "


def main() -> None:
    app_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "/app")
    hints = Hints.from_any(json.loads(os.environ.get("SHIPCUBE_HINTS", "{}")))
    summary = summarize(app_dir)
    profile = LocalRunner(Limits(timeout_s=540)).run(app_dir, summary, hints)
    profile.runner = "gcp"
    # One line, prefixed, so the control plane can find it in the execution's logs.
    summary.excerpt = ""  # the control plane has the source; keep the log line small
    print(RESULT_PREFIX + json.dumps({"summary": summary.to_dict(), "profile": profile.to_dict()}), flush=True)


if __name__ == "__main__":
    main()
