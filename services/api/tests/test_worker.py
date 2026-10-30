"""Durability of the operations queue: crash recovery, retries with backoff, typed failures,
cancellation and housekeeping. The embedded worker is paused so each test drives its own."""

import pytest

from shipcube_api import db, store_runtime
from shipcube_api.deploy.base import DeployError
from shipcube_api.deploy.mock import MockDeployer
from shipcube_api.worker import Worker

from .conftest import new_workspace

GIT = {"kind": "git", "url": "https://github.com/example/app"}


class Crash(BaseException):
    """Escapes the worker's error handling, like the process dying mid-step."""


class ScriptedDeployer(MockDeployer):
    def __init__(self, deploy_errors=(), build_error=None):
        super().__init__(speed=0.01)
        self.builds_started = 0
        self.deploy_errors = list(deploy_errors)
        self.build_error = build_error

    def build(self, app, staged, op_id, should_stop, on_build, existing_build_id=None):
        if self.build_error:
            raise self.build_error
        if not existing_build_id:
            self.builds_started += 1
        return super().build(app, staged, op_id, should_stop, on_build, existing_build_id)

    def deploy(self, app, image, caps, env):
        if self.deploy_errors:
            raise self.deploy_errors.pop(0)
        return super().deploy(app, image, caps, env)


@pytest.fixture(autouse=True)
def paused_embedded_worker(client):
    store_runtime.stop_embedded_worker()
    yield
    store_runtime.start_embedded_worker()


def _op(op_id):
    with db.tx() as conn:
        return conn.execute("select * from operations where id = %s", (op_id,)).fetchone()


def _deploy(client, ws, name):
    r = client.post(f"/v1/apps/{name}/deployments", json={"source": GIT}, headers=ws["agent"])
    assert r.status_code == 202, r.text
    return r.json()["id"]


def _drain(worker):
    while worker.run_once():
        pass


def test_a_crashed_worker_is_resumed_from_its_last_step_without_rebuilding(client):
    ws = new_workspace()
    op_id = _deploy(client, ws, "crash-app")
    crashing = ScriptedDeployer(deploy_errors=[Crash()])
    with pytest.raises(Crash):
        Worker(crashing, worker_id="w1").run_once()
    row = _op(op_id)
    assert row["status"] == "running" and row["locked_by"] == "w1" and row["step_index"] == 3
    assert row["step_state"]["build_id"] and crashing.builds_started == 1

    with db.tx() as conn:  # the lease runs out because nobody heartbeats any more
        conn.execute("update operations set locked_until = now() - interval '1 second' where id = %s", (op_id,))
    survivor = ScriptedDeployer()
    _drain(Worker(survivor, worker_id="w2"))
    row = _op(op_id)
    assert row["status"] == "succeeded" and row["attempts"] == 2 and row["locked_by"] is None
    assert survivor.builds_started == 0  # the build was re-attached by id, not started again


def test_transient_errors_back_off_and_retry(client):
    ws = new_workspace()
    op_id = _deploy(client, ws, "flaky-app")
    w = Worker(ScriptedDeployer(deploy_errors=[DeployError("unavailable", "503 from Cloud Run")]), worker_id="w3")
    assert w.run_once()
    row = _op(op_id)
    assert row["status"] == "queued" and row["error_code"] == "unavailable"
    assert row["next_attempt_at"] > row["updated_at"]
    assert [s["status"] for s in row["steps"]][3] == "retrying"
    assert not w.run_once()  # not due yet
    with db.tx() as conn:
        conn.execute("update operations set next_attempt_at = now() where id = %s", (op_id,))
    _drain(w)
    row = _op(op_id)
    assert row["status"] == "succeeded" and row["attempts"] == 2


def test_permanent_errors_fail_at_once_with_a_typed_code(client):
    ws = new_workspace()
    op_id = _deploy(client, ws, "broken-app")
    _drain(Worker(ScriptedDeployer(build_error=DeployError("build_failed", "pack: no Procfile")), worker_id="w4"))
    row = _op(op_id)
    assert row["status"] == "failed" and row["error_code"] == "build_failed" and row["attempts"] == 1
    with db.tx() as conn:
        dep = conn.execute("select status, error from deployments where id = %s", (row["input"]["deployment_id"],)).fetchone()
        app = conn.execute("select status from apps where id = %s", (row["app_id"],)).fetchone()
    assert dep["status"] == "failed" and "Procfile" in dep["error"] and app["status"] == "failed"
    op = client.get(f"/v1/operations/{op_id}", headers=ws["agent"]).json()
    assert op["error"]["code"] == "build_failed" and op["steps"][1]["status"] == "failed"


def test_retries_stop_at_max_attempts(client):
    ws = new_workspace()
    op_id = _deploy(client, ws, "down-app")
    with db.tx() as conn:
        conn.execute("update operations set max_attempts = 1 where id = %s", (op_id,))
    _drain(Worker(ScriptedDeployer(deploy_errors=[DeployError("unavailable", "still down")]), worker_id="w5"))
    assert _op(op_id)["status"] == "failed"


def test_cancel_stops_before_the_next_step(client):
    ws = new_workspace()
    op_id = _deploy(client, ws, "cancel-app")
    assert client.post(f"/v1/operations/{op_id}/cancel", headers=ws["agent"]).status_code == 200
    _drain(Worker(ScriptedDeployer(), worker_id="w6"))
    row = _op(op_id)
    assert row["status"] == "cancelled" and row["error_code"] == "cancelled"


def test_housekeeping_expires_approvals_and_purges_after_the_restore_window(client):
    ws = new_workspace()
    w = Worker(ScriptedDeployer(), worker_id="w7")
    _deploy(client, ws, "old-app")
    _drain(w)
    op = client.delete("/v1/apps/old-app", headers=ws["agent"]).json()
    with db.tx() as conn:
        conn.execute("update operations set approval_expires_at = now() - interval '1 minute' where id = %s", (op["id"],))
    w.housekeeping()
    assert _op(op["id"])["status"] == "cancelled" and _op(op["id"])["error_code"] == "approval_expired"

    op = client.delete("/v1/apps/old-app", headers=ws["agent"]).json()
    client.post(f"/v1/operations/{op['id']}/approve", json={}, headers=ws["human"])
    _drain(w)
    with db.tx() as conn:
        conn.execute("update apps set purge_after = now() - interval '1 minute' where slug = 'old-app' and workspace_id = %s",
                     (ws["workspace_id"],))
    w.housekeeping()
    _drain(w)
    with db.tx() as conn:
        app = conn.execute("select purged_at from apps where slug = 'old-app' and workspace_id = %s", (ws["workspace_id"],)).fetchone()
    assert app["purged_at"] is not None


def test_exposure_check_logs_public_hosted_services_as_critical(client, caplog):
    class Exposed(MockDeployer):
        def public_services(self, regions):
            assert regions  # always at least the default region
            return ["projects/shipcube-apps-ks/locations/asia-south1/services/leaky"], 3

    with caplog.at_level("INFO", logger="shipcube.worker"):
        Worker(Exposed(), worker_id="w-exposure").check_exposure()
    hits = [r for r in caplog.records if r.levelname == "CRITICAL" and "public" in r.getMessage()]
    assert hits and hits[0].service.endswith("/services/leaky")
    # The alert matches this exact message (infra/terraform/modules/monitoring/main.tf): keep them equal.
    import pathlib
    import re
    tf = (pathlib.Path(__file__).resolve().parents[3] / "infra/terraform/modules/monitoring/main.tf").read_text()
    assert hits[0].getMessage() == re.search(r'public_service_message = "([^"]+)"', tf)[1]
    # And a heartbeat every run, so a check that stops running alerts too (absence alert, same file).
    beats = [r for r in caplog.records if r.levelname == "INFO" and r.getMessage() == "exposure check ran"]
    assert beats and beats[0].services_checked == 3 and beats[0].public == 1
    assert beats[0].getMessage() == re.search(r'exposure_heartbeat_message = "([^"]+)"', tf)[1]


def test_a_failed_exposure_check_sends_no_heartbeat(client, caplog):
    class Broken(MockDeployer):
        def public_services(self, regions):
            raise RuntimeError("Cloud Run API unavailable")

    with caplog.at_level("INFO", logger="shipcube.worker"):
        Worker(Broken(), worker_id="w-exposure-3").check_exposure()
    assert not [r for r in caplog.records if r.getMessage() == "exposure check ran"]
    assert [r for r in caplog.records if r.getMessage() == "exposure check failed"]
    caplog.clear()
    with caplog.at_level("CRITICAL", logger="shipcube.worker"):
        Worker(MockDeployer(), worker_id="w-exposure-2").check_exposure()  # no check on the mock: nothing
    assert not [r for r in caplog.records if r.levelname == "CRITICAL"]
