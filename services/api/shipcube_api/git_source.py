"""Cloning a tenant's git URL in a ShipCube process, safely.

Mirrors the profiler's `_clone` (packages/profiler sources.py, PR #57 commit fb8e9c1), which the API
can't import: shipcube_profiler is an optional extra. Keep the two in step.

- https only (`protocol.allow=never` with https allowed); file:// only when a test passes allow_file,
  so a tenant can never make the worker clone one of its local paths.
- `--depth 1 --single-branch --no-tags`: one commit, no history or tags.
- `--filter=blob:limit=50M` with GIT_NO_LAZY_FETCH: an oversized file fails the clone. Servers
  that ignore filters are still held by the size watchdog.
- No submodules, no LFS (smudge skipped, filters unset), no hooks, no system or user git config (no
  insteadOf rewrites or credential helpers), no prompts.
- A watchdog kills git once the clone passes 256 MiB (.git included) or 120 s, and removes it; more than
  20,000 files is refused too. Failures are DeployError(invalid_argument), which isn't retried.

Hosted deploys don't clone here: Cloud Build's gitSource fetches the repo inside the workspace's own
builder (sc-build-<ws>), under the build timeout. The URL rule is shared through models.GitSource.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from .deploy.base import DeployError

MAX_BYTES = 256 * 2**20
MAX_BLOB_BYTES = 50 * 2**20
MAX_FILES = 20_000
TIMEOUT_S = 120.0


def _tree(path: Path) -> tuple[int, int]:
    total = files = 0
    for dirpath, _, names in os.walk(path):
        for n in names:
            try:
                total += os.lstat(os.path.join(dirpath, n)).st_size
                files += 1
            except OSError:
                continue
    return total, files


def clone(url: str, ref: str | None, target: Path, *, allow_file: bool = False,
          max_bytes: int = MAX_BYTES, timeout_s: float = TIMEOUT_S) -> Path:
    """A shallow, size- and time-capped clone of one branch or tag into `target` (absent or empty)."""
    if not url.startswith(("https://", "file://") if allow_file else "https://"):
        raise DeployError("invalid_argument", "git sources must be https:// URLs")
    protocols = ["-c", "protocol.allow=never", "-c", "protocol.https.allow=always"]
    if allow_file:
        protocols += ["-c", "protocol.file.allow=always"]
    cmd = ["git", *protocols,
           "-c", "submodule.recurse=false", "-c", "core.hooksPath=/dev/null",
           "-c", "filter.lfs.smudge=", "-c", "filter.lfs.process=", "-c", "filter.lfs.required=false",
           "clone", "--quiet", "--depth", "1", "--single-branch", "--no-tags", "--no-recurse-submodules",
           f"--filter=blob:limit={MAX_BLOB_BYTES}"]
    if ref:
        cmd += ["--branch", ref]
    cmd += ["--", url, str(target)]
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": str(target.parent),
           "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_LFS_SKIP_SMUDGE": "1", "GIT_NO_LAZY_FETCH": "1", "GIT_ASKPASS": "/bin/false"}
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
    end = time.monotonic() + timeout_s
    why = None
    while proc.poll() is None:
        if time.monotonic() > end:
            why = f"git clone took longer than {timeout_s:.0f}s"
        elif target.exists() and _tree(target)[0] > max_bytes:
            why = f"git clone is over {max_bytes // 2**20} MiB"
        if why:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            break
        time.sleep(0.1)
    err = proc.stderr.read().decode(errors="replace")[-300:] if proc.stderr else ""
    if why is None and proc.returncode != 0:
        why = f"git clone failed: {err.strip()!r}"
        if "lazy fetch" in err or "unable to read" in err or "missing" in err:
            why += f" (a file is over the {MAX_BLOB_BYTES // 2**20} MiB per-file limit)"
    if why is None:
        size, files = _tree(target)
        if size > max_bytes:
            why = f"git clone is over {max_bytes // 2**20} MiB"
        elif files > MAX_FILES:
            why = f"git clone has {files} files; the limit is {MAX_FILES}"
    if why:
        shutil.rmtree(target, ignore_errors=True)
        raise DeployError("invalid_argument", why)
    return target
