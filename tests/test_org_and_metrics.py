"""
What the organisation, upstream and metrics commands put on the wire, and how
a metrics answer reads.

The Go handlers bind pointer fields -- absent means "keep" -- so what matters
is which keys are sent at all. And an organisation's security policy is
replaced whole by the server, so `org security set` must merge into what it
read, or naming one setting would silently clear the others.
"""

import json
import math

import httpx
import pytest
from rich.console import Console
from typer.testing import CliRunner

from corerun import config as config_module
from corerun import endpoints as endpoints_api
from corerun import inference as inference_api
from corerun.cli import app, metrics_view
from corerun.cli import endpoints as endpoints_cli
from corerun.cli import org as org_cli
from corerun.cli import prices as prices_cli
from corerun.client import CoreRunClient
from corerun.http import describe_body

runner = CliRunner()
BASE = "https://example.test/api/v1"

INSTALLATION = {
    "tokens": {
        "access_ttl": "1h",
        "refresh_ttl": "720h",
        "service_max_ttl": "8760h",
        "require_service_expiry": False,
    },
    "sessions": {"browser_ttl": "24h"},
    "sign_in": {"lockout": {"attempts": 10, "window": "15m"}},
    "passwords": {"min_length": 12},
}


SERVER = {
    "id": "srv-1",
    "name": "nova",
    "status": "running",
    "server_type": "vllm",
    "model_source": "huggingface",
    "model_id": "org/nova",
    "compute_name": "gpu",
    "image": "vllm:latest",
    "owner_id": "u-1",
}


PROVIDERS = [
    {
        "key": "gemini",
        "name": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "priced_as": "gemini",
        "models": 80,
    },
]
LISTED = {
    "provider": "gemini",
    "model": "gemini-2.5-pro",
    "price": {"input": 1.25, "output": 10},
    "as_of": "2026-09-24T00:00:00Z",
    "source": "refresh",
}


@pytest.fixture
def wire(monkeypatch):
    """Answer locally, keeping every request actually built."""
    state = {"seen": [], "policy": {"tokens": {"access_ttl": "30m"}}, "version": 3}

    def handle(request: httpx.Request) -> httpx.Response:
        state["seen"].append(request)
        if request.url.path.endswith("/model-prices/providers"):
            return httpx.Response(200, json={"providers": PROVIDERS})
        if request.url.path.endswith("/model-prices") and request.method == "GET":
            return httpx.Response(200, json={"prices": [LISTED]})
        if request.url.path.endswith("/tenant/model-prices"):
            return httpx.Response(
                200, json={"prices": []} if request.method == "GET" else {"ok": True}
            )
        if request.url.path.endswith("/tenant/security-policy"):
            if request.method == "PUT":
                return httpx.Response(200, json={"version": state["version"] + 1})
            return httpx.Response(
                200,
                json={
                    "policy": state["policy"],
                    "version": state["version"],
                    "effective": INSTALLATION,
                    "installation": INSTALLATION,
                },
            )
        return httpx.Response(200, json=SERVER)

    client = CoreRunClient(config_module.Config(api_url=BASE))
    client._client = httpx.Client(transport=httpx.MockTransport(handle), base_url=BASE)
    monkeypatch.setattr(org_cli, "_init_client", lambda: None)
    monkeypatch.setattr(endpoints_cli, "_init_client", lambda: None)
    monkeypatch.setattr(prices_cli, "_init_client", lambda: None)
    monkeypatch.setattr(config_module, "_client", client)
    return state


def _body(request):
    return json.loads(request.content)


def test_editing_an_upstream_sends_only_what_changes(wire):
    endpoints_api.update_upstream("nova", "gpt-4o", upstream_name="gpt-4o-2024-08-06")
    request = wire["seen"][-1]
    assert request.method == "PATCH"
    assert request.url.path.endswith("/inference-endpoints/nova/upstreams/gpt-4o")
    # An absent api_key keeps the stored one; a sent null or "" would not.
    assert _body(request) == {"upstream_name": "gpt-4o-2024-08-06"}


def test_editing_an_upstream_with_nothing_named_is_refused_before_the_wire(wire):
    with pytest.raises(ValueError):
        endpoints_api.update_upstream("nova", "gpt-4o")
    assert wire["seen"] == []


def test_inference_update_sends_memory_share_and_served_names(wire):
    inference_api.update("srv-1", gpu_memory_util=0.85, served_model_names=["nova", "nova-latest"])
    body = _body(wire["seen"][-1])
    assert body["gpu_memory_util"] == 0.85
    assert body["served_model_names"] == ["nova", "nova-latest"]
    assert "image" not in body and "extra_args" not in body


def test_metrics_asks_for_the_range(wire):
    endpoints_api.metrics("nova", range="7d")
    request = wire["seen"][-1]
    assert request.url.path.endswith("/inference-endpoints/nova/metrics")
    assert request.url.params["range"] == "7d"


def test_security_set_merges_into_what_the_organisation_had(wire):
    result = runner.invoke(
        app,
        [
            "org",
            "security",
            "set",
            "--lockout-attempts",
            "5",
            "--deny-country",
            "kp",
            "--allow-network",
            "10.0.0.0/8",
        ],
    )
    assert result.exit_code == 0, result.output
    put = wire["seen"][-1]
    assert put.method == "PUT"
    body = _body(put)
    assert body["version"] == 3, "the write is pinned to the version read"
    assert body["policy"] == {
        "tokens": {"access_ttl": "30m"},  # kept, though not named
        "lockout": {"attempts": 5},
        "networks": {"allow": ["10.0.0.0/8"], "countries": {"deny": ["KP"]}},
    }


def test_security_set_with_nothing_named_writes_nothing(wire):
    result = runner.invoke(app, ["org", "security", "set"])
    assert result.exit_code == 1
    assert all(r.method == "GET" for r in wire["seen"])


def test_security_clear_sends_no_policy(wire):
    result = runner.invoke(app, ["org", "security", "set", "--clear"])
    assert result.exit_code == 0, result.output
    assert _body(wire["seen"][-1]) == {"policy": None, "version": 3}


def test_security_show_says_who_set_what(wire):
    result = runner.invoke(app, ["org", "security", "show"])
    assert result.exit_code == 0, result.output
    assert "this organisation" in result.output and "installation" in result.output


def test_a_cloudflare_challenge_is_named_rather_than_quoted():
    req = httpx.Request("POST", "https://console.example/api/v1/oauth/token")
    page = b"<!DOCTYPE html><html><head><title>Just a moment...</title></head></html>"
    by_header = httpx.Response(
        403,
        content=page,
        headers={"cf-mitigated": "challenge", "content-type": "text/html"},
        request=req,
    )
    by_title = httpx.Response(403, content=page, headers={"content-type": "text/html"}, request=req)
    for response in (by_header, by_title):
        message = describe_body(response)
        assert "Cloudflare" in message
        assert "<html" not in message.lower()


def test_metrics_latencies_are_weighted_by_requests_and_idle_minutes_do_not_count():
    answer = {
        "step_seconds": 60,
        "advanced": False,
        "retention_days": 30,
        "servers": [
            {
                "model": "nova",
                "kind": "upstream",
                "points": [
                    {
                        "requests": 3,
                        "failed": 1,
                        "prompt_tokens": 100,
                        "completion_tokens": 50,
                        "ttft_p50": 0.2,
                        "ttft_p90": 0.4,
                        "ttft_p99": 0.5,
                        "e2e_p50": 1.0,
                        "itl_p50": 0.02,
                    },
                    # A minute with no requests reports 0 latencies; it must not
                    # drag the average down.
                    {"requests": 0, "failed": 0, "ttft_p50": 0, "e2e_p50": 0, "itl_p50": 0},
                ],
            }
        ],
    }
    assert math.isclose(metrics_view._weighted(answer["servers"][0]["points"], "ttft_p50"), 0.2)
    console = Console(width=200, record=True)
    console.print(metrics_view.table(answer, "t"))
    text = console.export_text()
    assert "nova" in text and "upstream" in text
    assert "200 ms" in text and "50.0 tok/s" in text
    assert "100 / 50" in text
    caption = metrics_view.caption(answer)
    assert "kept 30 days" in caption and "Advanced plan" in caption


def test_metrics_with_no_traffic_show_dashes():
    console = Console(width=200, record=True)
    console.print(metrics_view.table({"servers": [{"model": "idle", "points": []}]}, "t"))
    assert "—" in console.export_text()


# --- prices -------------------------------------------------------------


def test_a_model_is_priced_by_its_name_with_the_slash_kept_in_one_segment(wire):
    endpoints_api.price(
        "chat", "deepseek-ai/deepseek-flash", input=0.27, output=1.1, cache_read=0.07
    )
    request = wire["seen"][-1]
    assert request.method == "PUT"
    # The API routes on the raw path: an unencoded slash would be two segments
    # and match no route at all.
    assert request.url.raw_path.decode().endswith(
        "/inference-endpoints/chat/pricing/deepseek-ai%2Fdeepseek-flash"
    )
    # An unset cache price is left out, not sent as null or zero: left out, the
    # server charges cached tokens as input.
    assert _body(request) == {"input": 0.27, "output": 1.1, "cache_read": 0.07}


def test_clearing_a_price_and_editing_an_upstream_encode_the_name_too(wire):
    endpoints_api.clear_price("chat", "org/model")
    assert wire["seen"][-1].method == "DELETE"
    assert wire["seen"][-1].url.raw_path.decode().endswith("/pricing/org%2Fmodel")
    endpoints_api.update_upstream("chat", "org/model", upstream_name="x")
    assert wire["seen"][-1].url.raw_path.decode().endswith("/upstreams/org%2Fmodel")


def test_an_upstream_can_be_priced_as_it_is_added(wire):
    result = runner.invoke(
        app,
        [
            "endpoints",
            "add-upstream",
            "chat",
            "--model",
            "gpt-4o",
            "--base-url",
            "https://api.openai.com/v1",
            "--price-input",
            "2.5",
            "--price-output",
            "10",
        ],
    )
    assert result.exit_code == 0, result.output
    body = _body(wire["seen"][-1])
    assert body["pricing"] == {"input": 2.5, "output": 10.0}


def test_an_upstream_added_without_a_price_sends_none(wire):
    endpoints_api.add_upstream("chat", model="m", base_url="https://x/v1")
    assert "pricing" not in _body(wire["seen"][-1])


def test_the_price_command_needs_input_and_output(wire):
    result = runner.invoke(app, ["endpoints", "price", "chat", "gpt-4o", "--input", "2.5"])
    assert result.exit_code == 1
    assert wire["seen"] == []
    result = runner.invoke(
        app, ["endpoints", "price", "chat", "gpt-4o", "--input", "-1", "--output", "2"]
    )
    assert result.exit_code == 1


def test_the_price_command_sets_and_clears(wire):
    result = runner.invoke(
        app,
        [
            "endpoints",
            "price",
            "chat",
            "gpt-4o",
            "--input",
            "2.5",
            "--output",
            "10",
            "--cache-read",
            "1.25",
        ],
    )
    assert result.exit_code == 0, result.output
    assert _body(wire["seen"][-1]) == {"input": 2.5, "output": 10.0, "cache_read": 1.25}
    result = runner.invoke(app, ["endpoints", "price", "chat", "gpt-4o", "--clear"])
    assert result.exit_code == 0, result.output
    assert wire["seen"][-1].method == "DELETE"


def test_metrics_show_what_calls_cost_and_say_when_nothing_is_priced():
    answer = {
        "servers": [
            {
                "model": "priced",
                "kind": "upstream",
                "pricing": {"input": 1, "output": 2},
                "points": [
                    {
                        "requests": 2,
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "cache_read_tokens": 4,
                        "cost": 0.5,
                    }
                ],
            },
            {"model": "free", "kind": "server", "points": [{"requests": 1, "prompt_tokens": 1}]},
        ]
    }
    console = Console(width=240, record=True)
    console.print(metrics_view.table(answer, "t"))
    text = console.export_text()
    assert "$0.50" in text
    assert "not priced" in text
    assert metrics_view.dollars(0.00123) == "$0.0012"


# --- the price list and the organisation's rates --------------------------


def test_prices_search_asks_under_the_name_the_list_prices_it(wire):
    result = runner.invoke(app, ["prices", "search", "gemini", "2.5-pro"])
    assert result.exit_code == 0, result.output
    asked = [r for r in wire["seen"] if r.url.path.endswith("/model-prices")][-1]
    assert asked.url.params["provider"] == "gemini"
    assert asked.url.params["q"] == "2.5-pro"
    assert "gemini-2.5-pro" in result.output and "$1.25 in" in result.output


def test_an_upstream_added_by_provider_sends_no_base_url(wire):
    endpoints_api.add_upstream("chat", model="gemini-2.5-pro", provider="gemini")
    body = _body(wire["seen"][-1])
    # The server fills in the provider's address; an empty one is not a
    # mistake to be caught here.
    assert body["provider"] == "gemini" and body["base_url"] == ""


def test_an_organisation_rate_is_sent_as_a_price_and_cleared_by_name(wire):
    result = runner.invoke(
        app, ["org", "prices", "set", "openai", "gpt-4o", "--input", "2", "--output", "8"]
    )
    assert result.exit_code == 0, result.output
    put = wire["seen"][-1]
    assert put.method == "PUT" and put.url.path.endswith("/tenant/model-prices")
    assert _body(put) == {
        "provider": "openai",
        "model": "gpt-4o",
        "price": {"input": 2.0, "output": 8.0},
    }

    result = runner.invoke(app, ["org", "prices", "clear", "openai", "gpt-4o"])
    assert result.exit_code == 0, result.output
    gone = wire["seen"][-1]
    assert gone.method == "DELETE"
    assert gone.url.params["provider"] == "openai" and gone.url.params["model"] == "gpt-4o"
