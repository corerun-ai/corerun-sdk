"""
The models available to deploy, and the images that can serve them.

The catalogue is a chooser, not storage: it lists models the platform knows
something about -- how much context they hold, which engine serves them, which
checkpoint they came from -- so a deployment does not start from a blank field.
The workspace registry is where a workspace's own weights live, and `corerun
models` lists that.
"""

from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from corerun.cli import output

console = output.console
app = typer.Typer(help="Models available from the catalogue")


def _init_client():
    try:
        from corerun import init

        return init()
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        console.print("Run 'corerun login' to authenticate")
        raise typer.Exit(1)


def _cards(tp: Optional[int], by_hardware: Optional[dict]) -> str:
    """How many cards a build wants, said the way the publisher stated it.

    Often per card generation rather than once: a model that fits on one B300
    takes two B200s, and collapsing that to a number would be wrong for
    whichever card the deployment lands on.
    """
    if tp:
        return f"{tp} card{'s' if tp > 1 else ''}"
    if not by_hardware:
        return "-"
    grouped: dict = {}
    for hardware, count in by_hardware.items():
        grouped.setdefault(count, []).append(hardware.upper())
    return ", ".join(f"{count} on {'/'.join(sorted(hws))}" for count, hws in sorted(grouped.items()))


@app.callback(invoke_without_command=True)
def catalogue(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Print results as JSON, for piping into other tools"),
    search: Optional[str] = typer.Option(None, "--search", "-s", help="Filter by name or model id"),
    engine: Optional[str] = typer.Option(None, "--engine", "-e", help="Only models this engine serves"),
    limit: Optional[int] = typer.Option(None, "--limit", "-n", help="How many to show (25 by default; 0 for all)"),
):
    """Models available from the catalogue."""
    # Accepted here as well as on the top-level app, because both readings are
    # natural and only one of them used to work: `corerun --json catalogue
    # list` is the documented spelling, and `corerun catalogue --json` is what
    # somebody types. Only ever set, never cleared -- a subcommand's own
    # default must not undo the flag its parent was given.
    if json_output:
        output.set_json(True)

    # With no subcommand, this is the list. The question somebody has when they
    # type the word is what is in it, and a usage message is not an answer --
    # and the filters are declared here too, so the shorthand takes the same
    # arguments as the command it stands for. A shorthand that quietly rejects
    # half of them is worse than no shorthand.
    if ctx.invoked_subcommand is None:
        list_models(search=search, engine=engine, limit=limit, json_output=json_output)


def _context(value: int) -> str:
    """Context length as a person says it."""
    if not value:
        return "-"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    return f"{round(value / 1024)}k"


@app.command("list")
def list_models(
    search: Optional[str] = typer.Option(None, "--search", "-s", help="Filter by name or model id"),
    engine: Optional[str] = typer.Option(None, "--engine", "-e", help="Only models this engine serves"),
    limit: Optional[int] = typer.Option(None, "--limit", "-n", help="How many to show (25 by default; 0 for all)"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    List the models the catalogue knows.

    Example:
        corerun catalogue list
        corerun catalogue list --search qwen --limit 10
    """
    _init_client()

    import corerun.inference as inference

    try:
        models = inference.catalogue(engine=engine)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if search:
        wanted = search.lower()
        models = [
            m for m in models
            if wanted in m.name.lower()
            or wanted in m.slug.lower()
            or wanted in (m.external_id or "").lower()
        ]

    if json_output:
        output.set_json(True)
    if output.json_mode():
        # Complete unless asked otherwise. A pipe is usually something counting
        # or diffing, and a default that quietly drops 350 of 382 records is
        # the kind of truncation nobody notices until the number is wrong.
        output.emit([m.model_dump(mode="json") for m in (models[:limit] if limit else models)])
        return

    if not models:
        console.print("Nothing in the catalogue matches")
        console.print("[dim]The catalogue is filled from the published vLLM recipes; "
                      "`corerun inference deploy --model <hf-id>` works without it.[/dim]")
        return

    if limit is None:
        limit = 25
    shown = models if limit == 0 else models[:limit]
    # The model id is what you deploy with, so it goes on its own line under
    # the name rather than in a column of its own: on a terminal the columns
    # together are wider than the screen, and the one that gets truncated is
    # always the one somebody needed.
    table = Table(title=f"Catalogue ({len(models)} models)", show_lines=False)
    table.add_column("Model", style="cyan")
    table.add_column("Params", justify="right", no_wrap=True)
    table.add_column("Weights", no_wrap=True)
    table.add_column("VRAM", justify="right", no_wrap=True)
    table.add_column("Context", justify="right", no_wrap=True)
    table.add_column("Needs", no_wrap=True)

    for m in shown:
        needs = " ".join(x for x in [m.requires_engine or "", m.min_engine_version or ""] if x) or "-"
        table.add_row(
            f"{m.name}\n[dim]{m.external_id or m.slug}[/dim]",
            m.parameter_count or "-",
            m.quantization or "-",
            f"{m.min_gpu_memory_gb} GB" if m.min_gpu_memory_gb else "-",
            _context(m.context_length),
            needs,
        )

    console.print(table)
    if len(shown) < len(models):
        console.print(f"[dim]{len(models) - len(shown)} more; --limit 0 for all, --search to narrow[/dim]")


@app.command("show")
def show_model(
    reference: str = typer.Argument(..., metavar="SLUG|MODEL_ID", help="Catalogue slug or model id"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    What the catalogue knows about one model.

    Example:
        corerun catalogue show qwen3-8-27b-nvfp4
        corerun catalogue show Qwen/Qwen3.8-27B
    """
    _init_client()

    import corerun.inference as inference

    try:
        model = inference.catalogue_entry(reference)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if model is None:
        console.print(f"[yellow]{reference} is not in the catalogue.[/yellow]")
        console.print("[dim]A model does not have to be: deploy it by its HuggingFace id.[/dim]")
        raise typer.Exit(1)

    if json_output:
        output.set_json(True)
    if output.json_mode():
        output.emit(model.model_dump(mode="json"))
        return

    console.print(f"\n[bold cyan]{model.name}[/]  [dim]{model.slug}[/]")
    if model.description:
        console.print(f"  {model.description}")
    console.print(f"  Model ID:   {model.external_id or '-'}")
    if model.parameter_count:
        console.print(f"  Parameters: {model.parameter_count}")
    if model.quantization:
        console.print(f"  Weights:    {model.quantization}")
    if model.min_gpu_memory_gb:
        fit = f"{model.min_gpu_memory_gb} GB"
        cards = _cards(model.tensor_parallel, (model.tags or {}).get("tp_by_hardware"))
        if cards != "-":
            fit += f" across {cards}"
        console.print(f"  Memory:     needs {fit}")
    if model.context_length:
        console.print(f"  Context:    {model.context_length} tokens")
    if model.requires_engine:
        floor = f" {model.min_engine_version} or newer" if model.min_engine_version else ""
        console.print(f"  Served by:  {model.requires_engine}{floor}")

    if model.features:
        console.print(f"  Can:        {', '.join(model.features)}")

    other = model.variants
    if other:
        table = Table(title="Other builds of this model", show_lines=False)
        table.add_column("Build", style="cyan")
        table.add_column("Model ID")
        table.add_column("VRAM", justify="right")
        table.add_column("Cards", justify="right")
        for name in sorted(other):
            v = other[name] or {}
            table.add_row(
                name,
                v.get("model_id") or "-",
                f"{v['vram_gb']} GB" if v.get("vram_gb") else "-",
                _cards(v.get("tp"), v.get("tp_by_hardware")),
            )
        console.print(table)

    labels = (model.tags or {}).get("labels")
    if labels:
        console.print(f"  [dim]{' · '.join(labels)}[/dim]")
    url = (model.tags or {}).get("source_url")
    if url:
        console.print(f"  [dim]From {url}[/dim]")


@app.command("engines")
def list_engines(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    The serving images the platform knows, and what each was built for.

    Example:
        corerun catalogue engines
    """
    _init_client()

    from corerun.config import get_client

    try:
        payload = get_client().get("/inference-servers/catalog/engines") or {}
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    engines = payload.get("engines") or []
    if json_output:
        output.set_json(True)
    if output.json_mode():
        output.emit(engines)
        return

    if not engines:
        console.print("No serving images recorded")
        return

    table = Table(title="Engine catalogue")
    table.add_column("Image", style="cyan")
    table.add_column("Engine")
    table.add_column("Version")
    table.add_column("CUDA")
    table.add_column("Arch")
    table.add_column("Default", justify="center")

    for e in engines:
        table.add_row(
            e.get("Image", ""),
            e.get("Engine", ""),
            e.get("Version") or "[dim]not recorded[/dim]",
            e.get("CUDAVersion") or "-",
            e.get("Architecture") or "-",
            "yes" if e.get("IsDefault") else "",
        )
    console.print(table)


@app.command("check")
def check_model(
    model_id: str = typer.Argument(..., metavar="MODEL", help="Model id, or catalogue slug"),
    engine: Optional[str] = typer.Option(
        None, "--engine", "-e",
        help="Check this engine's default image (default: the catalogue's engine, else vllm)",
    ),
    image: Optional[str] = typer.Option(
        None, "--image", "-i", help="Check against this image instead",
    ),
    architecture: Optional[str] = typer.Option(
        None, "--architecture", help="The model's architecture, if the catalogue lacks it",
    ),
    quantization: Optional[str] = typer.Option(
        None, "--quantization", "-q", help="The weights' quantization, if the catalogue lacks it",
    ),
    compute: Optional[str] = typer.Option(
        None, "--compute", "-c", help="Check on this cluster: its card, and the image it would get",
    ),
    profile: Optional[str] = typer.Option(None, "--profile", help="The cluster's profile to check with"),
    gpu: Optional[float] = typer.Option(None, "--gpu", help="GPUs the deployment would ask for (default 1)"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    Whether a model can be served, before a deployment finds out.

    Decided by the same code that refuses a deployment, so the two agree.
    With --compute the answer is about that cluster: the image its card would
    be given, whether the card needs a newer engine, and whether a Mac can
    load the weights at all. Exits 1 when the model cannot be served, so a
    script can gate on it.

    Example:
        corerun catalogue check Qwen/Qwen3-8B
        corerun catalogue check Qwen/Qwen3-8B --compute gb10dgx01
        corerun catalogue check Qwen/Qwen3-8B --image vllm/vllm-openai:v0.11.0
    """
    _init_client()

    import corerun.inference as inference

    try:
        verdict = inference.check_compatibility(
            model_id, engine=engine, image=image,
            architecture=architecture, quantization=quantization,
            compute_name=compute, profile=profile, gpu=gpu,
        )
    except Exception as e:
        raise output.fail(str(e))

    if json_output:
        output.set_json(True)

    def render():
        engine_name = verdict.get("engine") or "an unrecorded engine"
        if verdict.get("engine_version"):
            engine_name += f" {verdict['engine_version']}"
        if verdict.get("serves_natively"):
            against = f"{verdict.get('cluster')} [dim](serves natively)[/dim]"
        else:
            against = f"{verdict.get('image') or '(no image)'} [dim]({engine_name})[/dim]"
        if verdict.get("cluster") and not verdict.get("serves_natively"):
            card = (verdict.get("accelerator") or {}).get("name")
            against += f" on {verdict['cluster']}" + (f" [dim]({card})[/dim]" if card else "")
        if verdict.get("compatible"):
            console.print(f"[green]Compatible[/green]  {model_id} on {against}")
            if not verdict.get("engine_known") and not verdict.get("serves_natively"):
                # Nothing to check against is not the same as a pass.
                console.print(
                    "[dim]  The catalogue does not record this image, so little was checked.[/dim]"
                )
        else:
            console.print(f"[red]Not compatible[/red]  {model_id} on {against}")
            if verdict.get("reason"):
                console.print(f"  {verdict['reason']}")
        if verdict.get("cuda_version"):
            console.print(f"  [dim]Built against CUDA {verdict['cuda_version']}[/dim]")
        if verdict.get("compatibility_url") and not verdict.get("compatible"):
            console.print(f"  [dim]What it serves: {verdict['compatibility_url']}[/dim]")

    output.emit(verdict, render)
    if not verdict.get("compatible"):
        raise typer.Exit(1)
