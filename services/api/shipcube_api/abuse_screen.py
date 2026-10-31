"""The load estimator's abuse pre-screen (shipcube_profiler.prescreen), enforced on deploys and estimates.

prescreen() is static and fast: file names, requirements, regexes, one AST check, no symlinks followed.
Its verdict is ok, flagged (low-confidence signals, `prescreen_<code>`) or blocked (high-confidence
reasons: crypto_miner, attack_tool, phishing_kit). Here:
- every signal and every blocked reason (as `prescreen_<reason>`) is a strike (profiler_guard.record_abuse);
  `prescreen_crypto_miner` suspends the workspace's profiling until a person clears it, like the
  sandbox's mining_pool;
- a blocked source is refused with error code `abuse_refused`, before any build.
Image sources have no files and are skipped. The verdict's hits never contain the matched text.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from . import db, git_source, profiler_guard, upload_guard
from .deploy.base import DeployError
from .settings import on_cloud_run

log = logging.getLogger("shipcube.abuse_screen")
REFUSED = "abuse_refused"


def _prescreen():
    """The pre-screen, or None while the installed shipcube_profiler predates it. Production installs the
    profiler from the deployed commit (deploy/api.Dockerfile), so this only happens with a stale local or
    CI install of the pinned estimator extra."""
    try:
        from shipcube_profiler import prescreen
    except ImportError:
        return None
    return prescreen


def strikes(verdict: dict[str, Any]) -> list[str]:
    return sorted(set(verdict.get("signals") or []) | {f"prescreen_{r}" for r in verdict.get("reasons") or []})


def refusal(reasons: list[str]) -> DeployError:
    return DeployError(REFUSED, "ShipCube doesn't host this source: the abuse pre-screen found "
                                f"{', '.join(sorted(reasons))}. If this is a mistake, contact ShipCube.")


def enforce(op: dict[str, Any], verdict: dict[str, Any], source_key: str | None = None) -> None:
    """Record the verdict's strikes (once per source when source_key is given), then refuse a blocked source."""
    if s := strikes(verdict):
        with db.worker_tx() as conn:
            profiler_guard.record_abuse(conn, op, s, source_key)
    if verdict.get("verdict") == "blocked":
        log.error("abuse pre-screen refused a source", extra={"workspace_id": str(op["workspace_id"]),
                                                              "reasons": verdict.get("reasons")})
        raise refusal(list(verdict.get("reasons") or []))


def _digest(files: list[tuple[str, bytes]]) -> str:
    """A content hash over file names and contents, independent of how they were packed."""
    h = hashlib.sha256()
    for name, data in sorted(files):
        h.update(name.lstrip("./").encode() + b"\0" + hashlib.sha256(data).digest())
    return h.hexdigest()


def tarball_digest(path: Path) -> str:
    """_digest of a (guarded) tarball's regular files: the same code uploaded twice has the same key,
    whatever the archive's timestamps."""
    files = []
    with tarfile.open(path) as tar:
        for m in tar:
            if m.isfile():
                f = tar.extractfile(m)
                files.append((m.name, f.read() if f else b""))
    return _digest(files)


def source_key(deployer: Any, workspace_id: str, source: dict[str, Any], pins: dict[str, Any] | None = None) -> str | None:
    """What strikes are counted against: the source's content, so an estimate and the deploys that reuse
    a source share one key. Git: the commit (screened, or the ref's current one). Uploads: a hash of
    their files (the screened generation). Inline: a hash of its files. None when it can't be keyed."""
    pins, kind = pins or {}, (source or {}).get("kind")
    try:
        if kind == "git":
            sha = pins.get("commit") or git_source.resolve(source["url"], source.get("ref"))
            return f"git:{sha}" if sha else None
        if kind == "inline":
            return "files:" + _digest([(f["path"], base64.b64decode(f["content"]) if f.get("encoding") == "base64"
                                        else f["content"].encode()) for f in source.get("files") or []])
        if kind == "upload":
            with upload_guard.source_slot(source):
                tmp = Path(tempfile.mkdtemp(prefix="shipcube-key-"))
                try:
                    tarball = deployer.download_upload(workspace_id, source["upload_id"], tmp / "src.tgz",
                                                       generation=pins.get("generation"))
                    upload_guard.check_tarball(tarball)
                    return "files:" + tarball_digest(tarball)
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
    except Exception:  # noqa: BLE001 — unkeyed strikes still count, per operation
        log.warning("couldn't key a source for its strikes; counting them per operation", exc_info=True)
    return None


def screen(deployer: Any, workspace_id: str, source: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Materialize the source (like the estimator, under the same upload guard and slot) and pre-screen it.
    Returns (verdict, pins): pins name exactly what was screened, so the build uses the same bytes:
    {"generation": n} for an upload (the object generation read), {"commit": sha} for git (the resolved
    ref). None when there is nothing to screen (an image). On Cloud Run a missing pre-screen refuses the
    deploy: production never deploys unscreened."""
    if (source or {}).get("kind") not in ("upload", "inline", "git"):
        return None
    prescreen = _prescreen()
    if prescreen is None:
        if on_cloud_run():
            raise DeployError("screen_unavailable", "the abuse pre-screen isn't available, so ShipCube can't "
                                                    "deploy this source right now; try again later")
        log.warning("abuse pre-screen unavailable (shipcube_profiler too old); deploy not screened")
        return None
    from shipcube_profiler import materialize

    pins: dict[str, Any] = {}
    with upload_guard.source_slot(source):
        tmp = Path(tempfile.mkdtemp(prefix="shipcube-screen-"))
        try:
            def fetch_upload(upload_id: str) -> Path:
                pins["generation"] = deployer.upload_generation(workspace_id, upload_id)
                tarball = deployer.download_upload(workspace_id, upload_id, tmp / f"{upload_id}.tgz",
                                                   generation=pins["generation"])
                upload_guard.check_tarball(tarball)  # before tarfile sees it: bounded memory and CPU
                return tarball

            app_dir = materialize(source, tmp / "src", fetch_upload=fetch_upload)
            if source["kind"] == "git":
                pins["commit"] = git_source.head(tmp / "src" / "repo")  # the build fetches exactly this
            return prescreen(app_dir).to_dict(), pins
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

