"""
Connectors: the tools an agent is given, and each person's sign-ins to them.

A connector is an MCP server (kind ``mcp``) or a Kubernetes cluster given as a
kubeconfig (kind ``kubernetes``). It belongs to a workspace, or -- with
``org=True`` -- to the organisation, which shares it with every workspace.
Creating or changing one checks it live and is refused if the check fails.
No secret a connector holds is ever read back.

Connections are a person's own: an ``auth: oauth`` connector is signed in to by
each person who uses it, and what an agent does with it is done as them.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict

from corerun.config import get_client

KINDS = ("mcp", "kubernetes")
MCP_AUTH = ("none", "bearer", "headers", "oauth")


class Connector(BaseModel):
    """A connector as the platform describes it. Never carries a secret."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    description: Optional[str] = None
    kind: str = "mcp"
    url: str = ""
    auth: str = "none"
    has_secret: bool = False
    #: "workspace" or "organization"
    scope: str = "workspace"
    tools: List[Dict[str, Any]] = []
    checked_at: Optional[str] = None
    check_error: Optional[str] = None
    header_names: Optional[List[str]] = None
    registration: Optional[str] = None
    connected: Optional[bool] = None
    context: Optional[str] = None
    server_version: Optional[str] = None
    created_at: Optional[str] = None


def _base(org: bool) -> str:
    return "/tenant/shared/connectors" if org else "/connectors"


def list(org: bool = False, workspace: Optional[str] = None) -> List[Connector]:
    """
    The connectors a workspace can give its agents: its own and the
    organisation's (``scope`` says which). With ``org=True``, only the
    organisation's, for an organisation administrator.
    """
    answer = get_client().get(_base(org), workspace=workspace) or {}
    return [Connector.model_validate(c) for c in (answer.get("connectors") or [])]


def find(name_or_id: str, org: bool = False, workspace: Optional[str] = None) -> Connector:
    """One connector by its name (case-insensitive) or id."""
    wanted = name_or_id.strip().lower()
    for c in list(org=org, workspace=workspace):
        if c.id == name_or_id or c.name.lower() == wanted:
            return c
    raise LookupError(f"no connector {name_or_id!r}" + (" in the organisation" if org else " here"))


def create(
    name: str,
    *,
    kind: str = "mcp",
    url: Optional[str] = None,
    auth: Optional[str] = None,
    token: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
    scopes: Optional[List[str]] = None,
    kubeconfig: Optional[str] = None,
    context: Optional[str] = None,
    description: Optional[str] = None,
    org: bool = False,
    workspace: Optional[str] = None,
) -> Connector:
    """
    Add a connector. It is checked live before it is kept.

    An MCP server needs ``url`` (a public https address) and its ``auth``:
    ``bearer`` with ``token``, ``headers`` with ``headers``, or ``oauth``, where
    each person signs in (``client_id``/``client_secret`` only for a server
    that takes no client registration). A Kubernetes cluster needs
    ``kubeconfig`` (its text), and ``context`` when it holds several.

    Example:
        corerun.connectors.create("prod", kind="kubernetes",
                                  kubeconfig=open("prod.yaml").read())
    """
    if kind == "mcp" and auth is None:
        auth = "none"
    body = _body(
        name=name, kind=kind, url=url, auth=auth, token=token, headers=headers,
        client_id=client_id, client_secret=client_secret, scopes=scopes,
        kubeconfig=kubeconfig, context=context, description=description,
    )
    return Connector.model_validate(get_client().post(_base(org), json=body, workspace=workspace))


def update(
    connector_id: str,
    *,
    name: Optional[str] = None,
    url: Optional[str] = None,
    auth: Optional[str] = None,
    token: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
    scopes: Optional[List[str]] = None,
    kubeconfig: Optional[str] = None,
    context: Optional[str] = None,
    description: Optional[str] = None,
    org: bool = False,
    workspace: Optional[str] = None,
) -> Connector:
    """Change a connector; what is not given stays. Checked live again."""
    body = _body(
        name=name, url=url, auth=auth, token=token, headers=headers, client_id=client_id,
        client_secret=client_secret, scopes=scopes, kubeconfig=kubeconfig, context=context,
        description=description,
    )
    return Connector.model_validate(
        get_client().put(f"{_base(org)}/{connector_id}", json=body, workspace=workspace)
    )


def delete(connector_id: str, org: bool = False, workspace: Optional[str] = None) -> None:
    """Remove a connector; agents given it lose it."""
    get_client().delete(f"{_base(org)}/{connector_id}", workspace=workspace)


def test(connector_id: str, org: bool = False, workspace: Optional[str] = None) -> Dict[str, Any]:
    """Check a connector now: ``{ok, message?, connector}``."""
    return get_client().post(f"{_base(org)}/{connector_id}/test", workspace=workspace) or {}


def _body(**fields) -> Dict[str, Any]:
    return {k: v for k, v in fields.items() if v is not None}


# ── connections: each person's own sign-ins ───────────────────────────────────


def connections(agent: Optional[str] = None, workspace: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    The caller's sign-ins to the workspace's ``auth: oauth`` connectors:
    ``[{connector_id, name, host, connected, connected_at?}]``. With ``agent``,
    only that agent's.
    """
    params = {"agent": agent} if agent else None
    answer = get_client().get("/connections", params=params, workspace=workspace) or {}
    return answer.get("connections") or []


def connect(connector_id: str, workspace: Optional[str] = None) -> str:
    """
    Start signing in to a connector; answers the address to open in a browser.
    The provider returns to the console, which finishes the sign-in.
    """
    answer = get_client().post(f"/connections/{connector_id}", workspace=workspace) or {}
    return answer["authorize_url"]


def disconnect(connector_id: str, workspace: Optional[str] = None) -> None:
    """Forget the caller's sign-in to a connector."""
    get_client().delete(f"/connections/{connector_id}", workspace=workspace)
