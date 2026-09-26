"""
Inference server CLI commands
"""

import json as _json
from typing import Optional, List

import typer
from rich.console import Console

from corerun.cli import output, resolve
from rich.table import Table

console = output.console
app = typer.Typer(help="Inference server commands")


def _init_client():
    """Initialize client, handling errors gracefully"""
    try:
        from corerun import init
        return init()
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        console.print("Run 'corerun login' to authenticate")
        raise typer.Exit(1)


def _resolve(reference: str, workspace) -> str:
    """A name, a full id, or the shortened id the list prints."""
    import corerun.inference as inference

    return resolve.by_name_or_id(
        reference, lambda: inference.list(workspace=workspace), "inference server"
    )


def _status_style(status: str) -> str:
    styles = {
        "pending": "yellow",
        "deploying": "yellow",
        "starting": "yellow",
        "running": "green",
        "stopped": "dim",
        "failed": "red",
    }
    return styles.get(status, "white")


@app.command("list")
def list_servers(
    status: Optional[str] = typer.Option(None, "--status", "-s", help="Filter by status"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    List inference servers.

    Example:
        corerun inference list
        corerun inference list --status running
    """
    _init_client()

    import corerun.inference as inference

    try:
        servers = inference.list(status=status, workspace=workspace)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if json_output:
        output.set_json(True)
    if output.json_mode():
        output.emit([s.model_dump(mode="json") for s in servers])
        return

    if not servers:
        console.print("No inference servers found")
        return

    table = Table(title="Inference Servers")
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Type", no_wrap=True)
    table.add_column("Model")
    table.add_column("Status", no_wrap=True)
    table.add_column("GPU", justify="right")
    table.add_column("Replicas", justify="right")
    table.add_column("ID", style="dim", no_wrap=True)

    for s in servers:
        model = s.model_id or "-"
        if len(model) > 30:
            model = model[:30] + "..."
        table.add_row(
            s.name,
            s.server_type,
            model,
            f"[{_status_style(s.status)}]{s.status}[/]",
            str(int(s.gpu)) if s.gpu else "-",
            str(s.replicas),
            s.id,
        )

    console.print(table)


@app.command("get")
def get_server(
    server_id: str = typer.Argument(..., metavar="SERVER", help="Server name or ID"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    Show an inference server's details.

    Example:
        corerun inference get abc123
    """
    _init_client()

    import corerun.inference as inference

    resolved = _resolve(server_id, workspace)

    try:
        s = inference.get(resolved, workspace=workspace)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if json_output:
        output.set_json(True)
    if output.json_mode():
        output.emit(s.model_dump(mode="json"))
        return

    console.print(f"\n[bold cyan]{s.name}[/]  [dim]{s.id}[/]")
    console.print(f"  Status:   [{_status_style(s.status)}]{s.status}[/]")
    console.print(f"  Type:     {s.server_type}")
    console.print(f"  Model:    {s.model_id} [dim]({s.model_source})[/]")
    console.print(f"  Compute:  {s.compute_name}")
    console.print(f"  GPU:      {s.gpu}")
    console.print(f"  Replicas: {s.replicas} [dim](min {s.min_replicas}, max {s.max_replicas})[/]")

    if s.endpoint_name:
        console.print(f"  Endpoint: {s.endpoint_name}")
    if s.openai_base_url:
        console.print(f"  OpenAI base URL: {s.openai_base_url}")
    if s.image:
        engine = f" [dim](engine {s.engine_version})[/dim]" if s.engine_version else ""
        console.print(f"  Image:    {s.image}{engine}")
    if s.features:
        console.print(f"  Features: {', '.join(s.features)}")
    _print_facts(s.model_facts)

    # What the engine runs with: the recipe's arguments, the card's, and the
    # operator's together, as a deployment resolves them. Only the operator's
    # own are stored, so they alone would hide the parsers a model needs, and
    # a model that cannot call tools would look exactly like one that can.
    try:
        p = inference.plan(resolved, workspace=workspace)
        args = p.get("extra_args") or []
        console.print(f"  Engine args: {' '.join(args) if args else '[dim](none)[/dim]'}")
        if s.extra_args:
            console.print(f"    [dim]yours: {' '.join(s.extra_args)}[/dim]")
        if p.get("recipe_source"):
            console.print(f"    [dim]recipe: {p['recipe_source']}[/dim]")
        if p.get("recipe_note"):
            console.print(f"    [dim]{p['recipe_note']}[/dim]")
    except Exception:
        if s.extra_args:
            console.print(f"  Engine args (yours): {' '.join(s.extra_args)}")
    if s.error:
        console.print(f"  [red]Error:[/red] {s.error}")

    if s.lora_modules:
        table = Table(title="LoRA Modules")
        table.add_column("Name", style="cyan")
        table.add_column("Source")
        for m in s.lora_modules:
            table.add_row(m.name, m.source or "-")
        console.print(table)


@app.command("deploy")
def deploy_server(
    name: str = typer.Option(..., "--name", "-n", help="Server name"),
    model: str = typer.Option(
        ..., "--model", "-m", help="Model ID (HuggingFace ID, registry name, or path)"
    ),
    compute: str = typer.Option(..., "--compute", "-c", help="Compute target name"),
    server_type: str = typer.Option(
        "vllm", "--type", "-t", help="Server engine: vllm, sglang or ollama"
    ),
    model_source: str = typer.Option(
        "huggingface", "--source",
        help="Where the weights come from: huggingface, mlflow, registry, or path "
             "for a directory already on the machine",
    ),
    endpoint: Optional[str] = typer.Option(
        None, "--endpoint", "-e",
        help="Publish behind this endpoint; defaults to a new one named after the deployment",
    ),
    served_name: Optional[List[str]] = typer.Option(
        None, "--served-name",
        help="What callers ask for; repeat for several. Needed for --source path, "
             "which would otherwise publish the directory as the model name",
    ),
    arg: Optional[List[str]] = typer.Option(
        None, "--arg",
        help="Engine flag passed through as given; repeat for each. "
             "e.g. --arg --kv-cache-dtype --arg fp8",
    ),
    model_version: Optional[str] = typer.Option(
        None, "--model-version", help="Version, for registry models"
    ),
    image: Optional[str] = typer.Option(
        None, "--image", help="Container image (defaults per server type)"
    ),
    gpu: float = typer.Option(1, "--gpu", "-g", help="GPUs per replica"),
    min_replicas: int = typer.Option(1, "--min-replicas", help="Minimum replicas"),
    max_replicas: int = typer.Option(1, "--max-replicas", help="Maximum replicas"),
    max_model_len: Optional[int] = typer.Option(
        None, "--max-model-len", help="Maximum context length (vLLM)"
    ),
    tensor_parallel: Optional[int] = typer.Option(
        None, "--tensor-parallel", help="Tensor parallel size (vLLM)"
    ),
    quantization: Optional[str] = typer.Option(
        None,
        "--quantization",
        "-q",
        help="Leave unset for a checkpoint that declares its own (compressed-tensors, "
        "NVFP4, FP8, AWQ, GPTQ in its config.json): the engine reads it from there. "
        "Only to force one: awq, gptq, fp8, bitsandbytes, gguf, compressed-tensors, modelopt",
    ),
    gpu_memory_util: Optional[float] = typer.Option(
        None, "--gpu-memory-util", help="GPU memory utilization, 0.0-1.0 (vLLM)"
    ),
    enforce_eager: bool = typer.Option(False, "--enforce-eager", help="Disable CUDA graphs (vLLM)"),
    enable_tracing: bool = typer.Option(False, "--tracing", help="Enable MLflow tracing"),
    feature: Optional[List[str]] = typer.Option(
        None, "--feature",
        help="Turn on one of the model's published opt-in features, e.g. spec_decoding "
             "(MTP speculative decoding); repeat for several. --dry-run lists what it offers",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Show what it would run with -- the recipe found for the model, the engine "
             "arguments, the features it offers -- and deploy nothing",
    ),
    wait: bool = typer.Option(False, "--wait", help="Wait until the server is running"),
    timeout: int = typer.Option(600, "--timeout", help="Seconds to wait when --wait is set"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Deploy an inference server.

    This allocates GPUs and counts against the workspace quota.

    The engine's arguments come from the model's published recipe -- found by
    its id, or for a path by what its own README and config.json say it is --
    so parsers, KV-cache type and the rest need not be typed. Look first with
    --dry-run.

    Example:
        corerun inference deploy --name qwen --source path \\
            --model /data/models/Qwen3.8-27B-NVFP4 --served-name qwen \\
            --compute datacore-host --endpoint chat --dry-run
    """
    _init_client()

    import corerun.inference as inference

    try:
        server = inference.deploy(
            name=name,
            model_id=model,
            compute_name=compute,
            server_type=server_type,
            model_source=model_source,
            model_version=model_version,
            image=image,
            gpu=gpu,
            min_replicas=min_replicas,
            max_replicas=max_replicas,
            max_model_len=max_model_len,
            tensor_parallel=tensor_parallel,
            quantization=quantization,
            gpu_memory_util=gpu_memory_util,
            enforce_eager=enforce_eager,
            enable_tracing=enable_tracing,
            endpoint=endpoint,
            extra_args=list(arg) if arg else None,
            served_names=list(served_name) if served_name else None,
            features=list(feature) if feature else None,
            dry_run=dry_run,
            wait=wait,
            timeout=timeout,
            workspace=workspace,
        )
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if dry_run:
        if output.json_mode():
            output.emit(server)
            return
        _print_plan(server, f"{name} would run with")
        refusals = server.get("would_refuse") or []
        for reason in refusals:
            console.print(f"  [red]Would be refused:[/red] {reason}")
        console.print(
            "\n[dim]Nothing was deployed. "
            "Run the same command without --dry-run to deploy.[/dim]"
        )
        return

    console.print(f"[green]Deployed[/green] {server.name} [dim]({server.id})[/]")
    console.print(f"  Status: [{_status_style(server.status)}]{server.status}[/]")
    if server.endpoint_name:
        console.print(f"  Endpoint: {server.endpoint_name}")
    if server.openai_base_url:
        console.print(f"  OpenAI base URL: {server.openai_base_url}")
    try:
        _print_plan(inference.plan(server.id, workspace=workspace), "Running with")
    except Exception:
        pass
    if not wait:
        console.print(f"\n[dim]Follow with: corerun inference get {server.id}[/]")


@app.command("scale")
def scale_server(
    server_id: str = typer.Argument(..., metavar="SERVER", help="Server name or ID"),
    min_replicas: int = typer.Option(..., "--min-replicas", help="Minimum replicas"),
    max_replicas: int = typer.Option(..., "--max-replicas", help="Maximum replicas"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Scale an inference server's replicas.

    Scaling up allocates more GPUs.

    Example:
        corerun inference scale abc123 --min-replicas 2 --max-replicas 4
    """
    _init_client()

    import corerun.inference as inference

    try:
        server = inference.scale(
            server_id,
            min_replicas=min_replicas,
            max_replicas=max_replicas,
            workspace=workspace,
        )
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    console.print(
        f"[green]Scaled[/green] {server.name} to "
        f"min {server.min_replicas}, max {server.max_replicas}"
    )


@app.command("stop")
def stop_server(
    server_id: str = typer.Argument(..., metavar="SERVER", help="Server name or ID"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Stop an inference server, releasing its GPUs.

    Example:
        corerun inference stop abc123
    """
    _init_client()

    import corerun.inference as inference

    resolved = _resolve(server_id, workspace)

    try:
        server = inference.stop(resolved, workspace=workspace)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    console.print(f"[green]Stopped[/green] {server.name} [dim]({server.status})[/]")


@app.command("start")
def start_server(
    server_id: str = typer.Argument(..., metavar="SERVER", help="Server name or ID"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Start a stopped inference server.

    Example:
        corerun inference start abc123
    """
    _init_client()

    import corerun.inference as inference

    resolved = _resolve(server_id, workspace)

    try:
        server = inference.start(resolved, workspace=workspace)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    console.print(f"[green]Started[/green] {server.name} [dim]({server.status})[/]")


@app.command("restart")
def restart_server(
    server_id: str = typer.Argument(..., metavar="SERVER", help="Server name or ID"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Restart an inference server.

    Example:
        corerun inference restart abc123
    """
    _init_client()

    import corerun.inference as inference

    resolved = _resolve(server_id, workspace)

    try:
        inference.restart(resolved, workspace=workspace)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    console.print("[green]Restart requested[/green]")


@app.command("update")
def update_server(
    reference: str = typer.Argument(..., metavar="SERVER", help="Server name or ID"),
    image: Optional[str] = typer.Option(None, "--image", help="Container image to run instead"),
    extra_arg: Optional[List[str]] = typer.Option(
        None, "--extra-arg",
        help="Engine flag, passed through as given (repeatable). Replaces the whole list; "
             "--add-arg and --remove-arg change it instead",
    ),
    add_arg: Optional[List[str]] = typer.Option(
        None, "--add-arg",
        help="Add to your engine flags, replacing the same flag if set (repeatable): "
             "--add-arg --max-num-seqs --add-arg 16",
    ),
    remove_arg: Optional[List[str]] = typer.Option(
        None, "--remove-arg", help="Remove one of your engine flags, with its value (repeatable)"
    ),
    feature: Optional[List[str]] = typer.Option(
        None,
        "--feature",
        help="The model's opt-in features to run with (repeatable; replaces the list)",
    ),
    no_features: bool = typer.Option(False, "--no-features", help="Turn every opt-in feature off"),
    endpoint: Optional[str] = typer.Option(
        None, "--endpoint", "-e",
        help="Move behind this endpoint. Routing only: takes effect at once and redeploys nothing",
    ),
    gpu: Optional[float] = typer.Option(None, "--gpu", help="GPUs to allocate"),
    max_model_len: Optional[int] = typer.Option(
        None, "--max-model-len", help="Maximum sequence length"
    ),
    enforce_eager: Optional[bool] = typer.Option(
        None,
        "--enforce-eager/--no-enforce-eager",
        help="Disable CUDA graphs, for GPUs that need it",
    ),
    gpu_memory_util: Optional[float] = typer.Option(
        None, "--gpu-memory-util", help="Fraction of GPU memory the engine may take, above 0 and at most 1"
    ),
    served_name: Optional[List[str]] = typer.Option(
        None, "--served-name", help="A name callers ask for this model by (repeatable; replaces the list)"
    ),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Change how a server runs, keeping its ID, endpoint and key.

    The engine reads its flags at startup, so the workload is replaced -- but
    the server is not. Callers keep the endpoint they were given.

    Example:
        # Turn on MTP speculative decoding, if the model offers it
        corerun inference update qwen38-nvfp4 --feature spec_decoding

        # Add one engine flag, keeping the rest
        corerun inference update qwen38-nvfp4 --add-arg --max-num-seqs --add-arg 16

        # Move behind the "chat" endpoint without a restart
        corerun inference update qwen38-nvfp4 --endpoint chat

        # Wait for a redeploy to come up
        corerun inference wait qwen38-nvfp4
    """
    _init_client()

    import corerun.inference as inference

    if extra_arg and (add_arg or remove_arg):
        console.print(
            "[red]Error:[/red] --extra-arg replaces the list; "
            "use it or --add-arg/--remove-arg, not both"
        )
        raise typer.Exit(2)

    resolved = _resolve(reference, workspace)

    new_args = list(extra_arg) if extra_arg else None
    if add_arg or remove_arg:
        try:
            current = inference.get(resolved, workspace=workspace).extra_args
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)
        new_args = _edit_args(current, list(add_arg or []), list(remove_arg or []))

    features = [] if no_features else (list(feature) if feature else None)

    changed = {
        "image": image,
        "extra_args": new_args,
        "features": features,
        "endpoint": endpoint,
        "gpu": gpu,
        "max_model_len": max_model_len,
        "enforce_eager": enforce_eager,
        "gpu_memory_util": gpu_memory_util,
        "served_model_names": served_name,
    }
    if all(value is None for value in changed.values()):
        console.print("[red]Error:[/red] nothing to change: name at least one setting")
        raise typer.Exit(1)

    try:
        server = inference.update(resolved, workspace=workspace, **changed)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    only_moved = endpoint is not None and all(
        v is None for k, v in changed.items() if k != "endpoint"
    )
    if only_moved:
        console.print(
            f"[green]{server.name} now answers behind {server.endpoint_name}[/green] "
            "(not redeployed)"
        )
        return

    console.print(f"[green]Updating {server.name}[/green]")
    console.print(f"  Status: [{_status_style(server.status)}]{server.status}[/]")
    # Said back in full: a list that replaces the last one is easy to shorten
    # by accident, and the engine would start without whatever was dropped.
    yours = " ".join(server.extra_args) if server.extra_args else "(none)"
    console.print(f"  Your engine args: {yours}")
    if server.features:
        console.print(f"  Features: {', '.join(server.features)}")
    console.print(f"\n  Use 'corerun inference wait {server.name}' to follow it")


@app.command("delete")
def delete_server(
    server_id: str = typer.Argument(..., metavar="SERVER", help="Server name or ID"),
    force: bool = typer.Option(
        False, "--yes", "-y", "--force", "-f", help="Do not ask for confirmation"
    ),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Delete an inference server.

    Example:
        corerun inference delete abc123
    """
    _init_client()

    import corerun.inference as inference

    resolved = _resolve(server_id, workspace)

    if not force:
        output.confirm(f"Delete inference server {server_id}?")

    try:
        inference.delete(resolved, workspace=workspace)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    console.print(f"[green]Deleted[/green] {server_id}")


@app.command("wait")
def wait_server(
    server_id: str = typer.Argument(..., metavar="SERVER", help="Server name or ID"),
    timeout: int = typer.Option(600, "--timeout", help="Maximum seconds to wait"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Wait for an inference server to reach running.

    Example:
        corerun inference wait abc123
    """
    _init_client()

    import corerun.inference as inference

    resolved = _resolve(server_id, workspace)

    try:
        server = inference.wait_for_running(resolved, timeout=timeout, workspace=workspace)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    console.print(f"[green]Running[/green] {server.name}")
    if server.openai_base_url:
        console.print(f"  OpenAI base URL: {server.openai_base_url}")


@app.command("metrics")
def server_metrics(
    reference: str = typer.Argument(..., metavar="SERVER", help="Server name or ID"),
    range: str = typer.Option("24h", "--range", "-r", help="1h, 6h, 24h, 7d, 30d or 90d"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """What a server has been doing: requests, failures, tokens, latency and decode speed."""
    _init_client()
    import corerun.inference as inference
    from corerun.cli import metrics_view

    answer = inference.metrics(_resolve(reference, workspace), range=range, workspace=workspace)

    def render():
        console.print(metrics_view.table(answer, f"{reference}, last {range}"))
        console.print(f"[dim]{metrics_view.caption(answer)}[/dim]")

    output.emit(answer, render)


@app.command("types")
def list_types(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    List the server engines this platform supports.

    Example:
        corerun inference types
    """
    _init_client()

    import corerun.inference as inference

    try:
        items = inference.list_types(workspace=workspace)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if json_output:
        output.set_json(True)
    if output.json_mode():
        output.emit(items)
        return

    if not items:
        console.print("No server types available")
        return

    table = Table(title="Inference Server Types")
    table.add_column("Type (--type)", style="cyan")
    table.add_column("Description")

    for t in items:
        if isinstance(t, dict):
            table.add_row(str(t.get("server_type") or "-"), str(t.get("description", "")))
        else:
            table.add_row(str(t), "")

    console.print(table)


@app.command("plan")
def plan_server(
    server_id: str = typer.Argument(..., metavar="SERVER", help="Server name or ID"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    What this server's next deployment would run with.

    The engine arguments a deployment would use, resolved the way the
    deployment resolves them and with nothing deployed. The one way to see the
    arguments of a server that is already serving, and the way to see what a
    change to the accelerator table would do before it happens.

    Example:
        corerun inference plan qwen38-nvfp4
    """
    _init_client()

    import corerun.inference as inference

    resolved = _resolve(server_id, workspace)

    try:
        p = inference.plan(resolved, workspace=workspace)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if json_output:
        output.set_json(True)
    if output.json_mode():
        output.emit(p)
        return

    _print_plan(p, f"Next deployment of {server_id}")


def _print_plan(p: dict, heading: str) -> None:
    """What a deployment runs with, and why each part of it is there."""
    console.print(f"\n[bold cyan]{heading}[/]")
    image = p.get("image") or "-"
    if p.get("engine_version"):
        image += f" [dim](engine {p['engine_version']})[/dim]"
    console.print(f"  Image:       {image}")
    if p.get("endpoint_name"):
        console.print(f"  Endpoint:    {p['endpoint_name']}")
        adds = p.get("served_names") or []
        if adds:
            console.print(f"               callers ask for: {', '.join(adds)}")
        # Only a dry run says what is there already. An endpoint serves any
        # number of models side by side, chosen by the name callers send, so
        # joining one adds a model and replaces nothing.
        serves = p.get("endpoint_serves")
        if serves is not None:
            if serves:
                listed = ", ".join(f"{m['model']} ({m['kind']})" for m in serves)
                console.print(
                    f"               already serves {listed}; "
                    "this adds a model beside them, nothing is replaced"
                )
                clash = sorted({m["model"] for m in serves} & set(adds))
                if clash:
                    console.print(
                        f"               [yellow]{', '.join(clash)} is already a name on "
                        "this endpoint; callers asking for it would reach either. "
                        "Pick another --served-name.[/yellow]"
                    )
            else:
                console.print("               a new endpoint: this is its first model")
    if p.get("accelerator"):
        console.print(f"  Accelerator: {p['accelerator']} [dim]({p.get('accelerator_from', '')})[/dim]")
    _print_facts(p.get("model_facts"))
    if p.get("recipe_source"):
        console.print(f"  Recipe:      {p['recipe_source']}")
    else:
        console.print(
            "  Recipe:      [yellow]none found[/yellow] -- only the arguments you give are used"
        )
    for line in (p.get("recipe_note") or "").split("; "):
        if line.strip():
            console.print(f"  [dim]·[/dim] {line}")
    args = p.get("extra_args") or []
    console.print(f"  Engine args: {' '.join(args) if args else '[dim](none)[/dim]'}")
    for line in (p.get("accelerator_note") or "").split("; "):
        if line.strip():
            console.print(f"  [yellow]·[/yellow] {line}")
    on = set(p.get("features") or [])
    offered = p.get("opt_in") or []
    if offered:
        console.print("  Opt-in features [dim](--feature NAME)[/dim]:")
        for o in offered:
            mark = "[green]on[/green] " if o.get("name") in on else "off"
            console.print(f"    {mark} [cyan]{o.get('name')}[/cyan]  {o.get('description', '')}")
            console.print(f"         [dim]{' '.join(o.get('args') or [])}[/dim]")


def _print_facts(facts) -> None:
    """What the model said about itself, in one line."""
    if not facts:
        return
    parts = []
    if facts.get("base_models"):
        parts.append("built from " + ", ".join(facts["base_models"]))
    if facts.get("architecture"):
        parts.append(facts["architecture"])
    quant = facts.get("quantization")
    precision = " ".join(x for x in (facts.get("precision"), f"({quant})" if quant else "") if x)
    if precision:
        parts.append(precision)
    if facts.get("context_length"):
        parts.append(f"{facts['context_length']:,} context")
    if facts.get("mtp_layers"):
        parts.append("MTP head")
    if facts.get("kv_cache_fp8"):
        parts.append("FP8 KV scales")
    if facts.get("sampling"):
        parts.append("sampling defaults in generation_config.json")
    console.print(f"  Model says:  {'; '.join(parts)}")


def _edit_args(current: List[str], add: List[str], remove: List[str]) -> List[str]:
    """Your engine flags with some added or replaced, and some removed.

    A flag owns the values after it up to the next flag, so removing
    --kv-cache-dtype takes its fp8 with it, and adding a flag already set
    replaces it where it was rather than passing it twice.
    """

    def groups(args: List[str]) -> List[List[str]]:
        out: List[List[str]] = []
        for a in args:
            if a.startswith("--") or not out:
                out.append([a])
            else:
                out[-1].append(a)
        return out

    result = groups(current)
    drop = {r if r.startswith("--") else "--" + r for r in remove}
    result = [g for g in result if g[0] not in drop]
    for g in groups(add):
        for i, existing in enumerate(result):
            if existing[0] == g[0]:
                result[i] = g
                break
        else:
            result.append(g)
    return [a for g in result for a in g]


@app.command("recipe")
def model_recipe(
    model: str = typer.Argument(..., metavar="MODEL", help="Model id, e.g. Qwen/Qwen3.8-27B"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    Show how a model's publisher says it should be served.

    Deployments take these engine arguments as defaults, so this is what a
    deployment of this model will run with -- minus any flag you set yourself.
    Worth checking before a deploy: a model launched without its tool-call
    parser cannot call tools, and nothing says so until a request needs them.

    Example:
        corerun inference recipe Qwen/Qwen3.8-27B
    """
    _init_client()

    import corerun.inference as inference

    try:
        r = inference.recipe(model, workspace=workspace)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if json_output:
        output.set_json(True)
    if output.json_mode():
        output.emit(r.model_dump(mode="json"))
        return

    if not r.found:
        console.print(f"[yellow]No serving recipe published for {model}.[/yellow]")
        if r.reason:
            console.print(f"[dim]{r.reason}[/dim]")
        console.print("A deployment proceeds without one, exactly as before.")
        return

    console.print(f"\n[bold cyan]{r.title or r.hf_id}[/]  [dim]{r.hf_id}[/]")
    if r.provider:
        console.print(f"  Provider:  {r.provider}")
    if r.context_length:
        console.print(f"  Context:   {r.context_length} tokens")
    if r.min_vllm_version:
        console.print(f"  Needs vLLM: {r.min_vllm_version} or newer")
    if r.image:
        console.print(f"  Image:     {r.image}")
    if r.hardware:
        console.print(f"  Written for: [magenta]{r.hardware}[/magenta]")
    if r.note:
        console.print(f"  [yellow]Note:[/yellow] {r.note}")

    if r.args:
        console.print("\n  Engine arguments:")
        for arg in r.args:
            console.print(f"    {arg}")
    if r.source:
        console.print(f"\n  [dim]Read from {r.source}[/dim]")
