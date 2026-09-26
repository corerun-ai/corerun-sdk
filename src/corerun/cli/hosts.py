"""
Bare-metal host CLI commands.

A host is a cluster to everything downstream -- it registers under a cluster
name and speaks the same operator protocol -- but it is not onboarded like one.
There is no Kubernetes to apply a manifest to, so it is set up by running two
commands on the machine itself. That difference is the whole of why this is its
own group rather than a flag on `clusters add`.
"""

from typing import Optional

import typer

from corerun.cli import output

console = output.console
app = typer.Typer(help="Bare-metal hosts: machines joined without Kubernetes")


def _init_client():
    """Initialize client, handling errors gracefully"""
    try:
        from corerun import init
        return init()
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        console.print("Run 'corerun login' to authenticate")
        raise typer.Exit(1)


def _install_command_is_usable(command: str) -> bool:
    """
    Whether the install command the API built actually names an address.

    It is assembled from the platform's public API URL, and the check that
    guards it can be satisfied by the download base alone. When only that is
    set, the API still succeeds and hands back `curl -fsSL /operators/install.sh`
    -- a command with no host in it, which reads as perfectly ordinary until
    somebody runs it. Printing it would be worse than saying nothing.
    """
    return "://" in command


@app.command("add")
def add_host(
    name: str = typer.Argument(..., metavar="NAME", help="Host name, e.g. dgx1"),
    architecture: Optional[str] = typer.Option(
        None, "--architecture", "--arch",
        help="amd64 or arm64; usually unneeded -- detected when the host connects",
    ),
    os: str = typer.Option("linux", "--os", help="linux or darwin"),
    accelerator_family: Optional[str] = typer.Option(
        None,
        "--accelerator-family",
        "--gpu",
        help="What this machine's cards are: a family (hopper) or a card "
        "(h100). A host has no pod profiles, so this is the only place it can "
        "declare its hardware -- left unset, it is identified from what its "
        "operator reports. Check with 'corerun accelerators show <value>'.",
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
    Prepare a bare-metal machine so it can join.

    Two commands come back, and they run on the machine being added: the first
    installs the operator (and Incus, if it is missing), the second joins it
    with a one-time enrollment token.

    The token is a credential and is printed in full here, once. Running the
    connect command again is safe -- the platform accepts the superseded token
    and hands the machine its replacement.

    Adding a Kubernetes cluster instead? `corerun cluster add`.

    Example:
        corerun host add dgx1
    """
    _init_client()

    import corerun.clusters as clusters

    try:
        enrollment = clusters.prepare_host(
            name,
            architecture=architecture,
            os=os,
            accelerator_family=accelerator_family,
            tenant_wide=tenant_wide,
            workspace=workspace,
        )
    except Exception as e:
        raise output.fail(str(e))

    def render():
        console.print(
            f"[green]Prepared[/green] {name} "
            f"[dim](host, {enrollment.os}; architecture detected when it connects)[/dim]"
        )
        console.print("\n[bold]1. On the machine, install the operator:[/bold]")
        if _install_command_is_usable(enrollment.install_command):
            # soft_wrap: rich folds at the terminal width otherwise, and a
            # command that arrives folded pastes with a newline in the middle
            # of it. The enrollment token below is long enough to hit this on
            # any ordinary terminal, and the result is a command that fails
            # for a reason nobody can see.
            console.print(f"     {enrollment.install_command}", soft_wrap=True)
        else:
            console.print("     [yellow]The platform built an install command with no "
                          "address in it.[/yellow]")
            console.print(
                "     [dim]This deployment has no public API URL set, so the installer "
                "does not know where to fetch the operator from. Set CORERUN_PUBLIC_API_URL "
                "(or the 'operators_download_url' platform setting) and prepare the host "
                "again. The connect command below is not affected.[/dim]"
            )
        console.print("\n[bold]2. Then join it:[/bold]")
        console.print(f"     {enrollment.connect_command}", soft_wrap=True)
        if enrollment.note:
            console.print(f"\n[dim]{enrollment.note}[/dim]")
        console.print(
            f"[dim]It appears as a cluster once it connects. Watch with "
            f"'corerun clusters get {name}'.[/dim]"
        )

    output.emit(enrollment.model_dump(), render)


@app.command("rm")
def remove_host(
    name: str = typer.Argument(..., metavar="NAME", help="Host name"),
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
    Remove a host from the platform.

    This removes the record, not the software: the operator is still installed
    on the machine and will keep trying to connect until it is stopped there
    (systemctl disable --now corerun-host-operator).

    Example:
        corerun host rm dgx1
    """
    _init_client()

    if not yes and not output.json_mode():
        output.confirm(f"Remove the host '{name}' from the platform?")

    import corerun.clusters as clusters

    try:
        # Nothing Kubernetes-shaped to tear down: there is no Helm release, no
        # namespace and no scheduler. Asking for it would only put failures in
        # the cleanup report for things that were never there.
        result = clusters.remove(
            name,
            clean_resources=False,
            delete_namespace=False,
            clean_kueue=False,
            clean_kai=False,
            tenant_wide=tenant_wide,
            workspace=workspace,
        )
    except Exception as e:
        message = str(e)
        if "tenant_scoped" in message:
            message += "\nThat host belongs to the whole tenant. Add --tenant-wide to remove it."
        raise output.fail(message)

    def render():
        console.print(f"[green]Removed[/green] {name}")
        console.print(
            "[dim]Its operator is still installed. Stop it on the machine with "
            "'systemctl disable --now corerun-host-operator'.[/dim]"
        )

    output.emit(result, render)
