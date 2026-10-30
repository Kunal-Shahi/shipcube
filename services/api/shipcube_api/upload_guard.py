"""Keeps one estimate's source from taking the worker down (red team on PR #55).

Estimates run as threads in the worker container, next to the deploy lane, and on Cloud Run /tmp is
memory. Before an uploaded tarball is handed to the profiler (which unpacks it with tarfile), this:

- decompresses it as a stream and stops at a byte budget (default 256 MiB, SHIPCUBE_ESTIMATE_UNPACK_MAX_MIB).
  The decompressed tar holds every file plus its headers, so its size bounds what unpacking writes. A
  gzip bomb costs at most the budget's worth of CPU, instead of tarfile's getmembers() inflating it all.
- lets at most 2 estimates (SHIPCUBE_ESTIMATE_SOURCE_SLOTS) materialise an upload or git source in this
  process at once, so the estimate threads together stay well under the worker's memory.
"""

from __future__ import annotations

import bz2
import gzip
import lzma
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from .deploy.base import DeployError

CHUNK = 1 << 20


def unpack_max_bytes() -> int:
    return int(float(os.environ.get("SHIPCUBE_ESTIMATE_UNPACK_MAX_MIB") or 256) * 2**20)


def _open(path: Path) -> BinaryIO:
    with open(path, "rb") as f:
        magic = f.read(6)
    if magic.startswith(b"\x1f\x8b"):
        return gzip.open(path, "rb")
    if magic.startswith(b"BZh"):
        return bz2.open(path, "rb")
    if magic.startswith(b"\xfd7zXZ\x00"):
        return lzma.open(path, "rb")
    return open(path, "rb")


def check_tarball(path: Path, max_bytes: int | None = None) -> int:
    """Return the tarball's decompressed size, or raise DeployError(invalid_argument) as soon as it
    passes max_bytes (or isn't a readable archive)."""
    limit = unpack_max_bytes() if max_bytes is None else max_bytes
    total = 0
    try:
        with _open(path) as f:
            while chunk := f.read(CHUNK):
                total += len(chunk)
                if total > limit:
                    raise DeployError("invalid_argument",
                                      f"the upload unpacks to more than {limit // 2**20} MiB; estimates accept up to that")
    except (OSError, EOFError, lzma.LZMAError) as e:
        raise DeployError("invalid_argument", f"the upload is not a readable archive: {e}") from e
    return total


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
