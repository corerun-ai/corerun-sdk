"""
What the evaluation commands put on the wire.

The Go handler binds a specific shape -- `compute_name`, a nested `benchmark`,
a nested `target` -- and nothing checks that this side sends it. A field spelled
differently here is not a type error in either language: it is a 400 at best,
and at worst a benchmark that runs with a limit the caller thought they set and
the server never saw.

So these assert the request, not the response. The response is the easy half.
"""

import httpx
import pytest
from typer.testing import CliRunner

from corerun import config as config_module
from corerun import evaluations as api
from corerun.cli import app
from corerun.cli import evaluations as evaluations_cli
from corerun.client import CoreRunClient

runner = CliRunner()

BASE = "https://example.test/api/v1"

STARTED = {
    "id": "job-1",
    "name": "gsm8k-nova",
    "status": "running",
    "benchmark": "gsm8k",
    "target": "nova",
    "run_id": "run-9",
    "experiment_id": "17",
    "experiment": "evaluations",
}


@pytest.fixture
def wire(monkeypatch):
    """Answer locally, keeping every request the CLI actually built."""
    state = {"seen": []}

    def handle(request: httpx.Request) -> httpx.Response:
        state["seen"].append(request)
        if request.method == "POST":
            return httpx.Response(201, json=STARTED)
        if request.url.path.endswith("/evaluations"):
            return httpx.Response(200, json={"evaluations": [STARTED], "total": 1})
        return httpx.Response(200, json=STARTED)

    client = CoreRunClient(config_module.Config(api_url=BASE))
    client._client = httpx.Client(transport=httpx.MockTransport(handle), base_url=BASE)

    monkeypatch.setattr(evaluations_cli, "_init_client", lambda: None)
    monkeypatch.setattr(config_module, "_client", client)
    return state


def test_start_sends_the_shape_the_api_binds(wire):
    api.start(
        name="gsm8k-nova",
        compute="datacore-host",
        benchmark="gsm8k",
        endpoint="nova",
        limit=10,
    )

    request = wire["seen"][-1]
    assert request.method == "POST"
    assert request.url.path.endswith("/evaluations")

    import json

    body = json.loads(request.content)
    # The API binds compute_name, not "compute": a request spelling it the
    # short way is refused for a missing required field.
    assert body["compute_name"] == "datacore-host"
    assert body["benchmark"]["name"] == "gsm8k"
    assert body["benchmark"]["limit"] == 10
    assert body["target"]["endpoint"] == "nova"


def test_limit_is_omitted_rather_than_sent_as_null(wire):
    """An absent limit must not become a limit of nothing.

    The Go side takes *int and treats absent as "the whole benchmark". A
    JSON null decodes to the same nil, so this is belt and braces -- but a
    zero would not, and sending 0 would quietly evaluate no examples at all.
    """
    api.start(name="n", compute="c", benchmark="gsm8k", endpoint="e")

    import json

    body = json.loads(wire["seen"][-1].content)
    assert "limit" not in body["benchmark"]


def test_sandbox_is_sent_only_when_asked_for(wire):
    """Nesting containers is a privilege, so it travels only on request."""
    import json

    api.start(name="n", compute="c", benchmark="b", endpoint="e")
    assert "sandbox" not in json.loads(wire["seen"][-1].content)["benchmark"]

    api.start(name="n", compute="c", benchmark="b", endpoint="e", sandbox=True)
    assert json.loads(wire["seen"][-1].content)["benchmark"]["sandbox"] is True


def test_a_target_is_required_before_anything_is_sent(wire):
    """Refused here, not by the runner twenty minutes later."""
    with pytest.raises(ValueError):
        api.start(name="n", compute="c", benchmark="b")
    with pytest.raises(ValueError):
        api.start(name="n", compute="c", benchmark="b", base_url="http://x")
    assert wire["seen"] == [], "a request was sent for an evaluation with no target"


def test_cli_start_prints_both_ids(wire):
    """The job id and the run id answer different questions.

    One has the logs, the other will have the scores. Printing only one sends
    people to the wrong place, which is the whole reason both are labelled.
    """
    result = runner.invoke(
        app,
        ["evaluations", "start", "gsm8k", "--compute", "c", "--endpoint", "nova", "--limit", "5"],
    )
    assert result.exit_code == 0, result.output
    assert "job-1" in result.output
    assert "run-9" in result.output


def test_cli_warns_when_no_limit_is_set(wire):
    """A full benchmark is thousands of calls. Saying so is cheap."""
    result = runner.invoke(
        app, ["evaluations", "start", "gsm8k", "--compute", "c", "--endpoint", "nova"]
    )
    assert result.exit_code == 0, result.output
    assert "No limit" in result.output


def test_cli_refuses_without_a_target(wire):
    result = runner.invoke(app, ["evaluations", "start", "gsm8k", "--compute", "c"])
    assert result.exit_code != 0
    assert "--endpoint" in result.output


def test_finished_reads_the_states_the_platform_writes(wire):
    """Lowercase, as the job document spells them.

    Comparing to "COMPLETE" matches nothing and reads as still-running for
    ever, which is the bug this property exists to prevent.
    """
    rows = api.evaluations()
    assert rows[0].status == "running"
    assert not rows[0].finished

    from corerun.evaluations import Evaluation

    assert Evaluation.from_json({"id": "x", "status": "failed"}).finished
    assert Evaluation.from_json({"id": "x", "status": "failed"}).failed
    assert Evaluation.from_json({"id": "x", "status": "completed"}).finished
    assert not Evaluation.from_json({"id": "x", "status": "pending"}).finished


def test_cancel_posts_to_the_route_the_api_serves(wire):
    """`corerun jobs cancel` has to reach a route that exists.

    It posted to `/jobs/{id}/cancel` and the API serves `/jobs/{id}/stop`, so
    every cancel came back as `404 page not found` -- which reads as a missing
    job, not a missing route, and so went unnoticed. Nothing on either side
    would catch it: both halves are valid, they just disagree.
    """
    from corerun import jobs

    # The reply here is an evaluation, not a job, so decoding it raises. That
    # is irrelevant: what is being pinned is the path the request went to,
    # which is already recorded by then.
    try:
        jobs.cancel("job-1")
    except Exception:  # noqa: BLE001 — the response shape is not what is under test
        pass

    path = wire["seen"][-1].url.path
    assert path.endswith("/jobs/job-1/stop"), f"cancel posted to {path}"


def test_logs_and_stop_use_the_evaluations_routes(wire):
    """Not the jobs routes, and this is the whole point of them existing.

    `/api/v1/jobs` is refused in a GenAI workspace on the `training`
    capability, which that onboarding preset does not grant. An evaluation
    started there could be neither watched nor stopped, and the CLI printed
    `corerun jobs logs <id>` as the next step.
    """
    from corerun import evaluations as api

    try:
        api.logs("job-1")
    except Exception:  # noqa: BLE001 — the fixture's reply shape is not under test
        pass
    assert wire["seen"][-1].url.path.endswith("/evaluations/job-1/logs")

    try:
        api.stop("job-1")
    except Exception:  # noqa: BLE001
        pass
    assert wire["seen"][-1].url.path.endswith("/evaluations/job-1/stop")


def test_the_printed_next_step_is_reachable_where_evaluations_run(wire):
    """The hint has to name a command that works in a GenAI workspace."""
    result = runner.invoke(
        app,
        ["evaluations", "start", "gsm8k", "--compute", "c", "--endpoint", "nova", "--limit", "5"],
    )
    assert result.exit_code == 0, result.output
    assert "corerun evaluations logs" in result.output
    assert "corerun jobs logs" not in result.output
