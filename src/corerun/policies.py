"""
Policies: what agents may do with their tools, as rules.

A policy is a list of rules, each ``{name, effect, match?, except?, message?}``
where ``effect`` is ``allow``, ``ask`` or ``deny`` and ``match`` maps a field
(``adapter``, ``action``, ``group``, ``resource``, ``subresource``,
``namespace``, ``name``, ``labels``, ``flags``, ``connector``, ``agent``) to a
list of globs. Policies apply at three levels -- the organisation's, a
workspace's, and one agent's -- and the strictest effect that matches wins
(deny over ask over allow); a request nothing allows is refused. An agent with
no policy of its own may only read.

Network policies (``kind="network"``) are the same shape for where an agent's
sandbox may connect -- the fields ``host``, ``port``, ``action`` and
``agent`` -- decided by the egress proxy. What a destination no rule matches
gets is said per level (``unmatched``): refused unless some level says ask or
allow, the strictest said winning.
"""

import builtins
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict

from corerun.config import get_client

EFFECTS = ("allow", "ask", "deny")
FIELDS = (
    "adapter", "action", "group", "resource", "subresource", "namespace",
    "name", "labels", "flags", "connector", "agent",
)
#: What a network rule may match on.
NETWORK_FIELDS = ("host", "port", "action", "agent")
KINDS = ("tools", "network")


class Policy(BaseModel):
    """A policy at the organisation's or a workspace's level."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    description: Optional[str] = None
    #: "organization", "workspace" or "agent"
    level: str = "workspace"
    #: "tools" or "network"
    kind: str = "tools"
    rules: List[Dict[str, Any]] = []
    enabled: bool = True
    updated_at: Optional[str] = None


def _base(org: bool) -> str:
    return "/tenant/shared/policies" if org else "/policies"


def list(org: bool = False, workspace: Optional[str] = None, kind: str = "tools") -> List[Policy]:
    """
    The policies of a kind that apply in a workspace: the organisation's and
    its own (agents' own are on the agent). With ``org=True``, the
    organisation's only.
    """
    answer = get_client().get(_base(org), params={"kind": kind}, workspace=workspace) or {}
    return [Policy.model_validate(p) for p in (answer.get("policies") or [])]


def find(
    name_or_id: str, org: bool = False, workspace: Optional[str] = None, kind: str = "tools"
) -> Policy:
    """One policy by its name (case-insensitive) or id."""
    wanted = name_or_id.strip().lower()
    for p in list(org=org, workspace=workspace, kind=kind):
        if p.id == name_or_id or p.name.lower() == wanted:
            return p
    raise LookupError(f"no policy {name_or_id!r}" + (" in the organisation" if org else " here"))


def create(
    name: str,
    rules: List[Dict[str, Any]],
    *,
    description: Optional[str] = None,
    enabled: bool = True,
    org: bool = False,
    workspace: Optional[str] = None,
    kind: str = "tools",
) -> Policy:
    """
    Add a policy. A workspace's needs a workspace administrator; the
    organisation's (``org=True``) an organisation administrator.

    Example:
        corerun.policies.create("careful", [
            {"name": "ask-before-writes", "effect": "ask",
             "match": {"action": ["create", "update", "patch", "delete"]}},
        ])
    """
    body: Dict[str, Any] = {"name": name, "rules": rules, "enabled": enabled, "kind": kind}
    if description is not None:
        body["description"] = description
    return Policy.model_validate(get_client().post(_base(org), json=body, workspace=workspace))


def update(
    policy_id: str,
    *,
    name: Optional[str] = None,
    rules: Optional[List[Dict[str, Any]]] = None,
    description: Optional[str] = None,
    enabled: Optional[bool] = None,
    org: bool = False,
    workspace: Optional[str] = None,
) -> Policy:
    """Change a policy; what is not given stays."""
    body = {
        k: v
        for k, v in {"name": name, "rules": rules, "description": description, "enabled": enabled}.items()
        if v is not None
    }
    return Policy.model_validate(get_client().put(f"{_base(org)}/{policy_id}", json=body, workspace=workspace))


def delete(policy_id: str, org: bool = False, workspace: Optional[str] = None) -> None:
    """Remove a policy."""
    get_client().delete(f"{_base(org)}/{policy_id}", workspace=workspace)


def unmatched(org: bool = False, workspace: Optional[str] = None) -> Dict[str, Any]:
    """
    What a destination no network rule matches gets, at the workspace's (or,
    with ``org=True``, the organisation's) level: ``{effect, effective,
    levels}``. The strictest any level says wins; nothing said is ``deny``.
    """
    return get_client().get(f"{_base(org)}/network/unmatched", workspace=workspace)


def set_unmatched(
    effect: str, org: bool = False, workspace: Optional[str] = None
) -> Dict[str, Any]:
    """
    Say what a destination no network rule matches gets: ``deny`` (an
    allowlist), ``ask``, ``allow`` (a blocklist), or ``""`` to say nothing --
    each agent may then choose.
    """
    if effect not in ("deny", "ask", "allow", ""):
        raise ValueError("effect is deny, ask or allow, or '' for none")
    return get_client().put(
        f"{_base(org)}/network/unmatched", json={"effect": effect}, workspace=workspace
    )


def load_rules(text: str, kind: str = "tools") -> List[Dict[str, Any]]:
    """
    Rules from YAML (or JSON): a list of rules, or a mapping with ``rules``.
    Checked for shape here so a mistake is named before anything is sent.
    """
    import yaml

    data = yaml.safe_load(text)
    if isinstance(data, dict) and "rules" in data:
        data = data["rules"]
    if data is None:
        return []
    if not isinstance(data, builtins.list):
        raise ValueError("expected a list of rules, or a mapping with 'rules'")
    for i, rule in enumerate(data):
        where = f"rule {i + 1}" + (f" ({rule.get('name')})" if isinstance(rule, dict) and rule.get("name") else "")
        if not isinstance(rule, dict):
            raise ValueError(f"{where}: a rule is a mapping")
        if rule.get("effect") not in EFFECTS:
            raise ValueError(f"{where}: effect must be one of {', '.join(EFFECTS)}")
        fields = NETWORK_FIELDS if kind == "network" else FIELDS
        for key in ("match",):
            _check_fields(rule.get(key) or {}, where, fields)
        for clause in rule.get("except") or []:
            _check_fields(clause, where + " except", fields)
    return data


def _check_fields(match: Dict[str, Any], where: str, fields: tuple = FIELDS) -> None:
    if not isinstance(match, dict):
        raise ValueError(f"{where}: match is a mapping of field to globs")
    for field, globs in match.items():
        if field not in fields:
            raise ValueError(f"{where}: unknown field {field!r} (one of {', '.join(fields)})")
        if not isinstance(globs, builtins.list) or not globs:
            raise ValueError(f"{where}: {field} needs a list of at least one glob")


def explain(
    request: Dict[str, Any],
    *,
    agent_id: Optional[str] = None,
    connector_id: Optional[str] = None,
    rules: Optional[List[Dict[str, Any]]] = None,
    draft: Optional[Dict[str, Any]] = None,
    workspace: Optional[str] = None,
) -> Dict[str, Any]:
    """
    What the platform's policy engine would decide for a request, without
    sending anything to a cluster: ``{effect, rule, layer, message}``.

    With ``agent_id`` and ``connector_id`` the rules are the ones a real request
    from that agent would meet (organisation, workspace, the agent's own, the
    platform's floor). Otherwise ``rules`` -- each labelled with its ``policy``
    (``organization:<name>``, ``workspace:<name>``, ``agent``) -- are used. A
    ``draft`` ``{policy, rules}`` replaces that policy's rules: unsaved edits.

    ``request`` is ``{action, group, resource, subresource, namespace, name}``;
    ``corerun.kubectl.parse`` makes one from a command line.

    Example:
        request, _ = corerun.kubectl.parse("kubectl delete deploy api -n prod")
        corerun.policies.explain(request, agent_id=a, connector_id=c)
    """
    body: Dict[str, Any] = {"request": request}
    if agent_id or connector_id:
        body["agent_id"], body["connector_id"] = agent_id, connector_id
    if rules is not None:
        body["rules"] = rules
    if draft is not None:
        body["draft"] = draft
    return get_client().post("/toolgate/explain", json=body, workspace=workspace) or {}
