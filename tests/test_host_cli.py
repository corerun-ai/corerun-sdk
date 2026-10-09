"""
Adding a bare-metal host, and the command that has no address in it.

A host is onboarded by two commands rather than a manifest, and the second one
carries a credential. Both facts the tests below pin are ones where printing
something wrong is worse than printing nothing: an install command the platform
failed to build a URL into looks perfectly ordinary, and a connect command
without its token cannot work at all.
"""

import json

import httpx
import pytest
from typer.testing import CliRunner

from corerun import config as config_module
from corerun.cli import app
from corerun.cli import hosts as hosts_cli
from corerun.client import CoreRunClient

runner = CliRunner()

BASE = "https://example.test/api/v1"

GOOD_INSTALL = (
    "curl -fsSL https://example.test/api/v1/operators/install.sh | sudo bash"
)
CONNECT = "sudo corerun-host-operator connect --token ENROLL"


@pytest.fixture
def wire(monkeypatch):
    """Answer every request locally, and hand back what was asked for."""
    state = {"seen": [], "respond": None, "install": GOOD_INSTALL}

    def handle(request: httpx.Request) -> httpx.Response:
        state["seen"].append(request)
        if state["respond"] is not None:
            return state["respond"](request)

        path = request.url.path
        if path.endswith("/clusters/prepare"):
            body = json.loads(request.content)
            return httpx.Response(
                201,
                json={
                    "name": body["name"],
                    "type": "host",
                    "architecture": body.get("architecture", "amd64"),
                    "os": body.get("os", "linux"),
                    "enrollment_token": "ENROLL",
                    "install_command": state["install"],
                    "connect_command": CONNECT,
                    "note": "Run the install command first, then connect.",
                },
            )
        if request.method == "DELETE":
            return httpx.Response(200, json={"message": "Deleted", "cleanup": []})
        return httpx.Response(200, json={})

    client = CoreRunClient(config_module.Config(api_url=BASE))
    client._client = httpx.Client(transport=httpx.MockTransport(handle), base_url=BASE)

    monkeypatch.setattr(hosts_cli, "_init_client", lambda: None)
    monkeypatch.setattr(config_module, "_client", client)
    return state


def test_adding_a_host_sends_type_host_and_both_commands(wire):
    result = runner.invoke(app, ["host", "add", "dgx1"])
    assert result.exit_code == 0, result.stderr

    body = json.loads(wire["seen"][0].content)
    assert body["name"] == "dgx1"
    # The one field that decides between a manifest and two commands.
    assert body["type"] == "host"
    assert body["workspace_scope"] is True

    # Both steps, in order, with the credential intact: a connect command
    # without its token is a command that cannot work.
    assert GOOD_INSTALL in result.stdout
    assert CONNECT in result.stdout
    assert result.stdout.index(GOOD_INSTALL) < result.stdout.index(CONNECT)


def test_a_darwin_host_is_sent_as_darwin(wire):
    result = runner.invoke(app, ["host", "add", "mac1", "--os", "darwin", "--arch", "arm64"])
    assert result.exit_code == 0, result.stderr
    body = json.loads(wire["seen"][0].content)
    assert body["os"] == "darwin"
    assert body["architecture"] == "arm64"


def test_an_install_command_with_no_address_is_reported_not_printed(wire):
    # The API builds this from the platform's public API URL, and the check
    # guarding it can be satisfied by the download base alone. When only that
    # is set the command still comes back, minus its host -- and reads as
    # ordinary right up until somebody runs it.
    wire["install"] = "curl -fsSL /operators/install.sh | sudo bash"

    result = runner.invoke(app, ["host", "add", "dgx1"])
    assert result.exit_code == 0, result.stderr

    assert "curl -fsSL /operators/install.sh" not in result.stdout
    assert "CORERUN_PUBLIC_API_URL" in result.stdout
    # The connect command is built from the token and is unaffected, so it is
    # still worth giving.
    assert CONNECT in result.stdout


def test_json_mode_emits_the_enrollment_whole(wire):
    result = runner.invoke(app, ["--json", "host", "add", "dgx1"])
    assert result.exit_code == 0, result.stderr

    payload = json.loads(result.stdout)
    assert payload["enrollment_token"] == "ENROLL"
    assert payload["connect_command"] == CONNECT


def test_removing_a_host_does_not_ask_for_kubernetes_cleanup(wire):
    # There is no Helm release, namespace or scheduler on a bare machine.
    # Asking for their removal only puts failures in the report.
    result = runner.invoke(app, ["host", "rm", "dgx1", "--yes"])
    assert result.exit_code == 0, result.stderr

    request = wire["seen"][0]
    assert request.method == "DELETE"
    body = json.loads(request.content)
    assert body["clean_resources"] is False
    assert body["delete_namespace"] is False

    # And it says the software is still on the machine, which is the part
    # somebody would otherwise assume had been undone. On stdout rather than
    # stderr: removing a host has no piped payload, so everything here is for
    # a person reading it.
    assert "systemctl" in result.stdout


def test_removing_a_tenant_wide_host_uses_the_tenants_route(wire):
    # Which route removes a cluster is decided by who owns it, and the
    # workspace route answers 403 for a tenant-wide one. So a host added with
    # --tenant-wide could not be removed at all until this flag existed --
    # found by doing exactly that on a real host, and having to reach for curl.
    result = runner.invoke(app, ["host", "rm", "dgx1", "--tenant-wide", "--yes"])
    assert result.exit_code == 0, result.stderr

    request = wire["seen"][0]
    assert request.method == "DELETE"
    assert request.url.path == "/api/v1/tenant/shared/clusters/dgx1"


def test_a_host_can_declare_its_accelerator_family(wire):
    """
    A host can say what its cards are at enrollment, as a cluster can.

    It matters more for a host than for a cluster. A cluster describes its
    hardware through pod profiles; a bare machine has none, so this is the only
    place it can say anything. Without it the platform falls back to what the
    operator reports -- a machine's account of itself, which is exactly what
    declaring a family exists to override.

    The flag was missing for a while even though the API accepted the field on
    the same endpoint the cluster path uses, so the gap was in the client only.
    """
    result = runner.invoke(
        app, ["host", "add", "dgx1", "--accelerator-family", "nvidia/blackwell-rtx"]
    )
    assert result.exit_code == 0, result.stderr

    body = json.loads(wire["seen"][0].content)
    assert body["accelerator_family"] == "nvidia/blackwell-rtx"
    assert body["type"] == "host"


def test_an_undeclared_host_sends_no_family(wire):
    """
    Omitted means omitted, not empty.

    An empty string would be a declaration of nothing, and the API keeps a
    declared family apart from a detected one precisely so that a report never
    overwrites what a person wrote. Sending "" would blur that.
    """
    result = runner.invoke(app, ["host", "add", "dgx1"])
    assert result.exit_code == 0, result.stderr

    body = json.loads(wire["seen"][0].content)
    assert "accelerator_family" not in body


def test_a_host_can_run_its_workloads_with_docker(wire):
    result = runner.invoke(app, ["host", "add", "gpu-box", "--runtime", "docker"])
    assert result.exit_code == 0, result.output
    assert json.loads(wire["seen"][0].content)["runtime"] == "docker"


def test_an_unsaid_runtime_is_left_to_the_platform(wire):
    result = runner.invoke(app, ["host", "add", "dgx1"])
    assert result.exit_code == 0, result.output
    assert "runtime" not in json.loads(wire["seen"][0].content)
