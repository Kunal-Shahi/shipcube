"""Git sources are shallow and capped: size, per-file size, time, no submodules, no LFS,
no local paths from tenants (docs/research/profiler-sandbox.md §5)."""

import os
import subprocess
from pathlib import Path

import pytest

import shipcube_profiler.sources as sources
from shipcube_profiler import SourceError, materialize

ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                           "-c", "protocol.file.allow=always", *args],
                          check=True, capture_output=True, text=True, env=ENV).stdout


def make_repo(path: Path, files: dict[str, bytes], commits: int = 1) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True, env=ENV)
    git(path, "config", "uploadpack.allowFilter", "true")
    for i in range(commits):
        for rel, data in files.items():
            (path / rel).parent.mkdir(parents=True, exist_ok=True)
            (path / rel).write_bytes(data + str(i).encode())
        git(path, "add", "-A")
        git(path, "commit", "-qm", f"c{i}")
        git(path, "tag", f"v{i}")
    return path


def clone(repo: Path, dest: Path, **src) -> Path:
    return materialize({"kind": "git", "url": f"file://{repo}", **src}, dest, allow_file_urls=True)


def test_tenants_cannot_clone_local_paths(tmp_path):
    repo = make_repo(tmp_path / "r", {"main.py": b"print(1)\n"})
    for url in (f"file://{repo}", str(repo), "ssh://git@example.com/x.git", "git@example.com:x.git",
                "git://example.com/x", "ext::sh -c id", "fd::3", "http://example.com/x.git", "HTTPS://x/../etc"):
        with pytest.raises(SourceError):
            materialize({"kind": "git", "url": url}, tmp_path / "out")


def test_transport_allowlist_holds_even_past_the_url_check(tmp_path):
    """GIT_ALLOW_PROTOCOL=https blocks local transports at git's level too."""
    repo = make_repo(tmp_path / "r", {"main.py": b"print(1)\n"})
    with pytest.raises(SourceError, match="clone failed"):
        sources._clone(f"file://{repo}", None, tmp_path / "out" / "repo", allow_file=False)


def test_clone_is_shallow_single_branch_without_tags(tmp_path):
    repo = make_repo(tmp_path / "r", {"main.py": b"print(1)\n"}, commits=3)
    git(repo, "branch", "other")
    root = clone(repo, tmp_path / "out")
    assert git(root, "rev-list", "--count", "HEAD").strip() == "1"
    assert git(root, "tag").strip() == ""
    assert "other" not in git(root, "branch", "-a")


def test_clone_over_the_size_cap_is_aborted_and_removed(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "r", {"main.py": b"print(1)\n", "data.bin": os.urandom(3 * 2**20)})
    monkeypatch.setattr(sources, "GIT_MAX_BYTES", 2**20)
    with pytest.raises(SourceError, match="over"):
        clone(repo, tmp_path / "out")
    assert not (tmp_path / "out" / "repo").exists()


def test_file_over_the_per_file_limit_is_not_fetched(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "r", {"main.py": b"print(1)\n", "big.bin": os.urandom(2 * 2**20)})
    monkeypatch.setattr(sources, "GIT_MAX_BLOB_BYTES", 2**20)
    with pytest.raises(SourceError, match="per-file limit"):
        clone(repo, tmp_path / "out")
    assert not (tmp_path / "out" / "repo").exists()


def test_slow_clone_times_out(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "r", {"main.py": b"print(1)\n"})
    monkeypatch.setattr(sources, "GIT_CLONE_TIMEOUT_S", 0.0)
    with pytest.raises(SourceError, match="longer than"):
        clone(repo, tmp_path / "out")


def test_submodules_are_not_fetched(tmp_path):
    sub = make_repo(tmp_path / "sub", {"secret.py": b"x = 1\n"})
    repo = make_repo(tmp_path / "r", {"main.py": b"print(1)\n"})
    git(repo, "submodule", "add", "-q", f"file://{sub}", "vendored")
    git(repo, "commit", "-qm", "sub")
    root = clone(repo, tmp_path / "out")
    assert (root / "main.py").exists()
    assert not (root / "vendored" / "secret.py").exists()


def test_lfs_files_stay_pointers(tmp_path, monkeypatch):
    pointer = b"version https://git-lfs.github.com/spec/v1\noid sha256:" + b"a" * 64 + b"\nsize 999999999\n"
    repo = make_repo(tmp_path / "r", {"main.py": b"print(1)\n", ".gitattributes": b"*.bin filter=lfs diff=lfs merge=lfs -text\n"})
    (repo / "model.bin").write_bytes(pointer)
    git(repo, "-c", "filter.lfs.clean=cat", "add", "model.bin")
    git(repo, "commit", "-qm", "lfs")
    # The worker's own git config (here: an LFS smudge filter that would fetch) is ignored.
    gc = tmp_path / "gitconfig"
    gc.write_text("[filter \"lfs\"]\n\tsmudge = sh -c 'echo SMUDGED; exit 1'\n\trequired = true\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gc))
    root = clone(repo, tmp_path / "out")
    assert (root / "model.bin").read_bytes() == pointer


def test_clone_command_flags(tmp_path, monkeypatch):
    seen = {}

    class Fake:
        def __init__(self, cmd, env, **kw):
            seen["cmd"], seen["env"] = cmd, env
            self.returncode, self.pid, self.stderr = 1, 0, None

        def poll(self):
            return 1

    monkeypatch.setattr(sources.subprocess, "Popen", Fake)
    with pytest.raises(SourceError):
        materialize({"kind": "git", "url": "https://example.com/x.git", "ref": "main"}, tmp_path / "o")
    cmd, env = seen["cmd"], seen["env"]
    for flag in ("--depth", "--single-branch", "--no-tags", "--no-recurse-submodules", "protocol.allow=never",
                 "protocol.https.allow=always", "submodule.recurse=false", "filter.lfs.smudge="):
        assert flag in cmd, flag
    assert any(a.startswith("--filter=blob:limit=") for a in cmd)
    assert "protocol.file.allow=always" not in cmd
    assert cmd[-3:] == ["--", "https://example.com/x.git", str(tmp_path / "o" / "repo")]
    assert env["GIT_LFS_SKIP_SMUDGE"] == "1" and env["GIT_NO_LAZY_FETCH"] == "1"
    assert env["GIT_CONFIG_NOSYSTEM"] == "1" and env["GIT_CONFIG_GLOBAL"] == os.devnull
    assert env["GIT_ALLOW_PROTOCOL"] == "https"
    assert "META_API_KEY" not in env and "GOOGLE_APPLICATION_CREDENTIALS" not in env
