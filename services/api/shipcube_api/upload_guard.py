"""Keeps one estimate's source from taking the worker down (red team on PR #55).

Estimates run as threads in the worker container, next to the deploy lane, and on Cloud Run /tmp is
memory. Before an uploaded tarball is handed to the profiler (which unpacks it with tarfile), this:

- walks it with tarfile's own stream decoder (`r|*`), so every format tarfile can open (gzip, xz,
  legacy .lzma, plain) is measured, and anything it can't open is refused. bzip2 is refused outright:
  one bzip2 block can expand about a million times in a single decompress call, before any budget sees it;
- feeds tarfile at most 1 KiB of compressed input per read, so one decompress step yields at most a
  few MiB (gzip ~1,000:1, xz/lzma ~7,000:1);
- counts decompressed bytes as tarfile reads them and stops at a budget (default 256 MiB,
  SHIPCUBE_ESTIMATE_UNPACK_MAX_MIB) before a read can go past it: a gzip bomb, or a pax header
  declaring gigabytes, costs at most the budget's worth of CPU and memory;
- refuses more than 20,000 members, declared file sizes adding up past the budget, paths longer than
  1,024 bytes (or a component over 255), absolute or `..` paths, sparse members, devices and fifos,
  and links that point outside the archive;
- lets at most 2 estimates (SHIPCUBE_ESTIMATE_SOURCE_SLOTS) materialise an upload or git source in this
  process at once, so the estimate threads together stay well under the worker's memory.
"""

from __future__ import annotations

import os
import posixpath
import tarfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .deploy.base import DeployError

RAW_READ = 1024
MAX_MEMBERS = 20_000
MAX_PATH = 1024
MAX_COMPONENT = 255


def unpack_max_bytes() -> int:
    return int(float(os.environ.get("SHIPCUBE_ESTIMATE_UNPACK_MAX_MIB") or 256) * 2**20)


def _refuse(reason: str) -> DeployError:
    return DeployError("invalid_argument", f"the upload was refused: {reason}")


class _Trickle:
    """The compressed file, at most RAW_READ bytes per read: bounds what one decompress step can produce."""

    def __init__(self, f: Any) -> None:
        self.f = f

    def read(self, size: int = -1) -> bytes:
        return self.f.read(RAW_READ if size is None or size < 0 else min(size, RAW_READ))


class _Budget:
    """tarfile's decompressed stream, refusing any read or seek that would pass `limit` bytes.
    Checked before the read, so a single oversized read (a pax header's declared size) never happens."""

    def __init__(self, stream: Any, limit: int) -> None:
        self.stream, self.limit = stream, limit

    def _check(self, end: int) -> None:
        if end > self.limit:
            raise _refuse(f"it unpacks to more than {self.limit // 2**20} MiB; estimates accept up to that")

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            raise _refuse("unbounded read")
        self._check(self.stream.tell() + size)
        return self.stream.read(size)

    def seek(self, pos: int) -> int:  # tarfile's stream only seeks forward, to an absolute offset
        self._check(pos)
        return self.stream.seek(pos)

    def __getattr__(self, name: str) -> Any:  # tell, close, mode, name
        return getattr(self.stream, name)


def _check_member(m: tarfile.TarInfo) -> None:
    name = m.name
    if len(name.encode()) > MAX_PATH or any(len(c.encode()) > MAX_COMPONENT for c in name.split("/")):
        raise _refuse(f"a path is longer than {MAX_PATH} bytes (or a part of it over {MAX_COMPONENT})")
    norm = posixpath.normpath(name)
    if name.startswith("/") or norm == ".." or norm.startswith("../"):
        raise _refuse(f"{name[:200]!r} points outside the archive")
    if m.issparse():
        raise _refuse(f"{name[:200]!r} is a sparse file")
    if m.ischr() or m.isblk() or m.isfifo() or not (m.isfile() or m.isdir() or m.issym() or m.islnk()):
        raise _refuse(f"{name[:200]!r} is not a regular file, directory or link")
    if m.issym() or m.islnk():
        base = posixpath.dirname(norm) if m.issym() else ""  # hard links are relative to the archive root
        target = posixpath.normpath(posixpath.join(base, m.linkname))
        if m.linkname.startswith("/") or target == ".." or target.startswith("../"):
            raise _refuse(f"{name[:200]!r} links outside the archive")


def check_tarball(path: Path, max_bytes: int | None = None) -> dict[str, int]:
    """Walk the tarball the way tarfile would read it and return {members, declared_bytes,
    decompressed_bytes}, or raise DeployError(invalid_argument) at the first limit it passes."""
    limit = unpack_max_bytes() if max_bytes is None else max_bytes

    class Guarded(tarfile.TarFile):
        def __init__(self, name=None, mode="r", fileobj=None, **kw):  # wraps tarfile's stream before it reads
            super().__init__(name, mode, _Budget(fileobj, limit), **kw)

    members = declared = 0
    try:
        with open(path, "rb") as raw:
            if raw.read(3) == b"BZh":
                raise _refuse("bzip2 archives aren't accepted; use gzip or xz")
            raw.seek(0)
            tar = Guarded.open(fileobj=_Trickle(raw), mode="r|*")
            with tar:
                for m in tar:
                    members += 1
                    if members > MAX_MEMBERS:
                        raise _refuse(f"it has more than {MAX_MEMBERS} entries")
                    _check_member(m)
                    declared += m.size if m.isfile() else 0
                    if declared > limit:
                        raise _refuse(f"its files add up to more than {limit // 2**20} MiB")
                decompressed = tar.fileobj.tell()
    except DeployError:
        raise
    except (tarfile.TarError, OSError, EOFError, ValueError) as e:
        raise DeployError("invalid_argument", f"the upload is not a readable archive: {e}") from e
    return {"members": members, "declared_bytes": declared, "decompressed_bytes": decompressed}


_slots: threading.BoundedSemaphore | None = None
_slots_lock = threading.Lock()


@contextmanager
def source_slot(source: dict) -> Iterator[None]:
    """Hold one of the process's source slots while an upload or git source is on local disk."""
    global _slots
    if source.get("kind") not in ("upload", "git"):
        yield
        return
    with _slots_lock:
        if _slots is None:
            _slots = threading.BoundedSemaphore(int(os.environ.get("SHIPCUBE_ESTIMATE_SOURCE_SLOTS") or 2))
    with _slots:
        yield
