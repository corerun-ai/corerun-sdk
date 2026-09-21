"""
GenAI observability for the corerun SDK.

Agent traces: a span tree for one invocation, the sessions they group into, the
judges that score them and the datasets they run against.

Not the same thing as `corerun.traces`, which is the record of requests through
a model endpoint. Two features, two prefixes, and a token scoped to one does
not reach the other.

**Sending traces needs nothing from here.** The platform accepts OpenTelemetry
on its own endpoint, so any instrumented application already speaks it:

    OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=https://<host>/v1/traces
    OTEL_EXPORTER_OTLP_TRACES_HEADERS=Authorization=Bearer <key>
    OTEL_EXPORTER_OTLP_TRACES_PROTOCOL=http/protobuf
    OTEL_SERVICE_NAME=<the experiment's name>

Which experiment a trace belongs to comes from `service.name` -- the resource
attribute every OpenTelemetry deployment already sets, usually through
OTEL_SERVICE_NAME. It means here what it means everywhere: which application
these spans came from. So an application that names itself needs nothing added
at all, and a name nobody has used yet is created on the first export.

Pointing an exporter at the URL is the whole setup.

This module is for reading them back.

Usage:
    import corerun
    corerun.init()

    for experiment in corerun.genai.experiments():
        print(experiment.experiment_id, experiment.name)

    for trace in corerun.genai.traces(experiment="2"):
        print(trace.trace_id, trace.state, trace.duration_ms)

    detail = corerun.genai.trace("tr-1c803305208d9e1b9cc32a302a56ade2")
    for span in detail.spans:
        print(span.name, span.span_type, span.duration_ms)
"""

import base64
import binascii
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

from corerun.config import get_client

# The engine is reached under the platform's own prefix, and authorised there
# like every other path. Versions differ per endpoint rather than per API, and
# a few endpoints exist only on the console's `ajax-api` -- deleting an
# experiment is one, and the REST path answers 404 for it.
_V2 = "/genai/api/2.0/mlflow"
_V3 = "/genai/api/3.0/mlflow"
_AJAX2 = "/genai/ajax-api/2.0/mlflow"
_AJAX3 = "/genai/ajax-api/3.0/mlflow"

# The six dimensions a trace is judged against, in the engine's own spelling.
#
# Listed here rather than fetched so `--categories` can be validated before a
# call is made, and so a name the engine stops accepting fails where somebody
# can read it rather than by quietly finding nothing.
ISSUE_CATEGORIES = (
    "correctness",
    "latency",
    "execution",
    "adherence",
    "relevance",
    "safety",
)

# The engine refuses a larger batch in one invocation.
MAX_TRACES_PER_DETECTION = 100

# Where the engine keeps the session id and the token counts: metadata keys,
# not columns.
_SESSION_KEY = "mlflow.trace.session"
_TOKENS_KEY = "mlflow.trace.tokenUsage"
_COST_KEY = "mlflow.trace.cost"

_DURATION = re.compile(r"([\d.]+)\s*(ms|s|m|h)")
_UNITS = {"ms": 1.0, "s": 1000.0, "m": 60_000.0, "h": 3_600_000.0}


def _duration_ms(formatted: Optional[str]) -> Optional[float]:
    """ "2.400s" or "1m 3s" as a number.

    The engine formats this for display. Unparseable reads as absent rather
    than as zero: a trace of unknown duration is not one that took no time.
    """
    if not formatted:
        return None
    total, matched = 0.0, False
    for value, unit in _DURATION.findall(formatted):
        try:
            total += float(value) * _UNITS[unit]
            matched = True
        except (ValueError, KeyError):
            continue
    return total if matched else None


def _metadata_json(metadata: Dict[str, str], key: str) -> Dict[str, Any]:
    raw = (metadata or {}).get(key)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def _hex(encoded: Optional[str]) -> Optional[str]:
    """Span ids come back base64; everything that reads one wants hex."""
    if not encoded:
        return None
    if re.fullmatch(r"[0-9a-fA-F]+", encoded) and len(encoded) % 2 == 0:
        return encoded.lower()
    try:
        return base64.b64decode(encoded).hex()
    except (binascii.Error, ValueError):
        return encoded


def _any_value(value: Optional[Dict[str, Any]]) -> Any:
    """An OTLP attribute value, as an ordinary Python one.

    Exactly one field is set and an empty object means null, so this is a union
    unwrap rather than a parse. Integers arrive as strings because proto3 JSON
    says 64-bit numbers do.
    """
    if not value:
        return None
    if "string_value" in value:
        return value["string_value"]
    if "bool_value" in value:
        return value["bool_value"]
    if "double_value" in value:
        return value["double_value"]
    if "int_value" in value:
        try:
            return int(value["int_value"])
        except (TypeError, ValueError):
            return value["int_value"]
    if "array_value" in value:
        return [_any_value(v) for v in value["array_value"].get("values", [])]
    if "kvlist_value" in value:
        return {
            entry["key"]: _any_value(entry.get("value"))
            for entry in value["kvlist_value"].get("values", [])
        }
    return None


def _decoded(text: Any) -> Any:
    """The engine JSON-encodes some attribute values, quotes and all."""
    if not isinstance(text, str):
        return text
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


@dataclass
class Experiment:
    """Where traces are collected. A trace belongs to exactly one."""

    experiment_id: str
    name: str
    artifact_location: Optional[str] = None
    lifecycle_stage: Optional[str] = None
    creation_time: Optional[int] = None
    last_update_time: Optional[int] = None
    tags: Dict[str, str] = field(default_factory=dict)


@dataclass
class Trace:
    """One agent invocation, summarised."""

    trace_id: str
    experiment_id: Optional[str] = None
    session_id: Optional[str] = None
    state: str = "OK"
    start_time: Optional[datetime] = None
    duration_ms: Optional[float] = None
    input_preview: Optional[str] = None
    output_preview: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    cost: Optional[float] = None
    tags: Dict[str, str] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        """Whether this invocation ended badly.

        The engine spells it in a state string, and every caller that wants to
        know asks the same question of it. Hiding the spelling is what the
        dataclass is for -- and it is the one comparison a caller gets wrong
        silently, by testing for "FAILED" or "error" and matching nothing.
        """
        return self.state == "ERROR"


@dataclass
class Span:
    """One step inside a trace."""

    span_id: str
    name: str
    parent_span_id: Optional[str] = None
    span_type: Optional[str] = None
    start_time: Optional[datetime] = None
    duration_ms: Optional[float] = None
    status: Optional[str] = None
    inputs: Any = None
    outputs: Any = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    attributes: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TraceDetail(Trace):
    """A trace with everything it did."""

    spans: List[Span] = field(default_factory=list)
    assessments: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class Session:
    """A conversation: the traces that carry the same session id.

    Derived rather than stored. The engine has no sessions endpoint, and its
    own console groups traces the same way.
    """

    session_id: str
    trace_count: int = 0
    error_count: int = 0
    total_tokens: int = 0
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None


@dataclass
class EvaluationRun:
    """One scoring pass over a dataset.

    The engine has no evaluation-run entity: a run carrying scores is an
    evaluation run and one that does not is a training run, and they live in
    the same table. So the scores are the substance here.
    """

    run_id: str
    name: str
    status: str
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    duration_ms: Optional[float] = None
    dataset: Optional[str] = None
    metrics: Dict[str, float] = field(default_factory=dict)
    params: Dict[str, str] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return self.status == "FAILED"


@dataclass
class ReviewQueue:
    """Traces somebody has been asked to look at.

    A USER queue is a person's own list, made on demand, one per person per
    experiment. A CUSTOM queue is one somebody created and assigned.
    """

    queue_id: str
    experiment_id: str
    name: str
    queue_type: str = "CUSTOM"
    created_by: Optional[str] = None
    users: List[str] = field(default_factory=list)


@dataclass
class ReviewItem:
    """One trace in a queue, and what was decided about it."""

    queue_id: str
    item_id: str
    status: str
    item_type: str = "TRACE"
    completed_by: Optional[str] = None

    @property
    def pending(self) -> bool:
        return self.status == "PENDING"


@dataclass
class Judge:
    """A scorer registered against an experiment."""

    scorer_id: str
    name: str
    version: int = 1
    experiment_id: Optional[str] = None
    serialized_scorer: str = ""


def _when(iso: Optional[str]) -> Optional[datetime]:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None


def _epoch(ms: Any) -> Optional[datetime]:
    """Epoch milliseconds as a datetime. Runs are timed in millis, not nanos."""
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _nanos(value: Any) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(int(value) / 1e9, tz=timezone.utc)
    except (TypeError, ValueError):
        return None


def _to_trace(info: Dict[str, Any]) -> Trace:
    metadata = info.get("trace_metadata") or {}
    usage = _metadata_json(metadata, _TOKENS_KEY)
    cost = _metadata_json(metadata, _COST_KEY)
    tags = info.get("tags") or {}
    return Trace(
        trace_id=info.get("trace_id", ""),
        experiment_id=(info.get("trace_location") or {})
        .get("mlflow_experiment", {})
        .get("experiment_id"),
        session_id=metadata.get(_SESSION_KEY) or tags.get(_SESSION_KEY),
        state=info.get("state") or "OK",
        start_time=_when(info.get("request_time")),
        duration_ms=_duration_ms(info.get("execution_duration")),
        input_preview=info.get("request_preview"),
        output_preview=info.get("response_preview"),
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        total_tokens=usage.get("total_tokens"),
        cost=cost.get("total_cost"),
        tags={k: v for k, v in tags.items() if not k.startswith("mlflow.")},
    )


def _to_span(raw: Dict[str, Any]) -> Span:
    attributes = {
        entry["key"]: _any_value(entry.get("value")) for entry in raw.get("attributes", [])
    }
    start = raw.get("start_time_unix_nano")
    end = raw.get("end_time_unix_nano")
    duration = None
    try:
        if start is not None and end is not None and int(end) > int(start):
            duration = (int(end) - int(start)) / 1e6
    except (TypeError, ValueError):
        duration = None

    usage = attributes.get("mlflow.chat.tokenUsage") or {}
    if not isinstance(usage, dict):
        usage = {}

    return Span(
        span_id=_hex(raw.get("span_id")) or "",
        parent_span_id=_hex(raw.get("parent_span_id")),
        name=raw.get("name", ""),
        span_type=_decoded(attributes.get("mlflow.spanType")),
        start_time=_nanos(start),
        duration_ms=duration,
        status=(raw.get("status") or {}).get("code"),
        inputs=attributes.get("mlflow.spanInputs") or attributes.get("gen_ai.input.messages"),
        outputs=attributes.get("mlflow.spanOutputs") or attributes.get("gen_ai.output.messages"),
        input_tokens=usage.get("input_tokens") or attributes.get("gen_ai.usage.input_tokens"),
        output_tokens=usage.get("output_tokens") or attributes.get("gen_ai.usage.output_tokens"),
        total_tokens=usage.get("total_tokens"),
        attributes=attributes,
    )


def experiments(*, limit: int = 200, workspace: Optional[str] = None) -> List[Experiment]:
    """Every experiment in the workspace, most recently touched first."""
    payload = get_client().get(
        f"{_V2}/experiments/search",
        params={"max_results": limit, "order_by": "last_update_time DESC"},
        workspace=workspace,
    )
    out = []
    for raw in payload.get("experiments", []) or []:
        out.append(
            Experiment(
                experiment_id=raw.get("experiment_id", ""),
                name=raw.get("name", ""),
                artifact_location=raw.get("artifact_location"),
                lifecycle_stage=raw.get("lifecycle_stage"),
                creation_time=raw.get("creation_time"),
                last_update_time=raw.get("last_update_time"),
                tags={t["key"]: t.get("value", "") for t in raw.get("tags", []) or []},
            )
        )
    return out


def create_experiment(name: str, *, workspace: Optional[str] = None) -> str:
    """Make one, and return its id. An exporter can also create it by naming it."""
    payload = get_client().post(
        f"{_V2}/experiments/create", json={"name": name}, workspace=workspace
    )
    return payload.get("experiment_id", "")


def delete_experiment(experiment_id: str, *, workspace: Optional[str] = None) -> None:
    """Delete one.

    On the console-only prefix at version 2: the REST path and both version-3
    spellings answer 404, which would read as the experiment being gone
    already.
    """
    get_client().post(
        f"{_AJAX2}/experiments/delete",
        json={"experiment_id": experiment_id},
        workspace=workspace,
    )


def traces(
    *,
    experiment: str,
    limit: int = 50,
    state: Optional[str] = None,
    session: Optional[str] = None,
    page_token: Optional[str] = None,
    workspace: Optional[str] = None,
) -> List[Trace]:
    """The most recent traces in an experiment.

    An experiment is required because the engine reads traces out of one: there
    is no such thing as the traces of a workspace at large.
    """
    filters = []
    if state:
        filters.append(f"attributes.status = '{state}'")
    if session:
        filters.append(f"metadata.`{_SESSION_KEY}` = '{session}'")

    body: Dict[str, Any] = {
        "locations": [
            {"type": "MLFLOW_EXPERIMENT", "mlflow_experiment": {"experiment_id": experiment}}
        ],
        "max_results": limit,
        # 'timestamp', not 'request_time': the search filters and sorts on the
        # stored attribute names while answering in the newer ones, so the
        # field you sort by and the field you read back are spelled
        # differently for the same thing.
        "order_by": ["timestamp DESC"],
    }
    if filters:
        body["filter"] = " AND ".join(filters)
    if page_token:
        body["page_token"] = page_token

    payload = get_client().post(f"{_V3}/traces/search", json=body, workspace=workspace)
    return [_to_trace(raw) for raw in payload.get("traces", []) or []]


def iter_traces(
    *, experiment: str, page_size: int = 100, workspace: Optional[str] = None, **filters
) -> Iterator[Trace]:
    """Every trace in an experiment, a page at a time."""
    token = None
    while True:
        body: Dict[str, Any] = {
            "locations": [
                {"type": "MLFLOW_EXPERIMENT", "mlflow_experiment": {"experiment_id": experiment}}
            ],
            "max_results": page_size,
            "order_by": ["timestamp DESC"],
        }
        if token:
            body["page_token"] = token
        payload = get_client().post(f"{_V3}/traces/search", json=body, workspace=workspace)

        rows = payload.get("traces", []) or []
        for raw in rows:
            yield _to_trace(raw)

        token = payload.get("next_page_token")
        if not token or not rows:
            return


def trace(trace_id: str, *, workspace: Optional[str] = None) -> TraceDetail:
    """One trace, with its spans.

    The batch read, for one: it is the only endpoint that answers with a trace
    and its spans together. Asking for the trace alone returns a record with no
    spans, and the artifact they used to live in is not where this engine keeps
    them.
    """
    payload = get_client().get(
        f"{_V3}/traces/batchGet", params={"trace_ids": trace_id}, workspace=workspace
    )
    rows = payload.get("traces", []) or []
    if not rows:
        raise ValueError(f"No trace {trace_id!r}")

    got = rows[0]
    summary = _to_trace(got.get("trace_info") or {})
    return TraceDetail(
        **summary.__dict__,
        spans=[_to_span(raw) for raw in got.get("spans", []) or []],
        assessments=(got.get("trace_info") or {}).get("assessments", []) or [],
    )


def delete_traces(trace_ids: List[str], *, experiment: str, workspace: Optional[str] = None) -> int:
    """Delete traces, and say how many went.

    `request_ids`, though the engine's own error for leaving it out asks for
    `trace_ids` -- sending the name it asks for repeats the same sentence.
    """
    if not trace_ids:
        return 0
    payload = get_client().post(
        f"{_V3}/traces/delete-traces",
        json={"experiment_id": experiment, "request_ids": trace_ids},
        workspace=workspace,
    )
    return payload.get("traces_deleted", 0)


def set_tag(trace_id: str, key: str, value: str, *, workspace: Optional[str] = None) -> None:
    """Set one tag on one trace."""
    get_client().patch(
        f"{_V3}/traces/{trace_id}/tags", json={"key": key, "value": value}, workspace=workspace
    )


def sessions(
    *, experiment: str, limit: int = 500, workspace: Optional[str] = None
) -> List[Session]:
    """The conversations in an experiment.

    Grouped here because the engine has no sessions endpoint. A trace with no
    session id belongs to no conversation and is left out rather than given one
    of its own.
    """
    grouped: Dict[str, Session] = {}
    for item in traces(experiment=experiment, limit=limit, workspace=workspace):
        if not item.session_id:
            continue
        session = grouped.get(item.session_id)
        if session is None:
            grouped[item.session_id] = Session(
                session_id=item.session_id,
                trace_count=1,
                error_count=1 if item.state == "ERROR" else 0,
                total_tokens=item.total_tokens or 0,
                first_seen=item.start_time,
                last_seen=item.start_time,
            )
            continue
        session.trace_count += 1
        session.total_tokens += item.total_tokens or 0
        if item.state == "ERROR":
            session.error_count += 1
        if item.start_time and session.first_seen and item.start_time < session.first_seen:
            session.first_seen = item.start_time
        if item.start_time and session.last_seen and item.start_time > session.last_seen:
            session.last_seen = item.start_time

    return sorted(
        grouped.values(),
        key=lambda s: s.last_seen or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )


def judges(*, experiment: str, workspace: Optional[str] = None) -> List[Judge]:
    """The judges registered against an experiment."""
    payload = get_client().get(
        f"{_V3}/scorers/list", params={"experiment_id": experiment}, workspace=workspace
    )
    return [
        Judge(
            scorer_id=raw.get("scorer_id", ""),
            # `scorer_name` coming back, `name` going in. The engine is not
            # symmetric here.
            name=raw.get("scorer_name") or raw.get("name") or "",
            version=raw.get("scorer_version") or raw.get("version") or 1,
            experiment_id=str(raw.get("experiment_id", "")),
            serialized_scorer=raw.get("serialized_scorer", ""),
        )
        for raw in payload.get("scorers", []) or []
    ]


def evaluation_runs(
    *, experiment: str, limit: int = 200, workspace: Optional[str] = None
) -> List[EvaluationRun]:
    """The evaluation runs in an experiment, newest first.

    Version 2 of that endpoint because there is no version 3 -- it answers 404
    where every other route here has a newer spelling.
    """
    payload = get_client().post(
        f"{_V2}/runs/search",
        json={
            "experiment_ids": [experiment],
            "max_results": limit,
            "order_by": ["attributes.start_time DESC"],
            # Deleted runs sit in the same table under a lifecycle stage, and
            # asking for everything lists runs somebody already threw away.
            "run_view_type": "ACTIVE_ONLY",
        },
        workspace=workspace,
    )

    out = []
    for raw in payload.get("runs", []) or []:
        info = raw.get("info") or {}
        data = raw.get("data") or {}
        tags = {t["key"]: t.get("value", "") for t in data.get("tags", []) or []}
        dataset = ((raw.get("inputs") or {}).get("dataset_inputs") or [{}])[0].get("dataset") or {}
        start, end = info.get("start_time"), info.get("end_time")
        out.append(
            EvaluationRun(
                run_id=info.get("run_id") or info.get("run_uuid", ""),
                name=tags.get("mlflow.runName") or info.get("run_name") or "(unnamed)",
                status=info.get("status", "UNKNOWN"),
                start_time=_epoch(start),
                end_time=_epoch(end),
                # Absent rather than zero while a run is still going: one that
                # has not finished has no duration.
                duration_ms=(end - start) if start and end and end > start else None,
                dataset=dataset.get("name"),
                metrics={m["key"]: m["value"] for m in data.get("metrics", []) or []},
                params={p["key"]: p.get("value", "") for p in data.get("params", []) or []},
            )
        )
    return out


def review_queues(
    *, experiment: str, user: Optional[str] = None, workspace: Optional[str] = None
) -> List[ReviewQueue]:
    """The review queues in an experiment, or only one person's.

    A GET, where the rest of this prefix is POST -- posting to it answers 405.
    """
    payload = get_client().get(
        f"{_V3}/review-queues/list",
        params={"experiment_id": experiment, **({"user": user} if user else {})},
        workspace=workspace,
    )
    return [
        ReviewQueue(
            queue_id=raw.get("queue_id", ""),
            experiment_id=str(raw.get("experiment_id", "")),
            name=raw.get("name", ""),
            queue_type=raw.get("queue_type", "CUSTOM"),
            created_by=raw.get("created_by"),
            users=list(raw.get("users") or []),
        )
        for raw in payload.get("review_queues", []) or []
    ]


def review_items(queue_id: str, *, workspace: Optional[str] = None) -> List[ReviewItem]:
    """What is in a queue, and what has been decided about each of it."""
    payload = get_client().get(
        f"{_V3}/review-queues/items/list", params={"queue_id": queue_id}, workspace=workspace
    )
    return [
        ReviewItem(
            queue_id=raw.get("queue_id", ""),
            item_id=raw.get("item_id", ""),
            status=raw.get("status", "PENDING"),
            item_type=raw.get("item_type", "TRACE"),
            completed_by=raw.get("completed_by"),
        )
        for raw in payload.get("items", []) or []
    ]


def create_review_queue(
    name: str,
    *,
    experiment: str,
    queue_type: str = "CUSTOM",
    workspace: Optional[str] = None,
) -> ReviewQueue:
    """Make a queue.

    The type goes in uppercase. The engine rejects the lowercase spelling its
    own Python enum uses, with "got proto enum value 0".
    """
    payload = get_client().post(
        f"{_V3}/review-queues/create",
        json={
            "experiment_id": experiment,
            "name": name,
            "queue_type": queue_type.upper(),
        },
        workspace=workspace,
    )
    raw = payload.get("review_queue") or {}
    return ReviewQueue(
        queue_id=raw.get("queue_id", ""),
        experiment_id=str(raw.get("experiment_id", "")),
        name=raw.get("name", ""),
        queue_type=raw.get("queue_type", "CUSTOM"),
        created_by=raw.get("created_by"),
        users=list(raw.get("users") or []),
    )


def add_to_review(queue_id: str, trace_ids: List[str], *, workspace: Optional[str] = None) -> None:
    """Queue traces for somebody to read. `item_ids`, not `items`."""
    if not trace_ids:
        return
    get_client().post(
        f"{_V3}/review-queues/items/add",
        json={"queue_id": queue_id, "item_ids": trace_ids, "item_type": "TRACE"},
        workspace=workspace,
    )


def review(
    queue_id: str,
    trace_id: str,
    status: str,
    *,
    by: Optional[str] = None,
    workspace: Optional[str] = None,
) -> None:
    """Record a decision: PENDING, COMPLETE or DECLINED.

    `by` is required when completing and the engine says so -- a completed
    review has an author.
    """
    status = status.upper()
    body = {"queue_id": queue_id, "item_id": trace_id, "status": status}
    if status == "COMPLETE":
        body["completed_by"] = by or "unknown"
    get_client().post(f"{_V3}/review-queues/items/set-status", json=body, workspace=workspace)


def assess(
    trace_id: str,
    name: str,
    value: Any,
    *,
    rationale: Optional[str] = None,
    span_id: Optional[str] = None,
    workspace: Optional[str] = None,
) -> Dict[str, Any]:
    """Record a judgement against a trace.

    The source is a person: this is the call a reviewer's script makes, and a
    judge's own scores are written by the judge.
    """
    assessment: Dict[str, Any] = {
        "trace_id": trace_id,
        "assessment_name": name,
        "source": {"source_type": "HUMAN"},
        "feedback": {"value": value},
    }
    if rationale:
        assessment["rationale"] = rationale
    if span_id:
        assessment["span_id"] = span_id

    return get_client().post(
        f"{_V3}/traces/{trace_id}/assessments", json={"assessment": assessment}, workspace=workspace
    )


@dataclass
class IssueDetection:
    """A detection job, and the run its findings are written into.

    Two identifiers because they answer different questions: `job_id` is
    whether the work is still going, `run_id` is where the result lives. The
    run exists from the moment the job is accepted, so it can be opened while
    the judge is still working.
    """

    job_id: str
    run_id: Optional[str] = None
    status: Optional[str] = None
    result: Any = None
    error: Optional[str] = None

    @property
    def finished(self) -> bool:
        return (self.status or "").upper() in {"SUCCEEDED", "FAILED", "DONE", "TIMEOUT"}

    @property
    def failed(self) -> bool:
        return (self.status or "").upper() in {"FAILED", "TIMEOUT"}


def detect_issues(
    *,
    experiment: str,
    endpoint: str,
    trace_ids: Optional[List[str]] = None,
    categories: Optional[List[str]] = None,
    sample: int = 25,
    workspace: Optional[str] = None,
) -> IssueDetection:
    """Read a sample of traces with a model, and file what it finds.

    The findings are written as assessments on the traces themselves, which is
    why nothing is returned here but a handle: they show up beside every other
    score rather than in a report of their own.

    `endpoint` is a corerun inference endpoint. It has to be one the engine can
    call itself, which every endpoint on this platform is -- they are
    registered with the engine when they are created.

    A sample, always. `sample` traces across `categories` dimensions is that
    many judgements on somebody's GPU, and a default meaning "all of them"
    would be a bill nobody agreed to. Pass `trace_ids` to choose exactly.

    Args:
        experiment: The experiment id whose traces to read.
        endpoint: The endpoint that judges, by name.
        trace_ids: Specific traces. Omit to take the most recent `sample`.
        categories: Which dimensions to judge. Omit for all six.
        sample: How many recent traces to read when `trace_ids` is omitted.

    Raises:
        ValueError: if a category is not one the engine defines, or there is
            nothing to read.
    """
    wanted = list(categories or ISSUE_CATEGORIES)
    unknown = [c for c in wanted if c not in ISSUE_CATEGORIES]
    if unknown:
        raise ValueError(
            f"Unknown {'category' if len(unknown) == 1 else 'categories'}: "
            f"{', '.join(unknown)}. Choose from: {', '.join(ISSUE_CATEGORIES)}"
        )

    ids = list(trace_ids or [])
    if not ids:
        # Recent rather than random: the question is nearly always "is it going
        # wrong now", and a random draw over a month answers a different one.
        ids = [t.trace_id for t in traces(experiment=experiment, limit=sample, workspace=workspace)]
    ids = ids[:MAX_TRACES_PER_DETECTION]

    if not ids:
        raise ValueError(f"No traces to read in experiment {experiment}.")

    payload = get_client().post(
        f"{_AJAX3}/issues/invoke",
        json={
            "experiment_id": experiment,
            "trace_ids": ids,
            "categories": wanted,
            "endpoint_name": endpoint,
            # Required by the engine's validator and read nowhere on this path:
            # with `endpoint_name` the model is built as `gateway:/<name>`. Sent
            # as `gateway` to say what is actually happening rather than to name
            # a vendor that is not involved.
            "provider": "gateway",
        },
        workspace=workspace,
    )
    return IssueDetection(
        job_id=payload.get("job_id", ""),
        run_id=payload.get("run_id"),
    )


def detection(job_id: str, *, workspace: Optional[str] = None) -> IssueDetection:
    """How a detection job is getting on, and what it found if it is done."""
    payload = get_client().get(f"{_AJAX3}/jobs/{job_id}", workspace=workspace)
    result = payload.get("result")
    # A failure arrives as the result rather than as an error field, spelled as
    # the engine's own exception repr. Reported as an error, because a caller
    # checking `.result` would otherwise read a traceback as a finding.
    error = payload.get("error")
    if error is None and isinstance(result, str) and result.startswith("MlflowException("):
        error, result = result, None
    return IssueDetection(
        job_id=job_id,
        run_id=payload.get("run_id"),
        status=payload.get("status"),
        result=result,
        error=error,
    )
