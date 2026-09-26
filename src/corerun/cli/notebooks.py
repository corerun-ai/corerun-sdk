"""
Notebook CLI commands.

The SDK has driven notebooks all along; only the command group was missing, so
the one thing a person is most likely to want from a terminal — start a
notebook, get its URL, stop it when done — was the one thing they had to open a
browser for.
"""

import re
from typing import List, Optional

import typer
from rich.table import Table

from corerun.cli import output, resolve

console = output.console
app = typer.Typer(help="Notebook sessions")


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
        "creating": "yellow",
        "starting": "yellow",
        "running": "green",
        "stopped": "dim",
        "failed": "red",
        "delete_failed": "red",
    }
    return styles.get(status, "white")


def _type_label(notebook_type: str) -> str:
    # What the product calls them, so the CLI and the page agree.
    return {"jupyter": "Notebook", "code-server": "Code Editor"}.get(notebook_type, notebook_type)


def _fail(e: Exception) -> "typer.Exit":
    console.print(f"[red]Error:[/red] {e}")
    return typer.Exit(1)



def _env_vars(env, secret):
    """Turn repeated NAME=VALUE options into what the API expects.

    Raises ValueError on anything that is not NAME=VALUE, rather than sending a
    half-understood variable: a notebook that starts without the variable it was
    told to carry is worse than one that refuses to start.
    """
    parsed = []
    for values, is_secret in ((env or [], False), (secret or [], True)):
        for item in values:
            name, sep, value = item.partition("=")
            if not sep or not name.strip():
                raise ValueError(f"Expected NAME=VALUE, got {item!r}")
            parsed.append(
                {"name": name.strip(), "value": value, "is_secret": is_secret}
            )
    return parsed


def _suggested_name() -> str:
    """A short name derived from whoever is signed in, as the web UI suggests.

    Names only have to be unique per person, so initials and four characters is
    enough, and it saves naming a notebook nobody will refer to by name. An
    explicit name always wins.

    Falls back to a bare suffix when there is nobody to take initials from.
    """
    import random

    initials = ""
    try:
        from corerun.client import get_client

        me = get_client().get("/auth/me") or {}
        source = str(me.get("name") or me.get("email") or "").strip()
    except Exception:
        # Not worth failing a create over a name suggestion.
        source = ""

    if "@" in source:
        source = source.split("@", 1)[0]
    parts = [p for p in re.split(r"[^A-Za-z]+", source) if p]
    if len(parts) >= 2:
        initials = (parts[0][0] + parts[1][0]).lower()
    elif parts:
        initials = parts[0][:2].lower()

    # No l/i/o/0/1: these get read aloud and typed back.
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    suffix = "".join(random.choice(alphabet) for _ in range(4))
    return f"{initials}-{suffix}" if initials else f"nb-{suffix}"


def _resolve(reference: str, workspace: Optional[str]) -> str:
    """A name, a full id, or the shortened id the list prints."""
    import corerun.notebooks as notebooks

    return resolve.by_name_or_id(
        reference, lambda: notebooks.list(workspace=workspace), "notebook"
    )


@app.command("list")
def list_notebooks(
    status: Optional[str] = typer.Option(None, "--status", "-s", help="Filter by status"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    List notebooks in the workspace.

    Example:
        corerun notebooks list
        corerun notebooks list --status running
    """
    _init_client()

    import corerun.notebooks as notebooks

    try:
        found = notebooks.list(status=status, workspace=workspace)
    except Exception as e:
        raise _fail(e)

    if json_output:
        output.set_json(True)
    if output.json_mode():
        output.emit([n.model_dump(mode="json") for n in found])
        return

    if not found:
        console.print("No notebooks found")
        return

    table = Table(title="Notebooks")
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Type", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Compute", no_wrap=True)
    table.add_column("GPU", justify="right")
    table.add_column("ID", style="dim", no_wrap=True)

    for n in found:
        table.add_row(
            n.name,
            _type_label(n.notebook_type),
            f"[{_status_style(n.status)}]{n.status}[/]",
            n.compute_name or "-",
            str(int(n.gpu)) if n.gpu else "-",
            n.id,
        )

    console.print(table)


@app.command("get")
def get_notebook(
    notebook_id: str = typer.Argument(..., metavar="NOTEBOOK", help="Notebook name or ID"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    Show a notebook's details.

    Example:
        corerun notebooks get abc123
    """
    _init_client()

    import corerun.notebooks as notebooks

    resolved = _resolve(notebook_id, workspace)

    try:
        n = notebooks.get(resolved, workspace=workspace)
    except Exception as e:
        raise _fail(e)

    if json_output:
        output.set_json(True)
    if output.json_mode():
        output.emit(n.model_dump(mode="json"))
        return

    console.print(f"[bold cyan]{n.name}[/]  [dim]{n.id}[/]")
    console.print(f"  Status:   [{_status_style(n.status)}]{n.status}[/]")
    console.print(f"  Type:     {_type_label(n.notebook_type)}")
    console.print(f"  Compute:  {n.compute_name or '-'}")
    console.print(f"  Image:    {n.image or '-'}")
    console.print(f"  GPUs:     {int(n.gpu) if n.gpu else 'none'}")
    if n.datasets:
        console.print(f"  Datasets: {', '.join(n.datasets)}")
    if n.url:
        console.print(f"  URL:      {_absolute(n.url)}", soft_wrap=True)
    if n.error:
        console.print(f"  [red]Error:[/red]    {n.error}")


@app.command("create")
def create_notebook(
    name: Optional[str] = typer.Argument(None, help="Notebook name; generated if omitted"),
    compute: str = typer.Option(..., "--compute", "-c", help="Compute target to run on"),
    type_: str = typer.Option("jupyter", "--type", "-t", help="jupyter or code-server"),
    gpu: float = typer.Option(0, "--gpu", "-g", help="GPUs to request"),
    image: Optional[str] = typer.Option(None, "--image", "-i", help="Override the default image"),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Resource profile (Kubernetes)"),
    dataset: Optional[List[str]] = typer.Option(None, "--dataset", "-d", help="Dataset to mount; repeatable"),
    visibility: str = typer.Option("personal", "--visibility", help="personal or shared"),
    env: Optional[List[str]] = typer.Option(None, "--env", "-e", help="NAME=VALUE; repeatable"),
    secret: Optional[List[str]] = typer.Option(None, "--secret", help="NAME=VALUE, hidden after creation; repeatable"),
    workspace_env: Optional[List[str]] = typer.Option(None, "--workspace-env", help="Workspace env var ID to include; repeatable"),
    storage_class: Optional[str] = typer.Option(None, "--storage-class", help="Storage class for the volume (Kubernetes)"),
    wait: bool = typer.Option(False, "--wait", help="Wait until it is running"),
    timeout: int = typer.Option(600, "--timeout", help="Seconds to wait for --wait"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    Create a notebook.

    Example:
        corerun notebooks create --compute gb10dgx01-host
        corerun notebooks create my-notebook --compute gb10dgx01-host
        corerun notebooks create trainer --compute prod --gpu 1 --wait
        corerun notebooks create shared-nb --compute prod --visibility shared
        corerun notebooks create dev --compute prod -e DEBUG=1 --secret TOKEN=abc123
    """
    _init_client()

    import corerun.notebooks as notebooks

    try:
        custom_env_vars = _env_vars(env, secret)
    except ValueError as e:
        raise _fail(e)

    if not name:
        name = _suggested_name()

    try:
        n = notebooks.create(
            name=name,
            compute_name=compute,
            notebook_type=type_,
            image=image,
            gpu=gpu,
            profile=profile,
            datasets=list(dataset) if dataset else None,
            workspace_env_vars=list(workspace_env) if workspace_env else None,
            custom_env_vars=custom_env_vars or None,
            visibility=visibility,
            storage_class=storage_class,
            wait=wait,
            timeout=timeout,
            workspace=workspace,
        )
    except Exception as e:
        raise _fail(e)

    if json_output:
        output.set_json(True)
    if output.json_mode():
        output.emit(n.model_dump(mode="json"))
        return

    detail = f"  [dim]{n.id}[/]"
    if n.visibility and n.visibility != "personal":
        detail = f"  [dim]{n.visibility} · {n.id}[/]"
    console.print(f"[green]Created[/green] {n.name}{detail}")
    if n.url:
        console.print(f"  {_absolute(n.url)}", soft_wrap=True)
    elif not wait:
        # Starting is normal and can take minutes on a host pulling an image
        # for the first time. Say so, rather than leave a bare id.
        console.print("  Starting. 'corerun notebooks wait' blocks until it is ready.")


@app.command("wait")
def wait_notebook(
    notebook_id: str = typer.Argument(..., metavar="NOTEBOOK", help="Notebook name or ID"),
    timeout: int = typer.Option(600, "--timeout", help="Seconds to wait"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    Wait for a notebook to be running.

    Example:
        corerun notebooks wait abc123
    """
    _init_client()

    import corerun.notebooks as notebooks

    resolved = _resolve(notebook_id, workspace)

    try:
        n = notebooks.wait_for_running(resolved, timeout=timeout, workspace=workspace)
    except Exception as e:
        raise _fail(e)

    if json_output:
        output.set_json(True)
    if output.json_mode():
        output.emit(n.model_dump(mode="json"))
        return

    console.print(f"[green]Running[/green] {n.name}")
    if n.url:
        console.print(f"  {_absolute(n.url)}", soft_wrap=True)


def _absolute(url: str) -> str:
    """Make a notebook's address openable.

    The platform returns a path — /clusters/<cluster>/notebook/<id>/lab?token=…
    — because it is proxied at the origin rather than under the API prefix. A
    path cannot be opened or clicked, so the origin goes back on: the API URL
    with its /api/v… suffix removed.
    """
    if url.startswith(("http://", "https://")):
        return url

    from corerun.config import get_config

    api_url = (get_config().api_url or "").rstrip("/")
    origin = re.sub(r"/api/v\d+$", "", api_url)
    if not origin:
        return url
    return origin + ("" if url.startswith("/") else "/") + url


@app.command("url")
def notebook_url(
    notebook_id: str = typer.Argument(..., metavar="NOTEBOOK", help="Notebook name or ID"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Print a notebook's URL, and nothing else, so it can be piped or opened.

    Example:
        open "$(corerun notebooks url abc123)"
    """
    _init_client()

    import corerun.notebooks as notebooks

    resolved = _resolve(notebook_id, workspace)

    try:
        url = notebooks.get_url(resolved, workspace=workspace)
    except Exception as e:
        raise _fail(e)

    if not url:
        console.print("[yellow]Not running yet, so it has no URL.[/yellow]")
        raise typer.Exit(1)
    # print, not console.print: a URL that is piped should arrive unstyled and
    # unwrapped — rich would fold a long token across lines.
    print(_absolute(url))


@app.command("visibility")
def set_visibility(
    notebook_id: str = typer.Argument(..., metavar="NOTEBOOK", help="Notebook name or ID"),
    visibility: str = typer.Argument(..., metavar="personal|shared", help="Who can see it"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Change who can see a notebook.

    Shared gives everyone in the workspace access. Going back to personal takes
    it away again, including from anyone it was shared with by name.

    Example:
        corerun notebooks visibility my-notebook shared
        corerun notebooks visibility my-notebook personal
    """
    _init_client()

    import corerun.notebooks as notebooks

    resolved = _resolve(notebook_id, workspace)

    try:
        n = notebooks.set_visibility(resolved, visibility, workspace=workspace)
    except Exception as e:
        raise _fail(e)

    console.print(f"[green]{n.name}[/green] is now {visibility}")


@app.command("share")
def share_notebook(
    notebook_id: str = typer.Argument(..., metavar="NOTEBOOK", help="Notebook name or ID"),
    user: str = typer.Argument(..., metavar="USER", help="The person to share with"),
    unshare: bool = typer.Option(False, "--unshare", help="Withdraw access instead"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Share a notebook with one person, or stop sharing it.

    Separate from making it shared: this grants one person access while the
    notebook stays personal to everyone else.

    Example:
        corerun notebooks share my-notebook someone@example.com
        corerun notebooks share my-notebook someone@example.com --unshare
    """
    _init_client()

    import corerun.notebooks as notebooks

    resolved = _resolve(notebook_id, workspace)

    try:
        n = notebooks.share(resolved, user, shared=not unshare, workspace=workspace)
    except Exception as e:
        raise _fail(e)

    if unshare:
        console.print(f"[yellow]{user}[/yellow] can no longer see {n.name}")
    else:
        console.print(f"[green]{n.name}[/green] is shared with {user}")


@app.command("stop")
def stop_notebook(
    notebook_id: str = typer.Argument(..., metavar="NOTEBOOK", help="Notebook name or ID"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Stop a notebook, releasing its compute. Its files survive.

    Example:
        corerun notebooks stop abc123
    """
    _init_client()

    import corerun.notebooks as notebooks

    resolved = _resolve(notebook_id, workspace)

    try:
        n = notebooks.stop(resolved, workspace=workspace)
    except Exception as e:
        raise _fail(e)

    console.print(f"[yellow]Stopping[/yellow] {n.name}")


@app.command("start")
def start_notebook(
    notebook_id: str = typer.Argument(..., metavar="NOTEBOOK", help="Notebook name or ID"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Start a stopped notebook.

    Example:
        corerun notebooks start abc123
    """
    _init_client()

    import corerun.notebooks as notebooks

    resolved = _resolve(notebook_id, workspace)

    try:
        n = notebooks.start(resolved, workspace=workspace)
    except Exception as e:
        raise _fail(e)

    console.print(f"[green]Starting[/green] {n.name}")


@app.command("delete")
def delete_notebook(
    notebook_id: str = typer.Argument(..., metavar="NOTEBOOK", help="Notebook name or ID"),
    force: bool = typer.Option(False, "--yes", "-y", "--force", "-f", help="Do not ask for confirmation"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Delete a notebook and everything in it.

    Example:
        corerun notebooks delete abc123
    """
    _init_client()

    import corerun.notebooks as notebooks

    resolved = _resolve(notebook_id, workspace)

    if not force:
        # Deletion takes the notebook's files with it, which stopping does not.
        output.confirm(f"Delete notebook {notebook_id} and its files?")

    try:
        notebooks.delete(resolved, workspace=workspace)
    except Exception as e:
        raise _fail(e)

    console.print(f"[green]Deleted[/green] {notebook_id}")
