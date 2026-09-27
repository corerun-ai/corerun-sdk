"""
Fine-tuning CLI commands
"""

from typing import List, Optional

import typer
from rich.console import Console

from corerun.cli import output
from rich.table import Table

console = output.console
app = typer.Typer(help="Fine-tuning job commands")


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
    styles = {
        "pending": "yellow",
        "running": "blue",
        "succeeded": "green",
        "failed": "red",
        "cancelled": "dim",
    }
    return styles.get(status, "white")


@app.command("list")
def list_jobs(
    status: Optional[str] = typer.Option(None, "--status", "-s", help="Filter by status"),
    framework: Optional[str] = typer.Option(None, "--framework", "-f", help="Filter by framework"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    List fine-tuning jobs.

    Example:
        corerun finetune list
        corerun finetune list --status running
        corerun finetune list --status running
    """
    _init_client()

    import corerun.finetune as ft

    try:
        jobs = ft.list(status=status, framework=framework, workspace=workspace)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if json_output:
        output.set_json(True)
    if output.json_mode():
        import json
        output.emit([j.model_dump(mode="json") for j in jobs])
        return

    if not jobs:
        console.print("No fine-tuning jobs found")
        return

    table = Table(title="Fine-tuning Jobs")
    table.add_column("ID", style="dim", no_wrap=True)
    table.add_column("Name", style="cyan")
    table.add_column("Framework")
    table.add_column("Model")
    table.add_column("Method")
    table.add_column("Status")
    table.add_column("GPU", justify="right")
    table.add_column("Epochs", justify="right")

    for job in jobs:
        status_text = f"[{_status_style(job.status)}]{job.status}[/]"
        table.add_row(
            job.id,
            job.name,
            job.framework,
            job.base_model[:30] + ("..." if len(job.base_model) > 30 else ""),
            job.method,
            status_text,
            str(int(job.gpu)) if job.gpu else "-",
            str(job.epochs),
        )

    console.print(table)


@app.command("get")
def get_job(
    job_id: str = typer.Argument(..., help="Fine-tuning job ID"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    Get fine-tuning job details.

    Example:
        corerun finetune get abc123
    """
    _init_client()

    import corerun.finetune as ft

    try:
        job = ft.get(job_id, workspace=workspace)
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
    console.print(f"[bold]Fine-tune Job: {job.name}[/bold]")
    console.print(f"  ID: {job.id}")
    console.print(f"  Status: {status_text}")
    console.print(f"  Framework: {job.framework}")
    console.print(f"  Base Model: {job.base_model}")
    console.print(f"  Method: {job.method}")
    console.print(f"  Dataset: {job.dataset_name or job.dataset_id}")
    console.print(f"  Compute: {job.compute_name}")
    console.print(f"  GPU: {job.gpu}")
    console.print(f"  Epochs: {job.epochs}  Batch Size: {job.batch_size}  LR: {job.learning_rate}")
    if job.method in ("lora", "qlora"):
        console.print(f"  LoRA: r={job.lora_r}  alpha={job.lora_alpha}  max_seq={job.max_seq_length}")
    if job.experiment:
        console.print(f"  Experiment: {job.experiment}")
    if job.error:
        console.print(f"  Error: [red]{job.error}[/red]")
    if job.created_at:
        console.print(f"  Created: {job.created_at}")


@app.command("create")
def create_job(
    name: str = typer.Option(..., "--name", "-n", help="Job name"),
    framework: str = typer.Option("hf_trainer", "--framework", "-f", help="Trainer framework"),
    base_model: str = typer.Option(..., "--model", "-m", help="Hugging Face id, or registry://name"),
    dataset_id: str = typer.Option(..., "--dataset", "-d", help="Dataset name or ID"),
    compute: str = typer.Option(..., "--compute", "-c", help="Compute target name"),
    method: str = typer.Option("lora", "--method", help="Method: lora, qlora, or full"),
    gpu: float = typer.Option(1.0, "--gpu", "-g", help="Number of GPUs"),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Resource profile name"),
    epochs: int = typer.Option(1, "--epochs", "-e", help="Training epochs"),
    batch_size: int = typer.Option(2, "--batch-size", help="Batch size per device"),
    learning_rate: float = typer.Option(2e-4, "--lr", help="Learning rate"),
    lora_r: int = typer.Option(16, "--lora-r", help="LoRA rank"),
    lora_alpha: int = typer.Option(16, "--lora-alpha", help="LoRA alpha"),
    max_seq_length: int = typer.Option(2048, "--max-seq-len", help="Max sequence length"),
    experiment: Optional[str] = typer.Option(None, "--experiment", "-x", help="Experiment name"),
    result_name: Optional[str] = typer.Option(None, "--as", help="Register the result in the model registry under this name when it succeeds"),
    eval_split: float = typer.Option(0.0, "--eval-split", help="Share held out for evaluation, e.g. 0.1 for 90/10"),
    priority: Optional[str] = typer.Option(None, "--priority", help="low, normal or high"),
    max_runtime: Optional[int] = typer.Option(None, "--max-runtime", help="Stop the job after this many minutes"),
    wait_for_completion: bool = typer.Option(False, "--wait", help="Wait for job to complete"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Create a fine-tuning job.

    Example:
        corerun finetune create --name llama3-arabic \\
            --model meta-llama/Llama-3.1-8B-Instruct --dataset arabic-support-chat \\
            --compute gke-uae-n1 --profile gpu-h100-1 --method lora --eval-split 0.1 \\
            --as llama3-8b-arabic-chat
    """
    _init_client()

    import corerun.finetune as ft

    try:
        job = ft.create(
            name=name,
            framework=framework,
            base_model=base_model,
            dataset_id=ft.dataset_id(dataset_id, workspace=workspace),
            compute_name=compute,
            method=method,
            gpu=gpu,
            profile=profile,
            epochs=epochs,
            batch_size=batch_size,
            learning_rate=learning_rate,
            lora_r=lora_r,
            lora_alpha=lora_alpha,
            max_seq_length=max_seq_length,
            experiment=experiment,
            workspace=workspace,
            result_name=result_name,
            eval_fraction=eval_split,
            priority=priority,
            max_runtime_minutes=max_runtime,
        )
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    console.print(f"[green]✓[/green] Created fine-tuning job '{job.name}'")
    console.print(f"  ID: {job.id}")
    console.print(f"  Framework: {job.framework}  Method: {job.method}")
    console.print(f"  Model: {job.base_model}")
    if job.result_name:
        console.print(f"  Result: registered as {job.result_name} when it succeeds")
    console.print(f"  Status: [{_status_style(job.status)}]{job.status}[/]")

    if wait_for_completion:
        console.print("\nWaiting for completion...")
        try:
            def on_update(j):
                console.print(f"  Status: [{_status_style(j.status)}]{j.status}[/]")

            job = ft.wait(job.id, callback=on_update, workspace=workspace)
            console.print(f"\n[green]✓[/green] Completed with status: [{_status_style(job.status)}]{job.status}[/]")
            if job.error:
                console.print(f"  Error: {job.error}")
        except KeyboardInterrupt:
            console.print("\nInterrupted (job continues running)")


@app.command("delete")
def delete_job(
    job_id: str = typer.Argument(..., help="Fine-tuning job ID"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    force: bool = typer.Option(False, "--yes", "-y", "--force", "-f", help="Do not ask for confirmation"),
):
    """
    Delete a fine-tuning job.

    Example:
        corerun finetune delete abc123
        corerun finetune delete abc123 --force
    """
    _init_client()

    import corerun.finetune as ft

    if not force:
        output.confirm(f"Delete fine-tuning job '{job_id}'?")

    try:
        ft.delete(job_id, workspace=workspace)
        console.print(f"[green]✓[/green] Deleted fine-tuning job '{job_id}'")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@app.command("stop")
def stop_job(
    job_id: str = typer.Argument(..., help="Fine-tuning job ID"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Stop a running fine-tune. Its record and what it wrote so far stay.

    Example:
        corerun finetune stop abc123
    """
    _init_client()
    import corerun.finetune as ft

    try:
        ft.stop(job_id, workspace=workspace)
    except Exception as e:
        raise output.fail(str(e))
    console.print(f"[green]Stopped[/green] {job_id}")


@app.command("logs")
def job_logs(
    job_id: str = typer.Argument(..., help="Fine-tuning job ID"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Keep printing until it finishes"),
    tail: Optional[int] = typer.Option(None, "--tail", "-n", help="Only the last N lines"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    The trainer's output: loss as it is logged, and why it stopped if it did.

    Example:
        corerun finetune logs abc123 --follow
    """
    _init_client()
    import corerun.finetune as ft

    if not follow:
        try:
            console.print(ft.logs(job_id, tail=tail, workspace=workspace), end="", markup=False, highlight=False)
        except Exception as e:
            raise output.fail(str(e))
        return

    # Until the fine-tune ends, then stop, as `jobs logs --follow` does: a
    # failure that may pass is retried a few times in the SDK, and one that
    # persists ends the command non-zero with its reason.
    try:
        for chunk in ft.follow_logs(job_id, workspace=workspace):
            console.print(chunk, end="", markup=False, highlight=False)
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped following logs (the fine-tune keeps running)[/dim]")
        raise typer.Exit(130)
    except Exception as e:
        console.print()
        raise output.fail(f"Could not read the fine-tune's logs: {e}")
    try:
        status = ft.get(job_id, workspace=workspace).status
        console.print(f"\n[dim]Fine-tune finished with status: {status}[/dim]")
    except Exception:
        console.print("\n[dim]Fine-tune finished[/dim]")


@app.command("wait")
def wait_for_job(
    job_id: str = typer.Argument(..., help="Fine-tuning job ID"),
    timeout: Optional[int] = typer.Option(None, "--timeout", "-t", help="Timeout in seconds"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Wait for a fine-tuning job to complete.

    Example:
        corerun finetune wait abc123
        corerun finetune wait abc123 --timeout 7200
    """
    _init_client()

    import corerun.finetune as ft

    console.print(f"Waiting for fine-tuning job {job_id}...")

    try:
        def on_update(job):
            console.print(f"  Status: [{_status_style(job.status)}]{job.status}[/]")

        job = ft.wait(job_id, timeout=timeout, callback=on_update, poll_interval=15, workspace=workspace)
        console.print(f"\n[green]✓[/green] Job completed")
        console.print(f"  Status: [{_status_style(job.status)}]{job.status}[/]")
        if job.error:
            console.print(f"  Error: {job.error}")
    except KeyboardInterrupt:
        console.print("\nInterrupted (job continues running)")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


def _params(n: int) -> str:
    """A parameter count as people say it: 8.2B, 350M."""
    if n >= 1e9:
        return f"{n / 1e9:.1f}B"
    if n >= 1e6:
        return f"{n / 1e6:.0f}M"
    return str(n)


@app.command("plan")
def plan_job(
    base_model: str = typer.Argument(..., metavar="MODEL", help="Hugging Face id, or registry://name"),
    compute: Optional[str] = typer.Option(None, "--compute", "-c", help="Cluster to plan against"),
    batch_size: Optional[int] = typer.Option(
        None, "--batch-size", "-b", help="Per-GPU batch size (default 4)"
    ),
    max_seq_length: Optional[int] = typer.Option(
        None, "--max-seq-length", help="Sequence length (default 2048)"
    ),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    What a fine-tune of a model would need, and where it fits. Creates nothing.

    The memory each method needs per GPU, each of the compute's profiles and
    which methods fit on it, and the method and profile to start with. Every
    figure is an estimate.

    Example:
        corerun finetune plan Qwen/Qwen3-8B
        corerun finetune plan Qwen/Qwen3-8B --compute dgx --batch-size 8
    """
    _init_client()

    import corerun.finetune as ft

    try:
        plan = ft.plan(
            base_model,
            compute_name=compute,
            batch_size=batch_size,
            max_seq_length=max_seq_length,
            workspace=workspace,
        )
    except Exception as e:
        raise output.fail(str(e))

    if json_output:
        output.set_json(True)
    if output.json_mode():
        output.emit(plan)
        return

    model = plan.get("model") or {}
    source = model.get("source", "")
    console.print(f"[bold]{model.get('id') or base_model}[/bold]  [dim]({source})[/dim]")
    if model.get("parameters"):
        size = _params(model["parameters"])
        if model.get("estimated"):
            size += " (estimated from the weights' size)"
        console.print(f"  Parameters: {size}")
    if model.get("dtype"):
        console.print(f"  Weights:    {model['dtype']}")
    if model.get("gated"):
        token = (
            "a token is on file" if model.get("token_on_file")
            else "[red]no Hugging Face token is on file[/red]"
        )
        console.print(f"  Gated:      yes -- {token}")
    if model.get("note"):
        console.print(f"  [yellow]{model['note']}[/yellow]")

    needs = plan.get("needs_gb") or {}
    if needs:
        console.print("  Needs per GPU: " + "  ".join(
            f"{m} {needs[m]:g} GB" for m in ("lora", "qlora", "full") if m in needs
        ))

    cluster = plan.get("cluster")
    if cluster:
        state = "connected" if cluster.get("connected") else "[yellow]not connected[/yellow]"
        gpu = cluster.get("gpu_model") or "no GPU reported"
        if cluster.get("gpu_memory_gb"):
            gpu += f", {cluster['gpu_memory_gb']:g} GB"
        console.print(
            f"  Compute:    {cluster.get('name')} ({state}) -- {gpu}; "
            f"{cluster.get('gpus_free', 0)} of {cluster.get('gpus_total', 0)} GPUs free"
        )

    profiles = plan.get("profiles") or []
    fits = plan.get("fits") or {}
    if profiles:
        table = Table(show_header=True, header_style="bold")
        table.add_column("Profile", style="cyan")
        table.add_column("GPUs", justify="right")
        table.add_column("GPU")
        for method in ("lora", "qlora", "full"):
            table.add_column(method, justify="center")
        for p in profiles:
            name = p.get("name", "")
            if p.get("default"):
                name += " [dim](default)[/dim]"
            gpu = p.get("gpu_model") or "-"
            if p.get("gpu_memory_gb"):
                gpu += f" {p['gpu_memory_gb']:g} GB"
            verdicts = fits.get(p.get("name"), {})
            table.add_row(
                name,
                f"{p.get('gpus', 0):g}",
                gpu,
                *(
                    "[green]fits[/green]" if verdicts.get(m) else "[dim]-[/dim]"
                    for m in ("lora", "qlora", "full")
                ),
            )
        console.print(table)

    rec = plan.get("recommended") or {}
    where = ""
    if rec.get("profile"):
        where = f" on profile {rec['profile']}"
    elif rec.get("gpus"):
        where = f" on {rec['gpus']} GPU{'s' if rec['gpus'] > 1 else ''}"
    method = rec.get("method", "lora")
    console.print(f"  Recommended: [bold]{method}[/bold]{where} -- {rec.get('reason', '')}")
