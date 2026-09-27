"""
Workspace selection.

Which workspace a command acts on is the single piece of context every other
command inherits, so it gets its own place rather than living inside auth.
"""

from typing import Optional

import typer
from rich.table import Table

from corerun.cli import output
from corerun.config import get_config

console = output.console
app = typer.Typer(help="Workspaces: which one you work in, and making or removing them")


def _require_credentials():
    config = get_config()
    if not config.auth_token:
        console.print("[red]Error:[/red] not signed in. Run 'corerun login' first.")
        raise typer.Exit(1)
    return config


def fetch(api_url: str, api_key: str, verify: bool = True):
    """The workspaces this credential can act in.

    Raises the same UnreachableError the rest of the SDK raises, so a command
    that cannot resolve the platform says which address it tried rather than
    passing the resolver's own wording through -- "[Errno 8] nodename nor
    servname provided, or not known" names neither the host nor the setting
    that chose it, and this is the command people run first.
    """
    from corerun.cli import http

    body = http.call(
        "GET", api_url.rstrip("/") + "/workspaces", token=api_key, verify=verify, timeout=30.0
    )
    return body.get("workspaces", [])


def label(workspace: dict) -> str:
    """Name a workspace the way its owner would recognise it."""
    name = workspace.get("display_name") or workspace.get("slug") or workspace.get("id", "")
    tenant = workspace.get("tenant_slug")
    return f"{tenant}/{name}" if tenant else name


def resolve(workspaces, wanted: str):
    """Find a workspace by whatever the user typed.

    An ID, a slug, a display name, or "tenant/name" as it is printed back to
    them -- because the name shown is the name people will type, and demanding
    a UUID for something they have just seen listed by name is a poor trade for
    a few lines of matching.
    """
    wanted = wanted.strip()
    for workspace in workspaces:
        if workspace.get("id") == wanted:
            return workspace
    lowered = wanted.lower()
    for workspace in workspaces:
        candidates = {
            (workspace.get("slug") or "").lower(),
            (workspace.get("display_name") or "").lower(),
            label(workspace).lower(),
        }
        if lowered in candidates:
            return workspace
    return None


def choose(api_url: str, api_key: str, default: str = None, verify: bool = True, tenant_id: str = None):
    """List the caller's workspaces and ask which one to use.

    A single workspace is chosen without asking -- there is no decision to make
    -- and named, so it is still clear where the work will go. ``tenant_id``
    narrows the choice to one organisation's, as after switching to it.
    """
    try:
        workspaces = fetch(api_url, api_key, verify=verify)
        if tenant_id:
            workspaces = [w for w in workspaces if w.get("tenant_id") == tenant_id]
    except Exception as e:
        console.print(f"[yellow]Could not list workspaces:[/yellow] {e}")
        console.print("  Choose one later with: corerun ws set")
        return default

    if not workspaces:
        console.print("[yellow]You are not a member of any workspace yet.[/yellow]")
        return None

    if len(workspaces) == 1:
        only = workspaces[0]
        console.print(f"  Workspace: [bold]{label(only)}[/bold]")
        return only["id"]

    _render(workspaces, current=default)

    # Default to the workspace already in use, so pressing Enter keeps things
    # as they are rather than moving the user somewhere.
    selected = 1
    for n, workspace in enumerate(workspaces, 1):
        if default and workspace["id"] == default:
            selected = n
            break

    # With no terminal to ask on -- a script, an agent, a CI step -- take the
    # default and say which, rather than abort: aborting here threw away a
    # sign-in that had already succeeded, and nothing had been saved.
    import sys

    if not sys.stdin.isatty():
        chosen = workspaces[selected - 1]
        console.print(f"  Using [bold]{label(chosen)}[/bold] (no terminal to ask on; change it with: corerun ws set)")
        return chosen["id"]

    from rich.prompt import IntPrompt

    choice = IntPrompt.ask(
        "[bold]Select a workspace[/bold]",
        choices=[str(n) for n in range(1, len(workspaces) + 1)],
        default=selected,
        show_choices=False,
    )
    chosen = workspaces[choice - 1]
    console.print(f"  Using [bold]{label(chosen)}[/bold]")
    return chosen["id"]


def _render(workspaces, current: str = None) -> None:
    table = Table(title="Workspaces", show_header=True)
    table.add_column("#", style="dim", width=3)
    table.add_column("Workspace")
    table.add_column("Tenant")
    table.add_column("Role")
    table.add_column("")

    for n, workspace in enumerate(workspaces, 1):
        table.add_row(
            str(n),
            workspace.get("display_name") or workspace.get("slug", ""),
            workspace.get("tenant_slug", ""),
            workspace.get("role", ""),
            "[green]current[/green]" if workspace.get("id") == current else "",
        )
    console.print()
    console.print(table)


@app.command("list")
def list_workspaces():
    """
    List the workspaces you belong to.

    Example:
        corerun ws list
    """
    config = _require_credentials()
    try:
        workspaces = fetch(config.api_url, config.auth_token, config.verify_ssl)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    output.emit(
        workspaces,
        lambda: _render(workspaces, current=config.workspace)
        if workspaces
        else console.print("You are not a member of any workspace yet."),
    )


@app.command("set")
def set_workspace(
    name: str = typer.Argument(None, help="Workspace name, slug or ID; omitted, pick from a list"),
):
    """
    Choose the workspace commands act on.

    Example:
        corerun ws set ws1
        corerun ws set imys/ws1
        corerun ws set
    """
    config = _require_credentials()

    if name:
        try:
            workspaces = fetch(config.api_url, config.auth_token, config.verify_ssl)
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)

        chosen = resolve(workspaces, name)
        if chosen is None:
            console.print(f"[red]Error:[/red] no workspace called {name!r}")
            console.print("  Run 'corerun ws list' to see the ones you belong to.")
            raise typer.Exit(1)
        chosen_id = chosen["id"]
        console.print(f"  Using [bold]{label(chosen)}[/bold]")
    else:
        chosen_id = choose(
            config.api_url,
            config.auth_token,
            default=config.workspace,
            verify=config.verify_ssl,
        )
        if not chosen_id:
            raise typer.Exit(1)

    config.workspace = chosen_id
    config.save()
    output.emit(
        {"workspace_id": chosen_id},
        lambda: console.print("[green]✓[/green] Workspace set"),
    )


@app.command("show")
def show_workspace():
    """
    Show which workspace commands currently act on.

    Example:
        corerun ws show
    """
    config = get_config()
    if not config.workspace:
        console.print("No workspace selected. Run 'corerun ws set'.")
        raise typer.Exit(1)

    try:
        workspaces = fetch(config.api_url, config.auth_token, config.verify_ssl)
        current = next((w for w in workspaces if w["id"] == config.workspace), None)
    except Exception:
        current = None

    payload = current or {"id": config.workspace}
    output.emit(
        payload,
        lambda: console.print(
            f"[bold]{label(current)}[/bold]  ({current['id']}, role {current.get('role', '')})"
            if current
            else config.workspace
        ),
    )


# The capability keys the platform recognises, in the order the console lists
# them. Kept here rather than fetched so `--help` can name them offline.
#
# Must match models.AllCapabilities. It did not: `experiments` and `traces`
# were added when GenAI workspaces arrived and never reached this list, so the
# CLI rejected as "unknown capability" two keys the platform not only knows but
# gates its whole GenAI surface on.
CAPABILITIES = (
    "notebooks",
    "training",
    "models",
    "datasets",
    "images",
    "endpoints",
    "experiments",
    "traces",
)


@app.command("create")
def create_workspace(
    name: str = typer.Argument(..., help="Display name, e.g. 'ML Research'"),
    slug: str = typer.Option(None, "--slug", help="URL and CLI name; derived from the name if omitted"),
    capabilities: str = typer.Option(
        None,
        "--capabilities",
        "-c",
        help=f"Comma-separated subset of: {', '.join(CAPABILITIES)}. All of them if omitted.",
    ),
    use: bool = typer.Option(False, "--use", help="Make it the workspace this CLI acts in"),
):
    """
    Create a workspace.

    Storage comes from the organization's shared account when it has one: the
    workspace gets a bucket of its own, and on ObjectIO a credential confined
    to it.

    Example:
        corerun ws create "ML Research"
        corerun ws create "Speech" --slug speech --capabilities notebooks,training
        corerun ws create "Scratch" --use
    """

    config = _require_credentials()

    derived = slug or _slugify(name)
    if not derived:
        console.print("[red]Error:[/red] could not derive a slug from that name; pass --slug")
        raise typer.Exit(1)

    payload = {"slug": derived, "display_name": name}
    if capabilities:
        wanted = [c.strip().lower() for c in capabilities.split(",") if c.strip()]
        unknown = [c for c in wanted if c not in CAPABILITIES]
        if unknown:
            console.print(f"[red]Error:[/red] unknown capability: {', '.join(unknown)}")
            console.print(f"  Known: {', '.join(CAPABILITIES)}")
            raise typer.Exit(1)
        payload["capabilities"] = wanted

    from corerun.cli import http

    created = http.request("POST", "/workspaces", json=payload)
    if use:
        config.workspace = created.get("id")
        config.save()

    output.emit(
        created,
        lambda: console.print(
            f"[green]✓[/green] Created [bold]{created.get('display_name')}[/bold] ({created.get('slug')})"
            + ("  — now the current workspace" if use else "")
        ),
    )


@app.command("delete")
def delete_workspace(
    name: str = typer.Argument(..., help="Workspace name, slug or ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask"),
):
    """
    Delete a workspace and everything in it.

    Example:
        corerun ws delete scratch
        corerun ws delete scratch --yes
    """

    config = _require_credentials()

    try:
        workspaces = fetch(config.api_url, config.auth_token, config.verify_ssl)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    target = resolve(workspaces, name)
    if target is None:
        console.print(f"[red]Error:[/red] no workspace called {name!r}")
        console.print("  Run 'corerun ws list' to see the ones you belong to.")
        raise typer.Exit(1)

    if not yes:
        # Named back before it is destroyed: a slug typed from memory is how
        # the wrong workspace gets deleted.
        output.confirm(
            f"Delete {label(target)} and everything in it? This cannot be undone."
        )

    from corerun.cli import http

    http.request("DELETE", f"/workspaces/{target['id']}")

    # The CLI must not keep pointing at something that no longer exists.
    if config.workspace == target["id"]:
        config.workspace = None
        config.save()

    output.emit(
        {"deleted": target["id"]},
        lambda: console.print(f"[green]✓[/green] Deleted [bold]{label(target)}[/bold]"),
    )


def _slugify(name: str) -> str:
    """Lowercase, dashes only — the same shape the console derives."""
    import re

    return re.sub(r"^-+|-+$", "", re.sub(r"[^a-z0-9]+", "-", name.lower()))


@app.command("edit")
def edit_workspace(
    slug: str = typer.Argument(..., help="The workspace to change"),
    name: Optional[str] = typer.Option(None, "--name", help="New display name"),
    kind: Optional[str] = typer.Option(
        None, "--kind", help="genai, ml or deploy — which console rail and home page this workspace shows; it never changes what the workspace can do"
    ),
    capabilities: Optional[str] = typer.Option(
        None,
        "--capabilities",
        "-c",
        help=f"The complete set, comma-separated, from: {', '.join(CAPABILITIES)}",
    ),
):
    """Change a workspace.

    `--capabilities` is the whole set, not an addition: what you pass is what
    the workspace ends up with, and anything you leave out is withdrawn. That
    is deliberate — it is the only phrasing where what you typed and what you
    get are the same thing.

    Withdrawing one takes effect immediately and stops every request for that
    family, answering 404 because from then on the resource genuinely is not
    there. The data is not deleted; granting it back brings it into view.

    Example:
        corerun ws edit research --name "ML Research"
        corerun ws edit genai-dev --capabilities experiments,traces,datasets,endpoints,training
    """

    if name is None and kind is None and capabilities is None:
        console.print("[yellow]Nothing to change.[/yellow] Pass --name, --kind or --capabilities.")
        raise typer.Exit(1)

    config = _require_credentials()

    try:
        workspaces = fetch(config.api_url, config.auth_token, config.verify_ssl)
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    workspace = resolve(workspaces, slug)
    if workspace is None:
        console.print(f"[red]Error:[/red] no workspace called {slug!r}")
        console.print("  Run 'corerun ws list' to see the ones you belong to.")
        raise typer.Exit(1)

    payload: dict = {}
    if name is not None:
        payload["display_name"] = name
    if kind is not None:
        if kind not in ("ml", "genai", "deploy"):
            console.print("[red]Error:[/red] --kind must be genai, ml or deploy")
            raise typer.Exit(1)
        payload["kind"] = kind
    if capabilities is not None:
        wanted = [c.strip().lower() for c in capabilities.split(",") if c.strip()]
        unknown = [c for c in wanted if c not in CAPABILITIES]
        if unknown:
            console.print(f"[red]Error:[/red] unknown capability: {', '.join(unknown)}")
            console.print(f"  Known: {', '.join(CAPABILITIES)}")
            raise typer.Exit(1)
        payload["capabilities"] = wanted

    from corerun.cli import http

    updated = http.request(
        "PUT", f"/workspaces/{workspace['id']}", json=payload, timeout=config.timeout
    )
    console.print(f"[green]Updated[/green] {updated.get('display_name') or slug}")
    console.print(f"  kind          {updated.get('kind')}")
    console.print(f"  capabilities  {', '.join(updated.get('capabilities') or [])}")
