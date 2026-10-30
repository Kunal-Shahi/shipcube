"""What the sandbox agrees to build and run (docs/research/profiler-sandbox.md §4-5).

- Requirements: plain PyPI names with extras, version specifiers, markers and hashes.
  No URLs, VCS, paths, options or custom indexes. The image installs them with
  `--only-binary=:all:` from pypi.org, so no package code runs at build time.
- Size: requirement count, unpacked source and image size are capped; over a cap the
  estimate falls back to static (reason too_large).
"""

from __future__ import annotations

import re
from pathlib import Path

from .errors import ProfilerError

MAX_REQUIREMENTS = 60
MAX_SOURCE_BYTES = 500 * 2**20
MAX_SOURCE_FILES = 10_000
MAX_IMAGE_BYTES = 2 * 2**30
BUILD_TIMEOUT_S = 300
PYPI_INDEX = "https://pypi.org/simple"

_NAME = r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"
_EXTRAS = r"(?:\s*\[\s*" + _NAME + r"(?:\s*,\s*" + _NAME + r")*\s*\])?"
_SPEC = r"(?:===|==|!=|<=|>=|~=|<|>)\s*[A-Za-z0-9.*+!_-]+"
_SPECS = r"(?:\s*\(?\s*" + _SPEC + r"(?:\s*,\s*" + _SPEC + r")*\s*\)?)?"
_MARKER = r"(?:\s*;\s*[A-Za-z0-9_.'\" <>=!~()-]+)?"
_HASHES = r"(?:\s+--hash=sha256:[0-9a-f]{64})*"
REQUIREMENT = re.compile(r"^" + _NAME + _EXTRAS + _SPECS + _MARKER + _HASHES + r"\s*$")
# PEP 508 environment markers: these names, quoted strings, comparison ops, and/or/in/not.
_MARKER_TOKEN = re.compile(
    r"\s*(?:python_version|python_full_version|os_name|sys_platform|platform_release|platform_system"
    r"|platform_version|platform_machine|platform_python_implementation|implementation_name"
    r"|implementation_version|extra|and|or|not|in|'[^']*'|\"[^\"]*\"|===|==|!=|<=|>=|~=|<|>|\(|\))")


def _marker_ok(marker: str) -> bool:
    pos, marker = 0, marker.strip()
    while pos < len(marker):
        m = _MARKER_TOKEN.match(marker, pos)
        if not m or m.end() == pos:
            return False
        pos = m.end()
    return True


def sanitize_requirements(text: str) -> list[str]:
    """The requirement lines to install, or ProfilerError(reason=requirements_policy|too_large)."""
    out: list[str] = []
    for raw in text.replace("\\\n", " ").splitlines():
        line = re.sub(r"(^|\s)#.*$", "", raw).strip()
        if not line:
            continue
        marker = line.split(";", 1)[1].split(" --hash=")[0] if ";" in line else ""
        if "://" in line or "@" in line or not REQUIREMENT.match(line) or not _marker_ok(marker):
            raise ProfilerError("policy", f"requirements line not allowed in the sandbox: {line[:80]!r}",
                                reason="requirements_policy")
        out.append(re.sub(r"\s+", " ", line))
    if len(out) > MAX_REQUIREMENTS:
        raise ProfilerError("policy", f"{len(out)} requirements; the sandbox allows {MAX_REQUIREMENTS}",
                            reason="too_large")
    return out


def check_source_size(files: list[Path]) -> None:
    total = 0
    for p in files:
        total += p.stat().st_size
        if total > MAX_SOURCE_BYTES:
            raise ProfilerError("policy", f"source is over {MAX_SOURCE_BYTES // 2**20} MiB", reason="too_large")
    if len(files) > MAX_SOURCE_FILES:
        raise ProfilerError("policy", f"{len(files)} files; the sandbox allows {MAX_SOURCE_FILES}",
                            reason="too_large")


def check_image_size(size: int) -> None:
    if size > MAX_IMAGE_BYTES:
        raise ProfilerError("build", f"image is {size / 2**30:.1f} GiB; the sandbox allows "
                                     f"{MAX_IMAGE_BYTES // 2**30} GiB", reason="too_large")
