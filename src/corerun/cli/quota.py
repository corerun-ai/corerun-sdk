"""
Workspace quota CLI commands
"""

import json as _json
from typing import Optional

import typer
from rich.console import Console

from corerun.cli import output
from rich.table import Table

console = output.console
app = typer.Typer(help="Workspace quota commands")


def _init_client():
    """Initialize client, handling errors gracefully"""
    try:
        from corerun import init
        return init()
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        console.print("Run 'corerun login' to authenticate")
        raise typer.Exit(1)


def _usage_cell(q) -> str:
    """Render usage against a limit, flagging anything already full."""
    if q.is_unlimited:
        return f"{q.used} / [dim]unlimited[/]"
    colour = "red" if q.is_exhausted else "white"
    return f"[{colour}]{q.used} / {q.limit}[/]"


@app.command("show")
def show_quota(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    Show the workspace's limits and what is consuming them.

    Example:
        corerun quota show
    """
    _init_client()

    import corerun.quota as quota

    try:
        q = quota.get(workspace=workspace)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if json_output:
        output.set_json(True)
    if output.json_mode():
        output.emit(q.model_dump(mode="json"))
        return

    # Counts are of active work: a stopped or finished workload has
    # released its slot, so stopping one frees room at once.
    table = Table(title="Workspace limits", caption="Used counts running and starting work only")
    table.add_column("Resource", style="cyan")
    table.add_column("Used / Limit", justify="right")
    table.add_column("Available", justify="right")

    rows = [
        ("Jobs", q.jobs),
        ("Notebooks", q.notebooks),
        ("Inference servers", q.inference_servers),
        ("GPUs", q.gpus),
        ("Storage (GB)", q.storage_gb),
    ]
    for label, item in rows:
        available = "unlimited" if item.is_unlimited else str(item.available)
        table.add_row(label, _usage_cell(item), available)

    console.print(table)

    exhausted = [label for label, item in rows if item.is_exhausted]
    if exhausted:
        console.print(f"\n[red]At limit:[/red] {', '.join(exhausted)}")


@app.command("gpus")
def show_gpu_usage(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    Show GPU usage broken down by the workloads holding them.

    Example:
        corerun quota gpus
    """
    _init_client()

    import corerun.quota as quota

    try:
        data = quota.gpu_usage(workspace=workspace)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if json_output:
        output.set_json(True)
    if output.json_mode():
        output.emit(data)
        return

    used = data.get("used_gpus", 0)
    if data.get("is_unlimited"):
        console.print(f"GPUs in use: [bold]{used}[/] (no limit)")
    else:
        console.print(
            f"GPUs in use: [bold]{used}[/] / {data.get('max_gpus', 0)} "
            f"({data.get('available_gpus', 0)} available)"
        )

    breakdown = data.get("breakdown") or []
    if not breakdown:
        return

    table = Table(title="GPU Usage by Resource")
    table.add_column("Type", style="cyan")
    table.add_column("Name")
    table.add_column("GPUs", justify="right")

    for item in breakdown:
        if not isinstance(item, dict):
            continue
        table.add_row(
            str(item.get("resource_type", item.get("type", "-"))),
            str(item.get("name", item.get("resource_name", "-"))),
            str(item.get("gpus", item.get("gpu_count", "-"))),
        )

    console.print(table)
