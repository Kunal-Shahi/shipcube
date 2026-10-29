"""The local secret provider: generated once, kept private, and refused outside local."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

LOCAL = Path(__file__).resolve().parents[1]


def run(script: str, tmp: Path, *args: str, **env: str) -> subprocess.CompletedProcess:
    e = {k: v for k, v in os.environ.items() if k not in ("K_SERVICE", "CLOUD_RUN_JOB", "SHIPCUBE_ENV")}
    e.update(SHIPCUBE_LOCAL_DIR=str(tmp), **env)
    return subprocess.run([sys.executable, str(LOCAL / script), *args], env=e, capture_output=True, text=True, timeout=10, check=False)


def test_init_is_idempotent_and_private(tmp_path):
    assert run("localsecrets.py", tmp_path, "init").returncode == 0
    first = json.loads((tmp_path / "secrets.json").read_text())
    assert run("localsecrets.py", tmp_path, "init").returncode == 0
    assert json.loads((tmp_path / "secrets.json").read_text()) == first  # never rotated under a live database
    for name in ("token-hmac-pepper", "sql-postgres-password", "db-sc-migrator", "db-sc-api", "db-sc-worker"):
        assert first[name].startswith("sclocal_") and len(first[name]) > 40
    for f in ("secrets.json", "secrets.env"):
        assert (tmp_path / f).stat().st_mode & 0o077 == 0, f
    env = (tmp_path / "secrets.env").read_text()
    assert f"SHIPCUBE_TOKEN_PEPPER={first['token-hmac-pepper']}\n" in env
    assert "sc-api%40shipcube-control-ks.iam:" in env and "@db:5432/shipcube" in env


@pytest.mark.parametrize("env", [{"K_SERVICE": "shipcube-mcp"}, {"CLOUD_RUN_JOB": "migrate"}, {"SHIPCUBE_ENV": "production"}])
@pytest.mark.parametrize("script", ["localsecrets.py", "secretmanager.py"])
def test_refuses_outside_local(tmp_path, env, script):
    assert run("localsecrets.py", tmp_path, "init").returncode == 0
    r = run(script, tmp_path, *(["init"] if script == "localsecrets.py" else ["--port", "0"]), **env)
    assert r.returncode != 0 and "refuse" in r.stderr
