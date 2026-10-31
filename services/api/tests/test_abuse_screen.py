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
        return box["v"]

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
    assert abuse_screen.screen(store_runtime.deployer(), ws["workspace_id"], INLINE)["verdict"] == "ok"
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
    v = abuse_screen.screen(store_runtime.deployer(), ws["workspace_id"], miner)
    assert v["verdict"] == "blocked" and "crypto_miner" in v["reasons"]
    assert "pool.example.com" not in str(v) and "wallet" not in str(v)  # never the matched text
