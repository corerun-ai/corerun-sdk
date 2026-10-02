"""
Compute target CLI commands
"""

import json as _json
from typing import Optional

import typer
from rich.console import Console

from corerun.cli import output
from rich.table import Table

console = output.console
app = typer.Typer(help="Compute target commands")


def _init_client():
    """Initialize client, handling errors gracefully"""
    try:
        from corerun import init
        return init()
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        console.print("Run 'corerun login' to authenticate")
        raise typer.Exit(1)


@app.command("list")
def list_targets(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    List compute targets available to the workspace.

    Example:
        corerun compute list
    """
    _init_client()

    import corerun.compute as compute

    try:
        items = compute.list(workspace=workspace)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if json_output:
        output.set_json(True)
    if output.json_mode():
        output.emit([t.model_dump(mode="json") for t in items])
        return

    if not items:
        console.print("No compute targets found")
        return

    table = Table(title="Compute Targets")
    table.add_column("Name", style="cyan")
    table.add_column("Type")
    table.add_column("Scope")
    table.add_column("GPUs", justify="right")
    table.add_column("Description")

    for t in items:
        gpus = f"{t.gpus} {t.gpu_model}".strip() if t.gpus else "-"
        note = t.description or ""
        if t.operator_update_available:
            note = (note + " " if note else "") + "[yellow](operator update available)[/]"
        table.add_row(t.name, t.type or "-", t.scope or "-", gpus, note)

    console.print(table)


@app.command("get")
def get_target(
    name: str = typer.Argument(..., help="Compute target name"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    Show a compute target's details.

    Example:
        corerun compute get dgx
    """
    _init_client()

    import corerun.compute as compute

    try:
        t = compute.get(name, workspace=workspace)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if json_output:
        output.set_json(True)
    if output.json_mode():
        output.emit(t.model_dump(mode="json"))
        return

    console.print(f"\n[bold cyan]{t.name}[/]")
    console.print(f"  Type:        {t.type or '-'}")
    console.print(f"  Scope:       {t.scope or '-'}")
    console.print(f"  Cluster ID:  {t.cluster_id or '-'}")
    console.print(f"  Description: {t.description or '-'}")

    if t.config:
        console.print("\n[bold]Config[/]")
        for k, v in sorted(t.config.items()):
            console.print(f"  {k}: {v}")


@app.command("types")
def list_types(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    List the compute target types this platform supports.

    Example:
        corerun compute types
    """
    _init_client()

    import corerun.compute as compute

    try:
        items = compute.types(workspace=workspace)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if json_output:
        output.set_json(True)
    if output.json_mode():
        output.emit(items)
        return

    if not items:
        console.print("No compute types available")
        return

    table = Table(title="Compute Types")
    table.add_column("Name", style="cyan")
    table.add_column("Description")

    for t in items:
        if isinstance(t, dict):
            table.add_row(str(t.get("name", "-")), str(t.get("description", "")))
        else:
            table.add_row(str(t), "")

    console.print(table)
