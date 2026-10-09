"""
Custom MCP servers: built from API descriptions, run by the platform.

A server is made from the APIs it calls -- each with its address, where it is
(on the internet, or inside the organisation's network, reached through a
connected cluster or host) and its own credential -- and tools, each one
operation of one API. The platform answers it: a tool's call is made to its
API with that API's credential, attached at the moment of the call, and no
agent ever holds an address or a credential.

What its tools are is versioned. Every change is a draft beside the running
version, called with :func:`call` to test it, then :func:`publish`-ed; an
earlier version comes back with :func:`restore`, as the newest. Once published
a server is an ordinary connector agents can be given.

A server can instead run an MCP server's own image -- one the
:func:`catalogue` offers, or yours -- on a cluster or host of the
organisation's (:func:`create_image`): each version in a workload of its own,
held to the hosts it is given by the egress proxy; its tools are chosen from
what it lists (:func:`live_tools`) and its secrets are its own
(:func:`set_secrets`).
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict

from corerun.config import get_client

AUTH = ("none", "bearer", "basic", "headers", "client_credentials")


class ServerAPI(BaseModel):
    """One API a server's tools call. Never carries its secret."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    base_url: str
    #: "internet", or "network": reached through ``via``, or directly (on-premises)
    location: str = "internet"
    via: Optional[str] = None
    auth: str = "none"
    has_secret: bool = False
    has_spec: bool = False
    spec_url: Optional[str] = None


class Server(BaseModel):
    """A custom server: its APIs, the running version's tools, any draft."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    description: Optional[str] = None
    #: "api": made from API descriptions; "image": an MCP server's image the platform runs
    kind: str = "api"
    #: For an image server: what the draft runs, else the running version
    image: Optional[Dict[str, Any]] = None
    #: For an image server: its secret settings that have a value
    secrets: Optional[List[str]] = None
    running_workload: Optional[Dict[str, Any]] = None
    draft_workload: Optional[Dict[str, Any]] = None
    connector_id: Optional[str] = None
    #: The version agents call; 0 before the first is published
    running: int = 0
    #: The open draft's number; 0 when there is none
    draft: int = 0
    apis: List[ServerAPI] = []
    tools: List[Dict[str, Any]] = []
    draft_tools: Optional[List[Dict[str, Any]]] = None
    changes: Optional[List[Dict[str, Any]]] = None
    versions: Optional[List[Dict[str, Any]]] = None

    @property
    def working(self) -> List[Dict[str, Any]]:
        """The tools the next version starts from: the draft's, or the running one's."""
        return self.draft_tools if self.draft_tools is not None else self.tools


def list(workspace: Optional[str] = None) -> List[Server]:
    """The workspace's custom servers."""
    answer = get_client().get("/mcp-servers", workspace=workspace) or {}
    return [Server.model_validate(s) for s in (answer.get("servers") or [])]


def get(server_id: str, workspace: Optional[str] = None) -> Server:
    """One server, with its history."""
    return Server.model_validate(get_client().get(f"/mcp-servers/{server_id}", workspace=workspace))


def find(name_or_id: str, workspace: Optional[str] = None) -> Server:
    """One server by its name (case-insensitive) or id, with its history."""
    wanted = name_or_id.strip().lower()
    for s in list(workspace=workspace):
        if s.id == name_or_id or s.name.lower() == wanted:
            return get(s.id, workspace=workspace)
    raise LookupError(f"no custom server {name_or_id!r} here")


def create(name: str, description: Optional[str] = None, workspace: Optional[str] = None) -> Server:
    """A server with nothing in it yet: add an API, then tools."""
    body = {"name": name, "description": description} if description else {"name": name}
    return Server.model_validate(get_client().post("/mcp-servers", json=body, workspace=workspace))


def delete(server_id: str, workspace: Optional[str] = None) -> None:
    """Delete a server, its versions and its APIs; agents given it lose it."""
    get_client().delete(f"/mcp-servers/{server_id}", workspace=workspace)


def _description(spec: Optional[str]) -> Dict[str, str]:
    """A description given as an address or a local file."""
    if not spec:
        return {}
    if spec.startswith("https://") or spec.startswith("http://"):
        return {"spec_url": spec}
    return {"spec_text": Path(spec).expanduser().read_text()}


def read(spec: str, api: str = "api", workspace: Optional[str] = None) -> Dict[str, Any]:
    """
    What a description holds, before anything is saved:
    ``{title, base_url, operations: [tool]}``. ``spec`` is an address or a file.
    """
    return (
        get_client().post(
            "/mcp-servers/read", json={"api": api, **_description(spec)}, workspace=workspace
        )
        or {}
    )


def add_api(
    server_id: str,
    name: str,
    base_url: str,
    *,
    spec: Optional[str] = None,
    location: str = "internet",
    via: Optional[str] = None,
    auth: str = "none",
    token: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    token_url: Optional[str] = None,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
    scopes: Optional[List[str]] = None,
    workspace: Optional[str] = None,
) -> ServerAPI:
    """
    Add an API to a server. In the network it is reached ``via`` a connected
    cluster or host whose operator relays APIs, or with ``via=""`` directly
    (on-premises only).
    """
    if auth not in AUTH:
        raise ValueError(f"auth is one of {', '.join(AUTH)}")
    body: Dict[str, Any] = {
        "name": name,
        "base_url": base_url,
        "location": location,
        "auth": auth,
        **_description(spec),
    }
    for k, v in (
        ("via", via),
        ("token", token),
        ("headers", headers),
        ("username", username),
        ("password", password),
        ("token_url", token_url),
        ("client_id", client_id),
        ("client_secret", client_secret),
        ("scopes", scopes),
    ):
        if v is not None:
            body[k] = v
    return ServerAPI.model_validate(
        get_client().post(f"/mcp-servers/{server_id}/apis", json=body, workspace=workspace)
    )


def remove_api(server_id: str, api: str, workspace: Optional[str] = None) -> None:
    """Remove an API no version in use calls."""
    get_client().delete(f"/mcp-servers/{server_id}/apis/{api}", workspace=workspace)


def operations(
    server_id: str, api: str, refresh: bool = False, workspace: Optional[str] = None
) -> Dict[str, Any]:
    """An API's description as tools; ``refresh`` reads it again from its address."""
    params = {"refresh": 1} if refresh else None
    return (
        get_client().get(
            f"/mcp-servers/{server_id}/apis/{api}/operations", params=params, workspace=workspace
        )
        or {}
    )


def put_draft(
    server_id: str, tools: List[Dict[str, Any]], workspace: Optional[str] = None
) -> Server:
    """The next version's tools, whole: a draft beside the running version."""
    return Server.model_validate(
        get_client().put(
            f"/mcp-servers/{server_id}/draft", json={"tools": tools}, workspace=workspace
        )
    )


def discard(server_id: str, workspace: Optional[str] = None) -> Server:
    """Throw the draft away."""
    return Server.model_validate(
        get_client().delete(f"/mcp-servers/{server_id}/draft", workspace=workspace)
    )


def publish(server_id: str, workspace: Optional[str] = None) -> Server:
    """The draft becomes what every agent given the server calls."""
    return Server.model_validate(
        get_client().post(f"/mcp-servers/{server_id}/publish", json={}, workspace=workspace)
    )


def restore(server_id: str, version: int, workspace: Optional[str] = None) -> Server:
    """An earlier version published again, as the newest."""
    return Server.model_validate(
        get_client().post(
            f"/mcp-servers/{server_id}/versions/{version}/restore", json={}, workspace=workspace
        )
    )


def call(
    server_id: str,
    tool: str,
    arguments: Optional[Dict[str, Any]] = None,
    version: Optional[int] = None,
    workspace: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Call one tool as the person testing it, with its API's own credential:
    ``{version, tool, result: {content, isError}, ms}``. The draft's when
    there is one, unless ``version`` names another.
    """
    body: Dict[str, Any] = {"tool": tool, "arguments": arguments or {}}
    if version:
        body["version"] = version
    return get_client().post(f"/mcp-servers/{server_id}/call", json=body, workspace=workspace) or {}


def catalogue(workspace: Optional[str] = None) -> List[Dict[str, Any]]:
    """The servers the platform runs from images, and the fields each needs."""
    return (get_client().get("/mcp-servers/catalogue", workspace=workspace) or {}).get(
        "servers"
    ) or []


def create_image(
    name: str,
    image: Dict[str, Any],
    description: Optional[str] = None,
    workspace: Optional[str] = None,
) -> Server:
    """
    A server run from an image, started at once as its first draft: ``image``
    is ``{catalogue, fields, compute, size}`` for one the catalogue offers, or
    ``{image, command, transport, port, path, settings, hosts, tcp, compute,
    size}`` for your own. Its tools are chosen once it runs.
    """
    body: Dict[str, Any] = {"name": name, "kind": "image", "image": image}
    if description:
        body["description"] = description
    return Server.model_validate(get_client().post("/mcp-servers", json=body, workspace=workspace))


def live_tools(
    server_id: str, version: int, workspace: Optional[str] = None
) -> List[Dict[str, Any]]:
    """What an image server's version lists now, each tool at the default it would start at."""
    answer = (
        get_client().get(f"/mcp-servers/{server_id}/versions/{version}/tools", workspace=workspace)
        or {}
    )
    return answer.get("tools") or []


def put_image_draft(
    server_id: str,
    tools: List[Dict[str, Any]],
    image: Optional[Dict[str, Any]] = None,
    workspace: Optional[str] = None,
) -> Server:
    """An image server's next version: its tools, and what runs when ``image`` changes it."""
    body: Dict[str, Any] = {"tools": tools}
    if image is not None:
        body["image"] = image
    return Server.model_validate(
        get_client().put(f"/mcp-servers/{server_id}/draft", json=body, workspace=workspace)
    )


def set_secrets(server_id: str, values: Dict[str, str], workspace: Optional[str] = None) -> Server:
    """
    An image server's secrets by name (an empty value removes one); its
    server starts again with them.
    """
    return Server.model_validate(
        get_client().put(
            f"/mcp-servers/{server_id}/secrets", json={"values": values}, workspace=workspace
        )
    )
