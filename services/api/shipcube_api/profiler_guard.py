"""Who may run the profiler sandbox, and how often (docs/research/profiler-sandbox.md §6–7).

A profiled estimate builds and runs the user's code in the sandbox, which costs money and shares
Cloud Build and Cloud Run quota with hosted apps. Everything here decides whether an estimate is
profiled or answered statically, and says why with a `static_reason` (§9):

- Admission (API, `start_estimate`): at most 2 profiled estimates in flight per workspace (the
  third gets 429 `rate_limited` with `retry_after`), and 20 per workspace per day (more run static,
  reason=quota). Image sources never run (reason=image_source).
- Claim (worker): at most 3 profiled runs at once, at most 1 per workspace, fairest workspace first.
  A profiled estimate that waited 10 minutes for a slot runs static (reason=busy).
- Run start (worker): the spend ledger. Each profiled run adds its worst-case cost to today's
  `profiler_spend` row; once today's total reaches $5 globally, or $1 for the workspace, new runs
  are static (reason=budget). `SHIPCUBE_PROFILER_ENABLED=0` or the janitor's brake makes them
  static too (reason=disabled).

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
    if (payload.get("source") or {}).get("kind") == "image":
        return static("image_source")
    if not configured() or not enabled():
        return static("disabled")
    lim = Limits.from_env()
    row = conn.execute(
        "select count(*) filter (where status in ('queued','running')) as in_flight,"
        "       count(*) filter (where created_at >= date_trunc('day', now() at time zone 'utc') at time zone 'utc') as today"
        " from operations where workspace_id = %s and kind = 'estimate' and (input->>'profiled')::boolean",
        (workspace_id,)).fetchone()
    if row["in_flight"] >= lim.in_flight_per_workspace:
        raise RateLimited(f"{row['in_flight']} profiled estimates are already running or queued in this workspace "
                          f"(limit {lim.in_flight_per_workspace}); retry when one finishes", retry_after_s=60)
    if row["today"] >= lim.per_workspace_per_day:
        return static("quota")
    return dict(PROFILED)


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
    return {"configured": configured(), "enabled": enabled(), "limits": Limits.from_env().__dict__,
            "running": running, "brake": brake and {"until": brake["until"].isoformat(), "reason": brake["reason"]},
            "today_usd": float(sum(r["usd"] for r in spend)),
            "today_by_workspace": [{"workspace_id": str(r["workspace_id"]), "runs": r["runs"], "usd": float(r["usd"])}
                                   for r in spend]}
