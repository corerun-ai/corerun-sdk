"""
What hosted models cost: the providers a model can be published from, and
each provider's published price per million tokens, which the platform keeps
current from a public list.

A model a provider runs, published on an endpoint, is priced from this list
unless the organisation set its own rate (``corerun.org.set_model_price``) or
the endpoint set a price of its own (``corerun.endpoints.price``).

Example:
    import corerun
    corerun.init()
    [p["key"] for p in corerun.prices.providers()]
    corerun.prices.search("openai", "gpt-4o")[0]["price"]
"""

from typing import Any, Dict, List

from corerun.config import get_client


def providers() -> List[Dict[str, Any]]:
    """
    The providers an upstream can be added from: ``key`` (what
    ``endpoints.add_upstream(provider=...)`` takes), ``name``, ``base_url``
    (empty where each customer has its own, as on Azure), ``priced_as`` (the
    provider the list prices it under) and ``models`` (how many it prices).
    """
    return get_client().get("/model-prices/providers").get("providers") or []


def search(provider: str, query: str = "", limit: int = 50) -> List[Dict[str, Any]]:
    """
    A provider's models and their list prices, filtered by a fragment of the
    name. Each has ``price`` ({input, output, cache_read?, cache_write?} per
    million tokens), ``as_of``, and ``organisation`` when your organisation
    pays its own rate for it.
    """
    answer = get_client().get(
        "/model-prices", params={"provider": provider, "q": query, "limit": limit}
    )
    return answer.get("prices") or []
