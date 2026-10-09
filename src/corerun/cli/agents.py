"""
Agent CLI commands.

An agent is built once -- a harness, instructions, the connectors it is given
and the policy it works under -- and then used by everyone it is shared with,
each in a sandbox of their own. These commands cover both halves: building
and sharing one, and talking to it.

`chat` answers what the agent can answer from its model alone. A question that
needs its sandbox and tools is handed over, and continues in the console,
which runs the sandbox; this says so rather than pretending to.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

import typer
import yaml
from rich.table import Table

from corerun.cli import output, resolve

console = output.console
app = typer.Typer(help="Agents: build, share, tools, policy, chat")

ACCESS_LEVELS = ("chat", "edit", "none")
TOOL_POLICIES = ("allow", "ask", "off")


def _init_client():
    """Initialize client, handling errors gracefully"""
    try:
        from corerun import init
        return init()
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        console.print("Run 'corerun login' to authenticate")
        raise typer.Exit(1)


def _fail(e: Any) -> "typer.Exit":
    """The platform's reason, said once -- not a traceback around it."""
    return output.fail(str(e))


def _json(flag: bool) -> None:
    if flag:
        output.set_json(True)


def _resolve(reference: str, workspace: Optional[str]) -> str:
    """An agent by name, id or the short id `agents list` prints."""
    import corerun.agents as agents

    return resolve.by_name_or_id(reference, lambda: agents.list(workspace=workspace), "agent")


def _console_url(agent_id: str) -> Optional[str]:
    """Where the agent opens in the console, from the address this CLI calls."""
    from corerun.config import get_config

    api = (get_config().api_url or "").rstrip("/")
    if not api.endswith("/api/v1"):
        return None
    return f"{api[: -len('/api/v1')]}/agents/{agent_id}"


def _yaml(value: Any) -> str:
    return yaml.safe_dump(value, sort_keys=False, default_flow_style=False).rstrip()


# ── the agent itself ────────────────────────────────────────────────────────


@app.command("list")
def list_agents(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    List the agents you may use in the workspace.

    Example:
        corerun agents list
    """
    _init_client()
    _json(json_output)
    import corerun.agents as agents

    try:
        found = agents.list(workspace=workspace)
    except Exception as e:
        raise _fail(e)

    def render():
        if not found:
            console.print("No agents here yet. Create one: corerun agents create <name> --compute <target>")
            return
        table = Table(title="Agents")
        table.add_column("Name")
        table.add_column("ID")
        table.add_column("Harness")
        table.add_column("Compute")
        table.add_column("Creator")
        table.add_column("")
        for a in found:
            table.add_row(
                a.name,
                a.agent_id[:8],
                a.harness,
                a.compute_name,
                a.creator_email or a.creator_name or "",
                "yours to manage" if a.may_manage else "",
            )
        console.print(table)

    output.emit(found, render)


@app.command("mine")
def mine(json_output: bool = typer.Option(False, "--json", help="Output as JSON")):
    """
    List the agents you may use in every workspace you belong to.

    Example:
        corerun agents mine
    """
    _init_client()
    _json(json_output)
    import corerun.agents as agents

    try:
        found = agents.mine()
    except Exception as e:
        raise _fail(e)

    def render():
        if not found:
            console.print("No agents in any of your workspaces.")
            return
        table = Table(title="Your agents")
        table.add_column("Name")
        table.add_column("ID")
        table.add_column("Workspace")
        table.add_column("Organisation")
        for a in found:
            table.add_row(a.get("name", ""), str(a.get("agent_id", ""))[:8], a.get("workspace_name", ""), a.get("tenant_name", ""))
        console.print(table)

    output.emit(found, render)


@app.command("get")
def get_agent(
    agent: str = typer.Argument(..., help="Agent name or ID"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    Show an agent: harness, compute, model, starters, tools and your sandbox.

    Example:
        corerun agents get support-bot
    """
    _init_client()
    _json(json_output)
    import corerun.agents as agents

    resolved = _resolve(agent, workspace)
    try:
        a = agents.get(resolved, workspace=workspace)
    except Exception as e:
        raise _fail(e)

    def render():
        console.print(f"[bold]{a.name}[/bold]  [dim]{a.agent_id}[/dim]")
        if a.description:
            console.print(f"  {a.description}")
        console.print(f"  Harness: {a.harness}   Compute: {a.compute_name}")
        console.print(f"  Model: {a.model or 'the organisation default'}")
        if a.creator_email or a.creator_name:
            console.print(f"  Creator: {a.creator_email or a.creator_name}")
        if a.sandbox:
            console.print("  Sandbox: " + ", ".join(f"{k} {v}" for k, v in a.sandbox.items()))
        if a.network:
            mode = "rules enforced" if a.network == "enforce" else "reported, not enforced"
            console.print(f"  Network: {mode}")
        if a.ready is not None:
            console.print(f"  Sandboxes kept ready: {a.ready} (ready now: {a.ready_sandboxes})")
        if a.telemetry and a.telemetry.get("enabled"):
            console.print(f"  Telemetry: on ({a.telemetry.get('experiment_name') or a.telemetry.get('experiment_id', '')})")
        if a.tool_names:
            console.print(f"  Tools: {', '.join(a.tool_names)}")
        if a.starters:
            console.print("  Starters:")
            for s in a.starters:
                console.print(f"    - {s}")
        if a.session:
            console.print(f"  Your sandbox: {a.session.status}" + (f" ({a.session.progress})" if a.session.progress else ""))
        if a.may_manage and a.instructions:
            console.print("  Instructions:")
            console.print(a.instructions, markup=False, highlight=False)
        url = _console_url(a.agent_id)
        if url:
            console.print(f"  Open: {url}")

    output.emit(a, render)


def _instructions(text: Optional[str], file: Optional[Path]) -> Optional[str]:
    if text is not None and file is not None:
        raise output.fail("Give --instructions or --instructions-file, not both.")
    if file is not None:
        try:
            return file.read_text(encoding="utf-8")
        except OSError as e:
            raise output.fail(f"Cannot read {file}: {e}")
    return text


@app.command("create")
def create_agent(
    name: str = typer.Argument(..., help="The agent's name"),
    compute: str = typer.Option(..., "--compute", "-c", help="Compute its sandboxes run on (corerun compute list)"),
    harness: Optional[str] = typer.Option(None, "--harness", help="What runs it, e.g. opencode (the default), goose"),
    description: Optional[str] = typer.Option(None, "--description", "-d", help="What it is for"),
    instructions: Optional[str] = typer.Option(None, "--instructions", "-i", help="Its instructions"),
    instructions_file: Optional[Path] = typer.Option(None, "--instructions-file", help="Read its instructions from a file"),
    starter: Optional[List[str]] = typer.Option(None, "--starter", help="A suggested first question (repeatable, at most 4)"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    Create an agent.

    It starts with no tools and the default policy (reads only). Give it tools
    with `corerun agents tools-set`, and people with `corerun agents grant`.

    Example:
        corerun agents create support-bot --compute gb10 -d "Answers support questions" --instructions-file prompt.md
    """
    _init_client()
    _json(json_output)
    import corerun.agents as agents

    text = _instructions(instructions, instructions_file)
    try:
        a = agents.create(
            name,
            compute,
            description=description,
            instructions=text,
            harness=harness,
            starters=list(starter) if starter else None,
            workspace=workspace,
        )
    except Exception as e:
        raise _fail(e)

    def render():
        console.print(f"[green]Created[/green] {a.name} [dim]{a.agent_id}[/dim] ({a.harness} on {a.compute_name})")

    output.emit(a, render)


@app.command("update")
def update_agent(
    agent: str = typer.Argument(..., help="Agent name or ID"),
    name: Optional[str] = typer.Option(None, "--name", help="A new name"),
    description: Optional[str] = typer.Option(None, "--description", "-d", help="What it is for"),
    instructions: Optional[str] = typer.Option(None, "--instructions", "-i", help="Its instructions"),
    instructions_file: Optional[Path] = typer.Option(None, "--instructions-file", help="Read its instructions from a file"),
    harness: Optional[str] = typer.Option(None, "--harness", help="What runs it"),
    model: Optional[str] = typer.Option(None, "--model", help="Its model; '' for the organisation's default"),
    starter: Optional[List[str]] = typer.Option(None, "--starter", help="Replace its suggested first questions (repeatable)"),
    ready: Optional[int] = typer.Option(None, "--ready", min=0, max=5, help="Sandboxes kept warm, 0-5"),
    shell: Optional[str] = typer.Option(None, "--shell", help="Its shell in the sandbox: allow, ask or off"),
    web: Optional[str] = typer.Option(None, "--web", help="Its web access in the sandbox: allow, ask or off"),
    enforce_network: bool = typer.Option(
        False, "--enforce-network",
        help="Enforce its network rules from now on (an older agent only reports); not undone",
    ),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    Change an agent. Anything not given stays as it is.

    Example:
        corerun agents update support-bot --instructions-file prompt.md --ready 1
        corerun agents update support-bot --enforce-network
    """
    _init_client()
    _json(json_output)
    import corerun.agents as agents

    for flag, value in (("--shell", shell), ("--web", web)):
        if value is not None and value not in TOOL_POLICIES:
            raise output.fail(f"{flag} is one of {', '.join(TOOL_POLICIES)}, not {value!r}.")

    resolved = _resolve(agent, workspace)
    fields: Dict[str, Any] = {
        "name": name,
        "description": description,
        "instructions": _instructions(instructions, instructions_file),
        "harness": harness,
        "model": model,
        "starters": list(starter) if starter else None,
        "ready": ready,
        "network": "enforce" if enforce_network else None,
    }
    try:
        if shell is not None or web is not None:
            current = agents.get(resolved, workspace=workspace).sandbox or {}
            sandbox = dict(current)
            if shell is not None:
                sandbox["shell"] = shell
            if web is not None:
                sandbox["web"] = web
            fields["sandbox"] = sandbox
        a = agents.update(resolved, workspace=workspace, **fields)
    except typer.Exit:
        raise
    except Exception as e:
        raise _fail(e)

    output.emit(a, lambda: console.print(f"[green]Updated[/green] {a.name}"))


@app.command("delete")
def delete_agent(
    agent: str = typer.Argument(..., help="Agent name or ID"),
    force: bool = typer.Option(False, "--yes", "-y", help="Do not ask for confirmation"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Delete an agent, and everyone's conversations with it.

    Example:
        corerun agents delete support-bot --yes
    """
    _init_client()
    import corerun.agents as agents

    resolved = _resolve(agent, workspace)
    if not force:
        output.confirm(f"Delete agent {agent}, and every conversation anyone had with it?")
    try:
        agents.delete(resolved, workspace=workspace)
    except Exception as e:
        raise _fail(e)
    console.print(f"[green]Deleted[/green] {agent}")


@app.command("harnesses")
def list_harnesses(
    agent: str = typer.Argument(..., help="Agent name or ID"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    The harnesses the agent's compute can run.

    Example:
        corerun agents harnesses support-bot
    """
    _init_client()
    _json(json_output)
    import corerun.agents as agents

    resolved = _resolve(agent, workspace)
    try:
        answer = agents.harnesses(resolved, workspace=workspace)
    except Exception as e:
        raise _fail(e)

    def render():
        table = Table(title=f"Harnesses on {answer.get('compute', '')}")
        table.add_column("Harness")
        table.add_column("Name")
        table.add_column("Available")
        table.add_column("About")
        for h in answer.get("harnesses") or []:
            table.add_row(h.get("harness", ""), h.get("name", ""), "yes" if h.get("available") else "no", h.get("description") or "")
        console.print(table)

    output.emit(answer, render)


# ── who may use it ──────────────────────────────────────────────────────────


@app.command("access")
def show_access(
    agent: str = typer.Argument(..., help="Agent name or ID"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    Who may use an agent: people, groups, invitations, and everyone in the workspace.

    Example:
        corerun agents access support-bot
    """
    _init_client()
    _json(json_output)
    import corerun.agents as agents

    resolved = _resolve(agent, workspace)
    try:
        answer = agents.access(resolved, workspace=workspace)
    except Exception as e:
        raise _fail(e)

    def render():
        table = Table(title="Access")
        table.add_column("Who")
        table.add_column("Kind")
        table.add_column("Access")
        for p in answer.get("people") or []:
            table.add_row(p.get("email") or p.get("name", ""), "person", "creator" if p.get("creator") else p.get("access", ""))
        for g in answer.get("groups") or []:
            table.add_row(g.get("name", ""), f"group ({g.get('members', 0)})", g.get("access", ""))
        for i in answer.get("invites") or []:
            table.add_row(i.get("email", ""), "invited", f"until {i.get('expires_at', '')}")
        console.print(table)
        workspace_name = answer.get("workspace") or "the workspace"
        console.print(
            f"Everyone in {workspace_name} may chat with it." if answer.get("everyone")
            else f"Not open to everyone in {workspace_name} (corerun agents everyone {agent} on)."
        )

    output.emit(answer, render)


@app.command("grant")
def grant(
    agent: str = typer.Argument(..., help="Agent name or ID"),
    who: str = typer.Argument(..., help="A person's email, or a group's name"),
    level: str = typer.Argument(..., help="chat, edit, or none to take it away"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Give a person or a group access to an agent, change it, or take it away.

    `chat` uses it; `edit` also changes it. A person not yet listed is invited
    by address -- shared at once if they are in the workspace.

    Example:
        corerun agents grant support-bot sam@example.com chat
        corerun agents grant support-bot "Support Team" edit
    """
    _init_client()
    import corerun.agents as agents

    if level not in ACCESS_LEVELS:
        raise output.fail(f"Access is one of {', '.join(ACCESS_LEVELS)}, not {level!r}.")
    wanted = "" if level == "none" else level

    resolved = _resolve(agent, workspace)
    try:
        current = agents.access(resolved, workspace=workspace)
        key = who.strip().lower()
        person = next((p for p in current.get("people") or [] if (p.get("email") or "").lower() == key), None)
        groups = (current.get("groups") or []) + (current.get("available_groups") or [])
        group = next((g for g in groups if (g.get("name") or "").lower() == key), None)

        if person is not None:
            if person.get("creator"):
                raise output.fail(f"{who} created {agent}; a creator's access does not change.")
            agents.set_access(resolved, "user", person["user_id"], wanted, workspace=workspace)
        elif group is not None:
            agents.set_access(resolved, "group", group["group_id"], wanted, workspace=workspace)
        elif "@" in who:
            if not wanted:
                console.print(f"{who} has no access to {agent}.")
                return
            invited = agents.invite(resolved, who, workspace=workspace)
            if invited.get("status") == "invited":
                console.print(f"[green]Invited[/green] {who}: they get {agent} once they accept.")
                return
            if wanted == "edit":
                again = agents.access(resolved, workspace=workspace)
                person = next(p for p in again.get("people") or [] if (p.get("email") or "").lower() == key)
                agents.set_access(resolved, "user", person["user_id"], "edit", workspace=workspace)
        else:
            known = ", ".join(sorted({g.get("name", "") for g in groups})) or "none"
            raise output.fail(f"No group called '{who}' here (groups: {known}). For a person, give their email.")
    except typer.Exit:
        raise
    except Exception as e:
        raise _fail(e)

    console.print(f"[green]{who}[/green]: {level} on {agent}")


@app.command("everyone")
def everyone(
    agent: str = typer.Argument(..., help="Agent name or ID"),
    state: str = typer.Argument(..., help="on or off"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Let everyone in the workspace chat with an agent, or stop.

    Example:
        corerun agents everyone support-bot on
    """
    _init_client()
    import corerun.agents as agents

    if state not in ("on", "off"):
        raise output.fail(f"Say on or off, not {state!r}.")
    resolved = _resolve(agent, workspace)
    try:
        agents.set_everyone(resolved, state == "on", workspace=workspace)
    except Exception as e:
        raise _fail(e)
    console.print(f"{agent}: everyone in the workspace {'may' if state == 'on' else 'may no longer'} chat with it")


@app.command("invite")
def invite(
    agent: str = typer.Argument(..., help="Agent name or ID"),
    email: str = typer.Argument(..., help="Who to invite"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Share an agent with someone by email; shared at once if they are in the workspace.

    Example:
        corerun agents invite support-bot sam@example.com
    """
    _init_client()
    import corerun.agents as agents

    resolved = _resolve(agent, workspace)
    try:
        answer = agents.invite(resolved, email, workspace=workspace)
    except Exception as e:
        raise _fail(e)
    if answer.get("status") == "shared":
        console.print(f"[green]Shared[/green] {agent} with {email}")
    else:
        console.print(f"[green]Invited[/green] {email} to {agent}")


@app.command("invites")
def list_invites(
    agent: str = typer.Argument(..., help="Agent name or ID"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    Invitations to an agent that nobody has accepted yet.

    Example:
        corerun agents invites support-bot
    """
    _init_client()
    _json(json_output)
    import corerun.agents as agents

    resolved = _resolve(agent, workspace)
    try:
        found = agents.access(resolved, workspace=workspace).get("invites") or []
    except Exception as e:
        raise _fail(e)

    def render():
        if not found:
            console.print("No open invitations.")
            return
        for i in found:
            console.print(f"  {i.get('email', '')}  [dim]until {i.get('expires_at', '')}  {i.get('invite_id', '')}[/dim]")

    output.emit(found, render)


@app.command("telemetry")
def telemetry(
    agent: str = typer.Argument(..., help="Agent name or ID"),
    state: str = typer.Argument(..., help="on or off"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Trace every turn of an agent into an experiment of its own, or stop.

    Example:
        corerun agents telemetry support-bot on
    """
    _init_client()
    import corerun.agents as agents

    if state not in ("on", "off"):
        raise output.fail(f"Say on or off, not {state!r}.")
    resolved = _resolve(agent, workspace)
    try:
        a = agents.set_telemetry(resolved, state == "on", workspace=workspace)
    except Exception as e:
        raise _fail(e)
    where = (a.telemetry or {}).get("experiment_name")
    console.print(f"{a.name}: telemetry {state}" + (f", into experiment {where}" if state == "on" and where else ""))


@app.command("publish")
def publish(
    agent: str = typer.Argument(..., help="Agent name or ID"),
    as_: str = typer.Option(
        "itself",
        "--as",
        help="itself (a support or SRE agent), me, or the email of the person it acts as",
    ),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Let an agent answer with nobody signed in -- through your organisation's
    channels -- as itself or as a person.

    Examples:
        corerun agents publish support-bot
        corerun agents publish my-assistant --as me
        corerun agents publish their-assistant --as someone@example.com
    """
    _init_client()
    import corerun.agents as agents

    resolved = _resolve(agent, workspace)
    try:
        if as_ == "itself":
            pub = agents.publish(resolved, mode="service", workspace=workspace)
        else:
            delegate = None
            if as_ != "me":
                people = agents.access(resolved, workspace=workspace).get("people") or []
                match = [p for p in people if (p.get("email") or "").lower() == as_.lower()]
                if not match:
                    raise output.fail(
                        f"{as_} cannot use {agent}: give it to them first (corerun agents grant)."
                    )
                delegate = match[0]["user_id"]
            pub = agents.publish(
                resolved, mode="delegate", delegate_id=delegate, workspace=workspace
            )
    except typer.Exit:
        raise
    except Exception as e:
        raise _fail(e)
    console.print(f"{agent}: {_publication_status(pub)}")


@app.command("publication")
def publication(
    agent: str = typer.Argument(..., help="Agent name or ID"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Whether an agent is published, and as whom it answers.
    """
    _init_client()
    import corerun.agents as agents

    try:
        pub = agents.publication(_resolve(agent, workspace), workspace=workspace)
    except Exception as e:
        raise _fail(e)
    console.print(f"{agent}: {_publication_status(pub)}")


@app.command("unpublish")
def unpublish(
    agent: str = typer.Argument(..., help="Agent name or ID"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Stop an agent answering with nobody signed in. An agent published as
    itself loses its own account.
    """
    _init_client()
    import corerun.agents as agents

    try:
        agents.unpublish(_resolve(agent, workspace), workspace=workspace)
    except Exception as e:
        raise _fail(e)
    console.print(f"{agent}: unpublished")


def _publication_status(pub: dict) -> str:
    if not pub.get("published"):
        return "not published"
    if pub.get("waiting") == "switched_off":
        return "published, switched off"
    who = pub.get("delegate_name") or pub.get("delegate_email") or "them"
    if pub.get("waiting") == "delegate_confirmation":
        return f"published as {who}, waiting for them to say so in the console"
    if pub.get("mode") == "delegate":
        return f"answers as {who}"
    return "answers as itself"


# ── tools and policy ────────────────────────────────────────────────────────


@app.command("tools")
def show_tools(
    agent: str = typer.Argument(..., help="Agent name or ID"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    The workspace's connectors, which ones an agent is given, and each tool's policy.

    Example:
        corerun agents tools support-bot
    """
    _init_client()
    _json(json_output)
    import corerun.agents as agents

    resolved = _resolve(agent, workspace)
    try:
        found = agents.tools(resolved, workspace=workspace)
    except Exception as e:
        raise _fail(e)

    def render():
        if not found:
            console.print("This workspace has no connectors. Add one: corerun connectors add")
            return
        table = Table(title="Tools")
        table.add_column("Connector")
        table.add_column("Kind")
        table.add_column("Given")
        table.add_column("Tool policies")
        for entry in found:
            c = entry.get("connector") or {}
            policies = entry.get("policies") or {}
            table.add_row(
                c.get("name", ""),
                c.get("kind", ""),
                "yes" if entry.get("enabled") else "",
                ", ".join(f"{t}={p}" for t, p in sorted(policies.items())),
            )
        console.print(table)

    output.emit(found, render)


def _connectors_by_name(workspace: Optional[str]) -> Dict[str, Dict[str, Any]]:
    from corerun.config import get_client

    found = get_client().get("/connectors", workspace=workspace).get("connectors") or []
    by: Dict[str, Dict[str, Any]] = {}
    for c in found:
        by[c["name"].lower()] = c
        by[c["id"]] = c
    return by


@app.command("tools-set")
def set_tools(
    agent: str = typer.Argument(..., help="Agent name or ID"),
    connector: Optional[List[str]] = typer.Option(None, "--connector", help="Give it this connector (repeatable); the list replaces its tools"),
    tool_policy: Optional[List[str]] = typer.Option(
        None, "--tool-policy", help="CONNECTOR:TOOL=allow|ask|off, or TOOL=... with one connector (repeatable)"
    ),
    clear: bool = typer.Option(False, "--clear", help="Take every connector away"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Give an agent exactly these connectors, with per-tool policies.

    The connectors given replace the agent's. A tool with no policy is asked
    about each time. A Kubernetes connector takes no tool policies: what the
    agent may do there is a policy's (corerun agents policy-set).

    Example:
        corerun agents tools-set support-bot --connector github --tool-policy search_issues=allow --tool-policy create_issue=ask
    """
    _init_client()
    import corerun.agents as agents

    if clear and connector:
        raise output.fail("Give --connector or --clear, not both.")
    if not clear and not connector:
        raise output.fail("Name the connectors to give it with --connector, or take them all away with --clear.")

    resolved = _resolve(agent, workspace)
    try:
        chosen: List[Dict[str, Any]] = []
        if connector:
            known = _connectors_by_name(workspace)
            for name in connector:
                c = known.get(name.lower()) or known.get(name)
                if c is None:
                    names = ", ".join(sorted({v["name"] for v in known.values()})) or "none"
                    raise output.fail(f"No connector called '{name}' in this workspace (connectors: {names}).")
                chosen.append({"connector_id": c["id"], "name": c["name"], "kind": c.get("kind", "mcp"), "policies": {}})

        for spec in tool_policy or []:
            target, _, effect = spec.partition("=")
            if effect not in TOOL_POLICIES:
                raise output.fail(f"{spec!r}: a tool's policy is one of {', '.join(TOOL_POLICIES)}.")
            connector_name, _, tool = target.rpartition(":")
            if connector_name:
                entry = next((c for c in chosen if c["name"].lower() == connector_name.lower()), None)
                if entry is None:
                    raise output.fail(f"{spec!r} names connector '{connector_name}', which is not among --connector.")
            elif len(chosen) == 1:
                entry = chosen[0]
            else:
                raise output.fail(f"{spec!r}: with several connectors, say which: CONNECTOR:TOOL=...")
            if entry["kind"] == "kubernetes":
                raise output.fail(f"{entry['name']} is a Kubernetes connector; what an agent may do there is its policy's.")
            entry["policies"][tool] = effect

        body = [{"connector_id": c["connector_id"], "policies": c["policies"]} for c in chosen]
        agents.set_tools(resolved, body, workspace=workspace)
    except typer.Exit:
        raise
    except Exception as e:
        raise _fail(e)

    if not chosen:
        console.print(f"{agent}: no connectors")
    else:
        console.print(f"{agent}: " + ", ".join(c["name"] for c in chosen))


@app.command("policy")
def show_policy(
    agent: str = typer.Argument(..., help="Agent name or ID"),
    network: bool = typer.Option(
        False, "--network", help="Its network rules: where its sandbox may connect"
    ),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    An agent's own policy rules, as YAML that policy-set takes back.

    The organisation's and workspace's policies apply as well
    (corerun policies list); the strictest answer wins.

    Example:
        corerun agents policy sre-bot > rules.yaml
    """
    _init_client()
    _json(json_output)
    import corerun.agents as agents

    resolved = _resolve(agent, workspace)
    try:
        kind = "network" if network else "tools"
        answer = agents.policy(resolved, workspace=workspace, kind=kind)
    except Exception as e:
        raise _fail(e)

    def render():
        if answer.get("default"):
            output.errors.print("[dim]# The default: no policy of its own yet.[/dim]")
        print(_yaml({"rules": answer.get("rules") or []}))

    output.emit(answer, render)


def _read_rules(path: Path) -> List[Dict[str, Any]]:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise output.fail(f"Cannot read {path}: {e}")
    except yaml.YAMLError as e:
        raise output.fail(f"{path} is not YAML: {e}")
    if isinstance(loaded, dict):
        loaded = loaded.get("rules")
    if not isinstance(loaded, list) or not all(isinstance(r, dict) for r in loaded):
        raise output.fail(f"{path} should hold a list of rules, or rules: [...]")
    return loaded


@app.command("policy-set")
def set_policy(
    agent: str = typer.Argument(..., help="Agent name or ID"),
    file: Optional[Path] = typer.Option(None, "--file", "-f", help="YAML: a list of rules, or rules: [...]"),
    default: bool = typer.Option(False, "--default", help="Drop its own rules and go back to the default (reads only)"),
    network: bool = typer.Option(
        False, "--network", help="Its network rules (host, port, action, agent)"
    ),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Replace an agent's own policy rules.

    A rule is name, effect (allow, ask or deny), and match/except on action,
    group, resource, subresource, namespace, name, labels, flags, connector
    or agent -- each a list of globs. With --network: host, port, action and
    agent.

    Example:
        corerun agents policy-set sre-bot -f rules.yaml
        corerun agents policy-set sre-bot -f network.yaml --network
    """
    _init_client()
    import corerun.agents as agents

    if bool(file) == default:
        raise output.fail("Give a rules file with -f, or --default.")
    rules = [] if default else _read_rules(file)
    resolved = _resolve(agent, workspace)
    try:
        kind = "network" if network else "tools"
        answer = agents.set_policy(resolved, rules, workspace=workspace, kind=kind)
    except Exception as e:
        raise _fail(e)
    if answer.get("default") or not rules:
        console.print(
            f"{agent}: no network rules of its own (the organisation's and workspace's apply)"
            if network else f"{agent}: the default policy (reads only)"
        )
    else:
        console.print(f"{agent}: {len(answer.get('rules') or rules)} rule(s)")


@app.command("network")
def network_refused(
    agent: str = typer.Argument(..., help="Agent name or ID"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    Where an agent was refused on the network in the last seven days.

    Each is a host and port the egress proxy refused (or, for an agent still
    reporting, would have). Allow one with `corerun agents network-allow`.

    Example:
        corerun agents network support-bot
    """
    _init_client()
    _json(json_output)
    import corerun.agents as agents

    resolved = _resolve(agent, workspace)
    try:
        a = agents.get(resolved, workspace=workspace)
        found = agents.network_refused(resolved, workspace=workspace)
    except Exception as e:
        raise _fail(e)

    def render():
        mode = "enforced" if a.network == "enforce" else "reported, not enforced"
        console.print(f"{a.name}: network rules {mode}")
        if not found:
            console.print("Nothing refused in the last seven days.")
            return
        table = Table(title="Refused recently")
        table.add_column("Destination")
        table.add_column("Times", justify="right")
        table.add_column("Last")
        for d in found:
            dest = f"{d['host']}:{d['port']}" + (" [dim](would be)[/dim]" if d.get("would") else "")
            last = str(d.get("last", ""))[:19].replace("T", " ")
            table.add_row(dest, str(d.get("times", "")), last)
        console.print(table)

    output.emit(found, render)


@app.command("activity")
def activity(
    agent: str = typer.Argument(..., help="Agent name or ID"),
    since: str = typer.Option("24h", "--since", help="24h, 7d or 30d"),
    person: Optional[str] = typer.Option(
        None, "--person", help="One person's use of it: their account id"
    ),
    timeline: bool = typer.Option(
        False, "--timeline", help="Every event, newest first, instead of by destination"
    ),
    category: Optional[List[str]] = typer.Option(
        None,
        "--category",
        "-c",
        help="With --timeline: agent_network, agent_tools, agent_approvals, agent_files, "
        "agent_sandboxes or agent_changes (repeatable)",
    ),
    csv_file: Optional[str] = typer.Option(None, "--csv", help="Write it to this CSV file instead"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    What an agent did, for whoever manages it: where it connected, by
    destination with the traffic and the rule that decided -- or, with
    --timeline, its connections, tools, approvals, files, sandboxes and the
    changes made to it. No conversation text is recorded.

    Example:
        corerun agents activity support-bot --since 7d
        corerun agents activity support-bot --timeline -c agent_tools -c agent_changes
    """
    _init_client()
    _json(json_output)
    import corerun.agents as agents

    resolved = _resolve(agent, workspace)
    view = "timeline" if timeline else "network"
    try:
        if csv_file:
            text = agents.activity_csv(
                resolved,
                view=view,
                since=since,
                person=person,
                category=category,
                workspace=workspace,
            )
            with open(csv_file, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            console.print(f"Wrote {max(text.count(chr(10)) - 1, 0)} rows to {csv_file}")
            return
        if timeline:
            found = agents.activity_timeline(
                resolved, since=since, person=person, category=category, workspace=workspace
            )
        else:
            found = agents.activity(resolved, since=since, person=person, workspace=workspace)
    except Exception as e:
        raise _fail(e)

    def size(n) -> str:
        n = float(n or 0)
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024 or unit == "GB":
                return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
            n /= 1024
        return ""

    def render_network():
        s = found.get("summary") or {}
        console.print(
            f"{s.get('connections', 0)} connections, {s.get('refused', 0)} refused, "
            f"{s.get('asked', 0)} asked; "
            f"{size(s.get('bytes_out'))} sent, {size(s.get('bytes_in'))} received"
        )
        rows = found.get("destinations") or []
        if not rows:
            console.print("No connections in this period.")
            return
        table = Table()
        for col in ("Destination", "Decisions", "Decided by", "Sent", "Received", "Last"):
            table.add_column(col, justify="right" if col in ("Sent", "Received") else "left")
        for d in rows:
            decisions = ", ".join(
                f"{label} {d[k]}"
                for k, label in (
                    ("reached", "reached"),
                    ("refused", "refused"),
                    ("would_refuse", "would refuse"),
                    ("asked", "asked"),
                    ("approved", "approved"),
                    ("refused_by_person", "refused by a person"),
                )
                if d.get(k)
            )
            dest = f"{d['host']}:{d['port']}"
            if d.get("workaround"):
                dest += " [yellow](looks like a workaround)[/yellow]"
            if d.get("sent_more"):
                dest += " [yellow](sent more than received)[/yellow]"
            table.add_row(
                dest,
                decisions,
                d.get("decided_by") or "-",
                size(d.get("bytes_out")),
                size(d.get("bytes_in")),
                str(d.get("last", ""))[:19].replace("T", " "),
            )
        console.print(table)

    def render_timeline():
        events = found.get("events") or []
        if not events:
            console.print("Nothing recorded in this period.")
            return
        table = Table()
        for col in ("When", "Who", "What", "Outcome", "Detail"):
            table.add_column(col)
        for ev in events:
            target = str(ev.get("target") or "")
            if target.startswith("agents/"):
                target = target.split("/", 2)[-1]
            table.add_row(
                str(ev.get("at", ""))[:19].replace("T", " "),
                ev.get("actor_email") or ev.get("actor_id") or "-",
                f"{ev.get('action', '')}\n[dim]{target}[/dim]",
                ev.get("outcome", ""),
                ev.get("reason") or "",
            )
        console.print(table)
        if found.get("next_before"):
            console.print(
                "[dim]More: narrow with --since/--category, or write them all with --csv.[/dim]"
            )

    output.emit(found, render_timeline if timeline else render_network)


@app.command("network-unmatched")
def network_unmatched(
    agent: str = typer.Argument(..., help="Agent name or ID"),
    effect: Optional[str] = typer.Argument(
        None, help="deny, ask or allow; none to leave it to the levels above. Omit to show"
    ),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    What a destination none of an agent's network rules match gets.

    deny is an allowlist, ask puts it to the person in the conversation, allow
    is a blocklist. The organisation's and workspace's say counts too: the
    strictest said wins, and nothing said is deny.

    Example:
        corerun agents network-unmatched support-bot
        corerun agents network-unmatched support-bot ask
    """
    _init_client()
    _json(json_output)
    import corerun.agents as agents

    resolved = _resolve(agent, workspace)
    try:
        if effect is None:
            answer = agents.network_unmatched(resolved, workspace=workspace)
        else:
            value = "" if effect == "none" else effect
            answer = agents.set_network_unmatched(resolved, value, workspace=workspace)
    except ValueError as e:
        raise output.fail(str(e))
    except Exception as e:
        raise _fail(e)

    def render():
        levels = answer.get("levels") or {}
        said = ", ".join(f"{k} {v}" for k, v in levels.items() if v) or "nothing"
        effective = answer.get("effective")
        console.print(f"{agent}: a destination no rule matches is {effective} (said: {said})")

    output.emit(answer, render)


@app.command("network-allow")
def network_allow(
    agent: str = typer.Argument(..., help="Agent name or ID"),
    destination: str = typer.Argument(
        ..., help="host or host:port, e.g. pypi.org or api.example.com:8443"
    ),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Allow an agent one destination: a rule added to its own network rules.

    The port defaults to 443. The organisation's and workspace's deny rules
    still win, and the platform's floor (private and metadata addresses) is
    never lifted.

    Example:
        corerun agents network-allow support-bot pypi.org
    """
    host, _, port = destination.strip().lower().partition(":")
    if not host:
        raise output.fail("Name a host, e.g. pypi.org or api.example.com:8443.")
    _init_client()
    import corerun.agents as agents

    resolved = _resolve(agent, workspace)
    try:
        agents.allow_network(resolved, host, port or "443", workspace=workspace)
    except Exception as e:
        raise _fail(e)
    console.print(f"{agent}: [green]allowed[/green] {host}:{port or '443'}")


@app.command("asks")
def list_asks(
    waiting: bool = typer.Option(False, "--waiting", help="Only the ones still waiting for you"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    What your agents asked you before going ahead, waiting and recently
    answered. Answer them in the console's Approvals page or the chat.

    Example:
        corerun agents asks --waiting
    """
    _init_client()
    _json(json_output)
    import corerun.agents as agents

    try:
        found = agents.asks(pending=waiting, workspace=workspace)
    except Exception as e:
        raise _fail(e)

    def render():
        if not found:
            console.print("Nothing is waiting for you." if waiting else "No asks in the last seven days.")
            return
        for a in found:
            console.print(f"  [{a.get('status', '')}] {a.get('title', '')}  [dim]{a.get('agent_id', '')[:8]} · {a.get('created_at', '')[:19]}[/dim]")

    output.emit(found, render)


@app.command("sandboxes")
def list_sandboxes(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    Where this workspace's agents run: your own sandboxes, or everyone's and
    the ready ones for its administrators.

    Example:
        corerun agents sandboxes
    """
    _init_client()
    _json(json_output)
    import corerun.agents as agents

    try:
        found = agents.sandboxes(workspace=workspace)
    except Exception as e:
        raise _fail(e)

    def render():
        boxes = found.get("sandboxes") or []
        if not boxes:
            console.print("No sandboxes are running.")
            return
        for b in boxes:
            who = "ready" if b.get("ready") else (b.get("owner_email") or b.get("owner_name") or "")
            console.print(
                f"  {b.get('agent_name', '')}  {who}  {b.get('status', '')}  {b.get('compute', '')}  "
                f"[dim]{b.get('notebook_id', '')}[/dim]"
            )
        console.print(f"[dim]{found.get('running', 0)} in use, {found.get('ready', 0)} ready[/dim]")

    output.emit(found, render)


@app.command("sandbox-stop")
def stop_sandbox(
    sandbox: str = typer.Argument(..., help="The sandbox's id, from 'corerun agents sandboxes'"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Stop an agent sandbox: your own, or -- for a workspace administrator --
    anyone's, which is recorded. Its person's files and history stay.

    Example:
        corerun agents sandbox-stop 6f1aa7e5-f1e1-42d8-a7d3-26e31f422413
    """
    _init_client()
    import corerun.agents as agents

    try:
        agents.stop_sandbox(sandbox, workspace=workspace)
    except Exception as e:
        raise _fail(e)
    console.print(f"[green]Stopped[/green] {sandbox}")


@app.command("consents")
def list_consents(
    agent: str = typer.Argument(..., help="Agent name or ID"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    The tools you told an agent it may always use without asking.

    Example:
        corerun agents consents support-bot
    """
    _init_client()
    _json(json_output)
    import corerun.agents as agents

    resolved = _resolve(agent, workspace)
    try:
        found = agents.consents(resolved, workspace=workspace)
    except Exception as e:
        raise _fail(e)

    def render():
        if not found:
            console.print("None: every tool set to ask still asks you.")
            return
        for c in found:
            console.print(f"  {c.get('connector') or c.get('connector_id', '')}: {c.get('tool', '')}  [dim]{c.get('consent_id', '')}[/dim]")

    output.emit(found, render)


@app.command("consent-revoke")
def revoke_consent(
    agent: str = typer.Argument(..., help="Agent name or ID"),
    consent: str = typer.Argument(..., help="The consent's id, or its tool's name"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Make an agent ask again before using a tool you had always allowed.

    Example:
        corerun agents consent-revoke support-bot create_issue
    """
    _init_client()
    import corerun.agents as agents

    resolved = _resolve(agent, workspace)
    try:
        found = agents.consents(resolved, workspace=workspace)
        matches = [c for c in found if consent in (c.get("consent_id"), c.get("tool"))]
        if not matches:
            raise output.fail(f"No consent '{consent}' (corerun agents consents {agent}).")
        if len(matches) > 1:
            raise output.fail(f"'{consent}' matches several; use an id: " + ", ".join(c["consent_id"] for c in matches))
        agents.revoke_consent(resolved, matches[0]["consent_id"], workspace=workspace)
    except typer.Exit:
        raise
    except Exception as e:
        raise _fail(e)
    console.print(f"{agent} will ask again before {matches[0].get('tool', consent)}")


# ── talking to it ───────────────────────────────────────────────────────────


@app.command("chat")
def chat(
    agent: str = typer.Argument(..., help="Agent name or ID"),
    message: str = typer.Argument(..., help="What to ask"),
    conversation: Optional[str] = typer.Option(None, "--conversation", "-c", help="Continue this conversation"),
    project: Optional[str] = typer.Option(None, "--project", help="Start the conversation in this project"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Ask an agent something, and print its answer as it is written.

    Answered from the agent's model. A question that needs its sandbox and
    tools is handed over: open the agent in the console to carry on.

    Example:
        corerun agents chat support-bot "What do we do when a customer asks for a refund?"
    """
    _init_client()
    import corerun.agents as agents

    resolved = _resolve(agent, workspace)
    try:
        if not conversation:
            conversation = agents.new_conversation(resolved, project_id=project, workspace=workspace)["conversation_id"]
        handed_over = False
        for event in agents.reply(resolved, conversation, message, workspace=workspace):
            if "delta" in event:
                typer.echo(event["delta"], nl=False)
            if event.get("handover"):
                handed_over = True
            if event.get("done") or handed_over:
                break
    except typer.Exit:
        raise
    except Exception as e:
        raise _fail(e)

    typer.echo("")
    if handed_over:
        url = _console_url(resolved)
        output.errors.print(
            "This needs the agent's sandbox and tools, which run in the console"
            + (f": {url}" if url else ".")
            + f" The conversation is {conversation}."
        )
    else:
        output.errors.print(f"[dim]conversation {conversation} -- continue with --conversation {conversation}[/dim]")


@app.command("conversations")
def list_conversations(
    agent: str = typer.Argument(..., help="Agent name or ID"),
    user: Optional[str] = typer.Option(None, "--user", help="One person's (the agent's managers only)"),
    project: Optional[str] = typer.Option(None, "--project", help="Only this project's"),
    query: Optional[str] = typer.Option(None, "--search", "-q", help="Containing this text"),
    archived: bool = typer.Option(False, "--archived", help="Archived ones"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    Your conversations with an agent -- everyone's, if you manage it.

    Example:
        corerun agents conversations support-bot
    """
    _init_client()
    _json(json_output)
    import corerun.agents as agents

    resolved = _resolve(agent, workspace)
    try:
        answer = agents.conversations(resolved, user=user, project=project, query=query, archived=archived, workspace=workspace)
    except Exception as e:
        raise _fail(e)
    found = answer.get("conversations") or []

    def render():
        if not found:
            console.print("No conversations.")
            return
        table = Table(title="Everyone's conversations" if answer.get("all") else "Your conversations")
        table.add_column("Title")
        table.add_column("ID")
        if answer.get("all"):
            table.add_column("Person")
        table.add_column("Project")
        table.add_column("Messages", justify="right")
        table.add_column("Updated")
        for c in found:
            row = [c.get("title") or "(untitled)", c.get("conversation_id", "")]
            if answer.get("all"):
                row.append(c.get("user_email") or c.get("user_name") or "")
            row += [c.get("project_id", ""), str(c.get("messages", 0)), c.get("updated_at", "")]
            table.add_row(*row)
        console.print(table)

    output.emit(answer, render)


@app.command("conversation")
def show_conversation(
    agent: str = typer.Argument(..., help="Agent name or ID"),
    conversation: str = typer.Argument(..., help="Conversation id"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    Read a conversation with an agent.

    Example:
        corerun agents conversation support-bot 5d1f7c2a-...
    """
    _init_client()
    _json(json_output)
    import corerun.agents as agents

    resolved = _resolve(agent, workspace)
    try:
        c = agents.get_conversation(resolved, conversation, workspace=workspace)
    except Exception as e:
        raise _fail(e)

    def render():
        console.print(f"[bold]{c.get('title') or '(untitled)'}[/bold]  [dim]{c.get('conversation_id', '')}[/dim]")
        for m in c.get("messages") or []:
            who = "You" if m.get("role") == "user" else "Agent"
            console.print(f"\n[bold]{who}[/bold] [dim]{m.get('at', '')}[/dim]")
            for t in m.get("tools") or []:
                console.print(f"  [dim]· {t.get('title', '')} {t.get('status', '')}[/dim]")
            console.print(m.get("text", ""), markup=False, highlight=False)
            for f in m.get("files") or []:
                console.print(f"  [dim]file: {f}[/dim]")

    output.emit(c, render)


@app.command("conversation-delete")
def delete_conversation(
    agent: str = typer.Argument(..., help="Agent name or ID"),
    conversation: str = typer.Argument(..., help="Conversation id"),
    force: bool = typer.Option(False, "--yes", "-y", help="Do not ask for confirmation"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Delete one of your conversations with an agent.

    Example:
        corerun agents conversation-delete support-bot 5d1f7c2a-... --yes
    """
    _init_client()
    import corerun.agents as agents

    resolved = _resolve(agent, workspace)
    if not force:
        output.confirm(f"Delete conversation {conversation}?")
    try:
        agents.delete_conversation(resolved, conversation, workspace=workspace)
    except Exception as e:
        raise _fail(e)
    console.print(f"[green]Deleted[/green] conversation {conversation}")


# ── projects and their files ────────────────────────────────────────────────


def _project_id(agent_id: str, reference: str, workspace: Optional[str]) -> str:
    """A project by id or name; "general" and "shared" are ids already."""
    import corerun.agents as agents

    if reference in ("general", "shared"):
        return reference
    found = agents.projects(agent_id, workspace=workspace).get("projects") or []
    for p in found:
        if reference in (p.get("project_id"), p.get("name")):
            return p["project_id"]
    return reference


@app.command("projects")
def list_projects(
    agent: str = typer.Argument(..., help="Agent name or ID"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    Your projects with an agent: files and instructions its conversations share.

    Example:
        corerun agents projects support-bot
    """
    _init_client()
    _json(json_output)
    import corerun.agents as agents

    resolved = _resolve(agent, workspace)
    try:
        answer = agents.projects(resolved, workspace=workspace)
    except Exception as e:
        raise _fail(e)

    def render():
        table = Table(title="Projects")
        table.add_column("Name")
        table.add_column("ID")
        table.add_column("Updated")
        for p in answer.get("projects") or []:
            table.add_row(p.get("name", ""), p.get("project_id", ""), p.get("updated_at", ""))
        console.print(table)

    output.emit(answer, render)


@app.command("project-create")
def create_project(
    agent: str = typer.Argument(..., help="Agent name or ID"),
    name: str = typer.Argument(..., help="The project's name"),
    instructions: Optional[str] = typer.Option(None, "--instructions", "-i", help="What the agent should know in this project"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Start a project with an agent.

    Example:
        corerun agents project-create support-bot "Q3 refunds"
    """
    _init_client()
    import corerun.agents as agents

    resolved = _resolve(agent, workspace)
    try:
        p = agents.create_project(resolved, name, instructions=instructions, workspace=workspace)
    except Exception as e:
        raise _fail(e)
    console.print(f"[green]Created[/green] project {p.get('name', name)} [dim]{p.get('project_id', '')}[/dim]")


@app.command("project-delete")
def delete_project(
    agent: str = typer.Argument(..., help="Agent name or ID"),
    project: str = typer.Argument(..., help="Project name or id"),
    force: bool = typer.Option(False, "--yes", "-y", help="Do not ask for confirmation"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Delete a project and its files. "general" and "shared" stay.

    Example:
        corerun agents project-delete support-bot "Q3 refunds" --yes
    """
    _init_client()
    import corerun.agents as agents

    resolved = _resolve(agent, workspace)
    try:
        pid = _project_id(resolved, project, workspace)
        if not force:
            output.confirm(f"Delete project {project} and its files?")
        agents.delete_project(resolved, pid, workspace=workspace)
    except typer.Exit:
        raise
    except Exception as e:
        raise _fail(e)
    console.print(f"[green]Deleted[/green] project {project}")


@app.command("files")
def list_files(
    agent: str = typer.Argument(..., help="Agent name or ID"),
    project: str = typer.Option("general", "--project", "-p", help="Project name or id; 'shared' for the agent's own"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    The files in one of your projects with an agent.

    Example:
        corerun agents files support-bot --project "Q3 refunds"
    """
    _init_client()
    _json(json_output)
    import corerun.agents as agents

    resolved = _resolve(agent, workspace)
    try:
        found = agents.sources(resolved, _project_id(resolved, project, workspace), workspace=workspace)
    except Exception as e:
        raise _fail(e)

    def render():
        if not found:
            console.print(f"No files in {project}. Add one: corerun agents upload {agent} <file> --project {project}")
            return
        table = Table(title=f"Files: {project}")
        table.add_column("Path")
        table.add_column("Size", justify="right")
        table.add_column("Text")
        table.add_column("Modified")
        for s in found:
            size = "" if s.get("kind") == "folder" else str(s.get("size") or 0)
            table.add_row(s.get("path", "") + ("/" if s.get("kind") == "folder" else ""), size,
                          (s.get("text") or {}).get("status", ""), s.get("modified", ""))
        console.print(table)

    output.emit(found, render)


@app.command("upload")
def upload(
    agent: str = typer.Argument(..., help="Agent name or ID"),
    local: Path = typer.Argument(..., exists=True, dir_okay=False, help="The file to upload"),
    project: str = typer.Option("general", "--project", "-p", help="Project name or id; 'shared' for the agent's own"),
    as_path: Optional[str] = typer.Option(None, "--as", help="Its path in the project (default: its file name)"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Put a file in one of your projects with an agent (50 MB at most).

    Example:
        corerun agents upload support-bot refund-policy.pdf --project "Q3 refunds"
    """
    import mimetypes

    _init_client()
    import corerun.agents as agents

    resolved = _resolve(agent, workspace)
    target = as_path or local.name
    content_type = mimetypes.guess_type(local.name)[0] or "application/octet-stream"
    try:
        agents.put_file(resolved, _project_id(resolved, project, workspace), target, local.read_bytes(),
                        content_type=content_type, workspace=workspace)
    except Exception as e:
        raise _fail(e)
    console.print(f"[green]Uploaded[/green] {local} to {project}/{target}")


@app.command("download")
def download(
    agent: str = typer.Argument(..., help="Agent name or ID"),
    path: str = typer.Argument(..., help="The file's path in the project"),
    project: str = typer.Option("general", "--project", "-p", help="Project name or id"),
    dest: Optional[Path] = typer.Option(None, "--output", "-o", help="Where to write it (default: its file name, here)"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Download a file from one of your projects with an agent.

    Example:
        corerun agents download support-bot outputs/report.md -o report.md
    """
    _init_client()
    import corerun.agents as agents

    resolved = _resolve(agent, workspace)
    target = str(dest or Path(path).name)
    try:
        written = agents.get_file(resolved, _project_id(resolved, project, workspace), path, target, workspace=workspace)
    except Exception as e:
        raise _fail(e)
    console.print(f"[green]Downloaded[/green] {path} to {target} ({written} bytes)")


@app.command("rm-file")
def remove_file(
    agent: str = typer.Argument(..., help="Agent name or ID"),
    path: str = typer.Argument(..., help="The file's or folder's path in the project"),
    project: str = typer.Option("general", "--project", "-p", help="Project name or id"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Remove a file, or a folder and everything in it, from a project.

    Example:
        corerun agents rm-file support-bot drafts --project "Q3 refunds"
    """
    _init_client()
    import corerun.agents as agents

    resolved = _resolve(agent, workspace)
    try:
        agents.delete_file(resolved, _project_id(resolved, project, workspace), path, workspace=workspace)
    except Exception as e:
        raise _fail(e)
    console.print(f"[green]Removed[/green] {project}/{path}")


@app.command("search")
def search(
    agent: str = typer.Argument(..., help="Agent name or ID"),
    query: str = typer.Argument(..., help="What to look for"),
    project: str = typer.Option("general", "--project", "-p", help="Project name or id"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    Search a project's files, and the agent's shared ones, as the agent would.

    Example:
        corerun agents search support-bot "refund window"
    """
    _init_client()
    _json(json_output)
    import corerun.agents as agents

    resolved = _resolve(agent, workspace)
    try:
        found = agents.search(resolved, query, _project_id(resolved, project, workspace), workspace=workspace)
    except Exception as e:
        raise _fail(e)

    def render():
        if not found:
            console.print("Nothing found.")
            return
        for p in found:
            where = p.get("file", "") + (f" p.{p['page']}" if p.get("page") else "")
            console.print(f"[bold]{where}[/bold]")
            console.print(p.get("text", ""), markup=False, highlight=False)
            console.print("")

    output.emit(found, render)
