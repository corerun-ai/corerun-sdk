"""
Starting a notebook elsewhere or with its compute's GPU, reading a stopped
notebook's files, and what the platform says about its operators and itself.

What the console does -- move a notebook, take or give back a GPU, read the
saved files of a stopped one -- an agent in a terminal does with the CLI, so
the two send the same request.
"""

import json

import httpx
import pytest
from typer.testing import CliRunner

from corerun import config as config_module
from corerun.cli import app
from corerun.cli import clusters as clusters_cli
from corerun.cli import compute as compute_cli
from corerun.cli import notebooks as notebooks_cli
from corerun.client import CoreRunClient

runner = CliRunner()
BASE = "https://example.test/api/v1"
NB = "6f1c2b0e-1111-4222-8333-944455556666"

NOTEBOOK = {
    "id": NB, "name": "trainer", "status": "stopped",
    "compute_name": "datacore-host", "image": "notebook:cpu", "gpu": 0, "owner_id": "me",
}


@pytest.fixture
def wire(monkeypatch):
    state = {"seen": [], "files": {"available": True, "path": "", "entries": []}}

    def handle(request: httpx.Request) -> httpx.Response:
        state["seen"].append(request)
        path = request.url.path
        if path.endswith("/health"):
            return httpx.Response(200, json={"status": "healthy", "version": "v0.4.0-dev.12.gabcdef12"})
        if path.endswith(f"/notebooks/{NB}/start"):
            body = json.loads(request.content) if request.content else {}
            answer = dict(NOTEBOOK, status="pending")
            answer["compute_name"] = body.get("compute_name", NOTEBOOK["compute_name"])
            answer["gpu"] = body.get("gpu", 0)
            return httpx.Response(202, json=answer)
        if path.endswith(f"/notebooks/{NB}/files"):
            return httpx.Response(200, json=state["files"])
        if path.endswith(f"/notebooks/{NB}/files/content"):
            return httpx.Response(200, json={"cells": [
                {"cell_type": "markdown", "source": ["# Train\n"]},
                {"cell_type": "code", "source": "print('hi')"},
            ]})
        if path.endswith(f"/notebooks/{NB}"):
            return httpx.Response(200, json=NOTEBOOK)
        if path.endswith("/notebooks"):
            return httpx.Response(200, json={"notebooks": [NOTEBOOK]})
        if path.endswith("/clusters"):
            return httpx.Response(200, json={"clusters": [
                {"name": "gb10", "type": "host", "status": "ready", "operator_connected": True,
                 "operator_version": "v0.4.0-dev.10.g11111111", "operator_update_available": True,
                 "operator_latest": "v0.4.0-dev.12.gabcdef12"},
            ]})
        if path.endswith("/compute"):
            return httpx.Response(200, json={"compute_targets": [
                {"name": "gb10-host", "type": "docker", "scope": "workspace", "gpus": 1, "gpu_model": "NVIDIA GB10"},
            ]})
        return httpx.Response(404, json={"error": "not_found"})

    client = CoreRunClient(config_module.Config(api_url=BASE))
    client._client = httpx.Client(transport=httpx.MockTransport(handle), base_url=BASE)
    for module in (notebooks_cli, clusters_cli, compute_cli):
        monkeypatch.setattr(module, "_init_client", lambda: None)
    monkeypatch.setattr(config_module, "_client", client)
    return state


def start_body(state) -> dict:
    req = [r for r in state["seen"] if r.url.path.endswith("/start")][-1]
    return json.loads(req.content) if req.content else {}


def test_a_plain_start_asks_for_nothing_else(wire):
    result = runner.invoke(app, ["notebooks", "start", NB])
    assert result.exit_code == 0, result.output
    assert start_body(wire) == {}


def test_starting_elsewhere_names_the_compute(wire):
    result = runner.invoke(app, ["notebooks", "start", NB, "--compute", "gb10-host"])
    assert result.exit_code == 0, result.output
    assert start_body(wire) == {"compute_name": "gb10-host"}
    assert "on gb10-host" in result.output


def test_taking_the_gpu_restarts_it_on_the_same_compute(wire):
    # The API reads a change of GPUs through its compute choice.
    result = runner.invoke(app, ["notebooks", "start", NB, "--gpu", "1"])
    assert result.exit_code == 0, result.output
    assert start_body(wire) == {"compute_name": "datacore-host", "gpu": 1.0}
    assert "with 1 GPU" in result.output


def test_a_stopped_notebooks_files(wire):
    wire["files"] = {"available": True, "path": "", "entries": [
        {"name": "exp", "path": "exp", "type": "directory"},
        {"name": "train.ipynb", "path": "train.ipynb", "type": "notebook", "size": 120},
    ]}
    result = runner.invoke(app, ["notebooks", "files", NB])
    assert result.exit_code == 0, result.output
    assert "exp/" in result.output and "train.ipynb" in result.output


def test_files_on_a_hosts_own_disk_say_so(wire):
    wire["files"] = {"available": False, "path": "", "entries": []}
    result = runner.invoke(app, ["notebooks", "files", NB])
    assert result.exit_code == 1
    assert "not kept" in result.output


def test_cat_shows_each_cell(wire):
    result = runner.invoke(app, ["notebooks", "cat", NB, "train.ipynb"])
    assert result.exit_code == 0, result.output
    assert "# Train" in result.output and "print('hi')" in result.output


def test_a_host_behind_says_what_to_run(wire):
    result = runner.invoke(app, ["clusters", "list"])
    assert result.exit_code == 0, result.output
    assert "update available" in result.output


def test_compute_lists_its_gpus(wire):
    result = runner.invoke(app, ["compute", "list"])
    assert result.exit_code == 0, result.output
    assert "NVIDIA GB10" in result.output


def test_version_names_the_platform_too(wire):
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0, result.output
    assert "platform v0.4.0-dev.12.gabcdef12" in result.output



def test_datasets_change_live_or_restart_only_when_asked(monkeypatch):
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.content))
        path = request.url.path
        if request.method == "GET" and path.endswith(f"/notebooks/{NB}"):
            return httpx.Response(200, json=dict(NOTEBOOK, status="running", datasets=["old"]))
        if request.method == "GET" and path.endswith("/notebooks"):
            return httpx.Response(200, json={"notebooks": [NOTEBOOK]})
        if request.method == "PUT" and path.endswith("/datasets"):
            body = json.loads(request.content)
            if body.get("next_start"):
                return httpx.Response(200, json={"applied": "next_start", "datasets": body["datasets"]})
            return httpx.Response(409, json={
                "error": "restart_required",
                "message": "A notebook on a Kubernetes cluster gets its datasets when it starts.",
            })
        if request.method == "POST" and (path.endswith("/stop") or path.endswith("/start")):
            return httpx.Response(200, json=dict(NOTEBOOK, status="pending"))
        return httpx.Response(404, json={"error": "not_found"})

    client = CoreRunClient(config_module.Config(api_url=BASE))
    client._client = httpx.Client(transport=httpx.MockTransport(handle), base_url=BASE)
    monkeypatch.setattr(notebooks_cli, "_init_client", lambda: None)
    monkeypatch.setattr(config_module, "_client", client)

    result = runner.invoke(app, ["notebooks", "datasets", NB, "--add", "mnist"])
    assert result.exit_code == 1, result.output
    assert "--restart" in result.output
    assert not any(m == "POST" for m, _, _ in calls), "restarted without being asked"

    calls.clear()
    result = runner.invoke(app, ["notebooks", "datasets", NB, "--add", "mnist", "--remove", "old", "--restart"])
    assert result.exit_code == 0, result.output
    puts = [json.loads(c) for m, _, c in calls if m == "PUT"]
    assert puts[-1] == {"datasets": ["mnist"], "next_start": True}
    assert [p.rsplit("/", 1)[-1] for m, p, _ in calls if m == "POST"] == ["stop", "start"]
