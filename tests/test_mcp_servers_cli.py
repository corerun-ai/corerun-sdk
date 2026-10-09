"""`corerun mcp-servers`: what each command sends, and what it makes of the answer."""

import json

import httpx
import pytest
from typer.testing import CliRunner

from corerun import config as config_module
from corerun.cli import mcp_servers as servers_cli
from corerun.client import CoreRunClient

runner = CliRunner()
BASE = "https://example.test/api/v1"

OPS = [
    {
        "name": "list_invoices",
        "api": "api",
        "method": "GET",
        "path": "/invoices",
        "changes": False,
        "default": "allow",
    },
    {
        "name": "get_invoice",
        "api": "api",
        "method": "GET",
        "path": "/invoices/{id}",
        "changes": False,
        "default": "allow",
        "params": [{"name": "id", "in": "path", "required": True}],
    },
    {
        "name": "release_invoice",
        "api": "api",
        "method": "POST",
        "path": "/invoices/{id}/release",
        "changes": True,
        "default": "ask",
        "params": [{"name": "id", "in": "path", "required": True}],
    },
]
SERVER = {"id": "s1", "name": "ERP", "running": 0, "draft": 0, "apis": [], "tools": []}


@pytest.fixture
def wire(monkeypatch):
    seen = []
    answers = {}

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        for (method, suffix), answer in answers.items():
            if request.method == method and request.url.path.endswith(suffix):
                return answer(request) if callable(answer) else httpx.Response(200, json=answer)
        return httpx.Response(
            404, json={"error": "not_found", "message": f"nothing at {request.url.path}"}
        )

    client = CoreRunClient(config_module.Config(api_url=BASE))
    client._client = httpx.Client(transport=httpx.MockTransport(handle), base_url=BASE)
    monkeypatch.setattr(config_module, "_client", client)
    monkeypatch.setattr(servers_cli, "_init_client", lambda: None)
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


def test_a_server_is_made_from_a_description_with_its_reads(wire):
    seen, answers = wire
    answers[("POST", "/mcp-servers/read")] = {
        "title": "SAP Invoices",
        "base_url": "https://erp.example.com/v2",
        "operations": OPS,
    }
    answers[("POST", "/mcp-servers")] = SERVER
    answers[("POST", "/mcp-servers/s1/apis")] = {
        "id": "a1",
        "name": "sap_invoices",
        "base_url": "https://erp.example.com/v2",
    }
    answers[("PUT", "/mcp-servers/s1/draft")] = {**SERVER, "draft": 1, "draft_tools": OPS[:2]}
    result = runner.invoke(
        _app(),
        [
            "mcp-servers",
            "create",
            "ERP",
            "--spec",
            "https://erp.example.com/openapi.json",
            "--reads",
            "--auth",
            "bearer",
            "--token",
            "t0k",
            "--via",
            "gb10dgx01-host",
        ],
    )
    assert result.exit_code == 0, result.output
    assert _body(_sent(seen, "POST", "/mcp-servers/read")) == {
        "api": "api",
        "spec_url": "https://erp.example.com/openapi.json",
    }
    api = _body(_sent(seen, "POST", "/mcp-servers/s1/apis"))
    assert api == {
        "name": "sap_invoices",
        "base_url": "https://erp.example.com/v2",
        "location": "network",
        "auth": "bearer",
        "spec_url": "https://erp.example.com/openapi.json",
        "via": "gb10dgx01-host",
        "token": "t0k",
    }
    draft = _body(_sent(seen, "PUT", "/mcp-servers/s1/draft"))
    assert [t["name"] for t in draft["tools"]] == ["list_invoices", "get_invoice"]
    assert {t["api"] for t in draft["tools"]} == {"sap_invoices"}
    assert "draft v1" in result.output


def test_an_operation_the_description_lacks_is_refused_and_nothing_made(wire):
    seen, answers = wire
    answers[("POST", "/mcp-servers/read")] = {
        "title": "x",
        "base_url": "https://erp.example.com",
        "operations": OPS,
    }
    result = runner.invoke(
        _app(),
        [
            "mcp-servers",
            "create",
            "ERP",
            "--spec",
            "https://erp.example.com/o.json",
            "--tools",
            "nope",
        ],
    )
    assert result.exit_code == 1
    assert not [r for r in seen if r.method == "POST" and r.url.path.endswith("/mcp-servers")]


def test_a_tool_is_called_published_and_rolled_back(wire):
    seen, answers = wire
    answers[("GET", "/mcp-servers")] = {"servers": [{**SERVER, "draft": 1}]}
    answers[("GET", "/mcp-servers/s1")] = {**SERVER, "draft": 1, "draft_tools": OPS}
    answers[("POST", "/mcp-servers/s1/call")] = {
        "version": 1,
        "tool": "list_invoices",
        "ms": 12,
        "result": {"content": [{"type": "text", "text": '[{"id":"7"}]'}], "isError": False},
    }
    answers[("POST", "/mcp-servers/s1/publish")] = {**SERVER, "running": 1}
    answers[("POST", "/mcp-servers/s1/versions/1/restore")] = {**SERVER, "running": 3}
    result = runner.invoke(
        _app(), ["mcp-servers", "call", "erp", "list_invoices", "--args", '{"status": "open"}']
    )
    assert result.exit_code == 0, result.output
    assert _body(_sent(seen, "POST", "/mcp-servers/s1/call")) == {
        "tool": "list_invoices",
        "arguments": {"status": "open"},
    }
    assert '[{"id":"7"}]' in result.output
    assert runner.invoke(_app(), ["mcp-servers", "publish", "ERP"]).exit_code == 0
    result = runner.invoke(_app(), ["mcp-servers", "rollback", "ERP", "1"])
    assert result.exit_code == 0 and "v3 is v1's tools again" in result.output


def test_an_api_error_answer_exits_one(wire):
    _, answers = wire
    answers[("GET", "/mcp-servers")] = {"servers": [SERVER]}
    answers[("GET", "/mcp-servers/s1")] = SERVER
    answers[("POST", "/mcp-servers/s1/call")] = {
        "version": 1,
        "tool": "x",
        "ms": 3,
        "result": {"content": [{"type": "text", "text": "erp answered 403"}], "isError": True},
    }
    result = runner.invoke(_app(), ["mcp-servers", "call", "ERP", "x"])
    assert result.exit_code == 1 and "403" in result.output


CATALOGUE = {
    "servers": [
        {
            "id": "postgres",
            "name": "Postgres",
            "publisher": "Crystal DBA",
            "image": "docker.io/crystaldba/postgres-mcp:0.3.0",
            "fields": [{"key": "password", "label": "Password", "secret": True, "required": True}],
        }
    ]
}
IMAGE_SERVER = {
    "id": "s2",
    "name": "Sales",
    "kind": "image",
    "running": 0,
    "draft": 1,
    "apis": [],
    "tools": [],
    "image": {
        "ref": "docker.io/crystaldba/postgres-mcp:0.3.0",
        "command": ["postgres-mcp"],
        "transport": "stdio",
        "compute": "datacore",
    },
}
LIVE = [
    {
        "name": "list_schemas",
        "api": "",
        "method": "",
        "path": "",
        "changes": False,
        "default": "allow",
        "input_schema": {"type": "object"},
    },
    {
        "name": "execute_sql",
        "api": "",
        "method": "",
        "path": "",
        "changes": True,
        "default": "ask",
        "input_schema": {"type": "object"},
    },
]


def test_a_catalogue_server_is_run_from_its_fields(wire):
    seen, answers = wire
    answers[("GET", "/mcp-servers/catalogue")] = CATALOGUE
    answers[("POST", "/mcp-servers")] = IMAGE_SERVER
    result = runner.invoke(
        _app(),
        [
            "mcp-servers",
            "run",
            "Sales",
            "--image",
            "postgres",
            "--on",
            "datacore",
            "--field",
            "host=db.internal",
            "--field",
            "password=p@ss",
            "--size",
            "medium",
        ],
    )
    assert result.exit_code == 0, result.output
    assert _body(_sent(seen, "POST", "/mcp-servers")) == {
        "name": "Sales",
        "kind": "image",
        "image": {
            "catalogue": "postgres",
            "fields": {"host": "db.internal", "password": "p@ss"},
            "compute": "datacore",
            "size": "medium",
        },
    }
    assert "starting on datacore" in result.output


def test_your_own_image_runs_with_its_settings_and_hosts(wire):
    seen, answers = wire
    answers[("GET", "/mcp-servers/catalogue")] = CATALOGUE
    answers[("POST", "/mcp-servers")] = IMAGE_SERVER
    result = runner.invoke(
        _app(),
        [
            "mcp-servers",
            "run",
            "Orders",
            "--image",
            "ghcr.io/acme/orders-mcp:1.4",
            "--command",
            "orders-mcp --stdio",
            "--on",
            "datacore",
            "--set",
            "LOG=info",
            "--secret",
            "API_TOKEN=t0k",
            "--host",
            "api.acme.com:443",
        ],
    )
    assert result.exit_code == 0, result.output
    image = _body(_sent(seen, "POST", "/mcp-servers"))["image"]
    assert image == {
        "image": "ghcr.io/acme/orders-mcp:1.4",
        "command": ["orders-mcp", "--stdio"],
        "transport": "stdio",
        "settings": [
            {"name": "LOG", "value": "info", "secret": False},
            {"name": "API_TOKEN", "value": "t0k", "secret": True},
        ],
        "compute": "datacore",
        "size": "small",
        "hosts": ["api.acme.com:443"],
    }
    result = runner.invoke(
        _app(),
        [
            "mcp-servers",
            "run",
            "Orders",
            "--image",
            "ghcr.io/acme/orders-mcp:1.4",
            "--on",
            "datacore",
        ],
    )
    assert result.exit_code != 0 and "--command" in result.output


def test_an_image_servers_tools_are_chosen_from_what_it_lists(wire):
    seen, answers = wire
    answers[("GET", "/mcp-servers")] = {"servers": [IMAGE_SERVER]}
    answers[("GET", "/mcp-servers/s2")] = IMAGE_SERVER
    answers[("GET", "/mcp-servers/s2/versions/1/tools")] = {"version": 1, "tools": LIVE}
    answers[("PUT", "/mcp-servers/s2/draft")] = {**IMAGE_SERVER, "draft_tools": LIVE}
    result = runner.invoke(
        _app(),
        [
            "mcp-servers",
            "choose",
            "Sales",
            "--reads",
            "--tools",
            "execute_sql",
            "--allow",
            "execute_sql",
        ],
    )
    assert result.exit_code == 0, result.output
    tools = _body(_sent(seen, "PUT", "/mcp-servers/s2/draft"))["tools"]
    assert [(t["name"], t["default"]) for t in tools] == [
        ("list_schemas", "allow"),
        ("execute_sql", "allow"),
    ]
    result = runner.invoke(_app(), ["mcp-servers", "choose", "Sales", "--tools", "drop_database"])
    assert result.exit_code != 0 and "drop_database" in result.output


def test_reads_from_a_server_that_marks_none_is_refused_and_nothing_saved(wire):
    # Postgres's server marks no tool read-only: every one is a change.
    seen, answers = wire
    unmarked = [{**t, "changes": True, "default": "ask"} for t in LIVE]
    answers[("GET", "/mcp-servers")] = {"servers": [IMAGE_SERVER]}
    answers[("GET", "/mcp-servers/s2")] = IMAGE_SERVER
    answers[("GET", "/mcp-servers/s2/versions/1/tools")] = {"version": 1, "tools": unmarked}
    result = runner.invoke(_app(), ["mcp-servers", "choose", "Sales", "--reads"])
    assert result.exit_code != 0 and "--tools" in result.output, result.output
    assert not [r for r in seen if r.method == "PUT"]


def test_get_shows_what_each_tool_starts_as(wire):
    # The setting is printed in brackets, which Rich would take for markup.
    seen, answers = wire
    running = {**IMAGE_SERVER, "running": 1, "draft": 0, "tools": LIVE}
    answers[("GET", "/mcp-servers")] = {"servers": [running]}
    answers[("GET", "/mcp-servers/s2")] = running
    result = runner.invoke(_app(), ["mcp-servers", "get", "Sales"])
    assert result.exit_code == 0, result.output
    assert "list_schemas  [allow]" in result.output and "execute_sql  [ask]" in result.output
    erp = {**SERVER, "running": 1, "tools": OPS}
    answers[("GET", "/mcp-servers")] = {"servers": [erp]}
    answers[("GET", "/mcp-servers/s1")] = erp
    result = runner.invoke(_app(), ["mcp-servers", "get", "ERP"])
    assert result.exit_code == 0, result.output
    assert "/invoices/{id}/release  [ask]" in result.output, result.output
