"""What `corerun runs` sends to the engine, and what it makes of the answer.

The shapes below are the engine's run search, get, history and listing,
trimmed. Values it cannot put in JSON -- NaN, infinity -- come back as strings.
"""

import json

import httpx
import pytest
from typer.testing import CliRunner

from corerun import config as config_module
from corerun import runs
from corerun.cli import app
from corerun.cli import runs as runs_cli
from corerun.client import CoreRunClient

runner = CliRunner()
BASE = "https://example.test/api/v1"
V2 = "/api/v1/genai/api/2.0/mlflow"

RUN = {
    "info": {
        "run_id": "r1", "experiment_id": "7", "status": "FINISHED",
        "start_time": 1_000, "end_time": 61_000, "lifecycle_stage": "active",
        "artifact_uri": "mlflow-artifacts:/workspaces/w1/7/r1/artifacts",
    },
    "data": {
        "metrics": [{"key": "loss", "value": 0.25}, {"key": "eval_loss", "value": "NaN"}],
        "params": [{"key": "lr", "value": "0.001"}],
        "tags": [{"key": "mlflow.runName", "value": "sweep-1"}, {"key": "corerun.job_id", "value": "job-9"}, {"key": "team", "value": "vision"},
                 {"key": "corerun.code.source", "value": "repo"}, {"key": "corerun.code.repo", "value": "trainer"},
                 {"key": "corerun.outputs", "value": "s3://ws/jobs/default/job-9/artifacts"}],
    },
}


@pytest.fixture
def wire(monkeypatch):
    seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path.endswith("/experiments/search"):
            return httpx.Response(200, json={"experiments": [{"experiment_id": "7", "name": "default"}]})
        if path.endswith("/runs/search"):
            return httpx.Response(200, json={"runs": [RUN]})
        if path.endswith("/runs/get"):
            return httpx.Response(200, json={"run": RUN})
        if path.endswith("/metrics/get-history"):
            return httpx.Response(200, json={"metrics": [{"step": 1, "value": 0.5, "timestamp": 2000}, {"step": 0, "value": 1.0, "timestamp": 1000}]})
        if path.endswith("/artifacts/list"):
            return httpx.Response(200, json={"root_uri": RUN["info"]["artifact_uri"], "files": [{"path": "summary.json", "is_dir": False, "file_size": 12}, {"path": "model", "is_dir": True}]})
        if path.endswith("/publish-from-job"):
            return httpx.Response(200, json={"model_name": "churn", "version": 3, "run_id": "r1"})
        if path.endswith("/registry/versions"):
            return httpx.Response(200, json={"versions": [{"model_name": "churn", "version": 3, "stage": "none", "location": "s3://ws/jobs/default/job-9/artifacts"}]})
        if "/mlflow-artifacts/artifacts/" in path:
            return httpx.Response(200, content=b'{"ok": true}')
        return httpx.Response(200, json={})

    client = CoreRunClient(config_module.Config(api_url=BASE))
    client._client = httpx.Client(transport=httpx.MockTransport(handle), base_url=BASE)
    monkeypatch.setattr(runs_cli, "_init_client", lambda: None)
    monkeypatch.setattr(config_module, "_client", client)
    return seen


def test_an_experiment_named_is_searched_by_its_id(wire):
    runs.search("default", filter="metrics.loss < 1", order_by="-metrics.eval/loss")
    search = [r for r in wire if r.url.path.endswith("/runs/search")][0]
    body = json.loads(search.content)
    assert body["experiment_ids"] == ["7"]
    assert body["filter"] == "metrics.loss < 1"
    # Backticks, because a metric's name can hold anything a person logged.
    assert body["order_by"][0] == "metrics.`eval/loss` DESC"
    assert body["run_view_type"] == "ACTIVE_ONLY"


def test_a_filter_left_out_is_not_sent(wire):
    runs.search("7")
    body = json.loads([r for r in wire if r.url.path.endswith("/runs/search")][0].content)
    assert "filter" not in body
    # An id needs no lookup.
    assert not any(r.url.path.endswith("/experiments/search") for r in wire)


def test_a_run_reads_its_name_job_and_duration(wire):
    run = runs.get("r1")
    assert (run.name, run.job_id, run.duration_ms, run.params["lr"]) == ("sweep-1", "job-9", 60_000, "0.001")
    assert run.metrics["eval_loss"] != run.metrics["eval_loss"]  # NaN
    assert run.code == {"source": "repo", "repo": "trainer"}
    assert run.outputs == "s3://ws/jobs/default/job-9/artifacts"


def test_history_is_ordered_by_step(wire):
    assert [p.step for p in runs.metric_history("r1", "loss")] == [0, 1]


def test_a_file_is_fetched_from_the_store_under_the_runs_root(wire, tmp_path):
    dest = tmp_path / "summary.json"
    assert runs.download("r1", "summary.json", str(dest)) == 12
    fetched = [r for r in wire if "/mlflow-artifacts/artifacts/" in r.url.path][0]
    assert fetched.url.path == "/api/v1/genai/api/2.0/mlflow-artifacts/artifacts/workspaces/w1/7/r1/artifacts/summary.json"
    assert dest.read_bytes() == b'{"ok": true}'


def test_the_cli_lists_and_shows(wire):
    listed = runner.invoke(app, ["runs", "list", "default", "--json"])
    assert listed.exit_code == 0, listed.output
    rows = json.loads(listed.output)
    assert rows[0]["run_id"] == "r1" and rows[0]["metrics"]["eval_loss"] is None
    assert "mlflow.runName" not in rows[0]["tags"]
    shown = runner.invoke(app, ["runs", "show", "r1"])
    assert shown.exit_code == 0 and "sweep-1" in shown.output
    files = runner.invoke(app, ["runs", "files", "r1"])
    assert "model/" in files.output


def test_registering_a_run_publishes_its_job(wire):
    result = runner.invoke(app, ["runs", "register", "r1", "churn"])
    assert result.exit_code == 0, result.output
    sent = [r for r in wire if r.url.path.endswith("/publish-from-job")][0]
    assert sent.url.path == "/api/v1/registry/models/churn/publish-from-job"
    assert json.loads(sent.content)["job_id"] == "job-9"
    assert "churn v3" in result.output


def test_a_runs_models_are_found_by_run_and_by_job(wire):
    result = runner.invoke(app, ["runs", "models", "r1", "--json"])
    assert result.exit_code == 0, result.output
    asked = [r for r in wire if r.url.path.endswith("/registry/versions")][0]
    assert asked.url.params.get_list("run_id") == ["r1", "job-9"]
    assert json.loads(result.output)[0]["location"] == "s3://ws/jobs/default/job-9/artifacts"
