"""
What the GenAI commands put on the wire, and what they make of what comes back.

The engine is a third party: we do not get to choose its paths, its field
names or its shapes, and every one of them is asymmetric somewhere. `name`
goes in and `scorer_name` comes back. A delete takes `request_ids` while its
own error message asks for `trace_ids`. Durations arrive formatted for a human
and ids arrive base64. A disagreement about any of it reads as "no traces"
rather than as a bug, which is why these are pinned here rather than left to
be noticed in a console.

The bodies below are verbatim from a running engine, trimmed.
"""

import json
import re

import httpx
import pytest
from typer.testing import CliRunner

from corerun import config as config_module
from corerun import genai
from corerun.cli import app
from corerun.cli import genai as genai_cli
from corerun.client import CoreRunClient

runner = CliRunner()

BASE = "https://example.test/api/v1"
EXPERIMENT = "13"

V2 = "/api/v1/genai/api/2.0/mlflow"
V3 = "/api/v1/genai/api/3.0/mlflow"

TRACE_ID = "tr-4536c69e4220348fc027e1292a04da8a"

# A trace as the engine answers it: the session and the token counts live in
# trace_metadata as strings, and the duration is formatted for display.
TRACE_INFO = {
    "trace_id": TRACE_ID,
    "trace_location": {
        "type": "MLFLOW_EXPERIMENT",
        "mlflow_experiment": {"experiment_id": EXPERIMENT},
    },
    "request_time": "2026-09-19T12:50:54.437Z",
    "execution_duration": "26.162s",
    "state": "OK",
    "trace_metadata": {
        "mlflow.trace.session": "20260918_29",
        "mlflow.trace.tokenUsage": json.dumps(
            {"input_tokens": 25402, "output_tokens": 846, "total_tokens": 27673}
        ),
    },
    "tags": {
        "service.name": "atlas",
        "mlflow.artifactLocation": "/mnt/artifacts/13/traces/x/artifacts",
    },
    "request_preview": '[{"role":"user","content":"hi any new mails"}]',
    "response_preview": '[{"role":"assistant","content":"a reply"}]',
}

# Span ids arrive base64 and nanosecond timestamps arrive as strings.
def _span(span_id: str, name: str, parent: str = None, start_ns: int = 0, end_ns: int = 0):
    import base64

    raw = {
        "span_id": base64.b64encode(bytes.fromhex(span_id)).decode(),
        "name": name,
        "start_time_unix_nano": str(start_ns),
        "end_time_unix_nano": str(end_ns),
        "attributes": [
            {"key": "mlflow.spanType", "value": {"string_value": '"AGENT"'}},
        ],
    }
    if parent:
        raw["parent_span_id"] = base64.b64encode(bytes.fromhex(parent)).decode()
    return raw


BATCH = {
    "traces": [
        {
            "trace_info": TRACE_INFO,
            "spans": [
                _span("aa" * 8, "invoke_agent", start_ns=0, end_ns=26_162_130_000),
                _span("bb" * 8, "router_call", parent="aa" * 8, start_ns=1_000_000_000, end_ns=5_760_000_000),
                _span("cc" * 8, "complete", parent="bb" * 8, start_ns=1_000_000_000, end_ns=5_760_000_000),
            ],
        }
    ]
}


@pytest.fixture
def wire(monkeypatch):
    """Answer every request locally, and record what was asked."""
    state = {"seen": [], "respond": None}

    def handle(request: httpx.Request) -> httpx.Response:
        state["seen"].append(request)
        if state["respond"] is not None:
            return state["respond"](request)
        path = request.url.path
        if path.endswith("/experiments/search"):
            return httpx.Response(200, json={"experiments": [
                {"experiment_id": EXPERIMENT, "name": "atlas", "lifecycle_stage": "active"}
            ]})
        if path.endswith("/traces/batchGet"):
            return httpx.Response(200, json=BATCH)
        if path.endswith("/traces/search"):
            return httpx.Response(200, json={"traces": [TRACE_INFO]})
        if path.endswith("/traces/delete-traces"):
            return httpx.Response(200, json={"traces_deleted": 1})
        if path.endswith("/scorers/list"):
            return httpx.Response(200, json={"scorers": [
                {"scorer_id": "s-1", "scorer_name": "relevance", "scorer_version": 2,
                 "experiment_id": EXPERIMENT, "serialized_scorer": "{}"}
            ]})
        return httpx.Response(200, json={})

    client = CoreRunClient(config_module.Config(api_url=BASE))
    client._client = httpx.Client(transport=httpx.MockTransport(handle), base_url=BASE)
    monkeypatch.setattr(genai_cli, "_init_client", lambda: None)
    monkeypatch.setattr(config_module, "_client", client)
    return state


# ── What goes on the wire ────────────────────────────────────────────────────


def test_a_search_names_the_experiment_as_a_location(wire):
    """Traces are read out of an experiment, and the engine calls that a location.

    A body without `locations` is not a search of everything -- it is a search
    the engine refuses.
    """
    genai.traces(experiment=EXPERIMENT)
    sent = wire["seen"][0]
    assert sent.url.path == f"{V3}/traces/search"
    assert sent.method == "POST"
    body = json.loads(sent.content)
    assert body["locations"] == [
        {"type": "MLFLOW_EXPERIMENT", "mlflow_experiment": {"experiment_id": EXPERIMENT}}
    ]


def test_the_sort_field_is_the_stored_name_not_the_answered_one(wire):
    """`timestamp` going in, `request_time` coming back.

    The engine filters and sorts on the stored attribute names while answering
    in the newer ones. Sorting by `request_time` is rejected, so this is not a
    style choice -- it is the only spelling that works.
    """
    genai.traces(experiment=EXPERIMENT)
    assert json.loads(wire["seen"][0].content)["order_by"] == ["timestamp DESC"]


def test_filters_are_built_in_the_engines_grammar(wire):
    genai.traces(experiment=EXPERIMENT, state="ERROR", session="20260918_29")
    body = json.loads(wire["seen"][0].content)
    assert "attributes.status = 'ERROR'" in body["filter"]
    assert "metadata.`mlflow.trace.session` = '20260918_29'" in body["filter"]


def test_a_filter_left_out_is_not_sent_as_empty(wire):
    """An absent filter must be absent, not present and blank.

    state="" asks for traces whose state is the empty string, which is none of
    them -- so sending it turns "no filter" into "no results".
    """
    genai.traces(experiment=EXPERIMENT, limit=5)
    body = json.loads(wire["seen"][0].content)
    assert "filter" not in body
    assert body["max_results"] == 5


def test_deleting_sends_request_ids(wire):
    """`request_ids`, though the engine's own error asks for `trace_ids`.

    Sending the name it asks for repeats the same error, so this pins the name
    that actually works.
    """
    assert genai.delete_traces([TRACE_ID], experiment=EXPERIMENT) == 1
    body = json.loads(wire["seen"][0].content)
    assert body["request_ids"] == [TRACE_ID]
    assert body["experiment_id"] == EXPERIMENT


def test_a_trace_is_read_through_batch_get(wire):
    """The only endpoint that answers with a trace and its spans together.

    Asking for the trace alone returns a record with no spans at all.
    """
    genai.trace(TRACE_ID)
    assert wire["seen"][0].url.path == f"{V3}/traces/batchGet"
    assert dict(wire["seen"][0].url.params)["trace_ids"] == TRACE_ID


# ── What is made of the answer ───────────────────────────────────────────────


def test_a_trace_is_read_out_of_the_shapes_the_engine_uses(wire):
    """Duration formatted, session and tokens buried in metadata as JSON."""
    found = genai.traces(experiment=EXPERIMENT)[0]
    assert found.trace_id == TRACE_ID
    assert found.duration_ms == pytest.approx(26162.0)
    assert found.session_id == "20260918_29"
    assert found.total_tokens == 27673
    assert found.experiment_id == EXPERIMENT
    assert found.input_preview.startswith('[{"role":"user"')


def test_the_engines_own_bookkeeping_is_not_shown_as_a_tag(wire):
    """`mlflow.artifactLocation` is not something anybody tagged."""
    found = genai.traces(experiment=EXPERIMENT)[0]
    assert found.tags == {"service.name": "atlas"}


@pytest.mark.parametrize(
    "formatted,expected",
    [
        ("26.162s", 26162.0),
        ("140ms", 140.0),
        ("1m 3s", 63000.0),
        ("2s", 2000.0),
        (None, None),
        ("", None),
        ("a while", None),
    ],
)
def test_durations_are_parsed_from_what_the_engine_formats(formatted, expected):
    """Unparseable reads as absent, never as zero: a trace of unknown duration
    is not a trace that took no time, and sorting would put it first."""
    assert genai._duration_ms(formatted) == expected


def test_span_ids_come_back_hex_whatever_they_arrived_as(wire):
    detail = genai.trace(TRACE_ID)
    assert [s.name for s in detail.spans] == ["invoke_agent", "router_call", "complete"]
    assert detail.spans[0].span_id == "aa" * 8
    assert detail.spans[1].parent_span_id == "aa" * 8
    assert detail.spans[1].duration_ms == pytest.approx(4760.0)


def test_a_judges_name_is_read_from_the_field_it_comes_back_in(wire):
    """`name` going in, `scorer_name` coming back. Reading `name` gets blanks."""
    found = genai.judges(experiment=EXPERIMENT)
    assert found[0].name == "relevance"
    assert found[0].version == 2


def test_a_field_the_sdk_has_not_heard_of_does_not_break_a_read(wire):
    """An SDK older than the engine should still return the trace: the
    alternative is that every upgrade breaks every client behind it."""
    wire["respond"] = lambda r: httpx.Response(
        200, json={"traces": [{**TRACE_INFO, "a_field_from_the_future": 1}]}
    )
    assert genai.traces(experiment=EXPERIMENT)[0].trace_id == TRACE_ID


def test_failed_reads_the_state_rather_than_guessing(wire):
    wire["respond"] = lambda r: httpx.Response(
        200, json={"traces": [{**TRACE_INFO, "state": "ERROR"}]}
    )
    assert genai.traces(experiment=EXPERIMENT)[0].failed is True


def test_iter_traces_follows_pages_and_stops(wire):
    """Paging stops on a missing token, and does not re-ask forever."""
    pages = [
        {"traces": [TRACE_INFO], "next_page_token": "second"},
        {"traces": [{**TRACE_INFO, "trace_id": "tr-" + "b" * 32}]},
    ]
    wire["respond"] = lambda r: httpx.Response(200, json=pages[min(len(wire["seen"]) - 1, 1)])
    got = list(genai.iter_traces(experiment=EXPERIMENT, page_size=1))
    assert [t.trace_id for t in got] == [TRACE_ID, "tr-" + "b" * 32]
    assert json.loads(wire["seen"][1].content)["page_token"] == "second"


def test_sessions_are_grouped_here_because_the_engine_has_none(wire):
    """No sessions endpoint exists, so they are rolled up from the traces."""
    wire["respond"] = lambda r: httpx.Response(200, json={"traces": [
        TRACE_INFO,
        {**TRACE_INFO, "trace_id": "tr-2", "state": "ERROR"},
    ]})
    found = genai.sessions(experiment=EXPERIMENT)
    assert len(found) == 1
    assert found[0].session_id == "20260918_29"
    assert found[0].trace_count == 2
    assert found[0].error_count == 1


def test_a_trace_with_no_session_joins_no_conversation(wire):
    """Not a conversation of its own: a trace outside a session is not a
    session with one trace in it, and counting it as one invents them."""
    without = {k: v for k, v in TRACE_INFO.items() if k != "trace_metadata"}
    wire["respond"] = lambda r: httpx.Response(200, json={"traces": [without]})
    assert genai.sessions(experiment=EXPERIMENT) == []


def test_an_assessment_is_recorded_as_coming_from_a_person(wire):
    """A judge writes its own scores; this call is a reviewer's."""
    genai.assess(TRACE_ID, "helpfulness", 4, rationale="answered the question")
    sent = wire["seen"][0]
    assert sent.url.path == f"{V3}/traces/{TRACE_ID}/assessments"
    body = json.loads(sent.content)["assessment"]
    assert body["source"] == {"source_type": "HUMAN"}
    assert body["feedback"] == {"value": 4}
    assert body["rationale"] == "answered the question"


# ── The CLI ──────────────────────────────────────────────────────────────────


def test_cli_lists_traces(wire):
    result = runner.invoke(app, ["genai", "traces", "list", "-e", EXPERIMENT, "--state", "ERROR"])
    assert result.exit_code == 0, result.output
    assert "attributes.status = 'ERROR'" in json.loads(wire["seen"][0].content)["filter"]


def test_cli_lists_experiments(wire):
    result = runner.invoke(app, ["genai", "experiments"])
    assert result.exit_code == 0, result.output
    assert "atlas" in result.output
    assert wire["seen"][0].url.path == f"{V2}/experiments/search"


def test_cli_lists_judges(wire):
    result = runner.invoke(app, ["genai", "judges", "-e", EXPERIMENT])
    assert result.exit_code == 0, result.output
    assert "relevance" in result.output


def test_cli_sessions_list(wire):
    result = runner.invoke(app, ["genai", "sessions", "list", "-e", EXPERIMENT])
    assert result.exit_code == 0, result.output
    assert "20260918_29" in result.output


def test_cli_prints_the_span_tree_indented(wire):
    result = runner.invoke(app, ["genai", "traces", "get", TRACE_ID, "-e", EXPERIMENT])
    assert result.exit_code == 0, result.output
    lines = [l for l in result.output.splitlines() if "router_call" in l or "complete" in l]
    # A child indents further than its parent, which is the whole point of
    # printing a tree rather than a list.
    assert lines[0].index("router_call") < lines[1].index("complete")


def test_the_tree_draws_guides_and_a_bar_per_span(wire):
    """The shape of the call is the point, not a list of names."""
    result = runner.invoke(app, ["genai", "traces", "get", TRACE_ID, "-e", EXPERIMENT])
    assert result.exit_code == 0, result.output
    assert "├─" in result.output or "└─" in result.output
    assert "█" in result.output


def test_a_root_child_is_not_indented_under_a_guide_that_was_not_drawn(wire):
    """A root draws no joint, so its children start at the left edge.

    They inherited three columns of padding from a guide that was never
    printed, which read as the whole tree hanging off nothing.
    """
    result = runner.invoke(app, ["genai", "traces", "get", TRACE_ID, "-e", EXPERIMENT])
    joints = [l for l in result.output.splitlines() if l.lstrip().startswith(("├─", "└─"))]
    assert joints, result.output
    first = joints[0]
    at = min(i for i in (first.find("├─"), first.find("└─")) if i >= 0)
    assert at <= 1, repr(first)


def test_the_id_the_table_prints_is_one_get_accepts(wire):
    """Whatever the table prints must work when pasted back.

    It printed sixteen characters and `get` demanded the whole thing, so
    copying an id out of the listing answered "no trace with that id". This
    pins the round trip rather than the width, so both remain valid ways to
    keep it true.
    """
    listing = runner.invoke(app, ["genai", "traces", "list", "-e", EXPERIMENT])
    assert listing.exit_code == 0, listing.output

    # The id as a reader would copy it. The "tr-" is part of it: dropping the
    # prefix leaves a string that resolves against nothing.
    shown = max(re.findall(r"tr-[0-9a-f]+", listing.output), key=len)
    assert TRACE_ID.startswith(shown)

    wire["seen"].clear()
    fetched = runner.invoke(app, ["genai", "traces", "get", shown, "-e", EXPERIMENT])
    assert fetched.exit_code == 0, fetched.output


def test_a_missing_trace_reads_as_one_line_not_a_traceback(wire):
    """A trace id that has aged out is an ordinary answer.

    Before this, the command raised through typer and printed a stack trace
    with the useful sentence at the bottom of it.
    """
    wire["respond"] = lambda r: httpx.Response(404, json={"error": "no trace with that id"})
    result = runner.invoke(app, ["genai", "traces", "get", TRACE_ID, "-e", EXPERIMENT])
    assert result.exit_code == 1
    assert "Traceback" not in result.output


def test_a_failure_is_reported_in_json_mode_too(wire):
    """--json callers get an error document, not prose on stderr."""
    wire["respond"] = lambda r: httpx.Response(404, json={"error": "no trace with that id"})
    result = runner.invoke(app, ["--json", "genai", "traces", "get", TRACE_ID, "-e", EXPERIMENT])
    assert result.exit_code == 1
    assert '"error"' in result.output


def test_a_span_too_short_to_fill_a_cell_is_still_drawn():
    """A 0ms span happened. Rounding it away would hide a step."""
    from corerun.cli.genai import _bar

    assert _bar(0.5, 0.5, 20).strip() != ""


def test_a_bar_sits_where_the_span_ran_not_at_the_left():
    """Position carries as much as length: overlap is the question a slow
    trace raises, and every bar starting at zero cannot answer it."""
    from corerun.cli.genai import _bar

    late = _bar(0.5, 1.0, 20)
    assert late.startswith(" ")
    assert late.index("█") >= 9


# ── Issue detection ──────────────────────────────────────────────────────────


AJAX3 = "/api/v1/genai/ajax-api/3.0/mlflow"


def test_detection_sends_the_provider_the_engine_demands(wire):
    """`provider` is required and unused on this path.

    With `endpoint_name` the model is built as `gateway:/<name>` and the
    provider is read nowhere. The request schema marks it required anyway, so
    omitting it is refused with "Missing value for required parameter
    'provider'" -- on the one path where it means nothing.
    """
    genai.detect_issues(experiment=EXPERIMENT, endpoint="qwen27b", trace_ids=[TRACE_ID])

    sent = wire["seen"][-1]
    assert sent.url.path == f"{AJAX3}/issues/invoke"
    body = json.loads(sent.content)
    assert body["provider"] == "gateway"
    assert body["endpoint_name"] == "qwen27b"
    assert body["trace_ids"] == [TRACE_ID]


def test_omitting_categories_asks_for_all_six(wire):
    genai.detect_issues(experiment=EXPERIMENT, endpoint="qwen27b", trace_ids=[TRACE_ID])
    assert json.loads(wire["seen"][-1].content)["categories"] == list(genai.ISSUE_CATEGORIES)


def test_a_misspelt_category_is_refused_before_anything_is_spent(wire):
    """The whole point of checking locally.

    A category the engine does not define is not an error there -- it judges
    the ones it recognises and silently drops the rest, so a typo costs a full
    run and returns less than was asked for, with nothing saying why.
    """
    with pytest.raises(ValueError, match="relevence"):
        genai.detect_issues(
            experiment=EXPERIMENT,
            endpoint="qwen27b",
            trace_ids=[TRACE_ID],
            categories=["relevence"],
        )

    # And nothing was sent.
    assert not any("issues/invoke" in r.url.path for r in wire["seen"])


def test_no_traces_is_refused_rather_than_sent_as_an_empty_batch(wire):
    wire["respond"] = lambda request: httpx.Response(200, json={"traces": []})
    with pytest.raises(ValueError, match="No traces"):
        genai.detect_issues(experiment=EXPERIMENT, endpoint="qwen27b")


def test_the_batch_is_capped_at_what_the_engine_accepts(wire):
    genai.detect_issues(
        experiment=EXPERIMENT,
        endpoint="qwen27b",
        trace_ids=[f"tr-{n:032x}" for n in range(genai.MAX_TRACES_PER_DETECTION + 40)],
    )
    sent = json.loads(wire["seen"][-1].content)
    assert len(sent["trace_ids"]) == genai.MAX_TRACES_PER_DETECTION


def test_a_failure_arriving_as_the_result_is_reported_as_an_error(wire):
    """The engine puts the exception in `result`, not in `error`.

    A caller reading `.result` would print a traceback as though it were a
    finding, and one checking `.error` would see None and call it a success.
    """
    wire["respond"] = lambda request: httpx.Response(200, json={
        "status": "FAILED",
        "result": "MlflowException('Failed to call LLM endpoint at http://0.0.0.0:5000/...')",
    })

    state = genai.detection("job-1")
    assert state.failed
    assert state.result is None
    assert "Failed to call LLM endpoint" in state.error


def test_a_real_result_is_left_as_a_result(wire):
    wire["respond"] = lambda request: httpx.Response(200, json={
        "status": "SUCCEEDED",
        "result": "Analyzed 5 traces. Found 2 issues:",
    })

    state = genai.detection("job-1")
    assert state.finished and not state.failed
    assert state.error is None
    assert state.result.startswith("Analyzed 5 traces")


def test_the_run_is_returned_so_it_can_be_opened_while_it_runs(wire):
    """The run exists before the job starts, which is what makes it linkable."""
    wire["respond"] = lambda request: httpx.Response(
        200, json={"job_id": "job-1", "run_id": "run-9"}
    )

    started = genai.detect_issues(
        experiment=EXPERIMENT, endpoint="qwen27b", trace_ids=[TRACE_ID]
    )
    assert started.job_id == "job-1"
    assert started.run_id == "run-9"


def test_cli_detect_prints_what_it_will_spend(wire):
    # Two paths are involved: the sample is read first, then the job started.
    def respond(request):
        if request.url.path.endswith("/traces/search"):
            return httpx.Response(200, json={"traces": [TRACE_INFO]})
        return httpx.Response(200, json={"job_id": "job-1", "run_id": "run-9"})

    wire["respond"] = respond

    result = runner.invoke(
        app,
        ["genai", "issues", "detect", EXPERIMENT, "--endpoint", "qwen27b",
         "-c", "correctness,safety", "-n", "10"],
    )
    assert result.exit_code == 0, result.output
    # 10 traces x 2 categories, said before the call rather than after the bill.
    assert "20 judgements" in result.output
    assert "run-9" in result.output


def test_cli_show_exits_non_zero_on_a_failed_detection(wire):
    """So a script that runs one finds out."""
    wire["respond"] = lambda request: httpx.Response(200, json={
        "status": "FAILED",
        "result": "MlflowException('GatewayEndpoint not found')",
    })

    result = runner.invoke(app, ["genai", "issues", "show", "job-1"])
    assert result.exit_code == 1
    assert "GatewayEndpoint not found" in result.output
