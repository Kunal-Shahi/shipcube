import os

from shipcube_profiler.detect import summarize


def test_symlinks_are_never_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("SECRET = 'leak-me'\n")
    root = tmp_path / "repo"
    root.mkdir()
    (root / "lib.py").write_text("import os\n")
    os.symlink(outside / "secret.py", root / "app.py")       # entrypoint name
    os.symlink(outside / "secret.py", root / "requirements.txt")
    os.symlink(outside, root / "pkg")                       # a directory link
    s = summarize(root)
    assert "leak-me" not in s.excerpt
    assert s.requirements == []
    assert s.entrypoint == "lib.py"
    assert s.files == 1
