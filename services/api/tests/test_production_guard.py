"""#29: on Cloud Run the API, MCP server, worker and admin jobs refuse dev mode and local adapters."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from shipcube_api.settings import Settings, on_cloud_run, production_problems

PROD = {
    "K_SERVICE": "shipcube-mcp",
    "SHIPCUBE_CONSOLE_URL": "https://console.example.com",
    "SHIPCUBE_AI_GATEWAY_URL": "https://gateway.example.com",
    "SHIPCUBE_TOKEN_PEPPER": "a-real-pepper-from-secret-manager",
}
SWITCHES = ("SHIPCUBE_DEV_MODE", "DEV_MODE", "SHIPCUBE_BACKEND", "SHIPCUBE_SECRET_STORE", "SHIPCUBE_EMBEDDED_WORKER",
            "SHIPCUBE_PROFILER", "SHIPCUBE_MONITOR",
            "SHIPCUBE_ESTIMATOR", "SHIPCUBE_ENV", "SHIPCUBE_CONSOLE_URL", "SHIPCUBE_AI_GATEWAY_URL", "SHIPCUBE_TOKEN_PEPPER",
            *("K_SERVICE", "K_REVISION", "CLOUD_RUN_JOB"))


def problems(monkeypatch, **env: str) -> list[str]:
    for k in SWITCHES:
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return production_problems(Settings(), dict(os.environ))


def test_a_correct_production_service_has_no_problems(monkeypatch):
    assert problems(monkeypatch, **PROD) == []


@pytest.mark.parametrize("env,expect", [
    ({"SHIPCUBE_DEV_MODE": "1"}, "dev mode"),
    ({"DEV_MODE": "1"}, "dev mode"),  # the deprecated alias counts
    ({"SHIPCUBE_BACKEND": "mock"}, "SHIPCUBE_BACKEND=mock"),
    ({"SHIPCUBE_BACKEND": "local"}, "SHIPCUBE_BACKEND=local"),
    ({"SHIPCUBE_SECRET_STORE": "local"}, "SHIPCUBE_SECRET_STORE=local"),
    ({"SHIPCUBE_EMBEDDED_WORKER": "1"}, "SHIPCUBE_EMBEDDED_WORKER"),
    ({"SHIPCUBE_ESTIMATOR": "dev"}, "SHIPCUBE_ESTIMATOR"),
    ({"SHIPCUBE_ENV": "local"}, "SHIPCUBE_ENV"),
    ({"SHIPCUBE_PROFILER": "local"}, "SHIPCUBE_PROFILER=local"),
    ({"SHIPCUBE_PROFILER": "dev"}, "SHIPCUBE_PROFILER=dev"),
    ({"SHIPCUBE_MONITOR": "local"}, "SHIPCUBE_MONITOR=local"),
    ({"SHIPCUBE_MONITOR": "mock"}, "SHIPCUBE_MONITOR=mock"),
    ({"SHIPCUBE_TOKEN_PEPPER": "sclocal_abc"}, "local value"),
    ({"SHIPCUBE_CONSOLE_URL": "http://localhost:3000"}, "SHIPCUBE_CONSOLE_URL"),
    ({"SHIPCUBE_CONSOLE_URL": "https://127.0.0.1"}, "SHIPCUBE_CONSOLE_URL"),
    ({"SHIPCUBE_CONSOLE_URL": "https://web.localhost"}, "SHIPCUBE_CONSOLE_URL"),
    ({"SHIPCUBE_CONSOLE_URL": "http://console.example.com"}, "not a public https URL"),
    ({"SHIPCUBE_AI_GATEWAY_URL": "http://host.docker.internal:8090"}, "SHIPCUBE_AI_GATEWAY_URL"),
])
def test_each_local_switch_is_a_problem(monkeypatch, env, expect):
    got = problems(monkeypatch, **{**PROD, **env})
    assert any(expect in p for p in got), got


def test_a_service_needs_its_console_url_but_a_job_does_not(monkeypatch):
    service = {k: v for k, v in PROD.items() if k != "SHIPCUBE_CONSOLE_URL"}
    assert any("SHIPCUBE_CONSOLE_URL is unset" in p for p in problems(monkeypatch, **service))
    job = {**{k: v for k, v in service.items() if k != "K_SERVICE"}, "CLOUD_RUN_JOB": "shipcube-admin"}
    assert problems(monkeypatch, **job) == []  # the admin job sends no approval links


def test_cloud_run_is_detected_by_any_of_its_variables():
    assert not on_cloud_run({})
    for k in ("K_SERVICE", "K_REVISION", "CLOUD_RUN_JOB"):
        assert on_cloud_run({k: "x"})


def _start(module: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    clean = {k: v for k, v in os.environ.items() if k not in SWITCHES}
    return subprocess.run([sys.executable, "-c", f"import {module}"], env={**clean, **env},
                          capture_output=True, text=True, timeout=60, check=False)


@pytest.mark.parametrize("module", ["shipcube_api.app", "shipcube_api.worker", "shipcube_api.admin"])
def test_startup_refuses_dev_mode_on_cloud_run(module):
    r = _start(module, {**PROD, "SHIPCUBE_DEV_MODE": "1"})
    assert r.returncode != 0 and "refusing to start on Cloud Run" in r.stderr and "dev mode" in r.stderr


def test_startup_proceeds_with_a_production_config():
    r = _start("shipcube_api.settings", PROD)
    assert r.returncode == 0, r.stderr


def test_dev_mode_still_works_off_cloud_run():
    r = _start("shipcube_api.settings", {"SHIPCUBE_DEV_MODE": "1"})
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("env", [{}, {"SHIPCUBE_PROFILER": ""}, {"SHIPCUBE_PROFILER": "gcp"}, {"SHIPCUBE_PROFILER": " GCP "},
                                 {"SHIPCUBE_MONITOR": "gcp"}])
def test_profiler_and_monitor_allow_gcp_or_unset(monkeypatch, env):
    # Unset SHIPCUBE_MONITOR resolves to gcp (shipcube_monitor), so the worker needn't set it.
    assert problems(monkeypatch, **{**PROD, **env}) == []


@pytest.mark.parametrize("env", [{"SHIPCUBE_PROFILER": "local"}, {"SHIPCUBE_MONITOR": "mock"}])
def test_startup_refuses_local_profiler_and_monitor(env):
    r = _start("shipcube_api.settings", {**PROD, **env})
    assert r.returncode != 0 and "refusing to start on Cloud Run" in r.stderr
