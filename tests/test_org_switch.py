"""Switching organisation: a new session for the other one, kept, and the old workspace let go."""

import json

import httpx
import pytest

from corerun import config as config_module
from corerun import org
from corerun.client import CoreRunClient

BASE = "https://example.test/api/v1"
ORGS = [
    {"id": "t-own", "slug": "own", "display_name": "Own", "is_admin": True, "current": True},
    {"id": "t-partner", "slug": "partner", "display_name": "Partner Co", "is_admin": False, "current": False},
]


@pytest.fixture
def wire(monkeypatch):
    seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/auth/organisations"):
            return httpx.Response(200, json={"organisations": ORGS, "current": "t-own"})
        if request.url.path.endswith("/auth/switch"):
            body = json.loads(request.content)
            if body["tenant_id"] != "t-partner":
                return httpx.Response(403, json={"error": "not_a_member", "message": "no"})
            return httpx.Response(200, json={"access_token": "new-access", "refresh_token": "new-refresh",
                                             "tenant": {"id": "t-partner", "slug": "partner", "display_name": "Partner Co"},
                                             "is_tenant_admin": False})
        return httpx.Response(404, json={})

    cfg = config_module.Config(api_url=BASE, auth_token="old-access", refresh_token="old-refresh", workspace="ws-own")
    monkeypatch.setattr(config_module.Config, "save", lambda self, path=None: None)
    client = CoreRunClient(cfg)
    client._client = httpx.Client(transport=httpx.MockTransport(handle), base_url=BASE)
    monkeypatch.setattr(config_module, "_client", client)
    return seen, cfg


def test_switch_keeps_the_new_session_and_drops_the_workspace(wire):
    seen, cfg = wire
    answer = org.switch("partner")
    sent = json.loads(seen[-1].content)
    # The refresh chain goes with the switch, or the next refresh would put the
    # session back in the organisation it left.
    assert sent == {"tenant_id": "t-partner", "refresh_token": "old-refresh"}
    assert (cfg.auth_token, cfg.refresh_token, cfg.workspace) == ("new-access", "new-refresh", None)
    assert answer["tenant"]["slug"] == "partner"


def test_an_organisation_you_do_not_belong_to_is_named(wire):
    with pytest.raises(ValueError, match="nowhere"):
        org.switch("nowhere")
