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
    for flag in ("--init", "--network none", "--read-only", "--user 0:0", "--cap-drop ALL", "--cap-add SETUID",
                 "--cap-add SETGID", "--cap-add CHOWN", "--cap-add KILL",
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
                                                   "SUBNET", "SERVICE_ACCOUNT", "BUILD_SERVICE_ACCOUNT")}
    assert isinstance(runner_from_env({"SHIPCUBE_PROFILER": "gcp", **cfg}), CloudRunJobRunner)


def test_cloud_run_runner_accepts_field_overrides(monkeypatch):
    for k in ("REGION", "BUCKET", "REPOSITORY", "NETWORK", "SUBNET", "BUILD_SERVICE_ACCOUNT"):
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


# --- Phase 1 hardening (docs/research/profiler-sandbox.md §4-8) -------------------------

def test_run_args_pass_nonce_and_agent_caps():
    cfg = DockerSandboxConfig(pids=256, disk_mb=512)
    a = run_args(cfg, "img:tag", "prof-x", Hints(), nonce="n0nce")
    env = dict(a[i + 1].split("=", 1) for i, x in enumerate(a) if x == "--env")
    assert env["SHIPCUBE_RESULT_NONCE"] == "n0nce"
    assert env["SHIPCUBE_AGENT_NPROC"] == "256" and env["SHIPCUBE_AGENT_DISK_MB"] == "512"
    assert DockerSandboxConfig().build_timeout_s == 300


FORGER = r'''
import json, os, sys
agent = os.getppid()
problems = []
try:
    open(f"/proc/{agent}/environ", "rb").read()
    problems.append("read the agent's environment")
except OSError:
    pass
fake = "SHIPCUBE_PROFILE " + json.dumps({"nonce": "guess", "summary": {}, "profile": {"mode": "measured",
        "runner": "docker", "kind": "script", "cpu_mean_cores": 9.9}}) + "\n"
for target in (f"/proc/{agent}/fd/1", "/proc/1/fd/1"):
    try:
        with open(target, "w") as f:
            f.write(fake)
        problems.append("wrote to " + target)
    except OSError:
        pass
print(fake)                    # the app's own stdout goes nowhere
sys.exit(7 if problems else 0)
'''


@needs_docker
def test_app_cannot_read_the_nonce_or_forge_the_result(tmp_path):
    root, s = _app(tmp_path, FORGER)
    p = DockerRunner().run(root, s, Hints())
    assert p.exit_code == 0, "the app reached the agent's /proc entries"
    assert p.cpu_mean_cores < 5 and p.abuse_signals == []


@needs_docker
def test_fork_bomb_is_stopped(tmp_path):
    root, s = _app(tmp_path, "import os, time\nwhile True:\n    try:\n        os.fork() or time.sleep(60)\n"
                             "    except OSError:\n        time.sleep(0.01)\n")
    p = DockerRunner(DockerSandboxConfig(timeout_s=60)).run(root, s, Hints())
    assert p.killed_reason and "processes" in p.killed_reason
    assert "fork_bomb" in p.abuse_signals


@needs_docker
def test_disk_filler_is_stopped(tmp_path):
    root, s = _app(tmp_path, "import os, time\nf = open('/tmp/fill', 'wb')\nfor _ in range(300):\n"
                             "    f.write(b'x' * 2**20); f.flush(); time.sleep(0.01)\ntime.sleep(10)\n")
    p = DockerRunner(DockerSandboxConfig(disk_mb=64, timeout_s=60)).run(root, s, Hints())
    assert p.killed_reason and p.killed_reason.startswith("disk")
    assert "disk_flood" in p.abuse_signals


@needs_docker
def test_sdist_only_requirement_fails_the_build(tmp_path):
    root, s = _app(tmp_path, "import docopt\n")
    (tmp_path / "requirements.txt").write_text("docopt==0.6.2\n")      # published as an sdist only
    with pytest.raises(ProfilerError) as e:
        DockerRunner().build(root)
    assert e.value.reason == "build_failed"


@needs_docker
def test_oversized_image_is_refused_and_removed(tmp_path, monkeypatch):
    import shipcube_profiler.policy as policy
    root, s = _app(tmp_path, "print('hi')\n")
    monkeypatch.setattr(policy, "MAX_IMAGE_BYTES", 1000)
    r = DockerRunner()
    with pytest.raises(ProfilerError) as e:
        r.build(root)
    assert e.value.reason == "too_large"
    left = subprocess.run(["docker", "images", "-q", "--filter", "label=dev.shipcube.role=profiler",
                           "--filter", "dangling=false"], capture_output=True, text=True).stdout
    import hashlib
    from shipcube_profiler.gcp_runner import _package
    tag = hashlib.sha256(_package(root, compress=False)).hexdigest()[:16]
    assert subprocess.run(["docker", "image", "inspect", f"shipcube-profiler-app:{tag}"],
                          capture_output=True).returncode != 0


# --- agent integrity (red team on #57): the upload and its wheels can't become the agent ---

FORGE = ('import os, sys\n'
         'n = os.environ.get("SHIPCUBE_RESULT_NONCE")\n'
         'if n:\n'
         '    sys.stdout.write("SHIPCUBE_PROFILE {\\"nonce\\": \\"%s\\", \\"summary\\": {}, \\"profile\\": '
         '{\\"mode\\": \\"measured\\", \\"runner\\": \\"x\\", \\"kind\\": \\"script\\", \\"cpu_mean_cores\\": 42.0}}\\n" % n)\n'
         '    sys.stdout.flush()\n')


def _genuine(p):
    return p.mode == "measured" and p.cpu_mean_cores < 5 and p.exit_code == 0


@needs_docker
def test_upload_cannot_shadow_the_agent(tmp_path):
    """WORKDIR /app plus `python -m` used to put the upload first on the agent's sys.path."""
    root, s = _app(tmp_path, "print('hi')\n")
    for rel in ("shipcube_profiler/__init__.py", "shipcube_profiler/agent.py", "json.py", "hmac.py", "ctypes.py",
                "psutil.py", "sitecustomize.py", "usercustomize.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(FORGE)
    p = DockerRunner().run(root, summarize(root), Hints())
    assert _genuine(p), p


def _wheel(tmp_path) -> tuple[str, bytes]:
    """evilpkg: a .pth, a .data stdlib overwrite and a psutil shadow, each forging the result."""
    import base64
    import hashlib
    import io
    import zipfile
    files = {
        "evilpkg/__init__.py": "VALUE = 1\n",
        "zz_evil.pth": "import os, sys; exec(" + repr(FORGE) + ")\n",
        "psutil/__init__.py": FORGE + "raise ImportError('shadowed psutil')\n",
        "evilpkg-1.0.data/data/lib/python3.12/json/__init__.py": FORGE,
        "evilpkg-1.0.data/data/lib/python3.12/site-packages/zz_data.pth": "import os, sys; exec(" + repr(FORGE) + ")\n",
        "evilpkg-1.0.dist-info/METADATA": "Metadata-Version: 2.1\nName: evilpkg\nVersion: 1.0\n",
        "evilpkg-1.0.dist-info/WHEEL": "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    record = []
    for name, text in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(text.encode()).digest()).rstrip(b"=").decode()
        record.append(f"{name},sha256={digest},{len(text.encode())}")
    files["evilpkg-1.0.dist-info/RECORD"] = "\n".join(record + ["evilpkg-1.0.dist-info/RECORD,,"]) + "\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return "evilpkg-1.0-py3-none-any.whl", buf.getvalue()


@needs_docker
def test_malicious_wheel_cannot_touch_the_agent(tmp_path):
    from shipcube_profiler.gcp_runner import _package, render_dockerfile
    root, s = _app(tmp_path, "import evilpkg\nprint(evilpkg.VALUE)\n")
    name, data = _wheel(tmp_path)
    # Same layers as production; only the package source differs (a local wheel, no index).
    dockerfile = render_dockerfile(app_install="--no-index --find-links /opt/wheels evilpkg",
                                   extra="COPY wheels/ /opt/wheels/\n")
    r = DockerRunner()
    image = r.build(root, context=_package(root, compress=False, dockerfile=dockerfile,
                                           extra_files={f"wheels/{name}": data}))
    listing = subprocess.run(["docker", "run", "--rm", "--entrypoint", "sh", image, "-c",
                              "ls /opt/appdeps; python -S -c 'import json; print(json.__file__)'"],
                             capture_output=True, text=True).stdout
    assert "evilpkg" in listing and "psutil" in listing          # the wheel really installed
    assert "/usr/local/lib/python3.12/json/__init__.py" in listing   # stdlib untouched
    p = r.run(root, s, Hints(), image=image)
    assert _genuine(p), p                                          # the app ran with its wheel; the agent was untouched


TAMPER = r'''
import ctypes, os, signal, sys
agent = os.getppid()
got_in = []
for pid in (agent, 1):
    for sig in (signal.SIGSTOP, signal.SIGKILL):
        try:
            os.kill(pid, sig)
            got_in.append(f"signal {sig} to {pid}")
        except PermissionError:
            pass
libc = ctypes.CDLL(None, use_errno=True)
if libc.kill(agent, signal.SIGKILL) == 0:                     # bypasses the Python hook
    got_in.append("libc kill")
if libc.ptrace(16, agent, None, None) == 0:                  # PTRACE_ATTACH
    got_in.append("ptrace")
for path in (f"/proc/{agent}/environ", f"/proc/{agent}/mem", f"/proc/{agent}/fd/1"):
    try:
        open(path, "rb").read(1)
        got_in.append("read " + path)
    except OSError:
        pass
print(got_in, file=sys.stderr)
sys.exit(9 if got_in else 0)
'''


@needs_docker
def test_app_cannot_stop_kill_trace_or_read_the_agent(tmp_path):
    root, s = _app(tmp_path, TAMPER)
    p = DockerRunner().run(root, s, Hints())
    assert p.mode == "measured" and p.exit_code == 0, p.notes   # every attempt refused; the agent reported
    assert p.tamper_attempts >= 4 and "agent_tamper" in p.abuse_signals


@needs_docker
def test_agent_is_root_and_app_has_its_own_uid(tmp_path):
    root, s = _app(tmp_path, "import os, sys\n"
                             "a = os.getppid()\n"
                             "st = open(f'/proc/{a}/status').read()\n"
                             "uid = int([l for l in st.splitlines() if l.startswith('Uid:')][0].split()[1])\n"
                             "sys.exit(0 if os.getuid() == 10001 and uid == 0 else 6)\n")
    assert DockerRunner().run(root, s, Hints()).exit_code == 0


ESCALATE = r'''
import os, shutil, subprocess, sys
problems = []
if "NoNewPrivs:\t1" not in open("/proc/self/status").read():
    problems.append("no_new_privs not set")
for cmd in (["su", "root", "-c", "id -u"], ["newgrp", "root"], ["sg", "root", "-c", "id -u"]):
    if shutil.which(cmd[0]):
        r = subprocess.run(cmd, input=b"\n", capture_output=True, timeout=10)
        if r.returncode == 0 or b"\n0\n" in b"\n" + r.stdout:
            problems.append("escalated with " + cmd[0])
for d in ("/bin", "/sbin", "/usr/bin", "/usr/sbin", "/usr/lib", "/usr/libexec"):
    for dirpath, _, names in os.walk(d):
        for n in names:
            p = os.path.join(dirpath, n)
            try:
                if os.lstat(p).st_mode & 0o6000:
                    problems.append("setuid/setgid: " + p)
            except OSError:
                pass
if os.getuid() != 10001 or os.geteuid() != 10001:
    problems.append("uid changed")
print(problems, file=sys.stderr)
sys.exit(8 if problems else 0)
'''


@needs_docker
def test_app_cannot_gain_privileges(tmp_path):
    root, s = _app(tmp_path, ESCALATE)
    p = DockerRunner().run(root, s, Hints())
    assert p.exit_code == 0, p.notes


@needs_docker
def test_image_has_no_setuid_or_setgid_files(tmp_path):
    root, s = _app(tmp_path, "print('hi')\n")
    image = DockerRunner().build(root)
    r = subprocess.run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "find", image,
                        "/", "-xdev", "-perm", "/6000", "-type", "f"], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == ""
    # The base image does ship them (so the strip step matters).
    base = subprocess.run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "find", "python:3.12-slim",
                           "/", "-xdev", "-perm", "/6000", "-type", "f"], capture_output=True, text=True)
    assert "/su" in base.stdout


# --- phase 2 offline build (#22), emulating the Cloud Build steps locally -------------------

def _offline_context(tmp_path, root):
    import io
    import tarfile
    from shipcube_profiler.gcp_runner import _package, offline_dockerfile
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    data = _package(root, compress=False, dockerfile=offline_dockerfile(),
                    extra_files={"wheels/.keep": b"", "agent-wheels/.keep": b""})
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(ctx, filter="data")
    return ctx


@needs_docker
def test_offline_build_installs_only_fetched_wheels_and_profiles(tmp_path):
    from shipcube_profiler.gcp_runner import FETCH_SCRIPT, PYPI_INDEX, REQUIREMENTS_FILE
    (tmp_path / "app").mkdir()
    root, s = _app(tmp_path / "app", "import tomli_w\nprint(tomli_w.dumps({'a': 1}))\n")
    (root / "requirements.txt").write_text("tomli-w==1.0.0\n")
    ctx = _offline_context(tmp_path, root)
    script = FETCH_SCRIPT.format(index=PYPI_INDEX, requirements=REQUIREMENTS_FILE)
    # docker cp, not a bind mount: works whatever the daemon can see of this filesystem.
    cid = subprocess.run(["docker", "create", "-w", "/workspace", "python:3.12-slim", "bash", "-c", script],
                         capture_output=True, text=True, check=True).stdout.strip()
    try:
        subprocess.run(["docker", "cp", f"{ctx}/.", f"{cid}:/workspace"], check=True, capture_output=True)
        fetch = subprocess.run(["docker", "start", "-a", cid], capture_output=True, text=True, timeout=300)
        if fetch.returncode != 0 and ("onnection" in fetch.stderr or "resolution" in fetch.stderr):
            pytest.skip(f"no network to PyPI for the fetch step: {fetch.stderr[-200:]}")
        assert fetch.returncode == 0, fetch.stderr[-2000:]
        for d in ("wheels", "agent-wheels"):
            subprocess.run(["docker", "cp", f"{cid}:/workspace/{d}/.", str(ctx / d)], check=True, capture_output=True)
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True)
    assert any(p.name.startswith("tomli_w") for p in (ctx / "wheels").iterdir())
    tag = "shipcube-profiler-offline-test:latest"
    build = subprocess.run(["docker", "build", "--network=none", "--label", "dev.shipcube.role=profiler", "-q",
                            "-t", tag, str(ctx)], capture_output=True, text=True, timeout=600)
    assert build.returncode == 0, build.stderr[-2000:]
    p = DockerRunner().run(root, s, Hints(), image=tag)
    assert p.mode == "measured" and p.exit_code == 0, p.notes


@needs_docker
def test_offline_build_has_no_network_without_fetched_wheels(tmp_path):
    (tmp_path / "app").mkdir()
    root, s = _app(tmp_path / "app", "import tomli_w\n")
    (root / "requirements.txt").write_text("tomli-w==1.0.0\n")
    ctx = _offline_context(tmp_path, root)                   # the fetch step never ran: no wheels
    build = subprocess.run(["docker", "build", "--network=none", "-q", "-t", "shipcube-profiler-offline-neg:latest",
                            str(ctx)], capture_output=True, text=True, timeout=600)
    assert build.returncode != 0                             # nothing to install from, and no index to reach


# The live shipcube-worker service's profiler settings (Oct 31, 2026): SHIPCUBE_PROFILER unset.
LIVE_WORKER = {
    "K_SERVICE": "shipcube-worker",
    "SHIPCUBE_PROFILER_BUCKET": "shipcube-ks-ml",
    "SHIPCUBE_PROFILER_BUILD_SERVICE_ACCOUNT": "profiler-build@shipcube-apps-ks.iam.gserviceaccount.com",
    "SHIPCUBE_PROFILER_NETWORK": "profiler-no-egress",
    "SHIPCUBE_PROFILER_PROJECT": "shipcube-apps-ks",
    "SHIPCUBE_PROFILER_REGION": "asia-south1",
    "SHIPCUBE_PROFILER_REPOSITORY": "profiler",
    "SHIPCUBE_PROFILER_SERVICE_ACCOUNT": "profiler-sandbox@shipcube-apps-ks.iam.gserviceaccount.com",
    "SHIPCUBE_PROFILER_SUBNET": "profiler",
}


def test_the_live_worker_still_gets_the_cloud_run_sandbox():
    assert isinstance(runner_from_env(LIVE_WORKER), CloudRunJobRunner)
    assert isinstance(runner_from_env({**LIVE_WORKER, "SHIPCUBE_PROFILER": "gcp"}), CloudRunJobRunner)


@pytest.mark.parametrize("extra", [{"SHIPCUBE_PROFILER": "local"}, {"SHIPCUBE_PROFILER": "inproc", "SHIPCUBE_DEV_MODE": "1"},
                                   {"SHIPCUBE_DEV_MODE": "1"}, {"SHIPCUBE_PROFILER": "gcp", "SHIPCUBE_DEV_MODE": "1"}])
@pytest.mark.parametrize("cloud_run", ["K_SERVICE", "K_REVISION", "CLOUD_RUN_JOB"])
def test_cloud_run_refuses_every_runner_but_the_sandbox(extra, cloud_run):
    env = {**{k: v for k, v in LIVE_WORKER.items() if k != "K_SERVICE"}, cloud_run: "x", **extra}
    with pytest.raises(ProfilerError, match="on Cloud Run") as e:
        runner_from_env(env)
    assert e.value.stage == "config"
