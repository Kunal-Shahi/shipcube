"""Tenant git URLs are cloned safely (git_source.py, mirroring the profiler's #57 clone): https only,
shallow, one branch, no submodules, size and time caps. Uses local repositories through file://,
which only tests may allow."""

import subprocess

import pytest

from shipcube_api import git_source
from shipcube_api.deploy.base import DeployError

from .conftest import new_workspace


def _git(*args, cwd):
    subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t", "-c", "protocol.file.allow=always",
                    *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "origin"
    r.mkdir()
    _git("init", "-q", "-b", "main", cwd=r)
    for i in range(3):
        (r / "app.py").write_text(f"print({i})\n")
        _git("add", ".", cwd=r)
        _git("commit", "-q", "-m", f"c{i}", cwd=r)
    _git("tag", "v1", cwd=r)
    return r


def test_a_clone_is_shallow_one_branch_and_no_tags(repo, tmp_path):
    out = git_source.clone(f"file://{repo}", "main", tmp_path / "c", allow_file=True)
    assert (out / "app.py").read_text() == "print(2)\n"
    log = subprocess.run(["git", "-C", str(out), "log", "--oneline"], capture_output=True, text=True).stdout
    assert len(log.splitlines()) == 1
    assert subprocess.run(["git", "-C", str(out), "tag"], capture_output=True, text=True).stdout == ""


@pytest.mark.parametrize("url", ["file:///etc", "ssh://git@github.com/x/y", "git://github.com/x/y",
                                 "git@github.com:x/y.git", "/srv/repo", "-uhttps://x"])
def test_only_https_urls_are_cloned(tmp_path, url):
    with pytest.raises(DeployError, match="https://") as e:
        git_source.clone(url, None, tmp_path / "c")
    assert e.value.code == "invalid_argument" and not (tmp_path / "c").exists()


def test_a_clone_over_the_size_cap_is_refused_and_removed(repo, tmp_path):
    (repo / "big.bin").write_bytes(b"\x01" * (2 << 20))
    _git("add", ".", cwd=repo)
    _git("commit", "-q", "-m", "big", cwd=repo)
    target = tmp_path / "c"
    with pytest.raises(DeployError, match="over 1 MiB"):
        git_source.clone(f"file://{repo}", None, target, allow_file=True, max_bytes=1 << 20)
    assert not target.exists()


def test_a_clone_past_its_time_limit_is_killed(repo, tmp_path):
    with pytest.raises(DeployError, match="longer than"):
        git_source.clone(f"file://{repo}", None, tmp_path / "c", allow_file=True, timeout_s=0)
    assert not (tmp_path / "c").exists()


def test_submodules_are_not_fetched(repo, tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    _git("init", "-q", "-b", "main", cwd=parent)
    _git("submodule", "add", "-q", f"file://{repo}", "vendored", cwd=parent)
    _git("commit", "-q", "-m", "with submodule", cwd=parent)
    out = git_source.clone(f"file://{parent}", None, tmp_path / "c", allow_file=True)
    assert (out / ".gitmodules").exists()
    assert not any((out / "vendored").iterdir())  # declared, never cloned


@pytest.mark.parametrize("source", [
    {"kind": "git", "url": "ssh://git@github.com/x/y"},
    {"kind": "git", "url": "file:///etc"},
    {"kind": "git", "url": "https://github.com/x/y", "ref": "--upload-pack=touch /tmp/x"},
    {"kind": "git", "url": "https://github.com/x/y", "subdir": "../../etc"},
    {"kind": "git", "url": "https://github.com/x/y", "subdir": "/etc"},
])
def test_the_api_refuses_unsafe_git_sources(client, source):
    ws = new_workspace()
    for path, body in [("/v1/estimates", {"source": source}), ("/v1/apps/git-app/deployments", {"source": source})]:
        r = client.post(path, json=body, headers=ws["agent"])
        assert r.status_code == 400, (path, r.text)
