"""
Credentials for things that are not people.

A person gets theirs from `corerun login`, which mints a short-lived token and
a refresh token to renew it. This is the other case: an exporter, a CI job, a
script — something that has to authenticate unattended and should be able to do
one job rather than everything its owner can do.

The scope list is the point. A token issued with no scopes carries everything
its owner can do, which is almost never what an OTLP exporter needs: it sends
spans and reads nothing, and a leaked credential that can only do that is a
very different incident from one that can start a training job.

    import corerun
    corerun.init()

    token = corerun.tokens.create(
        "goose tracing", scopes=["traces:write"], expires_in_days=30
    )
    print(token.key)   # shown once, never again
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from corerun.config import get_client

_BASE = "/api-keys"


@dataclass
class Token:
    """One issued credential.

    `key` is populated only by `create`. The platform stores a hash, so a token
    that was not written down when it was made cannot be recovered — it has to
    be revoked and replaced.
    """

    key_id: str
    name: str = ""
    description: str = ""
    scope: str = ""
    scopes: str = ""
    workspace_id: Optional[str] = None
    tenant_id: Optional[str] = None
    role: str = ""
    expires_at: Optional[str] = None
    created_at: Optional[str] = None
    last_used_at: Optional[str] = None

    #: Only on creation.
    key: str = ""

    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def scope_list(self) -> List[str]:
        """What it may do, as a list. Empty means everything its owner can."""
        return [s for s in (self.scopes or "").split() if s]

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "Token":
        return cls(
            key_id=data.get("key_id", ""),
            name=data.get("name", ""),
            description=data.get("description", "") or "",
            scope=data.get("scope", ""),
            scopes=data.get("scopes", "") or "",
            workspace_id=data.get("workspace_id"),
            tenant_id=data.get("tenant_id"),
            role=data.get("role", ""),
            expires_at=data.get("expires_at"),
            created_at=data.get("created_at"),
            last_used_at=data.get("last_used_at"),
            key=data.get("key", "") or "",
            raw=data,
        )


def create(
    name: str,
    *,
    scopes: Optional[List[str]] = None,
    scope: str = "workspace",
    workspace_id: Optional[str] = None,
    expires_in_days: int = 0,
    workspace: Optional[str] = None,
    description: str = "",
) -> Token:
    """Issue a token, returning it with its secret.

    `scopes` is what it may do — `["traces:write"]` for an exporter. Omitting
    it grants everything its owner can do, which the platform accepts and which
    is worth deciding rather than defaulting into.

    `expires_in_days=0` never expires. For a credential that will live in a
    config file on somebody's laptop, a date is cheaper than a revocation.
    """
    body: Dict[str, Any] = {"name": name, "scope": scope}
    if description:
        body["description"] = description

    # A workspace-scoped token needs to say which, and the answer is almost
    # always the one the caller is already working in. The API refuses without
    # it -- "workspace_id required" -- which is an odd thing to be asked when
    # every other command in the CLI infers it.
    if scope == "workspace" and not workspace_id:
        from corerun.config import get_config

        workspace_id = workspace or get_config().workspace
    if workspace_id:
        body["workspace_id"] = workspace_id
    if scopes:
        body["scopes"] = scopes
    if expires_in_days:
        body["expires_in_days"] = expires_in_days

    return Token.from_json(get_client().post(_BASE, json=body, workspace=workspace))


def tokens(*, workspace: Optional[str] = None) -> List[Token]:
    """Every token issued here. None of them carry their secret."""
    payload = get_client().get(_BASE, workspace=workspace)
    rows = payload if isinstance(payload, list) else (payload.get("api_keys") or payload.get("keys") or [])
    return [Token.from_json(row) for row in rows]


def revoke(key_id: str, *, workspace: Optional[str] = None) -> None:
    """Withdraw one. Refused everywhere within 30 seconds: the gateway checks
    revocation on every request and remembers each answer that long."""
    get_client().delete(f"{_BASE}/{key_id}", workspace=workspace)
