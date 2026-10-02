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
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    Change an agent. Anything not given stays as it is.

    Example:
        corerun agents update support-bot --instructions-file prompt.md --ready 1
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
        answer = agents.policy(resolved, workspace=workspace)
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
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Replace an agent's own policy rules.

    A rule is name, effect (allow, ask or deny), and match/except on action,
    group, resource, subresource, namespace, name, labels, flags, connector
    or agent -- each a list of globs.

    Example:
        corerun agents policy-set sre-bot -f rules.yaml
    """
    _init_client()
    import corerun.agents as agents

    if bool(file) == default:
        raise output.fail("Give a rules file with -f, or --default.")
    rules = [] if default else _read_rules(file)
    resolved = _resolve(agent, workspace)
    try:
        answer = agents.set_policy(resolved, rules, workspace=workspace)
    except Exception as e:
        raise _fail(e)
    if answer.get("default") or not rules:
        console.print(f"{agent}: the default policy (reads only)")
    else:
        console.print(f"{agent}: {len(answer.get('rules') or rules)} rule(s)")


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
