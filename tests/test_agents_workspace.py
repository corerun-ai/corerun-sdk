"""The agents workspace from the SDK and CLI: asks, sandboxes and the workspace's activity."""

import httpx
import pytest
from typer.testing import CliRunner

from corerun import agents
from corerun import config as config_module
from corerun.client import CoreRunClient

BASE = "https://example.test/api/v1"


@pytest.fixture
def wire(monkeypatch):
    seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path.endswith("/agentguard/asks"):
            return httpx.Response(200, json={"asks": [{"id": "a1", "agent_id": "ag-1", "title": "Use send_mail", "status": "pending",
                                                       "created_at": "2026-10-08T10:00:00Z"}], "waiting": 1})
        if path.endswith("/agent-sandboxes") and request.method == "GET":
            return httpx.Response(200, json={"sandboxes": [{"notebook_id": "nb-1", "agent_name": "Tutor", "status": "running",
                                                            "owner_email": "a@example.test", "compute": "gb10"}],
                                             "running": 1, "ready": 0, "all": True})
        if "/agent-sandboxes/" in path and request.method == "DELETE":
            return httpx.Response(204)
        if path.endswith("/agentguard/activity"):
            return httpx.Response(200, json={"summary": {}, "destinations": []})
        return httpx.Response(404, json={})

    cfg = config_module.Config(api_url=BASE, auth_token="t", workspace="ws")
    monkeypatch.setattr(config_module.Config, "save", lambda self, path=None: None)
    client = CoreRunClient(cfg)
    client._client = httpx.Client(transport=httpx.MockTransport(handle), base_url=BASE)
    monkeypatch.setattr(config_module, "_client", client)
    monkeypatch.setattr("corerun.init", lambda *a, **k: None, raising=False)
    return seen


def test_asks_and_the_waiting_filter(wire):
    assert agents.asks(pending=True)[0]["title"] == "Use send_mail"
    assert wire[-1].url.params["status"] == "pending"
    agents.asks()
    assert "status" not in wire[-1].url.params


def test_the_workspace_activity_names_one_agent_when_asked(wire):
    agents.workspace_activity(since="7d", agent_id="ag-1", timeline=True)
    params = wire[-1].url.params
    assert (params["since"], params["agent"], params["view"]) == ("7d", "ag-1", "timeline")


def test_the_cli_lists_and_stops_sandboxes(wire):
    from corerun.cli import app

    runner = CliRunner()
    out = runner.invoke(app, ["agents", "sandboxes"])
    assert out.exit_code == 0, out.output
    assert "Tutor" in out.output and "1 in use" in out.output
    out = runner.invoke(app, ["agents", "sandbox-stop", "nb-1"])
    assert out.exit_code == 0 and wire[-1].method == "DELETE" and wire[-1].url.path.endswith("/agent-sandboxes/nb-1")
    out = runner.invoke(app, ["agents", "asks", "--waiting"])
    assert out.exit_code == 0 and "Use send_mail" in out.output


def test_a_server_in_your_network_is_added_through_its_compute(monkeypatch):
    sent = []

    def handle(request: httpx.Request) -> httpx.Response:
        import json as _json

        sent.append(_json.loads(request.content))
        return httpx.Response(201, json={"id": "c1", "name": "wiki", "location": "network", "via": "gb10", "tools": []})

    cfg = config_module.Config(api_url=BASE, auth_token="t", workspace="ws")
    client = CoreRunClient(cfg)
    client._client = httpx.Client(transport=httpx.MockTransport(handle), base_url=BASE)
    monkeypatch.setattr(config_module, "_client", client)
    monkeypatch.setattr("corerun.init", lambda *a, **k: None, raising=False)
    from corerun.cli import app

    runner = CliRunner()
    out = runner.invoke(app, ["connectors", "add", "wiki", "--url", "http://wiki.internal/mcp", "--via", "gb10"])
    assert out.exit_code == 0, out.output
    assert sent[-1]["location"] == "network" and sent[-1]["via"] == "gb10"
    runner.invoke(app, ["connectors", "add", "wiki", "--url", "http://wiki.internal/mcp", "--direct"])
    assert sent[-1]["location"] == "network" and sent[-1]["via"] == ""
    out = runner.invoke(app, ["connectors", "add", "wiki", "--url", "http://x/mcp", "--via", "gb10", "--direct"])
    assert out.exit_code != 0
