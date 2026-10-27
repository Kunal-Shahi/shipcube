"""GcpDeployer request shapes, against a fake HTTP session (no network, no credentials)."""

import json

import pytest

from shipcube_api.deploy import gcp
from shipcube_api.deploy.base import AppRef, Target, normalize_caps
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


APP = AppRef(name="tracker", workspace_id="ws-1234", target=Target("shipcube", "shipcube", "shipcube-apps-ks"), region="asia-south1")


def test_names_follow_rules():
    assert APP.service.startswith("tracker-") and len(APP.service) <= 49
    sa = APP.runtime_sa
    assert sa.startswith("app-") and sa.endswith("@shipcube-apps-ks.iam.gserviceaccount.com")
    assert 6 <= len(sa.split("@")[0]) <= 30


def test_caps_follow_cloud_run_rules():
    assert normalize_caps({"cpu": "0.5", "concurrency": 40, "memory": "256Mi"}) == {"cpu": "0.5", "concurrency": 1, "memory": "512Mi"}


def test_build_uses_buildpacks_as_builder_sa(deployer):
    sess = FakeSession({
        ("POST", "/builds"): (200, {"metadata": {"build": {"id": "b1"}}}),
        ("GET", "/builds/b1"): (200, {"status": "SUCCESS"}),
    })
    deployer._sessions[None] = sess
    seen = []
    image = deployer.build(APP, {"storageSource": {"bucket": "b", "object": "o"}}, "op_1", lambda: False, seen.append)
    assert seen == ["b1"] and image == f"asia-south1-docker.pkg.dev/shipcube-apps-ks/shipcube/{APP.service}:op-1"
    body = sess.calls[0][2]
    step = body["steps"][0]
    assert step["name"] == "gcr.io/k8s-skaffold/pack" and "--publish" in step["args"] and image in step["args"]
    assert body["serviceAccount"].endswith("sc-builder@shipcube-apps-ks.iam.gserviceaccount.com")
    assert body["options"]["logging"] == "CLOUD_LOGGING_ONLY"


def test_deploy_is_private_gen2_scale_to_zero_with_iap(deployer):
    sess = FakeSession({
        ("PATCH", "/services/"): (200, {"name": "operations/1", "done": True,
                                        "response": {"uri": "https://x.run.app", "latestReadyRevision": "a/b/rev-1"}}),
        ("GET", ":getIamPolicy"): (200, {"bindings": []}),
    })
    deployer._sessions[None] = sess
    out = deployer.deploy(APP, "img:1", {"max_instances": 2, "concurrency": 40, "cpu": "1", "memory": "512Mi", "timeout_s": 60}, {})
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
