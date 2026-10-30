"""Estimate sources can't exhaust the worker's memory or CPU (red team on PR #55): a streaming
decompressed-byte budget before tarfile sees an upload, and a cap on sources on disk at once."""

import bz2
import gzip
import io
import lzma
import os
import tarfile
import threading
import time

import pytest

from shipcube_api import upload_guard
from shipcube_api.deploy.base import DeployError


def _tar(files: dict[str, int]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, size in files.items():
            info = tarfile.TarInfo(name)
            info.size = size
            tar.addfile(info, io.BytesIO(b"\0" * size))
    return buf.getvalue()


def test_a_normal_upload_passes_in_every_accepted_compression(tmp_path):
    raw = _tar({"app.py": 1000, "requirements.txt": 20})
    for name, data in [("a.tgz", gzip.compress(raw)), ("a.txz", lzma.compress(raw)),
                       ("a.tar.lzma", lzma.compress(raw, format=lzma.FORMAT_ALONE)), ("a.tar", raw)]:
        p = tmp_path / name
        p.write_bytes(data)
        got = upload_guard.check_tarball(p)
        assert got["members"] == 2 and got["declared_bytes"] == 1020 and got["decompressed_bytes"] >= 1020, name
        with tarfile.open(p, "r:*") as tar:  # what the profiler opens is exactly what was measured
            assert len(tar.getmembers()) == 2


def test_bzip2_is_refused(tmp_path):
    p = tmp_path / "a.tbz"
    p.write_bytes(bz2.compress(_tar({"app.py": 10})))
    with pytest.raises(DeployError, match="bzip2"):
        upload_guard.check_tarball(p)


def _refused(tmp_path, data, match, name="x.tgz"):
    p = tmp_path / name
    p.write_bytes(data)
    t0 = time.monotonic()
    with pytest.raises(DeployError, match=match) as e:
        upload_guard.check_tarball(p)
    assert e.value.code == "invalid_argument" and time.monotonic() - t0 < 10


def _bomb(member: tarfile.TarInfo, fill: int) -> bytes:
    raw = io.BytesIO()
    raw.write(member.tobuf(format=tarfile.GNU_FORMAT))
    chunk = b"\0" * (16 << 20)
    for _ in range(fill // len(chunk)):
        raw.write(chunk)
    return raw.getvalue()


def test_a_legacy_lzma_bomb_is_refused(tmp_path):
    # The red team's repro: an .lzma ("alone") file of a few hundred KiB holding a 2 GiB member, which
    # tarfile.open("r:*") decodes. Built from a 64 MiB body so the test stays fast; the header declares 2 GiB.
    info = tarfile.TarInfo("zeros")
    info.size = 2 << 30
    data = lzma.compress(_bomb(info, 64 << 20), format=lzma.FORMAT_ALONE)
    assert len(data) < 1 << 20
    _refused(tmp_path, data, "more than 256 MiB", "bomb.tar.lzma")


def test_a_pax_header_declaring_gigabytes_is_never_read(tmp_path):
    # tarfile reads a pax header's whole declared size into memory; the budget refuses the read first.
    info = tarfile.TarInfo("././@PaxHeader")
    info.type = tarfile.XHDTYPE
    info.size = 1 << 30
    _refused(tmp_path, gzip.compress(_bomb(info, 32 << 20)), "more than 256 MiB")


def test_a_gzip_bomb_is_refused_without_inflating_it_all(tmp_path):
    # One member declared and filled to 1 GiB of zeros: about 1 MiB compressed. tarfile.getmembers()
    # would inflate the whole stream; the guard stops at the 256 MiB budget.
    p = tmp_path / "bomb.tgz"
    with gzip.open(p, "wb", compresslevel=9) as gz:
        info = tarfile.TarInfo("zeros")
        info.size = 2**30
        gz.write(info.tobuf())
        chunk = b"\0" * (16 << 20)
        for _ in range(64):
            gz.write(chunk)
    assert p.stat().st_size < 4 << 20
    t0 = time.monotonic()
    with pytest.raises(DeployError) as e:
        upload_guard.check_tarball(p)
    assert e.value.code == "invalid_argument" and "256 MiB" in str(e.value)
    assert time.monotonic() - t0 < 10


def test_too_many_members_are_refused(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for i in range(upload_guard.MAX_MEMBERS + 1):
            tar.addfile(tarfile.TarInfo(f"f{i}"), io.BytesIO(b""))
    _refused(tmp_path, buf.getvalue(), "more than 20000 entries")


def test_declared_sizes_are_capped_even_when_the_bytes_are_small(tmp_path, monkeypatch):
    monkeypatch.setenv("SHIPCUBE_ESTIMATE_UNPACK_MAX_MIB", "1")
    _refused(tmp_path, gzip.compress(_tar({"a": 600 << 10, "b": 600 << 10})), "files add up to more than 1 MiB")


def _member_tar(info: tarfile.TarInfo, fmt=tarfile.PAX_FORMAT) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=fmt) as tar:
        tar.addfile(info, io.BytesIO(b"\0" * info.size) if info.isfile() else None)
    return gzip.compress(buf.getvalue())


@pytest.mark.parametrize("make, match", [
    (lambda: tarfile.TarInfo("a/" * 520 + "x"), "longer than 1024"),
    (lambda: tarfile.TarInfo("d" * 300), "longer than 1024"),
    (lambda: tarfile.TarInfo("/etc/passwd"), "outside the archive"),
    (lambda: tarfile.TarInfo("app/../../escape"), "outside the archive"),
])
def test_long_and_escaping_paths_are_refused(tmp_path, make, match):
    _refused(tmp_path, _member_tar(make()), match)


def _typed(name, kind, linkname=""):
    info = tarfile.TarInfo(name)
    info.type, info.linkname = kind, linkname
    return info


@pytest.mark.parametrize("info, match", [
    (_typed("disk", tarfile.BLKTYPE), "not a regular file"),
    (_typed("tty", tarfile.CHRTYPE), "not a regular file"),
    (_typed("pipe", tarfile.FIFOTYPE), "not a regular file"),
    (_typed("app/link", tarfile.SYMTYPE, "../../etc/passwd"), "links outside"),
    (_typed("app/abs", tarfile.SYMTYPE, "/etc/passwd"), "links outside"),
    (_typed("hard", tarfile.LNKTYPE, "../secret"), "links outside"),
])
def test_devices_fifos_and_escaping_links_are_refused(tmp_path, info, match):
    _refused(tmp_path, _member_tar(info), match)


def test_links_inside_the_archive_are_fine(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        f = tarfile.TarInfo("app/main.py")
        f.size = 3
        tar.addfile(f, io.BytesIO(b"x=1"))
        tar.addfile(_typed("app/alias.py", tarfile.SYMTYPE, "main.py"))
        tar.addfile(_typed("app/copy.py", tarfile.LNKTYPE, "app/main.py"))
    p = tmp_path / "ok.tgz"
    p.write_bytes(buf.getvalue())
    assert upload_guard.check_tarball(p)["members"] == 3


def test_sparse_members_are_refused(tmp_path):
    # A GNU sparse member ("S"), and a PAX 1.0 sparse member (GNU.sparse.* headers).
    gnu = tarfile.TarInfo("holes.bin")
    gnu.type = tarfile.GNUTYPE_SPARSE
    raw = gnu.tobuf(format=tarfile.GNU_FORMAT) + b"\0" * 1024
    _refused(tmp_path, gzip.compress(raw), "sparse", "gnu.tgz")

    pax = tarfile.TarInfo("holes2.bin")
    pax.size = 512
    pax.pax_headers = {"GNU.sparse.major": "1", "GNU.sparse.minor": "0", "GNU.sparse.name": "holes2.bin",
                       "GNU.sparse.realsize": str(1 << 40)}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        tar.addfile(pax, io.BytesIO(b"1\n0\n512\n".ljust(512, b"\0")))
    _refused(tmp_path, gzip.compress(buf.getvalue()), "sparse", "pax.tgz")


def test_the_budget_is_configurable_and_garbage_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("SHIPCUBE_ESTIMATE_UNPACK_MAX_MIB", "1")
    p = tmp_path / "two.tgz"
    p.write_bytes(gzip.compress(_tar({"big.bin": 2 << 20})))
    with pytest.raises(DeployError, match="more than 1 MiB"):
        upload_guard.check_tarball(p)
    bad = tmp_path / "bad.tgz"
    bad.write_bytes(b"\x1f\x8b" + b"not really gzip")
    with pytest.raises(DeployError, match="not a readable archive"):
        upload_guard.check_tarball(bad)


def test_at_most_two_sources_are_on_disk_at_once(monkeypatch):
    monkeypatch.setattr(upload_guard, "_slots", None)
    inside, peak, lock = 0, 0, threading.Lock()

    def run(kind):
        nonlocal inside, peak
        with upload_guard.source_slot({"kind": kind}):
            with lock:
                inside += 1
                peak = max(peak, inside)
            time.sleep(0.05)
            with lock:
                inside -= 1

    threads = [threading.Thread(target=run, args=("upload" if i % 2 else "git",)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak == 2
    with upload_guard.source_slot({"kind": "inline"}):  # inline sources are tiny and never wait
        pass


def test_the_estimator_checks_uploads_and_always_removes_the_download(tmp_path, monkeypatch):
    import sys
    import types

    from shipcube_api.estimator import PlacementEstimator

    seen = {}

    def fake_estimate(source, hints, *, runner, fetch_upload):
        path = fetch_upload(source["upload_id"])
        seen["dir"] = path.parent
        return types.SimpleNamespace(tier="T1", profile={"measurements": {"mode": "static"}})

    class StaticRunner:
        def __init__(self, reason=None):
            self.reason = reason

    monkeypatch.setitem(sys.modules, "shipcube_placement", types.SimpleNamespace(estimate=fake_estimate))
    monkeypatch.setitem(sys.modules, "shipcube_profiler", types.SimpleNamespace(StaticRunner=StaticRunner,
                                                                                 CloudRunJobRunner=None))
    bomb = gzip.compress(_tar({"zeros": 3 << 20}))

    class Deployer:
        def download_upload(self, workspace_id, upload_id, dest):
            dest.write_bytes(bomb if upload_id == "bomb" else gzip.compress(_tar({"app.py": 10})))
            return dest

    import tempfile

    from shipcube_api import estimator as estimator_mod

    made = []
    real_mkdtemp = tempfile.mkdtemp
    monkeypatch.setattr(estimator_mod.tempfile, "mkdtemp", lambda **kw: made.append(real_mkdtemp(**kw)) or made[-1])
    est = PlacementEstimator(Deployer())
    out = est.estimate({"kind": "upload", "upload_id": "ok"}, {}, workspace_id="w", op_id="op-1", static_reason="busy")
    assert out["estimate_id"] == "est_op1" and not seen["dir"].exists()

    monkeypatch.setenv("SHIPCUBE_ESTIMATE_UNPACK_MAX_MIB", "1")
    seen.clear()
    with pytest.raises(DeployError) as e:
        est.estimate({"kind": "upload", "upload_id": "bomb"}, {}, workspace_id="w", op_id="op-2", static_reason="busy")
    assert e.value.code == "invalid_argument"  # a typed failure: the worker doesn't retry it
    assert "dir" not in seen and len(made) == 2
    assert not any(os.path.exists(d) for d in made)  # both downloads removed
