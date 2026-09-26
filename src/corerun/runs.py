"""
Runs: what a training job, a fine-tune or a notebook logged.

Every training job on the platform logs into a run of its own, in the
experiment it was submitted under; a notebook logs into whichever experiment
its code names. A run holds the parameters it was given, the metrics it
logged over time, and the files it wrote. The console shows each run on its
own page; this is the same data from code.

Usage:
    import corerun
    corerun.init()

    for run in corerun.runs.search("default", filter="metrics.loss < 0.5", order_by="metrics.loss"):
        print(run.run_id, run.name, run.metrics.get("loss"))

    run = corerun.runs.get("3f2a...")
    history = corerun.runs.metric_history(run.run_id, "loss")
    for f in corerun.runs.files(run.run_id):
        print(f.path, f.size)
    corerun.runs.download(run.run_id, "model/MLmodel", "./MLmodel")
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from corerun.config import get_client
from corerun.exceptions import CoreRunError

_V2 = "/genai/api/2.0/mlflow"
_ARTIFACTS = "/genai/api/2.0/mlflow-artifacts/artifacts"

# The platform's own tag, naming the job a run belongs to.
JOB_TAG = "corerun.job_id"


@dataclass
class Run:
    run_id: str
    name: str
    experiment_id: str
    status: str
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    duration_ms: Optional[int] = None
    user: Optional[str] = None
    deleted: bool = False
    metrics: Dict[str, float] = field(default_factory=dict)
    params: Dict[str, str] = field(default_factory=dict)
    tags: Dict[str, str] = field(default_factory=dict)
    artifact_uri: Optional[str] = None

    @property
    def job_id(self) -> Optional[str]:
        """The platform job that wrote this run, when a job did."""
        return self.tags.get(JOB_TAG) or None

    @property
    def outputs(self) -> Optional[str]:
        """Where the job's outputs are kept, e.g. s3://bucket/jobs/<experiment>/<job>/artifacts."""
        return self.tags.get("corerun.outputs") or None

    @property
    def code(self) -> Dict[str, str]:
        """Where the code came from: source, repo, ref, path, commit -- whatever is known."""
        t = self.tags
        found = {
            "source": t.get("corerun.code.source") or t.get("mlflow.source.type", "").lower(),
            "repo": t.get("corerun.code.repo") or t.get("mlflow.source.git.repoURL", ""),
            "ref": t.get("corerun.code.ref") or t.get("mlflow.source.git.branch", ""),
            "path": t.get("corerun.code.path") or t.get("mlflow.source.name", ""),
            "commit": t.get("mlflow.source.git.commit", ""),
        }
        return {k: v for k, v in found.items() if v}


@dataclass
class RunFile:
    path: str
    is_dir: bool
    size: Optional[int] = None


@dataclass
class MetricPoint:
    step: int
    value: float
    timestamp: Optional[datetime] = None


def _epoch(ms: Any) -> Optional[datetime]:
    try:
        n = int(ms)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(n / 1000, tz=timezone.utc) if n > 0 else None


def _number(value: Any) -> float:
    """JSON has no NaN or infinity; the engine sends those as strings."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _to_run(raw: Dict[str, Any]) -> Run:
    info = raw.get("info") or {}
    data = raw.get("data") or {}
    tags = {t["key"]: t.get("value", "") for t in data.get("tags", []) or []}
    start, end = info.get("start_time"), info.get("end_time")
    try:
        duration = int(end) - int(start) if start and end and int(end) >= int(start) else None
    except (TypeError, ValueError):
        duration = None
    return Run(
        run_id=info.get("run_id") or info.get("run_uuid", ""),
        name=tags.get("mlflow.runName") or info.get("run_name") or "(unnamed)",
        experiment_id=str(info.get("experiment_id", "")),
        status=info.get("status", "UNKNOWN"),
        start_time=_epoch(start),
        end_time=_epoch(end),
        duration_ms=duration,
        user=info.get("user_id") or tags.get("mlflow.user") or None,
        deleted=info.get("lifecycle_stage") == "deleted",
        metrics={m["key"]: _number(m.get("value")) for m in data.get("metrics", []) or []},
        params={p["key"]: p.get("value", "") for p in data.get("params", []) or []},
        tags=tags,
        artifact_uri=info.get("artifact_uri"),
    )


def experiment_id(experiment: str, *, workspace: Optional[str] = None) -> str:
    """An experiment's id, from its id or its name.

    Jobs are submitted under a name, and the engine searches by id; a person
    knows the one and a script the other.
    """
    if experiment.isdigit():
        return experiment
    from corerun import genai

    for found in genai.experiments(workspace=workspace):
        if found.name == experiment:
            return found.experiment_id
    raise CoreRunError(f"No experiment called {experiment!r} in this workspace.")


def _order(order_by: Optional[str]) -> List[str]:
    """`metrics.loss` or `-metrics.loss` (descending), or the engine's own syntax."""
    if not order_by:
        return ["attributes.start_time DESC"]
    text = order_by.strip()
    if text.upper().endswith((" ASC", " DESC")):
        return [text]
    descending = text.startswith("-")
    key = text.lstrip("-")
    if "." in key and not key.startswith(("attributes.", "attribute.")):
        table, name = key.split(".", 1)
        key = f"{table}.`{name.strip('`')}`"
    return [f"{key} {'DESC' if descending else 'ASC'}", "attributes.start_time DESC"]


def search(
    experiment: str,
    *,
    filter: Optional[str] = None,
    order_by: Optional[str] = None,
    limit: int = 100,
    deleted: bool = False,
    workspace: Optional[str] = None,
) -> List[Run]:
    """Runs in an experiment (by name or id), newest first unless ordered.

    `filter` is the engine's search syntax, e.g. `metrics.loss < 0.5 AND
    params.lr = '0.001'`. `order_by` takes `metrics.loss` (ascending) or
    `-metrics.loss` (descending). Pages through until `limit` runs are read.
    """
    exp = experiment_id(experiment, workspace=workspace)
    found: List[Run] = []
    token: Optional[str] = None
    while len(found) < limit:
        body: Dict[str, Any] = {
            "experiment_ids": [exp],
            "max_results": min(1000, limit - len(found)),
            "order_by": _order(order_by),
            "run_view_type": "DELETED_ONLY" if deleted else "ACTIVE_ONLY",
        }
        if filter:
            body["filter"] = filter
        if token:
            body["page_token"] = token
        payload = get_client().post(f"{_V2}/runs/search", json=body, workspace=workspace)
        found.extend(_to_run(raw) for raw in payload.get("runs", []) or [])
        token = payload.get("next_page_token")
        if not token:
            break
    return found[:limit]


def get(run_id: str, *, workspace: Optional[str] = None) -> Run:
    """One run, by id."""
    payload = get_client().get(f"{_V2}/runs/get", params={"run_id": run_id}, workspace=workspace)
    return _to_run(payload.get("run") or {})


def metric_history(run_id: str, key: str, *, workspace: Optional[str] = None) -> List[MetricPoint]:
    """Every value a run logged for one metric, by step."""
    points: List[MetricPoint] = []
    token: Optional[str] = None
    while True:
        params: Dict[str, Any] = {"run_id": run_id, "metric_key": key, "max_results": 25000}
        if token:
            params["page_token"] = token
        payload = get_client().get(f"{_V2}/metrics/get-history", params=params, workspace=workspace)
        for m in payload.get("metrics", []) or []:
            points.append(MetricPoint(step=int(m.get("step") or 0), value=_number(m.get("value")), timestamp=_epoch(m.get("timestamp"))))
        token = payload.get("next_page_token")
        if not token:
            break
    points.sort(key=lambda p: p.step)
    return points


def _listing(run_id: str, path: Optional[str], workspace: Optional[str]) -> Dict[str, Any]:
    params: Dict[str, Any] = {"run_id": run_id}
    if path:
        params["path"] = path
    return get_client().get(f"{_V2}/artifacts/list", params=params, workspace=workspace)


def files(run_id: str, path: Optional[str] = None, *, workspace: Optional[str] = None) -> List[RunFile]:
    """One folder of a run's files; the top when no path is given."""
    payload = _listing(run_id, path, workspace)
    out = [
        RunFile(path=f["path"], is_dir=bool(f.get("is_dir")), size=int(f["file_size"]) if f.get("file_size") is not None else None)
        for f in payload.get("files", []) or []
    ]
    out.sort(key=lambda f: (not f.is_dir, f.path))
    return out


def _store_relative(root: str) -> str:
    """Where in the engine's store a run's files are: from `workspaces/` on."""
    at = root.find("workspaces/")
    if at < 0:
        raise CoreRunError("This run keeps its files where the platform cannot read them.")
    return root[at:].rstrip("/")


def download(run_id: str, path: str, dest: str, *, workspace: Optional[str] = None) -> int:
    """Save one of a run's files to `dest`, returning the bytes written."""
    root = _listing(run_id, None, workspace).get("root_uri") or ""
    relative = _store_relative(root)
    url = f"{_ARTIFACTS}/{quote(relative)}/{quote(path.strip('/'))}"
    return get_client().download(url, dest, workspace=workspace)
