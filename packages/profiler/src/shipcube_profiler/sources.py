"""Materialise an estimate_load `Source` into a local directory.

Source shapes follow the control-plane contract (services/api models):
  {kind: "inline", files: [{path, content}]}   ≤50 files, ≤256 KB
  {kind: "git", url, ref?, subdir?}           https only; shallow, capped (see _clone)
  {kind: "upload", upload_id}                  tarball in GCS; needs a fetcher
  {kind: "image", uri}                         can't be profiled locally
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tarfile
import time
from collections.abc import Callable
from pathlib import Path, PurePosixPath

INLINE_MAX_FILES = 50
INLINE_MAX_BYTES = 256 * 1024
UNPACKED_MAX_FILES = 20_000      # the sandbox itself takes up to 10,000 (policy.py); bigger is refused
UNPACKED_MAX_BYTES = 1 * 2**30   # outright, beyond that the estimate is static (too_large)
GIT_MAX_BYTES = 256 * 2**20      # whole clone, .git included (matches the API's unpack budget)
GIT_MAX_BLOB_BYTES = 50 * 2**20  # any single file; larger blobs aren't fetched
GIT_CLONE_TIMEOUT_S = 120.0


class SourceError(ValueError):
    """The source is malformed or can't be fetched."""


class NotRunnable(Exception):
    """The source is valid but the profiler can't execute it (e.g. a prebuilt image)."""


# upload_id -> path of a local .tar.gz. In production this downloads from the
# private uploads bucket; tests and the demo pass a dict-backed fetcher.
UploadFetcher = Callable[[str], Path]


def _tree_bytes(path: Path) -> tuple[int, int]:
    total = files = 0
    for dirpath, _, names in os.walk(path):
        for n in names:
            try:
                total += os.lstat(os.path.join(dirpath, n)).st_size
                files += 1
            except OSError:
                continue
    return total, files


def _clone(url: str, ref: str | None, target: Path, allow_file: bool = False) -> Path:
    """A shallow, size- and time-capped clone of one branch.

    - `--depth 1 --single-branch --no-tags`: one commit, no history or tags.
    - `--filter=blob:limit`: blobs over GIT_MAX_BLOB_BYTES aren't sent, and with
      GIT_NO_LAZY_FETCH the checkout can't fetch them later, so the clone fails instead.
      Servers that ignore filters are still held by the size watchdog.
    - A watchdog kills git once the clone passes GIT_MAX_BYTES or GIT_CLONE_TIMEOUT_S.
    - No submodules, no LFS (smudge skipped, filters unset), no system or user git
      config (no insteadOf rewrites or credential helpers), https only (plus file in tests).
    """
    protocols = ["-c", "protocol.allow=never", "-c", "protocol.https.allow=always"]
    if allow_file:
        protocols += ["-c", "protocol.file.allow=always"]
    cmd = ["git", *protocols,
           "-c", "submodule.recurse=false", "-c", "core.hooksPath=/dev/null",
           "-c", "filter.lfs.smudge=", "-c", "filter.lfs.process=", "-c", "filter.lfs.required=false",
           "clone", "--quiet", "--depth", "1", "--single-branch", "--no-tags", "--no-recurse-submodules",
           f"--filter=blob:limit={GIT_MAX_BLOB_BYTES}"]
    if ref:
        cmd += ["--branch", ref]
    cmd += ["--", url, str(target)]
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": str(target.parent),
           "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_LFS_SKIP_SMUDGE": "1", "GIT_NO_LAZY_FETCH": "1", "GIT_ASKPASS": "/bin/false",
           # Takes precedence over protocol.* config, so redirects and submodule URLs are held too.
           "GIT_ALLOW_PROTOCOL": "https:file" if allow_file else "https"}
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            start_new_session=True)
    end = time.monotonic() + GIT_CLONE_TIMEOUT_S
    why = None
    while proc.poll() is None:
        if time.monotonic() > end:
            why = f"git clone took longer than {GIT_CLONE_TIMEOUT_S:.0f}s"
        elif target.exists() and _tree_bytes(target)[0] > GIT_MAX_BYTES:
            why = f"git clone is over {GIT_MAX_BYTES // 2**20} MiB"
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
            why += f" (a file is over the {GIT_MAX_BLOB_BYTES // 2**20} MiB per-file limit)"
    if why is None:
        size, files = _tree_bytes(target)
        if size > GIT_MAX_BYTES:
            why = f"git clone is over {GIT_MAX_BYTES // 2**20} MiB"
        elif files > UNPACKED_MAX_FILES:
            why = f"git clone has {files} files; the limit is {UNPACKED_MAX_FILES}"
    if why:
        shutil.rmtree(target, ignore_errors=True)
        raise SourceError(why)
    _strip_symlinks(target)
    return target


def _strip_symlinks(root: Path) -> int:
    """Delete every symlink under root, before anything reads the tree. A checkout or
    upload could otherwise point detection, packaging or the profiler at files outside
    it (/etc/passwd, the worker's credentials). Returns how many were removed."""
    removed = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in [*dirnames, *filenames]:
            p = os.path.join(dirpath, name)
            if os.path.islink(p):
                os.unlink(p)
                removed += 1
    return removed


def _safe_rel(path: str) -> PurePosixPath:
    p = PurePosixPath(path)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise SourceError(f"unsafe path in source: {path!r}")
    return p


def materialize(source: dict, dest: Path, *, fetch_upload: UploadFetcher | None = None,
                allow_file_urls: bool = False) -> Path:
    """Write the source's files under dest and return the app root directory.

    allow_file_urls permits file:// git sources (tests). Production refuses them: a tenant
    must never make the worker clone one of its local paths."""
    dest.mkdir(parents=True, exist_ok=True)
    kind = source.get("kind")
    if kind == "inline":
        files = source.get("files") or []
        if len(files) > INLINE_MAX_FILES:
            raise SourceError(f"inline sources are limited to {INLINE_MAX_FILES} files")
        total = sum(len(f["content"].encode()) for f in files)
        if total > INLINE_MAX_BYTES:
            raise SourceError("inline sources are limited to 256 KB")
        for f in files:
            target = dest / _safe_rel(f["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f["content"])
        return dest
    if kind == "git":
        url = source["url"]
        if not url.startswith(("https://", "file://") if allow_file_urls else "https://"):
            raise SourceError("git sources must be https:// URLs")
        root = _clone(url, source.get("ref"), dest / "repo", allow_file=allow_file_urls)
        if source.get("subdir"):
            root = root / _safe_rel(source["subdir"])
            if not root.is_dir() or root.is_symlink():
                raise SourceError(f"subdir {source['subdir']!r} not found in repo")
        return root
    if kind == "upload":
        if fetch_upload is None:
            raise SourceError("upload sources need an upload fetcher (GCS in production)")
        tarball = fetch_upload(source["upload_id"])
        with tarfile.open(tarball, "r:*") as tar:
            # Check the declared sizes before writing anything: a small compressed upload
            # can unpack to far more than the worker's disk.
            members = tar.getmembers()
            if len(members) > UNPACKED_MAX_FILES:
                raise SourceError(f"upload has {len(members)} entries; the limit is {UNPACKED_MAX_FILES}")
            if sum(m.size for m in members if m.isfile()) > UNPACKED_MAX_BYTES:
                raise SourceError(f"upload unpacks to more than {UNPACKED_MAX_BYTES // 2**20} MiB")
            tar.extractall(dest, members=members, filter="data")  # rejects absolute paths, links out of dest
        _strip_symlinks(dest)   # and no links inside it either, same as git
        return dest
    if kind == "image":
        raise NotRunnable("container images are profiled on GCP only; using static hints")
    raise SourceError(f"unknown source kind {kind!r}")
