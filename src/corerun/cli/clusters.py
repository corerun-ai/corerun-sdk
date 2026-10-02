"""
Cluster CLI commands
"""

import sys
from typing import Optional

import typer
from rich.table import Table

from corerun.cli import output

console = output.console
app = typer.Typer(help="Cluster inspection commands")


def _init_client():
    """Initialize client, handling errors gracefully"""
    try:
        from corerun import init
        return init()
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        console.print("Run 'corerun login' to authenticate")
        raise typer.Exit(1)


def _write_document(text: str) -> None:
    """
    Write a document to stdout exactly as it arrived.

    Not console.print: this is a file, and rich would apply markup to it and
    fold its long lines at the terminal width. And not print() either, which
    appends its own newline -- a manifest already ends with one, so printing it
    leaves a blank line that was never in the document.
    """
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")


def _status_style(status: str) -> str:
    styles = {
        "ready": "green",
        "pending": "yellow",
        "error": "red",
    }
    return styles.get(status, "white")


@app.command("list")
def list_clusters(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    List clusters available to the workspace.

    Example:
        corerun clusters list
    """
    _init_client()

    import corerun.clusters as clusters

    try:
        items = clusters.list(workspace=workspace)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if json_output:
        output.set_json(True)
    if output.json_mode():
        output.emit([c.model_dump(mode="json") for c in items])
        return

    if not items:
        console.print("No clusters found")
        return

    table = Table(title="Clusters")
    table.add_column("Name", style="cyan")
    table.add_column("Backend")
    table.add_column("Scope")
    table.add_column("Status")
    table.add_column("Operator")
    table.add_column("Nodes", justify="right")
    table.add_column("GPUs", justify="right")

    for c in items:
        operator = "[green]connected[/]" if c.operator_connected else "[dim]offline[/]"
        if c.operator_update_available:
            operator += " [yellow]update available[/]"
        nodes = str(c.resources.node_count) if c.resources else "-"
        if c.resources and c.resources.total_gpus:
            gpus = f"{c.resources.allocated_gpus}/{c.resources.total_gpus}"
        else:
            gpus = "-"
        table.add_row(
            c.name,
            c.type or "-",
            c.scope or "-",
            f"[{_status_style(c.status)}]{c.status}[/]",
            operator,
            nodes,
            gpus,
        )

    console.print(table)


@app.command("get")
def get_cluster(
    name: str = typer.Argument(..., help="Cluster name"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    Show a cluster's details and live capacity.

    Example:
        corerun clusters get gb10dgx01
    """
    _init_client()

    import corerun.clusters as clusters

    try:
        c = clusters.get(name, workspace=workspace)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if json_output:
        output.set_json(True)
    if output.json_mode():
        output.emit(c.model_dump(mode="json"))
        return

    console.print(f"\n[bold cyan]{c.name}[/]")
    console.print(f"  Status:       [{_status_style(c.status)}]{c.status}[/]")
    console.print(f"  Type:         {c.type or '-'}")
    console.print(f"  Namespace:    {c.namespace or '-'}")
    console.print(f"  Scope:        {c.scope or '-'}")
    console.print(f"  Architecture: {c.architecture or '-'}")
    console.print(f"  GPU strategy: {c.gpu_strategy or '-'}")
    console.print(f"  Operator:    {'connected' if c.operator_connected else 'offline'}"
                  + (f" ({c.operator_version})" if c.operator_version else ""))
    if c.operator_update_available:
        console.print(
            f"  [yellow]Update available[/yellow]: the platform is {c.operator_latest or 'newer'}. On the host, run:\n"
            "    sudo corerun-host-operator upgrade"
        )

    if not c.resources:
        console.print("\n[dim]No resource data — the operator has not reported in.[/]")
        return

    r = c.resources
    console.print("\n[bold]Capacity[/]")
    console.print(f"  Nodes:  {r.node_count}")
    console.print(f"  CPU:    {r.allocated_cpu}/{r.total_cpu}")
    console.print(f"  Memory: {r.allocated_memory_mb}/{r.total_memory_mb} MB")
    console.print(f"  GPUs:   {r.allocated_gpus}/{r.total_gpus} ({r.available_gpus} free)")

    if r.gpus:
        table = Table(title="GPUs")
        table.add_column("Vendor")
        table.add_column("Model")
        table.add_column("Count", justify="right")
        for g in r.gpus:
            table.add_row(g.vendor or "-", g.model or "-", str(g.count))
        console.print(table)

    if r.storage_classes:
        table = Table(title="Storage Classes")
        table.add_column("Name", style="cyan")
        table.add_column("Provisioner")
        table.add_column("Default")
        for sc in r.storage_classes:
            table.add_row(sc.name, sc.provisioner or "-", "yes" if sc.is_default else "")
        console.print(table)


@app.command("profiles")
def list_profiles(
    name: str = typer.Argument(..., help="Cluster name"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    List the pod profiles a cluster offers.

    Example:
        corerun clusters profiles gb10dgx01
    """
    _init_client()

    import corerun.clusters as clusters

    try:
        items = clusters.profiles(name, workspace=workspace)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if json_output:
        output.set_json(True)
    if output.json_mode():
        output.emit(items)
        return

    if not items:
        console.print("No profiles defined")
        return

    table = Table(title=f"Profiles — {name}")
    table.add_column("Name", style="cyan")
    table.add_column("CPU", justify="right")
    table.add_column("Memory", justify="right")
    table.add_column("GPU", justify="right")

    for p in items:
        if not isinstance(p, dict):
            continue
        table.add_row(
            str(p.get("name", "-")),
            str(p.get("cpu_limit", p.get("cpu", "-"))),
            str(p.get("memory_limit", p.get("memory", "-"))),
            str(p.get("gpu", "-")),
        )

    console.print(table)


@app.command("types")
def list_types(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    List the cluster types a cluster can be added as.

    A type carries defaults -- which GPU strategy a cluster uses, above all.
    Adding a cluster takes its id, which is a UUID and not much use without
    somewhere to look it up, so this is that somewhere.

    Example:
        corerun clusters types
    """
    _init_client()

    import corerun.clusters as clusters

    try:
        items = clusters.types(workspace=workspace)
    except Exception as e:
        raise output.fail(str(e))

    if not items:
        console.print("No cluster types defined")
        return

    table = Table(title="Cluster types")
    table.add_column("Name", style="cyan")
    table.add_column("Display name")
    table.add_column("GPU strategy")
    table.add_column("ID", style="dim")

    for t in items:
        table.add_row(t.name, t.display_name or "-", t.gpu_strategy or "-", t.id)

    console.print(table)


@app.command("add")
def add_cluster(
    name: str = typer.Argument(..., metavar="NAME", help="Cluster name, e.g. gpu1"),
    namespace: str = typer.Option(
        "corerun", "--namespace", "-n", help="Namespace to install the operator into"
    ),
    architecture: str = typer.Option(
        "amd64", "--architecture", "--arch", help="amd64 or arm64"
    ),
    accelerator_family: Optional[str] = typer.Option(
        None,
        "--accelerator-family",
        "--gpu",
        help="What this cluster's cards are: a family (hopper) or a card (h100). "
        "Anything unrecognised is not refused -- the cluster is left to be "
        "identified from what its operator reports. Check with "
        "'corerun accelerators show <value>'.",
    ),
    cluster_type: Optional[str] = typer.Option(
        None, "--cluster-type", help="A type's ID, from 'corerun clusters types'"
    ),
    tenant_wide: bool = typer.Option(
        False,
        "--tenant-wide",
        help="Give it to the whole tenant rather than this workspace. Requires "
        "tenant administrator standing.",
    ),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Prepare a Kubernetes cluster so its operator can join.

    Nothing connects here. This writes the cluster's manifest to stdout and what
    to do with it to stderr, so `corerun cluster add gpu1 > cluster.yaml` leaves
    a file you can apply. Apply it on the target and the cluster's operator phones
    home on its own -- `corerun clusters list` shows when it has.

    Adding a host instead? `corerun host add` -- a bare machine has no
    Kubernetes to apply a manifest to, so it is onboarded differently.

    Example:
        corerun cluster add gpu1 --accelerator-family h100 > cluster.yaml
        kubectl apply -f cluster.yaml
    """
    _init_client()

    import corerun.clusters as clusters

    try:
        manifest = clusters.prepare(
            name,
            namespace=namespace,
            architecture=architecture,
            accelerator_family=accelerator_family,
            cluster_type_id=cluster_type,
            tenant_wide=tenant_wide,
            workspace=workspace,
        )
    except Exception as e:
        raise output.fail(str(e))

    def render():
        _write_document(manifest)
        # Everything a person reads goes to stderr, so it cannot end up in the
        # file somebody redirected.
        output.errors.print(
            f"\n[green]Prepared[/green] {name} [dim](kubernetes)[/dim]\n"
            "Apply it on the cluster:\n"
            "  [bold]kubectl apply -f -[/bold]   [dim](or the file you saved)[/dim]\n"
            "[dim]It registers itself once its operator connects. Watch for it with "
            "'corerun clusters list'.[/dim]"
        )

    output.emit({"name": name, "type": "kubernetes", "manifest": manifest}, render)


@app.command("manifest")
def cluster_manifest(
    name: str = typer.Argument(..., metavar="NAME", help="Cluster name"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    The onboarding manifest for a cluster, as it stands now.

    Worth re-fetching after rotating a token: the manifest you applied carries
    the credential from the day it was generated, and it is the only copy.

    A host-backed cluster answers with its installer script rather than a
    manifest -- there is no Kubernetes to apply one to.

    Example:
        corerun clusters manifest gpu1 > cluster.yaml
    """
    _init_client()

    import corerun.clusters as clusters

    try:
        document = clusters.operator_manifest(name, workspace=workspace)
    except Exception as e:
        raise output.fail(str(e))

    def render():
        _write_document(document)
        output.errors.print(
            f"\n[dim]This carries {name}'s current operator token. Its operator is "
            "already using it, so applying this again on a connected cluster "
            "changes nothing.[/dim]"
        )

    output.emit({"name": name, "manifest": document}, render)


@app.command("rm")
def remove_cluster(
    name: str = typer.Argument(..., metavar="NAME", help="Cluster name"),
    delete_namespace: bool = typer.Option(
        False,
        "--delete-namespace",
        help="Delete the namespace too. Destructive, and off by default: the "
        "namespace may hold more than this operator.",
    ),
    clean_kueue: bool = typer.Option(
        False, "--clean-kueue", help="Delete the cluster's Kueue queues and flavours"
    ),
    clean_kai: bool = typer.Option(
        False, "--clean-kai", help="Uninstall the KAI scheduler"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation"),
    tenant_wide: bool = typer.Option(
        False,
        "--tenant-wide",
        help="Remove one the whole tenant owns. A workspace's route refuses "
        "those; use this for anything added with --tenant-wide.",
    ),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Remove a cluster and tear down what its operator installed.

    The Helm release and RBAC are uninstalled by default. Nothing checks for
    running workloads first, so a cluster with jobs on it is removed with its
    jobs.

    Example:
        corerun clusters rm gpu1
    """
    _init_client()

    if not yes and not output.json_mode():
        output.confirm(f"Remove the cluster '{name}' and its operator's resources?")

    import corerun.clusters as clusters

    try:
        result = clusters.remove(
            name,
            delete_namespace=delete_namespace,
            clean_kueue=clean_kueue,
            clean_kai=clean_kai,
            tenant_wide=tenant_wide,
            workspace=workspace,
        )
    except Exception as e:
        message = str(e)
        if "tenant_scoped" in message:
            message += "\nThat cluster belongs to the whole tenant. Add --tenant-wide to remove it."
        raise output.fail(message)

    def render():
        console.print(f"[green]Removed[/green] {name}")
        for entry in result.get("cleanup") or []:
            status = entry.get("status", "")
            style = "green" if status in ("ok", "success") else "yellow"
            console.print(
                f"  [{style}]{entry.get('resource', '?')}[/{style}]"
                f" [dim]{entry.get('message', '')}[/dim]"
            )

    output.emit(result, render)


token_app = typer.Typer(help="The credential a cluster's operator connects with")
app.add_typer(token_app, name="token")


@token_app.command("rotate")
def rotate_token(
    name: str = typer.Argument(..., metavar="NAME", help="Cluster name"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Replace a cluster's operator token.

    The old token stops working the moment this returns, and a connected operator
    has no way to learn the new one -- so a cluster that is connected right now
    will drop off until it is given the replacement. It does not come back on
    its own.

    To recover: re-fetch the manifest (`corerun clusters manifest NAME`) and
    apply it on the cluster, or re-run the host's connect command.

    Example:
        corerun clusters token rotate gpu1
    """
    _init_client()

    if not yes and not output.json_mode():
        output.confirm(
            f"Rotate {name}'s token? A connected operator will drop off until it is "
            "given the new one"
        )

    import corerun.clusters as clusters

    try:
        token = clusters.rotate_token(name, workspace=workspace)
    except Exception as e:
        raise output.fail(str(e))

    def render():
        console.print(f"[green]New token[/green] for {name}")
        console.print(f"  {token}")
        output.errors.print(
            "[dim]The operator holding the previous one cannot reconnect. Re-apply "
            f"the manifest to restore it: corerun clusters manifest {name}[/dim]"
        )

    output.emit({"name": name, "token": token}, render)


@token_app.command("revoke")
def revoke_token(
    name: str = typer.Argument(..., metavar="NAME", help="Cluster name"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Clear a cluster's operator token, so its operator can no longer connect.

    This stops the credential the cluster currently holds. A replacement token
    its operator was already handed is not cleared, and the hub will still honour
    it -- so this is not a way to cut off an operator you have lost track of.

    Example:
        corerun clusters token revoke gpu1
    """
    _init_client()

    if not yes and not output.json_mode():
        output.confirm(
            f"Revoke {name}'s operator token? Its operator will be locked out"
        )

    import corerun.clusters as clusters

    try:
        clusters.revoke_token(name, workspace=workspace)
    except Exception as e:
        raise output.fail(str(e))

    result = {"name": name, "message": "Token revoked"}

    def render():
        console.print(f"[green]Revoked[/green] the operator token for {name}")
        output.errors.print(
            "[dim]Give it a new one with 'corerun clusters token rotate', then "
            "re-apply the manifest.[/dim]"
        )

    output.emit(result, render)


@app.command("scope")
def set_scope(
    name: str = typer.Argument(..., metavar="NAME", help="Cluster or server name"),
    to_workspace: Optional[str] = typer.Option(
        None, "--workspace-only", help="Give it to this workspace alone (id, slug or name)"
    ),
    organization: bool = typer.Option(
        False, "--organization", help="Share it with every workspace in the organisation"
    ),
):
    """
    Move a cluster or server between one workspace and the whole organisation.

    Nothing is reinstalled on the machine. Giving a shared one to a single
    workspace is refused while other workspaces have work running on it.
    Needs an organisation administrator.

    Example:
        corerun clusters scope datacore-host --organization
        corerun clusters scope datacore-host --workspace-only ml-research
    """
    if organization == bool(to_workspace):
        console.print("[red]Error:[/red] give --workspace-only WORKSPACE or --organization")
        raise typer.Exit(2)

    _init_client()
    import corerun.clusters as clusters
    from corerun.cli import workspace as ws_cli

    workspace_id = None
    if to_workspace:
        try:
            match = ws_cli.resolve(clusters.organisation_workspaces(), to_workspace)
        except Exception as e:
            raise output.fail(str(e))
        if not match:
            console.print(f"[red]Error:[/red] no workspace {to_workspace!r} in this organisation")
            raise typer.Exit(1)
        workspace_id = match["id"]

    try:
        result = clusters.set_scope(name, workspace_id=workspace_id, organization=organization)
    except Exception as e:
        raise output.fail(str(e))

    output.emit(result, lambda: console.print(
        f"[green]{name}[/green] is now "
        + ("shared with every workspace" if result.get("scope") == "tenant" else f"{to_workspace}'s alone")
        + " [dim](nothing was reinstalled)[/dim]"
    ))
