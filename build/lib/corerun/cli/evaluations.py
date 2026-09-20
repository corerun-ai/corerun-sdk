"""
Evaluation CLI commands.

An evaluation is a job that measures a model, and its result is an experiment
run. Both ids are printed when one starts, because they answer different
questions: the job id is what has logs, the run id is what will have scores.
"""

from typing import List, Optional

import typer
from rich.table import Table

from corerun.cli import output

console = output.console
app = typer.Typer(help="Run benchmarks against a model")


def _init_client():
    try:
        from corerun import init

        return init()
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        console.print("Run 'corerun login' to authenticate")
        raise typer.Exit(1)


def _called(fn):
    """Run one API call, turning a failure into a message rather than a trace."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 — the CLI reports, it does not raise
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


_STATUS_STYLE = {
    "pending": "yellow",
    "starting": "yellow",
    "running": "blue",
    "completed": "green",
    "succeeded": "green",
    "failed": "red",
    "stopped": "dim",
}


def _styled(status: str) -> str:
    return f"[{_STATUS_STYLE.get(status, 'white')}]{status or '-'}[/]"


@app.command("start")
def start_evaluation(
    benchmark: str = typer.Argument(..., help="The benchmark to run, e.g. gsm8k"),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="A name for this run"),
    compute: str = typer.Option(..., "--compute", "-c", help="Where to run it"),
    endpoint: Optional[str] = typer.Option(
        None, "--endpoint", "-e", help="An endpoint in this workspace to measure"
    ),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="The model to ask for"),
    base_url: Optional[str] = typer.Option(
        None, "--base-url", help="Any OpenAI-compatible address, instead of --endpoint"
    ),
    api_key: Optional[str] = typer.Option(None, "--api-key", help="Key for --base-url"),
    limit: Optional[int] = typer.Option(
        None, "--limit", "-l", help="Stop after this many examples"
    ),
    shots: Optional[int] = typer.Option(None, "--shots", help="Few-shot examples"),
    framework: Optional[str] = typer.Option(None, "--framework", help="Which harness"),
    sandbox: bool = typer.Option(
        False, "--sandbox", help="The benchmark runs each task in its own container"
    ),
    experiment: Optional[str] = typer.Option(
        None, "--experiment", help="Where the result is recorded"
    ),
    gpu: float = typer.Option(0, "--gpu", help="GPUs for the runner itself"),
    profile: Optional[str] = typer.Option(None, "--profile", help="Resource profile"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """Start a benchmark against a model.

    A limit is worth passing. A full benchmark is thousands of model calls,
    and the point of a first run is usually to find out whether the plumbing
    works rather than what the model scores.
    """
    _init_client()
    from corerun import evaluations as api

    if not endpoint and not base_url:
        console.print(
            "[red]Error:[/red] name a target: --endpoint <name>, "
            "or --base-url <url> --model <name>"
        )
        raise typer.Exit(1)

    started = _called(
        lambda: api.start(
            name=name or f"{benchmark}-{endpoint or 'external'}",
            compute=compute,
            benchmark=benchmark,
            endpoint=endpoint,
            model=model,
            base_url=base_url,
            api_key=api_key,
            limit=limit,
            shots=shots,
            framework=framework,
            sandbox=sandbox,
            experiment=experiment,
            gpu=gpu,
            profile=profile,
            workspace=workspace,
        )
    )

    console.print(f"[green]Started[/green] {started.name}")
    console.print(f"  job {started.id}")
    # Printed together and labelled, because they are not interchangeable: one
    # has the logs, the other will have the scores.
    console.print(f"  run {started.run_id}  (scores land here)")
    console.print()
    console.print(f"  corerun evaluations logs {started.id}")
    if limit is None:
        console.print(
            "[yellow]No limit set[/yellow] — this runs the whole benchmark, "
            "which is thousands of model calls."
        )


@app.command("list")
def list_evaluations(
    status: Optional[str] = typer.Option(None, "--status", "-s", help="Only this status"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """Evaluations in this workspace, newest first."""
    _init_client()
    from corerun import evaluations as api

    rows: List = _called(lambda: api.evaluations(status=status, workspace=workspace))
    if not rows:
        console.print("[yellow]No evaluations yet.[/yellow]")
        console.print("Start one with: corerun evaluations start gsm8k --compute <target> --endpoint <name> --limit 10")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Name")
    table.add_column("Benchmark")
    table.add_column("Target")
    table.add_column("Status")
    table.add_column("Job", style="dim")
    for row in rows:
        table.add_row(
            row.name,
            row.benchmark or "-",
            row.target or "-",
            _styled(row.status),
            row.id[:8],
        )
    console.print(table)


@app.command("show")
def show_evaluation(
    evaluation_id: str = typer.Argument(..., help="The job id from `start`"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """One evaluation, and where to look next."""
    _init_client()
    from corerun import evaluations as api

    row = _called(lambda: api.evaluation(evaluation_id, workspace=workspace))

    console.print(f"[bold]{row.name}[/bold]")
    console.print(f"  status     {_styled(row.status)}")
    console.print(f"  benchmark  {row.benchmark or '-'}")
    console.print(f"  target     {row.target or '-'}")
    console.print(f"  compute    {row.compute_name or '-'}")
    console.print(f"  job        {row.id}")
    console.print(f"  run        {row.run_id or '-'}")
    if row.experiment:
        console.print(f"  experiment {row.experiment}")
    if row.error:
        if row.status in ("pending", "starting"):
            console.print(f"  [yellow]waiting[/yellow]    {row.error}")
        else:
            console.print(f"  [red]error[/red]      {row.error}")

    console.print()
    console.print(f"  logs:   corerun evaluations logs {row.id}")
    if row.run_id:
        console.print(f"  scores: corerun genai evaluations -e {row.experiment_id or '<experiment>'}")


@app.command("logs")
def evaluation_logs(
    evaluation_id: str = typer.Argument(..., help="The job id from `start`"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """What the runner printed.

    Not `corerun jobs logs`: that route does not exist in a GenAI workspace,
    which is where evaluations usually run.
    """
    _init_client()
    from corerun import evaluations as api

    output = _called(lambda: api.logs(evaluation_id, workspace=workspace))
    if not output.strip():
        console.print("[yellow]No output yet.[/yellow] The runner may still be starting.")
        return
    # Printed raw. It is a benchmark's own output, and rich would try to read
    # its brackets and progress bars as markup.
    print(output)


@app.command("stop")
def stop_evaluation(
    evaluation_id: str = typer.Argument(..., help="The job id from `start`"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """Cancel a running evaluation."""
    _init_client()
    from corerun import evaluations as api

    _called(lambda: api.stop(evaluation_id, workspace=workspace))
    console.print(f"[green]Stopped[/green] {evaluation_id}")
    console.print("  The run keeps whatever it had already scored.")
