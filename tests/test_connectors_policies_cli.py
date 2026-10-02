"""`corerun connectors`, `connections`, `policies` and `clusters
notebook-creators`: what each sends, and what it makes of the answer."""

import json

import httpx
import pytest
from typer.testing import CliRunner

from corerun import config as config_module
from corerun.cli import clusters as clusters_cli
from corerun.cli import connectors as connectors_cli
from corerun.cli import policies as policies_cli
from corerun.client import CoreRunClient

runner = CliRunner()
BASE = "https://example.test/api/v1"

GITHUB = {
    "id": "c1", "name": "github", "kind": "mcp", "url": "https://api.githubcopilot.com/mcp/",
    "auth": "oauth", "has_secret": False, "scope": "workspace", "tools": [{"name": "search"}],
    "connected": False, "created_at": "2026-10-02T00:00:00Z",
}
PROD = {
    "id": "c2", "name": "prod", "kind": "kubernetes", "url": "https://10.0.0.1:6443",
    "auth": "kubeconfig", "has_secret": True, "scope": "organization", "tools": [],
    "context": "prod-admin", "server_version": "v1.33.1", "created_at": "2026-10-02T00:00:00Z",
}
CAREFUL = {
    "id": "p1", "name": "careful", "level": "workspace", "enabled": True,
    "rules": [{"name": "ask-writes", "effect": "ask", "match": {"action": ["create", "delete"]}}],
    "updated_at": "2026-10-02T00:00:00Z",
}


@pytest.fixture
def wire(monkeypatch):
    seen = []
    answers = {}

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        for (method, suffix), answer in answers.items():
            if request.method == method and request.url.path.endswith(suffix):
                return answer(request) if callable(answer) else httpx.Response(200, json=answer)
        return httpx.Response(404, json={"error": "not_found", "message": f"nothing at {request.url.path}"})

    transport = httpx.MockTransport(handle)
    client = CoreRunClient(config_module.Config(api_url=BASE))
    client._client = httpx.Client(transport=transport, base_url=BASE)
    monkeypatch.setattr(config_module, "_client", client)
    for module in (connectors_cli, policies_cli, clusters_cli):
        monkeypatch.setattr(module, "_init_client", lambda: None)
    return seen, answers


def _body(request):
    return json.loads(request.read() or b"null")


def _sent(seen, method, suffix):
    found = [r for r in seen if r.method == method and r.url.path.endswith(suffix)]
    assert found, f"no {method} {suffix} among {[(r.method, r.url.path) for r in seen]}"
    return found[-1]


def _app():
    from corerun.cli import app
    return app


# ── connectors ───────────────────────────────────────────────────────────────


def test_a_cluster_is_added_from_its_kubeconfig(wire, tmp_path):
    seen, answers = wire
    answers[("POST", "/connectors")] = PROD
    kubeconfig = tmp_path / "prod.yaml"
    kubeconfig.write_text("apiVersion: v1\nkind: Config\n")
    result = runner.invoke(_app(), ["connectors", "add", "prod", "--kubeconfig", str(kubeconfig), "--context", "prod-admin"])
    assert result.exit_code == 0, result.output
    assert _body(_sent(seen, "POST", "/api/v1/connectors")) == {
        "name": "prod", "kind": "kubernetes", "kubeconfig": "apiVersion: v1\nkind: Config\n", "context": "prod-admin",
    }
    assert "Kubernetes v1.33.1" in result.output


def test_an_mcp_server_with_headers_and_the_org_flag(wire):
    seen, answers = wire
    answers[("POST", "/tenant/shared/connectors")] = {**GITHUB, "auth": "headers", "scope": "organization"}
    result = runner.invoke(_app(), [
        "connectors", "add", "search", "--url", "https://mcp.example.com", "--auth", "headers",
        "--header", "X-Api-Key=abc=def", "--org",
    ])
    assert result.exit_code == 0, result.output
    body = _body(_sent(seen, "POST", "/tenant/shared/connectors"))
    assert body["headers"] == {"X-Api-Key": "abc=def"} and body["kind"] == "mcp" and body["auth"] == "headers"


def test_add_needs_an_address_or_a_kubeconfig_and_sends_nothing_without(wire):
    seen, _ = wire
    result = runner.invoke(_app(), ["connectors", "add", "nothing"])
    assert result.exit_code == 1
    assert not seen


def test_update_finds_the_connector_by_name(wire):
    seen, answers = wire
    answers[("GET", "/connectors")] = {"connectors": [GITHUB, PROD], "total": 2}
    answers[("PUT", "/connectors/c1")] = {**GITHUB, "description": "code"}
    result = runner.invoke(_app(), ["connectors", "update", "GitHub", "-d", "code"])
    assert result.exit_code == 0, result.output
    assert _body(_sent(seen, "PUT", "/connectors/c1")) == {"description": "code"}


def test_a_failed_check_exits_one(wire):
    _, answers = wire
    answers[("GET", "/connectors")] = {"connectors": [GITHUB], "total": 1}
    answers[("POST", "/connectors/c1/test")] = {"ok": False, "message": "401 from the server", "connector": GITHUB}
    result = runner.invoke(_app(), ["connectors", "test", "github"])
    assert result.exit_code == 1
    assert "401 from the server" in result.output


def test_list_in_json(wire):
    _, answers = wire
    answers[("GET", "/connectors")] = {"connectors": [GITHUB, PROD], "total": 2}
    result = runner.invoke(_app(), ["--json", "connectors", "list"])
    assert result.exit_code == 0, result.output
    assert [c["name"] for c in json.loads(result.stdout)] == ["github", "prod"]


# ── connections ──────────────────────────────────────────────────────────────


def test_connect_gives_the_sign_in_address(wire):
    seen, answers = wire
    answers[("GET", "/connectors")] = {"connectors": [GITHUB], "total": 1}
    answers[("POST", "/connections/c1")] = {"authorize_url": "https://github.com/login/oauth/authorize?x=1", "connector_id": "c1"}
    result = runner.invoke(_app(), ["connections", "connect", "github", "--no-browser"])
    assert result.exit_code == 0, result.output
    assert "https://github.com/login/oauth/authorize?x=1" in result.output


# ── policies ─────────────────────────────────────────────────────────────────


def test_a_policy_is_created_from_yaml(wire, tmp_path):
    seen, answers = wire
    answers[("POST", "/policies")] = CAREFUL
    rules = tmp_path / "careful.yaml"
    rules.write_text("rules:\n  - name: ask-writes\n    effect: ask\n    match:\n      action: [create, delete]\n")
    result = runner.invoke(_app(), ["policies", "create", "careful", "-f", str(rules)])
    assert result.exit_code == 0, result.output
    assert _body(_sent(seen, "POST", "/api/v1/policies")) == {
        "name": "careful", "enabled": True,
        "rules": [{"name": "ask-writes", "effect": "ask", "match": {"action": ["create", "delete"]}}],
    }


@pytest.mark.parametrize("text, says", [
    ("- name: x\n  effect: maybe\n", "effect must be one of"),
    ("- name: x\n  effect: allow\n  match:\n    verb: [get]\n", "unknown field 'verb'"),
    ("- name: x\n  effect: allow\n  match:\n    action: []\n", "at least one glob"),
])
def test_a_bad_rule_is_named_before_anything_is_sent(wire, tmp_path, text, says):
    seen, _ = wire
    rules = tmp_path / "bad.yaml"
    rules.write_text(text)
    result = runner.invoke(_app(), ["policies", "create", "bad", "-f", str(rules)])
    assert result.exit_code == 1
    assert says in result.output
    assert not seen


def test_disable_sends_only_that(wire):
    seen, answers = wire
    answers[("GET", "/tenant/shared/policies")] = {"policies": [{**CAREFUL, "level": "organization"}]}
    answers[("PUT", "/tenant/shared/policies/p1")] = {**CAREFUL, "enabled": False}
    result = runner.invoke(_app(), ["policies", "update", "careful", "--disable", "--org"])
    assert result.exit_code == 0, result.output
    assert _body(_sent(seen, "PUT", "/tenant/shared/policies/p1")) == {"enabled": False}


def test_get_prints_rules_that_create_reads_back(wire, tmp_path):
    seen, answers = wire
    answers[("GET", "/policies")] = {"policies": [CAREFUL]}
    result = runner.invoke(_app(), ["policies", "get", "careful"])
    assert result.exit_code == 0, result.output
    import corerun.policies as policies

    assert policies.load_rules(result.output) == CAREFUL["rules"]


# ── clusters notebook-creators ───────────────────────────────────────────────


def test_notebook_creators(wire):
    seen, answers = wire
    answers[("PUT", "/clusters/dgx/notebook-creators")] = {"name": "dgx", "notebook_creator_roles": ["admin", "analyst"]}
    result = runner.invoke(_app(), ["clusters", "notebook-creators", "dgx", "admin,analyst,admin"])
    assert result.exit_code == 0, result.output
    assert _body(_sent(seen, "PUT", "/clusters/dgx/notebook-creators")) == {"roles": ["admin", "analyst"]}
    assert "admin, analyst" in result.output


def test_notebook_creators_refuses_a_role_that_is_not_one(wire):
    seen, _ = wire
    result = runner.invoke(_app(), ["clusters", "notebook-creators", "dgx", "admin,owner"])
    assert result.exit_code == 1
    assert "owner" in result.output
    assert not seen
