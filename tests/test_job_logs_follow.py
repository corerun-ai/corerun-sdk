"""`corerun jobs logs --follow` ends when the job does, and says why when it cannot.

It used to poll for ever: every exception was swallowed and the loop only
stopped on a status the job model understood, so a job the API reported as
stopped -- or an API that answered 500 every time -- kept the command running
until somebody killed it, printing nothing.
"""

import json

import httpx
import pytest
from typer.testing import CliRunner

from corerun import config as config_module
from corerun import jobs
from corerun.cli import app
from corerun.cli import jobs as jobs_cli
from corerun.client import CoreRunClient
from corerun.exceptions import NotFoundError, ServerError
from corerun.models import Job

runner = CliRunner()
BASE = "https://example.test/api/v1"
JOB = "0b9c6a1e-7f4a-4c2e-9d1b-3a5e8f2c7d10"


def _job(status: str) -> dict:
    return {
        "id": JOB, "name": "train", "status": status, "compute_name": "dgx",
        "image": "img", "owner_id": "u1",
        "created_at": "2026-09-27T10:00:00Z", "updated_at": "2026-09-27T10:00:00Z",
    }


@pytest.fixture
def wire(monkeypatch):
    """Answers the logs route from a script, one entry per read.

    An entry is a (status code, body) pair; the last one repeats, so a script
    ending in a failure fails for ever.
    """
    state = {"script": [], "reads": 0, "job": "succeeded"}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/logs"):
            script = state["script"]
            code, body = script[min(state["reads"], len(script) - 1)]
            state["reads"] += 1
            return httpx.Response(code, json=body)
        if request.url.path.endswith(f"/jobs/{JOB}"):
            return httpx.Response(200, json=_job(state["job"]))
        return httpx.Response(404, json={"error": "not_found"})

    client = CoreRunClient(config_module.Config(api_url=BASE))
    client._client = httpx.Client(transport=httpx.MockTransport(handle), base_url=BASE)
    monkeypatch.setattr(config_module, "_client", client)
    monkeypatch.setattr(jobs_cli, "_init_client", lambda: None)
    monkeypatch.setattr(jobs.time, "sleep", lambda _: None)
    return state


def test_it_yields_what_is_new_and_stops_when_the_job_ends(wire):
    wire["script"] = [
        (200, {"status": "running", "logs": "epoch 1\n"}),
        (200, {"status": "running", "logs": "epoch 1\nepoch 2\n"}),
        (200, {"status": "succeeded", "logs": "epoch 1\nepoch 2\ndone\n"}),
    ]
    assert list(jobs.follow_logs(JOB)) == ["epoch 1\n", "epoch 2\n", "done\n"]
    assert wire["reads"] == 3


@pytest.mark.parametrize("status", ["succeeded", "completed", "failed", "error", "stopped", "cancelled", "published"])
def test_every_terminal_status_ends_it(wire, status):
    wire["script"] = [(200, {"status": status, "logs": "x"})]
    assert list(jobs.follow_logs(JOB)) == ["x"]


def test_a_log_that_starts_again_is_shown_from_the_top(wire):
    wire["script"] = [
        (200, {"status": "running", "logs": "attempt 1\n"}),
        (200, {"status": "failed", "logs": "attempt 2\n"}),
    ]
    assert list(jobs.follow_logs(JOB)) == ["attempt 1\n", "attempt 2\n"]


def test_a_passing_failure_is_retried(wire):
    wire["script"] = [
        (503, {"error": "unavailable"}),
        (503, {"error": "unavailable"}),
        (200, {"status": "succeeded", "logs": "ok"}),
    ]
    assert list(jobs.follow_logs(JOB, retries=3)) == ["ok"]


def test_a_failure_that_persists_is_raised(wire):
    wire["script"] = [(503, {"error": "unavailable"})]
    with pytest.raises(ServerError):
        list(jobs.follow_logs(JOB, retries=2))
    assert wire["reads"] == 3  # the first try and two more


def test_a_failure_that_will_not_pass_is_raised_at_once(wire):
    wire["script"] = [(404, {"error": "not_found", "message": "Job not found"})]
    with pytest.raises(NotFoundError):
        list(jobs.follow_logs(JOB))
    assert wire["reads"] == 1


def test_an_answer_without_a_status_asks_the_job(wire):
    wire["script"] = [(200, {"logs": "x"})]
    wire["job"] = "stopped"
    assert list(jobs.follow_logs(JOB)) == ["x"]


def test_logs_follow_returns_what_it_printed(wire):
    wire["script"] = [
        (200, {"status": "running", "logs": "a"}),
        (200, {"status": "succeeded", "logs": "ab"}),
    ]
    seen = []
    assert jobs.logs(JOB, follow=True, on_output=seen.append) == "ab"
    assert seen == ["a", "b"]


def test_a_stopped_job_is_finished():
    # jobs.wait polled a stopped job for ever: "stopped" is what the API's
    # Stop sets, and is_finished did not count it.
    assert Job(**_job("stopped")).is_finished


def test_the_cli_prints_the_log_and_the_final_status(wire):
    wire["script"] = [
        (200, {"status": "running", "logs": "epoch 1\n"}),
        (200, {"status": "failed", "logs": "epoch 1\nTraceback\n"}),
    ]
    wire["job"] = "failed"
    result = runner.invoke(app, ["jobs", "logs", JOB, "--follow"])
    assert result.exit_code == 0, result.output
    assert "epoch 1" in result.output and "Traceback" in result.output
    assert "Job finished with status: failed" in result.output


def test_the_cli_exits_non_zero_with_the_reason(wire):
    wire["script"] = [(503, {"error": "unavailable", "message": "upstream is restarting"})]
    result = runner.invoke(app, ["jobs", "logs", JOB, "--follow"])
    assert result.exit_code == 1
    assert "upstream is restarting" in result.output


def test_the_cli_json_error_is_a_document(wire):
    wire["script"] = [(404, {"error": "not_found", "message": "Job not found"})]
    result = runner.invoke(app, ["--json", "jobs", "logs", JOB, "--follow"])
    assert result.exit_code == 1
    assert "Job not found" in json.loads(result.output[result.output.index("{"):])["error"]


def test_ctrl_c_stops_following_and_says_the_job_keeps_running(wire, monkeypatch):
    def interrupted(*args, **kwargs):
        yield "partial\n"
        raise KeyboardInterrupt

    monkeypatch.setattr(jobs, "follow_logs", interrupted)
    result = runner.invoke(app, ["jobs", "logs", JOB, "--follow"])
    assert result.exit_code == 130
    assert "partial" in result.output
    assert "the job keeps running" in result.output
