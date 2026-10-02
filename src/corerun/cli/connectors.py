"""
Connector and connection commands.

Connectors are what an agent is given to work with: MCP servers, and
Kubernetes clusters given as a kubeconfig. A workspace keeps its own and sees
the organisation's; `--org` acts on the organisation's, for its
administrators. Connections are each person's own sign-ins to the connectors
that ask everyone to sign in (`auth: oauth`).
"""

from pathlib import Path
from typing import List, Optional

import typer
from rich.table import Table

from corerun.cli import output

console = output.console
app = typer.Typer(help="Connectors: MCP servers and Kubernetes clusters agents are given")
connections_app = typer.Typer(help="Your own sign-ins to connectors")

ORG = typer.Option(False, "--org", help="The organisation's connectors (organisation administrators)")


def _init_client():
    try:
        from corerun import init
        return init()
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        console.print("Run 'corerun login' to authenticate")
        raise typer.Exit(1)


def _find(name: str, org: bool):
    import corerun.connectors as connectors

    try:
        return connectors.find(name, org=org)
    except LookupError as e:
        raise output.fail(str(e))
    except Exception as e:
        raise output.fail(str(e))


def _headers(pairs: Optional[List[str]]) -> Optional[dict]:
    if not pairs:
        return None
    out = {}
    for pair in pairs:
        if "=" not in pair:
            raise output.fail(f"--header takes NAME=VALUE, not {pair!r}")
        k, v = pair.split("=", 1)
        out[k.strip()] = v
    return out


def _check(c) -> str:
    if c.check_error:
        return f"[red]{c.check_error}[/red]"
    if c.kind == "kubernetes":
        return f"Kubernetes {c.server_version}" if c.server_version else "checked"
    return f"{len(c.tools)} tools" if c.tools else "no tools"


@app.command("list")
def list_connectors(org: bool = ORG):
    """
    List connectors: the workspace's own and the organisation's.

    Example:
        corerun connectors list
        corerun connectors list --org
    """
    _init_client()
    import corerun.connectors as connectors

    try:
        found = connectors.list(org=org)
    except Exception as e:
        raise output.fail(str(e))

    def render():
        if not found:
            console.print("No connectors.")
            return
        table = Table(title="Connectors")
        table.add_column("Name")
        table.add_column("Kind")
        table.add_column("Address")
        table.add_column("Sign-in")
        table.add_column("Shared by")
        table.add_column("Check")
        for c in found:
            table.add_row(
                c.name,
                c.kind,
                c.url,
                c.auth + (" (you: signed in)" if c.connected else ""),
                "organisation" if c.scope == "organization" else "workspace",
                _check(c),
            )
        console.print(table)

    output.emit([c.model_dump() for c in found], render)


@app.command("get")
def get_connector(name: str = typer.Argument(..., help="Connector name or id"), org: bool = ORG):
    """
    Show a connector: its address, sign-in and the tools it offers.

    Example:
        corerun connectors get github
    """
    _init_client()
    c = _find(name, org)

    def render():
        console.print(f"[bold]{c.name}[/bold]  [dim]{c.id}[/dim]")
        if c.description:
            console.print(f"  {c.description}")
        console.print(f"  Kind:     {c.kind}")
        console.print(f"  Address:  {c.url}")
        if c.kind == "kubernetes":
            console.print(f"  Context:  {c.context or '(the kubeconfig current)'}")
            console.print(f"  Server:   Kubernetes {c.server_version or '?'}")
        else:
            console.print(f"  Sign-in:  {c.auth}" + (f" ({', '.join(c.header_names or [])})" if c.header_names else ""))
            if c.auth == "oauth":
                console.print(f"  You:      {'signed in' if c.connected else 'not signed in (corerun connections connect ' + c.name + ')'}")
        console.print(f"  Shared:   {'with every workspace' if c.scope == 'organization' else 'this workspace'}")
        if c.check_error:
            console.print(f"  [red]Check failed:[/red] {c.check_error}")
        for t in c.tools:
            console.print(f"    - {t.get('name')}" + (f": {t.get('description')}" if t.get("description") else ""))

    output.emit(c.model_dump(), render)


@app.command("add")
def add_connector(
    name: str = typer.Argument(..., help="A name for it"),
    url: Optional[str] = typer.Option(None, "--url", help="An MCP server's https address"),
    auth: str = typer.Option("none", "--auth", help="none, bearer, headers or oauth (MCP)"),
    token: Optional[str] = typer.Option(None, "--token", help="Bearer token (--auth bearer)"),
    header: Optional[List[str]] = typer.Option(None, "--header", help="NAME=VALUE (--auth headers; repeatable)"),
    client_id: Optional[str] = typer.Option(None, "--client-id", help="OAuth client id, for a server that registers no clients"),
    client_secret: Optional[str] = typer.Option(None, "--client-secret", help="OAuth client secret, with --client-id"),
    scope: Optional[List[str]] = typer.Option(None, "--scope", help="OAuth scope to ask for (repeatable)"),
    kubeconfig: Optional[Path] = typer.Option(None, "--kubeconfig", help="A Kubernetes cluster: its kubeconfig file", exists=True, dir_okay=False),
    context: Optional[str] = typer.Option(None, "--context", help="Which context, when the kubeconfig has several"),
    description: Optional[str] = typer.Option(None, "--description", "-d"),
    org: bool = ORG,
):
    """
    Add a connector. It is checked live first, and refused if the check fails.

    Example:
        corerun connectors add github --url https://api.githubcopilot.com/mcp/ --auth oauth
        corerun connectors add search --url https://mcp.example.com --auth bearer --token $TOKEN
        corerun connectors add prod --kubeconfig ~/.kube/prod.yaml --context prod-admin
    """
    if bool(url) == bool(kubeconfig):
        raise output.fail("give --url for an MCP server, or --kubeconfig for a Kubernetes cluster")
    _init_client()
    import corerun.connectors as connectors

    try:
        if kubeconfig:
            c = connectors.create(
                name, kind="kubernetes", kubeconfig=kubeconfig.read_text(encoding="utf-8"),
                context=context, description=description, org=org,
            )
        else:
            c = connectors.create(
                name, url=url, auth=auth, token=token, headers=_headers(header),
                client_id=client_id, client_secret=client_secret, scopes=scope or None,
                description=description, org=org,
            )
    except Exception as e:
        raise output.fail(_context_hint(e))

    output.emit(c.model_dump(), lambda: console.print(
        f"[green]Added[/green] {c.name} ({c.kind}): {_check(c)}"
        + (f"\nEach person signs in to it: corerun connections connect {c.name}" if c.auth == "oauth" else "")
    ))


@app.command("update")
def update_connector(
    name: str = typer.Argument(..., help="Connector name or id"),
    rename: Optional[str] = typer.Option(None, "--name", help="A new name"),
    url: Optional[str] = typer.Option(None, "--url"),
    auth: Optional[str] = typer.Option(None, "--auth"),
    token: Optional[str] = typer.Option(None, "--token"),
    header: Optional[List[str]] = typer.Option(None, "--header", help="NAME=VALUE (repeatable; replaces them all)"),
    client_id: Optional[str] = typer.Option(None, "--client-id"),
    client_secret: Optional[str] = typer.Option(None, "--client-secret"),
    scope: Optional[List[str]] = typer.Option(None, "--scope"),
    kubeconfig: Optional[Path] = typer.Option(None, "--kubeconfig", exists=True, dir_okay=False),
    context: Optional[str] = typer.Option(None, "--context", help="Needs --kubeconfig again"),
    description: Optional[str] = typer.Option(None, "--description", "-d"),
    org: bool = ORG,
):
    """
    Change a connector; what is not given stays. Checked live again.

    Example:
        corerun connectors update search --token $NEW_TOKEN
        corerun connectors update prod --kubeconfig ~/.kube/prod.yaml --context prod-readonly
    """
    _init_client()
    import corerun.connectors as connectors

    c = _find(name, org)
    try:
        c = connectors.update(
            c.id, name=rename, url=url, auth=auth, token=token, headers=_headers(header),
            client_id=client_id, client_secret=client_secret, scopes=scope or None,
            kubeconfig=kubeconfig.read_text(encoding="utf-8") if kubeconfig else None,
            context=context, description=description, org=org,
        )
    except Exception as e:
        raise output.fail(_context_hint(e))
    output.emit(c.model_dump(), lambda: console.print(f"[green]Updated[/green] {c.name}: {_check(c)}"))


@app.command("test")
def test_connector(name: str = typer.Argument(..., help="Connector name or id"), org: bool = ORG):
    """
    Check a connector now: reachable, signed in, and what it offers.

    Example:
        corerun connectors test prod
    """
    _init_client()
    import corerun.connectors as connectors

    c = _find(name, org)
    try:
        result = connectors.test(c.id, org=org)
    except Exception as e:
        raise output.fail(str(e))

    def render():
        if result.get("ok"):
            console.print(f"[green]OK[/green] {c.name}" + (f": {result.get('message')}" if result.get("message") else ""))
        else:
            console.print(f"[red]Failed[/red] {c.name}: {result.get('message') or 'no reason given'}")

    output.emit(result, render)
    if not result.get("ok"):
        raise typer.Exit(1)


@app.command("remove")
def remove_connector(
    name: str = typer.Argument(..., help="Connector name or id"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask"),
    org: bool = ORG,
):
    """
    Remove a connector. Every agent given it loses it.

    Example:
        corerun connectors remove search --yes
    """
    _init_client()
    import corerun.connectors as connectors

    c = _find(name, org)
    if not yes:
        output.confirm(f"Remove {c.name}? Agents given it lose it.")
    try:
        connectors.delete(c.id, org=org)
    except Exception as e:
        raise output.fail(str(e))
    output.emit({"removed": c.id}, lambda: console.print(f"Removed {c.name}."))


def _context_hint(e: Exception) -> str:
    """A kubeconfig with several contexts is refused with the list; say how to choose."""
    text = str(e)
    if "choose_context" in text or "several contexts" in text.lower():
        return text + "\nName one with --context."
    return text


# ── connections ───────────────────────────────────────────────────────────────


@connections_app.command("list")
def list_connections(agent: Optional[str] = typer.Option(None, "--agent", help="Only this agent's connectors (id)")):
    """
    Your sign-ins to connectors that ask each person to sign in.

    Example:
        corerun connections list
    """
    _init_client()
    import corerun.connectors as connectors

    try:
        found = connectors.connections(agent=agent)
    except Exception as e:
        raise output.fail(str(e))

    def render():
        if not found:
            console.print("No connectors here ask you to sign in.")
            return
        table = Table(title="Your connections")
        table.add_column("Connector")
        table.add_column("Host")
        table.add_column("Signed in")
        for c in found:
            table.add_row(c.get("name", ""), c.get("host", ""), (c.get("connected_at") or "yes") if c.get("connected") else "no")
        console.print(table)

    output.emit(found, render)


@connections_app.command("connect")
def connect(
    name: str = typer.Argument(..., help="Connector name or id"),
    no_browser: bool = typer.Option(False, "--no-browser", help="Print the address instead of opening it"),
):
    """
    Sign in to a connector. Opens the provider's page; the console finishes it.

    Example:
        corerun connections connect github
    """
    _init_client()
    import corerun.connectors as connectors

    c = _find(name, False)
    try:
        address = connectors.connect(c.id)
    except Exception as e:
        raise output.fail(str(e))
    opened = False
    if not no_browser:
        import webbrowser

        try:
            opened = webbrowser.open(address)
        except Exception:
            opened = False
    output.emit({"authorize_url": address, "connector_id": c.id}, lambda: console.print(
        ("Opened the sign-in page for " if opened else "Open this to sign in to ") + f"{c.name}:\n{address}\n"
        "[dim]The provider returns to the console, which finishes it; `corerun connections list` shows it then.[/dim]"
    ))


@connections_app.command("disconnect")
def disconnect(name: str = typer.Argument(..., help="Connector name or id")):
    """
    Forget your sign-in to a connector.

    Example:
        corerun connections disconnect github
    """
    _init_client()
    import corerun.connectors as connectors

    c = _find(name, False)
    try:
        connectors.disconnect(c.id)
    except Exception as e:
        raise output.fail(str(e))
    output.emit({"disconnected": c.id}, lambda: console.print(f"Signed out of {c.name}."))
