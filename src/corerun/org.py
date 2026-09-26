"""
The organisation you administer: what it has used this month, and its own
security settings.

Both are an organisation administrator's. The security settings can only make
signing in to the organisation stricter than the installation's defaults --
shorter lifetimes, a tighter lockout, longer passwords, the networks and
countries its people may come from; the server refuses anything looser and
names the field.

Example:
    import corerun
    corerun.init()
    corerun.org.usage()["usage"]["model_calls"]
    policy = corerun.org.security_policy()
    corerun.org.set_security_policy({"tokens": {"access_ttl": "30m"}}, version=policy["version"])
"""

from typing import Any, Dict, List, Optional

from corerun.config import get_client


def usage() -> Dict[str, Any]:
    """
    The organisation's plan and what it has used against it: workspaces,
    people, running workloads, storage, GPUs, and this month's model calls,
    with prompt and completion tokens per endpoint (``model_usage``).
    """
    return get_client().get("/tenant/usage")


def security_policy() -> Dict[str, Any]:
    """
    The organisation's own security settings.

    Returns ``{"policy", "version", "effective", "installation"}``: what the
    organisation set, the settings in force because of it, and the
    installation's, which every field defaults to and none may loosen.
    """
    return get_client().get("/tenant/security-policy")


def set_security_policy(policy: Optional[Dict[str, Any]], version: int) -> Dict[str, Any]:
    """
    Replace the organisation's security settings.

    Args:
        policy: the whole policy -- ``tokens`` (access_ttl, refresh_ttl,
            service_max_ttl as durations like "30m" or "720h",
            require_service_expiry), ``sessions`` (browser_ttl), ``lockout``
            (attempts, window), ``passwords`` (min_length), ``networks``
            (allow, deny: CIDRs; countries: {allow, deny}: ISO codes).
            None clears it.
        version: the version it was read at; a policy changed since is refused
            rather than overwritten.
    """
    return get_client().put("/tenant/security-policy", json={"policy": policy, "version": version})


def model_prices() -> List[Dict[str, Any]]:
    """
    The organisation's own prices for providers' models, each beside the
    list price it replaces (``list``). An organisation administrator's.
    """
    return get_client().get("/tenant/model-prices").get("prices") or []


def set_model_price(
    provider: str,
    model: str,
    *,
    input: float,
    output: float,
    cache_read: Optional[float] = None,
    cache_write: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Price a provider's model for the whole organisation -- a negotiated rate,
    a region's price -- in place of the list price, on every endpoint that
    publishes it and has no price of its own. US dollars per million tokens;
    applies to calls from now on.

    Args:
        provider: as the price list names it (``corerun.prices.providers()``'s
            ``priced_as``): "openai", "anthropic", "deepseek"...
        model: the provider's name for the model
    """
    price: Dict[str, float] = {"input": input, "output": output}
    if cache_read is not None:
        price["cache_read"] = cache_read
    if cache_write is not None:
        price["cache_write"] = cache_write
    return get_client().put("/tenant/model-prices", json={"provider": provider, "model": model, "price": price})


def clear_model_price(provider: str, model: str) -> Dict[str, Any]:
    """Back to the list price for the organisation, from now on."""
    return get_client().delete("/tenant/model-prices", params={"provider": provider, "model": model})


# --- The organisation itself -------------------------------------------------


def info() -> Dict[str, Any]:
    """The organisation: its name, slug, plan, and whether deletion is scheduled."""
    return get_client().get("/tenant/me")


def workspaces() -> List[Dict[str, Any]]:
    """Every workspace in the organisation, whether or not you are in it."""
    return get_client().get("/tenant/workspaces").get("workspaces") or []


def workspace_id(ref: str) -> str:
    """A workspace's id from its id, slug or display name."""
    wanted = ref.strip().lower()
    for ws in workspaces():
        if wanted in {
            str(ws.get("id", "")).lower(),
            (ws.get("slug") or "").lower(),
            (ws.get("display_name") or ws.get("name") or "").lower(),
        }:
            return ws["id"]
    raise ValueError(f"no workspace {ref!r} in this organisation")


# --- People ------------------------------------------------------------------

ROLES = ("admin", "engineer", "deployer", "analyst", "member", "viewer")


def members() -> List[Dict[str, Any]]:
    """Everyone with a seat in the organisation, and whether each administers it."""
    return get_client().get("/tenant/users").get("users") or []


def member_id(email: str) -> str:
    for user in members():
        if (user.get("email") or "").lower() == email.strip().lower():
            return user["id"]
    raise ValueError(f"nobody in this organisation has the address {email}")


def add_member(email: str, workspace: str, role: str) -> Dict[str, Any]:
    """
    Give somebody a role in a workspace. Somebody without an account yet is
    sent an invitation, accepted when they first sign in with that address.
    """
    return get_client().post(f"/workspaces/{workspace_id(workspace)}/members", json={"email": email, "role": role})


def remove_member(email: str, workspace: str) -> Dict[str, Any]:
    """Take somebody's role in one workspace away. Their seat stays."""
    return get_client().post(
        "/tenant/users/remove", json={"user_id": member_id(email), "workspace_id": workspace_id(workspace)}
    )


def invites() -> List[Dict[str, Any]]:
    """Invitations nobody has accepted yet, across every workspace."""
    return get_client().get("/tenant/users/invites").get("invites") or []


def cancel_invite(invite_id: str) -> Dict[str, Any]:
    for inv in invites():
        if inv["id"] == invite_id or inv["id"].startswith(invite_id):
            return get_client().delete(f"/workspaces/{inv['workspace_id']}/invites/{inv['id']}")
    raise ValueError(f"no pending invitation {invite_id!r}")


# --- Sign-in providers -------------------------------------------------------

PROVIDER_TYPES = ("entra", "google", "okta", "auth0", "github", "local", "custom")


def sign_in_providers() -> List[Dict[str, Any]]:
    """The identity providers the organisation's people sign in with."""
    return get_client().get("/tenant/auth-providers").get("providers") or []


def _provider_id(ref: str) -> str:
    for p in sign_in_providers():
        if ref in (p["id"], p["name"]) or p["id"].startswith(ref):
            return p["id"]
    raise ValueError(f"no sign-in provider {ref!r}")


def add_sign_in_provider(name: str, provider_type: str, **fields: Any) -> Dict[str, Any]:
    """
    Add an identity provider. ``fields``: issuer_url, client_id,
    client_secret, domain (the email domain it answers for), admin_group_id
    (a group whose members administer the organisation), is_default,
    display_order. The issuer is validated before anything is saved.
    """
    body = {"name": name, "provider_type": provider_type, **{k: v for k, v in fields.items() if v is not None}}
    return get_client().post("/tenant/auth-providers", json=body)


def update_sign_in_provider(ref: str, **fields: Any) -> Dict[str, Any]:
    """Change the fields named; is_active=False turns it off without deleting it."""
    body = {k: v for k, v in fields.items() if v is not None}
    return get_client().put(f"/tenant/auth-providers/{_provider_id(ref)}", json=body)


def set_default_sign_in_provider(ref: str) -> Dict[str, Any]:
    return get_client().post(f"/tenant/auth-providers/{_provider_id(ref)}/set-default")


def remove_sign_in_provider(ref: str) -> Dict[str, Any]:
    return get_client().delete(f"/tenant/auth-providers/{_provider_id(ref)}")


# --- Service accounts --------------------------------------------------------


def service_accounts() -> List[Dict[str, Any]]:
    """Machine identities: a client id and secret exchanged for tokens."""
    return get_client().get("/tenant/service-accounts").get("service_accounts") or []


def _account_id(ref: str) -> str:
    for a in service_accounts():
        if ref in (a["id"], a["name"], a.get("client_id")) or a["id"].startswith(ref):
            return a["id"]
    raise ValueError(f"no service account {ref!r}")


def create_service_account(name: str, description: str = "", scopes: Optional[List[str]] = None) -> Dict[str, Any]:
    """Returns the account with ``client_secret`` -- shown this once and never again."""
    body: Dict[str, Any] = {"name": name, "description": description}
    if scopes:
        body["scopes"] = scopes
    return get_client().post("/tenant/service-accounts", json=body)


def rotate_service_account(ref: str) -> Dict[str, Any]:
    """A new secret; the previous one keeps working until it is retired."""
    return get_client().post(f"/tenant/service-accounts/{_account_id(ref)}/rotate")


def retire_previous_secret(ref: str) -> Dict[str, Any]:
    return get_client().delete(f"/tenant/service-accounts/{_account_id(ref)}/previous-secret")


def set_service_account_enabled(ref: str, enabled: bool) -> Dict[str, Any]:
    return get_client().put(f"/tenant/service-accounts/{_account_id(ref)}/enabled", json={"enabled": enabled})


def delete_service_account(ref: str) -> Dict[str, Any]:
    return get_client().delete(f"/tenant/service-accounts/{_account_id(ref)}")


def service_account_workspaces(ref: str) -> List[Dict[str, Any]]:
    return get_client().get(f"/tenant/service-accounts/{_account_id(ref)}/workspaces").get("workspaces") or []


def grant_service_account(ref: str, workspace: str, role: str) -> Dict[str, Any]:
    return get_client().post(
        f"/tenant/service-accounts/{_account_id(ref)}/workspaces",
        json={"workspace_id": workspace_id(workspace), "role": role},
    )


def revoke_service_account(ref: str, workspace: str) -> Dict[str, Any]:
    return get_client().delete(f"/tenant/service-accounts/{_account_id(ref)}/workspaces/{workspace_id(workspace)}")


# --- Licence -----------------------------------------------------------------


def license() -> Dict[str, Any]:
    """
    The installation's licence: ``state`` (licensed, grace, restricted, or
    not_required on the hosted product), ``ends_at`` when a trial or grace
    runs out, and the licence's customer, expiry and limits when one is
    installed.
    """
    return get_client().get("/tenant/license")


def install_license(content: bytes) -> Dict[str, Any]:
    """Verify and install a signed licence file. Takes effect at once."""
    return get_client().request("POST", "/tenant/license", content=content, content_type="application/json")


# --- Workspace quotas --------------------------------------------------------

QUOTA_FIELDS = (
    "max_cpu_cores",
    "max_ram_gb",
    "max_gpu_count",
    "max_storage_gb",
    "max_notebooks",
    "max_jobs",
    "max_inference_servers",
)


def workspace_quota(workspace: str) -> Dict[str, Any]:
    """A workspace's own limits; 0 means the plan's limit applies."""
    return get_client().get(f"/tenant/workspaces/{workspace_id(workspace)}/quota")


def set_workspace_quota(workspace: str, **limits: int) -> Dict[str, Any]:
    """
    Change the limits named (see QUOTA_FIELDS); the rest keep their value.
    0 means "no limit of its own".
    """
    unknown = set(limits) - set(QUOTA_FIELDS)
    if unknown:
        raise ValueError(f"unknown quota fields {sorted(unknown)}")
    wid = workspace_id(workspace)
    current = get_client().get(f"/tenant/workspaces/{wid}/quota")
    body = {k: int(current.get(k) or 0) for k in QUOTA_FIELDS}
    body.update({k: int(v) for k, v in limits.items() if v is not None})
    return get_client().put(f"/tenant/workspaces/{wid}/quota", json=body)


# --- Git connections shared with every workspace -----------------------------

GIT_PROVIDERS = ("github", "gitlab", "bitbucket", "gitea", "generic")


def git_connections() -> List[Dict[str, Any]]:
    """Git hosts every workspace's jobs may clone from; tokens are never returned."""
    return get_client().get("/tenant/shared/git-connections").get("connections") or []


def _connection_id(ref: str) -> str:
    for g in git_connections():
        if ref in (g["id"], g["name"]) or g["id"].startswith(ref):
            return g["id"]
    raise ValueError(f"no git connection {ref!r}")


def add_git_connection(
    name: str, provider: str, host_url: str, token: str, username: str = "", note: str = ""
) -> Dict[str, Any]:
    return get_client().post(
        "/tenant/shared/git-connections",
        json={"name": name, "provider": provider, "host_url": host_url, "token": token, "username": username, "note": note},
    )


def test_git_connection(ref: str, repo: str) -> Dict[str, Any]:
    """``{"ok": true, "refs": [...]}`` when the token can read ``repo``."""
    return get_client().post(f"/tenant/shared/git-connections/{_connection_id(ref)}/test", json={"repo": repo})


def remove_git_connection(ref: str) -> Dict[str, Any]:
    return get_client().delete(f"/tenant/shared/git-connections/{_connection_id(ref)}")
