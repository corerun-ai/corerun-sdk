"""corerun login takes the address a person has, not only the API base."""

from corerun.cli.auth import api_base, resolve_login_url


def test_a_bare_origin_gets_the_api_path():
    assert api_base("http://localhost:8080") == "http://localhost:8080/api/v1"
    assert api_base("https://console.example.com/") == "https://console.example.com/api/v1"


def test_an_api_base_is_left_alone():
    assert api_base("https://corerun.ai/api/v1") == "https://corerun.ai/api/v1"
    assert api_base("https://corerun.ai/api/v1/") == "https://corerun.ai/api/v1"


def test_a_deployment_that_serves_the_api_elsewhere_is_not_second_guessed():
    assert api_base("https://example.com/platform/api") == "https://example.com/platform/api"


def test_the_explicit_url_wins_and_is_normalised(monkeypatch):
    monkeypatch.setenv("CORERUN_API_URL", "https://elsewhere.example.com/api/v1")
    assert resolve_login_url("http://localhost:8080") == "http://localhost:8080/api/v1"
    assert resolve_login_url(None) == "https://elsewhere.example.com/api/v1"
