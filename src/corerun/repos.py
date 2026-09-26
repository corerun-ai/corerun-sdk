"""
Code repositories: a workspace's own git repositories on the platform.

Training code reaches a job through git. A workspace repository is the
simplest place to keep it -- no account on a git host, no token to manage --
and ``push`` puts a local directory there as a commit, which a job then
clones at start.

Usage:
    import corerun

    corerun.repos.create("train")
    commit = corerun.repos.push("train", "./src", branch="main")
    corerun.jobs.submit(..., repo="train", ref=commit)
"""

import os
import shutil
import subprocess
import tempfile
from base64 import b64encode
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from corerun.config import get_client, get_config

# Never committed from a pushed directory. The directory's own .gitignore is
# honoured as well, since the directory is the work tree of the push.
DEFAULT_EXCLUDES = [
    "__pycache__/",
    "*.pyc",
    "*.pyo",
    ".venv/",
    "venv/",
    ".env",
    "node_modules/",
    ".DS_Store",
    "*.egg-info/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".tox/",
    ".ipynb_checkpoints/",
]


# What a code folder may hold. A push is one request, and the edge in front of
# the platform refuses a request over 100 MB; git sends the whole folder the
# first time. Ray caps a working directory the same way, for the same reason:
# code is small, and a dataset or a checkpoint that slipped in is the usual
# cause of a large one.
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_TOTAL_BYTES = 90 * 1024 * 1024


class RepoError(Exception):
    """A repository operation failed."""


def list(workspace: Optional[str] = None) -> List[Dict[str, Any]]:
    """The workspace's code repositories."""
    return get_client().get("/code-repos", workspace=workspace).get("repos") or []


def get(name: str, workspace: Optional[str] = None) -> Dict[str, Any]:
    """One repository, with its branches and tags."""
    return get_client().get(f"/code-repos/{name}", workspace=workspace)


def create(
    name: str,
    description: Optional[str] = None,
    default_branch: Optional[str] = None,
    workspace: Optional[str] = None,
) -> Dict[str, Any]:
    """Create an empty repository in the workspace."""
    body: Dict[str, Any] = {"name": name}
    if description:
        body["description"] = description
    if default_branch:
        body["default_branch"] = default_branch
    return get_client().post("/code-repos", json=body, workspace=workspace)


def ensure(name: str, workspace: Optional[str] = None) -> Dict[str, Any]:
    """The repository, created if it does not exist yet."""
    for repo in list(workspace=workspace):
        if repo.get("name") == name:
            return repo
    return create(name, workspace=workspace)


def delete(name: str, workspace: Optional[str] = None) -> None:
    """Delete a repository and its history."""
    get_client().delete(f"/code-repos/{name}", workspace=workspace)


def tree(
    name: str, ref: Optional[str] = None, workspace: Optional[str] = None
) -> List[Dict[str, Any]]:
    """The files at a ref (the default branch when none is named)."""
    params = {"ref": ref} if ref else None
    return get_client().get(f"/code-repos/{name}/tree", params=params, workspace=workspace).get("files") or []


def clone_url(name: str, workspace: Optional[str] = None) -> str:
    """Where git reaches the repository from this machine.

    Built from the API address this client uses rather than taken from the
    server, which may know itself by an address only its own cluster reaches.
    """
    config = get_config()
    ws = workspace or config.workspace
    if not ws:
        raise RepoError("no workspace selected: run 'corerun workspace use <name>'")
    return config.api_url.rstrip("/") + f"/code/{ws}/{name}.git"


def push(
    name: str,
    source: Union[str, Path],
    branch: str = "main",
    message: Optional[str] = None,
    excludes: Optional[List[str]] = None,
    workspace: Optional[str] = None,
) -> str:
    """Commit a directory to a branch of a workspace repository and push it.

    The directory becomes the branch's whole tree: files removed locally are
    removed in the new commit. Nothing is copied -- the directory is used as
    the work tree of a scratch git directory, so its own .gitignore applies,
    and a .git inside it is left alone. Returns the commit's SHA, which is also
    what is returned, unchanged, when the directory matches the branch already.
    """
    source = Path(source).resolve()
    if not source.is_dir():
        raise RepoError(f"{source} is not a directory")
    git = shutil.which("git")
    if not git:
        raise RepoError(
            "git is required to push code and was not found on PATH.\n"
            'Install it with "brew install git" on macOS, or your distribution\'s package manager.'
        )

    url = clone_url(name, workspace)
    token = get_config().auth_token or ""
    credential = b64encode(f"corerun:{token}".encode()).decode()
    environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}

    with tempfile.TemporaryDirectory(prefix="corerun-push-") as scratch:
        gitdir = Path(scratch) / "git"

        def run(args, what, check=True):
            result = subprocess.run(
                [git, "--git-dir", str(gitdir), "--work-tree", str(source),
                 "-c", f"http.extraHeader=Authorization: Basic {credential}", *args],
                cwd=source,
                env=environment,
                capture_output=True,
                text=True,
            )
            if check and result.returncode != 0:
                detail = (result.stderr or result.stdout or "").strip().replace(credential, "***")
                raise RepoError(f"{what} failed: {detail}")
            return result

        run(["init", "-q"], "init")
        run(["symbolic-ref", "HEAD", f"refs/heads/{branch}"], "naming the branch")
        run(["remote", "add", "origin", url], "adding the remote")
        (gitdir / "info").mkdir(exist_ok=True)
        # Folders that are their own git clones are kept away from git's add,
        # which records them as pointers to another repository -- or refuses
        # outright when one has no commit yet -- and their files are added
        # by hand below.
        nested = _nested_repositories(source)
        (gitdir / "info" / "exclude").write_text(
            "\n".join((DEFAULT_EXCLUDES if excludes is None else excludes) + [f"/{rel}/" for rel in nested]) + "\n"
        )

        # The branch's current commit becomes the parent, so a push extends its
        # history rather than proposing an unrelated one. A branch that does not
        # exist yet (or an empty repository) starts from nothing.
        fetched = run(["fetch", "-q", "--depth", "1", "origin", f"refs/heads/{branch}"], "fetch", check=False)
        parent = None
        if fetched.returncode == 0:
            parent = run(["rev-parse", "FETCH_HEAD"], "reading the branch").stdout.strip()
            run(["reset", "-q", parent], "starting from the branch")
        elif "couldn't find remote ref" not in (fetched.stderr or "").lower():
            detail = (fetched.stderr or "").strip().replace(credential, "***")
            raise RepoError(f"fetch failed: {detail}")

        run(["add", "-A"], "staging")
        _inline_nested_repositories(run, source, nested)
        _check_sizes(run, source)
        if parent and run(["diff", "--cached", "--quiet"], "diff", check=False).returncode == 0:
            return parent

        run(["-c", "user.email=cli@corerun.local", "-c", "user.name=corerun CLI",
             "commit", "-q", "-m", message or f"push {source.name}"], "commit")
        run(["push", "-q", "origin", f"HEAD:refs/heads/{branch}"], "push")
        return run(["rev-parse", "HEAD"], "reading the commit").stdout.strip()


def _nested_repositories(source: Path) -> List[str]:
    """Folders under source that are git clones of their own, outermost first."""
    found: List[str] = []
    for marker in sorted(source.rglob(".git")):
        folder = marker.parent
        if folder == source:
            continue
        rel = folder.relative_to(source).as_posix()
        if not any(rel.startswith(outer + "/") for outer in found):
            found.append(rel)
    return found


def _inline_nested_repositories(run, source: Path, nested: List[str]) -> None:
    """Commit the files of a folder that is its own git clone.

    git would record such a folder as a pointer to another repository
    ("embedded repository"), not its contents, so the job would receive an
    empty folder -- a vendored library cloned into the code, say. Its files
    are added as ordinary files instead, and its own .git is left alone.
    """
    for rel in nested:
        root = source / rel
        for path in sorted(root.rglob("*")):
            parts = path.relative_to(root).parts
            if ".git" in parts or "__pycache__" in parts or not path.is_file() or path.is_symlink():
                continue
            name = f"{rel}/{path.relative_to(root).as_posix()}"
            sha = run(["hash-object", "-w", "--path", name, str(path)], f"storing {name}").stdout.strip()
            mode = "100755" if path.stat().st_mode & 0o111 else "100644"
            run(["update-index", "--add", "--cacheinfo", f"{mode},{sha},{name}"], f"staging {name}")


def _check_sizes(run, source: Path) -> None:
    """Refuse a folder too large to push, naming what makes it so."""
    listed = run(["ls-files", "-s", "-z"], "listing").stdout.split("\0")
    entries = []
    for line in listed:
        if not line:
            continue
        meta, rel = line.split("\t", 1)
        mode, sha, _ = meta.split(" ")
        if mode != "160000":
            entries.append((sha, rel))
    if not entries:
        return
    checked = subprocess.run(
        ["git", "--git-dir", str(Path(run(["rev-parse", "--absolute-git-dir"], "locating").stdout.strip())),
         "cat-file", "--batch-check=%(objectsize)"],
        input="\n".join(sha for sha, _ in entries) + "\n", capture_output=True, text=True, check=True,
    ).stdout.split()
    sizes = [(int(n), rel) for n, (_, rel) in zip(checked, entries)]
    big = sorted([(n, rel) for n, rel in sizes if n > MAX_FILE_BYTES], reverse=True)
    total = sum(n for n, _ in sizes)
    if big or total > MAX_TOTAL_BYTES:
        worst = big or sorted(sizes, reverse=True)
        listing = "\n".join(f"  {n / 1048576:8.1f} MB  {rel}" for n, rel in worst[:8])
        raise RepoError(
            f"{source} is too large to push as code ({total / 1048576:.0f} MB; files up to "
            f"{MAX_FILE_BYTES // 1048576} MB, {MAX_TOTAL_BYTES // 1048576} MB in all). The largest:\n{listing}\n"
            "Leave data and weights out of the code folder: add them to .gitignore, and use a dataset "
            "(mounted under /data/) or the model registry instead."
        )
