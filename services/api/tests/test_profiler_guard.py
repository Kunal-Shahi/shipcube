"""The profiler sandbox's guard rails (docs/research/profiler-sandbox.md §6–8): worker lanes,
admission limits, the global ceiling and fairness, the queue-age fallback, the spend ledger,
the off switch, and the janitor with its brake. The embedded worker is paused so each test
drives its own workers."""

from datetime import UTC, datetime, timedelta

import pytest

from shipcube_api import db, profiler_guard, store, store_runtime
from shipcube_api.deploy.mock import MockDeployer
from shipcube_api.profiler_janitor import Janitor
from shipcube_api.worker import Worker, lanes_from_env

from .conftest import new_workspace

GIT = {"kind": "git", "url": "https://github.com/example/app"}


class RecordingEstimator:
    """Answers like shipcube_placement: profile.measurements carries mode, static_reason and abuse_signals."""

    def __init__(self, signals=()):
        self.calls = []
        self.hints = []
        self.signals = list(signals)

    def estimate(self, source, hints, *, workspace_id, op_id, static_reason=None):
        self.calls.append(static_reason)
        self.hints.append(hints)
        m = {"mode": "static" if static_reason else "measured", "static_reason": static_reason,
             "abuse_signals": [] if static_reason else self.signals}
        return {"estimate_id": f"est_{op_id[:8]}", "tier": "T1", "profile": {"measurements": m}}


def _reason(op_id):
    return _op(op_id)["result"]["profile"]["measurements"]["static_reason"]


def _clear():
    with db.worker_tx() as conn:
        conn.execute("update operations set status = 'cancelled', locked_by = null, locked_until = null, finished_at = now()"
                     " where status in ('queued', 'running')")
        conn.execute("delete from profiler_spend")
        conn.execute("delete from profiler_flags")
        conn.execute("delete from profiler_strikes")
        conn.execute("delete from profiler_suspensions")


@pytest.fixture(autouse=True)
def guarded(client, monkeypatch):
    store_runtime.stop_embedded_worker()
    monkeypatch.setattr(profiler_guard, "configured", lambda: True)
    monkeypatch.setattr(store, "GIT_ESTIMATES_PAUSED", False)  # these tests use git as a profiled source
    _clear()
    yield
    _clear()
    store_runtime.start_embedded_worker()


def _estimate(client, ws, source=GIT, key=None):
    headers = {**ws["agent"], **({"Idempotency-Key": key} if key else {})}
    return client.post("/v1/estimates", json={"source": source}, headers=headers)


def _queued(client, ws, **kw):
    r = _estimate(client, ws, **kw)
    assert r.status_code == 202, r.text
    return r.json()["id"]


def _op(op_id):
    with db.worker_tx() as conn:
        return conn.execute("select * from operations where id = %s", (op_id,)).fetchone()


def _worker(lane="estimate", estimator=None, **kw):
    return Worker(MockDeployer(speed=0.01), estimator=estimator or RecordingEstimator(), lane=lane, **kw)


def _drain(worker):
    while worker.run_once():
        pass


# --- lanes ------------------------------------------------------------------------------------------


def test_lanes_split_estimates_from_deploys(client):
    ws = new_workspace()
    est = _queued(client, ws)
    r = client.post("/v1/apps/lane-app/deployments", json={"source": GIT}, headers=ws["agent"])
    assert r.status_code == 202, r.text
    dep = r.json()["id"]

    claimed = _worker("deploy").claim()
    assert claimed["id"] == _op(dep)["id"]
    assert _worker("deploy").claim() is None  # the estimate isn't the deploy lane's
    assert _worker("estimate").claim()["id"] == _op(est)["id"]


def test_lanes_from_env(monkeypatch):
    monkeypatch.delenv("SHIPCUBE_WORKER_LANES", raising=False)
    assert lanes_from_env() == ["deploy", "estimate", "estimate", "estimate"]
    monkeypatch.setenv("SHIPCUBE_WORKER_LANES", "all")
    assert lanes_from_env() == ["all"]
    with pytest.raises(ValueError):
        _worker("estimates")


# --- admission (API) ------------------------------------------------------------------------------


def test_a_workspaces_third_profiled_estimate_in_flight_gets_429(client):
    ws = new_workspace()
    _queued(client, ws)
    _queued(client, ws)
    r = _estimate(client, ws)
    assert r.status_code == 429
    assert r.headers["Retry-After"] == "60"
    assert r.json()["error"]["code"] == "rate_limited" and r.json()["error"]["retry_after"] == 60
    # Another workspace isn't affected.
    _queued(client, new_workspace())


def test_replaying_an_admitted_estimate_is_not_rate_limited(client):
    ws = new_workspace()
    first = _queued(client, ws, key="est-1")
    _queued(client, ws, key="est-2")
    again = _estimate(client, ws, key="est-1")
    assert again.status_code == 202 and again.json()["id"] == first


def test_over_the_daily_quota_estimates_run_static(client, monkeypatch):
    monkeypatch.setenv("SHIPCUBE_PROFILER_PER_DAY", "1")
    ws = new_workspace()
    first = _queued(client, ws)
    second = _queued(client, ws)
    assert _op(first)["input"]["profiled"] is True
    assert _op(second)["input"] == {**_op(second)["input"], "profiled": False, "static_reason": "quota"}

    est = RecordingEstimator()
    _drain(_worker(estimator=est))
    assert sorted(est.calls, key=str) == [None, "quota"]
    assert _reason(second) == "quota"


def test_image_sources_and_the_off_switch_are_static(client, monkeypatch):
    ws = new_workspace()
    img = _queued(client, ws, source={"kind": "image", "uri": "us-docker.pkg.dev/cloudrun/container/hello"})
    assert _op(img)["input"]["static_reason"] == "image_source"

    monkeypatch.setenv("SHIPCUBE_PROFILER_ENABLED", "0")
    off = _queued(client, ws)
    assert _op(off)["input"]["static_reason"] == "disabled"
    # Static estimates don't count towards the profiled limit, but have their own: 3 in flight.
    _queued(client, ws)
    r = _estimate(client, ws)
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
    assert r.headers["Retry-After"] == "30"


def test_concurrent_static_estimates_are_limited_per_workspace(client, monkeypatch):
    monkeypatch.setattr(profiler_guard, "configured", lambda: False)  # production today: every estimate is static
    ws, other = new_workspace(), new_workspace()
    ops = [_queued(client, ws) for _ in range(3)]
    assert all(_op(o)["input"]["static_reason"] == "disabled" for o in ops)
    assert _estimate(client, ws).status_code == 429
    _queued(client, other)  # per workspace
    _drain(_worker())  # one finishes: there's room again
    _queued(client, ws)


# --- claim: global ceiling, one per workspace, fairness ----------------------------------------------------


def test_a_fourth_concurrent_profiled_estimate_waits(client):
    ops = [_queued(client, new_workspace()) for _ in range(4)]
    claimed = [_worker().claim() for _ in range(3)]
    assert all(claimed)
    assert _worker().claim() is None  # three are running: the fourth waits
    waiting = next(o for o in ops if o not in {str(c["id"]) for c in claimed})
    assert _op(waiting)["status"] == "queued"

    with db.worker_tx() as conn:  # one finishes
        conn.execute("update operations set status = 'succeeded', locked_by = null, locked_until = null where id = %s",
                     (claimed[0]["id"],))
    assert str(_worker().claim()["id"]) == waiting


def test_static_estimates_are_not_held_by_the_ceiling(client, monkeypatch):
    for _ in range(3):
        _queued(client, new_workspace())
        assert _worker().claim()
    monkeypatch.setenv("SHIPCUBE_PROFILER_ENABLED", "0")
    static = _queued(client, new_workspace())
    assert str(_worker().claim()["id"]) == static


def test_one_profiled_run_per_workspace_and_least_recent_workspace_first(client):
    busy, idle = new_workspace(), new_workspace()
    a1 = _queued(client, busy)
    a2 = _queued(client, busy)
    assert str(_worker().claim()["id"]) == a1
    b1 = _queued(client, idle)
    # busy's second estimate is older, but busy already has a run, so idle goes next...
    assert str(_worker().claim()["id"]) == b1
    # ...and busy's second waits until its first finishes.
    assert _worker().claim() is None
    with db.worker_tx() as conn:
        conn.execute("update operations set status = 'succeeded', locked_by = null, locked_until = null where id = %s", (a1,))
    assert str(_worker().claim()["id"]) == a2


def test_an_estimate_that_waited_too_long_for_a_slot_runs_static(client):
    ws = new_workspace()
    op = _queued(client, ws)
    with db.worker_tx() as conn:
        conn.execute("update operations set created_at = now() - interval '11 minutes' where id = %s", (op,))
    w = _worker()
    w.housekeeping()
    assert _op(op)["input"]["static_reason"] == "busy"
    _drain(w)
    assert _reason(op) == "busy"
    assert w.estimator.calls == ["busy"]


# --- the spend ledger ----------------------------------------------------------------------------


def _spend(workspace_id=None):
    with db.worker_tx() as conn:
        q = "select coalesce(sum(usd), 0) as usd, coalesce(sum(runs), 0) as runs from profiler_spend"
        row = conn.execute(q + (" where workspace_id = %s" if workspace_id else ""),
                           (workspace_id,) if workspace_id else ()).fetchone()
    return float(row["usd"]), int(row["runs"])


def test_a_profiled_run_is_charged_its_worst_case_once(client):
    ws = new_workspace()
    op = _queued(client, ws)
    est = RecordingEstimator()
    _drain(_worker(estimator=est))
    assert est.calls == [None] and _op(op)["status"] == "succeeded"
    assert _spend(ws["workspace_id"]) == (0.11, 1)


def test_over_the_global_cap_estimates_run_static_with_reason_budget(client):
    other = new_workspace()
    with db.worker_tx() as conn:
        conn.execute("insert into profiler_spend (day, workspace_id, runs, usd) values ((now() at time zone 'utc')::date, %s, 46, 5.06)",
                     (other["workspace_id"],))
    ws = new_workspace()
    op = _queued(client, ws)
    est = RecordingEstimator()
    _drain(_worker(estimator=est))
    assert est.calls == ["budget"]
    assert _op(op)["input"]["profiled"] is False
    assert _op(op)["result"]["profile"]["measurements"] == {"mode": "static", "static_reason": "budget", "abuse_signals": []}
    assert _spend(ws["workspace_id"]) == (0.0, 0)  # a static estimate costs nothing


def test_a_workspace_over_its_share_runs_static_while_others_still_profile(client, monkeypatch):
    monkeypatch.setenv("SHIPCUBE_PROFILER_WORKSPACE_DAILY_USD", "0.2")
    ws, other = new_workspace(), new_workspace()
    est = RecordingEstimator()
    for _ in range(3):
        _queued(client, ws)
        _drain(_worker(estimator=est))
    _queued(client, other)
    _drain(_worker(estimator=est))
    assert est.calls == [None, None, "budget", None]
    assert _spend(ws["workspace_id"]) == (0.22, 2)


# --- janitor and brake -----------------------------------------------------------------------------------


class FakeCloud:
    def __init__(self, now, executions=0):
        old, new = now - timedelta(hours=2), now - timedelta(minutes=2)
        self.job_list = [{"name": "jobs/prof-old", "created": old, "running_executions": 0},
                         {"name": "jobs/prof-new", "created": new, "running_executions": executions}]
        self.build_list = [{"id": "b-old", "created": old, "status": "WORKING"},
                           {"id": "b-new", "created": new, "status": "WORKING"}]
        self.object_list = [{"name": "profiler-src/old.tar.gz", "created": old},
                            {"name": "profiler-src/new.tar.gz", "created": new}]
        self.deleted, self.cancelled, self.removed = [], [], []

    def jobs(self):
        return self.job_list

    def delete_job(self, name):
        self.deleted.append(name)

    def builds(self):
        return self.build_list

    def cancel_build(self, build_id):
        self.cancelled.append(build_id)

    def objects(self):
        return self.object_list

    def delete_object(self, name):
        self.removed.append(name)


def test_the_janitor_removes_only_old_leftovers():
    now = datetime.now(UTC)
    cloud = FakeCloud(now)
    done = Janitor(cloud).sweep(now)
    assert cloud.deleted == ["jobs/prof-old"] and cloud.cancelled == ["b-old"]
    assert cloud.removed == ["profiler-src/old.tar.gz"]
    assert done.running_executions == 0
    with db.worker_tx() as conn:
        assert not profiler_guard.brake_on(conn)


def test_too_many_executions_set_the_brake_and_estimates_run_static(client):
    now = datetime.now(UTC)
    w = _worker(janitor=Janitor(FakeCloud(now, executions=6)))
    w.housekeeping()
    with db.worker_tx() as conn:
        assert profiler_guard.brake_on(conn)
    op = _queued(client, new_workspace())
    _drain(w)
    assert w.estimator.calls == ["disabled"]
    assert _reason(op) == "disabled"

    with db.worker_tx() as conn:
        profiler_guard.clear_brake(conn)
        assert not profiler_guard.brake_on(conn)
        assert profiler_guard.status(conn)["brake"] is None


def test_a_janitor_error_does_not_stop_housekeeping(client):
    class Broken(FakeCloud):
        def jobs(self):
            raise RuntimeError("403 from Cloud Run")

    ws = new_workspace()
    op = _queued(client, ws)
    with db.worker_tx() as conn:
        conn.execute("update operations set created_at = now() - interval '11 minutes' where id = %s", (op,))
    _worker(janitor=Janitor(Broken(datetime.now(UTC)))).housekeeping()
    assert _op(op)["input"]["static_reason"] == "busy"


def test_every_estimate_hint_reaches_the_estimator(client):
    ws = new_workspace()
    r = client.post("/v1/estimates", json={"source": GIT, "expected_users": 40, "region": "asia-south1",
                                           "traffic": "always_on"}, headers=ws["agent"])
    assert r.status_code == 202, r.text
    est = RecordingEstimator()
    _drain(_worker(estimator=est))
    assert est.hints == [{"expected_users": 40, "schedule": None, "region": "asia-south1", "traffic": "always_on"}]
    assert client.post("/v1/estimates", json={"source": GIT, "traffic": "sometimes"}, headers=ws["agent"]).status_code == 400


# --- strikes and suspensions ------------------------------------------------------------------------


def _profile_once(client, ws, est):
    op = _queued(client, ws)
    _drain(_worker(estimator=est))
    return op


def _suspension(ws):
    with db.worker_tx() as conn:
        return conn.execute("select * from profiler_suspensions where workspace_id = %s", (ws["workspace_id"],)).fetchone()


def test_three_strikes_in_a_day_suspend_profiling_for_a_day(client):
    ws = new_workspace()
    flood = RecordingEstimator(signals=["egress_flood"])
    _profile_once(client, ws, flood)
    _profile_once(client, ws, RecordingEstimator(signals=["fork_bomb", "disk_flood"]))
    s = _suspension(ws)
    assert s is not None and s["until"] is not None and "3 strikes" in s["reason"]

    est = RecordingEstimator()
    op = _profile_once(client, ws, est)
    assert est.calls == ["suspended"] and _reason(op) == "suspended"
    _profile_once(client, new_workspace(), est)  # other workspaces still profile
    assert est.calls == ["suspended", None]

    with db.worker_tx() as conn:  # a day later it lifts by itself
        conn.execute("update profiler_suspensions set until = now() - interval '1 second' where workspace_id = %s",
                     (ws["workspace_id"],))
    _profile_once(client, ws, est)
    assert est.calls[-1] is None


def test_two_strikes_are_not_a_suspension_and_a_retry_adds_none(client):
    ws = new_workspace()
    op = _profile_once(client, ws, RecordingEstimator(signals=["cpu_pinned", "cpu_pinned"]))
    with db.worker_tx() as conn:
        profiler_guard.record_abuse(conn, _op(op), ["cpu_pinned"])  # the same run again: no new strike
        n = conn.execute("select count(*) as n from profiler_strikes where workspace_id = %s", (ws["workspace_id"],)).fetchone()["n"]
    assert n == 1
    _profile_once(client, ws, RecordingEstimator(signals=["result_forged"]))
    assert _suspension(ws) is None


def test_a_mining_pool_signal_suspends_until_a_person_clears_it(client):
    ws = new_workspace()
    _profile_once(client, ws, RecordingEstimator(signals=["mining_pool"]))
    s = _suspension(ws)
    assert s["until"] is None and s["reason"].startswith("mining_pool")
    # Later strikes never turn it into a timed suspension.
    for _ in range(3):
        with db.worker_tx() as conn:
            op = conn.execute("select * from operations where workspace_id = %s limit 1", (ws["workspace_id"],)).fetchone()
            conn.execute("delete from profiler_strikes where workspace_id = %s", (ws["workspace_id"],))
            profiler_guard.record_abuse(conn, op, ["egress_flood", "fork_bomb", "disk_flood"])
    assert _suspension(ws)["until"] is None
    with db.worker_tx() as conn:
        assert [x["workspace_id"] for x in profiler_guard.status(conn)["suspended"]] == [ws["workspace_id"]]
        assert profiler_guard.unsuspend(conn, ws["workspace_id"])
        assert not profiler_guard.suspended(conn, ws["workspace_id"])
