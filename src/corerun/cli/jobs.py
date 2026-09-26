"""
Job CLI commands
"""

import typer
from rich.console import Console

from corerun.cli import output, resolve
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.live import Live
from typing import Optional, List
import time

console = output.console
app = typer.Typer(help="Job management commands")


def _init_client():
    """Initialize client, handling errors gracefully"""
    try:
        from corerun import init
        return init()
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        console.print("Run 'corerun login' to authenticate")
        raise typer.Exit(1)


def _status_style(status: str) -> str:
    """Get style for job status"""
    styles = {
        "pending": "yellow",
        "running": "blue",
        "succeeded": "green",
        "failed": "red",
        "cancelled": "dim",
    }
    return styles.get(status, "white")


def _format_duration(seconds: Optional[float]) -> str:
    """Format duration in human-readable format"""
    if seconds is None:
        return "-"
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds/60:.0f}m {seconds%60:.0f}s"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours}h {minutes}m"



def _resolve(reference: str, workspace) -> str:
    """A name, a full id, or the shortened id the list prints."""
    import corerun.jobs as jobs

    return resolve.by_name_or_id(
        reference, lambda: jobs.list(workspace=workspace), "job"
    )


@app.command("list")
def list_jobs(
    status: Optional[str] = typer.Option(None, "--status", "-s", help="Filter by status"),
    compute: Optional[str] = typer.Option(None, "--compute", "-c", help="Filter by compute target"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    List jobs in the workspace.

    Example:
        corerun jobs list
        corerun jobs list --status running
        corerun jobs list --compute dgx-cluster
    """
    _init_client()

    import corerun.jobs as jobs

    try:
        job_list = jobs.list(status=status, compute=compute, workspace=workspace)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if json_output:
        output.set_json(True)
    if output.json_mode():
        import json
        payload = [j.model_dump(mode="json") for j in job_list]
        output.emit(payload)
        return

    if not job_list:
        console.print("No jobs found")
        return

    table = Table(title="Jobs")
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Compute", no_wrap=True)
    table.add_column("GPU", justify="right")
    table.add_column("Duration", justify="right")
    table.add_column("Created", no_wrap=True)
    table.add_column("ID", style="dim", no_wrap=True)

    for job in job_list:
        status_text = f"[{_status_style(job.status)}]{job.status}[/]"
        table.add_row(
            job.name,
            status_text,
            job.compute_name,
            str(int(job.gpu)) if job.gpu else "-",
            _format_duration(job.duration_seconds),
            job.created_at.strftime("%Y-%m-%d %H:%M"),
            job.id,
        )

    console.print(table)


@app.command("get")
def get_job(
    job_id: str = typer.Argument(..., metavar="JOB", help="Job name or ID"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    Get job details.

    Example:
        corerun jobs get abc123
    """
    _init_client()

    import corerun.jobs as jobs

    resolved = _resolve(job_id, workspace)

    try:
        job = jobs.get(resolved, workspace=workspace)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if json_output:
        output.set_json(True)
    if output.json_mode():
        import json
        output.emit(job.model_dump(mode="json"))
        return

    status_text = f"[{_status_style(job.status)}]{job.status}[/]"

    console.print(f"[bold]Job: {job.name}[/bold]")
    console.print(f"  ID: {job.id}")
    console.print(f"  Status: {status_text}")
    console.print(f"  Compute: {job.compute_name}")
    console.print(f"  Image: {job.image}")
    console.print(f"  GPU: {job.gpu}")
    if job.datasets:
        console.print(f"  Datasets: {', '.join(job.datasets)}")
    if job.duration_seconds:
        console.print(f"  Duration: {_format_duration(job.duration_seconds)}")
    if job.error:
        # While it is pending this is why it has not started -- an image that
        # cannot be pulled, a node the scheduler cannot find. Calling that an
        # error would be wrong: nothing has failed yet, and it may still run.
        if job.status in ("pending", "starting"):
            console.print(f"  Waiting: [yellow]{job.error}[/yellow]")
        else:
            console.print(f"  Error: [red]{job.error}[/red]")
    if job.exit_code is not None:
        console.print(f"  Exit Code: {job.exit_code}")
    console.print(f"  Created: {job.created_at}")
    if job.started_at:
        console.print(f"  Started: {job.started_at}")
    if job.ended_at:
        console.print(f"  Ended: {job.ended_at}")


@app.command("submit", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def submit_job(
    ctx: typer.Context,
    job_file: Optional[str] = typer.Option(None, "--file", "-f", help="Job file (corerun.yaml); flags given beside it override it"),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Job name"),
    image: Optional[str] = typer.Option(None, "--image", "-i", help="Container image"),
    compute: Optional[str] = typer.Option(None, "--compute", "-c", help="Compute target name"),
    command: Optional[str] = typer.Option(None, "--command", help="Command, run by sh -c (or give it after --)"),
    gpu: Optional[float] = typer.Option(None, "--gpu", "-g", help="Number of GPUs (hosts; a profile sets it on Kubernetes)"),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Resource profile name from cluster"),
    experiment: Optional[str] = typer.Option(None, "--experiment", "-x", help="Experiment name for output grouping"),
    datasets: Optional[str] = typer.Option(None, "--datasets", "-d", help="Datasets (comma-separated), mounted under /data/"),
    env: Optional[List[str]] = typer.Option(None, "--env", "-e", help="Environment vars (KEY=VALUE)"),
    source_dir: Optional[str] = typer.Option(None, "--source", "-s", help="Local folder of code, cloned into /code"),
    repo: Optional[str] = typer.Option(None, "--repo", help="Workspace repository to clone (with --source: the one pushed to, default 'jobs')"),
    git_url: Optional[str] = typer.Option(None, "--git-url", help="Repository on another host: owner/name with --connection, or an https URL"),
    connection: Optional[str] = typer.Option(None, "--connection", help="Git connection (name or ID) that reads --git-url"),
    ref: Optional[str] = typer.Option(None, "--ref", help="Branch, tag or commit (with --source: the branch pushed to, default job/<name>)"),
    code_subpath: Optional[str] = typer.Option(None, "--path", help="Directory within the repository to use as /code"),
    working_dir: Optional[str] = typer.Option(None, "--workdir", help="Working directory in container (default: /code when there is code)"),
    no_requirements: bool = typer.Option(False, "--no-requirements", help="Do not install requirements.txt before the command"),
    priority: Optional[str] = typer.Option(None, "--priority", help="low, normal or high"),
    max_runtime: Optional[int] = typer.Option(None, "--max-runtime", help="Stop the job after this many minutes"),
    wait_for_completion: bool = typer.Option(False, "--wait", help="Wait for job to complete"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Submit a training job: your code, an image, a command.

    Your code is a folder -- it does not need to be a git repository. It is
    snapshotted on each submit and appears in the job as /code, the working
    directory. Its .gitignore is honoured; a requirements.txt in it is
    installed before the command (--no-requirements to skip). Read data from
    /data/<dataset>, write results to /outputs and checkpoints to /checkpoints:
    both are kept in the workspace's storage.

    Examples:

        # This folder, one GPU profile; the command goes after --
        corerun jobs submit --name train --image pytorch/pytorch:2.4.0-cuda12.1-cudnn9-runtime \\
            --compute gke-uae-n1 --profile gpu-h100-1 --source . -- python train.py --epochs 10

        # Everything in a file kept with the code (SkyPilot's task format)
        corerun jobs submit -f corerun.yaml

        # Code already in a repository: the workspace's, or GitHub's
        corerun jobs submit ... --repo train --ref main --path src -- python train.py
        corerun jobs submit ... --git-url acme/trainer --connection github --ref v2 -- python train.py
    """
    _init_client()

    import corerun.jobs as jobs
    from corerun import jobfile
    from pathlib import Path

    spec = {}
    if job_file:
        try:
            spec = jobfile.load(job_file)
        except jobfile.JobFileError as e:
            raise output.fail(str(e))

    # The command: after --, or --command, or the file's run. Given after --
    # it is an argv, kept exactly; --command and run go to a shell.
    trailing = list(ctx.args)
    if trailing and trailing[0] == "--":
        trailing = trailing[1:]
    if trailing and command:
        raise output.fail("give the command after -- or with --command, not both")
    if trailing:
        spec["command"] = trailing
    elif command:
        spec["command"] = ["sh", "-c", command]

    flags = {
        "name": name, "image": image, "compute_name": compute, "gpu": gpu, "profile": profile,
        "experiment": experiment, "working_dir": working_dir, "repo": repo, "git_url": git_url,
        "connection": connection, "ref": ref, "path": code_subpath, "priority": priority,
        "max_runtime_minutes": max_runtime,
    }
    spec.update({k: v for k, v in flags.items() if v is not None})
    if datasets:
        spec["datasets"] = [d.strip() for d in datasets.split(",") if d.strip()]
    if env:
        spec["environment"] = {**spec.get("environment", {}), **dict(e.split("=", 1) for e in env if "=" in e)}
    if no_requirements:
        spec["install_requirements"] = False
    if source_dir:
        spec["source_directory"] = Path(source_dir)

    missing = [flag for flag, key in (("--name", "name"), ("--image", "image"), ("--compute", "compute_name")) if not spec.get(key)]
    if missing:
        raise output.fail(f"missing {', '.join(missing)} (as flags, or in the job file)")
    if spec.get("priority") and spec["priority"] not in ("low", "normal", "high"):
        raise output.fail("priority is low, normal or high")
    folder = spec.get("source_directory")
    if folder is not None:
        if not Path(folder).is_dir():
            raise output.fail(f"no such folder: {folder}")
        console.print(f"Pushing code from {Path(folder).resolve()} to repository '{spec.get('repo') or 'jobs'}'")

    try:
        job = jobs.submit(workspace=workspace, **spec)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    console.print(f"[green]✓[/green] Submitted job '{job.name}'")
    console.print(f"  ID: {job.id}")
    console.print(f"  Experiment: {job.experiment or 'default'}")
    code = (job.config or {}).get("code") if isinstance(job.config, dict) else None
    if code:
        where = code.get("repo") or code.get("url") or ""
        console.print(f"  Code: {where}@{str(code.get('ref') or 'default')[:12]} -> /code")
    console.print(f"  Status: [{_status_style(job.status)}]{job.status}[/]")

    if wait_for_completion:
        console.print("\nWaiting for completion...")
        try:
            job = jobs.wait(
                job.id,
                callback=lambda j: console.print(f"  Status: [{_status_style(j.status)}]{j.status}[/]"),
                workspace=workspace,
            )
            console.print(f"\n[green]✓[/green] Job completed with status: [{_status_style(job.status)}]{job.status}[/]")
            if job.exit_code is not None:
                console.print(f"  Exit Code: {job.exit_code}")
        except KeyboardInterrupt:
            console.print("\nInterrupted (job continues running)")


@app.command("logs")
def get_logs(
    job_id: str = typer.Argument(..., metavar="JOB", help="Job name or ID"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow logs (stream)"),
    tail: Optional[int] = typer.Option(None, "--tail", "-n", help="Number of lines"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Get job logs.

    Example:
        corerun jobs logs abc123
        corerun jobs logs abc123 --tail 100
        corerun jobs logs abc123 --follow
    """
    _init_client()

    import corerun.jobs as jobs

    resolved = _resolve(job_id, workspace)

    if follow:
        # Streaming logs
        console.print(f"Following logs for job {job_id}... (Ctrl+C to stop)")
        try:
            last_logs = ""
            while True:
                try:
                    current_logs = jobs.logs(resolved, workspace=workspace)
                    # Print new content
                    if len(current_logs) > len(last_logs):
                        new_content = current_logs[len(last_logs):]
                        console.print(new_content, end="")
                        last_logs = current_logs

                    # Check if job is finished
                    job = jobs.get(resolved, workspace=workspace)
                    if job.is_finished:
                        console.print(f"\n[dim]Job finished with status: {job.status}[/dim]")
                        break

                    time.sleep(2)
                except Exception:
                    time.sleep(2)
        except KeyboardInterrupt:
            console.print("\n[dim]Stopped following logs[/dim]")
    else:
        try:
            log_content = jobs.logs(resolved, tail=tail, workspace=workspace)
            if log_content:
                console.print(log_content)
            else:
                console.print("[dim]No logs available[/dim]")
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)


@app.command("cancel")
def cancel_job(
    job_id: str = typer.Argument(..., metavar="JOB", help="Job name or ID"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Cancel a running job.

    Example:
        corerun jobs cancel abc123
    """
    _init_client()

    import corerun.jobs as jobs

    resolved = _resolve(job_id, workspace)

    try:
        job = jobs.cancel(resolved, workspace=workspace)
        console.print(f"[green]✓[/green] Cancelled job '{job.name}'")
        console.print(f"  Status: [{_status_style(job.status)}]{job.status}[/]")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@app.command("delete")
def delete_job(
    job_id: str = typer.Argument(..., metavar="JOB", help="Job name or ID"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    force: bool = typer.Option(False, "--yes", "-y", "--force", "-f", help="Do not ask for confirmation"),
):
    """
    Delete a job.

    Example:
        corerun jobs delete abc123
        corerun jobs delete abc123 --force
    """
    _init_client()

    import corerun.jobs as jobs

    resolved = _resolve(job_id, workspace)

    if not force:
        output.confirm(f"Delete job '{job_id}'?")

    try:
        jobs.delete(resolved, workspace=workspace)
        console.print(f"[green]✓[/green] Deleted job '{job_id}'")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@app.command("wait")
def wait_for_job(
    job_id: str = typer.Argument(..., metavar="JOB", help="Job name or ID"),
    timeout: Optional[int] = typer.Option(None, "--timeout", "-t", help="Timeout in seconds"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Wait for a job to complete.

    Example:
        corerun jobs wait abc123
        corerun jobs wait abc123 --timeout 3600
    """
    _init_client()

    import corerun.jobs as jobs

    resolved = _resolve(job_id, workspace)

    console.print(f"Waiting for job {job_id}...")

    try:
        def on_update(job):
            console.print(f"  Status: [{_status_style(job.status)}]{job.status}[/]")

        job = jobs.wait(resolved, timeout=timeout, callback=on_update, workspace=workspace)
        console.print(f"\n[green]✓[/green] Job completed")
        console.print(f"  Status: [{_status_style(job.status)}]{job.status}[/]")
        if job.exit_code is not None:
            console.print(f"  Exit Code: {job.exit_code}")
        if job.error:
            console.print(f"  Error: {job.error}")
    except KeyboardInterrupt:
        console.print("\nInterrupted (job continues running)")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
