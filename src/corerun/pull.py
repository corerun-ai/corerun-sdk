"""
Moving a model in and out of the registry's git plane.

Model weights are LFS objects in a repository the platform serves through its
own git-proxy. git carries the commits; the weights go through this SDK's own
LFS client (corerun.lfs) in both directions -- ranged and resumable on the way
down, streamed straight from disk on the way up -- so git is the only tool a
machine needs. git-lfs is never run.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from base64 import b64encode
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

from corerun import lfs as lfs_transfer


class PullError(Exception):
    """A pull that could not be completed, phrased for the person who ran it."""


@dataclass
class PullProgress:
    """One observation of a pull in flight."""

    stage: str  # resolve | clone | checkout | weights | done
    detail: str = ""
    files_done: int = 0
    files_total: int = 0
    bytes_done: int = 0
    bytes_total: int = 0


ProgressFn = Callable[[PullProgress], None]


def _require(tool: str) -> str:
    """Locate a tool, or say how to get it."""
    found = shutil.which(tool)
    if not found:
        raise PullError(
            f"{tool} is required to pull models and was not found on PATH.\n"
            f"Install it with \"brew install {tool}\" on macOS, "
            f"or your distribution's package manager."
        )
    return found


def public_repo_url(repo_url: str, api_url: str) -> str:
    """Turn a stored repository URL into one this machine can reach.

    The URL recorded on a version names the platform's git backend, which is
    reachable only from inside the platform's own cluster, and roots the
    repository at the tenant's org ("t-<tenant>"). The proxy that is reachable
    is keyed by the tenant ID itself. Only the path carries identity, so the
    rest is rebuilt from the API base this client is configured with.
    """
    path = urlparse(repo_url).path.strip("/")
    if not path:
        raise PullError(f"Cannot tell which repository {repo_url!r} refers to")

    segments = path.split("/", 1)
    segments[0] = segments[0][2:] if segments[0].startswith("t-") else segments[0]
    return api_url.rstrip("/") + "/git/" + "/".join(segments)


def pull(
    repo_url: str,
    dest: Path,
    api_key: str,
    revision: str = "",
    report: Optional[ProgressFn] = None,
    jobs: int = 0,
) -> Path:
    """Fetch one revision of a model repository into dest.

    Returns the directory holding the model itself, which is not always the
    checkout root: the registry's importer puts the model under "model/" and its
    own metadata at the top.
    """
    report = report or (lambda _: None)

    git = _require("git")

    # A destination that is already this repository is resumed rather than
    # refused. git-lfs keeps completed objects in .git/lfs/objects and skips
    # them on a later pull, so a pull that died having fetched four of five
    # shards fetches only the fifth. It does not resume a partial object -- the
    # temp file it leaves carries a random suffix no later run looks for -- so
    # the object that was in flight starts over, but the finished ones do not.
    resuming = (dest / ".git").is_dir()
    if dest.exists() and any(dest.iterdir()) and not resuming:
        raise PullError(f"{dest} already exists and is not empty")

    # In a header rather than the URL: a URL with a credential in it is written
    # into .git/config and echoed in git's own error messages, so it would
    # outlive the pull and end up in whatever captured the output.
    credential = b64encode(f"corerun:{api_key}".encode()).decode()
    auth = [
        "-c", f"http.extraHeader=Authorization: Basic {credential}",
        # Turn the LFS filter off rather than ask it to stand aside.
        #
        # Weights are fetched by the ranged transfer below, so git needs to do
        # nothing here but write the pointer files as they are. Setting
        # GIT_LFS_SKIP_SMUDGE is not enough: git still runs "git-lfs
        # filter-process" in order to be told to skip, so a machine without
        # git-lfs fails the checkout over a tool the pull does not use.
        # Emptying the filter means git never looks for it.
        "-c", "filter.lfs.process=",
        "-c", "filter.lfs.smudge=cat",
        "-c", "filter.lfs.clean=cat",
        "-c", "filter.lfs.required=false",
    ]

    environment = {
        **os.environ,
        # This runs from a terminal, but not necessarily an interactive one, and
        # a credential prompt would hang the pull rather than fail it.
        "GIT_TERMINAL_PROMPT": "0",
    }

    def run(args, cwd=None, env_extra=None, what="") -> None:
        result = subprocess.run(
            [git, *auth, *args],
            cwd=cwd,
            env={**environment, **(env_extra or {})},
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "").strip()
            raise PullError(f"{what or ' '.join(args)} failed: {message}")

    if not resuming:
        report(PullProgress(stage="clone", detail=repo_url))
        run(["clone", "--no-checkout", repo_url, str(dest)], what="clone")
    else:
        report(PullProgress(stage="clone", detail="resuming " + str(dest)))
        run(["fetch", "--all"], cwd=dest, what="fetch")

    # Partial objects from an interrupted run are never reused -- each attempt
    # writes a new temp file -- so they are removed rather than left to
    # accumulate a copy of the model per failed attempt.
    incomplete = dest / ".git" / "lfs" / "incomplete"
    if incomplete.is_dir():
        shutil.rmtree(incomplete, ignore_errors=True)

    report(PullProgress(stage="checkout", detail=revision or "HEAD"))
    run(
        # Detaching HEAD is the point -- a version names a commit -- so git's
        # advice about it is not advice to anyone here.
        ["-c", "advice.detachedHead=false", "checkout", revision or "HEAD"],
        cwd=dest,
        what="checkout",
    )

    # The weights are fetched here rather than by "git lfs pull", which streams
    # one object at a time and cannot resume a partial one -- it names each
    # attempt's temp file with a random suffix, so nothing looks for the last.
    report(PullProgress(stage="weights", detail="fetching"))

    def on_bytes(name: str, done: int, total: int) -> None:
        report(
            PullProgress(
                stage="weights", detail=name, bytes_done=done, bytes_total=total
            )
        )

    try:
        lfs_transfer.fetch_all(
            checkout=dest,
            repo_url=repo_url,
            api_key=api_key,
            jobs=jobs or lfs_transfer.DEFAULT_JOBS,
            report=on_bytes,
        )
    except lfs_transfer.TransferError as e:
        raise PullError(str(e)) from e

    model_dir = model_root(dest)
    report(PullProgress(stage="done", detail=str(model_dir)))
    return model_dir


def model_root(checkout: Path) -> Path:
    """Find the directory holding the model inside a checkout.

    The importer lays a repository out with the model under "model/" and VERSION
    metadata at the top, so the checkout root is not what a model loader should
    be pointed at. A repository pushed by hand may be flat instead, so both are
    accepted.
    """
    for candidate in (checkout / "model", checkout):
        if (candidate / "config.json").exists():
            return candidate
    # Not an error: a model need not be a transformers checkpoint, and the
    # caller asked for the repository, which they have.
    return checkout


# Files worth storing as LFS objects rather than in the git history. Weights are
# the reason the git plane uses LFS at all: committing a gigabyte of tensors
# directly makes every later clone pay for it forever, because git history is
# immutable.
LFS_PATTERNS = (
    "*.safetensors",
    "*.bin",
    "*.pt",
    "*.pth",
    "*.ckpt",
    "*.onnx",
    "*.gguf",
    "*.h5",
    "*.msgpack",
    "*.model",
    "*.tflite",
    "*.npz",
    "*.pkl",
)


def push(
    repo_url: str,
    source: Path,
    dest_subdir: str,
    api_key: str,
    message: str,
    report: Optional[ProgressFn] = None,
) -> str:
    """Push a directory of model files as a new commit, returning its SHA.

    The layout matches what the registry's own importer writes -- files under
    "model/" -- so a pushed model and an imported one are pulled the same way.
    """
    report = report or (lambda _: None)

    git = _require("git")

    if not source.is_dir():
        raise PullError(f"{source} is not a directory")

    credential = b64encode(f"corerun:{api_key}".encode()).decode()
    auth = [
        "-c", f"http.extraHeader=Authorization: Basic {credential}",
        # The pointers are written here, not by git-lfs's clean filter, so a
        # git-lfs the user has installed must pass them through untouched --
        # and a machine without it must not fail looking for it.
        "-c", "filter.lfs.process=",
        "-c", "filter.lfs.smudge=cat",
        "-c", "filter.lfs.clean=cat",
        "-c", "filter.lfs.required=false",
    ]
    environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}

    with tempfile.TemporaryDirectory() as scratch:
        work = Path(scratch) / "repo"

        def run(args, cwd=None, env_extra=None, what="", check=True):
            result = subprocess.run(
                [git, *auth, *args],
                cwd=cwd,
                env={**environment, **(env_extra or {})},
                capture_output=True,
                text=True,
            )
            if check and result.returncode != 0:
                message = (result.stderr or result.stdout or "").strip()
                raise PullError(f"{what or ' '.join(args)} failed: {message}")
            return result

        # Cloned rather than initialised so a second push adds a commit to the
        # existing history instead of proposing an unrelated one, which the
        # server would refuse. A repository with no commits yet clones empty,
        # which is fine. Skipping the smudge keeps a git-lfs the user happens
        # to have installed from downloading the previous version's weights.
        report(PullProgress(stage="clone", detail=repo_url))
        run(
            ["clone", "--depth", "1", repo_url, str(work)],
            env_extra={"GIT_LFS_SKIP_SMUDGE": "1"},
            what="clone",
        )

        report(PullProgress(stage="copy", detail=str(source)))
        target = work / dest_subdir if dest_subdir else work
        if target.exists():
            shutil.rmtree(target)
        # The whole tree, so a push is the model as it is now rather than a
        # merge of it with whatever a previous version left behind. Weights are
        # never copied: each is hashed where it lies and a pointer written in
        # its place, which is what git-lfs's clean filter would have committed.
        objects = _stage(source, target)
        _track(work / ".gitattributes")

        run(["add", "-A"], cwd=work, what="staging")

        status = run(["status", "--porcelain"], cwd=work, what="status")
        if not status.stdout.strip():
            raise PullError("nothing to push: these files match the current version")

        # Identity is required for a commit and this runs unattended, so one is
        # supplied rather than letting git fail on its absence.
        run(["-c", "user.email=cli@corerun.local", "-c", "user.name=corerun CLI",
             "commit", "-m", message], cwd=work, what="commit")

        # Objects before refs: a pushed commit must never name weights the
        # store does not have.
        report(PullProgress(stage="upload", detail="weights"))
        try:
            lfs_transfer.upload_all(repo_url, api_key, objects)
        except lfs_transfer.TransferError as e:
            raise PullError(f"uploading weights failed: {e}") from e

        report(PullProgress(stage="upload", detail="pushing"))
        run(["push", "origin", "HEAD:refs/heads/main"], cwd=work, what="push",
            env_extra={"GIT_LFS_SKIP_PUSH": "1"})

        sha = run(["rev-parse", "HEAD"], cwd=work, what="reading commit")
        return sha.stdout.strip()


def _is_weight(path: Path) -> bool:
    return any(fnmatch(path.name, pattern) for pattern in LFS_PATTERNS)


def _stage(source: Path, target: Path) -> "list[lfs_transfer.LocalObject]":
    """Copy source into target, writing a pointer for each weight file."""
    objects = []
    for path in sorted(source.rglob("*")):
        rel = path.relative_to(source)
        if ".git" in rel.parts:
            continue
        dest = target / rel
        if path.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        if _is_weight(path):
            obj = lfs_transfer.hash_file(path)
            dest.write_text(lfs_transfer.pointer_text(obj))
            objects.append(obj)
        else:
            shutil.copy2(path, dest)
    return objects


def _track(attributes: Path) -> None:
    """Mark the weight patterns as LFS, as "git lfs track" would, keeping what is there."""
    existing = attributes.read_text().splitlines() if attributes.exists() else []
    tracked = {line.split()[0] for line in existing if line.strip()}
    lines = existing + [f"{p} filter=lfs diff=lfs merge=lfs -text" for p in LFS_PATTERNS if p not in tracked]
    attributes.write_text("\n".join(lines) + "\n")
