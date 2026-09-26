"""
A job described in a file kept with the code.

The shape is SkyPilot's task YAML -- workdir, setup, run, envs, resources --
because that is what people running GPU jobs already write, and one file that
travels with the code is how a run is repeated without remembering its flags.
corerun adds what SkyPilot has no word for: the image, datasets, an
experiment, and code from a repository instead of the working directory.

    # corerun.yaml
    name: vision-train
    workdir: .                       # the folder to run; cloned into /code
    image: pytorch/pytorch:2.4.0-cuda12.1-cudnn9-runtime
    resources:
      compute: gke-uae-n1
      profile: gpu-h100-1            # or gpus: 1 on a host
      priority: normal               # low | normal | high
      max_runtime: 6h                # minutes, or 30m / 6h / 2d
    envs:
      EPOCHS: "10"
    datasets: [imagenet]             # mounted read-only under /data/
    experiment: vision-baselines
    setup: pip install -r requirements.txt   # optional; see below
    run: python train.py --epochs $EPOCHS

Without `setup`, a requirements.txt in the working directory is installed
before `run`, the way SageMaker, Vertex AI and Ray do; `setup` replaces that
with whatever it says. Code from a repository instead of `workdir`:

    repo: {name: train, ref: main, path: src}          # a workspace repository
    git:  {url: acme/trainer, connection: github, ref: v2}
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

KNOWN = {
    "name", "workdir", "image", "resources", "envs", "setup", "run",
    "datasets", "experiment", "repo", "git", "install_requirements",
}
RESOURCES = {"compute", "profile", "gpus", "priority", "max_runtime"}


class JobFileError(ValueError):
    """A job file that cannot be read as a job, said with where."""


def parse_minutes(value: Any) -> Optional[int]:
    """30, "30m", "6h", "2d" -> minutes."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    m = re.fullmatch(r"\s*(\d+)\s*([mhd]?)\s*", str(value))
    if not m:
        raise JobFileError(f"max_runtime {value!r} is not minutes, or a number with m, h or d")
    n, unit = int(m.group(1)), m.group(2) or "m"
    return n * {"m": 1, "h": 60, "d": 1440}[unit]


def load(path: str | Path) -> Dict[str, Any]:
    """Read a job file into the keyword arguments of corerun.jobs.submit.

    Only the keys the file sets are returned, so flags given beside it can
    fill in or override the rest.
    """
    path = Path(path)
    try:
        spec = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError) as e:
        raise JobFileError(f"{path}: {e}") from e
    if not isinstance(spec, dict):
        raise JobFileError(f"{path}: a job file is a mapping of keys, not {type(spec).__name__}")
    unknown = set(spec) - KNOWN
    if unknown:
        raise JobFileError(f"{path}: unknown keys {sorted(unknown)}; known are {sorted(KNOWN)}")
    resources = spec.get("resources") or {}
    unknown = set(resources) - RESOURCES
    if unknown:
        raise JobFileError(f"{path}: unknown resources {sorted(unknown)}; known are {sorted(RESOURCES)}")

    out: Dict[str, Any] = {}
    for key in ("name", "image", "experiment"):
        if spec.get(key):
            out[key] = str(spec[key])
    if resources.get("compute"):
        out["compute_name"] = str(resources["compute"])
    if resources.get("profile"):
        out["profile"] = str(resources["profile"])
    if resources.get("gpus") is not None:
        out["gpu"] = float(resources["gpus"])
    if resources.get("priority"):
        out["priority"] = str(resources["priority"])
    minutes = parse_minutes(resources.get("max_runtime"))
    if minutes:
        out["max_runtime_minutes"] = minutes
    if spec.get("envs"):
        out["environment"] = {str(k): str(v) for k, v in spec["envs"].items()}
    if spec.get("datasets"):
        out["datasets"] = [str(d) for d in spec["datasets"]]

    run, setup = spec.get("run"), spec.get("setup")
    if setup and not run:
        raise JobFileError(f"{path}: setup is given but run is not")
    if run:
        script = f"{str(setup).strip()}\n{str(run).strip()}" if setup else str(run).strip()
        out["command"] = ["sh", "-c", script]
    if setup:
        # setup says how to prepare; installing requirements.txt as well
        # would do it twice, or do what setup deliberately does not.
        out["install_requirements"] = False
    if "install_requirements" in spec:
        out["install_requirements"] = bool(spec["install_requirements"])

    sources = [k for k in ("workdir", "repo", "git") if spec.get(k)]
    if len(sources) > 1:
        raise JobFileError(f"{path}: name one of workdir, repo or git, not {sources}")
    if spec.get("workdir"):
        out["source_directory"] = (path.parent / str(spec["workdir"])).resolve()
    elif spec.get("repo"):
        r = spec["repo"] if isinstance(spec["repo"], dict) else {"name": spec["repo"]}
        out["repo"], out["ref"], out["path"] = r.get("name"), r.get("ref"), r.get("path")
    elif spec.get("git"):
        g = spec["git"] if isinstance(spec["git"], dict) else {"url": spec["git"]}
        out["git_url"], out["connection"], out["ref"], out["path"] = g.get("url"), g.get("connection"), g.get("ref"), g.get("path")
    return {k: v for k, v in out.items() if v is not None}
