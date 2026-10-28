"""LocalDeployer command and state shapes, with the docker/pack CLIs and the object store faked.
tests/test_local_integration.py runs the same flow against real Docker and fake-gcs-server."""

import io
import json
import tarfile
from dataclasses import replace

import pytest

from shipcube_api.deploy import isolation
from shipcube_api.deploy.base import AppRef, DeployError, Target
from shipcube_api.deploy.local import LocalDeployer
from shipcube_api.settings import Settings

WS = "3c1d5e2a-7b4f-4e8a-9c6d-2f1e0a9b8c7d"
OTHER = "9f0e8d7c-6b5a-4f3e-8d2c-1b0a9f8e7d6c"
IMAGE_ID = "sha256:" + "c" * 64


class FakeDocker:
    def __init__(self):
        self.calls, self.tags = [], {}  # image id -> repo tags

    def run(self, args, *, timeout=900, cwd=None, check=True):
        self.calls.append(args)
        if args[:2] in (["docker", "build"], ["pack", "build"]):
            tag = args[args.index("-t") + 1] if args[1] == "build" and args[0] == "docker" else args[2]
            self.tags[IMAGE_ID] = [tag]
        if args[:3] == ["docker", "image", "inspect"]:
            ref = args[-1]
            if "{{.Id}}" in args:
                return IMAGE_ID if any(ref in t for t in self.tags.values()) or ref == IMAGE_ID else ""
            return json.dumps(self.tags.get(ref, []))
        if args[:2] == ["docker", "inspect"]:
            return "true"
        if args[:2] == ["docker", "port"]:
            return "127.0.0.1:1"
        return ""


class FakeBlob:
    def __init__(self, store, name):
        self.store, self.name, self.size = store, name, None

    def exists(self):
        return self.name in self.store

    def reload(self):
        self.size = len(self.store[self.name])

    def download_to_filename(self, path):
        open(path, "wb").write(self.store[self.name])

    def upload_from_string(self, data, content_type=None):
        self.store[self.name] = data


class FakeBucket:
    def __init__(self):
        self.store = {}

    def blob(self, name):
        return FakeBlob(self.store, name)


def tgz(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, text in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(text)
            tar.addfile(info, io.BytesIO(text.encode()))
    return buf.getvalue()


@pytest.fixture
def d(tmp_path, monkeypatch):
    dep = LocalDeployer(replace(Settings(), local_state_dir=str(tmp_path / "state")), docker=FakeDocker())
    bucket = FakeBucket()
    monkeypatch.setattr(dep, "_bucket", lambda: bucket)
    monkeypatch.setattr(dep, "_wait_ready", lambda rev, t: None)
    dep.fake_bucket = bucket
    return dep


def app(ws=WS, name="tracker"):
    return AppRef(name=name, workspace_id=ws, target=Target("shipcube", "shipcube", "shipcube-apps-ks"), region="asia-south1")


def test_upload_url_is_v4_signed_for_the_workspace_prefix(tmp_path):
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import storage

    dep = LocalDeployer(replace(Settings(), local_state_dir=str(tmp_path), local_gcs_public_url="http://localhost:4443"),
                        docker=FakeDocker())
    dep._client = storage.Client(project="p", credentials=AnonymousCredentials(), client_options={"api_endpoint": "http://x"})
    up = dep.create_upload(WS, "application/gzip")
    assert up["url"].startswith(f"http://localhost:4443/shipcube-uploads-ks/ws/{WS}/uploads/{up['upload_id']}.tgz?")
    assert "X-Goog-Signature=" in up["url"] and "X-Goog-Algorithm=GOOG4-RSA-SHA256" in up["url"]


def test_upload_build_uses_only_the_workspace_object_and_namespace(d):
    obj = isolation.upload_object(d.s, WS, "upl_0123456789ab")
    d.fake_bucket.store[obj] = tgz({"main.py": "print(1)", "requirements.txt": ""})
    staged = d.stage_source(app(), {"kind": "upload", "upload_id": "upl_0123456789ab"}, "op_1")
    assert staged == {"storageSource": {"bucket": "shipcube-uploads-ks", "object": obj}}
    image = d.build(app(), staged, "op_1", lambda: False, lambda _: None)
    ns = f"shipcube.local/ws-{WS.replace('-', '')[:20]}/"
    assert image == f"{ns}{app().service}@{IMAGE_ID}"
    pack = next(c for c in d.docker.calls if c[0] == "pack")
    assert pack[2] == f"{ns}{app().service}:op-1" and pack[pack.index("--builder") + 1] == d.s.builder_image


def test_dockerfile_is_built_with_docker(d):
    obj = isolation.inline_object(d.s, WS, "op_2")
    staged = d.stage_source(app(), {"kind": "inline", "files": [{"path": "Dockerfile", "content": "FROM scratch"}]}, "op_2")
    assert staged["storageSource"]["object"] == obj
    d.build(app(), staged, "op_2", lambda: False, lambda _: None)
    assert any(c[:2] == ["docker", "build"] for c in d.docker.calls) and not any(c[0] == "pack" for c in d.docker.calls)


def test_another_workspaces_upload_is_not_found_and_its_objects_are_refused(d):
    d.fake_bucket.store[isolation.upload_object(d.s, OTHER, "upl_0123456789ab")] = tgz({"a": "b"})
    with pytest.raises(DeployError) as e:
        d.stage_source(app(), {"kind": "upload", "upload_id": "upl_0123456789ab"}, "op_1")
    assert e.value.code == "invalid_argument" and "not found" in str(e.value)
    foreign = {"storageSource": {"bucket": "shipcube-uploads-ks", "object": isolation.upload_object(d.s, OTHER, "upl_0123456789ab")}}
    with pytest.raises(DeployError) as e:
        d.build(app(), foreign, "op_1", lambda: False, lambda _: None)
    assert e.value.code == "permission_denied" and not any(c[0] in ("pack",) or c[:2] == ["docker", "build"] for c in d.docker.calls)


def test_deploy_runs_a_capped_private_container_and_refuses_foreign_images(d):
    ns = f"shipcube.local/ws-{WS.replace('-', '')[:20]}/"
    d.docker.tags[IMAGE_ID] = [f"{ns}{app().service}:op-1"]
    out = d.deploy(app(), f"{ns}{app().service}@{IMAGE_ID}", {"cpu": "0.5", "memory": "1Gi", "concurrency": 10},
                   {"FOO": "bar"})
    run = next(c for c in d.docker.calls if c[:2] == ["docker", "run"])
    assert run[run.index("--cpus") + 1] == "0.5" and run[run.index("--memory") + 1] == "1024m"
    assert run[run.index("--network") + 1] == "shipcube-local" and "shipcube.app=tracker" in run and "-e" in run and "FOO=bar" in run and "PORT=8080" in run
    assert run[-1] == IMAGE_ID and out.url == f"http://{app().service}.localhost:8088"
    st = d.status(app())
    assert st["ready"] and st["caps"]["concurrency"] == 1 and st["latest_ready_revision"] == out.revision

    # Same image id, but tagged only in another workspace's namespace: refused.
    other_ns = f"shipcube.local/ws-{OTHER.replace('-', '')[:20]}/"
    d.docker.tags[IMAGE_ID] = [f"{other_ns}victim:op-1"]
    for image, code in ((f"{ns}{app().service}@{IMAGE_ID}", "permission_denied"),
                        (f"{other_ns}victim@{IMAGE_ID}", "permission_denied"),
                        (f"{ns}{app().service}:latest", "invalid_argument")):
        with pytest.raises(DeployError) as e:
            d.deploy(app(), image, {}, {})
        assert e.value.code == code


def test_rollback_delete_restore_and_purge(d):
    ns = f"shipcube.local/ws-{WS.replace('-', '')[:20]}/"
    d.docker.tags[IMAGE_ID] = [f"{ns}{app().service}:op-1"]
    image = f"{ns}{app().service}@{IMAGE_ID}"
    first = d.deploy(app(), image, {}, {}).revision
    second = d.deploy(app(), image, {}, {}).revision
    assert ["docker", "stop", "-t", "5", f"sc-{first}"] in d.docker.calls
    d.route_traffic(app(), first)
    assert d.status(app())["latest_ready_revision"] == first and ["docker", "start", f"sc-{first}"] in d.docker.calls
    d.set_access(app(), ["user:a@x.com"])
    d.soft_delete(app())
    st = d.status(app())
    assert st["ingress"] == "internal" and st["access"] == [] and not st["ready"]
    waited = []
    d._wait_ready = lambda rev, t: waited.append(rev)
    d.restore(app(), ["user:a@x.com"])
    assert waited == [first]
    assert d.status(app())["ingress"] == "all" and d.status(app())["access"] == ["user:a@x.com"]
    d.purge(app())
    assert ["docker", "rm", "-f", f"sc-{second}"] in d.docker.calls and not d.status(app())["exists"]
