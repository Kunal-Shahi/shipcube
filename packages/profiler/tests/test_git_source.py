"""Git sources are shallow and capped: size, per-file size, time, no submodules, no LFS,
no local paths from tenants (docs/research/profiler-sandbox.md §5)."""

import os
import socket
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


def test_transport_allowlist_holds_even_past_the_url_check(tmp_path, monkeypatch):
    """GIT_ALLOW_PROTOCOL=https blocks local transports at git's level too."""
    monkeypatch.setattr(sources, "_safe_git_url", lambda url: (url, "x", 443))   # past the URL check
    monkeypatch.setattr(sources, "_resolve_public", lambda url: [])                # and the host check
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
    monkeypatch.setattr(sources, "_run_git", lambda step, *a, **kw: seen.append(step) or real(step, *a, **kw))
    with pytest.raises(SourceError, match="checkout would be 10 MiB"):
        clone(repo, tmp_path / "out")
    assert seen == ["clone", "ls-tree"]              # never reached the checkout
    assert not (tmp_path / "out" / "repo").exists()


def test_clone_does_not_check_out_before_the_size_check(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "r", {"main.py": b"print(1)\n"})
    seen = []
    real = sources._run_git
    monkeypatch.setattr(sources, "_run_git",
                        lambda step, args, *a, **kw: seen.append((step, list(args))) or real(step, args, *a, **kw))
    clone(repo, tmp_path / "out")
    steps = [st for st, _ in seen]
    assert steps[:2] == ["clone", "ls-tree"] and set(steps[2:]) == {"checkout"}
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


def _blob(repo: Path, path: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), "show", f"HEAD:{path}"], check=True, capture_output=True,
                          env=ENV).stdout


def test_repo_attributes_cannot_inflate_the_checkout(tmp_path, monkeypatch):
    """Red team repro: working-tree-encoding=UTF-32LE made a 4 MB tree check out as 16 MB,
    past what ls-tree counted. Attributes (top-level and nested), eol and text conversion
    from the repo are ignored, so the checkout writes exactly the blob bytes."""
    text = b"a" * (1 * 2**20)
    repo = make_repo(tmp_path / "r", {"main.py": b"print(1)\n", "big.txt": text, "sub/inner.txt": text,
                                      "crlf.txt": b"x\n" * 1000})
    # Attributes arrive in a later commit, so the stored blobs are plain UTF-8.
    (repo / ".gitattributes").write_bytes(b"*.txt working-tree-encoding=UTF-32LE\ncrlf.txt text eol=crlf\n")
    (repo / "sub" / ".gitattributes").write_bytes(b"*.txt working-tree-encoding=UTF-32BE\n")
    git(repo, "add", ".gitattributes", "sub/.gitattributes")
    git(repo, "commit", "-qm", "attributes")
    # Sanity: with its attributes, a plain checkout of this repo really is ~4x bigger.
    plain = tmp_path / "plain"
    subprocess.run(["git", "-c", "protocol.file.allow=always", "clone", "-q", f"file://{repo}", str(plain)],
                   check=True, env=ENV)
    assert (plain / "big.txt").stat().st_size >= 4 * len(text)

    monkeypatch.setattr(sources, "GIT_MAX_BYTES", 3 * 2**20)   # tree is ~2 MiB; UTF-32 would be ~8 MiB
    root = clone(repo, tmp_path / "out")
    for path in ("big.txt", "sub/inner.txt", "crlf.txt"):
        assert (root / path).read_bytes() == _blob(repo, path), path
    assert not (root / ".gitattributes").exists() and not (root / "sub" / ".gitattributes").exists()
    assert (root / "big.txt").stat().st_size < 2 * len(text)


def test_attribute_settings_cannot_come_from_config_or_info(tmp_path, monkeypatch):
    attrs = tmp_path / "attrs"
    attrs.write_text("*.txt working-tree-encoding=UTF-32LE\n")
    gc = tmp_path / "gitconfig"
    gc.write_text(f"[core]\n\tattributesFile = {attrs}\n\tautocrlf = true\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gc))   # the worker's own config is ignored too
    repo = make_repo(tmp_path / "r", {"main.py": b"print(1)\n", "a.txt": b"hello\n"})
    root = clone(repo, tmp_path / "out")
    assert (root / "a.txt").read_bytes() == _blob(repo, "a.txt")
    cfg = sources._git_config(False)
    assert f"core.attributesFile={os.devnull}" in cfg and "core.autocrlf=false" in cfg
    assert sources._git_env(tmp_path, False)["GIT_ATTR_SOURCE"] == sources.EMPTY_TREE


def test_older_git_fallback_alone_also_ignores_attributes(tmp_path, monkeypatch):
    """Without GIT_ATTR_SOURCE (git < 2.40), dropping .gitattributes from the index before
    checkout-index is enough on its own."""
    real_env = sources._git_env
    monkeypatch.setattr(sources, "_git_env", lambda *a: {k: v for k, v in real_env(*a).items() if k != "GIT_ATTR_SOURCE"})
    repo = make_repo(tmp_path / "r", {"main.py": b"print(1)\n", "big.txt": b"a" * 2**16})
    (repo / ".gitattributes").write_bytes(b"*.txt working-tree-encoding=UTF-32LE\n")
    git(repo, "add", ".gitattributes")
    git(repo, "commit", "-qm", "attributes")
    root = clone(repo, tmp_path / "out")
    assert (root / "big.txt").read_bytes() == _blob(repo, "big.txt")


# --- public hosts only (red-team P-3, #23) ---------------------------------------------

def _resolver(table):
    def fake(host, port, *a, **kw):
        if host not in table:
            raise socket.gaierror(f"no such host {host}")
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))
                for ip in table[host]]
    return fake


@pytest.mark.parametrize("url,table", [
    ("https://127.0.0.1/r.git", {"127.0.0.1": ["127.0.0.1"]}),
    ("https://localhost/r.git", {"localhost": ["127.0.0.1", "::1"]}),
    ("https://169.254.169.254/computeMetadata/v1/", {"169.254.169.254": ["169.254.169.254"]}),
    ("https://metadata.google.internal/x", {"metadata.google.internal": ["169.254.169.254"]}),
    ("https://10.0.0.5/r.git", {"10.0.0.5": ["10.0.0.5"]}),
    ("https://git.corp.example/r.git", {"git.corp.example": ["192.168.1.10"]}),
    ("https://cgnat.example/r.git", {"cgnat.example": ["100.64.0.1"]}),
    ("https://[::1]/r.git", {"::1": ["::1"]}),
    ("https://v6.example/r.git", {"v6.example": ["fd00::1"]}),
    ("https://mapped.example/r.git", {"mapped.example": ["::ffff:10.0.0.1"]}),
    ("https://mixed.example/r.git", {"mixed.example": ["93.184.216.34", "10.0.0.1"]}),   # any private address refuses
    ("https://nowhere.example/r.git", {}),
])
def test_git_hosts_must_be_public(tmp_path, monkeypatch, url, table):
    monkeypatch.setattr(sources.socket, "getaddrinfo", _resolver(table))
    ran = []
    monkeypatch.setattr(sources, "_run_git", lambda *a, **kw: ran.append(a) or (0, "", ""))
    with pytest.raises(SourceError):
        materialize({"kind": "git", "url": url}, tmp_path / "out")
    assert ran == []                                  # refused before git runs


def test_public_host_is_pinned_and_redirects_are_off(tmp_path, monkeypatch):
    monkeypatch.setattr(sources.socket, "getaddrinfo", _resolver({"github.com": ["2606:50c0::1", "20.207.73.82"]}))
    assert sources._resolve_public("https://github.com/o/r.git") == [
        "-c", "http.curloptResolve=github.com:443:20.207.73.82", "-c", "http.followRedirects=false"]
    monkeypatch.setattr(sources.socket, "getaddrinfo", _resolver({"git.example": ["2606:50c0::1"]}))
    assert sources._resolve_public("https://git.example:8443/r")[1] == "http.curloptResolve=git.example:8443:[2606:50c0::1]"
    seen = {}

    def run_git(step, args, *a, **kw):
        seen.setdefault(step, args)
        return 1, "", "stop here"
    monkeypatch.setattr(sources.socket, "getaddrinfo", _resolver({"github.com": ["20.207.73.82"]}))
    monkeypatch.setattr(sources, "_run_git", run_git)
    with pytest.raises(SourceError):
        materialize({"kind": "git", "url": "https://github.com/o/r.git"}, tmp_path / "out")
    assert "http.curloptResolve=github.com:443:20.207.73.82" in seen["clone"]
    assert "http.followRedirects=false" in seen["clone"]


# --- the URL git sees is rebuilt from parsed parts (red team on #63) --------------------

@pytest.mark.parametrize("url", [
    "https://evil@good.example/r.git",                 # userinfo
    "https://user:pass@good.example/r.git",
    "https://good.example\\@evil.example/r.git",       # backslash: parsers disagree on the host
    "https://good.example\\evil.example/r.git",
    "https://%65vil.example/r.git",                    # percent-encoded host
    "https://good.example%2f@evil.example/r.git",
    "https://good.example%00.evil/r.git",
    "https://xn--e1afmkfd.exampleа/r.git",       # non-ASCII
    "https://good.example/r.git?x=1", "https://good.example/r.git#frag",
    "https://good.example/../../etc", "https://good_example/r.git", "https://-bad.example/r.git",
    "https://good.example:0x50/r.git", "https://good.example /r.git", "https://good.example/r\n.git",
])
def test_ambiguous_git_urls_are_refused_before_resolving(tmp_path, monkeypatch, url):
    resolved = []
    monkeypatch.setattr(sources.socket, "getaddrinfo", lambda *a, **k: resolved.append(a) or [])
    monkeypatch.setattr(sources, "_run_git", lambda *a, **kw: pytest.fail("git must not run"))
    with pytest.raises(SourceError):
        sources._clone(url, None, tmp_path / "out" / "repo")
    assert resolved == []


@pytest.mark.parametrize("url,rebuilt,host,port", [
    ("https://GitHub.com./o/r.git", "https://github.com/o/r.git", "github.com", 443),
    ("https://github.com:443/o/r.git", "https://github.com:443/o/r.git", "github.com", 443),
    ("https://git.example:8443/team/r", "https://git.example:8443/team/r", "git.example", 8443),
    ("https://[2606:50c0::1]/o/r.git", "https://[2606:50c0::1]/o/r.git", "2606:50c0::1", 443),
    ("https://github.com", "https://github.com/", "github.com", 443),
])
def test_git_url_is_rebuilt_from_its_parts(url, rebuilt, host, port):
    assert sources._safe_git_url(url) == (rebuilt, host, port)


def test_git_runs_with_the_rebuilt_url(tmp_path, monkeypatch):
    monkeypatch.setattr(sources.socket, "getaddrinfo", _resolver({"github.com": ["20.207.73.82"]}))
    seen = {}

    def run_git(step, args, *a, **kw):
        seen.setdefault(step, list(args))
        return 1, "", "stop here"
    monkeypatch.setattr(sources, "_run_git", run_git)
    with pytest.raises(SourceError):
        sources._clone("https://GitHub.com./o/r.git", None, tmp_path / "out" / "repo")
    assert seen["clone"][-2] == "https://github.com/o/r.git"
    assert "http.curloptResolve=github.com:443:20.207.73.82" in seen["clone"]
