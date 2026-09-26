"""Installing the corerun skills into an agent's skills directory.

The skills ship inside this package so that anywhere the CLI is installed has
them: a laptop, CI, a notebook, an agent that has never seen corerun. They used
to live in the notebook image, which meant the only way to get them was to be
inside one.
"""

import shutil
from pathlib import Path
from typing import List, Optional

import typer
from rich.table import Table

from corerun.cli import output

console = output.console
app = typer.Typer(help="corerun skills for coding agents")

# Where each agent reads skills from, globally: a directory holding one folder
# per skill, each with its SKILL.md. The home of an agent -- the directory it
# creates when it is first run -- says it is installed here.
AGENTS = {
    "claude": ("Claude Code", Path.home() / ".claude", Path.home() / ".claude" / "skills"),
    "opencode": (
        "opencode",
        Path.home() / ".config" / "opencode",
        Path.home() / ".config" / "opencode" / "skills",
    ),
    "pi": ("pi", Path.home() / ".pi", Path.home() / ".pi" / "agent" / "skills"),
    "codex": ("Codex", Path.home() / ".codex", Path.home() / ".codex" / "skills"),
    # The shared location several agents read (opencode, goose and others),
    # and the one used when no agent is found at all.
    "agents": (
        "any agent reading ~/.agents",
        Path.home() / ".agents",
        Path.home() / ".agents" / "skills",
    ),
}


def _bundled() -> Path:
    """The skills carried in this package."""
    return Path(__file__).resolve().parent.parent / "skills"


def _available() -> list[Path]:
    root = _bundled()
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if (p / "SKILL.md").is_file())


def _describe(skill: Path) -> str:
    """The description an agent matches against, read from the front matter."""
    try:
        lines = (skill / "SKILL.md").read_text().splitlines()
    except OSError:
        return ""
    for line in lines[:10]:
        if line.startswith("description:"):
            return line.split(":", 1)[1].strip()
    return ""


def _detected() -> list[str]:
    """The agents installed on this machine, and the shared location."""
    found = [key for key, (_, home, _) in AGENTS.items() if key != "agents" and home.is_dir()]
    return found + ["agents"]


@app.command("list")
def list_skills():
    """Show the skills this CLI carries."""
    skills = _available()
    if not skills:
        console.print("[yellow]No skills are bundled with this build.[/yellow]")
        raise typer.Exit(1)

    table = Table(show_header=True, header_style="bold")
    table.add_column("Skill")
    table.add_column("What it covers")
    for skill in skills:
        table.add_row(skill.name, _describe(skill))
    console.print(table)


@app.command("install")
def install(
    agent: Optional[List[str]] = typer.Option(
        None,
        "--agent",
        "-a",
        help="claude, opencode, pi, codex or agents; repeat for several. "
        "Default: every agent found here, and ~/.agents/skills.",
    ),
    all_agents: bool = typer.Option(
        False, "--all", help="Every agent above, whether it is installed or not."
    ),
    dir: Optional[Path] = typer.Option(
        None,
        "--dir",
        "-d",
        help="Install into this directory instead, for an agent not listed.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Replace skills that are already there, e.g. after upgrading the CLI.",
    ),
):
    """
    Copy the corerun skills where coding agents read them.

    Example:
        corerun skills install                      # every agent found on this machine
        corerun skills install --agent claude --force
        corerun skills install --dir ./.claude/skills   # one project only
    """
    skills = _available()
    if not skills:
        console.print("[red]Error:[/red] no skills are bundled with this build.")
        raise typer.Exit(1)

    if dir is not None:
        targets = [("that directory", dir.expanduser())]
    else:
        keys = list(AGENTS) if all_agents else (agent or _detected())
        unknown = [k for k in keys if k not in AGENTS]
        if unknown:
            console.print(
                f"[red]Error:[/red] unknown agent {', '.join(unknown)}; one of {', '.join(AGENTS)}"
            )
            raise typer.Exit(1)
        targets = [(AGENTS[k][0], AGENTS[k][2]) for k in dict.fromkeys(keys)]

    for label, target in targets:
        _install_into(skills, label, target, force)


def _install_into(skills: list[Path], label: str, target: Path, force: bool) -> None:
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        console.print(f"[red]Error:[/red] could not create {target}: {e}")
        raise typer.Exit(1)

    installed, skipped = [], []
    for skill in skills:
        destination = target / skill.name
        if destination.exists() and not force:
            skipped.append(skill.name)
            continue
        try:
            # Replaced rather than merged: a half-updated skill is worse than
            # either version of it.
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(skill, destination)
        except OSError as e:
            console.print(f"[red]Error:[/red] could not install {skill.name}: {e}")
            raise typer.Exit(1)
        installed.append(skill.name)

    line = f"[bold]{label}[/bold] ({target}): {len(installed)} installed"
    if skipped:
        line += f", {len(skipped)} already there (--force replaces them)"
    console.print(line)
