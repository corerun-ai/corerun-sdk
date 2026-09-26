"""
The platform credential a corerun notebook is launched with.

These pin the two properties the rest of the design leans on: the key is read
from the file every time rather than remembered, and it never displaces a
credential the user chose themselves.
"""

import pytest

from corerun import config as config_module
from corerun.credentials import credential_path, read_platform_credential


@pytest.fixture
def credential_file(tmp_path, monkeypatch):
    path = tmp_path / "api-key"
    monkeypatch.setenv("CORERUN_AUTH_TOKEN_FILE", str(path))
    return path


def test_absent_credential_is_not_an_error(credential_file):
    # The normal case outside a corerun notebook.
    assert read_platform_credential() is None


def test_reads_the_key_and_strips_the_newline(credential_file):
    credential_file.write_text("cr-abcd1234-secret\n")
    assert read_platform_credential() == "cr-abcd1234-secret"


def test_empty_file_reads_as_absent(credential_file):
    # A half-written file must not be handed out as a key.
    credential_file.write_text("")
    assert read_platform_credential() is None


def test_rereads_after_renewal(credential_file):
    credential_file.write_text("cr-first-secret")
    assert read_platform_credential() == "cr-first-secret"

    # The platform replaces the file while the notebook runs.
    credential_file.write_text("cr-second-secret")
    assert read_platform_credential() == "cr-second-secret"


def test_path_follows_the_env_override(credential_file):
    assert credential_path() == credential_file


def test_platform_credential_fills_an_empty_config(credential_file):
    credential_file.write_text("cr-platform-secret")

    config = config_module.Config()
    config_module._apply_platform_credential(config)

    assert config.auth_token == "cr-platform-secret"
    # Recorded so the client can pick up a renewed key without being rebuilt.
    assert config.auth_token_file == credential_file


def test_platform_credential_does_not_displace_a_chosen_one(credential_file):
    credential_file.write_text("cr-platform-secret")

    config = config_module.Config(auth_token="cr-mine-secret")
    config_module._apply_platform_credential(config)

    assert config.auth_token == "cr-mine-secret"
    # No file to re-read, so a 401 on this key is a real failure.
    assert config.auth_token_file is None


def test_inference_url_survives_both_merges(tmp_path, monkeypatch):
    """A setting parsed from the environment and dropped in the merge is
    invisible: it looks configured and behaves as the default. CORERUN_VERIFY_SSL
    did exactly that for a while, so this pins the new one."""
    import importlib
    import corerun.config as config

    monkeypatch.setenv("CORERUN_INFERENCE_URL", "https://api.dev.corerun.ai")
    monkeypatch.setenv("HOME", str(tmp_path))
    importlib.reload(config)

    assert config.get_config().inference_url == "https://api.dev.corerun.ai"


def test_inference_url_has_no_default_and_is_overridable_by_file(tmp_path, monkeypatch):
    """The inference address used to have a default of its own, so pointing the
    SDK at a deployment moved the API and left model endpoints on the product's
    host, which resolves from nowhere that deployment is. The platform already
    reports where endpoints live, so there is nothing to guess."""
    import importlib
    import corerun.config as config

    monkeypatch.delenv("CORERUN_INFERENCE_URL", raising=False)
    importlib.reload(config)
    assert config.DEFAULT_INFERENCE_URL == ""

    path = tmp_path / "config"
    path.write_text("inference_url=https://inference.example.internal\n")
    loaded = config.Config.from_file(path)
    assert loaded.inference_url == "https://inference.example.internal"


def test_login_prefers_the_configured_url_over_the_default(monkeypatch):
    """Login read neither the environment nor the config file — only --url and a
    hardcoded default. That was invisible while the default happened to be the
    address people used; once it moved, logging in against a self-hosted
    deployment opened a browser at somebody else's platform, with
    CORERUN_API_URL set and ignored."""
    import corerun.cli.auth as auth

    monkeypatch.setenv("CORERUN_API_URL", "https://dev.corerun.ai/api/v1")
    assert auth.resolve_login_url(None) == "https://dev.corerun.ai/api/v1"


def test_an_explicit_url_beats_everything(monkeypatch):
    import corerun.cli.auth as auth

    monkeypatch.setenv("CORERUN_API_URL", "https://dev.corerun.ai/api/v1")
    assert auth.resolve_login_url("https://other.example/api/v1") == "https://other.example/api/v1"


def test_login_falls_back_to_the_default_when_nothing_is_configured(monkeypatch):
    import corerun.cli.auth as auth

    monkeypatch.delenv("CORERUN_API_URL", raising=False)
    monkeypatch.setattr(auth, "_configured_api_url", lambda: None)
    assert auth.resolve_login_url(None) == auth.DEFAULT_API_URL


def test_login_keeps_the_settings_it_does_not_own(tmp_path, monkeypatch):
    """Saving writes the whole file, so a config rebuilt from the four fields
    login sets dropped the rest: signing in again on a self-hosted deployment
    reset the inference address and the TLS setting to their defaults, and the
    loss surfaced later as an address that no longer resolved."""
    import corerun.cli.auth as auth

    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".corerun").mkdir()
    (tmp_path / ".corerun" / "config").write_text(
        "api_url=https://platform.example/api/v1\n"
        "inference_url=https://gateway.example/inference\n"
        "verify_ssl=false\n"
    )

    monkeypatch.delenv("CORERUN_API_URL", raising=False)
    monkeypatch.delenv("CORERUN_INFERENCE_URL", raising=False)
    # Which workspace to work in, and the check that the key works: both would
    # reach the platform, which is not what is under test.
    monkeypatch.setattr(auth.workspace_cli, "choose", lambda *a, **k: "ws-1")
    monkeypatch.setattr(auth, "init", lambda **kwargs: _Stub())

    auth.login(auth_token="cr-new", workspace=None, api_url=None, use_device_code=False)

    saved = (tmp_path / ".corerun" / "config").read_text()
    assert "auth_token=cr-new" in saved
    assert "inference_url=https://gateway.example/inference" in saved
    assert "verify_ssl=false" in saved


class _Stub:
    """Stands in for the client login builds to check the key works."""

    def get(self, *args, **kwargs):
        return {}


def test_a_host_that_does_not_resolve_is_named(monkeypatch):
    """A resolver failure reaches the user in the operating system's words —
    "[Errno 8] nodename nor servname provided, or not known" — which name
    neither the host nor the setting that chose it, leaving nothing to act on."""
    import socket

    from corerun.exceptions import UnreachableError, unreachable

    error = unreachable(
        "https://corerun.ai/api/v1/data",
        socket.gaierror(8, "nodename nor servname provided, or not known"),
    )
    assert isinstance(error, UnreachableError)
    assert "corerun.ai" in str(error)
    assert "CORERUN_API_URL" in str(error)


def test_a_refused_connection_says_so_instead():
    """Nobody listening is a different failure from a name that does not
    exist, and only one of the two is fixed by changing the address."""
    from corerun.exceptions import unreachable

    error = unreachable("https://dev.corerun.ai/api/v1/data", ConnectionRefusedError("refused"))
    assert "cannot reach https://dev.corerun.ai/api/v1/data" in str(error)


def test_an_inference_override_replaces_the_host_not_the_path(monkeypatch):
    """An endpoint is published at <base>/<name>: an override supplies the base,
    because the path is the platform's routing and the host is the part a
    caller may know better."""
    from corerun import endpoints

    endpoint = endpoints.Endpoint(name="chat", url="https://platform.example/inference/chat")

    monkeypatch.setattr(
        endpoints, "_configured_inference_url", lambda: "https://gateway.example/inference"
    )
    assert (
        endpoints._address(endpoint, "/v1/chat/completions")
        == "https://gateway.example/inference/chat/v1/chat/completions"
    )


def test_the_reported_endpoint_address_is_trusted_over_a_derived_one(monkeypatch):
    """The platform knows where it published an endpoint; a host with a prefix
    added to the API address is this SDK's guess. The guess is for when it
    reports nothing, not a correction to what it said."""
    from corerun import endpoints

    monkeypatch.setattr(endpoints, "_configured_inference_url", lambda: "")
    monkeypatch.setattr(endpoints, "_derived_inference_base", lambda: "https://api.example.com")

    endpoint = endpoints.Endpoint(name="chat", url="https://gateway.example/inference/chat")
    assert endpoints._address(endpoint, "/v1/models") == "https://gateway.example/inference/chat/v1/models"


def test_a_derived_address_stands_in_when_the_platform_reports_none(monkeypatch):
    from corerun import endpoints

    monkeypatch.setattr(endpoints, "_configured_inference_url", lambda: "")
    monkeypatch.setattr(endpoints, "_derived_inference_base", lambda: "https://api-dev.example.com")

    # An endpoint the API sent without a url of its own.
    endpoint = endpoints.Endpoint(name="chat")
    assert endpoints._address(endpoint, "/v1/models") == "https://api-dev.example.com/chat/v1/models"


def test_nowhere_to_call_an_endpoint_says_which_setting_to_set(monkeypatch):
    """A relative URL reached the transport as an unsupported protocol, which
    named neither the endpoint nor anything to do about it."""
    import pytest

    from corerun import endpoints
    from corerun.exceptions import ConfigurationError

    monkeypatch.setattr(endpoints, "_configured_inference_url", lambda: "")
    monkeypatch.setattr(endpoints, "_derived_inference_base", lambda: "")

    with pytest.raises(ConfigurationError) as raised:
        endpoints._address(endpoints.Endpoint(name="chat"), "/v1/models")
    assert "chat" in str(raised.value)
    assert "CORERUN_INFERENCE_URL" in str(raised.value)


def test_the_inference_address_follows_the_api_address():
    """The deployment's own layout: `corerun.ai` for production and
    `dev.corerun.ai` for dev, with the console at `console.` and the gateway
    at `api.` under each. The API is reached at the domain or at the console's
    host, and the gateway is the same from both -- api.dev.corerun.ai is the
    address the dev deployment serves endpoints from, which is what makes this
    the shape to build."""
    from corerun.config import inference_base_from

    assert inference_base_from("https://corerun.ai/api/v1") == "https://api.corerun.ai"
    assert inference_base_from("https://dev.corerun.ai/api/v1") == "https://api.dev.corerun.ai"
    # Through the console's own host -- what `corerun login` is given and what
    # a notebook is told -- the gateway is beside the console, not beneath it.
    assert inference_base_from("https://console.corerun.ai/api/v1") == "https://api.corerun.ai"
    assert inference_base_from("https://console.dev.corerun.ai/api/v1") == "https://api.dev.corerun.ai"
    # An API already addressed at the gateway's own name is not prefixed twice.
    assert inference_base_from("https://api.dev.corerun.ai/api/v1") == "https://api.dev.corerun.ai"
    # A self-hosted deployment follows the same rule -- which may name a host
    # nobody published, and is one of the reasons what the platform reports is
    # trusted first.
    assert (
        inference_base_from("http://corerun.example.com/api/v1")
        == "http://api.corerun.example.com"
    )
    # The scheme follows: a deployment on plain HTTP does not become HTTPS.
    assert inference_base_from("http://corerun.internal/api/v1") == "http://api.corerun.internal"

    # Nothing to build a deployment name from, so nothing is invented.
    assert inference_base_from("") == ""
    assert inference_base_from("http://localhost:8001/api/v1") == ""
    assert inference_base_from("http://192.168.4.50:4455/api/v1") == ""


def test_a_value_an_older_client_wrote_for_itself_is_not_a_choice(tmp_path):
    """Clients up to 0.1.0 wrote their own default inference address into the
    file on every login. Read back as a choice it takes precedence over both the
    address the platform reports and the one the API address implies — so a
    deployment that has since moved keeps being called at the host it used to
    live on, which is the failure this address was supposed to stop causing."""
    from corerun.config import Config

    path = tmp_path / "config"
    path.write_text(
        "api_url=https://dev.corerun.ai/api/v1\n"
        "inference_url=https://api.corerun.ai\n"
    )

    loaded = Config.from_file(path)
    assert loaded.inference_url == ""
    assert loaded.inference_base == "https://api.dev.corerun.ai"

    # A value somebody chose is kept, and still wins.
    path.write_text("inference_url=https://gateway.internal/inference\n")
    assert Config.from_file(path).inference_url == "https://gateway.internal/inference"


def test_the_derived_inference_address_is_not_written_to_the_file(tmp_path, monkeypatch):
    """It follows api_url, so storing it would freeze today's answer to a
    question that moves with the address."""
    import importlib

    import corerun.config as config

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CORERUN_INFERENCE_URL", raising=False)
    monkeypatch.setenv("CORERUN_API_URL", "https://dev.corerun.ai/api/v1")
    importlib.reload(config)

    loaded = config.get_config()
    assert loaded.inference_url == ""
    assert loaded.inference_base == "https://api.dev.corerun.ai"

    loaded.save()
    assert "inference_url" not in (tmp_path / ".corerun" / "config").read_text()


def test_logout_removes_the_credentials_and_leaves_the_address(tmp_path, monkeypatch):
    """Signing out deleted the file, so the deployment's address went with it and
    the next command was pointed at the default -- which on a self-hosted
    installation is a host that does not resolve. What a logout has to remove is
    the ability to act as somebody."""
    import corerun.cli.auth as auth

    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".corerun").mkdir()
    path = tmp_path / ".corerun" / "config"
    path.write_text(
        "auth_token=cr-mine\n"
        "refresh_token=cr-refresh\n"
        "workspace=ws-1\n"
        "api_url=https://platform.example/api/v1\n"
        "timeout=45\n"
    )

    auth.logout()

    reloaded = config_module.Config.from_file(path)
    assert reloaded.auth_token is None
    assert reloaded.refresh_token is None
    assert reloaded.api_url == "https://platform.example/api/v1"
    assert reloaded.timeout == 45
    assert reloaded.workspace == "ws-1"


def test_saving_replaces_the_file_in_one_step(tmp_path):
    """Written in place, the file is empty for as long as the write takes, and a
    command reading in that window finds no credentials -- "not logged in" just
    after signing in."""
    from corerun.config import Config

    path = tmp_path / "config"
    path.write_text("auth_token=cr-old\napi_url=https://platform.example/api/v1\n")

    Config(auth_token="cr-new", api_url="https://platform.example/api/v1").save(path)

    assert Config.from_file(path).auth_token == "cr-new"
    # Nothing left beside it: the replacement is moved onto the name, not kept.
    assert [p.name for p in tmp_path.iterdir()] == ["config"]
