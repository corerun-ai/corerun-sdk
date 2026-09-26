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
