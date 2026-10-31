"""Who may run the profiler sandbox, and how often (docs/research/profiler-sandbox.md §6–7).

A profiled estimate builds and runs the user's code in the sandbox, which costs money and shares
Cloud Build and Cloud Run quota with hosted apps. Everything here decides whether an estimate is
profiled or answered statically, and says why with a `static_reason` (§9):

- Admission (API, `start_estimate`): at most 2 profiled estimates in flight per workspace (the
  third gets 429 `rate_limited` with `retry_after`), and 20 per workspace per day (more run static,
  reason=quota). Image sources never run (reason=image_source). Static estimates are admitted too:
  at most 3 in flight per workspace, since they still unpack the source in the worker.
- Claim (worker): at most 3 profiled runs at once, at most 1 per workspace, fairest workspace first.
  A profiled estimate that waited 10 minutes for a slot runs static (reason=busy).
- Run start (worker): the spend ledger. Each profiled run adds its worst-case cost to today's
  `profiler_spend` row; once today's total reaches $5 globally, or $1 for the workspace, new runs
  are static (reason=budget). `SHIPCUBE_PROFILER_ENABLED=0` or the janitor's brake makes them
  static too (reason=disabled), and so does a suspension (reason=suspended).
- After the run (worker): every abuse signal the profiler reports is a strike. 3 strikes in 24 hours
  suspend the workspace's profiling for 24 hours; a mining_pool signal suspends it until a person
  clears it (§8).

The decision is stored in the operation's input (`profiled`, `static_reason`), outside the
idempotency hash, so the claim query can count profiled runs and the result can say why it's static.
Defaults are the design doc's; each can be overridden by its environment variable.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from psycopg.types.json import Jsonb

from .settings import settings

log = logging.getLogger("shipcube.profiler_guard")

# Serialises the claim of profiled estimates, so the global count can't race (§6.3).
CLAIM_LOCK = 0x5C_9F_01
# Serialises ledger updates, so two runs can't both slip under the cap.
LEDGER_LOCK = 0x5C_9F_02


class RateLimited(Exception):
    def __init__(self, message: str, retry_after_s: int) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


def _num(name: str, default: float) -> float:
    return float(os.environ.get(name) or default)


@dataclass(frozen=True)
class Limits:
    in_flight_per_workspace: int
    static_in_flight_per_workspace: int
    per_workspace_per_day: int
    global_runs: int
    queue_wait_s: int
    daily_usd: float
    workspace_daily_usd: float
    run_cost_usd: float

    @classmethod
    def from_env(cls) -> Limits:
        return cls(
            in_flight_per_workspace=int(_num("SHIPCUBE_PROFILER_IN_FLIGHT", 2)),
            static_in_flight_per_workspace=int(_num("SHIPCUBE_ESTIMATE_STATIC_IN_FLIGHT", 3)),
            per_workspace_per_day=int(_num("SHIPCUBE_PROFILER_PER_DAY", 20)),
            global_runs=int(_num("SHIPCUBE_PROFILER_GLOBAL_RUNS", 3)),
            queue_wait_s=int(_num("SHIPCUBE_PROFILER_QUEUE_WAIT_S", 600)),
            daily_usd=_num("SHIPCUBE_PROFILER_DAILY_USD", 5),
            workspace_daily_usd=_num("SHIPCUBE_PROFILER_WORKSPACE_DAILY_USD", 1),
            # Worst case per run (§7): $0.11 with today's 900 s build; $0.05 once builds stop at 300 s.
            run_cost_usd=_num("SHIPCUBE_PROFILER_RUN_COST_USD", 0.11),
        )


def configured() -> bool:
    """Whether this deployment can profile at all: the GCP sandbox job, or an explicit local
    sandbox (SHIPCUBE_PROFILER=local|gcp). Otherwise every estimate is static, as before."""
    return bool(settings.profiler_job) or os.environ.get("SHIPCUBE_PROFILER", "").strip().lower() in ("local", "gcp")


def enabled() -> bool:
    """The manual off switch, read on every admission and run (§8)."""
    return os.environ.get("SHIPCUBE_PROFILER_ENABLED", "1") != "0"


def static(reason: str) -> dict[str, Any]:
    return {"profiled": False, "static_reason": reason}


PROFILED: dict[str, Any] = {"profiled": True, "static_reason": None}

# --- admission (API, in the caller's transaction; RLS limits counts to the caller's workspace) ------


def admit(conn, workspace_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Profiled or static for a new estimate; raises RateLimited when the workspace already has
    the maximum number of profiled estimates in flight."""
    lim = Limits.from_env()
    row = conn.execute(
        "select count(*) filter (where status in ('queued','running') and profiled) as profiled_in_flight,"
        "       count(*) filter (where status in ('queued','running') and not profiled) as static_in_flight,"
        "       count(*) filter (where profiled and created_at >= date_trunc('day', now() at time zone 'utc') at time zone 'utc')"
        "         as profiled_today"
        " from (select status, created_at, coalesce((input->>'profiled')::boolean, false) as profiled from operations"
        "       where workspace_id = %s and kind = 'estimate') e", (workspace_id,)).fetchone()
    if (payload.get("source") or {}).get("kind") == "image":
        decision = static("image_source")
    elif not configured() or not enabled():
        decision = static("disabled")
    elif row["profiled_in_flight"] >= lim.in_flight_per_workspace:
        raise RateLimited(f"{row['profiled_in_flight']} profiled estimates are already running or queued in this workspace "
                          f"(limit {lim.in_flight_per_workspace}); retry when one finishes", retry_after_s=60)
    elif row["profiled_today"] >= lim.per_workspace_per_day:
        decision = static("quota")
    else:
        return dict(PROFILED)
    # Static estimates still download and unpack the source in the worker, next to the deploy lane.
    if row["static_in_flight"] >= lim.static_in_flight_per_workspace:
        raise RateLimited(f"{row['static_in_flight']} estimates are already running or queued in this workspace "
                          f"(limit {lim.static_in_flight_per_workspace}); retry when one finishes", retry_after_s=30)
    return decision


# --- claim (worker) ----------------------------------------------------------------------------


def claim_predicate(alias: str = "o") -> str:
    """SQL: a candidate operation may be claimed unless it's a profiled estimate and the sandbox
    already runs the global maximum, or one for the same workspace. Takes %(global_runs)s."""
    running = ("select 1 from operations r where r.kind = 'estimate' and r.status = 'running'"
               " and r.locked_until > now() and (r.input->>'profiled')::boolean")
    return (f"({alias}.kind <> 'estimate' or not coalesce(({alias}.input->>'profiled')::boolean, false) or"
            f" ((select count(*) from ({running}) n) < %(global_runs)s"
            f"  and not exists ({running} and r.workspace_id = {alias}.workspace_id)))")


def fairness_order(alias: str = "o") -> str:
    """SQL: estimates from the workspace that profiled least recently go first (§6.3)."""
    return (f"case when {alias}.kind = 'estimate' then (select max(p.started_at) from operations p"
            f" where p.kind = 'estimate' and p.workspace_id = {alias}.workspace_id) end asc nulls first")


def expire_waiting(conn) -> int:
    """Profiled estimates that waited longer than the queue limit for a slot run static (busy)."""
    lim = Limits.from_env()
    rows = conn.execute(
        "update operations set input = input || %s, updated_at = now() where kind = 'estimate' and status = 'queued'"
        " and started_at is null and (input->>'profiled')::boolean and created_at < now() - make_interval(secs => %s)"
        " returning id", (Jsonb(static("busy")), lim.queue_wait_s)).fetchall()
    if rows:
        log.info("profiled estimates waited too long; running static", extra={"count": len(rows), "reason": "busy"})
    return len(rows)


# --- run start (worker): the spend ledger -------------------------------------------------------


def start_run(conn, op: dict[str, Any]) -> dict[str, Any]:
    """Decide, once per operation, whether this estimate is profiled now. A profiled run is charged
    its worst-case cost to today's ledger before it starts. The decision is written to the input."""
    inp = op["input"] or {}
    if not inp.get("profiled"):
        decision = static(inp.get("static_reason") or "disabled")
    elif not enabled() or brake_on(conn):
        decision = static("disabled")
    elif suspended(conn, op["workspace_id"]):
        decision = static("suspended")
    else:
        decision = _charge(conn, op["workspace_id"], Limits.from_env())
    conn.execute("update operations set input = input || %s where id = %s", (Jsonb(decision), op["id"]))
    return decision


def _charge(conn, workspace_id: Any, lim: Limits) -> dict[str, Any]:
    conn.execute("select pg_advisory_xact_lock(%s)", (LEDGER_LOCK,))
    day = "(now() at time zone 'utc')::date"
    totals = conn.execute(
        f"select coalesce(sum(usd), 0) as total, coalesce(sum(usd) filter (where workspace_id = %s), 0) as mine"
        f" from profiler_spend where day = {day}", (workspace_id,)).fetchone()
    if float(totals["total"]) >= lim.daily_usd or float(totals["mine"]) >= lim.workspace_daily_usd:
        log.warning("profiler spend cap reached; running static",
                    extra={"global_usd": float(totals["total"]), "workspace_usd": float(totals["mine"]), "reason": "budget"})
        return static("budget")
    conn.execute(
        f"insert into profiler_spend (day, workspace_id, runs, usd) values ({day}, %s, 1, %s)"
        " on conflict (day, workspace_id) do update set runs = profiler_spend.runs + 1,"
        " usd = profiler_spend.usd + excluded.usd, updated_at = now()", (workspace_id, lim.run_cost_usd))
    return dict(PROFILED)


# --- strikes and suspensions (worker, after a profiled run) -----------------------------------------------

STRIKES_TO_SUSPEND = 3
STRIKE_WINDOW_H = 24
SUSPEND_H = 24
HUMAN_ONLY = {"mining_pool", "prescreen_crypto_miner"}  # suspended until a person clears it (abuse_screen.py)


def record_abuse(conn, op: dict[str, Any], signals: list[str]) -> str | None:
    """Record each abuse signal of a run as a strike, and suspend the workspace when the policy says
    so. Idempotent per (operation, signal), so a retried step doesn't add strikes. Returns the
    suspension's reason if this run caused one."""
    if not signals:
        return None
    ws = op["workspace_id"]
    for s in sorted(set(signals)):
        conn.execute("insert into profiler_strikes (workspace_id, operation_id, signal) values (%s, %s, %s)"
                     " on conflict (operation_id, signal) do nothing", (ws, op["id"], s))
    log.error("profiler abuse signals; strike recorded", extra={"workspace_id": str(ws), "signals": sorted(set(signals))})
    if human := sorted(HUMAN_ONLY & set(signals)):
        reason = f"{human[0]} in operation {op['id']}"
        conn.execute("insert into profiler_suspensions (workspace_id, until, reason) values (%s, null, %s)"
                     " on conflict (workspace_id) do update set until = null, reason = excluded.reason, set_at = now()",
                     (ws, reason))
    else:
        n = conn.execute("select count(*) as n from profiler_strikes where workspace_id = %s"
                         " and created_at > now() - make_interval(hours => %s)", (ws, STRIKE_WINDOW_H)).fetchone()["n"]
        if n < STRIKES_TO_SUSPEND:
            return None
        reason = f"{n} strikes in {STRIKE_WINDOW_H} h"
        # Never shortens a longer suspension, or lifts one that waits for a person.
        conn.execute("insert into profiler_suspensions (workspace_id, until, reason) values (%s, now() + make_interval(hours => %s), %s)"
                     " on conflict (workspace_id) do update set until = excluded.until, reason = excluded.reason, set_at = now()"
                     " where profiler_suspensions.until is not null and profiler_suspensions.until < excluded.until",
                     (ws, SUSPEND_H, reason))
    log.error("profiler suspended for a workspace; its estimates run static", extra={"workspace_id": str(ws), "reason": reason})
    return reason


def suspended(conn, workspace_id: Any) -> bool:
    return conn.execute("select 1 from profiler_suspensions where workspace_id = %s and (until is null or until > now())",
                        (workspace_id,)).fetchone() is not None


def unsuspend(conn, workspace_id: str) -> bool:
    return conn.execute("delete from profiler_suspensions where workspace_id = %s returning 1", (workspace_id,)).fetchone() is not None


# --- the brake ---------------------------------------------------------------------------------------


def brake_on(conn) -> bool:
    return conn.execute("select 1 from profiler_flags where name = 'brake' and until > now()").fetchone() is not None


def set_brake(conn, reason: str, minutes: int = 15) -> None:
    conn.execute("insert into profiler_flags (name, until, reason) values ('brake', now() + make_interval(mins => %s), %s)"
                 " on conflict (name) do update set until = excluded.until, reason = excluded.reason, set_at = now()",
                 (minutes, reason))


def clear_brake(conn) -> None:
    conn.execute("delete from profiler_flags where name = 'brake'")


def status(conn) -> dict[str, Any]:
    """For `admin profiler`: today's spend, runs in flight and the brake."""
    spend = conn.execute("select workspace_id, runs, usd from profiler_spend where day = (now() at time zone 'utc')::date"
                         " order by usd desc").fetchall()
    running = conn.execute("select count(*) as n from operations where kind = 'estimate' and status = 'running'"
                           " and (input->>'profiled')::boolean").fetchone()["n"]
    brake = conn.execute("select until, reason from profiler_flags where name = 'brake' and until > now()").fetchone()
    held = conn.execute("select workspace_id, until, reason from profiler_suspensions where until is null or until > now()"
                        " order by set_at desc").fetchall()
    return {"configured": configured(), "enabled": enabled(), "limits": Limits.from_env().__dict__,
            "running": running, "brake": brake and {"until": brake["until"].isoformat(), "reason": brake["reason"]},
            "suspended": [{"workspace_id": str(r["workspace_id"]), "until": r["until"] and r["until"].isoformat(),
                           "reason": r["reason"]} for r in held],
            "today_usd": float(sum(r["usd"] for r in spend)),
            "today_by_workspace": [{"workspace_id": str(r["workspace_id"]), "runs": r["runs"], "usd": float(r["usd"])}
                                   for r in spend]}
