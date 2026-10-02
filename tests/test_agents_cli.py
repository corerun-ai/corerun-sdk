"""`corerun agents`: what each command sends, and what it makes of the answer.

The platform's own refusals are its business; what is checked here is the part
the CLI decides -- that an update does not blank what it was not told to
change, that a name becomes the id the API wants, that a reply is printed as
it streams and a handover is said rather than swallowed.
"""

import json

import httpx
import pytest
from typer.testing import CliRunner

from corerun import config as config_module
from corerun.cli import agents as agents_cli
from corerun.client import CoreRunClient

runner = CliRunner()
BASE = "https://console.example.test/api/v1"
AGENT_ID = "7c1d2e3f-4a5b-4c6d-8e7f-9a0b1c2d3e4f"
CONV = "c0ffee00-0000-4000-8000-000000000001"
SAM = "5a4e0000-0000-4000-8000-0000000000aa"
GITHUB = "91b00000-0000-4000-8000-0000000000bb"
CLUSTER = "91b00000-0000-4000-8000-0000000000cc"

AGENT = {
    "agent_id": AGENT_ID, "name": "support-bot", "description": "Answers support questions",
    "harness": "opencode", "instructions": "Be kind.", "compute_name": "gb10", "workspace_id": "w1",
    "created_by": "u1", "may_manage": True, "ready_sandboxes": 0,
}


@pytest.fixture
def wire(monkeypatch):
    seen = []
    answers = {}

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        # Longest suffix first, so /agents/<id>/access is not answered as /agents/<id>.
        for (method, suffix), answer in sorted(answers.items(), key=lambda kv: -len(kv[0][1])):
            if request.method == method and path.endswith(suffix):
                return answer(request) if callable(answer) else httpx.Response(200, json=answer)
        return httpx.Response(404, json={"error": "not_found", "message": f"nothing at {request.method} {path}"})

    config = config_module.Config(api_url=BASE)
    client = CoreRunClient(config)
    client._client = httpx.Client(transport=httpx.MockTransport(handle), base_url=BASE)
    monkeypatch.setattr(config_module, "_client", client)
    monkeypatch.setattr(config_module, "_config", config)
    monkeypatch.setattr(agents_cli, "_init_client", lambda: None)
    answers[("GET", "/agents")] = {"agents": [AGENT], "may_build": True}
    answers[("GET", f"/agents/{AGENT_ID}")] = AGENT
    return seen, answers


def _body(seen, method, suffix):
    request = [r for r in seen if r.method == method and r.url.path.endswith(suffix)][-1]
    return json.loads(request.content) if request.content else None


def test_create_sends_what_was_given(wire, tmp_path):
    seen, answers = wire
    answers[("POST", "/agents")] = lambda r: httpx.Response(201, json=AGENT)
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Be kind.\n")
    result = runner.invoke(agents_cli.app, [
        "create", "support-bot", "--compute", "gb10", "-d", "Answers support questions",
        "--instructions-file", str(prompt), "--starter", "How do refunds work?",
    ])
    assert result.exit_code == 0, result.output
    assert _body(seen, "POST", "/agents") == {
        "name": "support-bot", "compute_name": "gb10", "description": "Answers support questions",
        "instructions": "Be kind.\n", "starters": ["How do refunds work?"],
    }


def test_update_keeps_what_it_was_not_told_to_change(wire):
    seen, answers = wire
    answers[("PUT", f"/agents/{AGENT_ID}")] = lambda r: httpx.Response(200, json=AGENT)
    result = runner.invoke(agents_cli.app, ["update", "support-bot", "--ready", "2"])
    assert result.exit_code == 0, result.output
    body = _body(seen, "PUT", f"/agents/{AGENT_ID}")
    # The platform overwrites both with whatever arrives, empty included.
    assert body["description"] == "Answers support questions"
    assert body["instructions"] == "Be kind."
    assert body["ready"] == 2


def test_grant_turns_an_email_into_the_person_s_id(wire):
    seen, answers = wire
    access = {
        "people": [{"user_id": SAM, "email": "sam@example.com", "access": "chat"}],
        "groups": [], "invites": [], "everyone": False, "workspace": "support", "available_groups": [],
    }
    answers[("GET", f"/agents/{AGENT_ID}/access")] = access
    answers[("PUT", f"/agents/{AGENT_ID}/access")] = access
    result = runner.invoke(agents_cli.app, ["grant", "support-bot", "Sam@Example.com", "edit"])
    assert result.exit_code == 0, result.output
    assert _body(seen, "PUT", "/access") == {"kind": "user", "id": SAM, "access": "edit"}


def test_grant_to_someone_not_listed_invites_them(wire):
    seen, answers = wire
    answers[("GET", f"/agents/{AGENT_ID}/access")] = {"people": [], "groups": [], "available_groups": []}
    answers[("POST", f"/agents/{AGENT_ID}/invite")] = {"status": "invited", "email": "new@example.com"}
    result = runner.invoke(agents_cli.app, ["grant", "support-bot", "new@example.com", "chat"])
    assert result.exit_code == 0, result.output
    assert _body(seen, "POST", "/invite") == {"email": "new@example.com"}
    assert "Invited" in result.output


def test_tools_set_names_connectors_and_their_tools(wire):
    seen, answers = wire
    answers[("GET", "/connectors")] = {"connectors": [
        {"id": GITHUB, "name": "GitHub", "kind": "mcp"},
        {"id": CLUSTER, "name": "prod", "kind": "kubernetes"},
    ]}
    answers[("PUT", f"/agents/{AGENT_ID}/tools")] = {"connectors": []}
    result = runner.invoke(agents_cli.app, [
        "tools-set", "support-bot", "--connector", "github", "--connector", "prod",
        "--tool-policy", "github:search_issues=allow", "--tool-policy", "GitHub:create_issue=ask",
    ])
    assert result.exit_code == 0, result.output
    assert _body(seen, "PUT", "/tools") == {"connectors": [
        {"connector_id": GITHUB, "policies": {"search_issues": "allow", "create_issue": "ask"}},
        {"connector_id": CLUSTER, "policies": {}},
    ]}


def test_tools_set_refuses_tool_policies_on_a_cluster(wire):
    seen, answers = wire
    answers[("GET", "/connectors")] = {"connectors": [{"id": CLUSTER, "name": "prod", "kind": "kubernetes"}]}
    result = runner.invoke(agents_cli.app, ["tools-set", "support-bot", "--connector", "prod", "--tool-policy", "get=allow"])
    assert result.exit_code == 1
    assert not [r for r in seen if r.method == "PUT"]


def test_policy_set_reads_rules_from_yaml(wire, tmp_path):
    seen, answers = wire
    answers[("PUT", f"/agents/{AGENT_ID}/policy")] = lambda r: httpx.Response(
        200, json={"rules": json.loads(r.content)["rules"], "default": False}
    )
    rules = tmp_path / "rules.yaml"
    rules.write_text(
        "rules:\n"
        "  - name: reads\n    effect: allow\n    match: {action: [get, list, watch]}\n"
        "  - name: deploys\n    effect: ask\n    match: {action: [create, patch], namespace: [apps]}\n"
    )
    result = runner.invoke(agents_cli.app, ["policy-set", "support-bot", "-f", str(rules)])
    assert result.exit_code == 0, result.output
    sent = _body(seen, "PUT", "/policy")["rules"]
    assert [r["name"] for r in sent] == ["reads", "deploys"]
    assert sent[1]["match"] == {"action": ["create", "patch"], "namespace": ["apps"]}


def test_policy_set_default_sends_no_rules(wire):
    seen, answers = wire
    answers[("PUT", f"/agents/{AGENT_ID}/policy")] = {"rules": [], "default": True}
    result = runner.invoke(agents_cli.app, ["policy-set", "support-bot", "--default"])
    assert result.exit_code == 0, result.output
    assert _body(seen, "PUT", "/policy") == {"rules": []}
    assert "default" in result.output


def _sse(*events):
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events).encode()


def test_chat_prints_the_answer_as_it_streams(wire):
    seen, answers = wire
    answers[("POST", f"/agents/{AGENT_ID}/conversations")] = lambda r: httpx.Response(201, json={"conversation_id": CONV})
    answers[("POST", f"/conversations/{CONV}/reply")] = lambda r: httpx.Response(
        200, headers={"content-type": "text/event-stream"},
        content=_sse({"delta": "Refunds are "}, {"delta": "within 30 days."}, {"done": True, "title": "Refunds"}),
    )
    result = runner.invoke(agents_cli.app, ["chat", "support-bot", "How do refunds work?"])
    assert result.exit_code == 0, result.output
    assert "Refunds are within 30 days." in result.output
    assert _body(seen, "POST", "/reply") == {"text": "How do refunds work?"}


def test_chat_says_when_the_question_is_handed_over(wire):
    seen, answers = wire
    answers[("POST", f"/conversations/{CONV}/reply")] = lambda r: httpx.Response(
        200, headers={"content-type": "text/event-stream"},
        content=_sse({"delta": "Let me look at the cluster."}, {"handover": True}),
    )
    result = runner.invoke(agents_cli.app, ["chat", "support-bot", "What is failing in prod?", "--conversation", CONV])
    assert result.exit_code == 0, result.output
    assert "sandbox and tools" in result.output
    assert f"https://console.example.test/agents/{AGENT_ID}" in result.output
    # An existing conversation is continued, not replaced by a new one.
    assert not [r for r in seen if r.method == "POST" and r.url.path.endswith("/conversations")]


def test_upload_sends_the_file_s_bytes_with_its_type(wire, tmp_path):
    seen, answers = wire
    answers[("GET", f"/agents/{AGENT_ID}/projects")] = {"projects": [
        {"project_id": "p-q3", "name": "Q3 refunds"}, {"project_id": "general", "name": "General"},
    ]}
    answers[("PUT", "/projects/p-q3/files/notes/policy.pdf")] = {"path": "notes/policy.pdf", "kind": "file"}
    local = tmp_path / "policy.pdf"
    local.write_bytes(b"%PDF-1.7 bytes")
    result = runner.invoke(agents_cli.app, [
        "upload", "support-bot", str(local), "--project", "Q3 refunds", "--as", "notes/policy.pdf",
    ])
    assert result.exit_code == 0, result.output
    request = [r for r in seen if r.method == "PUT"][-1]
    assert request.content == b"%PDF-1.7 bytes"
    assert request.headers["content-type"] == "application/pdf"


def test_list_as_json(wire):
    result = runner.invoke(agents_cli.app, ["list", "--json"])
    assert result.exit_code == 0, result.output
    listed = json.loads(result.stdout)
    assert listed[0]["agent_id"] == AGENT_ID and listed[0]["name"] == "support-bot"


def test_a_refusal_is_said_not_thrown(wire):
    seen, answers = wire
    answers[("DELETE", f"/agents/{AGENT_ID}")] = lambda r: httpx.Response(
        403, json={"error": "forbidden", "message": "Only this agent's creator, or an admin of the workspace"}
    )
    result = runner.invoke(agents_cli.app, ["delete", "support-bot", "--yes"])
    assert result.exit_code == 1
    assert "creator" in result.output and "Traceback" not in result.output
