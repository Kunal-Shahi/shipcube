"""Materialise an estimate_load `Source` into a local directory.

Source shapes follow the control-plane contract (services/api models):
  {kind: "inline", files: [{path, content}]}   ≤50 files, ≤256 KB
  {kind: "git", url, ref?, subdir?}
  {kind: "upload", upload_id}                  tarball in GCS; needs a fetcher
  {kind: "image", uri}                         can't be profiled locally
"""

from __future__ import annotations

import subprocess
import tarfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath

INLINE_MAX_FILES = 50
INLINE_MAX_BYTES = 256 * 1024


class SourceError(ValueError):
    """The source is malformed or can't be fetched."""


class NotRunnable(Exception):
    """The source is valid but the profiler can't execute it (e.g. a prebuilt image)."""


# upload_id -> path of a local .tar.gz. In production this downloads from the
# private uploads bucket; tests and the demo pass a dict-backed fetcher.
UploadFetcher = Callable[[str], Path]


def _safe_rel(path: str) -> PurePosixPath:
    p = PurePosixPath(path)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise SourceError(f"unsafe path in source: {path!r}")
    return p


def materialize(source: dict, dest: Path, *, fetch_upload: UploadFetcher | None = None) -> Path:
    """Write the source's files under dest and return the app root directory."""
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
        if not url.startswith(("https://", "file://")):
            raise SourceError("git sources must be https:// URLs")
        cmd = ["git", "clone", "--depth", "1", "--quiet"]
        if source.get("ref"):
            cmd += ["--branch", source["ref"]]
        try:
            subprocess.run([*cmd, url, str(dest / "repo")], check=True, capture_output=True,
                           timeout=120, env={"GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/bin:/opt/homebrew/bin"})
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            raise SourceError(f"git clone failed: {getattr(e, 'stderr', b'')[-300:]!r}") from e
        root = dest / "repo"
        if source.get("subdir"):
            root = root / _safe_rel(source["subdir"])
            if not root.is_dir():
                raise SourceError(f"subdir {source['subdir']!r} not found in repo")
        return root
    if kind == "upload":
        if fetch_upload is None:
            raise SourceError("upload sources need an upload fetcher (GCS in production)")
        tarball = fetch_upload(source["upload_id"])
        with tarfile.open(tarball, "r:*") as tar:
            tar.extractall(dest, filter="data")  # rejects absolute paths, links out of dest
        return dest
    if kind == "image":
        raise NotRunnable("container images are profiled on GCP only; using static hints")
    raise SourceError(f"unknown source kind {kind!r}")
