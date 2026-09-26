"""Where `corerun skills install` puts the skills: every coding agent found on
the machine, the shared ~/.agents, or what it was asked for."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from corerun.cli import app
from corerun.cli import skills as skills_cli

runner = CliRunner()


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home directory of our own, with the agents' locations under it."""
    agents = {
        key: (
            label,
            tmp_path / Path(home).relative_to(Path.home()),
            tmp_path / Path(target).relative_to(Path.home()),
        )
        for key, (label, home, target) in skills_cli.AGENTS.items()
    }
    monkeypatch.setattr(skills_cli, "AGENTS", agents)
    return tmp_path


def installed(path: Path) -> set:
    return {p.name for p in path.iterdir()} if path.is_dir() else set()


def test_every_agent_found_gets_them_and_so_does_the_shared_place(home):
    (home / ".claude").mkdir()
    (home / ".pi").mkdir()
    result = runner.invoke(app, ["skills", "install"])
    assert result.exit_code == 0, result.output
    names = installed(home / ".claude" / "skills")
    assert "corerun-endpoints" in names
    assert installed(home / ".pi" / "agent" / "skills") == names
    assert installed(home / ".agents" / "skills") == names
    # Agents that are not installed are left alone.
    assert not (home / ".codex").exists() and not (home / ".config" / "opencode").exists()


def test_an_agent_can_be_named_and_an_unknown_one_is_refused(home):
    result = runner.invoke(app, ["skills", "install", "--agent", "codex"])
    assert result.exit_code == 0, result.output
    assert "corerun-endpoints" in installed(home / ".codex" / "skills")
    assert not (home / ".agents" / "skills").exists()

    result = runner.invoke(app, ["skills", "install", "--agent", "cursor-ish"])
    assert result.exit_code == 1


def test_a_second_install_keeps_what_is_there_unless_forced(home):
    (home / ".claude").mkdir()
    runner.invoke(app, ["skills", "install", "--agent", "claude"])
    skill = home / ".claude" / "skills" / "corerun-endpoints" / "SKILL.md"
    skill.write_text("edited")
    runner.invoke(app, ["skills", "install", "--agent", "claude"])
    assert skill.read_text() == "edited"
    runner.invoke(app, ["skills", "install", "--agent", "claude", "--force"])
    assert skill.read_text() != "edited"
