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
import threading
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
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


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


def _git_env(home: Path, allow_file: bool) -> dict:
    return {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": str(home),
            "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_LFS_SKIP_SMUDGE": "1", "GIT_NO_LAZY_FETCH": "1", "GIT_ASKPASS": "/bin/false",
            # Takes precedence over protocol.* config, so redirects and submodule URLs are held too.
            "GIT_ALLOW_PROTOCOL": "https:file" if allow_file else "https",
            # git >= 2.40 (the worker image has Debian's 2.47): read attributes from the empty
            # tree, so the repo's .gitattributes can't change what the checkout writes.
            "GIT_ATTR_SOURCE": EMPTY_TREE}


def _git_config(allow_file: bool) -> list[str]:
    """`-c` options for every git command on an untrusted repo."""
    protocols = ["-c", "protocol.allow=never", "-c", "protocol.https.allow=always"]
    if allow_file:
        protocols += ["-c", "protocol.file.allow=always"]
    return [*protocols, "-c", "core.symlinks=false", "-c", "submodule.recurse=false",
            "-c", "core.hooksPath=/dev/null", "-c", "filter.lfs.smudge=", "-c", "filter.lfs.process=",
            "-c", "filter.lfs.required=false", "-c", f"core.attributesFile={os.devnull}",
            "-c", "core.autocrlf=false", "-c", "core.eol=lf"]


def _run_git(step: str, args: list[str], env: dict, deadline: float, watch: Path | None,
             stdin: str | None = None) -> tuple[int, str, str]:
    """Run git under the deadline; while it runs, kill it once `watch` passes
    GIT_MAX_BYTES (a backstop: the exact check is the tree listing). Returns
    (returncode, stdout, why-it-was-killed)."""
    proc = subprocess.Popen(["git", *args], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                            start_new_session=True)
    if stdin is not None:
        proc.stdin.write(stdin.encode())
        proc.stdin.close()
    out: list[bytes] = []
    reader = threading.Thread(target=lambda: out.append(proc.stdout.read()), daemon=True)
    reader.start()
    why = ""
    while proc.poll() is None:
        if time.monotonic() > deadline:
            why = f"git clone took longer than {GIT_CLONE_TIMEOUT_S:.0f}s"
        elif watch is not None and watch.exists() and _tree_bytes(watch)[0] > GIT_MAX_BYTES:
            why = f"git clone is over {GIT_MAX_BYTES // 2**20} MiB"
        if why:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            break
        time.sleep(0.05)
    reader.join(timeout=5)
    err = proc.stderr.read().decode(errors="replace")[-300:] if proc.stderr else ""
    if not why and proc.returncode != 0:
        why = f"git {step} failed: {err.strip()!r}"
        if "lazy fetch" in err or "unable to read" in err or "missing" in err or "not available" in err:
            why += f" (a file is over the {GIT_MAX_BLOB_BYTES // 2**20} MiB per-file limit)"
    return proc.returncode, (out[0] if out else b"").decode(errors="replace"), why


def _checkout_size(listing: str) -> tuple[int, int]:
    """Total bytes and file count a checkout would write, from `git ls-tree -r -l -z`."""
    total = files = 0
    for entry in filter(None, listing.split("\0")):
        meta = entry.split("\t", 1)[0].split()
        if len(meta) == 4 and meta[1] == "blob":
            if not meta[3].isdigit():   # the blob wasn't sent: over the per-file limit
                raise SourceError(f"a file is over the {GIT_MAX_BLOB_BYTES // 2**20} MiB per-file limit")
            total += int(meta[3])
            files += 1
    return total, files


def _reject_symlinks(root: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in [*dirnames, *filenames]:
            if os.path.islink(os.path.join(dirpath, name)):
                raise SourceError(f"the checkout contains a symlink ({name}); symlinks aren't allowed")


def _clone(url: str, ref: str | None, target: Path, allow_file: bool = False) -> Path:
    """A shallow, size- and time-capped clone of one branch, in three steps:

    1. `clone --no-checkout --depth 1 --single-branch --no-tags --no-recurse-submodules
       --filter=blob:limit`: one commit's objects, nothing written to the work tree.
       Blobs over GIT_MAX_BLOB_BYTES aren't sent, and with GIT_NO_LAZY_FETCH nothing can
       fetch them later, so such a repo fails.
    2. `ls-tree -r -l`: the exact size and file count the checkout would write. Over
       GIT_MAX_BYTES or UNPACKED_MAX_FILES is refused before writing anything (a polling
       watchdog alone can overshoot by GBs on one zero-filled blob repeated many times).
    3. Checkout that writes exactly what ls-tree counted: the repo's .gitattributes are
       ignored (GIT_ATTR_SOURCE = the empty tree, and on older git they're dropped from
       the index first), so `working-tree-encoding`, `eol` or filters can't inflate it
       (UTF-32 turned 4 MB into 16 MB). `core.symlinks=false`: links become small plain
       files holding their target path; any link left afterwards is refused.

    Every step shares one GIT_CLONE_TIMEOUT_S deadline and a size watchdog as a backstop.
    No submodules, no LFS, no hooks, no system or user git config, https only (plus
    file:// in tests).
    """
    deadline = time.monotonic() + GIT_CLONE_TIMEOUT_S
    env = _git_env(target.parent, allow_file)
    cfg = _git_config(allow_file)
    clone = [*cfg, "clone", "--quiet", "--no-checkout", "--depth", "1", "--single-branch", "--no-tags",
             "--no-recurse-submodules", f"--filter=blob:limit={GIT_MAX_BLOB_BYTES}"]
    if ref:
        clone += ["--branch", ref]
    clone += ["--", url, str(target)]
    try:
        code, _, why = _run_git("clone", clone, env, deadline, target)
        if not why:
            code, listing, why = _run_git("ls-tree", [*cfg, "-C", str(target), "ls-tree", "-r", "-l", "-z", "HEAD"],
                                          env, deadline, None)
            if not why:
                size, files = _checkout_size(listing)
                if size > GIT_MAX_BYTES:
                    why = f"the checkout would be {size / 2**20:.0f} MiB; the limit is {GIT_MAX_BYTES // 2**20} MiB"
                elif files > UNPACKED_MAX_FILES:
                    why = f"the checkout has {files} files; the limit is {UNPACKED_MAX_FILES}"
        if not why:
            why = _checkout(target, cfg, env, deadline)
        if not why and _tree_bytes(target)[0] > GIT_MAX_BYTES:
            why = f"git clone is over {GIT_MAX_BYTES // 2**20} MiB"
        if not why:
            _reject_symlinks(target)
    except SourceError:
        shutil.rmtree(target, ignore_errors=True)
        raise
    if why:
        shutil.rmtree(target, ignore_errors=True)
        raise SourceError(why)
    return target


def _checkout(target: Path, cfg: list[str], env: dict, deadline: float) -> str:
    """Write HEAD's files without the repo's attributes (see _clone step 3)."""
    git = [*cfg, "-C", str(target)]
    (target / ".git" / "info" / "attributes").unlink(missing_ok=True)   # never from the remote; just in case
    _, _, why = _run_git("checkout", [*git, "read-tree", "HEAD"], env, deadline, None)
    if why:
        return why
    _, listing, why = _run_git("checkout", [*git, "ls-files", "-z"], env, deadline, None)
    attrs = [f for f in listing.split("\0") if f and f.rsplit("/", 1)[-1] == ".gitattributes"]
    if not why and attrs:
        _, _, why = _run_git("checkout", [*git, "update-index", "--force-remove", "-z", "--stdin"],
                             env, deadline, None, stdin="\0".join(attrs))
    if not why:
        _, _, why = _run_git("checkout", [*git, "checkout-index", "-a", "-f"], env, deadline, target)
    return why


def _inside(root: Path, rel: str) -> Path:
    """root/rel, refusing anything that leaves root or passes through a symlink."""
    path = root
    for part in _safe_rel(rel).parts:
        path = path / part
        if path.is_symlink():
            raise SourceError(f"subdir {rel!r} goes through a symlink")
    real_root, real = root.resolve(), path.resolve()
    if real != real_root and real_root not in real.parents:
        raise SourceError(f"subdir {rel!r} is outside the checkout")
    return path


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
            root = _inside(root, source["subdir"])
            if not root.is_dir():
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
