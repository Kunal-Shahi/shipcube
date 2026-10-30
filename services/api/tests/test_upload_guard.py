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


def test_a_normal_upload_passes_in_every_compression(tmp_path):
    raw = _tar({"app.py": 1000, "requirements.txt": 20})
    for name, data in [("a.tgz", gzip.compress(raw)), ("a.tbz", bz2.compress(raw)), ("a.txz", lzma.compress(raw)),
                       ("a.tar", raw)]:
        p = tmp_path / name
        p.write_bytes(data)
        assert upload_guard.check_tarball(p) == len(raw)


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


def test_the_budget_is_configurable_and_garbage_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("SHIPCUBE_ESTIMATE_UNPACK_MAX_MIB", "1")
    p = tmp_path / "two.tgz"
    p.write_bytes(gzip.compress(_tar({"big.bin": 2 << 20})))
    with pytest.raises(DeployError, match="1 MiB"):
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
