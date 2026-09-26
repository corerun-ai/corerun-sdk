"""`corerun groups link|unlink|links`: directory groups mapped to groups here."""

from typer.testing import CliRunner

from corerun.cli import app
from corerun.cli import groups as groups_cli

runner = CliRunner()


def fake(monkeypatch, mappings):
    calls = []

    def call(method, path, json_body=None):
        calls.append((method, path, json_body))
        if method == "GET" and path.endswith("/mappings"):
            return {"mappings": mappings}
        if method == "DELETE":
            return {"withdrawn": 2}
        return {}

    monkeypatch.setattr(groups_cli, "_call", call)
    return calls


def test_link_sends_the_directory_group_as_given(monkeypatch):
    calls = fake(monkeypatch, [])
    result = runner.invoke(app, ["groups", "link", "speech", "Speech Team"])
    assert result.exit_code == 0, result.output
    assert calls[-1] == ("POST", "/groups/speech/mappings", {"directory_group": "Speech Team"})


def test_unlink_finds_the_link_by_name_and_says_who_left(monkeypatch):
    calls = fake(monkeypatch, [{"id": "m1", "directory_group": "Speech Team"}])
    result = runner.invoke(app, ["groups", "unlink", "speech", "speech team"])
    assert result.exit_code == 0, result.output
    assert calls[-1] == ("DELETE", "/groups/speech/mappings/m1", None)
    assert "2 people" in result.output


def test_unlink_of_something_not_linked_fails(monkeypatch):
    fake(monkeypatch, [])
    result = runner.invoke(app, ["groups", "unlink", "speech", "Nope"])
    assert result.exit_code == 1


def test_a_refusal_is_said_not_thrown(monkeypatch):
    from corerun.cli import http
    from corerun.exceptions import CoreRunError

    monkeypatch.setattr(groups_cli, "_require_credentials", lambda: None)

    def refuse(*a, **k):
        raise CoreRunError("HTTP 409: That directory group is already linked to this group")

    monkeypatch.setattr(http, "request", refuse)
    result = runner.invoke(app, ["groups", "link", "speech", "Speech Team"])
    assert result.exit_code == 1
    assert "already linked" in result.output and "Traceback" not in result.output
