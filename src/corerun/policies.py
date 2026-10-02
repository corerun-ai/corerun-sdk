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


class Policy(BaseModel):
    """A policy at the organisation's or a workspace's level."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    description: Optional[str] = None
    #: "organization", "workspace" or "agent"
    level: str = "workspace"
    rules: List[Dict[str, Any]] = []
    enabled: bool = True
    updated_at: Optional[str] = None


def _base(org: bool) -> str:
    return "/tenant/shared/policies" if org else "/policies"


def list(org: bool = False, workspace: Optional[str] = None) -> List[Policy]:
    """
    The policies that apply in a workspace: the organisation's and its own
    (agents' own are on the agent). With ``org=True``, the organisation's only.
    """
    answer = get_client().get(_base(org), workspace=workspace) or {}
    return [Policy.model_validate(p) for p in (answer.get("policies") or [])]


def find(name_or_id: str, org: bool = False, workspace: Optional[str] = None) -> Policy:
    """One policy by its name (case-insensitive) or id."""
    wanted = name_or_id.strip().lower()
    for p in list(org=org, workspace=workspace):
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
    body: Dict[str, Any] = {"name": name, "rules": rules, "enabled": enabled}
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


def load_rules(text: str) -> List[Dict[str, Any]]:
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
        for key in ("match",):
            _check_fields(rule.get(key) or {}, where)
        for clause in rule.get("except") or []:
            _check_fields(clause, where + " except")
    return data


def _check_fields(match: Dict[str, Any], where: str) -> None:
    if not isinstance(match, dict):
        raise ValueError(f"{where}: match is a mapping of field to globs")
    for field, globs in match.items():
        if field not in FIELDS:
            raise ValueError(f"{where}: unknown field {field!r} (one of {', '.join(FIELDS)})")
        if not isinstance(globs, builtins.list) or not globs:
            raise ValueError(f"{where}: {field} needs a list of at least one glob")
