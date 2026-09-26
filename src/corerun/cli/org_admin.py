"""
The rest of administering an organisation: who is in it, how they sign in,
the machines that act for it, its licence, each workspace's limits, and the
git hosts every workspace may clone from.

Attached to `corerun org` beside usage, security and prices. Every command
here needs an organisation administrator; the platform refuses anyone else,
and says so.
"""

from pathlib import Path
from typing import List, Optional

import typer
from rich.table import Table

from corerun.cli import output

console = output.console

members_app = typer.Typer(help="People: seats, workspace roles and invitations")
sso_app = typer.Typer(help="Identity providers the organisation signs in with")
accounts_app = typer.Typer(help="Service accounts: machines that act for the organisation")
license_app = typer.Typer(help="The installation's licence")
quota_app = typer.Typer(help="Each workspace's own limits, under the plan's")
git_app = typer.Typer(help="Git hosts every workspace's jobs may clone from")


def register(app: typer.Typer) -> None:
    app.command("show")(show)
    app.add_typer(members_app, name="members")
    app.add_typer(sso_app, name="sso")
    app.add_typer(accounts_app, name="service-accounts")
    app.add_typer(license_app, name="license")
    app.add_typer(quota_app, name="quota")
    app.add_typer(git_app, name="git")


def _org():
    try:
        from corerun import init

        init()
    except ValueError as e:
        raise output.fail(f"{e}. Run 'corerun login' first.")
    import corerun.org as org

    return org


def _do(fn, *args, **kwargs):
    """Call the SDK, turning a lookup that found nothing into one line."""
    try:
        return fn(*args, **kwargs)
    except ValueError as e:
        raise output.fail(str(e))


def _role(role: str) -> str:
    from corerun.org import ROLES

    if role not in ROLES:
        raise output.fail(f"role is one of {', '.join(ROLES)}")
    return role


# --- show --------------------------------------------------------------------


def show():
    """The organisation: name, slug, plan, and any scheduled deletion."""
    org = _org()
    answer = org.info()

    def render():
        plan = (answer.get("plan") or {}).get("display_name") or "none"
        console.print(f"[bold]{answer.get('display_name')}[/bold] ({answer.get('slug')})")
        console.print(f"  id:     {answer.get('id')}")
        console.print(f"  plan:   {plan}")
        console.print(f"  active: {'yes' if answer.get('is_active') else 'no'}")
        if answer.get("pending_deletion_at"):
            console.print(f"  [red]deletion scheduled for {answer['pending_deletion_at']}[/red]")

    output.emit(answer, render)


# --- members -----------------------------------------------------------------


@members_app.command("list")
def members_list():
    """Everyone with a seat, how many workspaces each is in, and who administers."""
    org = _org()
    people = org.members()

    def render():
        t = Table(title="People")
        for col in ("Email", "Name", "Workspaces", "Administrator", "Last seen"):
            t.add_column(col)
        for p in people:
            t.add_row(
                p.get("email") or "",
                p.get("name") or "",
                str(p.get("workspace_count", 0)),
                "yes" if p.get("is_tenant_admin") else "",
                str(p.get("last_login") or "")[:19],
            )
        console.print(t)

    output.emit(people, render)


@members_app.command("add")
def members_add(
    email: str = typer.Argument(..., help="Their address"),
    workspace: str = typer.Argument(..., help="Workspace slug, name or id"),
    role: str = typer.Option("engineer", "--role", "-r", help="admin, engineer, deployer, analyst, member or viewer"),
):
    """
    Give somebody a role in a workspace. Somebody without an account is
    invited instead, and gets the role when they first sign in.

    Example:
        corerun org members add sara@acme.com research --role engineer
    """
    org = _org()
    answer = _do(org.add_member, email, workspace, _role(role))
    status = answer.get("status")
    output.emit(
        answer,
        lambda: console.print(
            f"[green]Invited[/green] {email} to {workspace} as {role}"
            if status == "invited"
            else f"[green]{email}[/green] is {role} in {workspace}"
        ),
    )


@members_app.command("remove")
def members_remove(
    email: str = typer.Argument(...),
    workspace: str = typer.Argument(..., help="Workspace slug, name or id"),
):
    """Take somebody's role in one workspace away. Their seat and other roles stay."""
    org = _org()
    answer = _do(org.remove_member, email, workspace)
    output.emit(answer, lambda: console.print(f"[green]{email}[/green] is no longer in {workspace}"))


@members_app.command("invites")
def members_invites():
    """Invitations nobody has accepted yet, in every workspace."""
    org = _org()
    rows = org.invites()

    def render():
        if not rows:
            console.print("No pending invitations.")
            return
        t = Table(title="Pending invitations")
        for col in ("Id", "Email", "Workspace", "Role", "Expires"):
            t.add_column(col)
        for r in rows:
            t.add_row(r["id"][:8], r["email"], r.get("workspace") or "", r["role"], str(r.get("expires_at"))[:10])
        console.print(t)

    output.emit(rows, render)


@members_app.command("cancel-invite")
def members_cancel_invite(invite: str = typer.Argument(..., help="Invitation id, or its first characters")):
    """Withdraw an invitation before it is accepted."""
    org = _org()
    answer = _do(org.cancel_invite, invite)
    output.emit(answer, lambda: console.print("[green]Invitation withdrawn[/green]"))


# --- sso ---------------------------------------------------------------------


@sso_app.command("list")
def sso_list():
    """The organisation's identity providers."""
    org = _org()
    rows = org.sign_in_providers()

    def render():
        if not rows:
            console.print("No identity provider: people sign in only as the installation allows.")
            return
        t = Table(title="Identity providers")
        for col in ("Name", "Type", "Domain", "Issuer", "Default", "Active"):
            t.add_column(col)
        for p in rows:
            t.add_row(
                p["name"],
                p["provider_type"],
                p.get("domain") or "",
                p.get("issuer_url") or "",
                "yes" if p.get("is_default") else "",
                "yes" if p.get("is_active") else "no",
            )
        console.print(t)

    output.emit(rows, render)


@sso_app.command("add")
def sso_add(
    name: str = typer.Argument(..., help="What people see on the sign-in page"),
    provider_type: str = typer.Option(..., "--type", "-t", help="entra, google, okta, auth0, github, local or custom"),
    issuer_url: Optional[str] = typer.Option(None, "--issuer", help="OIDC issuer URL"),
    client_id: Optional[str] = typer.Option(None, "--client-id"),
    client_secret: Optional[str] = typer.Option(None, "--client-secret", help="Stored encrypted; never shown again"),
    domain: Optional[str] = typer.Option(None, "--domain", help="Email domain this provider answers for"),
    admin_group: Optional[str] = typer.Option(None, "--admin-group", help="Group id whose members administer the organisation"),
    default: bool = typer.Option(False, "--default", help="Make it the one offered first"),
):
    """
    Add an identity provider. The issuer is checked before anything is saved.

    Example:
        corerun org sso add "Acme Entra" --type entra \\
            --issuer https://login.microsoftonline.com/<tenant>/v2.0 \\
            --client-id <app-id> --client-secret <secret> --domain acme.com
    """
    from corerun.org import PROVIDER_TYPES

    if provider_type not in PROVIDER_TYPES:
        raise output.fail(f"--type is one of {', '.join(PROVIDER_TYPES)}")
    org = _org()
    answer = org.add_sign_in_provider(
        name,
        provider_type,
        issuer_url=issuer_url,
        client_id=client_id,
        client_secret=client_secret,
        domain=domain,
        admin_group_id=admin_group,
        is_default=default or None,
    )
    output.emit(answer, lambda: console.print(f"[green]Added[/green] {name}"))


@sso_app.command("update")
def sso_update(
    provider: str = typer.Argument(..., help="Name or id"),
    issuer_url: Optional[str] = typer.Option(None, "--issuer"),
    client_id: Optional[str] = typer.Option(None, "--client-id"),
    client_secret: Optional[str] = typer.Option(None, "--client-secret", help="Replace the secret, e.g. after it expired"),
    domain: Optional[str] = typer.Option(None, "--domain"),
    admin_group: Optional[str] = typer.Option(None, "--admin-group"),
    active: Optional[bool] = typer.Option(None, "--active/--inactive", help="Turn it on or off without deleting it"),
):
    """Change an identity provider; only what is named changes."""
    org = _org()
    answer = _do(
        org.update_sign_in_provider,
        provider,
        issuer_url=issuer_url,
        client_id=client_id,
        client_secret=client_secret,
        domain=domain,
        admin_group_id=admin_group,
        is_active=active,
    )
    output.emit(answer, lambda: console.print(f"[green]Updated[/green] {provider}"))


@sso_app.command("default")
def sso_default(provider: str = typer.Argument(..., help="Name or id")):
    """Make a provider the one offered first."""
    org = _org()
    answer = _do(org.set_default_sign_in_provider, provider)
    output.emit(answer, lambda: console.print(f"[green]{provider}[/green] is the default"))


@sso_app.command("remove")
def sso_remove(
    provider: str = typer.Argument(..., help="Name or id"),
    yes: bool = typer.Option(False, "--yes", "-y"),
):
    """Remove an identity provider. Nobody can sign in through it afterwards."""
    if not yes:
        output.confirm(f"Remove {provider}? Everyone who signs in through it is locked out until another is added.")
    org = _org()
    answer = _do(org.remove_sign_in_provider, provider)
    output.emit(answer, lambda: console.print(f"[green]Removed[/green] {provider}"))


# --- service accounts --------------------------------------------------------


@accounts_app.command("list")
def accounts_list():
    """Service accounts, their client ids, and whether each is enabled."""
    org = _org()
    rows = org.service_accounts()

    def render():
        if not rows:
            console.print("No service accounts.")
            return
        t = Table(title="Service accounts")
        for col in ("Name", "Client id", "Enabled", "Secret rotated", "Last used"):
            t.add_column(col)
        for a in rows:
            t.add_row(
                a["name"],
                a["client_id"],
                "no" if a.get("disabled_at") else "yes",
                str(a.get("secret_rotated_at") or "")[:10] + (" (previous still valid)" if a.get("has_previous_secret") else ""),
                str(a.get("last_used_at") or "never")[:19],
            )
        console.print(t)

    output.emit(rows, render)


def _print_secret(answer: dict) -> None:
    console.print(f"  client id:     {answer['client_id']}")
    console.print(f"  client secret: {answer['client_secret']}")
    console.print("[yellow]The secret is shown this once. Store it now.[/yellow]")


@accounts_app.command("create")
def accounts_create(
    name: str = typer.Argument(...),
    description: str = typer.Option("", "--description", "-d"),
    scopes: Optional[List[str]] = typer.Option(None, "--scope", help="Limit what its tokens may do (repeatable)"),
):
    """
    Create a service account. It can do nothing until granted a workspace role.

    Example:
        corerun org service-accounts create ci-deployer
        corerun org service-accounts grant ci-deployer production --role deployer
    """
    org = _org()
    answer = org.create_service_account(name, description, scopes)
    output.emit(answer, lambda: (console.print(f"[green]Created[/green] {name}"), _print_secret(answer)))


@accounts_app.command("rotate")
def accounts_rotate(account: str = typer.Argument(..., help="Name, id or client id")):
    """A new secret. The previous one keeps working until `retire-previous`."""
    org = _org()
    answer = _do(org.rotate_service_account, account)
    output.emit(answer, lambda: (console.print(f"[green]Rotated[/green] {account}"), _print_secret(answer)))


@accounts_app.command("retire-previous")
def accounts_retire(account: str = typer.Argument(...)):
    """Stop the secret replaced by the last rotation from working."""
    org = _org()
    answer = _do(org.retire_previous_secret, account)
    output.emit(answer, lambda: console.print(f"[green]{account}[/green]: previous secret retired"))


@accounts_app.command("disable")
def accounts_disable(account: str = typer.Argument(...)):
    """Refuse its tokens at once, without deleting it."""
    org = _org()
    answer = _do(org.set_service_account_enabled, account, False)
    output.emit(answer, lambda: console.print(f"[green]Disabled[/green] {account}"))


@accounts_app.command("enable")
def accounts_enable(account: str = typer.Argument(...)):
    org = _org()
    answer = _do(org.set_service_account_enabled, account, True)
    output.emit(answer, lambda: console.print(f"[green]Enabled[/green] {account}"))


@accounts_app.command("delete")
def accounts_delete(account: str = typer.Argument(...), yes: bool = typer.Option(False, "--yes", "-y")):
    """Delete a service account. Everything using its secret stops working."""
    if not yes:
        output.confirm(f"Delete {account}? Every pipeline using its secret stops working.")
    org = _org()
    answer = _do(org.delete_service_account, account)
    output.emit(answer, lambda: console.print(f"[green]Deleted[/green] {account}"))


@accounts_app.command("workspaces")
def accounts_workspaces(account: str = typer.Argument(...)):
    """The workspaces a service account holds a role in."""
    org = _org()
    rows = _do(org.service_account_workspaces, account)

    def render():
        if not rows:
            console.print("No workspace: it can do nothing yet.")
            return
        t = Table(title=account)
        t.add_column("Workspace")
        t.add_column("Role")
        for g in rows:
            t.add_row(g.get("workspace_slug") or g.get("workspace_id"), g["role"])
        console.print(t)

    output.emit(rows, render)


@accounts_app.command("grant")
def accounts_grant(
    account: str = typer.Argument(...),
    workspace: str = typer.Argument(..., help="Workspace slug, name or id"),
    role: str = typer.Option("deployer", "--role", "-r", help="engineer, deployer, analyst, member or viewer"),
):
    """Give a service account a role in a workspace. Never admin."""
    if role == "admin":
        raise output.fail("a service account cannot administer a workspace")
    org = _org()
    answer = _do(org.grant_service_account, account, workspace, _role(role))
    output.emit(answer, lambda: console.print(f"[green]{account}[/green] is {role} in {workspace}"))


@accounts_app.command("revoke")
def accounts_revoke(account: str = typer.Argument(...), workspace: str = typer.Argument(...)):
    org = _org()
    answer = _do(org.revoke_service_account, account, workspace)
    output.emit(answer, lambda: console.print(f"[green]{account}[/green] has no role in {workspace}"))


# --- licence -----------------------------------------------------------------


@license_app.command("show")
def license_show():
    """Whether the installation is licensed, in grace, or restricted, and until when."""
    org = _org()
    answer = org.license()

    def render():
        state = answer.get("state")
        colour = {"licensed": "green", "grace": "yellow", "restricted": "red"}.get(state, "white")
        console.print(f"[{colour}]{state}[/{colour}]  {answer.get('reason') or ''}")
        if answer.get("ends_at"):
            console.print(f"  until: {answer['ends_at']}")
        if answer.get("installed"):
            console.print(f"  customer: {answer.get('customer')}   expires: {answer.get('expires_at')}")
            limits = [f"{answer[k]} {k.replace('max_', '')}" for k in ("max_users", "max_workspaces") if answer.get(k)]
            if limits:
                console.print(f"  limits: {', '.join(limits)}")
            if answer.get("features"):
                console.print(f"  features: {', '.join(answer['features'])}")

    output.emit(answer, render)


@license_app.command("install")
def license_install(path: Path = typer.Argument(..., exists=True, dir_okay=False, help="The licence file")):
    """Verify and install a licence file. Takes effect at once; no restart."""
    org = _org()
    answer = org.install_license(path.read_bytes())
    output.emit(answer, lambda: console.print(f"[green]Installed[/green]: {answer.get('state')}, expires {answer.get('expires_at')}"))


# --- workspace quotas --------------------------------------------------------


@quota_app.command("show")
def quota_show(workspace: str = typer.Argument(..., help="Workspace slug, name or id")):
    """A workspace's own limits. 0 means the plan's limit applies."""
    org = _org()
    answer = _do(org.workspace_quota, workspace)

    def render():
        t = Table(title=f"{workspace}: limits")
        t.add_column("Limit")
        t.add_column("Value", justify="right")
        for k in org.QUOTA_FIELDS:
            v = answer.get(k) or 0
            t.add_row(k.replace("max_", "").replace("_", " "), str(v) if v else "plan")
        console.print(t)

    output.emit(answer, render)


@quota_app.command("set")
def quota_set(
    workspace: str = typer.Argument(..., help="Workspace slug, name or id"),
    gpus: Optional[int] = typer.Option(None, "--gpus"),
    cpu: Optional[int] = typer.Option(None, "--cpu", help="CPU cores"),
    ram: Optional[int] = typer.Option(None, "--ram", help="GB"),
    storage: Optional[int] = typer.Option(None, "--storage", help="GB"),
    notebooks: Optional[int] = typer.Option(None, "--notebooks"),
    jobs: Optional[int] = typer.Option(None, "--jobs"),
    servers: Optional[int] = typer.Option(None, "--servers", help="Inference servers"),
):
    """
    Set a workspace's own limits. Only the ones named change; 0 removes one.

    Example:
        corerun org quota set research --gpus 8 --notebooks 20
    """
    org = _org()
    limits = {
        "max_gpu_count": gpus,
        "max_cpu_cores": cpu,
        "max_ram_gb": ram,
        "max_storage_gb": storage,
        "max_notebooks": notebooks,
        "max_jobs": jobs,
        "max_inference_servers": servers,
    }
    if all(v is None for v in limits.values()):
        raise output.fail("name a limit to set, e.g. --gpus 8")
    answer = _do(org.set_workspace_quota, workspace, **{k: v for k, v in limits.items() if v is not None})
    output.emit(answer, lambda: console.print(f"[green]Saved[/green] limits for {workspace}"))


# --- git connections ---------------------------------------------------------


@git_app.command("list")
def git_list():
    """Git hosts shared with every workspace. Tokens are never shown."""
    org = _org()
    rows = org.git_connections()

    def render():
        if not rows:
            console.print("No shared git connections.")
            return
        t = Table(title="Shared git connections")
        for col in ("Name", "Provider", "Host", "Token", "Note"):
            t.add_column(col)
        for g in rows:
            t.add_row(g["name"], g["provider"], g["host_url"], "set" if g.get("has_token") else "none", g.get("note") or "")
        console.print(t)

    output.emit(rows, render)


@git_app.command("add")
def git_add(
    name: str = typer.Argument(..., help="What jobs name with --connection"),
    provider: str = typer.Option("github", "--provider", help="github, gitlab, bitbucket, gitea or generic"),
    host_url: str = typer.Option("https://github.com", "--host", help="The host's address"),
    token: str = typer.Option(..., "--token", help="A read-only token; stored encrypted"),
    username: str = typer.Option("", "--username", help="For hosts that want one with the token"),
    note: str = typer.Option("", "--note", help="Which repositories it can read, for the next person"),
):
    """
    Share a git host with every workspace, for jobs to clone private code.
    Use a fine-grained, read-only token limited to the repositories needed.

    Example:
        corerun org git add github --token <read-only-token> --note "acme/trainer only"
    """
    from corerun.org import GIT_PROVIDERS

    if provider not in GIT_PROVIDERS:
        raise output.fail(f"--provider is one of {', '.join(GIT_PROVIDERS)}")
    org = _org()
    answer = org.add_git_connection(name, provider, host_url, token, username, note)
    output.emit(answer, lambda: console.print(f"[green]Added[/green] {name}"))


@git_app.command("test")
def git_test(
    connection: str = typer.Argument(..., help="Name or id"),
    repo: str = typer.Argument(..., help="A repository it should read, e.g. acme/trainer"),
):
    """Check the token can read a repository."""
    org = _org()
    answer = _do(org.test_git_connection, connection, repo)

    def render():
        if answer.get("ok"):
            console.print(f"[green]Readable[/green]: {len(answer.get('refs') or [])} branches and tags")
        else:
            console.print(f"[red]Not readable[/red]: {answer.get('message')}")

    output.emit(answer, render)
    if not answer.get("ok"):
        raise typer.Exit(1)


@git_app.command("remove")
def git_remove(connection: str = typer.Argument(...), yes: bool = typer.Option(False, "--yes", "-y")):
    """Remove a shared git connection. Jobs naming it can no longer clone."""
    if not yes:
        output.confirm(f"Remove {connection}? Jobs that clone through it will fail.")
    org = _org()
    answer = _do(org.remove_git_connection, connection)
    output.emit(answer, lambda: console.print(f"[green]Removed[/green] {connection}"))
