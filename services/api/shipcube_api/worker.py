"""Durable operations worker: a Postgres-backed queue on security's `operations` table.

- Claim: `FOR UPDATE SKIP LOCKED` on due operations (`queued`, or `running` with an expired lease),
  so any number of workers can run side by side and a crashed worker's operation is picked up again.
- Lease: 90 s, extended by a heartbeat every 30 s while a step runs.
- Steps: every step is idempotent and its outputs are saved in `step_state` before the next one
  starts, so a retry or a new worker resumes from `step_index` (a running build is re-attached by id).
- Retries: transient cloud errors (429, 5xx, timeouts, network) back off exponentially with jitter,
  up to `max_attempts`; anything else fails the operation at once with a typed error.
- Lanes: estimates never wait behind deploys, or deploys behind estimates. A worker runs one lane:
  `deploy` (every kind but estimate), `estimate`, or `all` (DEV_MODE's embedded worker and tests).
  `run_lanes` starts SHIPCUBE_WORKER_LANES (default: 1 deploy thread, 3 estimate threads).
- Profiler guard (profiler_guard.py): at most 3 profiled estimates run at once, 1 per workspace,
  fairest workspace first; each charges the spend ledger before it starts, or runs static.
- Housekeeping every minute: expire unanswered approvals, enqueue purges of apps past their 7-day
  restore window, run long-waiting profiled estimates static, and sweep profiler leftovers
  (profiler_janitor.py).

Production: run standalone as its own Cloud Run service, signed in as sc-worker (database role
shipcube_worker, BYPASSRLS): `python -m shipcube_api.worker`. Every transaction here runs as
shipcube_worker (`db.worker_tx`). The API's own login (sc-api, shipcube_app) can't act for other
workspaces, so the embedded worker (SHIPCUBE_EMBEDDED_WORKER=1) is only for DEV_MODE and tests.
Operate it with `python -m shipcube_api.admin ops ...`.
"""

from __future__ import annotations

import logging
import os
import random
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

from psycopg.types.json import Jsonb

from . import abuse_screen, db, obs, profiler_guard, profiler_janitor
from .deploy import isolation
from .deploy.base import AppRef, Deployer, DeployError
from .estimator import Estimator, make_estimator, measurements
from .settings import settings
from .store import STEPS, app_ref, current_deployment, enqueue, grants, write_app_caps, write_grants

log = logging.getLogger("shipcube.worker")
LEASE_S = 90
HEARTBEAT_S = 30
RETRYABLE = {"unavailable", "timeout", "rate_limited"}
HINTS = ("expected_users", "schedule", "region", "traffic")  # estimate_load hints passed to the estimator
LANES = {"all": "true", "deploy": "o.kind <> 'estimate'", "estimate": "o.kind = 'estimate'"}


class Cancelled(Exception):
    pass


def backoff(attempt: int) -> float:
    return min(300.0, 5.0 * 2 ** (attempt - 1)) * random.uniform(0.8, 1.2)


class Worker:
    def __init__(self, deployer: Deployer, estimator: Estimator | None = None, worker_id: str | None = None,
                 lane: str = "all", janitor: profiler_janitor.Janitor | None = None) -> None:
        if lane not in LANES:
            raise ValueError(f"worker lane must be one of {sorted(LANES)}, not {lane!r}")
        self.d = deployer
        self.estimator = estimator or make_estimator(deployer)
        self.lane = lane
        self.janitor = janitor
        self.housekeeps = True  # run_lanes leaves it to one worker per process
        self.thread: threading.Thread | None = None
        self.id = worker_id or f"{settings.worker_id}/{lane}/{uuid.uuid4().hex[:6]}"
        self._stop = threading.Event()
        self._last_housekeeping = 0.0
        self._last_exposure_check = 0.0

    # --- loop ---------------------------------------------------------------------------

    def run_forever(self, poll_s: float = 1.0) -> None:
        log.info("worker started", extra={"worker": self.id})
        while not self._stop.is_set():
            try:
                if self.housekeeps and time.monotonic() - self._last_housekeeping > 60:
                    self.housekeeping()
                    self._last_housekeeping = time.monotonic()
                if not self.run_once():
                    self._stop.wait(poll_s)
            except Exception:  # noqa: BLE001 — the loop must survive a bad operation or a DB blip
                log.exception("worker loop error")
                self._stop.wait(5)

    def stop(self) -> None:
        self._stop.set()

    def start_thread(self) -> threading.Thread:
        t = threading.Thread(target=self.run_forever, name=f"shipcube-worker-{self.lane}", daemon=True)
        t.start()
        return t

    def claim(self) -> dict[str, Any] | None:
        with db.worker_tx() as conn:
            if self.lane != "deploy":  # makes the profiled-run count in claim_predicate race-free
                conn.execute("select pg_advisory_xact_lock(%s)", (profiler_guard.CLAIM_LOCK,))
            return conn.execute(
                "update operations set status = 'running', locked_by = %(me)s, locked_until = now() + make_interval(secs => %(lease)s),"
                " attempts = attempts + 1, started_at = coalesce(started_at, now()), updated_at = now()"
                " where id = (select o.id from operations o where o.status in ('queued','running') and o.next_attempt_at <= now()"
                "   and (o.locked_until is null or o.locked_until < now())"
                f"   and {LANES[self.lane]} and {profiler_guard.claim_predicate('o')}"
                f"   order by {profiler_guard.fairness_order('o')}, o.next_attempt_at"
                "   for update skip locked limit 1) returning *",
                {"me": self.id, "lease": LEASE_S, "global_runs": profiler_guard.Limits.from_env().global_runs}).fetchone()

    def run_once(self) -> bool:
        op = self.claim()
        if op is None:
            return False
        token = obs.operation_id.set(str(op["id"]))
        try:
            self.execute(op)
        finally:
            obs.operation_id.reset(token)
        return True

    # --- one operation ----------------------------------------------------------------------

    def execute(self, op: dict[str, Any]) -> None:
        kind = op["kind"]
        names = [n for n, _ in STEPS[kind]]
        state: dict[str, Any] = dict(op["step_state"] or {})
        timings: dict[str, float] = dict(op["timings"] or {})
        result: Any = op["result"]
        log.info("operation running", extra={"kind": kind, "attempt": op["attempts"], "step_index": op["step_index"]})
        beat = threading.Event()
        hb = threading.Thread(target=self._heartbeat, args=(op["id"], beat), daemon=True)
        hb.start()
        i = op["step_index"]
        try:
            plan = self._plan(op, state)
            for i in range(op["step_index"], len(names)):
                if self._cancel_requested(op["id"]):
                    raise Cancelled()
                name, message = STEPS[kind][i]
                self._progress(op["id"], names, i, message)
                t0 = time.monotonic()
                out = plan[name]()
                timings[name] = round(timings.get(name, 0) + time.monotonic() - t0, 1)
                if out is not None:
                    result = out
                self._save(op["id"], names, i + 1, state, timings, result)
                log.info("step done", extra={"kind": kind, "step": name, "seconds": timings[name]})
            self._finish(op, "succeeded", None, None, names, result, timings)
        except Cancelled:
            self._finish(op, "cancelled", "cancelled", "Cancelled by the caller", names, result, timings, failed_step=i)
        except DeployError as e:
            if e.code in RETRYABLE and op["attempts"] < op["max_attempts"]:
                delay = backoff(op["attempts"])
                log.warning("step failed; retrying", extra={"step": names[i], "code": e.code, "retry_in_s": round(delay, 1),
                                                             "error": str(e)[:500]})
                self._retry(op["id"], names, i, str(e), e.code, delay)
            else:
                log.error("operation failed", extra={"step": names[i], "code": e.code, "error": str(e)[:1000]})
                self._finish(op, "failed", e.code, str(e), names, result, timings, failed_step=i)
        except Exception as e:  # noqa: BLE001 — unknown errors fail the operation, never vanish
            log.exception("operation crashed", extra={"step": names[i] if i < len(names) else None})
            self._finish(op, "failed", "internal", f"{type(e).__name__}: {e}", names, result, timings, failed_step=i)
        finally:
            beat.set()

    def _heartbeat(self, op_id: Any, done: threading.Event) -> None:
        while not done.wait(HEARTBEAT_S):
            with db.worker_tx() as conn:
                conn.execute("update operations set locked_until = now() + make_interval(secs => %s) where id = %s and locked_by = %s",
                             (LEASE_S, op_id, self.id))

    def _cancel_requested(self, op_id: Any) -> bool:
        with db.worker_tx() as conn:
            return bool(conn.execute("select cancel_requested from operations where id = %s", (op_id,)).fetchone()["cancel_requested"])

    @staticmethod
    def _steps(names: list[str], done: int, current: str | None, current_status: str = "running") -> list[dict[str, str]]:
        return [{"name": n, "status": "succeeded" if j < done else (current_status if n == current else "pending")}
                for j, n in enumerate(names)]

    def _progress(self, op_id: Any, names: list[str], i: int, message: str) -> None:
        with db.worker_tx() as conn:
            conn.execute("update operations set step = %s, step_index = %s, steps = %s, progress_message = %s, updated_at = now()"
                         " where id = %s", (names[i], i, Jsonb(self._steps(names, i, names[i])), message, op_id))

    def _save(self, op_id: Any, names: list[str], next_index: int, state: dict, timings: dict, result: Any) -> None:
        with db.worker_tx() as conn:
            conn.execute("update operations set step_index = %s, step_state = %s, timings = %s, result = %s,"
                         " steps = %s, updated_at = now() where id = %s",
                         (next_index, Jsonb(state), Jsonb(timings), Jsonb(result) if result is not None else None,
                          Jsonb(self._steps(names, next_index, None)), op_id))

    def _retry(self, op_id: Any, names: list[str], i: int, error: str, code: str, delay: float) -> None:
        with db.worker_tx() as conn:
            conn.execute("update operations set status = 'queued', locked_by = null, locked_until = null,"
                         " next_attempt_at = now() + make_interval(secs => %s), progress_message = %s, error = %s, error_code = %s,"
                         " steps = %s, updated_at = now() where id = %s",
                         (delay, f"Retrying {names[i]} in {delay:.0f}s after: {error[:200]}", error[:2000], code,
                          Jsonb(self._steps(names, i, names[i], "retrying")), op_id))

    def _finish(self, op: dict[str, Any], status: str, code: str | None, error: str | None, names: list[str], result: Any,
                timings: dict, failed_step: int | None = None) -> None:
        done = len(names) if status == "succeeded" else (failed_step or 0)
        steps = self._steps(names, done, names[failed_step] if failed_step is not None and status != "succeeded" else None,
                            "cancelled" if status == "cancelled" else "failed")
        if isinstance(result, dict) and status == "succeeded":
            result = {**result, "timings_s": timings}
        message = "Done" if status == "succeeded" else f"{status.capitalize()}: {(error or '')[:250]}"
        with db.worker_tx() as conn:
            conn.execute("update operations set status = %s, error_code = %s, error = %s, result = %s, steps = %s, timings = %s,"
                         " progress_message = %s, locked_by = null, locked_until = null, finished_at = now(), updated_at = now()"
                         " where id = %s", (status, code, error and error[:4000], Jsonb(result) if result is not None else None,
                                            Jsonb(steps), Jsonb(timings), message, op["id"]))
            if status != "succeeded":
                self._on_failure(conn, op, status, error)
        log.info("operation finished", extra={"kind": op["kind"], "status": status, "code": code, "timings_s": timings})

    @staticmethod
    def _on_failure(conn, op: dict[str, Any], status: str, error: str | None) -> None:
        if op["kind"] == "deploy" and (dep := (op["input"] or {}).get("deployment_id")):
            conn.execute("update deployments set status = 'failed', error = %s, finished_at = now() where id = %s",
                         (error or status, dep))
            conn.execute("update apps set status = case when exists (select 1 from deployments where app_id = apps.id and"
                         " status = 'succeeded') then 'running' else 'failed' end where id = %s", (op["app_id"],))

    # --- step plans -----------------------------------------------------------------------------

    def _app(self, app_id: Any) -> tuple[dict[str, Any], AppRef]:
        with db.worker_tx() as conn:
            row = conn.execute("select * from apps where id = %s", (app_id,)).fetchone()
            return row, app_ref(conn, row)

    def _principals(self, app_id: Any) -> list[str]:
        with db.worker_tx() as conn:
            return [f"{g['principal_type']}:{g['principal']}" for g in grants(conn, app_id)]

    def _push_access(self, app_id: Any, ref: AppRef) -> list[str]:
        principals = self._principals(app_id)
        self.d.set_access(ref, principals)
        with db.worker_tx() as conn:
            conn.execute("update access_grants set sync_state = 'applied', last_synced_at = now() where app_id = %s and"
                         " revoked_at is null", (app_id,))
            conn.execute("update access_grants set sync_state = 'applied', last_synced_at = now() where app_id = %s and"
                         " revoked_at is not null and sync_state = 'removing'", (app_id,))
        return principals

    def _plan(self, op: dict[str, Any], state: dict[str, Any]) -> dict[str, Callable[[], Any]]:
        kind, inp = op["kind"], op["input"] or {}
        if kind == "estimate":
            def profile() -> None:
                if "profiled" not in state:  # once per operation: a retry doesn't charge the ledger again
                    with db.worker_tx() as conn:
                        state.update(profiler_guard.start_run(conn, op))

            def place() -> dict[str, Any]:
                try:
                    res = self.estimator.estimate(inp["source"], {k: inp.get(k) for k in HINTS},
                                                  workspace_id=str(op["workspace_id"]), op_id=str(op["id"]),
                                                  static_reason=state.get("static_reason"))
                except Exception as e:
                    # The pre-screen blocked the source before profiling (shipcube_profiler.AbuseRefused):
                    # a typed refusal and strikes, never an internal failure.
                    if type(e).__name__ != "AbuseRefused":
                        raise
                    verdict = getattr(e, "verdict", None)
                    verdict = verdict.to_dict() if hasattr(verdict, "to_dict") else verdict
                    abuse_screen.enforce(op, verdict or {"verdict": "blocked", "reasons": list(getattr(e, "codes", []) or [])},
                                         getattr(e, "source_key", None))  # keyed by what the estimator screened
                    raise  # enforce raised the typed refusal; this is only reached if the verdict wasn't blocked
                key = res.pop("source_key", None) if isinstance(res, dict) else None  # what this run read
                if signals := measurements(res).get("abuse_signals"):
                    with db.worker_tx() as conn:
                        profiler_guard.record_abuse(conn, op, list(signals), key)
                return res

            return {"profile": profile, "place": place}

        app, ref = self._app(op["app_id"])
        app_id = app["id"]

        if kind == "deploy":
            dep_id = inp["deployment_id"]

            def validate() -> None:
                if "prescreen" not in state:  # before staging or any build; refuses a blocked source
                    screened = abuse_screen.screen(self.d, str(op["workspace_id"]), inp["source"]) \
                        if getattr(self.d, "screens_sources", True) else None
                    verdict, pins = screened or (None, {})
                    if verdict is not None:
                        key = abuse_screen.source_key(self.d, str(op["workspace_id"]), inp["source"], pins) \
                            if abuse_screen.strikes(verdict) else None  # keyed only when there's a strike to count
                        abuse_screen.enforce(op, verdict, key)
                    state["prescreen"] = (verdict or {}).get("verdict", "skipped")
                    # Build exactly what was screened: the upload generation or git commit it read.
                    state["source"] = {**inp["source"], **pins}
                if "staged" not in state:
                    state["staged"] = self.d.stage_source(ref, state.get("source") or inp["source"], str(op["id"]))
                with db.worker_tx() as conn:
                    conn.execute("update apps set status = 'deploying' where id = %s", (app_id,))
                    conn.execute("update deployments set status = 'building' where id = %s", (dep_id,))

            def build() -> None:
                def on_build(build_id: str) -> None:  # saved at once so a retry re-attaches instead of rebuilding
                    state["build_id"] = build_id
                    with db.worker_tx() as conn:
                        conn.execute("update operations set step_state = %s where id = %s", (Jsonb(state), op["id"]))

                state["image"] = self.d.build(ref, state["staged"], str(op["id"]), lambda: self._cancel_requested(op["id"]),
                                              on_build, existing_build_id=state.get("build_id"))

            def push() -> dict[str, Any]:
                return {"app": ref.name, "image": state["image"], "build_id": state.get("build_id")}

            def deploy() -> None:
                with db.worker_tx() as conn:
                    conn.execute("update deployments set status = 'deploying' where id = %s", (dep_id,))
                    caps = {**self._app_caps(conn, app), **{k: v for k, v in (inp.get("caps") or {}).items() if v is not None}}
                isolation.check_image(settings, ref.workspace_id, state["image"],  # every deployer, before Cloud Run
                                      linked=ref.target.kind == "linked")
                d = self.d.deploy(ref, state["image"], caps, inp.get("env") or {})
                state.update(url=d.url, revision=d.revision, caps=caps)

            def configure_access() -> None:
                state["access"] = self._push_access(app_id, ref)

            def health_check() -> dict[str, Any]:
                st = self.d.status(ref)
                if not st.get("ready"):
                    raise DeployError("unavailable", f"service not ready yet: {st.get('condition')}")
                with db.worker_tx() as conn:
                    if inp.get("caps"):
                        write_app_caps(conn, str(app["workspace_id"]), app_id, inp["caps"], op["requested_by"])
                    conn.execute("update apps set status = 'running', url = %s, tier = coalesce(%s, tier), service_name = %s,"
                                 " runtime_sa = %s where id = %s",
                                 (state["url"], inp.get("tier"), ref.service, ref.runtime_sa, app_id))
                    conn.execute("update deployments set status = 'succeeded', revision = %s, image_digest = %s,"
                                 " finished_at = now() where id = %s", (state["revision"], state["image"], dep_id))
                return {"app": ref.name, "url": state["url"], "revision": state["revision"], "image": state["image"],
                        "target": ref.target.id, "build_id": state.get("build_id"),
                        "access": "private (IAP): " + ", ".join(state.get("access", []))}

            return {"validate": validate, "build": build, "push": push, "deploy": deploy,
                    "configure_access": configure_access, "health_check": health_check}

        if kind == "rollback":
            def shift() -> None:
                self.d.route_traffic(ref, inp["to_revision"])

            def check() -> dict[str, Any]:
                st = self.d.status(ref)
                if not st.get("ready"):
                    raise DeployError("unavailable", f"service not ready yet: {st.get('condition')}")
                with db.worker_tx() as conn:
                    conn.execute("update deployments set status = 'rolled_back' where id = %s", (inp["from_deployment"],))
                    conn.execute("update deployments set finished_at = now() where id = %s", (inp["to_deployment"],))
                return {"app": ref.name, "revision": inp["to_revision"], "url": app["url"]}

            return {"shift_traffic": shift, "health_check": check}

        if kind == "delete":
            def scale_to_zero() -> None:
                self.d.soft_delete(ref)

            def soft_delete() -> dict[str, Any]:
                with db.worker_tx() as conn:
                    row = conn.execute("update apps set status_before_delete = status, status = 'deleted', deleted_at = now(),"
                                       " deleted_by = %s, purge_after = now() + interval '7 days' where id = %s returning purge_after",
                                       (op["requested_by"], app_id)).fetchone()
                return {"app": ref.name, "deleted": True, "restorable_until": row["purge_after"].isoformat()}

            return {"scale_to_zero": scale_to_zero, "soft_delete": soft_delete}

        if kind == "restore":
            def restore() -> dict[str, Any]:
                self.d.restore(ref, self._principals(app_id))
                with db.worker_tx() as conn:
                    conn.execute("update apps set status = coalesce(status_before_delete, 'running'), status_before_delete = null,"
                                 " deleted_at = null, deleted_by = null, purge_after = null where id = %s", (app_id,))
                return {"app": ref.name, "restored": True, "url": app["url"]}

            return {"restore": restore}

        if kind == "purge":
            def purge() -> dict[str, Any]:
                self.d.purge(ref)
                with db.worker_tx() as conn:
                    conn.execute("update apps set purged_at = now() where id = %s", (app_id,))
                return {"app": ref.name, "purged": True}

            return {"purge": purge}

        if kind == "set_caps":
            def apply_caps() -> dict[str, Any]:
                with db.worker_tx() as conn:
                    if not inp.get("push_only"):
                        write_app_caps(conn, str(app["workspace_id"]), app_id, inp["caps"], op["requested_by"])
                    caps = self._app_caps(conn, app)
                if app["status"] == "running" and current_deployment_exists(app_id):
                    self.d.update_caps(ref, caps)
                return {"caps": caps}

            return {"apply": apply_caps}

        if kind == "set_access":
            def apply_access() -> dict[str, Any]:
                if not inp.get("push_only"):
                    with db.worker_tx() as conn:
                        write_grants(conn, str(app["workspace_id"]), app_id, inp.get("add", []), inp.get("remove", []),
                                     op["requested_by"])
                principals = self._push_access(app_id, ref) if app["status"] == "running" else self._principals(app_id)
                return {"access": principals}

            return {"apply": apply_access}

        raise DeployError("invalid_argument", f"unknown operation kind {kind}")

    @staticmethod
    def _app_caps(conn, app: dict[str, Any]) -> dict[str, Any]:
        from .store import app_caps

        return app_caps(conn, str(app["workspace_id"]), app["id"])

    # --- housekeeping ------------------------------------------------------------------------

    def housekeeping(self) -> None:
        with db.worker_tx() as conn:
            expired = conn.execute(
                "update operations set status = 'cancelled', error_code = 'approval_expired', error = 'No decision within 72 hours',"
                " progress_message = 'No decision within 72 hours', finished_at = now(), updated_at = now()"
                " where status = 'awaiting_approval' and approval_expires_at < now() returning id").fetchall()
            conn.execute("update approvals set status = 'expired', decided_at = now() where status = 'pending' and expires_at < now()")
            # Approvals whose operation was withdrawn by the agent (RLS keeps agents from touching approvals).
            conn.execute("update approvals a set status = 'cancelled', decided_at = now() from operations o"
                         " where a.operation_id = o.id and a.status = 'pending' and o.status = 'cancelled'")
            due = conn.execute("select a.*, (select project_id from gcp_links where id = a.gcp_link_id) as link_project from apps a"
                               " where a.deleted_at is not null and a.purge_after < now() and a.purged_at is null").fetchall()
            for app in due:
                enqueue(conn, None, str(app["workspace_id"]), "purge", app["id"], app["link_project"] or settings.apps_project,
                        {}, f"purge:{app['id']}")
            busy = profiler_guard.expire_waiting(conn)
        if expired or due or busy:
            log.info("housekeeping", extra={"approvals_expired": len(expired), "purges_enqueued": len(due),
                                            "estimates_static_busy": busy})
        if self.janitor is not None:
            try:
                self.janitor.sweep()
            except Exception:  # noqa: BLE001 — a cloud error must not stop the rest of housekeeping
                log.exception("profiler janitor failed")
        if time.monotonic() - self._last_exposure_check > 3600:
            self._last_exposure_check = time.monotonic()
            self.check_exposure()

    def check_exposure(self) -> None:
        """Hourly: no hosted app may be callable by anyone (allUsers / allAuthenticatedUsers), which
        would bypass IAP. Logged CRITICAL for alerting; ShipCube never makes such a grant itself."""
        check = getattr(self.d, "public_services", None)
        if check is None:
            return
        with db.worker_tx() as conn:
            regions = [r["region"] for r in conn.execute(
                "select distinct region from apps where gcp_link_id is null and deleted_at is null")]
        try:
            exposed, checked = check(regions or [settings.region])
        except Exception:  # noqa: BLE001
            log.exception("exposure check failed")  # no heartbeat: the absence alert fires after 3 h
            return
        for name in exposed:  # these exact messages feed alerts (infra/terraform/modules/monitoring)
            log.critical("hosted service is public: anyone can call it past IAP", extra={"service": name})
        log.info("exposure check ran", extra={"services_checked": checked, "public": len(exposed)})


def current_deployment_exists(app_id: Any) -> bool:
    with db.worker_tx() as conn:
        return current_deployment(conn, app_id) is not None


def lanes_from_env() -> list[str]:
    """SHIPCUBE_WORKER_LANES, e.g. "deploy,estimate*3": one worker thread per entry (×n repeats it)."""
    out: list[str] = []
    for part in os.environ.get("SHIPCUBE_WORKER_LANES", "deploy,estimate*3").split(","):
        lane, _, n = part.strip().partition("*")
        out += [lane] * int(n or 1)
    return out


def run_lanes(deployer: Deployer, factory: type[Worker] = Worker) -> list[Worker]:
    """Start one worker thread per lane. Only the first worker runs the profiler janitor, so the
    sweep happens once per process. Returns the workers (call stop() on each to shut down)."""
    janitor = profiler_janitor.from_env()
    workers = [factory(deployer, lane=lane, janitor=janitor if i == 0 else None) for i, lane in enumerate(lanes_from_env())]
    for i, w in enumerate(workers):
        w.housekeeps = i == 0
        w.thread = w.start_thread()
    return workers


def main() -> None:
    from .store_runtime import deployer

    obs.setup_logging()
    db.check_schema(migrate_if_dev=settings.dev_mode)
    workers = run_lanes(deployer())
    for w in workers:
        w.thread.join()


if __name__ == "__main__":
    main()
