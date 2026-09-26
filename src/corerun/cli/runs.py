"""
Runs CLI: what training jobs, fine-tunes and notebooks logged.

    corerun runs list default --filter "metrics.loss < 0.5" --order-by metrics.loss
    corerun runs show <run-id>
    corerun runs metric <run-id> loss
    corerun runs files <run-id> [path]
    corerun runs download <run-id> model/MLmodel -o ./MLmodel
    corerun runs register <run-id> churn-model
    corerun runs models <run-id>
"""

import json
import math
from typing import Optional

import typer
from rich.table import Table

from corerun.cli import output
from corerun.exceptions import CoreRunError

console = output.console
app = typer.Typer(help="Runs: parameters, metrics and files logged by jobs and notebooks")


def _init_client():
    try:
        from corerun import init

        return init()
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        console.print("Run 'corerun login' to authenticate")
        raise typer.Exit(1)


def _called(work):
    try:
        return work()
    except CoreRunError as e:
        raise output.fail(str(e))


def _value(v: Optional[float]) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "-"
    if isinstance(v, float) and math.isinf(v):
        return "inf" if v > 0 else "-inf"
    a = abs(v)
    if a and (a < 0.001 or a >= 1e6):
        return f"{v:.2e}"
    return f"{v:.4g}"


def _duration(ms: Optional[int]) -> str:
    if ms is None:
        return "-"
    s = ms / 1000
    if s < 60:
        return f"{s:.1f}s"
    if s < 3600:
        return f"{int(s // 60)}m {int(s % 60)}s"
    return f"{int(s // 3600)}h {int((s % 3600) // 60)}m"


def _run_json(run) -> dict:
    return {
        "run_id": run.run_id,
        "name": run.name,
        "experiment_id": run.experiment_id,
        "status": run.status,
        "start_time": run.start_time.isoformat() if run.start_time else None,
        "end_time": run.end_time.isoformat() if run.end_time else None,
        "duration_ms": run.duration_ms,
        "user": run.user,
        "job_id": run.job_id,
        "code": run.code,
        "outputs": run.outputs,
        "metrics": {k: (None if isinstance(v, float) and (math.isnan(v) or math.isinf(v)) else v) for k, v in run.metrics.items()},
        "params": run.params,
        "tags": {k: v for k, v in run.tags.items() if not k.startswith("mlflow.")},
    }


@app.command("list")
def list_runs(
    experiment: str = typer.Argument(..., help="Experiment name or id"),
    filter: Optional[str] = typer.Option(None, "--filter", "-f", help="e.g. \"metrics.loss < 0.5 AND params.lr = '0.001'\""),
    order_by: Optional[str] = typer.Option(None, "--order-by", help="metrics.loss, or -metrics.loss for descending"),
    metric: Optional[str] = typer.Option(None, "--metric", "-m", help="Show this metric as a column (repeatable)", show_default=False),
    limit: int = typer.Option(25, "--limit"),
    deleted: bool = typer.Option(False, "--deleted", help="List deleted runs instead"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """List runs in an experiment, newest first unless ordered."""
    _init_client()
    from corerun import runs

    found = _called(lambda: runs.search(experiment, filter=filter, order_by=order_by, limit=limit, deleted=deleted, workspace=workspace))
    if json_output:
        print(json.dumps([_run_json(r) for r in found], indent=2))
        return
    if not found:
        console.print("[dim]No runs match.[/dim]")
        return
    # Which metrics to show: the one asked for, else the first few every run has.
    keys = [metric] if metric else sorted({k for r in found for k in r.metrics if not k.startswith("system/")}, key=lambda k: (k not in ("loss", "train_loss"), k))[:4]
    table = Table()
    table.add_column("Run ID", style="dim", no_wrap=True)
    table.add_column("Name")
    table.add_column("Status")
    table.add_column("Duration", justify="right")
    for k in keys:
        table.add_column(k, justify="right")
    for r in found:
        table.add_row(r.run_id, r.name, r.status, _duration(r.duration_ms), *[_value(r.metrics.get(k)) for k in keys])
    console.print(table)


@app.command("show")
def show_run(
    run_id: str = typer.Argument(..., help="Run id"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """A run's status, parameters, latest metrics and tags."""
    _init_client()
    from corerun import runs

    run = _called(lambda: runs.get(run_id, workspace=workspace))
    if json_output:
        print(json.dumps(_run_json(run), indent=2))
        return
    console.print(f"[bold]{run.name}[/bold]  {run.status}  [dim]{run.run_id}[/dim]")
    console.print(f"experiment {run.experiment_id} · duration {_duration(run.duration_ms)}" + (f" · job {run.job_id}" if run.job_id else ""))
    if run.code:
        console.print("code: " + ", ".join(f"{k} {v}" for k, v in run.code.items()))
    if run.outputs:
        console.print(f"outputs: {run.outputs}")
    for title, items in (("Parameters", run.params), ("Metrics", {k: _value(v) for k, v in run.metrics.items()})):
        if not items:
            continue
        table = Table(title=title, title_justify="left")
        table.add_column("Key")
        table.add_column("Value")
        for k in sorted(items):
            table.add_row(k, str(items[k]))
        console.print(table)
    own = {k: v for k, v in run.tags.items() if not k.startswith(("mlflow.", "corerun."))}
    if own:
        console.print("Tags: " + ", ".join(f"{k}={v}" for k, v in sorted(own.items())))


@app.command("metric")
def metric_history(
    run_id: str = typer.Argument(..., help="Run id"),
    key: str = typer.Argument(..., help="Metric name"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """Every value a run logged for one metric, by step."""
    _init_client()
    from corerun import runs

    points = _called(lambda: runs.metric_history(run_id, key, workspace=workspace))
    if json_output:
        print(json.dumps([{"step": p.step, "value": None if math.isnan(p.value) else p.value, "timestamp": p.timestamp.isoformat() if p.timestamp else None} for p in points], indent=2))
        return
    if not points:
        console.print(f"[dim]No values for {key}.[/dim]")
        return
    table = Table()
    table.add_column("Step", justify="right")
    table.add_column(key, justify="right")
    for p in points:
        table.add_row(str(p.step), _value(p.value))
    console.print(table)


@app.command("files")
def list_files(
    run_id: str = typer.Argument(..., help="Run id"),
    path: Optional[str] = typer.Argument(None, help="A folder within the run"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """The files a run wrote, one folder at a time."""
    _init_client()
    from corerun import runs

    found = _called(lambda: runs.files(run_id, path, workspace=workspace))
    if json_output:
        print(json.dumps([{"path": f.path, "is_dir": f.is_dir, "size": f.size} for f in found], indent=2))
        return
    if not found:
        console.print("[dim]No files.[/dim]")
        return
    for f in found:
        console.print(f"{f.path}/" if f.is_dir else f"{f.path}  [dim]{f.size if f.size is not None else ''}[/dim]")


@app.command("download")
def download_file(
    run_id: str = typer.Argument(..., help="Run id"),
    path: str = typer.Argument(..., help="The file, as `corerun runs files` lists it"),
    out: Optional[str] = typer.Option(None, "--output", "-o", help="Where to save it (default: its name, here)"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """Save one of a run's files."""
    _init_client()
    from corerun import runs

    dest = out or path.rstrip("/").split("/")[-1]
    written = _called(lambda: runs.download(run_id, path, dest, workspace=workspace))
    console.print(f"Saved {dest} ({written} bytes)")


@app.command("register")
def register_run(
    run_id: str = typer.Argument(..., help="Run id"),
    model: str = typer.Argument(..., help="Model to add the version to (created if missing)"),
    description: Optional[str] = typer.Option(None, "--description", "-d"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """Register what the job behind a run wrote to its outputs folder, as a new model version."""
    _init_client()
    from corerun import registry, runs

    run = _called(lambda: runs.get(run_id, workspace=workspace))
    if not run.job_id:
        raise output.fail("This run was not written by a job, so there are no job outputs to register.")
    version = _called(lambda: registry.publish_from_job(model, run.job_id, description=description, workspace=workspace))
    console.print(f"[green]{model} v{version.version}[/green] from run {run.name}" + (f" ({run.outputs})" if run.outputs else ""))


@app.command("models")
def run_models(
    run_id: str = typer.Argument(..., help="Run id"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """What a run became in the model registry, and where each version's files are."""
    _init_client()
    from corerun import registry, runs

    run = _called(lambda: runs.get(run_id, workspace=workspace))
    found = _called(lambda: registry.versions_from_runs([run.run_id, run.job_id or ""], workspace=workspace))
    if json_output:
        print(json.dumps([{"model": v.get("model_name"), "version": v.get("version"), "stage": v.get("stage"), "location": v.get("location")} for v in found], indent=2))
        return
    if not found:
        console.print("[dim]Nothing from this run is in the registry.[/dim]")
        return
    table = Table()
    table.add_column("Model")
    table.add_column("Version", justify="right")
    table.add_column("Stage")
    table.add_column("Stored at")
    for v in found:
        table.add_row(v.get("model_name", ""), str(v.get("version", "")), v.get("stage", ""), v.get("location") or "-")
    console.print(table)
