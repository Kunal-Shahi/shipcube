"""Profiler errors and the reasons an estimate falls back to static numbers.

An estimate is `mode="static"` with `static_reason` set to one of STATIC_REASONS. The
profiler sets the first group itself; the control plane sets the second by passing
`StaticRunner(reason=...)` as the runner (admission, budget and abuse decisions live
there, see docs/research/profiler-sandbox.md §6-8).
"""

from __future__ import annotations

STATIC_REASONS = {
    # Set by the profiler / estimate().
    "image_source": "container images aren't run; numbers are typical for this app shape",
    "gpu": "GPU libraries detected; the sandbox has no GPU, so the app wasn't run",
    "no_entrypoint": "no entrypoint found to run",
    "requirements_policy": "requirements.txt has lines the sandbox doesn't allow (URLs, VCS, options or custom indexes)",
    "too_large": "the app is over the sandbox's size limits",
    "build_failed": "the sandbox image didn't build (installs are wheels-only from PyPI, 300 s)",
    "did_not_start": "the web app never served a request in the sandbox",
    "app_failed": "the script exited with an error in the sandbox",
    "result_rejected": "the sandbox's result failed its integrity check",
    "profiler_error": "the profiler failed for an infrastructure reason",
    # Set by the control plane.
    "quota": "this workspace is over its profiling quota",
    "budget": "the profiling budget is used up for today",
    "suspended": "profiling is suspended for this workspace",
    "busy": "no profiler slot was free in time",
    "disabled": "the profiler is switched off",
}


class ProfilerError(RuntimeError):
    """The profiler failed at `stage` (config, policy, upload, build, job, run, logs).

    `reason` is the STATIC_REASONS code estimate() falls back with."""

    def __init__(self, stage: str, message: str, reason: str = "profiler_error"):
        super().__init__(f"{stage}: {message}")
        self.stage = stage
        assert reason in STATIC_REASONS, reason
        self.reason = reason
