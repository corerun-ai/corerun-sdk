"""
What an agent was told by the CLI while deploying a model on 24 Sep 2026, and
was wrong to believe: a workspace with no GPU limit shown as having none to
spare, a declined delete that exited 0, a list of engine types with no types
in it. Each test pins the true answer.
"""

import json
import uuid

import httpx
import pytest
from typer.testing import CliRunner

from corerun import config as config_module
from corerun.cli import app
from corerun.cli import inference as inference_cli
from corerun.cli import quota as quota_cli
from corerun.cli.inference import _edit_args
from corerun.client import CoreRunClient

runner = CliRunner()
BASE = "https://example.test/api/v1"
SERVER = str(uuid.uuid4())


@pytest.fixture
def wire(monkeypatch):
    state = {"seen": []}

    def handle(request: httpx.Request) -> httpx.Response:
        state["seen"].append(request)
        path = request.url.path
        if path.endswith("/quota"):
            return httpx.Response(200, json=state["quota"])
        if path.endswith("/inference-servers/types"):
            return httpx.Response(200, json={"types": [
                {"name": "vllm", "description": "LLMs"},
                {"name": "triton", "description": "Everything else"},
            ]})
        if request.method == "DELETE":
            return httpx.Response(200, json={"message": "deleted"})
        return httpx.Response(200, json={})

    client = CoreRunClient(config_module.Config(api_url=BASE))
    client._client = httpx.Client(transport=httpx.MockTransport(handle), base_url=BASE)
    monkeypatch.setattr(quota_cli, "_init_client", lambda: None)
    monkeypatch.setattr(inference_cli, "_init_client", lambda: None)
    monkeypatch.setattr(config_module, "_client", client)
    return state


def quota(gpu_limit, servers=(0, 1)):
    return {
        "jobs": {"used": 0, "limit": -1},
        "notebooks": {"used": 0, "limit": -1},
        "inference_servers": {"used": servers[0], "limit": servers[1]},
        "gpus": {"used": 1, "limit": gpu_limit},
        "storage_gb": {"used": 3, "limit": 50},
    }


@pytest.mark.parametrize("limit", [0, -1])
def test_no_gpu_limit_is_unlimited_and_never_at_limit(wire, limit):
    # 0 is what a server before the sentinel was settled sent for a limit the
    # plan does not set; -1 is what it sends now. Neither is a ceiling of none.
    wire["quota"] = quota(limit)
    result = runner.invoke(app, ["quota", "show"])
    assert result.exit_code == 0, result.output
    assert "unlimited" in result.output
    assert "At limit" not in result.output
    assert "1 / 0" not in result.output


def test_a_real_limit_that_is_full_is_still_reported(wire):
    wire["quota"] = quota(2, servers=(1, 1))
    result = runner.invoke(app, ["quota", "show"])
    assert "At limit" in result.output and "Inference servers" in result.output
    assert "GPUs" not in result.output.split("At limit")[1]


def test_a_delete_with_no_terminal_to_confirm_on_fails_and_deletes_nothing(wire):
    result = runner.invoke(app, ["inference", "delete", SERVER])
    assert result.exit_code == 2
    assert "--yes" in result.output
    assert not [r for r in wire["seen"] if r.method == "DELETE"]


def test_yes_deletes_without_asking(wire):
    result = runner.invoke(app, ["inference", "delete", SERVER, "--yes"])
    assert result.exit_code == 0, result.output
    assert [r for r in wire["seen"] if r.method == "DELETE"]


def test_types_lists_the_types_it_accepts(wire):
    result = runner.invoke(app, ["inference", "types"])
    assert result.exit_code == 0, result.output
    assert "vllm" in result.output and "triton" in result.output


def test_editing_engine_args_keeps_the_rest():
    current = ["--kv-cache-dtype", "fp8", "--enable-auto-tool-choice", "--speculative-config", '{"method":"mtp"}']
    # Replace a flag in place, add one, remove one with its value.
    got = _edit_args(current, ["--kv-cache-dtype", "auto", "--max-num-seqs", "16"], ["--speculative-config"])
    assert got == ["--kv-cache-dtype", "auto", "--enable-auto-tool-choice", "--max-num-seqs", "16"]
    assert _edit_args(current, [], ["kv-cache-dtype"])[:1] == ["--enable-auto-tool-choice"]


def test_capacity_says_how_many_fit(monkeypatch):
    from corerun.cli import endpoints as endpoints_cli
    import corerun.endpoints as endpoints

    monkeypatch.setattr(endpoints_cli, "_init_client", lambda: None)
    monkeypatch.setattr(endpoints, "capacity", lambda name, model, workspace=None: {
        "kind": "deployment", "context_window": 32768,
        "capacity": {"kv_cache_tokens": 420285, "kv_cache_gib": 15.49, "max_model_len": 32768,
                     "max_concurrency": 12.8, "weights_gib": 22.12},
    })
    result = runner.invoke(app, ["endpoints", "capacity", "chat", "-m", "q",
                                 "--shape", "512/256", "--shape", "131072/1024"])
    assert result.exit_code == 0, result.output
    assert "~547 requests at once" in result.output
    assert "longer than the 32,768-token context" in result.output


def test_scope_needs_exactly_one_destination():
    result = runner.invoke(app, ["clusters", "scope", "box"])
    assert result.exit_code == 2


def test_a_new_token_is_printed_whole(monkeypatch):
    """A token is printed on one line, whatever the width.

    Rich wraps at the terminal's edge -- at 80 columns when the output is a
    pipe or a file -- and a token captured in pieces does not work: it was
    found by a script that saved `corerun tokens create` to a file and got
    401s for a token cut after 78 characters.
    """
    from types import SimpleNamespace

    from corerun import tokens as tokens_api
    from corerun.cli import tokens as tokens_cli

    secret = "eyJhbGciOiJFUzI1NiJ9." + "a" * 900 + "." + "b" * 86
    monkeypatch.setattr(tokens_cli, "_init_client", lambda: None)
    monkeypatch.setattr(tokens_api, "create", lambda *a, **k: SimpleNamespace(
        name="ci", key_id="k1", scope_list=["jobs:read"], workspace_id="w1",
        expires_at="2026-09-26", key=secret))
    result = runner.invoke(app, ["tokens", "create", "ci", "--scopes", "jobs:read", "--otel"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert any(line.strip() == secret for line in lines), "the token is not on one line"
    assert any(line.strip().endswith(f'Authorization=Bearer {secret}"') for line in lines), \
        "the exporter's header line is broken"
