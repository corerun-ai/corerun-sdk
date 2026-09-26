"""
What each accelerator generation can do, and what this platform has been told.

The table is the one the platform deploys from: the KV cache dtypes a card
holds, the formats it has kernels for, the toolkit an image must be built for,
and the arguments a family adds to every model it serves. A generation the
compiled-in table has never heard of can be added here, and a card can be
tagged to a family -- by name, by PCI device id, or both.
"""

import json as json_lib
from pathlib import Path
from typing import Optional

import typer
from urllib.parse import quote
from rich.console import Console
from rich.table import Table

from corerun.cli import output

console = output.console
app = typer.Typer(help="Accelerator generations and what they support")


def _family_path(name: str) -> str:
    """
    A family name as one path segment.

    A family is named `nvidia/hopper`, and a slash is a path separator: sent raw
    it is two segments and matches no `:family` route. Percent-encoding is what
    keeps it one, and the API routes on the raw path so the escape survives to
    the handler.
    """
    return quote(name, safe="")


def _init_client():
    try:
        from corerun import init

        return init()
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        console.print("Run 'corerun login' to authenticate")
        raise typer.Exit(1)


@app.callback(invoke_without_command=True)
def accelerators(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Print results as JSON, for piping into other tools"),
):
    """What each accelerator generation can do."""
    if json_output:
        output.set_json(True)

    if ctx.invoked_subcommand is None:
        list_families(json_output=json_output)


def _table(payload: dict, revision: str) -> Table:
    table = Table(title=f"Accelerators (table {revision})")
    table.add_column("Family", style="cyan", no_wrap=True)
    table.add_column("Cards")
    table.add_column("KV cache")
    table.add_column("Toolkit")
    table.add_column("Interconnect")

    for name in sorted(payload):
        caps = payload[name]
        table.add_row(
            name,
            caps.get("display_name", ""),
            ", ".join(caps.get("kv_cache_dtypes") or []) or "[dim]not claimed[/dim]",
            " ".join(x for x in [caps.get("runtime", ""), caps.get("min_runtime", "")] if x) or "-",
            caps.get("interconnect", "-"),
        )
    return table


@app.command("list")
def list_families(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    List the accelerator generations this platform knows.

    Example:
        corerun accelerators list
    """
    _init_client()

    from corerun.config import get_client

    try:
        payload = get_client().get("/inference-servers/accelerators") or {}
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if json_output:
        output.set_json(True)
    if output.json_mode():
        output.emit(payload)
        return

    families = payload.get("families") or {}
    if not families:
        console.print("No accelerators known")
        return

    console.print(_table(families, payload.get("revision", "?")))
    console.print(f"[dim]Read from {payload.get('origin', 'unknown')}[/dim]")


@app.command("show")
def show_accelerator(
    token: str = typer.Argument(..., metavar="FAMILY|CARD", help="e.g. hopper, h100, rtx pro 5000"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    What one accelerator is, and what it can do.

    A family name or the card itself, which is how a person knows what they
    have. Something the platform has never classified is reported as
    unclassified rather than refused: those machines are deployed to.

    Example:
        corerun accelerators show h100
        corerun accelerators show "rtx pro 5000"
    """
    _init_client()

    from corerun.config import get_client

    try:
        payload = get_client().get(f"/inference-servers/accelerators/{_family_path(token)}") or {}
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if json_output:
        output.set_json(True)
    if output.json_mode():
        output.emit(payload)
        return

    if not payload.get("known"):
        console.print(f"[yellow]{token} is not an accelerator this platform has classified.[/yellow]")
        console.print("Deployments proceed with the platform's own defaults, unchanged.")
        return

    caps = payload.get("capabilities") or {}
    console.print(f"\n[bold cyan]{caps.get('display_name', token)}[/]  [dim]{payload.get('family')}[/]")
    if caps.get("compute_capability"):
        console.print(f"  Compute capability: {caps['compute_capability']}")
    if caps.get("runtime"):
        console.print(f"  Toolkit:            {caps['runtime']} {caps.get('min_runtime', '')}".rstrip())
    if caps.get("min_vllm"):
        console.print(f"  Needs vLLM:         {caps['min_vllm']} or newer")
    if caps.get("kv_cache_dtypes"):
        console.print(f"  KV cache:           {', '.join(caps['kv_cache_dtypes'])}")
    if caps.get("quantizations"):
        console.print(f"  Quantizations:      {', '.join(caps['quantizations'])}")
    if caps.get("interconnect"):
        console.print(f"  Interconnect:       {caps['interconnect']}")
    if caps.get("unified_memory"):
        console.print("  Memory:             unified with the CPU")
    if caps.get("args"):
        console.print(f"  Always launched with: {' '.join(caps['args'])}")
    if caps.get("note"):
        console.print(f"  [yellow]·[/yellow] {caps['note']}")
    if caps.get("source"):
        console.print(f"  [dim]{caps['source']}[/dim]")

# ---------------------------------------------------------------------------
# Writing down what the platform should know
# ---------------------------------------------------------------------------
#
# Platform-wide, because a family is hardware knowledge rather than an
# organisation's preference: an A100 is an A100 in every tenant. These routes
# exist only where platform administration is served at all, so an enterprise
# install answers 404 -- which is said out loud below rather than left as a
# mystery.


def _refuse_unreachable(e: Exception, what: str):
    """Explain a 404 on an admin route, which is a build mode and not a bug."""
    console.print(f"[red]Error:[/red] {e}")
    console.print(
        f"[dim]{what} is a platform-administration route. A deployment that does not "
        "serve platform administration -- an enterprise install -- has no such route at "
        "all, so it answers 404 by design.[/dim]"
    )
    raise typer.Exit(1)


def _document_body(from_file: Optional[str], from_url: Optional[str], document: Optional[str]):
    """Read a family document from a file, a URL, or inline.

    Sent as bytes either way and parsed by the API, so there is one parser for
    YAML and JSON rather than one per language. A URL is fetched here rather
    than server-side on purpose: asking an API to fetch an address a caller
    chose is how a platform becomes a way to reach things it cannot see.
    """
    given = [x for x in (from_file, from_url, document) if x]
    if len(given) > 1:
        console.print("[red]Error:[/red] Give the document one way, not several")
        raise typer.Exit(1)
    if not given:
        console.print("[red]Error:[/red] Give a document: --from-file, --from-url or --json")
        raise typer.Exit(1)

    if from_file:
        path = Path(from_file)
        if not path.exists():
            console.print(f"[red]Error:[/red] No such file: {from_file}")
            raise typer.Exit(1)
        return path.read_bytes(), _content_type_for(path.name)

    if from_url:
        import httpx

        try:
            response = httpx.get(from_url, timeout=30.0, follow_redirects=True)
            response.raise_for_status()
        except Exception as e:
            console.print(f"[red]Error:[/red] Could not read {from_url}: {e}")
            raise typer.Exit(1)
        return response.content, response.headers.get("content-type", "application/json")

    return document.encode(), "application/json"


def _content_type_for(name: str) -> str:
    lowered = name.lower()
    if lowered.endswith((".yaml", ".yml")):
        return "application/yaml"
    return "application/json"


@app.command("add")
def add_family(
    family: str = typer.Argument(..., metavar="FAMILY", help="e.g. nvidia/rubin"),
    from_file: Optional[str] = typer.Option(None, "--from-file", help="A YAML or JSON document"),
    from_url: Optional[str] = typer.Option(None, "--from-url", help="Fetch the document from a URL"),
    document: Optional[str] = typer.Option(
        None, "--json", "--document", help="The document inline, as JSON"
    ),
):
    """
    Add or revise an accelerator family.

    The document is the same shape the accelerator catalogue publishes: the
    cache dtypes, the quantizations, the arguments to launch with. It may name
    its own family, and if it names a different one from the argument this
    refuses rather than guessing which was the typo.

    --json is the document here rather than the output, because that is what a
    person typing it means. Ask for JSON output with the parent:
    `corerun --json accelerators add ...`.

    Example:
        corerun accelerators add nvidia/rubin --from-file rubin.yaml
        corerun accelerators add nvidia/rubin --json '{"display_name": "Rubin"}'
    """
    _init_client()

    body, content_type = _document_body(from_file, from_url, document)

    from corerun.config import get_client

    try:
        row = get_client().put(
            f"/admin/accelerators/families/{_family_path(family)}",
            content=body,
            content_type=content_type,
        )
    except Exception as e:
        _refuse_unreachable(e, "Adding a family")

    def render():
        console.print(f"[green]Recorded[/green] {row.get('Name', family)}")
        if row.get("DisplayName"):
            console.print(f"  Name   {row['DisplayName']}")
        console.print(f"  From   {row.get('Origin', 'operator')}")
        console.print(f"  [dim]Tag cards to it with: corerun accelerators tag <CARD> --family {family}[/dim]")

    output.emit(row, render)


@app.command("rm")
def remove_family(
    family: str = typer.Argument(..., metavar="FAMILY", help="The family to remove"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation"),
):
    """
    Remove a family this platform recorded.

    The compiled-in generations are not records and cannot be removed; record
    your own to override one instead.

    Example:
        corerun accelerators rm nvidia/rubin
    """
    _init_client()

    if not yes and not output.json_mode():
        output.confirm(f"Remove the family '{family}' and every card tagged to it?")

    from corerun.config import get_client

    try:
        result = get_client().delete(f"/admin/accelerators/families/{_family_path(family)}")
    except Exception as e:
        _refuse_unreachable(e, f"Removing {family}")
        result = {}

    output.emit(result, lambda: console.print(f"[green]Removed[/green] {family}"))


@app.command("tag")
def tag_card(
    card: str = typer.Argument(..., metavar="CARD", help="The card, as a driver reports it: 'rtx pro 5000'"),
    family: str = typer.Option(..., "--family", "-f", help="The family it belongs to"),
    device_id: Optional[str] = typer.Option(
        None, "--device-id", help="A PCI device id, e.g. 10de:2e12. Preferred when a machine reports one"
    ),
    note: Optional[str] = typer.Option(None, "--note", help="Why this mapping exists, for the next person"),
):
    """
    Tag a card to an accelerator family.

    Either the card name, the device id, or both. A device id is preferred when
    a machine reports one, because it is the card's own claim about itself
    rather than a string somebody has to spell the same way twice.

    Example:
        corerun accelerators tag "rtx pro 5000" --family nvidia/blackwell-rtx
        corerun accelerators tag gb10 --family nvidia/grace-blackwell --device-id 10de:2e12
    """
    _init_client()

    payload = {"card": card, "family": family, "note": note or ""}
    if device_id:
        payload["device_id"] = device_id

    from corerun.config import get_client

    try:
        row = get_client().put("/admin/accelerators/cards", json=payload)
    except Exception as e:
        _refuse_unreachable(e, "Tagging a card")

    def render():
        console.print(f"[green]Tagged[/green] {row.get('Card') or row.get('DeviceID')} -> {row.get('Family')}")
        if row.get("DeviceID"):
            console.print(f"  Device {row['DeviceID']}")
        console.print("[dim]A machine reporting this card now resolves to that family.[/dim]")

    output.emit(row, render)


@app.command("untag")
def untag_card(
    card: str = typer.Argument(..., metavar="CARD", help="The card name pattern"),
    device_id: Optional[str] = typer.Option(None, "--device-id", help="The PCI device id, when the tag carries one"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation"),
):
    """
    Remove a card tag, falling back to whatever the platform already thought.

    Example:
        corerun accelerators untag "rtx pro 5000"
    """
    _init_client()

    if not yes and not output.json_mode():
        output.confirm(f"Remove the tag on '{card}'?")

    params = {"card": card}
    if device_id:
        params["device_id"] = device_id

    from corerun.config import get_client

    try:
        result = get_client().request("DELETE", "/admin/accelerators/cards", params=params)
    except Exception as e:
        _refuse_unreachable(e, "Removing a tag")
        result = {}

    output.emit(result, lambda: console.print(f"[green]Untagged[/green] {card}"))


@app.command("cards")
def list_cards(
    family: Optional[str] = typer.Option(None, "--family", "-f", help="Only cards tagged to this family"),
    search: Optional[str] = typer.Option(None, "--search", "-s", help="Filter by card name or device id"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    What has been tagged to what.

    The platform's mappings plus this workspace's tenant's, the tenant's last so
    its own answer is the one that applies.

    Example:
        corerun accelerators cards
        corerun accelerators cards --family nvidia/grace-blackwell
    """
    _init_client()

    from corerun.config import get_client

    try:
        payload = get_client().get("/inference-servers/accelerators/cards") or {}
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    # An API older than this command has no /accelerators/cards route, and the
    # param route beside it answers instead: `cards` is read as a token, and a
    # token nobody knows comes back as an unclassified accelerator. Reporting
    # that as "nothing is tagged" would be a lie somebody acts on by tagging
    # cards that are already tagged.
    if "cards" not in payload and payload.get("token"):
        console.print("[red]Error:[/red] This deployment does not serve card tags yet")
        console.print("[dim]It answered a token lookup for 'cards'. Update the platform.[/dim]")
        raise typer.Exit(1)

    cards = payload.get("cards") or []
    if family:
        cards = [c for c in cards if c.get("Family") == family]
    if search:
        needle = search.lower()
        cards = [
            c for c in cards
            if needle in (c.get("Card") or "").lower() or needle in (c.get("DeviceID") or "").lower()
        ]

    if json_output:
        output.set_json(True)

    def render():
        if not cards:
            console.print("No cards have been tagged on this platform")
            console.print("[dim]Add one with: corerun accelerators tag <CARD> --family <FAMILY>[/dim]")
            return
        table = Table(title=f"Tagged cards ({len(cards)})")
        table.add_column("Card", style="cyan", no_wrap=True)
        table.add_column("Device id")
        table.add_column("Family")
        table.add_column("From")
        table.add_column("Note")
        for c in cards:
            table.add_row(
                c.get("Card") or "[dim]-[/dim]",
                c.get("DeviceID") or "[dim]-[/dim]",
                c.get("Family", ""),
                c.get("Origin", ""),
                c.get("Note", "") or "",
            )
        console.print(table)

    output.emit(cards, render)
