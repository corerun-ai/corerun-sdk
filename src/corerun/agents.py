"""
corerun agents.

An agent is a harness (opencode, goose, ...) with instructions, tools and a
policy, run in a sandbox of its own for each person who talks to it. What it is
given and who may use it is the creator's; conversations, projects and the
files in them are each person's own. Agents live in a workspace, and only in a
GenAI one: elsewhere every route here answers 404.

The two answers that are not JSON -- a reply, streamed as server-sent events,
and a project's file, which is its bytes -- go through the client's own
transport, so they carry the same credential and workspace as everything else.
"""

import builtins
import json
from typing import Any, Dict, Iterator, List, Optional
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict

from corerun.config import get_client


class _Model(BaseModel):
    # The platform adds fields as agents grow; an older SDK should read the
    # ones it knows and carry on, not refuse the answer.
    model_config = ConfigDict(extra="allow")


class AgentSession(_Model):
    """The caller's sandbox for an agent."""

    notebook_id: str = ""
    status: str = ""
    progress: Optional[str] = None
    error: Optional[str] = None
    base_path: Optional[str] = None


class Agent(_Model):
    """An agent, as the platform describes it to the caller.

    `instructions` and `shared_with` are blank unless the caller may manage the
    agent; `tool_names` likewise.
    """

    agent_id: str
    name: str
    description: Optional[str] = None
    harness: str = ""
    instructions: Optional[str] = None
    model: Optional[str] = None
    ready: Optional[int] = None
    starters: Optional[List[str]] = None
    sandbox: Optional[Dict[str, str]] = None
    compute_name: str = ""
    cluster_id: Optional[str] = None
    workspace_id: str = ""
    created_by: str = ""
    shared_with: Optional[List[str]] = None
    telemetry: Optional[Dict[str, Any]] = None
    apps: Optional[Dict[str, Any]] = None
    tools: Optional[Dict[str, Any]] = None
    #: "report" (refusals recorded, nothing refused) or "enforce"
    network: Optional[str] = None
    may_manage: bool = False
    creator_name: Optional[str] = None
    creator_email: Optional[str] = None
    session: Optional[AgentSession] = None
    tool_names: Optional[List[str]] = None
    ready_sandboxes: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @property
    def id(self) -> str:
        return self.agent_id


def _path(agent_id: str, rest: str = "") -> str:
    return f"/agents/{quote(agent_id, safe='')}{rest}"


def _guard_path(agent_id: str, rest: str = "") -> str:
    """An agent's route at corerun-agentguard: its rules, its network."""
    return f"/agentguard/agents/{quote(agent_id, safe='')}{rest}"


def _file_path(path: str) -> str:
    # A project path keeps its slashes: the route takes the rest of the URL.
    return quote(path.lstrip("/"), safe="/")


# ── the agent itself ────────────────────────────────────────────────────────


def list(workspace: Optional[str] = None) -> List[Agent]:  # noqa: A001 - mirrors the other modules
    """Every agent the caller may use in the workspace."""
    response = get_client().get("/agents", workspace=workspace)
    return [Agent.model_validate(a) for a in response.get("agents") or []]


def may_build(workspace: Optional[str] = None) -> bool:
    """Whether the caller may create agents in the workspace."""
    return bool(get_client().get("/agents", workspace=workspace).get("may_build"))


def mine() -> List[Dict[str, Any]]:
    """The agents the caller may use, across every workspace they belong to."""
    return get_client().get("/me/agents").get("agents") or []


def get(agent_id: str, workspace: Optional[str] = None) -> Agent:
    return Agent.model_validate(get_client().get(_path(agent_id), workspace=workspace))


def create(
    name: str,
    compute_name: str,
    *,
    description: Optional[str] = None,
    instructions: Optional[str] = None,
    harness: Optional[str] = None,
    starters: Optional[List[str]] = None,
    workspace: Optional[str] = None,
) -> Agent:
    """Create an agent, run on `compute_name`.

    Args:
        harness: what runs it ("opencode" when not given); `harnesses()` lists
            what a compute offers.
        starters: up to four suggested first questions, 140 characters each.
    """
    body: Dict[str, Any] = {"name": name, "compute_name": compute_name}
    if description is not None:
        body["description"] = description
    if instructions is not None:
        body["instructions"] = instructions
    if harness:
        body["harness"] = harness
    if starters is not None:
        body["starters"] = starters
    return Agent.model_validate(get_client().post("/agents", json=body, workspace=workspace))


def update(agent_id: str, workspace: Optional[str] = None, **fields: Any) -> Agent:
    """Change an agent.

    The platform replaces `description` and `instructions` with whatever is
    sent, empty included, so this reads the agent first and sends the current
    values of anything not given. Fields: name, description, instructions,
    harness, starters, ready (0-5 sandboxes kept warm), model ("" for the
    organisation's default), sandbox ({"shell": ..., "web": ...}, each
    "allow", "ask" or "off"), network ("enforce": its network rules are
    enforced from now on; there is no going back to reporting).
    """
    current = get(agent_id, workspace=workspace)
    body: Dict[str, Any] = {
        "name": current.name,
        "description": current.description or "",
        "instructions": current.instructions or "",
        "harness": current.harness,
    }
    body.update({k: v for k, v in fields.items() if v is not None})
    return Agent.model_validate(get_client().put(_path(agent_id), json=body, workspace=workspace))


def delete(agent_id: str, workspace: Optional[str] = None) -> None:
    get_client().delete(_path(agent_id), workspace=workspace)


def harnesses(agent_id: str, workspace: Optional[str] = None) -> Dict[str, Any]:
    """The harnesses the agent's compute can run: {harnesses: [...], compute}."""
    return get_client().get(_path(agent_id, "/harnesses"), workspace=workspace)


# ── who may use it ──────────────────────────────────────────────────────────


def access(agent_id: str, workspace: Optional[str] = None) -> Dict[str, Any]:
    """People, groups, open invitations and whether everyone in the workspace may chat."""
    return get_client().get(_path(agent_id, "/access"), workspace=workspace)


def set_access(
    agent_id: str, kind: str, principal_id: str, level: str, workspace: Optional[str] = None
) -> Dict[str, Any]:
    """Give a user or group ("user" | "group") access "chat" or "edit", or "" to remove it."""
    body = {"kind": kind, "id": principal_id, "access": level}
    return get_client().put(_path(agent_id, "/access"), json=body, workspace=workspace)


def set_everyone(agent_id: str, on: bool, workspace: Optional[str] = None) -> Dict[str, Any]:
    """Let everyone in the workspace chat with the agent, or stop."""
    return get_client().put(_path(agent_id, "/everyone"), json={"on": on}, workspace=workspace)


def share(agent_id: str, user_id: str, workspace: Optional[str] = None) -> Agent:
    body = {"user_id": user_id}
    return Agent.model_validate(get_client().post(_path(agent_id, "/share"), json=body, workspace=workspace))


def unshare(agent_id: str, user_id: str, workspace: Optional[str] = None) -> Agent:
    return Agent.model_validate(
        get_client().delete(_path(agent_id, f"/share/{quote(user_id, safe='')}"), workspace=workspace)
    )


def invite(agent_id: str, email: str, workspace: Optional[str] = None) -> Dict[str, Any]:
    """Share the agent with someone by address: {status: "shared"|"invited", email, agent}.

    Somebody already in the workspace is shared with at once; anybody else is
    sent an invitation to it.
    """
    return get_client().post(_path(agent_id, "/invite"), json={"email": email}, workspace=workspace)


def invites(agent_id: str, workspace: Optional[str] = None) -> List[Dict[str, Any]]:
    return get_client().get(_path(agent_id, "/invites"), workspace=workspace).get("invites") or []


def cancel_invite(agent_id: str, invite_id: str, workspace: Optional[str] = None) -> None:
    get_client().delete(_path(agent_id, f"/invites/{quote(invite_id, safe='')}"), workspace=workspace)


def resend_invite(agent_id: str, invite_id: str, workspace: Optional[str] = None) -> Dict[str, Any]:
    return get_client().post(
        _path(agent_id, f"/invites/{quote(invite_id, safe='')}/resend"), json={}, workspace=workspace
    )


def publication(agent_id: str, workspace: Optional[str] = None) -> Dict[str, Any]:
    """Whether an agent is published, and as whom it answers with nobody signed in."""
    return get_client().get(_path(agent_id, "/publication"), workspace=workspace)


def publish(
    agent_id: str,
    mode: Optional[str] = None,
    delegate_id: Optional[str] = None,
    enabled: Optional[bool] = None,
    workspace: Optional[str] = None,
) -> Dict[str, Any]:
    """Publish an agent, or change how: ``mode`` "service" answers as itself,
    "delegate" as a person -- the caller, or ``delegate_id``, who then says so
    from their own session (``confirm_publication``). ``enabled=False``
    keeps the setting and answers nobody."""
    body: Dict[str, Any] = {}
    if mode is not None:
        body["mode"] = mode
    if delegate_id:
        body["delegate_id"] = delegate_id
    if enabled is not None:
        body["enabled"] = enabled
    return get_client().put(_path(agent_id, "/publication"), json=body, workspace=workspace)


def unpublish(agent_id: str, workspace: Optional[str] = None) -> None:
    """Stop an agent answering with nobody signed in; its own account goes."""
    get_client().delete(_path(agent_id, "/publication"), workspace=workspace)


def confirm_publication(agent_id: str, workspace: Optional[str] = None) -> Dict[str, Any]:
    """Say an agent published as you may act as you. Only from your own
    session: a key or a token is refused."""
    return get_client().post(_path(agent_id, "/publication/confirm"), workspace=workspace)


def set_telemetry(agent_id: str, enabled: bool, workspace: Optional[str] = None) -> Agent:
    """Trace every turn into an experiment of the agent's own, or stop."""
    body = {"enabled": enabled}
    return Agent.model_validate(get_client().put(_path(agent_id, "/telemetry"), json=body, workspace=workspace))


# ── tools and policy ────────────────────────────────────────────────────────


def tools(agent_id: str, workspace: Optional[str] = None) -> List[Dict[str, Any]]:
    """Each connector the workspace has, whether the agent is given it, and its per-tool policy."""
    return get_client().get(_path(agent_id, "/tools"), workspace=workspace).get("connectors") or []


def set_tools(
    agent_id: str, connectors: List[Dict[str, Any]], workspace: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Give the agent exactly these connectors.

    Each is {"connector_id": ..., "policies": {tool: "allow"|"ask"|"off"}}; the
    list replaces the agent's, so pass every connector it should keep.
    """
    body = {"connectors": connectors}
    return get_client().put(_path(agent_id, "/tools"), json=body, workspace=workspace).get("connectors") or []


def policy(agent_id: str, workspace: Optional[str] = None, kind: str = "tools") -> Dict[str, Any]:
    """The agent's own policy rules of a kind (tools, network): {rules: [...], default: bool}."""
    return get_client().get(_guard_path(agent_id, "/policy"), params={"kind": kind}, workspace=workspace)


def set_policy(
    agent_id: str, rules: List[Dict[str, Any]], workspace: Optional[str] = None, kind: str = "tools"
) -> Dict[str, Any]:
    """
    Replace the agent's policy rules of a kind. An empty list returns it to the
    default: reading only, for tools; the organisation's and workspace's rules
    only, for the network.
    """
    return get_client().put(
        _guard_path(agent_id, "/policy"), params={"kind": kind}, json={"rules": rules}, workspace=workspace
    )


UNMATCHED = ("deny", "ask", "allow")


def network_unmatched(agent_id: str, workspace: Optional[str] = None) -> Dict[str, Any]:
    """
    What a destination none of the agent's network rules match gets:
    ``{effect, effective, levels}`` -- its own say (``""`` for none), what
    applies, and what the organisation, workspace and agent each said. The
    strictest said wins; nothing said is ``deny`` (an allowlist).
    """
    return get_client().get(_guard_path(agent_id, "/network/unmatched"), workspace=workspace)


def set_network_unmatched(
    agent_id: str, effect: str, workspace: Optional[str] = None
) -> Dict[str, Any]:
    """
    Set the agent's say: ``deny`` (an allowlist), ``ask`` (put to the person),
    ``allow`` (a blocklist), or ``""`` to leave it to the levels above.
    """
    if effect not in UNMATCHED and effect != "":
        raise ValueError(f"effect is one of {', '.join(UNMATCHED)}, or '' for none")
    return get_client().put(
        _guard_path(agent_id, "/network/unmatched"), json={"effect": effect}, workspace=workspace
    )


def network_refused(agent_id: str, workspace: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Where the agent was refused on the network in the last seven days, most
    recent first: ``[{host, port, times, last, would?}]``. ``would`` marks a
    refusal an agent still reporting let through. A destination reached since
    its last refusal is left out. For whoever manages the agent.
    """
    answer = get_client().get(_guard_path(agent_id, "/network/refused"), workspace=workspace) or {}
    return answer.get("destinations") or []


def activity(
    agent_id: str,
    since: str = "24h",
    person: Optional[str] = None,
    workspace: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Where the agent connected in a period, by destination, for whoever
    manages it: ``{summary, destinations, people}``. ``summary`` totals
    connections, refusals, asks and bytes sent and received; each destination
    has its decisions (``reached``, ``refused``, ``asked``, ``approved`` ...),
    its traffic, the rule that decided (``decided_by``) and what looks wrong
    (``workaround``, ``sent_more``). ``since`` is ``24h``, ``7d`` or ``30d``;
    ``person`` an account id from ``people``.
    """
    params: Dict[str, Any] = {"since": since, "view": "network"}
    if person:
        params["person"] = person
    return (
        get_client().get(_guard_path(agent_id, "/activity"), params=params, workspace=workspace)
        or {}
    )


def activity_timeline(
    agent_id: str,
    since: str = "24h",
    person: Optional[str] = None,
    category: Optional[List[str]] = None,
    before: Optional[str] = None,
    workspace: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Everything recorded about the agent and every change made to it, newest
    first: ``{events, next_before, people}``. ``category`` narrows it
    (``agent_network``, ``agent_tools``, ``agent_approvals``, ``agent_files``,
    ``agent_sandboxes``, ``agent_changes``); pass ``next_before`` back as
    ``before`` for the page after.
    """
    params: Dict[str, Any] = {"since": since, "view": "timeline"}
    if person:
        params["person"] = person
    if category:
        params["category"] = ",".join(category)
    if before:
        params["before"] = before
    return (
        get_client().get(_guard_path(agent_id, "/activity"), params=params, workspace=workspace)
        or {}
    )


def activity_csv(
    agent_id: str,
    view: str = "network",
    since: str = "24h",
    person: Optional[str] = None,
    category: Optional[List[str]] = None,
    workspace: Optional[str] = None,
) -> str:
    """The agent's activity (``view`` network or timeline) as CSV text."""
    params: Dict[str, Any] = {"since": since, "view": view, "format": "csv"}
    if person:
        params["person"] = person
    if category and view == "timeline":
        params["category"] = ",".join(category)
    return get_client().get(
        _guard_path(agent_id, "/activity"), params=params, workspace=workspace, response_type="text"
    )


def allow_network(
    agent_id: str, host: str, port: str = "443", workspace: Optional[str] = None
) -> Dict[str, Any]:
    """
    Allow the agent one destination in its own network rules, as the console's
    Allow does. A rule that names that very host is changed rather than joined
    by another: one asking about or refusing it gives the host up (the
    strictest rule wins, so an allow beside it would change nothing), and one
    allowing it already is enough. Refused, with the rule named, when a
    pattern of the agent's own (``*.github.com``) would still win.
    """
    own = policy(agent_id, workspace=workspace, kind="network")
    rules = [] if own.get("default") else builtins.list(own.get("rules") or [])
    host, port = host.lower(), str(port)
    kept, allowed, changed = [], False, False
    for r in rules:
        m = r.get("match") or {}
        hosts = m.get("host") or []
        plain = not r.get("except") and set(m) <= {"host", "port"}
        names_it = host in [h.lower() for h in hosts] and _port_taken(m.get("port"), port)
        if not plain or not names_it:
            kept.append(r)
            continue
        if r.get("effect") == "allow":
            allowed = True
            kept.append(r)
            continue
        changed = True
        rest = [h for h in hosts if h.lower() != host]
        if rest:
            kept.append({**r, "match": {**m, "host": rest}})
    if not allowed:
        kept.append({"name": host, "effect": "allow", "match": {"host": [host], "port": [port]}})
        changed = True
    for r in kept:
        m = r.get("match") or {}
        if r.get("effect") != "allow" and any(
            _pattern_matches(h, host) for h in (m.get("host") or ["*"])
        ) and _port_taken(m.get("port"), port):
            verb = "refuses" if r.get("effect") == "deny" else "asks about"
            name = r.get("name")
            raise ValueError(f"the agent's rule {name!r} still {verb} {host}; change that rule")
    if not changed:
        return own
    return set_policy(agent_id, kept, workspace=workspace, kind="network")


def _pattern_matches(pattern: str, value: str) -> bool:
    """A pattern as the egress proxy reads it: * is anything, dots included."""
    import re

    parts = [re.escape(p) for p in pattern.lower().split("*")]
    return re.fullmatch(".*".join(parts), value.lower()) is not None


def _port_taken(ports: Optional[List[str]], port: str) -> bool:
    return not ports or any(_pattern_matches(str(p), port) for p in ports)


def consents(agent_id: str, workspace: Optional[str] = None) -> List[Dict[str, Any]]:
    """The caller's "always allow" answers to this agent's tools."""
    return get_client().get(_path(agent_id, "/tool-consents"), workspace=workspace).get("consents") or []


def revoke_consent(agent_id: str, consent_id: str, workspace: Optional[str] = None) -> None:
    get_client().delete(_path(agent_id, f"/tool-consents/{quote(consent_id, safe='')}"), workspace=workspace)


# ── the caller's sandbox ────────────────────────────────────────────────────


def workspace_activity(
    since: str = "24h",
    person: Optional[str] = None,
    agent_id: Optional[str] = None,
    timeline: bool = False,
    workspace: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Every agent's activity in the workspace, for its administrators: as
    ``activity`` (by destination, each with its ``agent_id``) or, with
    ``timeline``, as ``activity_timeline``. ``agent_id`` narrows it to one.
    """
    params: Dict[str, Any] = {"since": since, "view": "timeline" if timeline else "network"}
    if person:
        params["person"] = person
    if agent_id:
        params["agent"] = agent_id
    return get_client().get("/agentguard/activity", params=params, workspace=workspace) or {}


def asks(pending: bool = False, workspace: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    What your agents asked you in this workspace: waiting (``status``
    pending, until ``expires_at``) and answered in the last seven days. Each
    has its agent, kind (mcp, kubernetes, network), title and what an
    approval of it names. Answered from the console's Approvals page or the
    chat, with your own session.
    """
    params = {"status": "pending"} if pending else {}
    answer = get_client().get("/agentguard/asks", params=params, workspace=workspace) or {}
    return answer.get("asks") or []


def sandboxes(workspace: Optional[str] = None) -> Dict[str, Any]:
    """
    The workspace's agent sandboxes: ``{sandboxes, running, ready, all}`` --
    your own, or everyone's (``all``) for its administrators, with the ready
    ones waiting for whoever opens an agent next.
    """
    return get_client().get("/agent-sandboxes", workspace=workspace) or {}


def stop_sandbox(sandbox_id: str, workspace: Optional[str] = None) -> None:
    """Stop an agent sandbox: your own, or anyone's for a workspace administrator (recorded)."""
    get_client().delete(f"/agent-sandboxes/{sandbox_id}", workspace=workspace)


def start_session(agent_id: str, workspace: Optional[str] = None) -> Optional[AgentSession]:
    """Start or wake the caller's sandbox for the agent."""
    answer = get_client().post(_path(agent_id, "/session"), json={}, workspace=workspace)
    return AgentSession.model_validate(answer) if answer else None


def stop_session(agent_id: str, workspace: Optional[str] = None) -> Optional[AgentSession]:
    answer = get_client().delete(_path(agent_id, "/session"), workspace=workspace)
    return AgentSession.model_validate(answer) if answer else None


# ── conversations ───────────────────────────────────────────────────────────


def conversations(
    agent_id: str,
    *,
    user: Optional[str] = None,
    project: Optional[str] = None,
    query: Optional[str] = None,
    archived: bool = False,
    workspace: Optional[str] = None,
) -> Dict[str, Any]:
    """The caller's conversations with the agent -- everyone's, for its managers.

    Returns {conversations: [...], all: bool}; `all` says the list is everyone's.
    """
    params: Dict[str, Any] = {}
    if user:
        params["user"] = user
    if project:
        params["project"] = project
    if query:
        params["q"] = query
    if archived:
        params["archived"] = "1"
    return get_client().get(_path(agent_id, "/conversations"), params=params or None, workspace=workspace)


def get_conversation(agent_id: str, conversation_id: str, workspace: Optional[str] = None) -> Dict[str, Any]:
    return get_client().get(_path(agent_id, f"/conversations/{quote(conversation_id, safe='')}"), workspace=workspace)


def new_conversation(
    agent_id: str, project_id: Optional[str] = None, workspace: Optional[str] = None
) -> Dict[str, Any]:
    body = {"project_id": project_id} if project_id else {}
    return get_client().post(_path(agent_id, "/conversations"), json=body, workspace=workspace)


def update_conversation(
    agent_id: str, conversation_id: str, workspace: Optional[str] = None, **fields: Any
) -> Dict[str, Any]:
    """Rename, pin, archive or move a conversation: title, pinned, archived, project_id."""
    body = {k: v for k, v in fields.items() if v is not None}
    return get_client().patch(
        _path(agent_id, f"/conversations/{quote(conversation_id, safe='')}"), json=body, workspace=workspace
    )


def delete_conversation(agent_id: str, conversation_id: str, workspace: Optional[str] = None) -> None:
    get_client().delete(_path(agent_id, f"/conversations/{quote(conversation_id, safe='')}"), workspace=workspace)


def reply(
    agent_id: str, conversation_id: str, text: str, workspace: Optional[str] = None
) -> Iterator[Dict[str, Any]]:
    """Ask the agent, and read its answer as it is written.

    Yields each event the platform sends: {"delta": text} for each piece of
    the answer, then either {"done": true, "title": ...} -- the answer is
    complete -- or {"handover": true}: the question needs the agent's sandbox
    and tools, which a person continues in the console.
    """
    client = get_client()
    headers = {"Accept": "text/event-stream"}
    if workspace:
        headers["X-Workspace-ID"] = workspace
    path = _path(agent_id, f"/conversations/{quote(conversation_id, safe='')}/reply")
    with client.client.stream("POST", path, json={"text": text}, headers=headers, timeout=None) as response:
        if response.status_code >= 300:
            response.read()
            client._handle_response(response)  # raises the platform's reason
        for event in _events(response.iter_lines()):
            yield event


def _events(lines: Iterator[str]) -> Iterator[Dict[str, Any]]:
    """Server-sent events, as dicts: each `data:` line is one JSON event."""
    for line in lines:
        if not line.startswith("data:"):
            continue
        data = line[len("data:"):].strip()
        if not data:
            continue
        try:
            event = json.loads(data)
        except ValueError:
            continue
        if isinstance(event, dict):
            yield event


# ── projects and their files ────────────────────────────────────────────────


def projects(agent_id: str, user: Optional[str] = None, workspace: Optional[str] = None) -> Dict[str, Any]:
    """{projects: [...], may_write_shared: bool}. "general" always exists; "shared" is the agent's own sources."""
    params = {"user": user} if user else None
    return get_client().get(_path(agent_id, "/projects"), params=params, workspace=workspace)


def create_project(
    agent_id: str,
    name: str,
    *,
    instructions: Optional[str] = None,
    pinned: Optional[bool] = None,
    workspace: Optional[str] = None,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {"name": name}
    if instructions is not None:
        body["instructions"] = instructions
    if pinned is not None:
        body["pinned"] = pinned
    return get_client().post(_path(agent_id, "/projects"), json=body, workspace=workspace)


def update_project(agent_id: str, project_id: str, workspace: Optional[str] = None, **fields: Any) -> Dict[str, Any]:
    body = {k: v for k, v in fields.items() if v is not None}
    return get_client().patch(_path(agent_id, f"/projects/{quote(project_id, safe='')}"), json=body, workspace=workspace)


def delete_project(agent_id: str, project_id: str, workspace: Optional[str] = None) -> None:
    get_client().delete(_path(agent_id, f"/projects/{quote(project_id, safe='')}"), workspace=workspace)


def sources(
    agent_id: str,
    project_id: str = "general",
    *,
    user: Optional[str] = None,
    deleted: bool = False,
    workspace: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """The files and folders in a project."""
    params: Dict[str, Any] = {}
    if user:
        params["user"] = user
    if deleted:
        params["deleted"] = "1"
    answer = get_client().get(
        _path(agent_id, f"/projects/{quote(project_id, safe='')}/sources"), params=params or None, workspace=workspace
    )
    return answer.get("sources") or []


def create_folder(agent_id: str, project_id: str, path: str, workspace: Optional[str] = None) -> Dict[str, Any]:
    return get_client().post(
        _path(agent_id, f"/projects/{quote(project_id, safe='')}/folders"), json={"path": path}, workspace=workspace
    )


def put_file(
    agent_id: str,
    project_id: str,
    path: str,
    content: bytes,
    content_type: str = "application/octet-stream",
    workspace: Optional[str] = None,
) -> Dict[str, Any]:
    """Put a file in a project at `path` (50 MB at most); answers the source it became."""
    return get_client().put(
        _path(agent_id, f"/projects/{quote(project_id, safe='')}/files/{_file_path(path)}"),
        content=content,
        content_type=content_type or "application/octet-stream",
        workspace=workspace,
    )


def get_file(
    agent_id: str, project_id: str, path: str, dest: str, user: Optional[str] = None, workspace: Optional[str] = None
) -> int:
    """Download a project's file to `dest`; returns the bytes written."""
    params = {"user": user} if user else None
    return get_client().download(
        _path(agent_id, f"/projects/{quote(project_id, safe='')}/files/{_file_path(path)}"),
        dest,
        params=params,
        workspace=workspace,
    )


def delete_file(agent_id: str, project_id: str, path: str, workspace: Optional[str] = None) -> None:
    """Remove a file, or a folder and everything in it."""
    get_client().delete(
        _path(agent_id, f"/projects/{quote(project_id, safe='')}/files/{_file_path(path)}"), workspace=workspace
    )


def text(agent_id: str, project_id: str, path: str, user: Optional[str] = None, workspace: Optional[str] = None) -> str:
    """A file's text version: what the agent reads of a PDF or a document."""
    params = {"user": user} if user else None
    return get_client().get(
        _path(agent_id, f"/projects/{quote(project_id, safe='')}/text/{_file_path(path)}"),
        params=params,
        workspace=workspace,
        response_type="text",
    )


def search(
    agent_id: str, query: str, project_id: str = "general", user: Optional[str] = None, workspace: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Passages matching `query` in a project's files and the agent's shared ones."""
    params: Dict[str, Any] = {"q": query}
    if user:
        params["user"] = user
    answer = get_client().get(
        _path(agent_id, f"/projects/{quote(project_id, safe='')}/search"), params=params, workspace=workspace
    )
    return answer.get("passages") or []
