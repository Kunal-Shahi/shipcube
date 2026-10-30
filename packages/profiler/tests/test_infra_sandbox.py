"""The sandbox's Terraform guarantees, checked in CI: no egress path from the profiler
network, zero bindings for its runtime SA (infra/tests/profiler_sandbox_check.py), plus
the module's plan-only `terraform test` where terraform is installed."""

import importlib.util
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
TF = REPO / "infra" / "terraform"
spec = importlib.util.spec_from_file_location("profiler_sandbox_check", REPO / "infra/tests/profiler_sandbox_check.py")
chk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chk)


def test_current_terraform_passes():
    assert chk.check(TF) == []


def _with(tmp_path: Path, extra: str = "", sub: tuple[str, str] | None = None) -> list[str]:
    root = tmp_path / "terraform"
    shutil.copytree(TF, root, ignore=shutil.ignore_patterns(".terraform*"))
    main = root / "modules/profiler/main.tf"
    if sub:
        f = next(f for f in (root / "modules/profiler").glob("*.tf") if sub[0] in f.read_text())
        f.write_text(f.read_text().replace(sub[0], sub[1]))
    main.write_text(main.read_text() + extra)
    return chk.check(root)


BREAKS = {
    "allow rule": '\nresource "google_compute_firewall" "open" {\n  network = google_compute_network.sandbox.name\n'
                  '  direction = "EGRESS"\n  allow {\n    protocol = "tcp"\n  }\n}\n',
    "nat": '\nresource "google_compute_router" "r" {\n  network = google_compute_network.sandbox.id\n}\n'
           'resource "google_compute_router_nat" "n" {\n  router = google_compute_router.r.name\n}\n',
    "connector": '\nresource "google_vpc_access_connector" "c" {\n  network = "profiler-no-egress"\n}\n',
    "peering": '\nresource "google_compute_network_peering" "p" {\n  network = google_compute_network.sandbox.self_link\n}\n',
    "project role": '\nresource "google_project_iam_member" "x" {\n  role   = "roles/viewer"\n'
                    '  member = google_service_account.sandbox.member\n}\n',
    "bucket role": '\nresource "google_storage_bucket_iam_member" "x" {\n  role    = "roles/storage.objectViewer"\n'
                   '  member  = "serviceAccount:profiler-sandbox@p.iam.gserviceaccount.com"\n}\n',
    "nat on the build network": '\nresource "google_compute_router" "b" {\n  network = google_compute_network.build[0].id\n}\n',
    "token creator on itself": '\nresource "google_service_account_iam_member" "x" {\n'
                               '  service_account_id = google_service_account.sandbox.name\n'
                               '  role = "roles/iam.serviceAccountTokenCreator"\n  member = "user:a@b.c"\n}\n',
}


@pytest.mark.parametrize("name", BREAKS)
def test_check_catches(tmp_path, name):
    assert _with(tmp_path, BREAKS[name]), name


@pytest.mark.parametrize("sub", [
    ('private_ip_google_access = false', 'private_ip_google_access = true'),
    ('  deny {\n    protocol = "all"\n  }', '  allow {\n    protocol = "all"\n  }'),
    ('destination_ranges = ["0.0.0.0/0"]', 'destination_ranges = ["10.0.0.0/8"]'),
    ('no_external_ip = true', 'no_external_ip = false'),
])
def test_check_catches_weakened_rules(tmp_path, sub):
    assert _with(tmp_path, sub=sub)


@pytest.mark.skipif(not shutil.which("terraform"), reason="needs terraform")
def test_module_terraform_test(tmp_path):
    mod = tmp_path / "profiler"
    shutil.copytree(TF / "modules/profiler", mod, ignore=shutil.ignore_patterns(".terraform*"))
    init = subprocess.run(["terraform", "init", "-backend=false", "-input=false"], cwd=mod, capture_output=True, text=True)
    if init.returncode != 0:
        pytest.skip(f"terraform init failed (offline?): {init.stderr[-200:]}")
    r = subprocess.run(["terraform", "test", "-no-color"], cwd=mod, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]


UPLOADS_BREAKS = {
    "bucket-wide admin": ('roles/storage.objectViewer", "roles/storage.objectCreator"',
                          'roles/storage.objectViewer", "roles/storage.objectAdmin"'),
    "condition dropped": None,
    "segment check dropped": (' &&\n      resource.name.extract("${local.uploads_objects}/ws/{ws}/profiler/") == '
                              'resource.name.extract("${local.uploads_objects}/ws/{ws}/")', ""),
    "regex reintroduced": ('resource.name.startsWith("${local.uploads_objects}/ws/") &&',
                           'resource.name.matches("^${local.uploads_objects}/ws/[^/]+/profiler/") &&'),
    "contains used": ('!= ""', '!= "" && !resource.name.contains("..")'),
    "whole bucket": ('startsWith("${local.uploads_objects}/ws/")', 'startsWith("${local.uploads_objects}/")'),
    "other bucket": ('"projects/_/buckets/${google_storage_bucket.uploads.name}/objects"',
                     '"projects/_/buckets/other/objects"'),
    "extra permission": ('permissions = ["storage.objects.delete"]',
                         'permissions = ["storage.objects.delete", "storage.objects.create"]'),
}


@pytest.mark.parametrize("name", UPLOADS_BREAKS)
def test_worker_delete_on_uploads_stays_profiler_only(tmp_path, name):
    root = tmp_path / "terraform"
    shutil.copytree(TF, root, ignore=shutil.ignore_patterns(".terraform*"))
    up = root / "uploads.tf"
    text = up.read_text()
    if UPLOADS_BREAKS[name] is None:   # the janitor grant without its condition block
        broken = re.sub(r"\n  condition \{.*?\n  \}", "", text, count=1, flags=re.S)
    else:
        old, new = UPLOADS_BREAKS[name]
        broken = text.replace(old, new)
    assert broken != text, name
    up.write_text(broken)
    assert chk.check(root), name


# --- what the janitor condition allows, under IAM's extract() -----------------------------

def _extract(name: str, template: str, shortest: bool = True) -> str:
    """IAM's resource.name.extract(): the text where {placeholder} sits, or "" if the template
    doesn't match. `shortest` is the documented first-occurrence reading; the other reading is
    checked too, to show the condition is never broader under either."""
    prefix, _, rest = template.partition("{ws}")
    if not name.startswith(prefix):
        return ""
    tail = name[len(prefix):]
    if not rest:
        return tail
    i = tail.find(rest) if shortest else tail.rfind(rest)
    return tail[:i] if i >= 0 else ""


def _janitor_allows(name: str, bucket: str = "shipcube-uploads-ks", shortest: bool = True) -> bool:
    o = f"projects/_/buckets/{bucket}/objects"
    seg_profiler = _extract(name, f"{o}/ws/{{ws}}/profiler/", shortest)
    return (name.startswith(f"{o}/ws/") and seg_profiler != ""
            and seg_profiler == _extract(name, f"{o}/ws/{{ws}}/", shortest))


def test_janitor_condition_text_is_the_one_modelled():
    expr = chk._condition_expression((TF / "uploads.tf").read_text().split("uploads_worker_profiler_janitor", 1)[1])
    assert " ".join(expr.split()) == chk.JANITOR_CONDITION


@pytest.mark.parametrize("obj,allowed", [
    ("ws/3c1d5e2a-7b4f-4e8a-9c6d-1234567890ab/profiler/est-1.tar.gz", True),
    ("ws/3c1d/profiler/sub/x.tar.gz", True),                    # anything under that one folder
    ("ws/3c1d/uploads/upl_1.tgz", False),
    ("ws/3c1d/build-logs/log.txt", False),
    ("ws/a/b/profiler/x", False),                               # two segments before profiler/
    ("ws/3c1d/x/profiler/y", False),
    ("ws//profiler/x", False),                                  # empty segment
    ("ws/3c1d/profilerX/y", False),
    ("ws/3c1d/profiler", False),
    ("other/3c1d/profiler/x", False),
    ("profiler/x", False),
])
def test_janitor_condition_allows_exactly_one_ws_segment_then_profiler(obj, allowed):
    name = f"projects/_/buckets/shipcube-uploads-ks/objects/{obj}"
    assert _janitor_allows(name) is allowed, obj
    if not allowed:                                            # never broader under the other reading
        assert _janitor_allows(name, shortest=False) is False, obj
    assert _janitor_allows(name.replace("shipcube-uploads-ks", "other-bucket")) is False
