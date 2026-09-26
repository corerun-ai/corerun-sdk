"""Groups: a named set of people, and the roles that set holds.

Organization-wide, like storage and the container registry: a group belongs to
the organization and is granted a role in whichever of its workspaces it needs
one. Granting a group is one act however many people are in it, and adding
somebody to the group gives them the role everywhere it holds one.

These are administrator commands. They change what exists for everyone in the
organization, not what runs inside one workspace.
"""

from typing import Optional

import typer
from rich.table import Table

from corerun.cli import output
from corerun.config import get_config

console = output.console
app = typer.Typer(help="Groups, and the roles they hold")


def _require_credentials():
    config = get_config()
    if not config.auth_token:
        console.print("[red]Error:[/red] not signed in. Run 'corerun login' first.")
        raise typer.Exit(1)
    return config


def _call(method: str, path: str, json_body=None):
    """One request to the organization's group surface.

    Organization-wide, like storage: no workspace header, because a group
    belongs to the organization and is granted into workspaces rather than
    living in one.
    """
    from corerun.cli import http
    from corerun.exceptions import CoreRunError

    _require_credentials()
    try:
        return http.request(method, "/tenant" + path, json=json_body)
    except CoreRunError as e:
        # The platform's own reason, said once -- not a traceback around it.
        raise output.fail(str(e))


def _get(path: str):
    return _call("GET", path)


def _post(path: str, body: dict):
    return _call("POST", path, body)


def _delete(path: str):
    return _call("DELETE", path)


@app.command("list")
def list_groups():
    """
    List the organization's groups.

    Example:
        corerun groups list
    """
    groups = _get("/groups").get("groups", [])
    if not groups:
        console.print("No groups.")
        return

    table = Table(title="Groups")
    table.add_column("Group")
    table.add_column("Slug")
    table.add_column("People", justify="right")
    table.add_column("Workspaces", justify="right")
    table.add_column("")
    for group in groups:
        table.add_row(
            group["display_name"],
            group["slug"],
            str(group["member_count"]),
            # An organization's administrators reach every workspace through the
            # organization, so a count of grants would say 0 and mean the
            # opposite.
            "all" if group["tenant_admin"] else str(group["grant_count"]),
            "built in" if group["is_predefined"] else "",
        )
    console.print(table)


@app.command("show")
def show_group(name: str = typer.Argument(..., help="Group name or slug")):
    """
    Show who is in a group and what it grants.

    Example:
        corerun groups show engineers
    """
    members = _get(f"/groups/{name}/members")
    grants = _get(f"/groups/{name}/grants")

    if members.get("total"):
        people = Table(title=f"{name}: people")
        people.add_column("Email")
        people.add_column("From")
        for member in members["members"]:
            # Where a membership came from decides whether removing it sticks:
            # a directory sync withdraws only the rows it wrote.
            people.add_row(member["email"], member["source"])
        console.print(people)
    else:
        console.print("Nobody in this group.")

    if grants.get("tenant_admin"):
        console.print(
            "\nThis group administers the organization, so its members reach "
            "every workspace through it."
        )
        return

    if grants.get("total"):
        held = Table(title=f"{name}: roles in workspaces")
        held.add_column("Workspace")
        held.add_column("Role")
        for grant in grants["grants"]:
            held.add_row(grant.get("display_name") or grant["slug"], grant["role"])
        console.print(held)
    else:
        console.print("\nNo workspace yet.")


@app.command("create")
def create_group(
    name: str = typer.Argument(..., help="Display name, e.g. 'Speech Team'"),
    description: Optional[str] = typer.Option(None, "--description", "-d", help="What it is for"),
):
    """
    Create a group.

    Example:
        corerun groups create "Speech Team"
    """
    group = _post("/groups", {"display_name": name, "description": description or ""})
    console.print(f"[green]Created {group['display_name']}[/green] ({group['slug']})")
    console.print(f"  corerun groups grant {group['slug']} <workspace> --role engineer")


@app.command("delete")
def delete_group(
    name: str = typer.Argument(..., help="Group name or slug"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask"),
):
    """
    Delete a group.

    Everyone in it loses the access it granted, in every workspace it holds a
    role in. Nobody is removed from the organization.

    Example:
        corerun groups delete speech-team
    """
    if not yes:
        output.confirm(f"Delete {name}? Everyone in it loses the access it granted.")
    _delete(f"/groups/{name}")
    console.print(f"[green]Deleted {name}[/green]")


@app.command("add")
def add_member(
    name: str = typer.Argument(..., help="Group name or slug"),
    email: str = typer.Argument(..., help="Who to add"),
):
    """
    Put somebody in a group.

    They get every role the group holds, in every workspace it holds one.

    Example:
        corerun groups add engineers sara@acme.com
    """
    users = _get("/users").get("users", [])
    match = next((u for u in users if u.get("email", "").lower() == email.lower()), None)
    if not match:
        console.print(f"[red]Error:[/red] nobody in this organization has the address {email}")
        raise typer.Exit(1)
    _post(f"/groups/{name}/members", {"user_id": match["id"]})
    console.print(f"[green]{email} is in {name}[/green]")


@app.command("remove")
def remove_member(
    name: str = typer.Argument(..., help="Group name or slug"),
    email: str = typer.Argument(..., help="Who to remove"),
):
    """
    Take somebody out of a group.

    They lose everything the group gave them, everywhere, at once.

    Example:
        corerun groups remove engineers sara@acme.com
    """
    members = _get(f"/groups/{name}/members").get("members", [])
    match = next((m for m in members if m.get("email", "").lower() == email.lower()), None)
    if not match:
        console.print(f"[red]Error:[/red] {email} is not in {name}")
        raise typer.Exit(1)
    _delete(f"/groups/{name}/members/{match['user_id']}")
    console.print(f"[green]{email} is no longer in {name}[/green]")


@app.command("roles")
def list_roles():
    """
    The roles a group can hold in a workspace, and what each permits.

    Example:
        corerun groups roles
    """
    table = Table(title="Roles")
    table.add_column("Role")
    table.add_column("Permits")
    for role in _get("/roles").get("roles", []):
        table.add_row(role["role"], role["description"])
    console.print(table)


@app.command("grant")
def grant_role(
    name: str = typer.Argument(..., help="Group name or slug"),
    workspace: str = typer.Argument(..., help="Workspace name, slug or ID"),
    role: str = typer.Option(..., "--role", "-r", help="See: corerun groups roles"),
):
    """
    Give a group a role in a workspace.

    Everyone in the group gets it, including people added afterwards.

    Example:
        corerun groups grant engineers speech --role engineer
        corerun groups grant analysts speech --role analyst
    """
    workspaces = _get("/workspaces").get("workspaces", [])
    match = next(
        (w for w in workspaces if workspace in (w.get("id"), w.get("slug"), w.get("display_name"))),
        None,
    )
    if not match:
        console.print(f"[red]Error:[/red] no workspace called {workspace}")
        raise typer.Exit(1)
    _post(f"/groups/{name}/grants", {"workspace_id": match["id"], "role": role})
    console.print(f"[green]{name} is {role} in {workspace}[/green]")


@app.command("revoke")
def revoke_role(
    name: str = typer.Argument(..., help="Group name or slug"),
    workspace: str = typer.Argument(..., help="Workspace name, slug or ID"),
):
    """
    Take a group's role in a workspace away.

    Example:
        corerun groups revoke engineers speech
    """
    grants = _get(f"/groups/{name}/grants").get("grants", [])
    match = next(
        (
            g
            for g in grants
            if workspace in (g.get("workspace_id"), g.get("slug"), g.get("display_name"))
        ),
        None,
    )
    if not match:
        console.print(f"[red]Error:[/red] {name} holds no role in {workspace}")
        raise typer.Exit(1)
    _delete(f"/groups/{name}/grants/{match['workspace_id']}")
    console.print(f"[green]{name} no longer has a role in {workspace}[/green]")


@app.command("links")
def list_links(name: str = typer.Argument(..., help="Group name or slug")):
    """
    The directory groups that put people in a group at sign-in.
    """
    mappings = _get(f"/groups/{name}/mappings").get("mappings", [])

    def render():
        if not mappings:
            console.print(f"[dim]No directory group is linked to {name}.[/dim]")
            return
        for m in mappings:
            console.print(m.get("directory_group", ""))

    output.emit(mappings, render)


@app.command("link")
def link_directory_group(
    name: str = typer.Argument(..., help="Group name or slug"),
    directory_group: str = typer.Argument(..., help="The group as your identity provider's token names it: a name, or Entra's object id"),
):
    """
    Put everyone in a directory group into this group, at their next sign-in.

    They are taken out again when they sign in no longer in it. Anyone added
    by hand stays regardless. Entra sends group object ids unless it is set to
    send names; Okta and Google send names.

    Example:
        corerun groups link speech "Speech Team"
        corerun groups link speech 0d6c7c1e-4b1a-4f0e-9f35-1b2e3c4d5e6f
    """
    _post(f"/groups/{name}/mappings", {"directory_group": directory_group})
    console.print(f"[green]{directory_group} -> {name}[/green]: applied at each person's next sign-in")


@app.command("unlink")
def unlink_directory_group(
    name: str = typer.Argument(..., help="Group name or slug"),
    directory_group: str = typer.Argument(..., help="The linked directory group"),
):
    """
    Stop a directory group putting people in this group.

    When it was the last link, the people it put there leave now.
    """
    mappings = _get(f"/groups/{name}/mappings").get("mappings", [])
    match = next((m for m in mappings if m.get("directory_group", "").lower() == directory_group.lower()), None)
    if not match:
        console.print(f"[red]Error:[/red] {directory_group} is not linked to {name}")
        raise typer.Exit(1)
    answer = _delete(f"/groups/{name}/mappings/{match['id']}") or {}
    withdrawn = answer.get("withdrawn") or 0
    console.print(f"[green]Unlinked {directory_group}[/green]" + (f"; {withdrawn} people it added have left {name}" if withdrawn else ""))
