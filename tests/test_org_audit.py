"""`corerun org audit`: what goes on the wire, and what a person or a script sees."""

import json

import httpx
import pytest
from typer.testing import CliRunner

from corerun import config as config_module
from corerun import org
from corerun.cli import app
from corerun.cli import org as org_cli
from corerun.client import CoreRunClient

runner = CliRunner()
BASE = "https://example.test/api/v1"

EVENT = {
    "id": "e1", "at": "2026-09-27T10:00:00Z", "actor_email": "ana@example.test", "action": "access.denied",
    "target": "DELETE /api/v1/notebooks/n1", "status": 403, "outcome": "denied", "reason": "model_denied",
    "source_ip": "203.0.113.7", "source": "gatekeeper",
}
EXPORT = {"id": "x1", "name": "splunk", "kind": "webhook", "address": "https://splunk.example.test/hec", "enabled": True}


@pytest.fixture
def wire(monkeypatch):
    seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path.endswith("/tenant/audit") and request.url.params.get("format") == "csv":
            return httpx.Response(200, text="at,actor_email,action\n2026-09-27T10:00:00Z,ana@example.test,access.denied\n",
                                  headers={"Content-Type": "text/csv"})
        if path.endswith("/tenant/audit"):
            return httpx.Response(200, json={"events": [EVENT], "next_before": ""})
        if path.endswith("/tenant/audit/agents"):
            return httpx.Response(200, json={"agents": [
                {"agent_id": "a-1", "name": "Support bot", "workspace_id": "w1", "workspace_name": "Support"},
                {"agent_id": "a-2", "name": "Tutor", "workspace_id": "w2", "workspace_name": "School"},
                {"agent_id": "a-3", "name": "Tutor", "workspace_id": "w3", "workspace_name": "Home"},
            ]})
        if path.endswith("/tenant/audit/exports") and request.method == "GET":
            return httpx.Response(200, json={"exports": [EXPORT]})
        if path.endswith("/tenant/audit/exports"):
            return httpx.Response(201, json=EXPORT)
        if path.endswith("/test"):
            return httpx.Response(200, json={"delivered": False, "message": "connection refused"})
        return httpx.Response(200, json={})

    client = CoreRunClient(config_module.Config(api_url=BASE))
    client._client = httpx.Client(transport=httpx.MockTransport(handle), base_url=BASE)
    monkeypatch.setattr(org_cli, "_init_client", lambda: None)
    monkeypatch.setattr(config_module, "_client", client)
    return seen


def test_the_trail_is_asked_for_with_only_the_filters_given(wire):
    result = runner.invoke(app, ["--json", "org", "audit", "--since", "7d", "--actor", "ana", "--outcome", "denied"])
    assert result.exit_code == 0, result.output
    params = wire[-1].url.params
    assert (params["since"], params["actor"], params["outcome"]) == ("7d", "ana", "denied")
    assert "action" not in params
    assert json.loads(result.output)["events"][0]["reason"] == "model_denied"


def test_a_destination_named_is_addressed_by_its_id(wire):
    org.set_audit_export_enabled("splunk", False)
    sent = wire[-1]
    assert sent.method == "PUT" and sent.url.path.endswith("/tenant/audit/exports/x1")
    assert json.loads(sent.content) == {"enabled": False}


def test_a_test_that_did_not_arrive_fails(wire):
    result = runner.invoke(app, ["org", "audit", "test", "splunk"])
    assert result.exit_code == 1
    assert "connection refused" in result.output


def test_the_token_is_read_from_the_environment_not_the_command_line(wire, monkeypatch):
    monkeypatch.setenv("HEC_TOKEN", "s3cret")
    result = runner.invoke(app, ["org", "audit", "add", "splunk", "--kind", "webhook",
                                 "--address", "https://splunk.example.test/hec", "--token-env", "HEC_TOKEN"])
    assert result.exit_code == 0, result.output
    body = json.loads(wire[-1].content)
    assert body["token"] == "s3cret" and body["kind"] == "webhook"


def test_a_destination_is_added_with_its_format_and_categories(wire):
    result = runner.invoke(app, [
        "org", "audit", "add", "soc", "--kind", "webhook",
        "--address", "https://siem.example.test/hec",
        "--format", "ocsf", "--category", "agent_network", "--category", "gateway",
    ])
    assert result.exit_code == 0, result.output
    body = json.loads(wire[-1].content)
    assert body["format"] == "ocsf" and body["categories"] == ["agent_network", "gateway"]


def test_what_a_destination_is_sent_is_changed_by_name(wire):
    result = runner.invoke(app, ["org", "audit", "set", "splunk", "--category", "agent_network"])
    assert result.exit_code == 0, result.output
    put = wire[-1]
    assert put.method == "PUT" and put.url.path.endswith("/tenant/audit/exports/x1")
    assert json.loads(put.content) == {"categories": ["agent_network"]}
    everything = ["org", "audit", "set", "splunk", "--everything", "--format", "json"]
    result = runner.invoke(app, everything)
    assert result.exit_code == 0, result.output
    assert json.loads(wire[-1].content) == {"format": "json", "categories": []}
    assert runner.invoke(app, ["org", "audit", "set", "splunk"]).exit_code != 0


def test_the_agents_events_are_asked_for_by_category_and_by_the_agents_name(wire):
    result = runner.invoke(app, ["--json", "org", "audit", "--category", "agents", "--agent", "support bot"])
    assert result.exit_code == 0, result.output
    params = wire[-1].url.params
    assert params["category"] == ("agent_network,agent_tools,agent_approvals,agent_files,agent_sandboxes,agent_changes")
    assert params["agent"] == "a-1"


def test_an_agents_name_two_agents_share_is_refused(wire):
    result = runner.invoke(app, ["org", "audit", "--agent", "Tutor"])
    assert result.exit_code == 1
    assert "a-2 (School)" in result.output and "a-3 (Home)" in result.output


def test_the_trail_is_written_as_csv(wire, tmp_path):
    out = tmp_path / "audit.csv"
    result = runner.invoke(app, ["org", "audit", "-c", "signin", "--csv", str(out)])
    assert result.exit_code == 0, result.output
    assert wire[-1].url.params["format"] == "csv" and wire[-1].url.params["category"] == "signin"
    assert out.read_text().startswith("at,actor_email,action")
    assert "Wrote 1 events" in result.output
