"""A pushed directory becomes a commit on the branch, and nothing more.

Run against a bare repository on disk rather than the platform: what is under
test is the git work -- the directory as the work tree, the branch's history
extended rather than replaced, the excludes, a no-op push -- not the transport.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from corerun import repos

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def remote(tmp_path, monkeypatch):
    bare = tmp_path / "remote.git"
    _git("init", "-q", "--bare", str(bare))
    monkeypatch.setattr(repos, "clone_url", lambda name, workspace=None: bare.as_uri())

    class Config:
        auth_token = "not-a-real-token"

    monkeypatch.setattr(repos, "get_config", lambda: Config())
    return bare


def _files(bare: Path, ref: str):
    return sorted(_git("--git-dir", str(bare), "ls-tree", "-r", "--name-only", ref).splitlines())


def test_a_directory_is_pushed_extended_and_left_alone(tmp_path, remote):
    src = tmp_path / "src"
    (src / "pkg").mkdir(parents=True)
    (src / "train.py").write_text("print('hi')\n")
    (src / "pkg" / "__init__.py").write_text("")
    (src / "pkg" / "__pycache__").mkdir()
    (src / "pkg" / "__pycache__" / "x.pyc").write_text("junk")
    (src / "secret.log").write_text("ignored by the directory's own .gitignore")
    (src / ".gitignore").write_text("*.log\n")

    first = repos.push("train", src, branch="job/a")
    assert _git("--git-dir", str(remote), "rev-parse", "refs/heads/job/a") == first
    assert _files(remote, first) == [".gitignore", "pkg/__init__.py", "train.py"]

    # Unchanged: no new commit.
    assert repos.push("train", src, branch="job/a") == first

    # A removed file is removed; the new commit's parent is the old one.
    (src / "pkg" / "__init__.py").unlink()
    (src / "pkg" / "model.py").write_text("")
    second = repos.push("train", src, branch="job/a")
    assert second != first
    assert _git("--git-dir", str(remote), "rev-parse", f"{second}^") == first
    assert _files(remote, second) == [".gitignore", "pkg/model.py", "train.py"]

    # A directory that is its own git checkout is left exactly as it was.
    _git("init", "-q", cwd=src)
    repos.push("train", src, branch="main")
    assert _git("status", "--porcelain", cwd=src) != ""  # its own index untouched
    assert ".git/HEAD" not in _files(remote, "main")


def test_a_missing_directory_is_refused(tmp_path, remote):
    with pytest.raises(repos.RepoError):
        repos.push("train", tmp_path / "nope")


def test_a_nested_clone_arrives_as_files(tmp_path, remote):
    src = tmp_path / "src"
    (src / "vendor" / "lib").mkdir(parents=True)
    (src / "train.py").write_text("print('hi')\n")
    (src / "vendor" / "lib" / "util.py").write_text("X = 1\n")
    _git("init", "-q", cwd=src / "vendor")  # the vendored library is its own clone

    commit = repos.push("train", src, branch="main")
    assert "vendor/lib/util.py" in _files(remote, commit)
    assert not any(f.startswith("vendor/.git") for f in _files(remote, commit))


def test_a_folder_with_data_in_it_is_refused_before_anything_is_sent(tmp_path, remote, monkeypatch):
    monkeypatch.setattr(repos, "MAX_FILE_BYTES", 1000)
    src = tmp_path / "src"
    src.mkdir()
    (src / "train.py").write_text("print('hi')\n")
    (src / "data.bin").write_bytes(b"x" * 5000)

    with pytest.raises(repos.RepoError) as err:
        repos.push("train", src, branch="main")
    assert "data.bin" in str(err.value) and ".gitignore" in str(err.value)
    assert _git("--git-dir", str(remote), "branch", "--list") == ""  # nothing pushed

    (src / ".gitignore").write_text("*.bin\n")
    assert repos.push("train", src, branch="main")
