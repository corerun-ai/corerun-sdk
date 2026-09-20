"""
Running an evaluation, and reading the ones already run.

An evaluation measures a model rather than describing it: a public benchmark
against something serving, scored and recorded. It runs as a job on this
workspace's compute, and what it concluded is an experiment run in the GenAI
workspace -- so the score outlives the job that produced it, and is read
beside the traces of the thing it measured.

Two ids come back and both matter. `id` is the job: what to ask about logs, or
to stop. `run_id` is the result: what carries the scores once there are any.
The job finishing is not the same event as the score appearing, and code that
waits on one while wanting the other waits for the wrong thing.

    import corerun
    corerun.init()

    started = corerun.evaluations.start(
        name="gsm8k-on-nova",
        compute="datacore-host",
        benchmark="gsm8k",
        endpoint="nova",
        limit=10,
    )
    print(started.id, started.run_id)

    for evaluation in corerun.evaluations.evaluations():
        print(evaluation.name, evaluation.status, evaluation.benchmark)
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from corerun.config import get_client

_BASE = "/evaluations"


@dataclass
class Evaluation:
    """One evaluation: the job that ran it, and where its result lives."""

    id: str
    name: str = ""
    status: str = ""
    benchmark: str = ""
    target: str = ""

    #: The experiment run holding the scores. Created before the job starts,
    #: so this is never empty -- but it holds no metrics until the runner has
    #: something to report.
    run_id: str = ""
    experiment_id: str = ""
    experiment: str = ""

    compute_name: str = ""
    gpu: float = 0.0
    error: str = ""
    owner_email: str = ""
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None

    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def finished(self) -> bool:
        """Whether the job has stopped, for any reason.

        Not whether it succeeded: a failed evaluation is finished too. The
        engine spells the running states lowercase, and comparing to "DONE" or
        "COMPLETE" matches nothing and reads as still-running for ever.
        """
        return self.status in ("completed", "succeeded", "failed", "stopped")

    @property
    def failed(self) -> bool:
        return self.status == "failed"

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "Evaluation":
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            status=data.get("status", ""),
            benchmark=data.get("benchmark", ""),
            target=data.get("target", ""),
            run_id=data.get("run_id", ""),
            experiment_id=str(data.get("experiment_id") or ""),
            experiment=data.get("experiment", ""),
            compute_name=data.get("compute_name", ""),
            gpu=float(data.get("gpu") or 0),
            error=data.get("error", "") or "",
            owner_email=data.get("owner_email", ""),
            created_at=data.get("created_at"),
            started_at=data.get("started_at"),
            ended_at=data.get("ended_at"),
            raw=data,
        )


def start(
    *,
    name: str,
    compute: str,
    benchmark: str,
    endpoint: Optional[str] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    limit: Optional[int] = None,
    shots: Optional[int] = None,
    options: Optional[Dict[str, Any]] = None,
    framework: Optional[str] = None,
    sandbox: bool = False,
    experiment: Optional[str] = None,
    gpu: float = 0,
    profile: Optional[str] = None,
    image: Optional[str] = None,
    workspace: Optional[str] = None,
) -> Evaluation:
    """Start an evaluation and return it.

    The target is either `endpoint` -- one this workspace already serves -- or
    `base_url` plus `model` for anything else that speaks the OpenAI wire
    format. Naming neither is refused here rather than by the runner twenty
    minutes later.

    `sandbox` is for benchmarks that run each task in a container of their own.
    It asks for the privilege of nesting containers, so it is opt-in: almost no
    benchmark needs it and it should not be granted by default.
    """
    if not endpoint and not base_url:
        raise ValueError(
            "an evaluation needs a target: pass endpoint=, or base_url= and model="
        )
    if base_url and not model:
        raise ValueError("a target given by base_url must also name model=")

    benchmark_body: Dict[str, Any] = {"name": benchmark}
    if limit is not None:
        benchmark_body["limit"] = limit
    if shots is not None:
        benchmark_body["shots"] = shots
    if options:
        benchmark_body["options"] = options
    if framework:
        benchmark_body["framework"] = framework
    if sandbox:
        benchmark_body["sandbox"] = True

    target: Dict[str, Any] = {}
    if endpoint:
        target["endpoint"] = endpoint
    if model:
        target["model"] = model
    if base_url:
        target["base_url"] = base_url
    if api_key:
        target["api_key"] = api_key

    body: Dict[str, Any] = {
        "name": name,
        "compute_name": compute,
        "benchmark": benchmark_body,
        "target": target,
        "gpu": gpu,
    }
    if experiment:
        body["experiment"] = experiment
    if profile:
        body["profile"] = profile
    if image:
        body["image"] = image

    return Evaluation.from_json(get_client().post(_BASE, json=body, workspace=workspace))


def evaluations(
    *, status: Optional[str] = None, workspace: Optional[str] = None
) -> List[Evaluation]:
    """Every evaluation in this workspace, newest first."""
    params = {"status": status} if status else None
    payload = get_client().get(_BASE, params=params, workspace=workspace)
    return [Evaluation.from_json(row) for row in (payload.get("evaluations") or [])]


def evaluation(evaluation_id: str, *, workspace: Optional[str] = None) -> Evaluation:
    """One evaluation, by the id `start` returned."""
    return Evaluation.from_json(
        get_client().get(f"{_BASE}/{evaluation_id}", workspace=workspace)
    )
