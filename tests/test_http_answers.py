"""What a command says when the platform's answer is not what it expected.

A deploy in progress answers with an empty body or a gateway's page, a stale
session with a 401 and nothing else. Both used to reach the user as a JSON
decoder's complaint. Every answer now goes through one decoder, and the CLI's
request helper refreshes an expired session once before giving up.
"""

import httpx
import pytest

from corerun.cli import http as cli_http
from corerun.exceptions import AuthenticationError, CoreRunError, ServerError
from corerun.http import RETRY_HINT, SIGN_IN_HINT, decode_response


def answer(status, body=b"", content_type=None, reason=None):
    headers = {"content-type": content_type} if content_type else {}
    req = httpx.Request("GET", "https://console.example/api/v1/workspaces")
    return httpx.Response(status, content=body, headers=headers, request=req)


def test_a_success_that_is_not_json_says_so():
    with pytest.raises(CoreRunError) as caught:
        decode_response(answer(200, b"<!doctype html><html><head><title>Console</title></head></html>", "text/html"))
    message = str(caught.value)
    assert "not with JSON" in message and "text/html" in message and "titled 'Console'" in message
    assert RETRY_HINT in message


def test_an_empty_success_is_an_empty_document():
    assert decode_response(answer(200)) == {}
    assert decode_response(answer(204)) == {}


def test_a_bare_401_tells_the_person_to_sign_in():
    with pytest.raises(AuthenticationError) as caught:
        decode_response(answer(401))
    assert str(caught.value) == SIGN_IN_HINT


def test_a_401_with_a_reason_keeps_it_and_adds_the_hint():
    with pytest.raises(AuthenticationError) as caught:
        decode_response(answer(401, b'{"error":"invalid_token","message":"token expired"}', "application/json"))
    assert str(caught.value) == f"token expired. {SIGN_IN_HINT}"


def test_a_gateway_page_is_named_not_pasted():
    with pytest.raises(ServerError) as caught:
        decode_response(answer(502, b"<html><body><h1>502 Bad Gateway</h1></body></html>", "text/html"))
    message = str(caught.value)
    assert "not answering" in message and "an HTML page" in message and RETRY_HINT in message


def test_the_cli_helper_refreshes_an_expired_session_once(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers.get("authorization"))
        if request.url.path.endswith("/auth/token/refresh"):
            return httpx.Response(200, json={"access_token": "fresh", "refresh_token": "next"})
        if request.headers.get("authorization") == "Bearer stale":
            return httpx.Response(401)
        return httpx.Response(200, json={"workspaces": [{"slug": "ws1"}]})

    monkeypatch.setattr(cli_http, "transport", httpx.MockTransport(handler))

    class Cfg:
        api_url = "https://console.example/api/v1"
        auth_token = "stale"
        refresh_token = "rt"
        timeout = 5
        verify_ssl = True
        auth_token_file = None
        workspace = None
        saved = False

        def save(self):
            Cfg.saved = True

    cfg = Cfg()
    # The refresh goes through the SDK client, which posts with the module's
    # httpx; point it at the same fake.
    import corerun.client as client_module

    def fake_post(url, **kw):
        kw.pop("verify", None)
        return httpx.Client(transport=cli_http.transport).post(url, **kw)

    monkeypatch.setattr(client_module, "httpx", type("H", (), {"post": staticmethod(fake_post)}))

    body = cli_http.call("GET", cfg.api_url + "/workspaces", token="stale", config=cfg)
    assert body == {"workspaces": [{"slug": "ws1"}]}
    assert calls[0] == "Bearer stale" and calls[-1] == "Bearer fresh"
    assert cfg.auth_token == "fresh" and cfg.refresh_token == "next" and Cfg.saved


def test_the_cli_helper_gives_up_after_one_refresh(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    monkeypatch.setattr(cli_http, "transport", httpx.MockTransport(handler))

    class Cfg:
        api_url = "https://console.example/api/v1"
        auth_token = "stale"
        refresh_token = None
        verify_ssl = True

    with pytest.raises(AuthenticationError) as caught:
        cli_http.call("GET", Cfg.api_url + "/workspaces", token="stale", config=Cfg())
    assert str(caught.value) == SIGN_IN_HINT


def test_nothing_signed_in_is_said_before_any_request():
    with pytest.raises(AuthenticationError):
        cli_http.call("GET", "https://console.example/api/v1/workspaces", token="")
