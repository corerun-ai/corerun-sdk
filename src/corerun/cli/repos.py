"""
Code repository CLI commands.

A workspace keeps its training code in repositories on the platform's own git
service. Jobs clone from them; push to them with git or with `repos push`.
"""

from typing import Optional

import typer
from rich.table import Table

from corerun.cli import output

console = output.console
app = typer.Typer(help="Code repositories for training jobs")


def _init_client():
    try:
        from corerun import init

        return init()
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        console.print("Run 'corerun login' to authenticate")
        raise typer.Exit(1)


def _called(fn):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 — the CLI reports, it does not raise
        raise output.fail(str(e))


@app.command("list")
def list_repos(workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID")):
    """List the workspace's code repositories."""
    _init_client()
    from corerun import repos

    found = _called(lambda: repos.list(workspace=workspace))

    def render():
        if not found:
            console.print("No code repositories. Create one with: corerun repos create <name>")
            return
        table = Table()
        table.add_column("Name", style="bold")
        table.add_column("Default branch")
        table.add_column("Description")
        for r in found:
            table.add_row(r.get("name", ""), r.get("default_branch", ""), r.get("description") or "")
        console.print(table)

    output.emit(found, render)


@app.command("create")
def create_repo(
    name: str = typer.Argument(..., help="Repository name"),
    description: Optional[str] = typer.Option(None, "--description", "-d"),
    branch: Optional[str] = typer.Option(None, "--default-branch", help="Default branch (main)"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """Create an empty code repository."""
    _init_client()
    from corerun import repos

    repo = _called(lambda: repos.create(name, description=description, default_branch=branch, workspace=workspace))
    url = repos.clone_url(name, workspace)

    def render():
        console.print(f"[green]✓[/green] Created repository '{name}'")
        console.print(f"  Clone: {url}")
        console.print(f"  Push a directory: corerun repos push {name} ./src")

    output.emit({**repo, "clone_url": url}, render)


@app.command("show")
def show_repo(
    name: str = typer.Argument(..., help="Repository name"),
    ref: Optional[str] = typer.Option(None, "--ref", help="Branch, tag or commit for the file list"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """Show a repository's branches, tags and files."""
    _init_client()
    from corerun import repos

    info = _called(lambda: repos.get(name, workspace=workspace))
    files = [] if info.get("empty") else _called(lambda: repos.tree(name, ref=ref, workspace=workspace))

    def render():
        console.print(f"[bold]{name}[/bold]  {repos.clone_url(name, workspace)}")
        if info.get("empty"):
            console.print("  Empty. Push a directory: corerun repos push " + name + " ./src")
            return
        for r in info.get("refs") or []:
            console.print(f"  {r.get('kind', ''):6} {r.get('name', '')}  {str(r.get('commit', ''))[:12]}")
        console.print()
        for f in files:
            console.print(f"  {f.get('path', '')}")

    output.emit({**info, "files": files}, render)


@app.command("push")
def push_repo(
    name: str = typer.Argument(..., help="Repository name (created if it does not exist)"),
    source: str = typer.Argument(..., help="Local directory"),
    branch: str = typer.Option("main", "--branch", "-b", help="Branch to commit to"),
    message: Optional[str] = typer.Option(None, "--message", "-m", help="Commit message"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """Commit a directory to a branch and push it.

    The directory becomes the branch's whole tree; its .gitignore applies.

    Example:
        corerun repos push train ./src
        corerun repos push train ./src --branch experiment-2 -m "lower lr"
    """
    _init_client()
    from corerun import repos

    _called(lambda: repos.ensure(name, workspace=workspace))
    with console.status(f"Pushing {source} to {name}:{branch}..."):
        commit = _called(lambda: repos.push(name, source, branch=branch, message=message, workspace=workspace))

    output.emit(
        {"repo": name, "branch": branch, "commit": commit},
        lambda: console.print(f"[green]✓[/green] {name}:{branch} at {commit[:12]}"),
    )


@app.command("delete")
def delete_repo(
    name: str = typer.Argument(..., help="Repository name"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """Delete a repository and all of its history."""
    _init_client()
    from corerun import repos

    if not yes:
        output.confirm(f"Delete repository '{name}' and all of its history?")
    _called(lambda: repos.delete(name, workspace=workspace))
    output.emit({"deleted": name}, lambda: console.print(f"[green]✓[/green] Deleted '{name}'"))
