"""Entry point inside the profiler sandbox (Cloud Run job, or the local Docker
container): profile the app at /app, print JSON.

    python -m shipcube_profiler.agent [APP_DIR]

Hints arrive as JSON in SHIPCUBE_HINTS. The job has no egress, so the result goes to
stdout as one line starting with RESULT_PREFIX; Cloud Run sends it to Cloud Logging and
CloudRunJobRunner reads it from the execution's log entries.

The line carries SHIPCUBE_RESULT_NONCE, a per-run secret from the runner, which only
accepts a line with its nonce. The app runs as the same uid as the agent, so the agent
first makes itself non-dumpable: the kernel then hides /proc/<agent>/environ, fd/ and
mem from the app, which can neither read the nonce nor write to the agent's stdout.
"""

from __future__ import annotations

import ctypes
import hmac
import json
import os
import sys
from pathlib import Path

from .detect import summarize
from .profile import Hints
from .runners import Limits, LocalRunner

RESULT_PREFIX = "SHIPCUBE_PROFILE "
NONCE_ENV = "SHIPCUBE_RESULT_NONCE"
PR_SET_DUMPABLE = 4


def _harden() -> bool:
    """Linux: make this process non-dumpable (see the module docstring)."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        return ctypes.CDLL(None, use_errno=True).prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) == 0
    except (OSError, AttributeError):
        return False


def _env_int(name: str) -> int | None:
    v = os.environ.get(name)
    return int(v) if v else None


def main() -> None:
    _harden()
    nonce = os.environ.pop(NONCE_ENV, "")
    app_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "/app")
    hints = Hints.from_any(json.loads(os.environ.get("SHIPCUBE_HINTS", "{}")))
    summary = summarize(app_dir)
    # The sandbox (container) enforces the hard limits; these keep the agent inside them
    # so it can measure and report instead of being killed mid-run.
    limits = Limits(timeout_s=float(os.environ.get("SHIPCUBE_AGENT_TIMEOUT_S", "540")),
                    memory_mb=int(os.environ.get("SHIPCUBE_AGENT_MEMORY_MB", "1800")),
                    max_nproc=_env_int("SHIPCUBE_AGENT_NPROC") or 256,
                    max_disk_mb=_env_int("SHIPCUBE_AGENT_DISK_MB") or 512)
    profile = LocalRunner(limits).run(app_dir, summary, hints)
    profile.runner = os.environ.get("SHIPCUBE_RUNNER_NAME", "gcp")
    # One line, prefixed, so the control plane can find it in the execution's logs.
    summary.excerpt = ""  # the control plane has the source; keep the log line small
    print(RESULT_PREFIX + json.dumps({"nonce": nonce, "summary": summary.to_dict(),
                                           "profile": profile.to_dict()}), flush=True)


def parse_result(lines, nonce: str) -> dict | None:
    """The agent's result among output lines: the one RESULT_PREFIX line carrying `nonce`.

    None if there is no result line at all. Result lines that don't carry the nonce (or
    more than one that does) mean something else printed them: ProfilerError with
    reason result_rejected."""
    from .errors import ProfilerError

    found, bad = [], 0
    for line in lines:
        if not line.startswith(RESULT_PREFIX):
            continue
        try:
            d = json.loads(line[len(RESULT_PREFIX):])
        except ValueError:
            bad += 1
            continue
        if isinstance(d, dict) and nonce and hmac.compare_digest(str(d.get("nonce", "")), nonce):
            found.append(d)
        else:
            bad += 1
    if len(found) == 1 and not bad:
        return found[0]
    if found or bad:
        raise ProfilerError("run", f"result line failed the integrity check ({len(found)} valid, {bad} forged)",
                            reason="result_rejected")
    return None


if __name__ == "__main__":
    main()
