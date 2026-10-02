"""`corerun org agents`: the organisation's agent sandbox settings. The server
replaces them whole, so `set` must merge into what it read."""

import json

import httpx
import pytest
from typer.testing import CliRunner

from corerun import config as config_module
from corerun.cli import app
from corerun.cli import org_admin
from corerun.client import CoreRunClient

runner = CliRunner()
BASE = "https://example.test/api/v1"
CURRENT = {
    "default_idle_minutes": 15,
    "max_idle_minutes": 60,
    "max_ready_per_agent": 2,
    "ready_per_organization": None,
    "installation": {"idle_minutes": 10, "ready_per_organization": 10, "max_idle_minutes": 240, "max_ready_per_agent": 5},
}


@pytest.fixture
def wire(monkeypatch):
    sent = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            body = json.loads(request.read())
            sent.append(body)
            return httpx.Response(200, json={**body, "installation": CURRENT["installation"]})
        return httpx.Response(200, json=CURRENT)

    client = CoreRunClient(config_module.Config(api_url=BASE))
    client._client = httpx.Client(transport=httpx.MockTransport(handle), base_url=BASE)
    monkeypatch.setattr(config_module, "_client", client)
    import corerun.org as org

    monkeypatch.setattr(org_admin, "_org", lambda: org)
    return sent


def test_set_keeps_what_it_does_not_name(wire):
    result = runner.invoke(app, ["org", "agents", "set", "--ready-per-org", "6"])
    assert result.exit_code == 0, result.output
    assert wire[-1] == {"default_idle_minutes": 15, "max_idle_minutes": 60, "max_ready_per_agent": 2, "ready_per_organization": 6}


def test_minus_one_goes_back_to_the_default(wire):
    result = runner.invoke(app, ["org", "agents", "set", "--ready-per-agent", "-1", "--idle-default", "0"])
    assert result.exit_code == 0, result.output
    assert wire[-1]["max_ready_per_agent"] is None and wire[-1]["default_idle_minutes"] == 0


def test_set_needs_something_to_set(wire):
    result = runner.invoke(app, ["org", "agents", "set"])
    assert result.exit_code == 1 and not wire


def test_show(wire):
    result = runner.invoke(app, ["org", "agents", "show"])
    assert result.exit_code == 0, result.output
    assert "15 min" in result.output and "installation's" in result.output
