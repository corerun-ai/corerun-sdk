"""
Token CLI commands.

Issuing a credential for something that is not a person: an OTLP exporter, a
CI job, a script. The scope list is the whole point — a token that can only
send spans is a very different thing to lose than one that can start a job.
"""

from typing import List, Optional

import typer
from rich.table import Table

from corerun.cli import output

console = output.console
app = typer.Typer(help="Tokens for exporters, CI and scripts")


def _init_client():
    try:
        from corerun import init

        return init()
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        console.print("Run 'corerun login' to authenticate")
        raise typer.Exit(1)


def _called(fn):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 — the CLI reports, it does not raise
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


# The scopes worth suggesting, and why. Not the whole vocabulary -- the
# platform validates that -- but the handful somebody issuing a token for a
# machine actually reaches for.
COMMON_SCOPES = [
    ("traces:write", "Send spans to /v1/traces. What an OTLP exporter needs."),
    ("traces:read", "Read traces back. Add only if something reads them."),
    ("evaluations:use", "Start an evaluation. Spends model calls and compute."),
    ("jobs:write", "Create and submit jobs."),
    ("artifacts:read", "Read models and artifacts."),
]


@app.command("create")
def create_token(
    name: str = typer.Argument(..., help="What this token is for, e.g. 'goose tracing'"),
    scopes: Optional[str] = typer.Option(
        None,
        "--scopes",
        "-s",
        help="Comma-separated, e.g. traces:write. Omit to grant everything you can do.",
    ),
    expires_in: int = typer.Option(
        90, "--expires-in", help="Days until it expires. 0 never expires."
    ),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
    otel: bool = typer.Option(
        False, "--otel", help="Print the OTEL_ exports an exporter needs, with the token in them"
    ),
):
    """Issue a token for something that is not a person.

    The secret is shown once and never again — the platform keeps a hash, so a
    token nobody wrote down has to be revoked and replaced.

    Example:
        corerun tokens create "goose tracing" --scopes traces:write --otel
    """
    _init_client()
    from corerun import tokens as api
    from corerun.config import get_config

    wanted: List[str] = []
    if scopes:
        wanted = [s.strip() for s in scopes.split(",") if s.strip()]

    if not wanted:
        # Not refused -- the platform allows it -- but said out loud, because
        # "everything its owner can do" is rarely what a machine needs and is
        # the kind of default nobody revisits.
        console.print(
            "[yellow]No scopes given[/yellow] — this token will be able to do "
            "everything you can. Pass --scopes to narrow it."
        )

    token = _called(
        lambda: api.create(
            name, scopes=wanted or None, expires_in_days=expires_in, workspace=workspace
        )
    )

    console.print(f"[green]Created[/green] {token.name}")
    console.print(f"  id         {token.key_id}")
    console.print(f"  scopes     {' '.join(token.scope_list) or 'everything you can do'}")
    console.print(f"  workspace  {token.workspace_id or '-'}")
    console.print(f"  expires    {token.expires_at or 'never'}")
    console.print()
    console.print("[bold]Shown once. Copy it now.[/bold]")
    console.print(f"  {token.key}")

    if otel:
        config = get_config()
        base = (config.api_url or "").rstrip("/")
        # /v1/traces sits at the root, not under /api/v1: it is OTLP's own path
        # and an exporter should not have to know this platform's prefix.
        origin = base[: -len("/api/v1")] if base.endswith("/api/v1") else base
        console.print()
        console.print("[bold]For an OpenTelemetry exporter:[/bold]")
        console.print(f"  export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT={origin}/v1/traces")
        console.print("  export OTEL_EXPORTER_OTLP_TRACES_PROTOCOL=http/protobuf")
        console.print(f'  export OTEL_EXPORTER_OTLP_TRACES_HEADERS="Authorization=Bearer {token.key}"')
        console.print("  export OTEL_SERVICE_NAME=<the experiment to collect into>")
        console.print()
        console.print(
            "  [dim]service.name picks the experiment; one nobody has used yet is "
            "created on the first export.[/dim]"
        )


@app.command("list")
def list_tokens(workspace: Optional[str] = typer.Option(None, "--workspace", "-w")):
    """Tokens issued here. None of them show their secret."""
    _init_client()
    from corerun import tokens as api

    rows = _called(lambda: api.tokens(workspace=workspace))
    if not rows:
        console.print("[yellow]No tokens issued.[/yellow]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Name")
    table.add_column("Scopes")
    table.add_column("Expires")
    table.add_column("Last used")
    table.add_column("ID", style="dim")
    for row in rows:
        table.add_row(
            row.name,
            " ".join(row.scope_list) or "everything",
            row.expires_at or "never",
            row.last_used_at or "never",
            row.key_id[:12],
        )
    console.print(table)


@app.command("revoke")
def revoke_token(
    key_id: str = typer.Argument(..., help="The id from `tokens list`"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """Withdraw a token.

    Immediate: the next request carrying it is refused. Anything still using it
    stops working, which is the point — but it is worth knowing what that is
    before rather than after.
    """
    _init_client()
    from corerun import tokens as api

    if not yes:
        typer.confirm(f"Revoke {key_id}? Anything using it stops working.", abort=True)

    _called(lambda: api.revoke(key_id, workspace=workspace))
    console.print(f"[green]Revoked[/green] {key_id}")


@app.command("scopes")
def list_scopes():
    """The scopes worth reaching for, and what each one admits."""
    table = Table(show_header=True, header_style="bold")
    table.add_column("Scope")
    table.add_column("What it admits")
    for name, detail in COMMON_SCOPES:
        table.add_row(name, detail)
    console.print(table)
    console.print()
    console.print(
        "[dim]Scopes are <resource>:<action>. manage implies write implies use "
        "implies read, so the narrowest one that works is the one to issue.[/dim]"
    )
