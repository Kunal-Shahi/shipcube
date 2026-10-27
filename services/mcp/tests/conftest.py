"""MCP tests run the real API in-process (DEV_MODE: mock deployer) on a real Postgres:
SHIPCUBE_TEST_DATABASE_URL; in CI, DATABASE_URL (the workflow's postgres:16 service container,
ignored outside CI so tests never touch a developer's database); otherwise a throwaway local
cluster (initdb/pg_ctl on PATH)."""

import os
import shutil
import subprocess
import tempfile
import uuid

import pytest

os.environ.setdefault("SHIPCUBE_DEV_MODE", "1")
os.environ.setdefault("SHIPCUBE_BACKEND", "mock")
os.environ.setdefault("SHIPCUBE_MOCK_SPEED", "0.02")
os.environ.setdefault("SHIPCUBE_TOKEN_PEPPER", "test-pepper")

_stop = None
if not os.environ.get("SHIPCUBE_TEST_DATABASE_URL") and os.environ.get("CI") and os.environ.get("DATABASE_URL"):
    os.environ["SHIPCUBE_TEST_DATABASE_URL"] = os.environ["DATABASE_URL"]
if not os.environ.get("SHIPCUBE_TEST_DATABASE_URL"):
    if not shutil.which("initdb"):
        raise RuntimeError("tests need Postgres: set SHIPCUBE_TEST_DATABASE_URL or install postgresql (initdb on PATH)")
    data, sock = tempfile.mkdtemp(prefix="scpg-"), tempfile.mkdtemp(prefix="scm", dir="/tmp")
    env = {**os.environ, "LC_ALL": "en_US.UTF-8", "LANG": "en_US.UTF-8"}
    port = str(57000 + os.getpid() % 1000)
    subprocess.run(["initdb", "-D", data, "-U", "postgres", "-A", "trust"], check=True, capture_output=True, env=env)
    subprocess.run(["pg_ctl", "-D", data, "-o", f"-p {port} -k {sock} -c listen_addresses=''", "-l", f"{data}/log", "start", "-w"],
                   check=True, capture_output=True, env=env)
    subprocess.run(["createdb", "-h", sock, "-p", port, "-U", "postgres", "shipcube_test"], check=True, env=env)
    os.environ["SHIPCUBE_TEST_DATABASE_URL"] = f"postgresql://postgres@/shipcube_test?host={sock}&port={port}"

    def _stop():
        subprocess.run(["pg_ctl", "-D", data, "stop", "-m", "fast"], capture_output=True, env=env)
        shutil.rmtree(data, ignore_errors=True)
        shutil.rmtree(sock, ignore_errors=True)
os.environ["SHIPCUBE_DATABASE_URL"] = os.environ["SHIPCUBE_TEST_DATABASE_URL"]


@pytest.fixture(scope="session", autouse=True)
def api_started():
    from shipcube_api import db
    from shipcube_api.app import shutdown, startup

    startup()
    yield
    shutdown()
    db.close()
    if _stop:
        _stop()


@pytest.fixture
def agent_token():
    from shipcube_api import admin, auth

    user_id, ws_id, _ = admin.bootstrap(f"u-{uuid.uuid4().hex[:8]}@example.com", f"ws-{uuid.uuid4().hex[:8]}", "T", None)
    return auth.mint_pat(user_id, ws_id, ["apps:read", "apps:deploy", "access:write", "caps:write", "data:read"])
