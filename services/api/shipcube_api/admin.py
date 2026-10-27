"""Operator CLI (runbook in services/api/README.md).

  python -m shipcube_api.admin migrate
  python -m shipcube_api.admin bootstrap --email you@gmail.com --workspace demo --name "Demo"   # prints a PAT once
  python -m shipcube_api.admin mint-pat --email you@gmail.com --workspace demo --scopes apps:read,apps:deploy
  python -m shipcube_api.admin link --workspace demo --project acme-prod --deployer-sa ws-..@... --runtime-sa shipcube-apps@acme-prod...
  python -m shipcube_api.admin ops [--stuck] [--limit 20]
  python -m shipcube_api.admin requeue <operation-id>        # resume a failed/stuck operation from its last step
  python -m shipcube_api.admin fail <operation-id> --reason "..."
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid

from . import auth, db
from .store import CAP_PERIOD, WORKSPACE_DEFAULTS


def _workspace(conn, slug: str) -> dict:
    ws = conn.execute("select * from workspaces where slug = %s", (slug,)).fetchone()
    if ws is None:
        sys.exit(f"workspace {slug} not found")
    return ws


def _user(conn, email: str) -> dict:
    u = conn.execute("select * from users where email = %s", (email,)).fetchone()
    if u is None:
        sys.exit(f"user {email} not found")
    return u


def bootstrap(email: str, slug: str, name: str, firebase_uid: str | None) -> tuple[str, str, str]:
    with db.tx() as conn:
        u = conn.execute("select * from users where email = %s", (email,)).fetchone() or conn.execute(
            "insert into users (id, firebase_uid, email, email_verified) values (%s, %s, %s, true) returning *",
            (uuid.uuid4(), firebase_uid or f"pending:{email}", email)).fetchone()
        ws = conn.execute("select * from workspaces where slug = %s", (slug,)).fetchone() or conn.execute(
            "insert into workspaces (id, slug, name, created_by) values (%s, %s, %s, %s) returning *",
            (uuid.uuid4(), slug, name, u["id"])).fetchone()
        conn.execute("insert into memberships (workspace_id, user_id, role) values (%s, %s, 'owner') on conflict do nothing",
                     (ws["id"], u["id"]))
        for kind, value in WORKSPACE_DEFAULTS.items():
            conn.execute("insert into caps (id, workspace_id, app_id, kind, limit_value, period) values (%s,%s,null,%s,%s,%s)"
                         " on conflict do nothing", (uuid.uuid4(), ws["id"], kind, value, CAP_PERIOD.get(kind)))
    return str(u["id"]), str(ws["id"]), email


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="shipcube_api.admin")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    b = sub.add_parser("bootstrap")
    b.add_argument("--email", required=True)
    b.add_argument("--workspace", required=True)
    b.add_argument("--name", default="Workspace")
    b.add_argument("--firebase-uid")
    b.add_argument("--no-token", action="store_true")
    m = sub.add_parser("mint-pat")
    m.add_argument("--email", required=True)
    m.add_argument("--workspace", required=True)
    m.add_argument("--scopes", default=",".join(s for s in auth.SCOPES if s != "data:raw"))
    m.add_argument("--days", type=int, default=30)
    m.add_argument("--name", default="mcp")
    lk = sub.add_parser("link")
    lk.add_argument("--workspace", required=True)
    lk.add_argument("--project", required=True)
    lk.add_argument("--deployer-sa", required=True)
    lk.add_argument("--runtime-sa", required=True)
    lk.add_argument("--region", default="asia-south1")
    o = sub.add_parser("ops")
    o.add_argument("--stuck", action="store_true", help="running with an expired lease, or retried more than twice")
    o.add_argument("--limit", type=int, default=20)
    r = sub.add_parser("requeue")
    r.add_argument("id")
    f = sub.add_parser("fail")
    f.add_argument("id")
    f.add_argument("--reason", required=True)
    a = p.parse_args(argv)

    if a.cmd == "migrate":
        print("applied:", db.migrate() or "nothing")
        return
    db.migrate()
    if a.cmd == "bootstrap":
        user_id, ws_id, email = bootstrap(a.email, a.workspace, a.name, a.firebase_uid)
        print(json.dumps({"user_id": user_id, "workspace_id": ws_id}))
        if not a.no_token:
            print("PAT (shown once):", auth.mint_pat(user_id, ws_id, [s for s in auth.SCOPES if s != "data:raw"]))
    elif a.cmd == "mint-pat":
        with db.tx() as conn:
            u, ws = _user(conn, a.email), _workspace(conn, a.workspace)
        print(auth.mint_pat(str(u["id"]), str(ws["id"]), a.scopes.split(","), a.name, a.days))
    elif a.cmd == "link":
        with db.tx() as conn:
            ws = _workspace(conn, a.workspace)
            row = conn.execute(
                "insert into gcp_links (id, workspace_id, project_id, deployer_sa, runtime_sa, region, status, linked_by, verified_at)"
                " values (%s,%s,%s,%s,%s,%s,'active',%s, now()) returning id",
                (uuid.uuid4(), ws["id"], a.project, a.deployer_sa, a.runtime_sa, a.region, ws["created_by"])).fetchone()
        print(json.dumps({"target": str(row["id"])}))
    elif a.cmd == "ops":
        q = ("select id, kind, status, step, attempts, locked_by, locked_until, next_attempt_at, error_code,"
             " left(error, 160) as error, updated_at from operations")
        if a.stuck:
            q += (" where (status = 'running' and locked_until < now()) or (status in ('queued','running') and attempts > 2)"
                  " or (status = 'queued' and next_attempt_at < now() - interval '5 minutes')")
        with db.tx() as conn:
            for row in conn.execute(q + " order by updated_at desc limit %s", (a.limit,)).fetchall():
                print(json.dumps(row, default=str))
    elif a.cmd == "requeue":
        with db.tx() as conn:
            row = conn.execute("update operations set status = 'queued', locked_by = null, locked_until = null,"
                               " next_attempt_at = now(), attempts = 0, error = null, error_code = null, finished_at = null,"
                               " updated_at = now() where id = %s and status in ('failed','running','queued') returning id, step_index",
                               (a.id,)).fetchone()
        print(json.dumps(row, default=str) if row else "not requeued (unknown id, or cancelled/succeeded/awaiting approval)")
    elif a.cmd == "fail":
        with db.tx() as conn:
            row = conn.execute("update operations set status = 'failed', error_code = 'operator', error = %s, locked_by = null,"
                               " locked_until = null, finished_at = now(), updated_at = now() where id = %s and status in"
                               " ('queued','running') returning id", (a.reason, a.id)).fetchone()
        print("failed" if row else "not changed")


if __name__ == "__main__":
    main()
