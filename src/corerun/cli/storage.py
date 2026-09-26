"""
Storage accounts.

An account is an endpoint and a credential. Buckets are not named here: each
workspace gets its own, created from the account when the workspace is created.

Scope is what decides which API this talks to, and it is asked for once. An
organization-wide account is the default, because that is what a storage
account usually is -- one object store the whole organization writes to, with a
bucket per workspace inside it. Only an account meant for a single workspace
needs --workspace, and that one needs a workspace selected to act in.
"""

import typer
from rich.table import Table

from corerun.cli import output
from corerun.config import get_config

console = output.console
app = typer.Typer(help="Storage accounts, org-wide or per workspace")

PROVIDERS = ("objectio", "minio", "ceph", "aws", "other")
PLANES = ("git", "s3")
TYPES = ("s3", "azure")


def _require_credentials():
    config = get_config()
    if not config.auth_token:
        console.print("[red]Error:[/red] not signed in. Run 'corerun login' first.")
        raise typer.Exit(1)
    return config


def _headers(config, workspace_scoped: bool) -> dict:
    headers = {"Authorization": f"Bearer {config.auth_token}"}
    if workspace_scoped:
        if not config.workspace:
            console.print("[red]Error:[/red] no workspace selected. Run 'corerun ws set' first,")
            console.print("  or drop --workspace to act on the organization's shared storage.")
            raise typer.Exit(1)
        headers["X-Workspace-ID"] = config.workspace
    return headers


def _base(config, workspace_scoped: bool) -> str:
    root = config.api_url.rstrip("/")
    return f"{root}/storage" if workspace_scoped else f"{root}/tenant/shared/storage"


def _call(method: str, url: str, headers: dict, verify: bool, json_body=None):
    """One request, decoded; a failure is a sentence the entry point prints."""
    from corerun.cli import http

    return http.call(
        method,
        url,
        token=headers.get("Authorization", "").removeprefix("Bearer "),
        verify=verify,
        json=json_body,
        headers={k: v for k, v in headers.items() if k != "Authorization"},
        config=get_config(),
    )


def _render(targets, scope: str) -> None:
    if not targets:
        console.print(
            "No organization-wide storage yet."
            if scope == "organization"
            else "This workspace has no storage of its own."
        )
        return

    table = Table(title=f"Storage ({scope})", show_header=True)
    table.add_column("Name")
    table.add_column("Type")
    table.add_column("Provider")
    table.add_column("Models")
    table.add_column("Endpoint")

    for t in targets:
        config = t.get("config") or {}
        table.add_row(
            t.get("name", ""),
            t.get("storage_type", ""),
            str(config.get("provider") or "—"),
            "git repos" if config.get("storage_plane") == "git" else "objects",
            str(config.get("endpoint") or "—"),
        )
    console.print()
    console.print(table)


@app.command("list")
def list_storage(
    workspace: bool = typer.Option(
        False, "--workspace", "-w", help="This workspace's own storage instead of the organization's"
    ),
):
    """
    List storage accounts.

    Example:
        corerun storage list
        corerun storage list --workspace
    """
    config = _require_credentials()
    body = _call("GET", _base(config, workspace), _headers(config, workspace), config.verify_ssl)
    targets = body.get("storage") or body.get("storage_targets") or []
    scope = "workspace" if workspace else "organization"
    output.emit(targets, lambda: _render(targets, scope))


@app.command("add")
def add_storage(
    name: str = typer.Argument(..., help="What to call this account"),
    kind: str = typer.Option("s3", "--type", help=f"One of: {', '.join(TYPES)}"),
    endpoint: str = typer.Option(
        None,
        "--endpoint",
        help="S3 endpoint, e.g. https://s3.example.com (Azure: only for a sovereign cloud or emulator)",
    ),
    access_key: str = typer.Option(None, "--access-key", help="S3 account access key"),
    secret_key: str = typer.Option(
        None, "--secret-key", help="S3 account secret key (prompted when omitted)"
    ),
    account: str = typer.Option(None, "--account", help="Azure storage account name"),
    account_key: str = typer.Option(
        None, "--account-key", help="Azure storage account key (prompted when omitted)"
    ),
    container: str = typer.Option(
        None, "--container", help="Optional shared container; workspaces get their own regardless"
    ),
    provider: str = typer.Option(
        "objectio", "--provider", help=f"S3 only. One of: {', '.join(PROVIDERS)}"
    ),
    plane: str = typer.Option("git", "--plane", help=f"Where models live: {', '.join(PLANES)}"),
    region: str = typer.Option("us-east-1", "--region", help="S3 region"),
    bucket: str = typer.Option(
        None, "--bucket", help="Optional shared bucket; workspaces get their own regardless"
    ),
    workspace: bool = typer.Option(
        False, "--workspace", "-w", help="Scope to this workspace instead of the organization"
    ),
):
    """
    Add a storage account.

    Organization-wide by default: every workspace created afterwards gets a
    bucket or container of its own from it, with a credential confined to it
    -- a scoped key on ObjectIO, a container SAS on Azure Blob Storage.

    Example:
        corerun storage add orgs3 --endpoint https://s3.example.com \\
            --access-key AKIA... --secret-key ...
        corerun storage add orgblob --type azure --account mycompanyml --account-key ...
        corerun storage add scratch --endpoint ... --access-key ... --workspace
    """
    config = _require_credentials()

    if kind not in TYPES:
        console.print(f"[red]Error:[/red] unknown type {kind!r}. One of: {', '.join(TYPES)}")
        raise typer.Exit(1)
    if plane not in PLANES:
        console.print(f"[red]Error:[/red] unknown plane {plane!r}. One of: {', '.join(PLANES)}")
        raise typer.Exit(1)

    description = "Organization storage account" if not workspace else "Workspace storage account"
    if kind == "azure":
        if not account:
            console.print("[red]Error:[/red] --account is required for an Azure storage account")
            raise typer.Exit(1)
        if not account_key:
            account_key = typer.prompt("Account key", hide_input=True)
        storage_config = {
            "provider": "azure",
            "account": account,
            "account_key": account_key,
            "storage_plane": plane,
            **({"endpoint": endpoint} if endpoint else {}),
            **({"container": container} if container else {}),
        }
    else:
        if provider not in PROVIDERS:
            console.print(
                f"[red]Error:[/red] unknown provider {provider!r}. One of: {', '.join(PROVIDERS)}"
            )
            raise typer.Exit(1)
        if not endpoint or not access_key:
            console.print(
                "[red]Error:[/red] --endpoint and --access-key are required for an S3 account"
            )
            raise typer.Exit(1)
        if not secret_key:
            secret_key = typer.prompt("Secret key", hide_input=True)
        storage_config = {
            "endpoint": endpoint,
            "access_key": access_key,
            "secret_key": secret_key,
            "region": region,
            "provider": provider,
            "storage_plane": plane,
            "driver": "csi",
            "csi_driver": "ru.yandex.s3.csi",
            **({"bucket": bucket} if bucket else {}),
        }

    payload = {
        "name": name,
        "storage_type": kind,
        "description": description,
        "config": storage_config,
        "layout_version": "v2",
        "path_config": {
            "artifacts": "artifacts",
            "models": "models",
            "datasets": "datasets",
            "jobs": "experiments",
            "users": "users",
        },
    }

    created = _call(
        "POST", _base(config, workspace), _headers(config, workspace), config.verify_ssl, payload
    )
    scope = "workspace" if workspace else "organization"
    output.emit(
        created,
        lambda: console.print(
            f"[green]✓[/green] Added [bold]{name}[/bold] ({scope}-wide, "
            + ("Azure Blob Storage" if kind == "azure" else provider)
            + ", models as "
            + ("git repositories" if plane == "git" else "objects")
            + ")"
        ),
    )


@app.command("update")
def update_storage(
    name: str = typer.Argument(..., help="The account to change"),
    endpoint: str = typer.Option(None, "--endpoint", help="New endpoint"),
    access_key: str = typer.Option(None, "--access-key", help="New access key"),
    secret_key: str = typer.Option(None, "--secret-key", help="New secret key"),
    provider: str = typer.Option(None, "--provider", help=f"One of: {', '.join(PROVIDERS)}"),
    plane: str = typer.Option(None, "--plane", help=f"Where models live: {', '.join(PLANES)}"),
    region: str = typer.Option(None, "--region", help="New region"),
    workspace: bool = typer.Option(False, "--workspace", "-w", help="Scope to this workspace"),
):
    """
    Change a storage account.

    Only what you pass is changed; the rest of the account is left alone.

    Example:
        corerun storage update orgs3 --plane git
        corerun storage update orgs3 --secret-key ...
    """
    config = _require_credentials()

    if provider and provider not in PROVIDERS:
        console.print(f"[red]Error:[/red] unknown provider {provider!r}. One of: {', '.join(PROVIDERS)}")
        raise typer.Exit(1)
    if plane and plane not in PLANES:
        console.print(f"[red]Error:[/red] unknown plane {plane!r}. One of: {', '.join(PLANES)}")
        raise typer.Exit(1)

    changes = {
        k: v
        for k, v in {
            "endpoint": endpoint,
            "access_key": access_key,
            "secret_key": secret_key,
            "provider": provider,
            "storage_plane": plane,
            "region": region,
        }.items()
        if v
    }
    if not changes:
        console.print("[yellow]Nothing to change.[/yellow] Pass at least one option; see --help.")
        raise typer.Exit(1)

    url = _base(config, workspace) + f"/{name}"
    _call("PUT", url, _headers(config, workspace), config.verify_ssl, {"config": changes})
    output.emit(
        {"name": name, "changed": sorted(changes)},
        lambda: console.print(f"[green]✓[/green] Updated [bold]{name}[/bold] ({', '.join(sorted(changes))})"),
    )


@app.command("delete")
def delete_storage(
    name: str = typer.Argument(..., help="The account to remove"),
    workspace: bool = typer.Option(False, "--workspace", "-w", help="Scope to this workspace"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask"),
):
    """
    Remove a storage account.

    The account record is removed; the object store and its buckets are not
    touched, so nothing stored in it is lost by doing this.

    Example:
        corerun storage delete orgs3
        corerun storage delete scratch --workspace --yes
    """
    config = _require_credentials()
    scope = "workspace" if workspace else "organization"

    if not yes:
        output.confirm(
            f"Remove the {scope}-wide storage account {name!r}? "
            "Workspaces already pointing at it will lose their storage configuration."
        )

    url = _base(config, workspace) + f"/{name}"
    _call("DELETE", url, _headers(config, workspace), config.verify_ssl)
    output.emit(
        {"deleted": name},
        lambda: console.print(f"[green]✓[/green] Removed [bold]{name}[/bold]"),
    )
