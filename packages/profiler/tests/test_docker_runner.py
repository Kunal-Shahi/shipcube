"""The local Docker sandbox. Tests that need a Docker daemon run wherever one is
available, including GitHub's ubuntu runners (CI)."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from shipcube_profiler import (CloudRunJobRunner, DockerRunner, DockerSandboxConfig, Hints, LocalRunner,
                               ProfilerError, runner_from_env, summarize)
from shipcube_profiler.docker_runner import run_args

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


def _docker_up() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "version"], capture_output=True, timeout=20).returncode == 0


needs_docker = pytest.mark.skipif(not _docker_up(), reason="needs a Docker daemon")


def _app(tmp_path: Path, code: str) -> tuple[Path, object]:
    (tmp_path / "main.py").write_text(code)
    return tmp_path, summarize(tmp_path)


# --- no Docker needed ----------------------------------------------------------------

def test_run_args_carry_the_isolation_flags():
    cfg = DockerSandboxConfig(cpus="1", memory_mb=2048, pids=256, timeout_s=600, tmpfs_mb=512)
    a = run_args(cfg, "img:tag", "prof-x", Hints(expected_users=5, sample_input="/host/secret"))
    s = " ".join(a)
    for flag in ("--network none", "--read-only", "--user 10001:10001", "--cap-drop ALL",
                 "--security-opt no-new-privileges", "--cpus 1", "--memory 2048m", "--memory-swap 2048m",
                 "--pids-limit 256", "--tmpfs /tmp:rw,nosuid,nodev,size=512m"):
        assert flag in s, flag
    assert "/host/secret" not in s                     # no host paths reach the sandbox
    assert not any(x in ("-v", "--volume", "--mount", "--privileged") for x in a)
    env = [a[i + 1] for i, x in enumerate(a) if x == "--env"]
    assert all(e.split("=", 1)[0].startswith("SHIPCUBE_") for e in env)   # nothing from the host env
    assert a[-1] == "img:tag"


def test_runner_switch(monkeypatch):
    assert isinstance(runner_from_env({"SHIPCUBE_PROFILER": "local"}), DockerRunner)
    assert isinstance(runner_from_env({"SHIPCUBE_PROFILER": "inproc", "SHIPCUBE_DEV_MODE": "1"}), LocalRunner)
    assert isinstance(runner_from_env({"SHIPCUBE_DEV_MODE": "1"}), LocalRunner)
    with pytest.raises(ProfilerError):                 # running user code in-process needs DEV_MODE
        runner_from_env({"SHIPCUBE_PROFILER": "inproc"})
    with pytest.raises(ProfilerError):
        runner_from_env({"SHIPCUBE_PROFILER": "cloud"})
    with pytest.raises(ProfilerError) as e:            # gcp needs its config
        runner_from_env({"SHIPCUBE_PROFILER": "gcp"})
    assert e.value.stage == "config"
    cfg = {"SHIPCUBE_PROFILER_" + k: "x" for k in ("PROJECT", "REGION", "BUCKET", "REPOSITORY", "NETWORK",
                                                   "SUBNET", "SERVICE_ACCOUNT")}
    assert isinstance(runner_from_env({"SHIPCUBE_PROFILER": "gcp", **cfg}), CloudRunJobRunner)


def test_cloud_run_runner_accepts_field_overrides(monkeypatch):
    for k in ("REGION", "BUCKET", "REPOSITORY", "NETWORK", "SUBNET"):
        monkeypatch.setenv(f"SHIPCUBE_PROFILER_{k}", "x")
    r = CloudRunJobRunner(project="shipcube-apps-ks", service_account="sandbox@x")
    assert r.config.project == "shipcube-apps-ks" and r.config.service_account == "sandbox@x"


def test_bad_limits_are_a_config_error():
    with pytest.raises(ProfilerError):
        DockerSandboxConfig.from_env({"SHIPCUBE_PROFILER_MEMORY_MB": "lots"})


def test_missing_docker_cli_is_a_config_error():
    r = DockerRunner(DockerSandboxConfig(docker="/nonexistent/docker"))
    assert not r.available()
    with pytest.raises(ProfilerError) as e:
        r.run(SAMPLES / "nightly_batch", summarize(SAMPLES / "nightly_batch"), Hints())
    assert e.value.stage == "config"


# --- real containers -----------------------------------------------------------------

NO_NETWORK = r'''
import errno, os, sys
import _socket  # the C module: bypasses the profiler's Python-level socket hook,
                # so only the container's network namespace can stop this
results = []
for host, port in [("1.1.1.1", 53), ("8.8.8.8", 443), ("172.17.0.1", 80)]:
    s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    s.settimeout(3)
    try:
        s.connect((host, port))
        results.append("connected")
    except OSError as e:
        results.append(e.errno)
    finally:
        s.close()
try:
    _socket.getaddrinfo("example.com", 443)
    dns = "resolved"
except OSError:
    dns = "failed"
ifaces = sorted(l.split(":")[0].strip() for l in open("/proc/net/dev").read().splitlines()[2:])
ok = "connected" not in results and dns == "failed" and ifaces == ["lo"]
sys.exit(0 if ok else 3)
'''

LOCKED_DOWN = r'''
import os, sys
problems = []
if os.getuid() == 0 or os.getuid() != 10001:
    problems.append("uid")
for path in ("/app/x", "/usr/local/lib/x", "/etc/x"):
    try:
        open(path, "w").write("x")
        problems.append("wrote " + path)
    except OSError:
        pass
open("/tmp/ok", "w").write("x")          # the only writable place
if open("/proc/self/status").read().count("CapEff:\t0000000000000000") != 1:
    problems.append("capabilities")
sys.exit(4 if problems else 0)
'''


@needs_docker
def test_sandbox_cannot_reach_the_network(tmp_path):
    root, s = _app(tmp_path, NO_NETWORK)
    p = DockerRunner().run(root, s, Hints())
    assert p.runner == "docker" and p.mode == "measured"
    assert p.exit_code == 0, "the app reached the network, resolved DNS, or saw a non-loopback interface"


@needs_docker
def test_sandbox_is_non_root_read_only_and_capability_free(tmp_path):
    root, s = _app(tmp_path, LOCKED_DOWN)
    assert DockerRunner().run(root, s, Hints()).exit_code == 0


@needs_docker
def test_sandbox_profiles_the_batch_sample():
    p = DockerRunner().run(SAMPLES / "nightly_batch", summarize(SAMPLES / "nightly_batch"),
                           Hints(schedule="0 2 * * *"))
    assert p.runner == "docker" and p.exit_code == 0 and not p.timed_out
    assert p.cpu_seconds > 0 and p.mem_peak_gb > 0 and p.wall_s > 0
    assert p.io_source == "psutil"                       # Linux counters inside the container
    assert p.net_blocked >= 1 and "hooks.example.com" in p.remote_hosts


@needs_docker
def test_sandbox_profiles_the_flask_dashboard():
    p = DockerRunner().run(SAMPLES / "dashboard", summarize(SAMPLES / "dashboard"), Hints(expected_users=25))
    assert p.kind == "web" and p.requests.ok == p.requests.sent == 120
    assert p.net_calls == 0


@needs_docker
def test_sandbox_memory_limit(tmp_path):
    root, s = _app(tmp_path, "import time\nx = bytearray(600 * 2**20)\nx[::4096] = b'1' * len(x[::4096])\n"
                             "time.sleep(5)\n")
    p = DockerRunner(DockerSandboxConfig(memory_mb=256)).run(root, s, Hints())
    assert p.killed_reason and "memory" in p.killed_reason


@needs_docker
def test_sandbox_timeout_stops_runaway(tmp_path):
    root, s = _app(tmp_path, "import time\nwhile True: time.sleep(0.1)\n")
    p = DockerRunner(DockerSandboxConfig(timeout_s=40)).run(root, s, Hints())   # agent stops it at 10 s
    assert p.timed_out and p.wall_s < 20


@needs_docker
def test_requirements_install_at_build_time_and_image_is_reused(tmp_path):
    root, s = _app(tmp_path, "import tomli_w\nprint(tomli_w.dumps({'a': 1}))\n")
    (tmp_path / "requirements.txt").write_text("tomli-w==1.0.0\n")
    r = DockerRunner()
    first = r.build(root)
    assert r.build(root) == first                         # same code -> same cached image
    assert r.run(root, s, Hints()).exit_code == 0         # installed at build, imported offline


@needs_docker
def test_no_containers_left_behind(tmp_path):
    root, s = _app(tmp_path, "print('hi')\n")
    DockerRunner().run(root, s, Hints())
    left = subprocess.run(["docker", "ps", "-aq", "--filter", "label=dev.shipcube.role=profiler"],
                          capture_output=True, text=True).stdout.split()
    assert left == []


@needs_docker
def test_no_host_environment_leaks(tmp_path, monkeypatch):
    monkeypatch.setenv("META_API_KEY", "sentinel-not-a-real-key")
    root, s = _app(tmp_path, "import os, sys\nsys.exit(5 if any('sentinel' in v for v in os.environ.values()) else 0)\n")
    assert DockerRunner().run(root, s, Hints()).exit_code == 0
    assert os.environ["META_API_KEY"] == "sentinel-not-a-real-key"
