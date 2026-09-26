"""
Model endpoint CLI commands.

An endpoint is the address an application calls. These commands create one, put
models behind it — yours or a provider's — and then send a real request through
it, which is the only way to know the whole path works.
"""

import sys
from typing import Optional

import typer
from rich.table import Table

from corerun.cli import output

console = output.console
app = typer.Typer(help="Model endpoint commands")


def _init_client():
    try:
        from corerun import init

        return init()
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        console.print("Run 'corerun login' to authenticate")
        raise typer.Exit(1)


def _kind_style(kind: str) -> str:
    return "cyan" if kind == "external" else "magenta"


def _state_style(state: str) -> str:
    return {
        "running": "green",
        "available": "green",
        "deploying": "yellow",
        "pending": "yellow",
        "failed": "red",
        "stopped": "dim",
    }.get(state, "white")


@app.command("list")
def list_endpoints(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """List the endpoints in this workspace."""
    _init_client()
    import corerun.endpoints as endpoints

    items = endpoints.list(workspace=workspace)

    def render():
        if not items:
            console.print(
                "No endpoints yet. [cyan]corerun endpoints create <name>[/cyan] makes one."
            )
            return
        table = Table(title="Endpoints")
        table.add_column("Name", style="bold")
        table.add_column("Address")
        table.add_column("Models")
        table.add_column("Behind it", justify="right")
        for endpoint in items:
            table.add_row(
                endpoint.name,
                endpoint.url,
                ", ".join(endpoint.models) or "[dim]nothing serving[/dim]",
                str(endpoint.instances or len(endpoint.published)),
            )
        console.print(table)

    output.emit([e.model_dump() for e in items], render)


@app.command("show")
def show(
    name: str = typer.Argument(..., help="Endpoint name"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
    reveal: bool = typer.Option(False, "--reveal", help="Print the keys in full"),
):
    """Show one endpoint: its address, its keys, and what answers behind it."""
    _init_client()
    import corerun.endpoints as endpoints

    endpoint = endpoints.get(name, workspace=workspace)

    def mask(key: str) -> str:
        if not key:
            return "[dim]not generated[/dim]"
        return key if reveal else f"{key[:12]}{'•' * 12}"

    def render():
        console.print(f"\n[bold]{endpoint.name}[/bold]")
        if endpoint.description:
            console.print(f"[dim]{endpoint.description}[/dim]")
        console.print(f"\n  URL       {endpoint.url}")
        console.print(f"  Key 1     {mask(endpoint.api_key)}")
        console.print(f"  Key 2     {mask(endpoint.api_key_secondary)}")

        if not endpoint.published:
            console.print(
                "\n  [dim]Nothing published yet. Deploy a model into it, or"
                " add an external one.[/dim]\n"
            )
            return

        table = Table(title="Published models")
        table.add_column("Model", style="bold")
        table.add_column("Runs on")
        table.add_column("Accelerator")
        table.add_column("State")
        table.add_column("Upstream")
        table.add_column("Price per 1M")
        for model in endpoint.published:
            table.add_row(
                model.model,
                f"[{_kind_style(model.kind)}]{model.where}[/]",
                # Blank for an external model: what somebody else's API runs
                # on is not something we can honestly report.
                model.accelerator or "",
                f"[{_state_style(model.state)}]{model.state}[/]",
                model.upstream_name if model.upstream_name != model.model else "",
                (price_summary(model.pricing) + _source(model.pricing_source)) if model.pricing else "[dim]not priced[/dim]",
            )
        console.print(table)

    output.emit(endpoint.model_dump(), render)


@app.command("create")
def create(
    name: str = typer.Argument(..., help="Name — it becomes part of the URL"),
    description: str = typer.Option("", "--description", "-d"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """
    Create an empty endpoint.

    It has a URL and a key straight away, so it can be handed out before
    anything is serving behind it.
    """
    _init_client()
    import corerun.endpoints as endpoints

    endpoint = endpoints.create(name, description=description, workspace=workspace)

    def render():
        console.print(f"[green]Created[/green] {endpoint.name}")
        console.print(f"  URL  {endpoint.url}")
        console.print(f"  Key  {endpoint.api_key}", soft_wrap=True, highlight=False)
        console.print(
            "\n[dim]Nothing serves it yet. Deploy a model into it, or:[/dim]\n"
            f"  corerun endpoints add-upstream {endpoint.name} --model gpt-4o \\\n"
            "      --base-url https://api.openai.com/v1 --api-key sk-..."
        )

    output.emit(endpoint.model_dump(), render)


@app.command("delete")
def delete(
    name: str = typer.Argument(..., help="Endpoint name"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation"),
):
    """
    Delete an endpoint and the upstreams published on it.

    Refused while corerun is still running a deployment behind it — removing a
    name should not be how a running model is taken down. Upstreams go with it,
    since those are entries rather than workloads.
    """
    _init_client()
    import corerun.endpoints as endpoints

    if not yes and not output.json_mode():
        output.confirm(f"Delete the endpoint '{name}'?")

    result = endpoints.delete(name, workspace=workspace)
    output.emit(result, lambda: console.print(f"[green]Deleted[/green] {name}"))


@app.command("add-upstream")
def add_upstream(
    name: str = typer.Argument(..., help="Endpoint to publish on"),
    model: str = typer.Option(..., "--model", "-m", help="Name callers will ask for"),
    base_url: str = typer.Option("", "--base-url", help="OpenAI-compatible root URL (known for a --provider from `corerun prices providers`)"),
    api_key: str = typer.Option("", "--api-key", help="The provider's credential"),
    upstream_name: str = typer.Option(
        "", "--as", help="What the provider is asked for, if it differs"
    ),
    provider: str = typer.Option("", "--provider", help="openai, anthropic, deepseek… (`corerun prices providers`): says where its API is and how it is priced"),
    price_input: Optional[float] = typer.Option(None, "--price-input", help="US$ per 1M input tokens"),
    price_output: Optional[float] = typer.Option(None, "--price-output", help="US$ per 1M output tokens"),
    price_cache_read: Optional[float] = typer.Option(None, "--price-cache-read", help="US$ per 1M cached tokens read (default: as input)"),
    price_cache_write: Optional[float] = typer.Option(None, "--price-cache-write", help="US$ per 1M tokens written to the cache (default: as input)"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """
    Publish a model corerun does not run.

    Use --as to publish one name onto another: an endpoint can offer
    "chat-large" and send it to gpt-4o, so what is behind the name can change
    without anybody's code changing.

    With --provider, the model is priced from the provider's list price
    (`corerun prices search`), or your organisation's rate for it; the
    --price-* options set this endpoint's own price instead. Change it later
    with `corerun endpoints price`.

    Example:
        corerun endpoints add-upstream chat --model gpt-4o --provider openai --api-key sk-...
    """
    _init_client()
    import corerun.endpoints as endpoints

    pricing = _pricing(price_input, price_output, price_cache_read, price_cache_write)
    result = endpoints.add_upstream(
        name,
        model=model,
        base_url=base_url,
        api_key=api_key,
        upstream_name=upstream_name,
        provider=provider,
        pricing=pricing,
        workspace=workspace,
    )

    def render():
        console.print(f"[green]Published[/green] {model} on {name}")
        if pricing:
            console.print(f"  priced at {price_summary(pricing)} per 1M tokens")
        if upstream_name:
            console.print(f"  callers ask for {model} → sent upstream as {upstream_name}")
        console.print(f"  {base_url}")

    output.emit(result, render)


@app.command("remove-upstream")
def remove_upstream(
    name: str = typer.Argument(..., help="Endpoint name"),
    model: str = typer.Argument(..., help="The published name to stop serving"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """Stop publishing an upstream."""
    _init_client()
    import corerun.endpoints as endpoints

    result = endpoints.remove_upstream(name, model, workspace=workspace)
    output.emit(result, lambda: console.print(f"[green]Removed[/green] {model} from {name}"))


@app.command("edit-upstream")
def edit_upstream(
    name: str = typer.Argument(..., help="Endpoint name"),
    model: str = typer.Argument(..., help="The upstream's current published name"),
    new_name: Optional[str] = typer.Option(None, "--name", help="A new name for callers to ask for"),
    upstream_name: Optional[str] = typer.Option(None, "--upstream-name", help="What the provider is asked for"),
    base_url: Optional[str] = typer.Option(None, "--base-url", help="The provider's OpenAI-compatible root"),
    api_key: Optional[str] = typer.Option(None, "--api-key", help="A new provider key (the old one is kept otherwise)"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """
    Change an upstream in place: its name, the provider's model, its address or its key.

    Renaming moves callers: anybody asking for the old name gets a 404 from the
    endpoint until they ask for the new one.
    """
    _init_client()
    import corerun.endpoints as endpoints

    if new_name is None and upstream_name is None and base_url is None and api_key is None:
        console.print("[yellow]Nothing to change.[/yellow] Pass --name, --upstream-name, --base-url or --api-key.")
        raise typer.Exit(1)
    result = endpoints.update_upstream(name, model, new_name=new_name, upstream_name=upstream_name,
                                       base_url=base_url, api_key=api_key, workspace=workspace)
    output.emit(result, lambda: console.print(f"[green]Updated[/green] {new_name or model} on {name}"))


def _pricing(input_, output_, cache_read, cache_write) -> Optional[dict]:
    """The price the options say, or None when none were given."""
    given = [v for v in (input_, output_, cache_read, cache_write) if v is not None]
    if not given:
        return None
    if input_ is None or output_ is None:
        console.print("[red]Error:[/red] a price needs both the input and the output price.")
        raise typer.Exit(1)
    if any(v < 0 for v in given):
        console.print("[red]Error:[/red] prices are US dollars per million tokens, none negative.")
        raise typer.Exit(1)
    price = {"input": input_, "output": output_}
    if cache_read is not None:
        price["cache_read"] = cache_read
    if cache_write is not None:
        price["cache_write"] = cache_write
    return price


def _source(source: Optional[dict]) -> str:
    """Where a price came from, after it."""
    level = (source or {}).get("level")
    if level == "list":
        return f" [dim](list, {(source.get('as_of') or '')[:10]})[/dim]"
    if level == "organisation":
        return " [dim](organisation)[/dim]"
    return ""


def price_summary(p: Optional[dict]) -> str:
    """"$3 in · $15 out", with the cache when it is priced apart."""
    if not p:
        return ""

    def money(v: float) -> str:
        return f"${v:,.4f}".rstrip("0").rstrip(".")

    parts = [f"{money(p['input'])} in", f"{money(p['output'])} out"]
    if p.get("cache_read") is not None:
        parts.append(f"{money(p['cache_read'])} cache read")
    if p.get("cache_write") is not None:
        parts.append(f"{money(p['cache_write'])} cache write")
    return " · ".join(parts)


@app.command("price")
def price(
    name: str = typer.Argument(..., help="Endpoint name"),
    model: str = typer.Argument(..., help="The model, as callers ask for it"),
    input_: Optional[float] = typer.Option(None, "--input", help="US$ per 1M input tokens"),
    output_: Optional[float] = typer.Option(None, "--output", help="US$ per 1M output tokens"),
    cache_read: Optional[float] = typer.Option(None, "--cache-read", help="US$ per 1M cached tokens read (default: as input)"),
    cache_write: Optional[float] = typer.Option(None, "--cache-write", help="US$ per 1M tokens written to the cache (default: as input)"),
    clear: bool = typer.Option(False, "--clear", help="Stop pricing it"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """
    Set what a model behind an endpoint costs, per million tokens, as the
    provider lists it -- or an internal rate for a model you run. Calls are
    priced when they are recorded: a price applies from now on and never
    reprices calls already made. Spend shows in `corerun endpoints metrics`
    and `corerun org usage`.

    Example:
        corerun endpoints price chat gpt-4o --input 2.5 --output 10 --cache-read 1.25
        corerun endpoints price chat deepseek-ai/deepseek-flash --clear
    """
    _init_client()
    import corerun.endpoints as endpoints

    if clear:
        result = endpoints.clear_price(name, model, workspace=workspace)
        output.emit(result, lambda: console.print(f"[green]Stopped pricing[/green] {model} on {name}"))
        return
    pricing = _pricing(input_, output_, cache_read, cache_write)
    if pricing is None:
        console.print("[yellow]Nothing to set.[/yellow] Pass --input and --output, or --clear.")
        raise typer.Exit(1)
    result = endpoints.price(name, model, workspace=workspace, **pricing)
    output.emit(result, lambda: console.print(
        f"[green]Priced[/green] {model} on {name} at {price_summary(pricing)} per 1M tokens, from now on"))


@app.command("metrics")
def metrics(
    name: str = typer.Argument(..., help="Endpoint name"),
    range: str = typer.Option("24h", "--range", "-r", help="1h, 6h, 24h, 7d, 30d or 90d"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """
    What every model behind the endpoint has been doing: requests, failures,
    tokens, latency and decode speed, one row per model.
    """
    _init_client()
    import corerun.endpoints as endpoints
    from corerun.cli import metrics_view

    answer = endpoints.metrics(name, range=range, workspace=workspace)

    def render():
        if not answer.get("servers"):
            console.print(f"No model serves {name} yet.")
            return
        console.print(metrics_view.table(answer, f"{name}, last {range}"))
        console.print(f"[dim]{metrics_view.caption(answer)}[/dim]")

    output.emit(answer, render)


@app.command("rotate-key")
def rotate_key(
    name: str = typer.Argument(..., help="Endpoint name"),
    key: str = typer.Option("secondary", "--key", help="primary or secondary"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """
    Replace one of the two keys.

    Both work at once. Regenerate the one nobody uses, move callers onto it,
    then regenerate the other — that way nothing is ever without a key.
    """
    _init_client()
    import corerun.endpoints as endpoints

    result = endpoints.rotate_key(name, key=key, workspace=workspace)

    def render():
        console.print(f"[green]New {key} key[/green] for {name}")
        console.print(f"  {result.get('value', '')}")
        console.print("[dim]Move callers onto it, then rotate the other one.[/dim]")

    output.emit(result, render)


@app.command("models")
def models(
    name: str = typer.Argument(..., help="Endpoint name"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """
    Ask the endpoint what it serves.

    Goes through the endpoint itself rather than the management API, so the
    answer is what a caller would actually get.
    """
    _init_client()
    import corerun.endpoints as endpoints

    try:
        served = endpoints.models(name, workspace=workspace)
    except Exception as e:  # noqa: BLE001 — the reason matters more than the type
        output.fail(f"The endpoint did not answer: {e}")
        return

    def render():
        if not served:
            console.print(f"{name} is reachable but serving nothing right now.")
            return
        for model in served:
            console.print(f"  {model}")

    output.emit({"models": served}, render)


@app.command("call")
def call(
    name: str = typer.Argument(..., help="Endpoint name"),
    prompt: str = typer.Argument(..., help="What to ask"),
    model: str = typer.Option("", "--model", "-m", help="Which model; omit if only one"),
    max_tokens: int = typer.Option(256, "--max-tokens"),
    stream_reply: bool = typer.Option(False, "--stream", help="Print as it arrives"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """
    Send a real request through the endpoint.

    This is the test that matters: the endpoint's own URL and key, the router
    choosing a model, the credential swapped for the upstream one. If this
    works, an application given the same two values works.
    """
    _init_client()
    import corerun.endpoints as endpoints

    try:
        if stream_reply:
            # Written straight to stdout and flushed, not through the console.
            # Rich holds output when stdout is not a terminal, which turns a
            # stream back into a wait-then-paste the moment anybody pipes this
            # into something -- and piping it is how you check that streaming
            # works at all. A token is also not markup: passing one through a
            # renderer that reads [brackets] would eat part of the answer.
            # A reasoning model produces its thinking first, and can spend a
            # whole budget on it without ever reaching an answer. Shown, or
            # the command looks hung for a minute and then prints nothing --
            # which is indistinguishable from a gateway that is buffering.
            #
            # On stderr, because it is progress and not the reply: piping the
            # command still gets the answer alone.
            thinking_shown = False

            def show_thinking(piece: str) -> None:
                nonlocal thinking_shown
                if not thinking_shown:
                    sys.stderr.write("thinking: ")
                    thinking_shown = True
                sys.stderr.write(piece)
                sys.stderr.flush()

            for piece in endpoints.stream(
                name, model, prompt, max_tokens=max_tokens,
                workspace=workspace, on_reasoning=show_thinking,
            ):
                if thinking_shown:
                    sys.stderr.write("\n\n")
                    sys.stderr.flush()
                    thinking_shown = False
                sys.stdout.write(piece)
                sys.stdout.flush()
            if thinking_shown:
                sys.stderr.write("\n")
                sys.stderr.flush()
            sys.stdout.write("\n")
            sys.stdout.flush()
            return

        reply = endpoints.complete(
            name, model, prompt, max_tokens=max_tokens, workspace=workspace
        )
    except Exception as e:  # noqa: BLE001
        output.fail(f"The call failed: {e}")
        return

    output.emit({"reply": reply}, lambda: console.print(reply))


@app.command("benchmark")
def benchmark(
    name: str = typer.Argument(..., help="Endpoint name"),
    model: str = typer.Option(..., "--model", "-m", help="Which of its models, by the name callers use"),
    pattern: str = typer.Option(
        "synthetic", "--pattern", "-p", help="synthetic, multi_turn, agentic or sharegpt"
    ),
    isl: Optional[int] = typer.Option(None, "--isl", help="Input tokens (synthetic, multi_turn)"),
    osl: Optional[int] = typer.Option(None, "--osl", help="Output tokens (synthetic, multi_turn)"),
    turns: Optional[int] = typer.Option(None, "--turns", help="Turns per conversation (multi_turn)"),
    turn_delay_ms: Optional[int] = typer.Option(None, "--turn-delay-ms", help="Delay between turns"),
    dataset: Optional[str] = typer.Option(
        None, "--dataset", help="agentic: claude-code, claude-code-subagents or exgentic"
    ),
    concurrency: str = typer.Option("1,4,16", "--concurrency", "-c", help="Points to measure: 1,4,16"),
    shape: Optional[list[str]] = typer.Option(
        None, "--shape",
        help="An input/output length to sweep, ISL/OSL; repeat for several (synthetic): "
             "--shape 512/256 --shape 4096/256",
    ),
    duration: int = typer.Option(60, "--duration", help="Seconds each point runs"),
    compute: Optional[str] = typer.Option(
        None, "--compute",
        help="Run on this compute target of yours (no benchmark limit) instead of the platform's cluster",
    ),
    wait: bool = typer.Option(False, "--wait", help="Wait for it to finish and print the results"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """
    Load-test an endpoint with aiperf: time to first token, inter-token
    latency, latency and throughput at each concurrency.

    Calls go to the endpoint's public address and count toward the plan's
    model calls. On the platform's cluster the plan limits how many runs a
    month, how long and how many in flight; --compute runs it on your own.

    Example:
        corerun endpoints benchmark chat -m qwen35-27b --isl 4096 --osl 256 -c 1,4,16,64
        corerun endpoints benchmark chat -m qwen35-27b --shape 512/256 --shape 4096/256 -c 8,32,64
        corerun endpoints benchmark chat -m qwen35-27b -p agentic --dataset claude-code --compute datacore-host
    """
    _init_client()
    import time

    import corerun.endpoints as endpoints

    try:
        points = [int(c) for c in concurrency.split(",") if c.strip()]
    except ValueError:
        console.print("[red]Error:[/red] --concurrency is a list of numbers: 1,4,16")
        raise typer.Exit(2)

    try:
        shapes = [tuple(int(x) for x in s.split("/")) for s in shape or []]
        if any(len(s) != 2 for s in shapes):
            raise ValueError
    except ValueError:
        console.print("[red]Error:[/red] --shape is input/output tokens: 512/256")
        raise typer.Exit(2)

    run = endpoints.benchmark(
        name, model, pattern=pattern, isl=isl, osl=osl, turns=turns, turn_delay_ms=turn_delay_ms,
        dataset=dataset, concurrency=points, duration_seconds=duration, compute=compute,
        shapes=shapes or None, workspace=workspace,
    )
    if not wait:
        output.emit(run, lambda: console.print(
            f"[green]Started[/green] {run['id']} on {run.get('compute_name') or 'the platform'}\n"
            f"[dim]Follow: corerun endpoints benchmark-show {name} {run['id']}[/dim]"
        ))
        return

    while run["status"] in ("queued", "running"):
        time.sleep(10)
        run = endpoints.benchmark_run(name, run["id"], workspace=workspace)
        done = len([p for p in run.get("points", []) if not p.get("failed")])
        output.errors.print(f"{run['status']}: {done}/{run.get('points_total', 0)} points")
    output.emit(run, lambda: _print_run(run))
    if run["status"] not in ("succeeded", "partial"):
        raise typer.Exit(1)


@app.command("benchmarks")
def benchmarks(
    name: str = typer.Argument(..., help="Endpoint name"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """An endpoint's benchmark runs, newest first, and what the plan leaves this month."""
    _init_client()
    import corerun.endpoints as endpoints

    answer = endpoints.benchmarks(name, workspace=workspace)

    def render():
        runs = answer.get("benchmarks") or []
        if not runs:
            console.print(f"No benchmarks of {name} yet.")
        else:
            table = Table(show_header=True, header_style="bold")
            for col in ("Id", "Workload", "Model", "Runs on", "Concurrency", "Status", "Started"):
                table.add_column(col)
            for r in runs:
                table.add_row(
                    r["id"][:8], _workload(r), r["model"], r.get("compute_name") or "platform",
                    ",".join(str(c) for c in r["config"]["concurrency"]), r["status"],
                    r["created_at"][:16].replace("T", " "),
                )
            console.print(table)
        a = answer.get("platform_allowance") or {}
        if a.get("per_month"):
            console.print(
                f"[dim]Platform runs this month: {a.get('used_this_month', 0)} of {a['per_month']}; "
                "runs on your own compute are not counted.[/dim]"
            )

    output.emit(answer, render)


@app.command("benchmark-show")
def benchmark_show(
    name: str = typer.Argument(..., help="Endpoint name"),
    run_id: str = typer.Argument(..., help="Run id (or its first characters)"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """One benchmark run: every concurrency point, in a table."""
    _init_client()
    import corerun.endpoints as endpoints

    if len(run_id) < 36:
        matches = [r["id"] for r in endpoints.benchmarks(name, workspace=workspace).get("benchmarks", [])
                   if r["id"].startswith(run_id)]
        if len(matches) != 1:
            console.print(f"[red]Error:[/red] {run_id!r} matches {len(matches)} runs")
            raise typer.Exit(1)
        run_id = matches[0]
    run = endpoints.benchmark_run(name, run_id, workspace=workspace)
    output.emit(run, lambda: _print_run(run))


def _workload(r: dict) -> str:
    c = r["config"]
    shapes = c.get("shapes") or []
    if c["pattern"] == "synthetic" and len(shapes) > 1:
        return "synthetic " + ", ".join(f"{s['isl']}/{s['osl']}" for s in shapes)
    return {
        "synthetic": f"synthetic {c.get('isl')}/{c.get('osl')}",
        "multi_turn": f"multi-turn x{c.get('turns')} {c.get('isl')}/{c.get('osl')}",
        "agentic": f"agentic {c.get('dataset')}",
    }.get(c["pattern"], c["pattern"])


def _print_run(run: dict) -> None:
    def n(v, digits=0):
        return "-" if v is None else f"{v:,.{digits}f}"

    console.print(f"[bold]{_workload(run)}[/bold] on {run['model']} -- {run['status']}")
    if run.get("error") or run.get("note"):
        console.print(f"[dim]{run.get('error') or run.get('note')}[/dim]")
    table = Table(show_header=True, header_style="bold")
    for col in ("ISL/OSL", "Conc.", "Output tok/s", "Per caller stream/e2e", "Req/s", "TTFT p50/p99 ms",
                "ITL p50/p99 ms", "Latency p50/p99 ms", "Errors"):
        table.add_column(col, justify="right")

    def shape(p):
        return f"{p['isl_target']}/{p['osl_target']}" if p.get("isl_target") else "-"

    points = sorted(
        run.get("points", []),
        key=lambda p: (p.get("isl_target") or 0, p.get("osl_target") or 0, p["concurrency"]),
    )
    for p in points:
        if p.get("failed"):
            table.add_row(shape(p), str(p["concurrency"]), "no results", "", "", "", "", "", "")
            continue
        t, i, lat = p.get("ttft_ms") or {}, p.get("itl_ms") or {}, p.get("latency_ms") or {}
        stream = (p.get("output_token_throughput_per_user") or {}).get("p50")
        e2e = (p.get("e2e_output_token_throughput_per_user") or {}).get("p50")
        per_user = f"{n(stream)} / {n(e2e)}"
        table.add_row(
            shape(p), str(p["concurrency"]), n(p.get("output_token_throughput")), per_user,
            n(p.get("request_throughput"), 2),
            f"{n(t.get('p50'))} / {n(t.get('p99'))}", f"{n(i.get('p50'), 1)} / {n(i.get('p99'), 1)}",
            f"{n(lat.get('p50'))} / {n(lat.get('p99'))}", f"{n(p.get('error_rate'), 1)}%",
        )
    console.print(table)
    console.print(
        "[dim]Per caller: streaming is a reply's speed after its first token; end-to-end "
        "includes the wait for it. The system total includes that wait too.[/dim]"
    )


@app.command("capacity")
def capacity(
    name: str = typer.Argument(..., help="Endpoint name"),
    model: str = typer.Option(..., "--model", "-m", help="Which of its models, by the name callers use"),
    shape: Optional[list[str]] = typer.Option(
        None, "--shape", help="ISL/OSL to see how many fit at once; repeat for several"
    ),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """
    What a deployed model can hold, as its engine reported at start: KV cache,
    context, and how many requests of a shape fit at once. Use it to choose a
    benchmark's concurrency: levels above the fit queue for cache.

    Example:
        corerun endpoints capacity chat -m qwen35-27b --shape 512/256 --shape 32768/512
    """
    _init_client()
    import corerun.endpoints as endpoints

    answer = endpoints.capacity(name, model, workspace=workspace)

    def render():
        if answer.get("kind") == "upstream":
            console.print(f"{model} is a provider's model: its capacity is the provider's.")
            return
        cap = answer.get("capacity") or {}
        kv = cap.get("kv_cache_tokens") or 0
        if not kv:
            console.print(f"{model}: the engine's capacity is read from its start-up log once it is running.")
            return
        console.print(
            f"[bold]{model}[/bold]  KV cache {kv:,} tokens ({cap.get('kv_cache_gib')} GiB) · "
            f"context {answer.get('context_window') or cap.get('max_model_len'):,} · "
            f"weights {cap.get('weights_gib')} GiB · {cap.get('max_concurrency')}x at full context"
        )
        for s in shape or ["512/256", "4096/256", "32768/512"]:
            try:
                i, o = (int(x) for x in s.split("/"))
            except ValueError:
                console.print(f"[red]Error:[/red] --shape is ISL/OSL: {s!r}")
                raise typer.Exit(2)
            window = answer.get("context_window") or 0
            if window and i + o > window:
                console.print(f"  {s}: longer than the {window:,}-token context")
            else:
                console.print(f"  {s}: ~{kv // (i + o):,} requests at once before they queue for cache")

    output.emit(answer, render)
