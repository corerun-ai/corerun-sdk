"""`corerun finetune logs --follow` ends when the fine-tune does, as jobs do.

It had the same loop `jobs logs --follow` had: every exception swallowed, a
three-second sleep, and no way out but a status the model understood -- so an
API answering 500 kept it running for ever, printing nothing. A fine-tune's
logs are served by the jobs handler, so it follows them the same way.
"""

import json

import httpx
import pytest
from typer.testing import CliRunner

from corerun import config as config_module
from corerun import finetune
from corerun import jobs
from corerun.cli import app
from corerun.cli import finetune as finetune_cli
from corerun.client import CoreRunClient
from corerun.exceptions import NotFoundError, ServerError

runner = CliRunner()
BASE = "https://example.test/api/v1"
FT = "ft-1"


def _finetune(status: str) -> dict:
    return {
        "id": FT, "name": "tune", "framework": "hf_trainer", "base_model": "m", "method": "lora",
        "dataset_id": "d1", "status": status, "compute_name": "dgx", "owner_id": "u1",
    }


@pytest.fixture
def wire(monkeypatch):
    state = {"script": [], "reads": 0, "status": "succeeded", "paths": []}

    def handle(request: httpx.Request) -> httpx.Response:
        state["paths"].append(request.url.path)
        if request.url.path.endswith("/logs"):
            script = state["script"]
            code, body = script[min(state["reads"], len(script) - 1)]
            state["reads"] += 1
            return httpx.Response(code, json=body)
        if request.url.path.endswith(f"/finetune/{FT}"):
            return httpx.Response(200, json=_finetune(state["status"]))
        return httpx.Response(404, json={"error": "not_found"})

    client = CoreRunClient(config_module.Config(api_url=BASE))
    client._client = httpx.Client(transport=httpx.MockTransport(handle), base_url=BASE)
    monkeypatch.setattr(config_module, "_client", client)
    monkeypatch.setattr(finetune_cli, "_init_client", lambda: None)
    monkeypatch.setattr(jobs.time, "sleep", lambda _: None)
    return state


def test_it_reads_the_finetune_route_and_stops_when_it_ends(wire):
    wire["script"] = [
        (200, {"status": "running", "logs": "loss 2.1\n"}),
        (200, {"status": "succeeded", "logs": "loss 2.1\nloss 1.4\n"}),
    ]
    assert list(finetune.follow_logs(FT)) == ["loss 2.1\n", "loss 1.4\n"]
    assert all(p == f"/api/v1/finetune/{FT}/logs" for p in wire["paths"])


def test_an_answer_without_a_status_asks_the_finetune(wire):
    wire["script"] = [(200, {"logs": "x"})]
    wire["status"] = "stopped"
    assert list(finetune.follow_logs(FT)) == ["x"]
    assert f"/api/v1/finetune/{FT}" in wire["paths"]


def test_a_passing_failure_is_retried_and_a_lasting_one_raised(wire):
    wire["script"] = [(502, {"error": "bad_gateway"}), (200, {"status": "failed", "logs": "oom"})]
    assert list(finetune.follow_logs(FT)) == ["oom"]

    wire.update(script=[(503, {"error": "unavailable"})], reads=0)
    with pytest.raises(ServerError):
        list(finetune.follow_logs(FT, retries=2))
    assert wire["reads"] == 3


def test_a_failure_that_will_not_pass_is_raised_at_once(wire):
    wire["script"] = [(404, {"error": "not_found", "message": "Fine-tune not found"})]
    with pytest.raises(NotFoundError):
        list(finetune.follow_logs(FT))
    assert wire["reads"] == 1


def test_logs_follow_returns_what_it_printed(wire):
    wire["script"] = [(200, {"status": "running", "logs": "a"}), (200, {"status": "succeeded", "logs": "ab"})]
    seen = []
    assert finetune.logs(FT, follow=True, on_output=seen.append) == "ab"
    assert seen == ["a", "b"]


def test_the_cli_prints_the_log_and_the_final_status(wire):
    wire["script"] = [(200, {"status": "running", "logs": "loss 2.1\n"}), (200, {"status": "failed", "logs": "loss 2.1\nOOM\n"})]
    wire["status"] = "failed"
    result = runner.invoke(app, ["finetune", "logs", FT, "--follow"])
    assert result.exit_code == 0, result.output
    assert "OOM" in result.output and "Fine-tune finished with status: failed" in result.output


def test_the_cli_exits_non_zero_with_the_reason(wire):
    wire["script"] = [(503, {"error": "unavailable", "message": "upstream is restarting"})]
    result = runner.invoke(app, ["finetune", "logs", FT, "--follow"])
    assert result.exit_code == 1
    assert "upstream is restarting" in result.output

    as_json = runner.invoke(app, ["--json", "finetune", "logs", FT, "--follow"])
    assert as_json.exit_code == 1
    assert "upstream is restarting" in json.loads(as_json.output[as_json.output.index("{"):])["error"]


def test_ctrl_c_stops_following_and_exits_130(wire, monkeypatch):
    def interrupted(*args, **kwargs):
        yield "partial\n"
        raise KeyboardInterrupt

    monkeypatch.setattr(finetune, "follow_logs", interrupted)
    result = runner.invoke(app, ["finetune", "logs", FT, "--follow"])
    assert result.exit_code == 130
    assert "partial" in result.output and "keeps running" in result.output
