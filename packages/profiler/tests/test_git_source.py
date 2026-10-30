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
    with pytest.raises(SourceError, match="1 MiB"):   # the tree listing, or the watchdog if it fires first
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
            import io
            self.returncode, self.pid, self.stderr, self.stdout = 1, 0, None, io.BytesIO(b"")

        def poll(self):
            return 1

    monkeypatch.setattr(sources.subprocess, "Popen", Fake)
    with pytest.raises(SourceError):
        materialize({"kind": "git", "url": "https://example.com/x.git", "ref": "main"}, tmp_path / "o")
    cmd, env = seen["cmd"], seen["env"]
    for flag in ("--no-checkout", "core.symlinks=false", "--depth", "--single-branch", "--no-tags",
                 "--no-recurse-submodules", "protocol.allow=never",
                 "protocol.https.allow=always", "submodule.recurse=false", "filter.lfs.smudge="):
        assert flag in cmd, flag
    assert any(a.startswith("--filter=blob:limit=") for a in cmd)
    assert "protocol.file.allow=always" not in cmd
    assert cmd[-3:] == ["--", "https://example.com/x.git", str(tmp_path / "o" / "repo")]
    assert env["GIT_LFS_SKIP_SMUDGE"] == "1" and env["GIT_NO_LAZY_FETCH"] == "1"
    assert env["GIT_CONFIG_NOSYSTEM"] == "1" and env["GIT_CONFIG_GLOBAL"] == os.devnull
    assert env["GIT_ALLOW_PROTOCOL"] == "https"
    assert "META_API_KEY" not in env and "GOOGLE_APPLICATION_CREDENTIALS" not in env


def test_symlinks_are_checked_out_as_plain_files(tmp_path):
    """core.symlinks=false: a link becomes a small file holding its target path, so
    nothing that reads the tree can be led outside it."""
    repo = make_repo(tmp_path / "r", {"lib/real.py": b"x = 1\n"})
    (repo / "main.py").symlink_to("/etc/passwd")
    (repo / "up").symlink_to("../../..")
    (repo / "libdir").symlink_to("lib")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "links")
    root = clone(repo, tmp_path / "out")
    for name, target in (("main.py", "/etc/passwd"), ("up", "../../.."), ("libdir", "lib")):
        assert not (root / name).is_symlink() and (root / name).read_text() == target, name
    assert (root / "lib" / "real.py").is_file()
    with pytest.raises(SourceError):                 # a subdir that was a link isn't a directory
        clone(repo, tmp_path / "out2", subdir="libdir")
    with pytest.raises(SourceError):
        clone(repo, tmp_path / "out3", subdir="up")


def test_any_link_left_after_checkout_is_refused(tmp_path):
    (tmp_path / "a").write_text("x")
    (tmp_path / "b").symlink_to("a")
    with pytest.raises(SourceError, match="symlink"):
        sources._reject_symlinks(tmp_path)


def test_subdir_must_resolve_inside_without_links(tmp_path):
    root = tmp_path / "checkout"
    (root / "svc").mkdir(parents=True)
    (root / "out").symlink_to("/")
    (root / "inner").symlink_to("svc")
    assert sources._inside(root, "svc") == root / "svc"
    for bad in ("out", "out/etc", "inner", "../x", "/etc", "svc/../../x"):
        with pytest.raises(SourceError):
            sources._inside(root, bad)


def test_exact_size_check_catches_repeated_blobs_before_checkout(tmp_path, monkeypatch):
    """Ten copies of one zero-filled 1 MiB blob: .git stays tiny, the checkout is 10 MiB.
    The tree listing refuses it before anything is written to the work tree."""
    zeros = b"\0" * 2**20
    repo = make_repo(tmp_path / "r", {"main.py": b"print(1)\n"})
    for i in range(10):
        (repo / f"z{i}.bin").write_bytes(zeros)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "zeros")
    monkeypatch.setattr(sources, "GIT_MAX_BYTES", 5 * 2**20)
    seen = []
    real = sources._run_git
    monkeypatch.setattr(sources, "_run_git", lambda step, *a: seen.append(step) or real(step, *a))
    with pytest.raises(SourceError, match="checkout would be 10 MiB"):
        clone(repo, tmp_path / "out")
    assert seen == ["clone", "ls-tree"]              # never reached the checkout
    assert not (tmp_path / "out" / "repo").exists()


def test_clone_does_not_check_out_before_the_size_check(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "r", {"main.py": b"print(1)\n"})
    seen = []
    real = sources._run_git
    monkeypatch.setattr(sources, "_run_git", lambda step, args, *a: seen.append((step, list(args))) or real(step, args, *a))
    clone(repo, tmp_path / "out")
    assert [st for st, _ in seen] == ["clone", "ls-tree", "checkout"]
    assert "--no-checkout" in seen[0][1] and "core.symlinks=false" in seen[0][1]
    assert all("core.symlinks=false" in args for _, args in seen)


def test_upload_symlinks_are_removed(tmp_path):
    import io
    import tarfile
    tgz = tmp_path / "u.tgz"
    with tarfile.open(tgz, "w:gz") as tar:
        data = b"print(1)\n"
        ti = tarfile.TarInfo("main.py")
        ti.size = len(data)
        tar.addfile(ti, io.BytesIO(data))
        link = tarfile.TarInfo("alias.py")
        link.type, link.linkname = tarfile.SYMTYPE, "main.py"
        tar.addfile(link)
    root = materialize({"kind": "upload", "upload_id": "u"}, tmp_path / "out", fetch_upload=lambda _: tgz)
    assert (root / "main.py").is_file() and not (root / "alias.py").is_symlink()
