"""GcpDeployer request shapes, against a fake HTTP session (no network, no credentials)."""

import json

import pytest

from shipcube_api.deploy import gcp, isolation
from shipcube_api.deploy.base import AppRef, DeployError, Target, normalize_caps
from shipcube_api.settings import Settings


class FakeResponse:
    def __init__(self, status: int, body: dict) -> None:
        self.status_code, self._body = status, body
        self.content = json.dumps(body).encode()
        self.text = self.content.decode()

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def request(self, method, url, timeout=None, json=None, params=None):
        self.calls.append((method, url.split("?")[0], json, params))
        for (m, frag), resp in self.routes.items():
            if m == method and frag in url:
                return FakeResponse(*resp(json) if callable(resp) else resp)
        return FakeResponse(200, {})

    def get(self, url, timeout=None):
        return self.request("GET", url)


@pytest.fixture
def deployer(monkeypatch):
    monkeypatch.setattr(gcp.google.auth, "default", lambda scopes: (object(), "p"))
    d = gcp.GcpDeployer(Settings())
    d._project_numbers["shipcube-apps-ks"] = "330999971585"
    d._iap_agents["shipcube-apps-ks"] = "service-330999971585@gcp-sa-iap.iam.gserviceaccount.com"
    return d


APP = AppRef(name="tracker", workspace_id="3c1d5e2a-7b4f-4e8a-9c6d-2f1e0a9b8c7d", target=Target("shipcube", "shipcube", "shipcube-apps-ks"), region="asia-south1")


def test_names_follow_rules():
    assert APP.service.startswith("tracker-") and len(APP.service) <= 49
    sa = APP.runtime_sa
    assert sa.startswith("app-") and sa.endswith("@shipcube-apps-ks.iam.gserviceaccount.com")
    assert 6 <= len(sa.split("@")[0]) <= 30


def test_caps_follow_cloud_run_rules():
    assert normalize_caps({"cpu": "0.5", "concurrency": 40, "memory": "256Mi"}) == {"cpu": "0.5", "concurrency": 1, "memory": "512Mi"}


WS = APP.workspace_id
REPO = f"asia-south1-docker.pkg.dev/shipcube-apps-ks/ws-{WS.replace('-', '')[:20]}"
DIGEST = "sha256:" + "a" * 64
OTHER_WS = "9f0e8d7c-6b5a-4f3e-8d2c-1b0a9f8e7d6c"


def test_build_runs_as_the_workspace_builder_and_pins_the_digest(deployer):
    obj = f"ws/{WS}/uploads/upl_0123456789ab.tgz"
    sess = FakeSession({
        ("POST", "/builds"): (200, {"metadata": {"build": {"id": "b1"}}}),
        ("GET", "/builds/b1"): (200, {"status": "SUCCESS"}),
        ("GET", "/tags/op-1"): (200, {"version": f"projects/p/locations/l/repositories/r/packages/x/versions/{DIGEST}"}),
    })
    deployer._sessions[None] = sess
    deployer._provisioned.add(WS)
    seen = []
    image = deployer.build(APP, {"storageSource": {"bucket": "shipcube-uploads-ks", "object": obj}}, "op_1", lambda: False, seen.append)
    assert seen == ["b1"] and image == f"{REPO}/{APP.service}@{DIGEST}"
    body = sess.calls[0][2]
    step = body["steps"][0]
    assert step["name"] == "gcr.io/k8s-skaffold/pack" and "--publish" in step["args"]
    assert f"{REPO}/{APP.service}:op-1" in step["args"]  # pushes only into the workspace's repo
    assert body["serviceAccount"] == (f"projects/shipcube-apps-ks/serviceAccounts/sc-build-{WS.replace('-', '')[:20]}"
                                      "@shipcube-apps-ks.iam.gserviceaccount.com")
    assert body["source"] == {"storageSource": {"bucket": "shipcube-uploads-ks", "object": obj}}
    # No logWriter for hosted builders (#28): logs go to the workspace's own folder in GCS.
    assert body["options"]["logging"] == "GCS_ONLY" and body["logsBucket"] == f"gs://shipcube-uploads-ks/ws/{WS}/build-logs"
    tag = next(c for c in sess.calls if "/tags/" in c[1])
    assert f"/repositories/ws-{WS.replace('-', '')[:20]}/packages/{APP.service}/tags/op-1" in tag[1]


def test_linked_builds_log_to_cloud_logging_in_the_users_project(deployer):
    sess = FakeSession({
        ("POST", "/builds"): (200, {"metadata": {"build": {"id": "b2"}}}),
        ("GET", "/builds/b2"): (200, {"status": "SUCCESS"}),
        ("GET", "/tags/op-2"): (200, {"version": f"projects/p/locations/l/repositories/r/packages/x/versions/{DIGEST}"}),
    })
    linked = AppRef("tracker", WS, Target("l1", "linked", "user-proj", deployer_sa="ws-d@c.iam.gserviceaccount.com",
                                          builder_sa="shipcube-builder@user-proj.iam.gserviceaccount.com"), "asia-south1")
    deployer._sessions["ws-d@c.iam.gserviceaccount.com"] = sess
    deployer.build(linked, {"gitSource": {"url": "https://github.com/x/y"}}, "op_2", lambda: False, lambda _: None)
    body = sess.calls[0][2]
    assert body["options"] == {"logging": "CLOUD_LOGGING_ONLY"} and "logsBucket" not in body  # link-project.sh grants logWriter there


def test_ensure_builder_provisions_one_sa_repo_and_folder_per_workspace(deployer):
    short = WS.replace("-", "")[:20]
    sa = f"sc-build-{short}@shipcube-apps-ks.iam.gserviceaccount.com"
    folder_iam = {"bindings": [{"role": "roles/storage.objectViewer", "members": ["serviceAccount:someone@x"],
                                "condition": {"title": "c"}}]}
    prov = FakeSession({
        ("POST", ":setIamPolicy"): (200, {}),
        ("POST", "/repositories"): (409, {"error": {"message": "exists"}}),  # a retry: already created
        ("GET", ":getIamPolicy"): (200, {"bindings": []}),
        ("GET", "/iam"): (200, folder_iam),
        ("POST", ":getIamPolicy"): (200, {"etag": "e"}),
    })
    deployer._sessions[deployer.s.provisioner_sa] = prov
    deployer.ensure_builder(WS)
    deployer.ensure_builder(WS)  # cached: no second round of calls
    calls = [(m, u.split("/v1/")[-1], b, p) for m, u, b, p in prov.calls]
    assert calls[0][0:2] == ("POST", "projects/shipcube-apps-ks/serviceAccounts") and calls[0][2]["accountId"] == f"sc-build-{short}"
    assert calls[1][3] == {"repositoryId": f"ws-{short}"} and calls[1][2]["format"] == "DOCKER"
    repo_set = next(c for c in calls if c[1].endswith(f"repositories/ws-{short}:setIamPolicy"))
    assert repo_set[2]["policy"]["bindings"] == [{"role": "roles/artifactregistry.writer", "members": [f"serviceAccount:{sa}"]}]
    mf = next(c for c in calls if c[0] == "POST" and c[1].endswith("/managedFolders"))
    assert mf[2] == {"name": f"ws/{WS}/"}
    puts = [c for c in calls if c[0] == "PUT"]
    assert puts[0][1].endswith(f"managedFolders/ws%2F{WS}%2F/iam")
    assert {"role": "roles/storage.objectViewer", "members": [f"serviceAccount:{sa}"]} in puts[0][2]["bindings"]
    # Its build-logs folder: the only place it can write, so builds need no logging.logWriter (#28).
    assert [c[2] for c in calls if c[0] == "POST" and c[1].endswith("/managedFolders")][1] == {"name": f"ws/{WS}/build-logs/"}
    assert puts[1][1].endswith(f"managedFolders/ws%2F{WS}%2Fbuild-logs%2F/iam")
    assert {"role": "roles/storage.objectUser", "members": [f"serviceAccount:{sa}"]} in puts[1][2]["bindings"]
    sa_set = next(c for c in calls if c[1].endswith(f"serviceAccounts/{sa}:setIamPolicy"))
    assert sa_set[2]["policy"]["etag"] == "e" and sorted(b["role"] for b in sa_set[2]["policy"]["bindings"]) == [
        "roles/iam.serviceAccountTokenCreator", "roles/iam.serviceAccountUser"]
    assert len(prov.calls) == 12  # SA, repo + IAM get/set, 2 folders + IAM get/put each, SA IAM get/set
    # A hosted build provisions first, as the provisioner, never with the deployer's own identity.
    assert all("managedFolders" not in u and "repositories?" not in u for _, u, _, _ in deployer._sessions.get(None, FakeSession({})).calls)


def test_ensure_link_deployer_only_creates_the_sa_in_the_links_project(deployer):
    sa = isolation.link_deployer_sa(deployer.s, WS)
    assert sa == f"ws-{WS.replace('-', '')[:12]}-deployer@shipcube-links-ks.iam.gserviceaccount.com"
    adc = FakeSession({("POST", "/serviceAccounts"): (409, {"error": {"message": "exists"}})})  # a retry
    deployer._sessions[None] = adc
    deployer.ensure_link_deployer(WS, sa)
    deployer.ensure_link_deployer(WS, sa)  # cached: no second call
    calls = [(m, u.split("/v1/")[-1], b) for m, u, b, _ in adc.calls]
    # One create, nothing else: no getIamPolicy/setIamPolicy, which the API may no longer call (#15).
    assert [c[0:2] for c in calls] == [("POST", "projects/shipcube-links-ks/serviceAccounts")]
    assert calls[0][2]["accountId"] == f"ws-{WS.replace('-', '')[:12]}-deployer"


@pytest.mark.parametrize("bucket,obj", [
    ("shipcube-uploads-ks", f"ws/{OTHER_WS}/uploads/upl_0123456789ab.tgz"),  # another workspace's upload
    ("shipcube-uploads-ks", "uploads/x/upl_0123456789ab.tgz"),  # the pre-isolation layout
    ("shipcube-uploads-ks", f"ws/{WS}/../{OTHER_WS}/uploads/upl_0123456789ab.tgz"),
    ("someone-elses-bucket", f"ws/{WS}/uploads/upl_0123456789ab.tgz"),
])
def test_build_refuses_source_outside_the_workspace(deployer, bucket, obj):
    deployer._sessions[None] = sess = FakeSession({})
    deployer._provisioned.add(WS)
    with pytest.raises(DeployError) as e:
        deployer.build(APP, {"storageSource": {"bucket": bucket, "object": obj}}, "op_1", lambda: False, lambda _: None)
    assert e.value.code == "permission_denied" and sess.calls == []  # nothing was submitted


def test_uploads_are_named_from_the_callers_workspace_only(deployer):
    assert deployer._object(WS, "upl_0123456789ab") == f"ws/{WS}/uploads/upl_0123456789ab.tgz"
    for bad in ("../x", "upl_../../a", f"ws/{OTHER_WS}/uploads/upl_0123456789ab", "upl_ZZ", ""):
        with pytest.raises(DeployError) as e:
            deployer._object(WS, bad)
        assert e.value.code == "invalid_argument"


@pytest.mark.parametrize("image,code", [
    (f"asia-south1-docker.pkg.dev/shipcube-apps-ks/ws-{OTHER_WS.replace('-', '')[:20]}/victim-123456@{DIGEST}", "permission_denied"),
    (f"asia-south1-docker.pkg.dev/shipcube-apps-ks/shipcube/victim-123456@{DIGEST}", "permission_denied"),
    (f"asia-south1-docker.pkg.dev/shipcube-apps-ks/profiler/x@{DIGEST}", "permission_denied"),
    (f"us-docker.pkg.dev/shipcube-apps-ks/ws-{WS.replace('-', '')[:20]}/x@{DIGEST}", "permission_denied"),
    (f"gcr.io/shipcube-control-ks/x@{DIGEST}", "permission_denied"),
    (f"ASIA-SOUTH1-DOCKER.PKG.DEV/SHIPCUBE-APPS-KS/shipcube/x@{DIGEST}", "permission_denied"),
    (f"{REPO}/tracker-abc123:latest", "invalid_argument"),  # own repo, but a movable tag
])
def test_deploy_refuses_images_outside_the_workspace_repo(deployer, image, code):
    deployer._sessions[None] = sess = FakeSession({})
    with pytest.raises(DeployError) as e:
        deployer.deploy(APP, image, {}, {})
    assert e.value.code == code and sess.calls == []  # Cloud Run was never called
    with pytest.raises(DeployError):
        deployer.stage_source(APP, {"kind": "image", "uri": image}, "op_1")


def test_update_caps_refuses_a_service_still_on_a_shared_repo_tag(deployer):
    svc = {"template": {"containers": [{"image": "asia-south1-docker.pkg.dev/shipcube-apps-ks/shipcube/x:op-1",
                                        "resources": {"limits": {}}}]}}
    deployer._sessions[None] = sess = FakeSession({("GET", "/services/"): (200, svc)})
    with pytest.raises(DeployError) as e:
        deployer.update_caps(APP, {"max_instances": 1})
    assert e.value.code == "permission_denied" and not any(c[0] == "PATCH" for c in sess.calls)


def test_public_and_linked_project_images_are_the_users_choice(deployer):
    from shipcube_api.deploy import isolation

    for image in ("nginx:1.27", "us-docker.pkg.dev/cloudrun/container/hello", "europe-docker.pkg.dev/user-proj/r/app:v1"):
        isolation.check_image(deployer.s, WS, image)


def test_deploy_is_private_gen2_scale_to_zero_with_iap(deployer):
    sess = FakeSession({
        ("PATCH", "/services/"): (200, {"name": "operations/1", "done": True,
                                        "response": {"uri": "https://x.run.app", "latestReadyRevision": "a/b/rev-1"}}),
        ("GET", ":getIamPolicy"): (200, {"bindings": []}),
    })
    deployer._sessions[None] = sess
    out = deployer.deploy(APP, f"{REPO}/{APP.service}@{DIGEST}", {"max_instances": 2, "concurrency": 40, "cpu": "1", "memory": "512Mi", "timeout_s": 60}, {})
    assert out.url == "https://x.run.app" and out.revision == "rev-1"
    sa_create = next(c for c in sess.calls if c[0] == "POST" and c[1].endswith("/serviceAccounts"))
    assert sa_create[2]["accountId"] == APP.runtime_sa.split("@")[0]
    patch = next(c for c in sess.calls if c[0] == "PATCH")
    svc = patch[2]
    assert svc["iapEnabled"] is True and svc["invokerIamDisabled"] is False
    t = svc["template"]
    assert t["executionEnvironment"] == "EXECUTION_ENVIRONMENT_GEN2" and t["serviceAccount"] == APP.runtime_sa
    assert t["scaling"] == {"minInstanceCount": 0, "maxInstanceCount": 2} and t["maxInstanceRequestConcurrency"] == 40
    assert t["containers"][0]["resources"]["cpuIdle"] is True and t["timeout"] == "60s"
    invoker = next(c for c in sess.calls if c[1].endswith(":setIamPolicy"))
    assert {"role": "roles/run.invoker",
            "members": ["serviceAccount:service-330999971585@gcp-sa-iap.iam.gserviceaccount.com"]} in invoker[2]["policy"]["bindings"]


def test_set_access_replaces_iap_accessors_only(deployer):
    sess = FakeSession({("POST", ":getIamPolicy"): (200, {"etag": "e", "bindings": [
        {"role": "roles/iap.httpsResourceAccessor", "members": ["user:old@x.com"]},
        {"role": "roles/other", "members": ["user:keep@x.com"]}]})})
    deployer._sessions[None] = sess
    deployer.set_access(APP, ["user:owner@x.com", "domain:x.com"])
    method, url, body, _ = sess.calls[-1]
    assert url.endswith(f"/projects/330999971585/iap_web/cloud_run-asia-south1/services/{APP.service}:setIamPolicy")
    assert body["policy"]["etag"] == "e"
    assert {"role": "roles/iap.httpsResourceAccessor", "members": ["domain:x.com", "user:owner@x.com"]} in body["policy"]["bindings"]
    assert {"role": "roles/other", "members": ["user:keep@x.com"]} in body["policy"]["bindings"]


def test_linked_target_impersonates_the_workspace_deployer(deployer, monkeypatch):
    made = []
    monkeypatch.setattr(gcp.impersonated_credentials, "Credentials", lambda **kw: made.append(kw) or object())
    monkeypatch.setattr(gcp, "AuthorizedSession", lambda creds: FakeSession({}))
    linked = AppRef("tracker", "ws-1", Target("linked-1", "linked", "user-proj", deployer_sa="ws-1-deployer@c.iam.gserviceaccount.com",
                                              shared_runtime_sa="shipcube-apps@user-proj.iam.gserviceaccount.com"), "asia-south1")
    deployer._for(linked)
    assert made[0]["target_principal"] == "ws-1-deployer@c.iam.gserviceaccount.com"
    assert linked.runtime_sa == "shipcube-apps@user-proj.iam.gserviceaccount.com"


def test_soft_delete_blocks_ingress_and_access(deployer):
    sess = FakeSession({("POST", ":getIamPolicy"): (200, {"bindings": [{"role": "roles/iap.httpsResourceAccessor", "members": ["user:a@x.com"]}]}),
                        ("PATCH", "/services/"): (200, {"name": "operations/2", "done": True, "response": {}})})
    deployer._sessions[None] = sess
    deployer.soft_delete(APP)
    iam = next(c for c in sess.calls if c[1].endswith(":setIamPolicy"))
    assert iam[2]["policy"]["bindings"] == []
    patch = next(c for c in sess.calls if c[0] == "PATCH")
    assert patch[2] == {"ingress": "INGRESS_TRAFFIC_INTERNAL_ONLY"} and patch[3] == {"updateMask": "ingress"}
