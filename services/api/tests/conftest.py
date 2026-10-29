"""Tests run against a real Postgres, in this order:
1. SHIPCUBE_TEST_DATABASE_URL, if set;
2. DATABASE_URL when running in CI (the postgres:16 service container in .github/workflows/ci.yml).
   Outside CI it is ignored, so tests never write into a developer's own database;
3. a throwaway local cluster started with initdb/pg_ctl."""

import os
import shutil
import subprocess
import tempfile
import time
import uuid

import pytest

os.environ.setdefault("SHIPCUBE_DEV_MODE", "1")
os.environ.setdefault("SHIPCUBE_BACKEND", "mock")
os.environ.setdefault("SHIPCUBE_MOCK_SPEED", "0.05")
os.environ.setdefault("SHIPCUBE_TOKEN_PEPPER", "test-pepper")
os.environ.setdefault("SHIPCUBE_LOCAL_SECRETS_FILE", os.path.join(tempfile.mkdtemp(prefix="scsec-"), "secrets.json"))


def _start_local_cluster() -> tuple[str, callable]:
    data = tempfile.mkdtemp(prefix="scpg-")
    sock = tempfile.mkdtemp(prefix="scs", dir="/tmp")
    env = {**os.environ, "LC_ALL": "en_US.UTF-8", "LANG": "en_US.UTF-8"}
    subprocess.run(["initdb", "-D", data, "-U", "postgres", "-A", "trust"], check=True, capture_output=True, env=env)
    port = str(56000 + os.getpid() % 1000)
    subprocess.run(["pg_ctl", "-D", data, "-o", f"-p {port} -k {sock} -c listen_addresses=''", "-l", f"{data}/log", "start", "-w"],
                   check=True, capture_output=True, env=env)
    subprocess.run(["createdb", "-h", sock, "-p", port, "-U", "postgres", "shipcube_test"], check=True, env=env)

    def stop():
        subprocess.run(["pg_ctl", "-D", data, "stop", "-m", "fast"], capture_output=True, env=env)
        shutil.rmtree(data, ignore_errors=True)
        shutil.rmtree(sock, ignore_errors=True)

    return f"postgresql://postgres@/shipcube_test?host={sock}&port={port}", stop


_url = os.environ.get("SHIPCUBE_TEST_DATABASE_URL") or (os.environ.get("DATABASE_URL") if os.environ.get("CI") else None)
_stop = None
if not _url:
    if not shutil.which("initdb"):
        raise RuntimeError("tests need Postgres: set SHIPCUBE_TEST_DATABASE_URL or install postgresql (initdb on PATH)")
    _url, _stop = _start_local_cluster()
os.environ["SHIPCUBE_DATABASE_URL"] = _url
# shipcube-data's catalog (one schema per workspace) lives in the same test database; /v1/data/* is mounted
# when the package is installed (tests/test_data_routes.py), and skipped otherwise.
os.environ.setdefault("SHIPCUBE_DATA_CATALOG_URL", _url)


def pytest_sessionfinish(session, exitstatus):
    from shipcube_api import db, store_runtime

    store_runtime.stop_embedded_worker()
    db.close()
    if _stop:
        _stop()


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from shipcube_api.app import app

    with TestClient(app) as c:
        yield c


def new_workspace(email: str | None = None, *, console: bool = True, scopes: list[str] | None = None) -> dict:
    """A user + workspace with generous limits, a PAT and (DEV_MODE) a console session."""
    from shipcube_api import admin, auth, db

    email = email or f"u-{uuid.uuid4().hex[:8]}@example.com"
    slug = f"ws-{uuid.uuid4().hex[:8]}"
    user_id, ws_id, _ = admin.bootstrap(email, slug, "Test", None)
    with db.tx() as conn:
        conn.execute("update caps set limit_value = 50 where workspace_id = %s and app_id is null and kind in ('max_apps','deploys_per_day')",
                     (ws_id,))
    pat = auth.mint_pat(user_id, ws_id, scopes or ["apps:read", "apps:deploy", "access:write", "caps:write", "secrets:write"])
    out = {"email": email, "user_id": user_id, "workspace_id": ws_id, "slug": slug,
           "agent": {"Authorization": f"Bearer {pat}"}}
    if console:
        ses = f"sc_ses_{uuid.uuid4().hex[:8]}_{uuid.uuid4().hex}{uuid.uuid4().hex[:8]}"
        auth.register_dev_session(ses, user_id, ws_id)
        out["human"] = {"Authorization": f"Bearer {ses}"}
    return out


def wait_op(client, headers, op_id: str, timeout: float = 30, until=("succeeded", "failed", "cancelled")) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        op = client.get(f"/v1/operations/{op_id}", headers=headers).json()
        if op["status"] in until or time.monotonic() > deadline:
            return op
        time.sleep(0.1)
