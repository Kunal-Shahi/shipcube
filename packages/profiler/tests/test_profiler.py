import math
import subprocess
import sys
from pathlib import Path

import pytest

from shipcube_profiler import (Hints, Limits, LocalRunner, NotRunnable, SourceError,
                               StaticRunner, materialize, runs_per_day, summarize, to_features)

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


def inline(app_dir: Path) -> dict:
    return {"kind": "inline", "files": [{"path": str(p.relative_to(app_dir)), "content": p.read_text()}
                                        for p in sorted(app_dir.rglob("*")) if p.is_file()]}


# --- schedule -------------------------------------------------------------------------

@pytest.mark.parametrize("expr,runs", [("0 2 * * *", 1), ("*/15 * * * *", 96), ("@hourly", 24),
                                       ("0 9 * * 1-5", 5 / 7), ("0 0,12 * * *", 2)])
def test_runs_per_day(expr, runs):
    assert math.isclose(runs_per_day(expr), runs, rel_tol=1e-6)


def test_bad_cron():
    with pytest.raises(ValueError):
        runs_per_day("every day")


# --- sources --------------------------------------------------------------------------

def test_inline_rejects_path_traversal(tmp_path):
    with pytest.raises(SourceError):
        materialize({"kind": "inline", "files": [{"path": "../evil.py", "content": "x"}]}, tmp_path)


def test_inline_size_limit(tmp_path):
    with pytest.raises(SourceError):
        materialize({"kind": "inline", "files": [{"path": "a.py", "content": "x" * 300_000}]}, tmp_path)


def test_image_is_not_runnable_locally(tmp_path):
    with pytest.raises(NotRunnable):
        materialize({"kind": "image", "uri": "us-docker.pkg.dev/p/r/app@sha256:abc"}, tmp_path)


def test_git_source(tmp_path):
    repo = tmp_path / "repo"
    (repo / "svc").mkdir(parents=True)
    (repo / "svc" / "main.py").write_text("print('hi')\n")
    git = ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "x"], check=True)
    root = materialize({"kind": "git", "url": f"file://{repo}", "subdir": "svc"}, tmp_path / "out",
                       allow_file_urls=True)
    assert (root / "main.py").exists()


def test_upload_needs_fetcher(tmp_path):
    with pytest.raises(SourceError):
        materialize({"kind": "upload", "upload_id": "u1"}, tmp_path)


# --- detection ------------------------------------------------------------------------

def test_detects_flask_dashboard():
    s = summarize(SAMPLES / "dashboard")
    assert (s.kind, s.framework, s.entrypoint) == ("web", "flask", "app.py")
    assert "/api/summary" in s.routes


def test_detects_batch_script():
    s = summarize(SAMPLES / "nightly_batch")
    assert (s.kind, s.entrypoint) == ("script", "job.py")
    assert "csv" in s.writes_outputs


# --- local runner on the two sample apps --------------------------------------------

@pytest.fixture(scope="module")
def dashboard_profile(tmp_path_factory):
    pytest.importorskip("flask")
    root = materialize(inline(SAMPLES / "dashboard"), tmp_path_factory.mktemp("dash"))
    s = summarize(root)
    return LocalRunner().run(root, s, Hints(expected_users=25)), s


@pytest.fixture(scope="module")
def batch_profile(tmp_path_factory):
    root = materialize(inline(SAMPLES / "nightly_batch"), tmp_path_factory.mktemp("batch"))
    s = summarize(root)
    return LocalRunner().run(root, s, Hints(schedule="0 2 * * *")), s


def test_dashboard_is_measured_under_load(dashboard_profile):
    p, _ = dashboard_profile
    assert p.mode == "measured" and p.kind == "web"
    assert p.requests.sent == 120 and p.requests.ok == 120
    assert 0 < p.mem_peak_gb < 1
    assert p.cpu_seconds > 0 and p.samples > 5
    assert p.net_calls == 0


def test_batch_runs_to_completion_and_egress_is_blocked(batch_profile):
    p, _ = batch_profile
    assert p.exit_code == 0 and not p.timed_out
    assert p.cpu_mean_cores > 0.3 and p.wall_s > 0.2
    assert p.net_blocked >= 1 and "hooks.example.com" in p.remote_hosts
    assert p.processes_max == 1


def test_features_from_profiles(dashboard_profile, batch_profile):
    fd, notes = to_features(dashboard_profile[0], Hints(expected_users=25))
    assert fd["invocations_per_hour"] > 100 and 0.5 < fd["idle_fraction"] < 1
    fb, _ = to_features(batch_profile[0], Hints(schedule="0 2 * * *"))
    assert fb["invocations_per_hour"] == pytest.approx(1 / 24, rel=1e-4)
    assert fb["idle_fraction"] > 0.99
    assert fb["duration_s"] == pytest.approx(batch_profile[0].active_s, rel=1e-3)


def _script(tmp_path, code: str):
    (tmp_path / "main.py").write_text(code)
    return tmp_path, summarize(tmp_path)


def test_parent_secrets_do_not_leak(tmp_path, monkeypatch):
    monkeypatch.setenv("META_API_KEY", "sentinel-not-a-real-key")
    root, s = _script(tmp_path, "import os, sys\nsys.exit(3 if 'META_API_KEY' in os.environ else 0)\n")
    assert LocalRunner().run(root, s, Hints()).exit_code == 0


def test_timeout_kills_runaway(tmp_path):
    root, s = _script(tmp_path, "import time\nwhile True: time.sleep(0.05)\n")
    p = LocalRunner(Limits(timeout_s=1.5)).run(root, s, Hints())
    assert p.timed_out and p.wall_s < 6


def test_memory_limit_kills_hog(tmp_path):
    root, s = _script(tmp_path, "import time\nx = bytearray(400 * 2**20)\nx[::4096] = b'1' * len(x[::4096])\ntime.sleep(5)\n")
    p = LocalRunner(Limits(timeout_s=10, memory_mb=150)).run(root, s, Hints())
    assert p.killed_reason and "memory" in p.killed_reason


def test_static_runner_and_hints():
    s = summarize(SAMPLES / "nightly_batch")
    p = StaticRunner().run(None, s, Hints(schedule="*/30 * * * *"))
    assert p.mode == "static"
    f, _ = to_features(p, Hints(schedule="*/30 * * * *"))
    assert f["invocations_per_hour"] == 2


def test_python_is_this_interpreter_by_default():
    assert LocalRunner().python == sys.executable


def test_web_cpu_cv_is_left_unknown(dashboard_profile):
    f, _ = to_features(dashboard_profile[0], Hints(expected_users=25))
    assert math.isnan(f["cpu_cv"])


def test_api_only_apps_get_always_on_traffic(tmp_path):
    (tmp_path / "app.py").write_text("from flask import Flask\napp = Flask(__name__)\n"
                                     "@app.route('/api/items')\ndef items(): return '[]'\n")
    s = summarize(tmp_path)
    assert s.api_only
    p = StaticRunner().run(tmp_path, s, Hints(expected_users=1000))
    f, notes = to_features(p, Hints(expected_users=1000), api_only=True)
    assert f["idle_fraction"] < 0.01 and any("always on" in n for n in notes)


def test_allocation_refused_is_reported_as_memory_limit():
    # Linux path: RLIMIT_AS makes the allocation fail before RSS can grow.
    from shipcube_profiler.runners import _allocation_refused
    assert "memory" in _allocation_refused(1, "Traceback ...\nMemoryError\n", Limits(memory_mb=150))
    assert _allocation_refused(1, "ValueError: nope", Limits()) is None
    assert _allocation_refused(0, "MemoryError", Limits()) is None
