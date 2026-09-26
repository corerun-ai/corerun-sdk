"""
Traces CLI commands
"""

import typer
from rich.console import Console

from corerun.cli import output
from rich.table import Table
from rich.tree import Tree
from rich.panel import Panel
from rich.syntax import Syntax
from typing import Optional
import json

console = output.console
app = typer.Typer(help="Trace management commands")


def _init_client():
    """Initialize client, handling errors gracefully"""
    try:
        from corerun import init
        return init()
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        console.print("Run 'corerun login' to authenticate")
        raise typer.Exit(1)


def _format_duration(ms: int) -> str:
    """Format duration in ms to human readable."""
    if ms < 1000:
        return f"{ms}ms"
    elif ms < 60000:
        return f"{ms/1000:.2f}s"
    else:
        return f"{ms/60000:.1f}m"


def _get_status_style(status: str) -> str:
    """Get style for status."""
    return {
        "ok": "green",
        "OK": "green",
        "error": "red",
        "ERROR": "red",
    }.get(status, "yellow")


def _get_span_type_style(span_type: str) -> str:
    """Get style for span type."""
    return {
        "CHAT_MODEL": "cyan",
        "LLM": "cyan",
        "EMBEDDING": "magenta",
        "RETRIEVER": "yellow",
        "TOOL": "green",
        "AGENT": "blue",
        "CHAIN": "white",
    }.get(span_type, "dim")


@app.command("list")
def list_traces(
    experiment: Optional[str] = typer.Option(None, "--experiment", "-e", help="Filter by experiment ID"),
    server: Optional[str] = typer.Option(None, "--server", "-s", help="Filter by inference server ID"),
    status: Optional[str] = typer.Option(None, "--status", help="Filter by status (ok, error)"),
    limit: int = typer.Option(20, "--limit", "-n", help="Number of traces to show"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    List traces in the workspace.

    Example:
        corerun traces list
        corerun traces list --experiment inference-server-123
        corerun traces list --status error --limit 10
    """
    _init_client()

    from corerun import get_client
    client = get_client()

    params = {"limit": str(limit)}
    if experiment:
        params["experiment_id"] = experiment
    if server:
        params["server_id"] = server
    if status:
        params["status"] = status

    try:
        data = client.get("/traces", params=params, workspace=workspace)
        traces = data.get("traces", [])
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if json_output:
        output.set_json(True)
    if output.json_mode():
        output.emit(traces)
        return

    if not traces:
        console.print("No traces found")
        return

    table = Table(title="Traces")
    table.add_column("Trace ID", style="dim")
    table.add_column("Model", style="cyan")
    table.add_column("Tokens", justify="right")
    table.add_column("Latency", justify="right")
    table.add_column("Status")
    table.add_column("Prompt Preview")
    table.add_column("Time")

    for t in traces:
        status_style = _get_status_style(t.get("status", ""))
        prompt = t.get("prompt_preview", "")[:40]
        if len(t.get("prompt_preview", "")) > 40:
            prompt += "..."

        table.add_row(
            t.get("trace_id", ""),
            t.get("model", "-"),
            str(t.get("total_tokens", 0)),
            _format_duration(t.get("latency_ms", 0)),
            f"[{status_style}]{t.get('status', '-')}[/{status_style}]",
            prompt,
            t.get("start_time", "-")[:19] if t.get("start_time") else "-",
        )

    console.print(table)


@app.command("get")
def get_trace(
    trace_id: str = typer.Argument(..., help="Trace ID"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    Get detailed information about a trace.

    Example:
        corerun traces get abc123
    """
    _init_client()

    from corerun import get_client
    client = get_client()

    try:
        trace = client.get(f"/traces/{trace_id}", workspace=workspace)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if json_output:
        output.set_json(True)
    if output.json_mode():
        output.emit(trace)
        return

    # Header info
    status_style = _get_status_style(trace.get("status", ""))
    console.print(f"[bold]Trace:[/bold] {trace.get('trace_id', '-')}")
    console.print(f"  Model: {trace.get('model', '-')} ({trace.get('provider', '-')})")
    console.print(f"  Status: [{status_style}]{trace.get('status', '-')}[/{status_style}]")
    console.print(f"  Latency: {_format_duration(trace.get('latency_ms', 0))}")
    console.print(f"  Tokens: {trace.get('total_tokens', 0)} (prompt: {trace.get('prompt_tokens', 0)}, completion: {trace.get('completion_tokens', 0)})")
    console.print(f"  Time: {trace.get('start_time', '-')}")
    console.print()

    # Spans tree
    spans = trace.get("spans", [])
    if spans:
        console.print("[bold]Spans:[/bold]")

        # Build tree
        span_map = {s["span_id"]: s for s in spans}
        root_spans = [s for s in spans if not s.get("parent_id")]

        def add_span_to_tree(tree, span):
            style = _get_span_type_style(span.get("span_type", ""))
            duration = _format_duration(span.get("duration_ms", 0))
            label = f"[{style}]{span.get('name', '-')}[/{style}] ({span.get('span_type', '-')}) - {duration}"

            status = span.get("status", "")
            if status == "ERROR":
                label += " [red][ERROR][/red]"

            branch = tree.add(label)

            # Find children
            children = [s for s in spans if s.get("parent_id") == span.get("span_id")]
            for child in children:
                add_span_to_tree(branch, child)

        tree = Tree("[bold]root[/bold]")
        for root in root_spans:
            add_span_to_tree(tree, root)

        console.print(tree)
        console.print()

    # Prompt preview
    if trace.get("prompt_preview"):
        console.print("[bold]Prompt:[/bold]")
        console.print(Panel(trace.get("prompt_preview", ""), border_style="cyan"))

    # Response preview
    if trace.get("response_preview"):
        console.print("[bold]Response:[/bold]")
        console.print(Panel(trace.get("response_preview", ""), border_style="green"))

    # Feedback
    feedback = trace.get("feedback")
    if feedback:
        console.print("[bold]Feedback:[/bold]")
        rating = feedback.get("rating", 0)
        stars = "[yellow]" + "★" * rating + "☆" * (5 - rating) + "[/yellow]"
        console.print(f"  Rating: {stars}")
        if feedback.get("comment"):
            console.print(f"  Comment: {feedback.get('comment')}")

    # Eval scores
    eval_scores = trace.get("eval_scores")
    if eval_scores:
        console.print("[bold]Evaluation Scores:[/bold]")
        for key, value in eval_scores.items():
            score_pct = int(value * 100)
            color = "green" if value >= 0.8 else "yellow" if value >= 0.6 else "red"
            bar = "█" * (score_pct // 10) + "░" * (10 - score_pct // 10)
            console.print(f"  {key}: [{color}]{bar}[/{color}] {score_pct}%")


@app.command("spans")
def list_spans(
    trace_id: str = typer.Argument(..., help="Trace ID"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    List spans in a trace.

    Example:
        corerun traces spans abc123
    """
    _init_client()

    from corerun import get_client
    client = get_client()

    try:
        trace = client.get(f"/traces/{trace_id}", workspace=workspace)
        spans = trace.get("spans", [])
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if json_output:
        output.set_json(True)
    if output.json_mode():
        output.emit(spans)
        return

    if not spans:
        console.print("No spans found")
        return

    table = Table(title=f"Spans in {trace_id[:12]}...")
    table.add_column("Span ID", style="dim")
    table.add_column("Name")
    table.add_column("Type", style="cyan")
    table.add_column("Duration", justify="right")
    table.add_column("Status")
    table.add_column("Parent")

    for s in spans:
        status_style = _get_status_style(s.get("status", ""))
        parent = s.get("parent_id", "")[:8] + "..." if s.get("parent_id") else "[root]"

        table.add_row(
            s.get("span_id", "")[:12] + "...",
            s.get("name", "-"),
            s.get("span_type", "-"),
            _format_duration(s.get("duration_ms", 0)),
            f"[{status_style}]{s.get('status', '-')}[/{status_style}]",
            parent,
        )

    console.print(table)


@app.command("feedback")
def add_feedback(
    trace_id: str = typer.Argument(..., help="Trace ID"),
    rating: int = typer.Argument(..., help="Rating (1-5)"),
    comment: Optional[str] = typer.Option(None, "--comment", "-c", help="Feedback comment"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Add feedback to a trace.

    Example:
        corerun traces feedback abc123 5
        corerun traces feedback abc123 4 --comment "Good response but a bit verbose"
    """
    _init_client()

    if rating < 1 or rating > 5:
        console.print("[red]Error:[/red] Rating must be between 1 and 5")
        raise typer.Exit(1)

    from corerun import get_client
    client = get_client()

    try:
        client.post(
            f"/traces/{trace_id}/feedback",
            json={"rating": rating, "comment": comment or ""},
            workspace=workspace,
        )
        stars = "★" * rating + "☆" * (5 - rating)
        console.print(f"[green]Added feedback:[/green] {stars}")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@app.command("delete")
def delete_trace(
    trace_id: str = typer.Argument(..., help="Trace ID"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    force: bool = typer.Option(False, "--yes", "-y", "--force", "-f", help="Do not ask for confirmation"),
):
    """
    Delete a trace.

    Example:
        corerun traces delete abc123
        corerun traces delete abc123 --force
    """
    _init_client()

    if not force:
        output.confirm(f"Delete trace '{trace_id}'?")

    from corerun import get_client
    client = get_client()

    try:
        client.delete(f"/traces/{trace_id}", workspace=workspace)
        console.print(f"[green]Deleted trace:[/green] {trace_id}")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
