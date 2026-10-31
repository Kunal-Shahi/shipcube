"""The abuse pre-screen enforced at deploy and estimate time (abuse_screen.py)."""

import types

import pytest

from shipcube_api import abuse_screen, db, store_runtime
from shipcube_api.deploy.base import DeployError

from .conftest import new_workspace, wait_op

GIT = {"kind": "git", "url": "https://github.com/example/app"}
INLINE = {"kind": "inline", "files": [{"path": "app.py", "content": "print('hi')\n"}]}


@pytest.fixture
def ws(client):
    return new_workspace()


@pytest.fixture
def verdict(monkeypatch):
    """Deploys are screened (the mock deployer normally skips it) and get the verdict a test sets."""
    box: dict = {"v": {"verdict": "ok", "reasons": [], "signals": [], "hits": []}, "calls": 0}

    def fake_screen(deployer, workspace_id, source):
        box["calls"] += 1
        return box["v"], {}

    monkeypatch.setattr(store_runtime.deployer(), "screens_sources", True, raising=False)
    monkeypatch.setattr(abuse_screen, "screen", fake_screen)
    return box


def _strikes(ws) -> list[str]:
    with db.worker_tx() as conn:
        return [r["signal"] for r in conn.execute("select signal from profiler_strikes where workspace_id = %s order by signal",
                                                  (ws["workspace_id"],)).fetchall()]


def _suspension(ws):
    with db.worker_tx() as conn:
        return conn.execute("select until, reason from profiler_suspensions where workspace_id = %s",
                            (ws["workspace_id"],)).fetchone()


def _deploy(client, ws, app, source=GIT):
    r = client.post(f"/v1/apps/{app}/deployments", json={"source": source}, headers=ws["agent"])
    assert r.status_code == 202, r.text
    return wait_op(client, ws["agent"], r.json()["id"])


def test_a_blocked_source_is_refused_before_any_build(client, ws, verdict):
    verdict["v"] = {"verdict": "blocked", "reasons": ["crypto_miner"], "signals": ["prescreen_mining_pool_reference"],
                    "hits": [{"code": "crypto_miner", "confidence": "high", "rule": "xmrig", "file": "run.sh"}]}
    op = _deploy(client, ws, "miner")
    assert op["status"] == "failed" and op["error"]["code"] == "abuse_refused"
    assert "crypto_miner" in op["error"]["message"] and "xmrig" not in op["error"]["message"]
    assert not (op["result"] or {}).get("build_id")  # refused in validate, before the build
    assert _strikes(ws) == ["prescreen_crypto_miner", "prescreen_mining_pool_reference"]
    s = _suspension(ws)
    assert s is not None and s["until"] is None  # a miner waits for a person, like the sandbox's mining_pool


def test_a_flagged_source_deploys_and_counts_a_strike(client, ws, verdict):
    verdict["v"] = {"verdict": "flagged", "reasons": [], "signals": ["prescreen_network_scanner"], "hits": []}
    assert _deploy(client, ws, "scanner-ish")["status"] == "succeeded"
    assert _strikes(ws) == ["prescreen_network_scanner"] and _suspension(ws) is None


def test_a_clean_source_deploys_without_strikes(client, ws, verdict):
    assert _deploy(client, ws, "clean")["status"] == "succeeded" and verdict["calls"] == 1
    assert _strikes(ws) == []


def test_an_estimate_the_prescreen_blocks_is_a_typed_refusal(client, ws, monkeypatch):
    class AbuseRefused(Exception):  # stands in for shipcube_profiler.AbuseRefused
        def __init__(self):
            super().__init__("blocked")
            self.codes = ["attack_tool"]
            self.verdict = types.SimpleNamespace(to_dict=lambda: {"verdict": "blocked", "reasons": ["attack_tool"],
                                                                   "signals": [], "hits": []})

    def refuse(*a, **kw):
        raise AbuseRefused()

    from shipcube_api import estimator as est_mod
    for cls in (est_mod.PlacementEstimator, est_mod.DevEstimator, est_mod.UnavailableEstimator):
        monkeypatch.setattr(cls, "estimate", refuse)
    r = client.post("/v1/estimates", json={"source": INLINE}, headers=ws["agent"])
    assert r.status_code == 202, r.text
    op = wait_op(client, ws["agent"], r.json()["id"])
    assert op["status"] == "failed" and op["error"]["code"] == "abuse_refused", op
    assert "attack_tool" in op["error"]["message"]
    assert _strikes(ws) == ["prescreen_attack_tool"]
    assert _suspension(ws) is None  # one strike; only a miner suspends at once


def test_screen_materializes_the_source_and_skips_images(client, ws, monkeypatch):
    profiler = pytest.importorskip("shipcube_profiler")  # the API's optional estimator extra
    # Installed but without the pre-screen means a stale pin: fail, so screening can't silently switch off.
    assert hasattr(profiler, "prescreen") and hasattr(profiler, "AbuseRefused"), \
        "the installed shipcube_profiler predates prescreen: bump its pin in services/api/pyproject.toml"
    seen: dict = {}

    def fake_prescreen(app_dir):
        seen["files"] = sorted(p.name for p in app_dir.rglob("*") if p.is_file())
        return types.SimpleNamespace(to_dict=lambda: {"verdict": "ok", "reasons": [], "signals": [], "hits": []})

    monkeypatch.setattr(abuse_screen, "_prescreen", lambda: fake_prescreen)
    assert abuse_screen.screen(store_runtime.deployer(), ws["workspace_id"], INLINE)[0]["verdict"] == "ok"
    assert seen["files"] == ["app.py"]
    assert abuse_screen.screen(store_runtime.deployer(), ws["workspace_id"], {"kind": "image", "uri": "x"}) is None


def test_without_the_prescreen_installed_deploys_are_not_screened(client, monkeypatch):
    monkeypatch.setattr(abuse_screen, "_prescreen", lambda: None)
    assert abuse_screen.screen(store_runtime.deployer(), "ws", INLINE) is None


def test_refusal_message_never_carries_matched_text():
    e = abuse_screen.refusal(["phishing_kit"])
    assert isinstance(e, DeployError) and e.code == "abuse_refused" and "phishing_kit" in str(e)


def test_the_real_prescreen_blocks_a_miner(client, ws):
    """With the estimator's pre-screen installed (#95): an obvious miner is blocked end to end."""
    profiler = pytest.importorskip("shipcube_profiler")
    if not hasattr(profiler, "prescreen"):
        pytest.skip("the installed shipcube_profiler predates prescreen")
    miner = {"kind": "inline", "files": [{"path": "run.sh", "content": "./xmrig -o stratum+tcp://pool.example.com:3333 -u wallet\n"}]}
    v, _ = abuse_screen.screen(store_runtime.deployer(), ws["workspace_id"], miner)
    assert v["verdict"] == "blocked" and "crypto_miner" in v["reasons"]
    assert "pool.example.com" not in str(v) and "wallet" not in str(v)  # never the matched text


# --- what was screened is what gets built (security review of #97) -------------------------------------

import subprocess  # noqa: E402
from dataclasses import replace  # noqa: E402

from shipcube_api import git_source  # noqa: E402
from shipcube_api.deploy import gcp, local  # noqa: E402
from shipcube_api.deploy.base import AppRef, Target  # noqa: E402
from shipcube_api.settings import Settings  # noqa: E402

APP = AppRef(name="tracker", workspace_id="3c1d5e2a-7b4f-4e8a-9c6d-2f1e0a9b8c7d",
             target=Target("shipcube", "shipcube", "shipcube-apps-ks"), region="asia-south1")


def test_on_cloud_run_a_missing_prescreen_refuses_the_deploy(monkeypatch):
    monkeypatch.setattr(abuse_screen, "_prescreen", lambda: None)
    monkeypatch.setenv("K_SERVICE", "shipcube-worker")
    with pytest.raises(DeployError) as e:
        abuse_screen.screen(store_runtime.deployer(), "ws", INLINE)
    assert e.value.code == "screen_unavailable"
    monkeypatch.delenv("K_SERVICE")
    assert abuse_screen.screen(store_runtime.deployer(), "ws", INLINE) is None  # local dev: warn and continue


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def _repo(path):
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    (path / "app.py").write_text("print('v1')\n")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "v1")
    return path


def test_screen_records_the_git_commit_it_read(monkeypatch, tmp_path):
    profiler = pytest.importorskip("shipcube_profiler")
    upstream = _repo(tmp_path / "upstream")
    screened_sha = _git(upstream, "rev-parse", "HEAD")

    def materialize(source, dest, fetch_upload=None):  # what the profiler does, from a local repo
        subprocess.run(["git", "clone", "-q", str(upstream), str(dest / "repo")], check=True)
        return dest / "repo"

    monkeypatch.setattr(profiler, "materialize", materialize)
    monkeypatch.setattr(abuse_screen, "_prescreen",
                        lambda: lambda d: types.SimpleNamespace(to_dict=lambda: {"verdict": "ok", "reasons": [], "signals": []}))
    verdict, pins = abuse_screen.screen(store_runtime.deployer(), APP.workspace_id, GIT)
    assert verdict["verdict"] == "ok" and pins == {"commit": screened_sha}


@pytest.fixture
def gcp_deployer(monkeypatch):
    monkeypatch.setattr(gcp.google.auth, "default", lambda scopes: (object(), "p"))
    return gcp.GcpDeployer(Settings())


def test_gcp_builds_the_screened_commit_even_if_the_ref_moved(gcp_deployer):
    sha = "a" * 40
    staged = gcp_deployer.stage_source(APP, {**GIT, "ref": "main", "commit": sha}, "op-1")
    assert staged["gitSource"]["revision"] == sha  # not "main", which may point elsewhere by build time


def test_local_build_refuses_a_ref_that_moved_after_the_screen(tmp_path, monkeypatch):
    upstream = _repo(tmp_path / "upstream")
    screened_sha = _git(upstream, "rev-parse", "HEAD")
    (upstream / "app.py").write_text("print('swapped')\n")
    _git(upstream, "commit", "-q", "-am", "pushed after the screen")
    real_clone = git_source.clone
    monkeypatch.setattr(git_source, "clone", lambda url, ref, target: real_clone(url, ref, target, allow_file=True))
    d = local.LocalDeployer(replace(Settings(), local_state_dir=str(tmp_path / "state")))
    git = {"url": f"file://{upstream}", "ref": "main"}
    work = tmp_path / "w1"
    work.mkdir()
    with pytest.raises(DeployError, match="changed after it was screened"):
        d._fetch_source(APP, {"git": {**git, "commit": screened_sha}}, work)
    work2 = tmp_path / "w2"
    work2.mkdir()
    built = d._fetch_source(APP, {"git": {**git, "commit": _git(upstream, "rev-parse", "HEAD")}}, work2)
    assert (built / "app.py").read_text() == "print('swapped')\n"  # the screened commit builds


class _Blob:
    def __init__(self, store, name, generation=None):
        self.store, self.name, self.want = store, name, generation
        self.size = 10

    @property
    def generation(self):
        return self.store.get(self.name)

    def exists(self):
        return self.name in self.store and (self.want is None or self.store[self.name] == self.want)

    def reload(self):
        pass

    def download_to_filename(self, path):
        open(path, "wb").write(b"")


class _Bucket:
    def __init__(self, store):
        self.store, self.asked = store, []

    def blob(self, name, generation=None):
        self.asked.append((name, generation))
        return _Blob(self.store, name, generation)

    def get_blob(self, name):
        return _Blob(self.store, name) if name in self.store else None


def test_an_upload_overwritten_after_the_screen_is_refused(gcp_deployer, monkeypatch, tmp_path):
    from shipcube_api.deploy import isolation

    name = isolation.upload_object(gcp_deployer.s, APP.workspace_id, "upl_0123456789ab")
    store = {name: 111}
    bucket = _Bucket(store)
    monkeypatch.setattr(gcp_deployer, "_bucket", lambda: bucket)
    src = {"kind": "upload", "upload_id": "upl_0123456789ab"}

    screened = gcp_deployer.upload_generation(APP.workspace_id, "upl_0123456789ab")
    gcp_deployer.download_upload(APP.workspace_id, "upl_0123456789ab", tmp_path / "x.tgz", generation=screened)
    assert bucket.asked[-1] == (name, 111)  # the screen read exactly that generation
    staged = gcp_deployer.stage_source(APP, {**src, "generation": screened}, "op-1")
    assert staged["storageSource"]["generation"] == "111"  # and Cloud Build fetches exactly it

    store[name] = 222  # the signed URL was used again after the screen
    with pytest.raises(DeployError, match="changed after it was screened"):
        gcp_deployer.stage_source(APP, {**src, "generation": screened}, "op-2")
    with pytest.raises(DeployError, match="not found"):  # and the screened version can't be read any more
        gcp_deployer.download_upload(APP.workspace_id, "upl_0123456789ab", tmp_path / "y.tgz", generation=screened)


def test_the_worker_stages_the_pinned_source(client, ws, monkeypatch):
    seen = {}
    d = store_runtime.deployer()
    real_stage = d.stage_source

    def stage(app, source, op_id):
        seen["source"] = source
        return real_stage(app, {k: v for k, v in source.items() if k != "commit"}, op_id)

    monkeypatch.setattr(d, "screens_sources", True, raising=False)
    monkeypatch.setattr(d, "stage_source", stage)
    monkeypatch.setattr(abuse_screen, "screen", lambda deployer, workspace_id, source:
                        ({"verdict": "ok", "reasons": [], "signals": []}, {"commit": "b" * 40}))
    assert _deploy(client, ws, "pinned")["status"] == "succeeded"
    assert seen["source"]["url"] == GIT["url"] and seen["source"]["commit"] == "b" * 40


# --- strikes count once per source, not per operation (Kunal, #97) ---------------------------------------

import io  # noqa: E402
import tarfile  # noqa: E402


def _flagged_inline(text: str) -> dict:
    return {"kind": "inline", "files": [{"path": "scan.py", "content": text}]}


def test_one_flagged_source_estimated_and_deployed_twice_is_one_strike(client, ws, verdict, monkeypatch):
    from shipcube_api import estimator as est_mod

    src = _flagged_inline("import socket  # a port scanner, say\n")
    verdict["v"] = {"verdict": "flagged", "reasons": [], "signals": ["prescreen_network_scanner"], "hits": []}
    real = est_mod.DevEstimator.estimate

    def flagged_estimate(self, *a, **kw):
        res = real(self, *a, **kw)
        res.setdefault("profile", {}).setdefault("measurements", {})["abuse_signals"] = ["prescreen_network_scanner"]
        return res

    monkeypatch.setattr(est_mod.DevEstimator, "estimate", flagged_estimate)
    r = client.post("/v1/estimates", json={"source": src}, headers=ws["agent"])
    assert wait_op(client, ws["agent"], r.json()["id"])["status"] == "succeeded"
    assert _deploy(client, ws, "same-src", src)["status"] == "succeeded"
    assert _deploy(client, ws, "same-src", src)["status"] == "succeeded"
    assert _strikes(ws) == ["prescreen_network_scanner"]  # one source, one signal: one strike
    assert _suspension(ws) is None


def test_three_distinct_flagged_sources_still_suspend(client, ws, verdict):
    verdict["v"] = {"verdict": "flagged", "reasons": [], "signals": ["prescreen_network_scanner"], "hits": []}
    for i in range(3):
        assert _deploy(client, ws, f"scan-{i}", _flagged_inline(f"# scanner variant {i}\n"))["status"] == "succeeded"
    assert len(_strikes(ws)) == 3
    s = _suspension(ws)
    assert s is not None and s["until"] is not None  # 3 strikes in 24 h: profiling paused for a day


def _tgz(files: dict[str, str], mtime: int) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, text in files.items():
            info = tarfile.TarInfo(name)
            info.size, info.mtime = len(text), mtime
            tar.addfile(info, io.BytesIO(text.encode()))
    return buf.getvalue()


def test_the_source_key_is_the_content_not_the_packing(tmp_path):
    files = {"app.py": "print('hi')\n", "requirements.txt": "flask\n"}
    a, b = tmp_path / "a.tgz", tmp_path / "b.tgz"
    a.write_bytes(_tgz(files, 1))
    b.write_bytes(_tgz(dict(reversed(files.items())), 999))  # repacked later, other order
    assert a.read_bytes() != b.read_bytes()
    assert abuse_screen.tarball_digest(a) == abuse_screen.tarball_digest(b)
    inline = {"kind": "inline", "files": [{"path": k, "content": v} for k, v in files.items()]}
    assert abuse_screen.source_key(None, "ws", inline) == "files:" + abuse_screen.tarball_digest(a)
    other = {"kind": "inline", "files": [{"path": "app.py", "content": "print('bye')\n"}]}
    assert abuse_screen.source_key(None, "ws", other) != abuse_screen.source_key(None, "ws", inline)
    assert abuse_screen.source_key(None, "ws", GIT, {"commit": "c" * 40}) == "git:" + "c" * 40
