"""Phase 1 sandbox policy: requirements, sizes, static reasons, abuse signals, caps
(docs/research/profiler-sandbox.md §4-9). No Docker or GCP needed."""

import io
import sys
import tarfile
from pathlib import Path

import pytest

from shipcube_profiler import (STATIC_REASONS, Hints, Limits, LocalRunner, Profile, ProfilerError, SourceError,
                               StaticRunner, abuse_signals, materialize, summarize)
from shipcube_profiler.agent import RESULT_PREFIX, parse_result
from shipcube_profiler.gcp_runner import _package
from shipcube_profiler.policy import (MAX_IMAGE_BYTES, MAX_REQUIREMENTS, check_image_size, check_source_size,
                                      sanitize_requirements)

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


# --- requirements ------------------------------------------------------------------------

@pytest.mark.parametrize("line", [
    "flask", "Flask==3.0.3", "pandas>=2,<3", "uvicorn[standard]~=0.30", "numpy (>=1.26)",
    "tomli; python_version < '3.11'", "requests==2.32.3 --hash=sha256:" + "a" * 64,
    "zope.interface>=6", "ruamel.yaml.clib==0.2.8",
])
def test_plain_pypi_requirements_are_allowed(line):
    assert sanitize_requirements(line + "  # pinned\n") == [line]


@pytest.mark.parametrize("line", [
    "-e git+https://github.com/x/y#egg=y", "git+https://github.com/x/y", "--index-url https://evil.example/simple",
    "--extra-index-url https://evil.example/simple", "-i https://evil.example", "--trusted-host evil.example",
    "-f https://evil.example/wheels", "-r other.txt", "-c constraints.txt", "./local_pkg", "/abs/path.whl",
    "pkg @ https://evil.example/pkg.tar.gz", "https://evil.example/pkg.whl", "pkg==1.0; os.system('x')",
    "pkg==1.0 --global-option=build_ext", "file:///tmp/pkg",
])
def test_urls_options_and_paths_are_refused(line):
    with pytest.raises(ProfilerError) as e:
        sanitize_requirements(f"flask\n{line}\n")
    assert e.value.reason == "requirements_policy" and e.value.stage == "policy"


def test_continuation_lines_and_comments():
    text = "# deps\nrequests==2.32.3 \\\n    --hash=sha256:" + "b" * 64 + "\n\nflask  # web\n"
    assert sanitize_requirements(text) == ["requests==2.32.3 --hash=sha256:" + "b" * 64, "flask"]


def test_too_many_requirements_is_too_large():
    with pytest.raises(ProfilerError) as e:
        sanitize_requirements("\n".join(f"pkg{i}" for i in range(MAX_REQUIREMENTS + 1)))
    assert e.value.reason == "too_large"


def test_package_ships_only_the_sanitized_list(tmp_path):
    (tmp_path / "main.py").write_text("print(1)\n")
    (tmp_path / "requirements.txt").write_text("# c\nFlask==3.0.3\n\n")
    with tarfile.open(fileobj=io.BytesIO(_package(tmp_path)), mode="r:gz") as tar:
        assert tar.extractfile("shipcube-requirements.txt").read() == b"Flask==3.0.3\n"
    (tmp_path / "requirements.txt").write_text("--extra-index-url https://evil.example\nflask\n")
    with pytest.raises(ProfilerError) as e:
        _package(tmp_path)
    assert e.value.reason == "requirements_policy"


# --- sizes ---------------------------------------------------------------------------------

def test_source_and_image_size_caps(tmp_path, monkeypatch):
    import shipcube_profiler.policy as policy
    files = []
    for i in range(5):
        f = tmp_path / f"f{i}.bin"
        f.write_bytes(b"x" * 1000)
        files.append(f)
    check_source_size(files)
    monkeypatch.setattr(policy, "MAX_SOURCE_BYTES", 4000)
    with pytest.raises(ProfilerError) as e:
        policy.check_source_size(files)
    assert e.value.reason == "too_large"
    monkeypatch.setattr(policy, "MAX_SOURCE_BYTES", 10**9)
    monkeypatch.setattr(policy, "MAX_SOURCE_FILES", 4)
    with pytest.raises(ProfilerError):
        policy.check_source_size(files)
    check_image_size(MAX_IMAGE_BYTES)
    with pytest.raises(ProfilerError) as e:
        check_image_size(MAX_IMAGE_BYTES + 1)
    assert e.value.reason == "too_large"


def test_upload_that_unpacks_too_big_is_refused_before_extracting(tmp_path, monkeypatch):
    import shipcube_profiler.sources as sources
    tgz = tmp_path / "u.tgz"
    with tarfile.open(tgz, "w:gz") as tar:
        data = b"\0" * 200_000                     # compresses to almost nothing
        ti = tarfile.TarInfo("big.bin")
        ti.size = len(data)
        tar.addfile(ti, io.BytesIO(data))
    monkeypatch.setattr(sources, "UNPACKED_MAX_BYTES", 100_000)
    with pytest.raises(SourceError):
        materialize({"kind": "upload", "upload_id": "u"}, tmp_path / "out", fetch_upload=lambda _: tgz)
    assert not (tmp_path / "out" / "big.bin").exists()


# --- static reasons ---------------------------------------------------------------------------

def test_static_runner_carries_a_known_reason():
    s = summarize(SAMPLES / "nightly_batch")
    p = StaticRunner(reason="quota").run(None, s, Hints())
    assert p.mode == "static" and p.static_reason == "quota" and STATIC_REASONS["quota"] in p.notes
    assert StaticRunner().run(None, s, Hints()).static_reason is None
    with pytest.raises(ValueError):
        StaticRunner(reason="because")
    assert Profile.from_dict(p.to_dict()).static_reason == "quota"


def test_every_error_reason_is_a_static_reason():
    with pytest.raises(AssertionError):
        ProfilerError("x", "y", reason="nope")
    assert ProfilerError("logs", "y").reason == "profiler_error"


# --- result integrity -------------------------------------------------------------------------

def _line(nonce, **profile):
    import json
    return RESULT_PREFIX + json.dumps({"nonce": nonce, "summary": {}, "profile": profile})


def test_parse_result_accepts_only_the_nonce_line():
    assert parse_result(["noise", _line("n1", mode="measured")], "n1")["profile"]["mode"] == "measured"
    assert parse_result(["noise"], "n1") is None
    for lines in ([_line("n2")], [_line("n1"), _line("n2")], [_line("n1"), _line("n1")],
                  [_line("n1"), RESULT_PREFIX + "{not json"], [_line("")]):
        with pytest.raises(ProfilerError) as e:
            parse_result(lines, "n1")
        assert e.value.reason == "result_rejected"
    with pytest.raises(ProfilerError):
        parse_result([_line("")], "")              # an empty nonce never matches


# --- abuse signals -------------------------------------------------------------------------

def test_abuse_signals():
    clean = Profile(mode="measured", runner="gcp", kind="script", net_blocked=1, remote_hosts=["hooks.example.com"])
    assert abuse_signals(clean) == []
    assert abuse_signals(Profile(mode="measured", runner="gcp", kind="script", net_blocked=51)) == ["egress_flood"]
    assert "mining_pool" in abuse_signals(Profile(mode="measured", runner="gcp", kind="script", remote_ports=[3333]))
    assert "mining_pool" in abuse_signals(Profile(mode="measured", runner="gcp", kind="script",
                                                  remote_hosts=["pool.supportxmr.com"]))
    assert abuse_signals(Profile(mode="measured", runner="gcp", kind="script",
                                 killed_reason="more than 256 processes (fork refused by RLIMIT_NPROC)")) == ["fork_bomb"]
    assert abuse_signals(Profile(mode="measured", runner="gcp", kind="script",
                                 killed_reason="disk: more than 512 MB written")) == ["disk_flood"]
    assert abuse_signals(Profile(mode="measured", runner="gcp", kind="script",
                                 killed_reason="memory over 1843 MB")) == []          # memory-heavy is not abuse
    assert abuse_signals(Profile(mode="measured", runner="gcp", kind="script", active_s=120,
                                 cpu_mean_cores=0.99, io_write_mb=0)) == ["cpu_pinned"]
    assert abuse_signals(Profile(mode="static", runner="static", kind="script",
                                 static_reason="result_rejected")) == ["result_forged"]


# --- caps in the agent's runner (in-process; the Docker tests cover the container) ----------

def _script(tmp_path, code):
    (tmp_path / "main.py").write_text(code)
    return tmp_path, summarize(tmp_path)


def test_stderr_is_drained_and_only_the_tail_kept(tmp_path):
    # 4 MiB to stderr: with a plain pipe read at exit the app would block on a full pipe.
    root, s = _script(tmp_path, "import sys\nfor _ in range(4096): sys.stderr.write('e' * 1023 + '\\n')\n"
                                "sys.stderr.write('LAST LINE\\n'); sys.exit(2)\n")
    p = LocalRunner(Limits(timeout_s=20, stderr_tail_bytes=4096)).run(root, s, Hints())
    assert not p.timed_out and p.exit_code == 2
    assert any("LAST LINE" in n for n in p.notes)


def test_disk_watchdog_kills_a_writer(tmp_path):
    root, s = _script(tmp_path, "import os, time\nf = open(os.path.join(os.environ['TMPDIR'], 'fill'), 'wb')\n"
                                "for _ in range(400):\n    f.write(b'x' * 2**20); f.flush(); os.fsync(f.fileno())\n"
                                "    time.sleep(0.01)\ntime.sleep(5)\n")
    p = LocalRunner(Limits(timeout_s=30, max_disk_mb=32, max_file_mb=1024)).run(root, s, Hints())
    assert p.killed_reason and p.killed_reason.startswith("disk")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="RLIMIT_NPROC is per-uid; checked on Linux")
def test_nproc_limit_refuses_a_fork_bomb_on_linux(tmp_path):
    root, s = _script(tmp_path, "import os, time\npids = []\nfor _ in range(10_000):\n"
                                "    pid = os.fork()\n    if pid == 0:\n        time.sleep(5); os._exit(0)\n"
                                "    pids.append(pid)\n")
    import os
    import psutil
    mine = len([p for p in psutil.process_iter(["uids"]) if p.info["uids"] and p.info["uids"].real == os.getuid()])
    p = LocalRunner(Limits(timeout_s=20, max_nproc=mine + 64, max_processes=10_000)).run(root, s, Hints())
    assert p.killed_reason and "processes" in p.killed_reason
