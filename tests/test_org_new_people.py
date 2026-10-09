"""Whether workspaces may invite new people: the organisation's switch, from the SDK and the CLI."""

import json

import httpx
import pytest
from typer.testing import CliRunner

from corerun import config as config_module
from corerun import org
from corerun.client import CoreRunClient

BASE = "https://example.test/api/v1"


@pytest.fixture
def wire(monkeypatch):
    seen = []
    state = {"invite_new_people": True}

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/tenant/me"):
            if request.method == "PUT":
                state.update(json.loads(request.content))
            return httpx.Response(200, json={"id": "t1", "display_name": "Acme", **state})
        return httpx.Response(404, json={})

    cfg = config_module.Config(api_url=BASE, auth_token="t", workspace="ws")
    monkeypatch.setattr(config_module.Config, "save", lambda self, path=None: None)
    client = CoreRunClient(cfg)
    client._client = httpx.Client(transport=httpx.MockTransport(handle), base_url=BASE)
    monkeypatch.setattr(config_module, "_client", client)
    return seen


def test_the_switch_is_sent_alone(wire):
    assert org.set_invite_new_people(False)["invite_new_people"] is False
    assert wire[-1].method == "PUT" and json.loads(wire[-1].content) == {"invite_new_people": False}


def test_the_cli_reads_and_sets_it(wire, monkeypatch):
    from corerun.cli import app

    monkeypatch.setattr("corerun.init", lambda *a, **k: None, raising=False)
    runner = CliRunner()
    out = runner.invoke(app, ["org", "members", "new-people", "off"])
    assert out.exit_code == 0, out.output
    assert "only people already in the organisation" in out.output
    assert json.loads(wire[-1].content) == {"invite_new_people": False}
    out = runner.invoke(app, ["org", "members", "new-people"])
    assert out.exit_code == 0 and "only people already" in out.output and wire[-1].method == "GET"
    assert runner.invoke(app, ["org", "members", "new-people", "maybe"]).exit_code != 0
