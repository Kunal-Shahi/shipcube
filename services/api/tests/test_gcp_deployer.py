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
        ("GET", ":getIamPolicy"): lambda _: (200, {"bindings": []}),  # a fresh policy per resource
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
    assert len(prov.calls) == 18  # SA, repo + IAM get/set, 2 folders + IAM get/put each, SA IAM get/set, 2 mirrors x describe + IAM get/set
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


PIN = "@sha256:" + "a" * 64


@pytest.mark.parametrize("image", [
    "nginx:1.27", "nginx" + PIN, "docker.io/xmrig/xmrig:latest", "ghcr.io/evil/miner" + PIN,
    "europe-docker.pkg.dev/user-proj/r/app:v1", "europe-docker.pkg.dev/user-proj/r/app" + PIN,
    "us-docker.pkg.dev/cloudrun/containerx/hello" + PIN,          # a prefix look-alike
])
def test_hosted_apps_refuse_public_and_foreign_images(deployer, image):
    """Red-team A-1 (#21): a hosted app runs on ShipCube's credit, so no arbitrary images."""
    from shipcube_api.deploy import isolation

    with pytest.raises(DeployError) as e:
        isolation.check_image(deployer.s, WS, image)
    assert e.value.code == "permission_denied"


def test_hosted_apps_may_run_allowlisted_images_pinned_by_digest(deployer, monkeypatch):
    from shipcube_api.deploy import isolation

    isolation.check_image(deployer.s, WS, "us-docker.pkg.dev/cloudrun/container/hello" + PIN)
    with pytest.raises(DeployError) as e:                           # a tag can be moved; a digest can't
        isolation.check_image(deployer.s, WS, "us-docker.pkg.dev/cloudrun/container/hello")
    assert e.value.code == "invalid_argument"
    monkeypatch.setenv("SHIPCUBE_IMAGE_ALLOWLIST", "europe-docker.pkg.dev/samples/demo")
    isolation.check_image(deployer.s, WS, "europe-docker.pkg.dev/samples/demo/app" + PIN)
    with pytest.raises(DeployError):                                # the default is replaced, not extended
        isolation.check_image(deployer.s, WS, "us-docker.pkg.dev/cloudrun/container/hello" + PIN)
    monkeypatch.setenv("SHIPCUBE_IMAGE_ALLOWLIST", "")
    with pytest.raises(DeployError):                                # empty: workspace images only
        isolation.check_image(deployer.s, WS, "us-docker.pkg.dev/cloudrun/container/hello" + PIN)


def test_workspace_images_must_be_its_own_and_pinned(deployer):
    from shipcube_api.deploy import isolation

    own = isolation.image_prefix(deployer.s, WS) + "app-123456"
    isolation.check_image(deployer.s, WS, own + PIN)
    for image, code in ((own + ":latest", "invalid_argument"),
                        (isolation.image_prefix(deployer.s, OTHER_WS) + "app-123456" + PIN, "permission_denied"),
                        (own.replace("/app-123456", "/../shared/app") + PIN, "invalid_argument"),
                        (own + PIN + " ", None), (" " + own + PIN, None)):
        if code is None:
            isolation.check_image(deployer.s, WS, image)            # surrounding whitespace is trimmed
            continue
        with pytest.raises(DeployError) as e:
            isolation.check_image(deployer.s, WS, image)
        assert e.value.code == code, image


def test_linked_projects_may_run_any_outside_image_but_never_shipcubes(deployer):
    """Linked apps run in the user's project, on their bill: their choice, as before."""
    from shipcube_api.deploy import isolation

    for image in ("nginx:1.27", "us-docker.pkg.dev/cloudrun/container/hello", "europe-docker.pkg.dev/user-proj/r/app:v1"):
        isolation.check_image(deployer.s, WS, image, linked=True)
    with pytest.raises(DeployError):
        isolation.check_image(deployer.s, WS, isolation.image_prefix(deployer.s, OTHER_WS) + "app" + PIN, linked=True)


def test_deploy_is_private_gen2_scale_to_zero_with_iap(deployer):
    sess = FakeSession({
        ("PATCH", "/services/"): (200, {"name": "operations/1", "done": True,
                                        "response": {"uri": "https://x.run.app", "latestReadyRevision": "a/b/rev-1"}}),
        ("GET", ":getIamPolicy"): (200, {"bindings": []}),
    })
    prov = FakeSession({})
    deployer._sessions[None] = sess
    deployer._sessions[deployer.s.provisioner_sa] = prov
    out = deployer.deploy(APP, f"{REPO}/{APP.service}@{DIGEST}", {"max_instances": 2, "concurrency": 40, "cpu": "1", "memory": "512Mi", "timeout_s": 60}, {})
    assert out.url == "https://x.run.app" and out.revision == "rev-1"
    sa_create = next(c for c in prov.calls if c[0] == "POST" and c[1].endswith("/serviceAccounts"))  # the provisioner's (#16)
    assert sa_create[2]["accountId"] == APP.runtime_sa.split("@")[0]
    assert not any(c[1].endswith("/serviceAccounts") for c in sess.calls)
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


def test_image_hosts_are_normalised_before_matching(deployer, monkeypatch):
    """Red team on #59: `HOST.`, `HOST:443` and upper case are the same registry, so they can't
    dodge the ShipCube-registry rule (even for linked apps) or miss the allow-list."""
    from shipcube_api.deploy import isolation

    other = isolation.image_prefix(deployer.s, OTHER_WS) + "victim-123456" + PIN
    host, _, rest = other.partition("/")
    for spelling in (f"{host}./{rest}", f"{host}:443/{rest}", f"{host.upper()}/{rest}", f"{host}.:443/{rest}",
                     f"{host.upper()}.:443/{rest.upper()}"):
        for linked in (False, True):
            with pytest.raises(DeployError) as e:
                isolation.check_image(deployer.s, WS, spelling, linked=linked)
            assert e.value.code == "permission_denied", (spelling, linked)
    own = isolation.image_prefix(deployer.s, WS) + "app-123456" + PIN
    h, _, r = own.partition("/")
    for spelling in (f"{h}./{r}", f"{h}:443/{r}", f"{h.upper()}/{r}"):
        isolation.check_image(deployer.s, WS, spelling)
    for spelling in ("US-DOCKER.PKG.DEV/cloudrun/container/hello", "us-docker.pkg.dev.:443/cloudrun/container/hello",
                     "us-docker.pkg.dev:443/CloudRun/Container/hello"):
        isolation.check_image(deployer.s, WS, spelling + PIN)
    with pytest.raises(DeployError):                     # another port is another registry
        isolation.check_image(deployer.s, WS, "us-docker.pkg.dev:8443/cloudrun/container/hello" + PIN)


def test_docker_hub_aliases_match_one_allowlist_entry(deployer, monkeypatch):
    from shipcube_api.deploy import isolation

    monkeypatch.setenv("SHIPCUBE_IMAGE_ALLOWLIST", "nginx")
    for spelling in ("nginx", "docker.io/nginx", "docker.io/library/nginx", "index.docker.io/library/nginx",
                     "registry-1.docker.io/library/nginx", "Docker.IO./library/nginx", "registry.hub.docker.com/nginx"):
        isolation.check_image(deployer.s, WS, spelling + PIN)
    for spelling in ("nginxx", "evil/nginx", "ghcr.io/library/nginx", "docker.io.evil.com/library/nginx"):
        with pytest.raises(DeployError):
            isolation.check_image(deployer.s, WS, spelling + PIN)
    assert isolation.canonical_image("nginx:1.27") == "docker.io/library/nginx:1.27"


@pytest.mark.parametrize("image", ["us-docker.pkg.dev\\cloudrun/container/hello", "us-docker.pkg.dev/cloudrun/%2e%2e/x",
                                   "us-docker.pkg.dev/cloudrun/container/hello?x=1", "user@us-docker.pkg.dev/x/y",
                                   "us-docker.pkg.dev:abc/x/y", "./x", "", "   "])
def test_malformed_image_references_are_refused(deployer, image):
    from shipcube_api.deploy import isolation

    with pytest.raises(DeployError) as e:
        isolation.check_image(deployer.s, WS, image + (PIN if image.strip() else ""))
    assert e.value.code == "invalid_argument", image


def test_ensure_builder_grants_read_on_the_profiler_mirrors_that_exist(deployer):
    short = WS.replace("-", "")[:20]
    member = f"serviceAccount:sc-build-{short}@shipcube-apps-ks.iam.gserviceaccount.com"
    prov = FakeSession({
        ("GET", "repositories/dockerhub-remote:getIamPolicy"): lambda _: (200, {"bindings": []}),
        ("GET", "repositories/dockerhub-remote"): (404, {"error": {"message": "not found"}}),  # offline_builds off
        ("GET", ":getIamPolicy"): lambda _: (200, {"bindings": []}),
        ("POST", ":getIamPolicy"): (200, {"etag": "e"}),
        ("GET", "/iam"): lambda _: (200, {"bindings": []}),
    })
    deployer._sessions[deployer.s.provisioner_sa] = prov
    deployer.ensure_builder(WS)
    sets = {c[1].split("/repositories/")[-1]: c[2] for c in prov.calls if c[1].endswith(":setIamPolicy") and "/repositories/" in c[1]}
    assert sets["pypi-remote:setIamPolicy"]["policy"]["bindings"] == [{"role": "roles/artifactregistry.reader", "members": [member]}]
    assert "dockerhub-remote:setIamPolicy" not in sets  # skipped: the mirror doesn't exist


# --- #16: the hosted deployer may act only as each app's own runtime SA ---------------------------

HOSTED_DEPLOYER = "sc-hosted-deployer@shipcube-control-ks.iam.gserviceaccount.com"


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.setattr(gcp.google.auth, "default", lambda scopes: (object(), "p"))
    d = gcp.GcpDeployer(Settings(hosted_deployer_sa=HOSTED_DEPLOYER))
    d._project_numbers["shipcube-apps-ks"] = "330999971585"
    d._iap_agents["shipcube-apps-ks"] = "service-330999971585@gcp-sa-iap.iam.gserviceaccount.com"
    return d


def test_hosted_runtime_sa_grants_the_deployer_act_as_on_that_sa_only(hosted):
    prov = FakeSession({("POST", ":getIamPolicy"): (200, {"etag": "e", "bindings": []})})
    dep = FakeSession({
        ("PATCH", "/services/"): (200, {"name": "operations/1", "done": True, "response": {"uri": "https://x.run.app"}}),
        ("GET", ":getIamPolicy"): (200, {"bindings": []}),
    })
    hosted._sessions[hosted.s.provisioner_sa] = prov
    hosted._sessions[HOSTED_DEPLOYER] = dep
    hosted.deploy(APP, f"{REPO}/{APP.service}@{DIGEST}", {}, {})
    sa_url = f"projects/shipcube-apps-ks/serviceAccounts/{APP.runtime_sa}"
    calls = [(m, u.split("/v1/")[-1], b) for m, u, b, _ in prov.calls]
    assert calls[0][0:2] == ("POST", "projects/shipcube-apps-ks/serviceAccounts")
    set_policy = next(c for c in calls if c[1] == f"{sa_url}:setIamPolicy")
    assert set_policy[2]["policy"] == {"etag": "e", "bindings": [
        {"role": "roles/iam.serviceAccountUser", "members": [f"serviceAccount:{HOSTED_DEPLOYER}"]}]}
    # The deployer itself never creates SAs or edits their IAM.
    assert not any("/serviceAccounts" in u for _, u, _, _ in dep.calls)


def test_hosted_runtime_sa_binding_is_idempotent(hosted):
    prov = FakeSession({("POST", ":getIamPolicy"): (200, {"etag": "e", "bindings": [
        {"role": "roles/iam.serviceAccountUser", "members": [f"serviceAccount:{HOSTED_DEPLOYER}"]}]})})
    hosted._sessions[hosted.s.provisioner_sa] = prov
    assert hosted._ensure_runtime_sa(FakeSession({}), APP) == APP.runtime_sa
    assert not any(u.endswith(":setIamPolicy") for _, u, _, _ in prov.calls)


def test_purge_deletes_the_hosted_runtime_sa_as_the_provisioner(hosted):
    prov = FakeSession({})
    dep = FakeSession({("DELETE", "/services/"): (200, {"name": "operations/1", "done": True}),
                       ("DELETE", "/packages/"): (200, {"name": "operations/2", "done": True})})
    hosted._sessions[hosted.s.provisioner_sa] = prov
    hosted._sessions[HOSTED_DEPLOYER] = dep
    hosted.purge(APP)
    assert ("DELETE", f"https://iam.googleapis.com/v1/projects/shipcube-apps-ks/serviceAccounts/{APP.runtime_sa}") in [
        (m, u) for m, u, _, _ in prov.calls]
    assert not any("/serviceAccounts/" in u for _, u, _, _ in dep.calls)


def test_retry_act_as_waits_for_a_new_binding_only(monkeypatch):
    monkeypatch.setattr(gcp.time, "sleep", lambda s: None)
    tries = []

    def flaky():
        tries.append(1)
        if len(tries) < 3:
            raise DeployError("permission_denied", "Permission 'iam.serviceaccounts.actAs' denied on service account app-x")
        return "ok"

    assert gcp.GcpDeployer._retry_act_as(flaky) == "ok" and len(tries) == 3

    def other():
        tries.append(1)
        raise DeployError("permission_denied", "Permission 'run.services.update' denied")

    tries.clear()
    with pytest.raises(DeployError):
        gcp.GcpDeployer._retry_act_as(other)
    assert len(tries) == 1


def test_public_services_finds_all_users_bindings(hosted):
    base = "/projects/shipcube-apps-ks/locations/asia-south1/services"
    adc = FakeSession({
        ("GET", "a:getIamPolicy"): (200, {"bindings": [{"role": "roles/run.invoker", "members": ["allUsers"]}]}),
        ("GET", "b:getIamPolicy"): (200, {"bindings": [{"role": "roles/run.invoker", "members": ["serviceAccount:iap@x"]}]}),
        ("GET", base): (200, {"services": [{"name": f"projects/shipcube-apps-ks/locations/asia-south1/services/a"},
                                           {"name": f"projects/shipcube-apps-ks/locations/asia-south1/services/b"}]}),
    })
    hosted._sessions[None] = adc
    assert hosted.public_services(["asia-south1"]) == (["projects/shipcube-apps-ks/locations/asia-south1/services/a"], 2)
