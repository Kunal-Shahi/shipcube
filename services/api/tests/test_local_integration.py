"""The local backend end to end against real Docker and fake-gcs-server: signed upload, build, a
private container behind the local IAP, logs, rollback, purge, and cross-workspace rejection.

Needs Docker. In CI (CI=true) a missing Docker is a failure; on a laptop without it, the test is
skipped with the reason. Uses the Dockerfile path; the pack path needs `pack` and is covered by
test_local_deployer.py."""

import io
import os
import shutil
import subprocess
import tarfile
import time
from dataclasses import replace

import httpx
import pytest
from starlette.testclient import TestClient

from shipcube_api.deploy.base import AppRef, DeployError, Target
from shipcube_api.deploy.local import LocalDeployer
from shipcube_api.local_iap import LocalIap
from shipcube_api.settings import Settings

if not shutil.which("docker") or subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
    if os.environ.get("CI"):
        raise RuntimeError("the local-backend integration test needs Docker in CI")
    pytest.skip("Docker is not available on this machine", allow_module_level=True)

GCS_IMAGE = "fsouza/fake-gcs-server:1.55.1"
GCS_PORT = 4443
NETWORK = "shipcube-local-test"
WS_A = "3c1d5e2a-7b4f-4e8a-9c6d-2f1e0a9b8c7d"
WS_B = "9f0e8d7c-6b5a-4f3e-8d2c-1b0a9f8e7d6c"
APP_PY = """
import http.server, os
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = (f"{os.environ.get('GREETING')} {self.headers.get('x-goog-authenticated-user-email')}").encode()
        print("served", self.path, flush=True)
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
http.server.ThreadingHTTPServer(("", int(os.environ["PORT"])), H).serve_forever()
"""
DOCKERFILE = "FROM python:3.12-alpine\nCOPY app.py /app.py\nCMD [\"python\", \"/app.py\"]\n"


def sh(*args, check=True):
    return subprocess.run(args, capture_output=True, text=True, check=check).stdout.strip()


@pytest.fixture(scope="module")
def deployer(tmp_path_factory):
    sh("docker", "rm", "-f", "sc-test-gcs", check=False)
    sh("docker", "network", "create", NETWORK, check=False)
    sh("docker", "run", "-d", "--name", "sc-test-gcs", "-p", f"127.0.0.1:{GCS_PORT}:{GCS_PORT}", GCS_IMAGE,
       "-scheme", "http", "-port", str(GCS_PORT), "-public-host", f"localhost:{GCS_PORT}")
    for _ in range(60):
        try:
            if httpx.get(f"http://localhost:{GCS_PORT}/_internal/healthcheck", timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    url = f"http://localhost:{GCS_PORT}"
    d = LocalDeployer(replace(Settings(), local_state_dir=str(tmp_path_factory.mktemp("state")), local_gcs_url=url,
                              local_gcs_public_url=url, local_network=NETWORK))
    yield d
    sh("docker", "rm", "-f", "sc-test-gcs", check=False)
    for c in sh("docker", "ps", "-aq", "--filter", "label=managed-by=shipcube", check=False).split():
        sh("docker", "rm", "-f", c, check=False)
    sh("docker", "network", "rm", NETWORK, check=False)


def app(ws: str, name: str = "hello") -> AppRef:
    return AppRef(name=name, workspace_id=ws, target=Target("shipcube", "shipcube", "shipcube-apps-ks"), region="asia-south1")


def tarball() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, text in (("Dockerfile", DOCKERFILE), ("app.py", APP_PY)):
            info = tarfile.TarInfo(name)
            info.size = len(text)
            tar.addfile(info, io.BytesIO(text.encode()))
    return buf.getvalue()


def test_local_backend_end_to_end(deployer, monkeypatch):
    d, a, b = deployer, app(WS_A), app(WS_B, "thief")

    up = d.create_upload(WS_A, "application/gzip")
    assert f"/ws/{WS_A}/uploads/" in up["url"]
    r = httpx.put(up["url"], content=tarball(), headers=up["headers"], timeout=30)
    assert r.status_code in (200, 201), r.text
    source = {"kind": "upload", "upload_id": up["upload_id"]}

    # Workspace B can't use A's upload id: it resolves inside B's own prefix, where nothing exists.
    with pytest.raises(DeployError) as e:
        d.stage_source(b, source, "op_b")
    assert e.value.code == "invalid_argument"

    staged = d.stage_source(a, source, "op_1")
    image = d.build(a, staged, "op_1", lambda: False, lambda _: None)
    assert image.startswith(f"shipcube.local/ws-{WS_A.replace('-', '')[:20]}/{a.service}@sha256:")
    first = d.deploy(a, image, {"cpu": "0.5", "memory": "512Mi", "concurrency": 4, "timeout_s": 10}, {"GREETING": "v1"})
    d.set_access(a, ["user:alice@acme.com"])

    # B can't run A's image, under A's name or rewritten into B's namespace.
    with pytest.raises(DeployError) as e:
        d.deploy(b, image, {}, {})
    assert e.value.code == "permission_denied"
    with pytest.raises(DeployError) as e:
        d.deploy(b, image.replace(WS_A.replace("-", "")[:20], WS_B.replace("-", "")[:20]), {}, {})
    assert e.value.code == "permission_denied"

    # Private by default: only Alice, through the local IAP.
    async def resolve(token):
        return {"sc_pat_alice": {"email": "alice@acme.com", "user_id": "u1"},
                "sc_pat_bob": {"email": "bob@acme.com", "user_id": "u2"}}.get(token)

    client = TestClient(LocalIap(d.s, resolve=resolve), base_url=f"http://{a.service}.localhost:8088")
    assert client.get("/").status_code == 401
    assert client.get("/", headers={"Authorization": "Bearer sc_pat_bob"}).status_code == 403
    r = client.get("/hi", headers={"Authorization": "Bearer sc_pat_alice"})
    assert r.status_code == 200 and r.text == "v1 accounts.google.com:alice@acme.com", r.text
    assert any("served /hi" in e["message"] for e in d.logs(first.revision, 20))

    second = d.deploy(a, image, {}, {"GREETING": "v2"})
    assert client.get("/", headers={"Authorization": "Bearer sc_pat_alice"}).text.startswith("v2")
    d.route_traffic(a, first.revision)
    assert client.get("/", headers={"Authorization": "Bearer sc_pat_alice"}).text.startswith("v1")
    assert d.status(a)["latest_ready_revision"] == first.revision != second.revision

    d.soft_delete(a)
    assert client.get("/", headers={"Authorization": "Bearer sc_pat_alice"}).status_code == 404
    d.purge(a)
    assert not d.status(a)["exists"]
    assert sh("docker", "ps", "-aq", "--filter", f"label=shipcube.service={a.service}") == ""


def test_api_and_durable_worker_on_the_local_backend(client, deployer, monkeypatch):
    """The same through the API: create_upload, deploy operations run by the worker, live status,
    and another workspace's upload id or image refused."""
    from shipcube_api import app as api_app
    from shipcube_api import store_runtime
    from shipcube_api.worker import Worker

    from .conftest import new_workspace

    store_runtime.stop_embedded_worker()
    monkeypatch.setattr(api_app, "deployer", lambda: deployer)
    worker = Worker(deployer, worker_id="local-it")
    a, b = new_workspace(), new_workspace()

    def run(ws, slug, source):
        r = client.post(f"/v1/apps/{slug}/deployments", json={"source": source}, headers=ws["agent"])
        assert r.status_code == 202, r.text
        while worker.run_once():
            pass
        return client.get(f"/v1/operations/{r.json()['id']}", headers=ws["agent"]).json()

    try:
        up = client.post("/v1/uploads", json={}, headers=a["agent"]).json()
        assert f"/ws/{a['workspace_id']}/uploads/" in up["url"]
        assert httpx.put(up["url"], content=tarball(), headers=up["headers"], timeout=30).status_code in (200, 201)

        stolen = run(b, "thief", {"kind": "upload", "upload_id": up["upload_id"]})
        assert stolen["status"] == "failed" and stolen["error"]["code"] == "invalid_argument"

        op = run(a, "hello-api", {"kind": "upload", "upload_id": up["upload_id"]})
        assert op["status"] == "succeeded", op
        image = op["result"]["image"]
        assert op["result"]["url"].endswith(".localhost:8088")
        view = client.get("/v1/apps/hello-api", headers=a["agent"]).json()
        assert view["runtime"]["ready"] and view["runtime"]["access"] == [f"user:{a['email']}"]

        r = client.post("/v1/apps/img-thief/deployments", json={"source": {"kind": "image", "uri": image}}, headers=b["agent"])
        assert r.status_code == 403 and r.json()["error"]["code"] == "permission_denied"
    finally:
        deployer.purge(app(a["workspace_id"], "hello-api"))
        store_runtime.start_embedded_worker()
