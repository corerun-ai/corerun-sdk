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
    answers[("PUT", f"/agentguard/agents/{AGENT_ID}/policy")] = lambda r: httpx.Response(
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
    answers[("PUT", f"/agentguard/agents/{AGENT_ID}/policy")] = {"rules": [], "default": True}
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


# ── the network ──────────────────────────────────────────────────────────────


def test_refused_destinations_are_listed_with_the_agents_mode(wire):
    seen, answers = wire
    answers[("GET", f"/agents/{AGENT_ID}")] = {**AGENT, "network": "report"}
    answers[("GET", f"/agentguard/agents/{AGENT_ID}/network/refused")] = {"destinations": [
        {"host": "example.com", "port": "443", "times": 3, "last": "2026-10-05T09:12:00Z"},
        {"host": "registry.npmjs.org", "port": "443", "times": 1,
         "last": "2026-10-05T08:00:00Z", "would": True},
    ]}
    result = runner.invoke(agents_cli.app, ["network", "support-bot"])
    assert result.exit_code == 0, result.output
    assert "reported, not enforced" in result.output
    assert "example.com:443" in result.output and "registry.npmjs.org:443" in result.output


def test_allowing_a_destination_keeps_the_agents_other_rules(wire):
    seen, answers = wire
    own = [{"name": "Python packages", "effect": "allow",
            "match": {"host": ["pypi.org"], "port": ["443"]}}]
    answers[("GET", f"/agentguard/agents/{AGENT_ID}/policy")] = {"rules": own, "default": False}
    answers[("PUT", f"/agentguard/agents/{AGENT_ID}/policy")] = lambda r: httpx.Response(
        200, json=json.loads(r.content))
    result = runner.invoke(agents_cli.app, ["network-allow", "support-bot", "Example.com:8443"])
    assert result.exit_code == 0, result.output
    put = [r for r in seen if r.method == "PUT" and r.url.path.endswith("/policy")][-1]
    assert put.url.params.get("kind") == "network"
    assert json.loads(put.content)["rules"] == own + [
        {"name": "example.com", "effect": "allow",
         "match": {"host": ["example.com"], "port": ["8443"]}},
    ]


def test_a_destination_already_allowed_is_not_added_twice(wire):
    seen, answers = wire
    own = [{"name": "pypi.org", "effect": "allow",
            "match": {"host": ["pypi.org"], "port": ["443"]}}]
    answers[("GET", f"/agentguard/agents/{AGENT_ID}/policy")] = {"rules": own, "default": False}
    result = runner.invoke(agents_cli.app, ["network-allow", "support-bot", "pypi.org"])
    assert result.exit_code == 0, result.output
    assert not [r for r in seen if r.method == "PUT"]


def test_enforcing_the_network_is_sent_and_nothing_else_is_blanked(wire):
    seen, answers = wire
    answers[("PUT", f"/agents/{AGENT_ID}")] = lambda r: httpx.Response(
        200, json={**AGENT, "network": "enforce"})
    result = runner.invoke(agents_cli.app, ["update", "support-bot", "--enforce-network"])
    assert result.exit_code == 0, result.output
    body = _body(seen, "PUT", f"/agents/{AGENT_ID}")
    assert body["network"] == "enforce" and body["instructions"] == "Be kind."


def test_allowing_a_host_a_rule_refuses_changes_that_rule(wire):
    seen, answers = wire
    own = [
        {"name": "api.github.com", "effect": "deny",
         "match": {"host": ["api.github.com"], "port": ["443"]}},
        {"name": "Python packages", "effect": "ask",
         "match": {"host": ["pypi.org", "files.pythonhosted.org"], "port": ["443"]}},
    ]
    answers[("GET", f"/agentguard/agents/{AGENT_ID}/policy")] = {"rules": own, "default": False}
    answers[("PUT", f"/agentguard/agents/{AGENT_ID}/policy")] = lambda r: httpx.Response(
        200, json=json.loads(r.content))
    result = runner.invoke(agents_cli.app, ["network-allow", "support-bot", "pypi.org"])
    assert result.exit_code == 0, result.output
    rules = _body(seen, "PUT", f"/agentguard/agents/{AGENT_ID}/policy")["rules"]
    assert rules == [
        own[0],
        {"name": "Python packages", "effect": "ask",
         "match": {"host": ["files.pythonhosted.org"], "port": ["443"]}},
        {"name": "pypi.org", "effect": "allow", "match": {"host": ["pypi.org"], "port": ["443"]}},
    ]


def test_allowing_a_host_a_pattern_still_refuses_is_refused(wire):
    seen, answers = wire
    own = [{"name": "no GitHub", "effect": "deny", "match": {"host": ["*.github.com"]}}]
    answers[("GET", f"/agentguard/agents/{AGENT_ID}/policy")] = {"rules": own, "default": False}
    result = runner.invoke(agents_cli.app, ["network-allow", "support-bot", "api.github.com"])
    assert result.exit_code != 0
    assert "no GitHub" in result.output
    assert not [r for r in seen if r.method == "PUT"]


def test_what_no_rule_matches_gets_is_shown_and_set(wire):
    seen, answers = wire
    path = f"/agentguard/agents/{AGENT_ID}/network/unmatched"
    answers[("GET", path)] = {"effect": "", "effective": "ask",
                              "levels": {"organization": "ask", "workspace": "", "agent": ""}}
    answers[("PUT", path)] = lambda r: httpx.Response(200, json={
        "effect": json.loads(r.content)["effect"], "effective": "deny",
        "levels": {"organization": "ask", "workspace": "", "agent": "deny"}})
    result = runner.invoke(agents_cli.app, ["network-unmatched", "support-bot"])
    assert result.exit_code == 0, result.output
    assert "is ask" in result.output and "organization ask" in result.output
    result = runner.invoke(agents_cli.app, ["network-unmatched", "support-bot", "deny"])
    assert result.exit_code == 0, result.output
    assert _body(seen, "PUT", path) == {"effect": "deny"}
    runner.invoke(agents_cli.app, ["network-unmatched", "support-bot", "none"])
    assert _body(seen, "PUT", path) == {"effect": ""}
    result = runner.invoke(agents_cli.app, ["network-unmatched", "support-bot", "maybe"])
    assert result.exit_code != 0


def test_an_agents_activity_by_destination(wire):
    seen, answers = wire
    answers[("GET", f"/agentguard/agents/{AGENT_ID}/activity")] = {
        "summary": {"connections": 1200, "refused": 3, "asked": 1, "bytes_out": 2048, "bytes_in": 5 * 1024 * 1024},
        "destinations": [
            {"host": "pypi.org", "port": "443", "reached": 1200, "refused": 0, "asked": 0, "approved": 0,
             "refused_by_person": 0, "would_refuse": 0, "bytes_out": 2048, "bytes_in": 5242880,
             "last": "2026-10-06T09:41:00Z", "decided_by": "agent:Python packages"},
            {"host": "r.jina.ai", "port": "443", "reached": 0, "refused": 3, "asked": 0, "approved": 0,
             "refused_by_person": 0, "would_refuse": 0, "bytes_out": 0, "bytes_in": 0,
             "last": "2026-10-06T09:13:00Z", "decided_by": "policy", "workaround": "a known relay"},
        ],
        "people": [],
    }
    result = runner.invoke(agents_cli.app, ["activity", "support-bot", "--since", "7d"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    asked = [r for r in seen if r.url.path.endswith("/activity")][-1]
    assert asked.url.params["since"] == "7d" and asked.url.params["view"] == "network"
    assert "1200 connections, 3 refused" in result.output
    assert "pypi.org:443" in result.output and "looks like a workaround" in result.output


def test_an_agents_timeline_is_asked_for_by_category(wire, tmp_path):
    seen, answers = wire
    answers[("GET", f"/agentguard/agents/{AGENT_ID}/activity")] = lambda r: (
        httpx.Response(200, text="at,action\n2026-10-06T09:00:00Z,tool.kubernetes.delete\n")
        if r.url.params.get("format") == "csv" else httpx.Response(200, json={"events": [], "people": []}))
    result = runner.invoke(agents_cli.app, ["activity", "support-bot", "--timeline", "-c", "agent_tools", "-c", "agent_changes"])
    assert result.exit_code == 0, result.output
    asked = [r for r in seen if r.url.path.endswith("/activity")][-1]
    assert asked.url.params["view"] == "timeline" and asked.url.params["category"] == "agent_tools,agent_changes"
    out = tmp_path / "a.csv"
    result = runner.invoke(agents_cli.app, ["activity", "support-bot", "--timeline", "--csv", str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_text().startswith("at,action")


def test_publish_as_itself_sends_service(wire):
    seen, answers = wire
    answers[("PUT", f"/agents/{AGENT_ID}/publication")] = {
        "published": True, "mode": "service", "enabled": True, "answers_as": "a1",
    }
    result = runner.invoke(agents_cli.app, ["publish", "support-bot"])
    assert result.exit_code == 0, result.output
    assert _body(seen, "PUT", "/publication") == {"mode": "service"}
    assert "answers as itself" in result.output


def test_publish_as_someone_turns_their_email_into_their_id(wire):
    seen, answers = wire
    answers[("GET", f"/agents/{AGENT_ID}/access")] = {
        "people": [{"user_id": SAM, "email": "sam@example.com", "access": "chat"}],
    }
    answers[("PUT", f"/agents/{AGENT_ID}/publication")] = {
        "published": True, "mode": "delegate", "enabled": True,
        "delegate_email": "sam@example.com", "waiting": "delegate_confirmation",
    }
    result = runner.invoke(agents_cli.app, ["publish", "support-bot", "--as", "Sam@example.com"])
    assert result.exit_code == 0, result.output
    assert _body(seen, "PUT", "/publication") == {"mode": "delegate", "delegate_id": SAM}
    assert "waiting for them" in result.output


def test_publish_as_somebody_without_the_agent_is_refused_before_asking(wire):
    seen, answers = wire
    answers[("GET", f"/agents/{AGENT_ID}/access")] = {"people": []}
    result = runner.invoke(agents_cli.app, ["publish", "support-bot", "--as", "nobody@example.com"])
    assert result.exit_code != 0
    assert "cannot use" in result.output
    assert not [r for r in seen if r.method == "PUT"]


def test_unpublish(wire):
    seen, answers = wire
    answers[("DELETE", f"/agents/{AGENT_ID}/publication")] = lambda r: httpx.Response(204)
    result = runner.invoke(agents_cli.app, ["unpublish", "support-bot"])
    assert result.exit_code == 0, result.output
    assert "unpublished" in result.output
