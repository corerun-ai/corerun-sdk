"""
Model endpoints.

An endpoint is an address applications call, and the models behind it are an
implementation detail of that address. Some of them run on corerun; some are a
provider's. A caller sends one URL, one key, and the name of a model — and which
of those two answers is a decision you can change without telling them.

Usage:
    import corerun

    corerun.init(auth_token="...")

    chat = corerun.endpoints.create("chat")
    print(chat.url)     # https://.../inference/chat
    print(chat.api_key)

    # Publish a model somebody else runs
    corerun.endpoints.add_upstream(
        "chat",
        name="chat-large",                       # what callers ask for
        base_url="https://api.openai.com/v1",
        api_key="sk-...",
        upstream_name="gpt-4o",                  # what the provider is asked for
    )

    # Ask it something, through the endpoint
    print(corerun.endpoints.complete("chat", "chat-large", "Say hello"))
"""

from contextlib import contextmanager
from typing import Callable, Any, Dict, List, Optional
from urllib.parse import quote

import httpx
from pydantic import BaseModel, field_validator

from corerun.config import get_client, get_config
from corerun.exceptions import ConfigurationError, unreachable


def _reply(payload: Dict[str, Any]) -> str:
    """
    The assistant's answer, wherever this endpoint put it.

    A reasoning model answers in two parts and only the second lands in
    ``content``; with a small token budget the thinking uses it all and content
    comes back null. The first part is not in the OpenAI schema, so endpoints
    spell it differently — vLLM and DeepSeek say ``reasoning_content``, others
    say ``reasoning`` — and reading one name but not the other fails on half of
    them. Reading neither turns a working call into the word "None".
    """
    try:
        message = payload["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return ""

    for field in ("content", "reasoning_content", "reasoning"):
        value = message.get(field)
        if value and value.strip():
            return value
    return ""


def _verify() -> bool:
    """
    Whether to check TLS on calls to the endpoint itself.

    These calls go to the gateway rather than the management API, so they do not
    go through the SDK's client — and without this they would ignore the
    verify_ssl setting and fail against any deployment with a self-signed
    certificate, which is every development cluster.
    """
    try:
        return get_config().verify_ssl
    except Exception:  # noqa: BLE001 — no config yet means default behaviour
        return True


def _configured_inference_url() -> str:
    """The address somebody chose for model endpoints, if they chose one."""
    try:
        return get_config().inference_url or ""
    except Exception:  # noqa: BLE001 — no config yet means default behaviour
        return ""


def _derived_inference_base() -> str:
    """The address the API address implies, for when nothing else supplies one."""
    try:
        return get_config().inference_base
    except Exception:  # noqa: BLE001 — no config yet means default behaviour
        return ""


def _address(endpoint: "Endpoint", path: str) -> str:
    """The URL to call an endpoint on, host included.

    Three answers, in the order they are trusted.

    An address somebody configured (``inference_url``, ``CORERUN_INFERENCE_URL``)
    is a decision, and covers the deployment whose callers cannot reach the
    address the platform reports -- a gateway on a host of its own, a cluster
    behind a private load balancer. It replaces the base and keeps the path, the
    path being the platform's routing and the host the part a caller may know
    better.

    Otherwise the address the platform reports for this endpoint, which it knows
    better than anything guessed from the API address here.

    And only when it reports none, the address its API address implies -- the
    deployment's own ``api``-prefixed host.
    """
    configured = _configured_inference_url()
    if configured:
        return f"{configured.rstrip('/')}/{endpoint.name}{path}"

    if endpoint.url:
        return f"{endpoint.url}{path}"

    derived = _derived_inference_base()
    if derived:
        return f"{derived.rstrip('/')}/{endpoint.name}{path}"

    raise ConfigurationError(
        f"no address to call the '{endpoint.name}' endpoint on: the platform "
        f"reports none for it, and none follows from the API address. Set "
        f"CORERUN_INFERENCE_URL to where this deployment publishes its endpoints."
    )


def _request(http: httpx.Client, method: str, url: str, **kwargs) -> httpx.Response:
    """Call the gateway, or say which address could not be reached.

    These calls do not go through the SDK's client -- they go to the gateway
    rather than the management API -- so the address has to be named here too.
    Otherwise a deployment whose gateway does not resolve reports it the way
    the resolver worded it, which names no host and suggests nothing.
    """
    try:
        return http.request(method, url, **kwargs)
    except httpx.ConnectError as e:
        raise unreachable(url, e, setting="CORERUN_INFERENCE_URL") from e


@contextmanager
def _stream(http: httpx.Client, method: str, url: str, **kwargs):
    """The same, for a response that is read as it arrives."""
    try:
        with http.stream(method, url, **kwargs) as response:
            yield response
    except httpx.ConnectError as e:
        raise unreachable(url, e, setting="CORERUN_INFERENCE_URL") from e


class PublishedModel(BaseModel):
    """One model an endpoint answers for."""

    model: str
    kind: str = "deployment"
    state: str = ""
    id: str = ""
    base_url: str = ""
    upstream_name: str = ""
    runs_on: str = ""
    accelerator: str = ""
    # What it costs from now on, in US dollars per million tokens: input,
    # output, and cache_read / cache_write when priced apart. None when
    # nobody priced it.
    pricing: Optional[Dict[str, float]] = None
    # Where that price came from: {"level": "endpoint" | "organisation" |
    # "list", "as_of": ...}.
    pricing_source: Optional[Dict[str, Any]] = None

    @property
    def is_external(self) -> bool:
        """Whether this model runs somewhere corerun does not."""
        return self.kind == "external"

    @property
    def where(self) -> str:
        """The machine serving this model.

        The cluster it was deployed to, or the provider's host for a model
        corerun does not run. Saying "corerun" for everything the platform
        runs names the thing the reader is already looking at; what they
        cannot see is which of their machines is answering.

        The fallbacks are for an older API that does not send the field.
        """
        if self.runs_on:
            return self.runs_on
        if self.is_external:
            return self.base_url
        return "unknown host"

    def __repr__(self) -> str:
        where = self.where
        if self.accelerator:
            where = f"{where}, {self.accelerator}"
        return f"<PublishedModel {self.model} ({where}) {self.state}>"


class Endpoint(BaseModel):
    """An address, and what answers behind it."""

    name: str
    url: str = ""
    api_key: str = ""
    api_key_secondary: str = ""
    description: str = ""
    models: List[str] = []
    published: List[PublishedModel] = []
    instances: int = 0
    created_at: Optional[str] = None

    @field_validator("models", "published", mode="before")
    @classmethod
    def _empty_when_absent(cls, value):
        """A server that sends null for an empty list still means empty."""
        return value or []

    def __repr__(self) -> str:
        return f"<Endpoint {self.name} {len(self.models)} model(s)>"


def list(workspace: Optional[str] = None) -> List[Endpoint]:
    """Every endpoint in the workspace."""
    client = get_client()
    data = client.get("/inference-endpoints", workspace=workspace)
    return [Endpoint.model_validate(e) for e in data.get("endpoints", [])]


def get(name: str, workspace: Optional[str] = None) -> Endpoint:
    """One endpoint, with everything published behind it."""
    client = get_client()
    return Endpoint.model_validate(
        client.get(f"/inference-endpoints/{name}", workspace=workspace)
    )


def create(
    name: str, description: str = "", workspace: Optional[str] = None
) -> Endpoint:
    """
    Create an empty endpoint.

    Empty is useful: it has a URL and a key immediately, so a team can be given
    them today and what serves the models settled afterwards.
    """
    client = get_client()
    return Endpoint.model_validate(
        client.post(
            "/inference-endpoints",
            json={"name": name, "description": description},
            workspace=workspace,
        )
    )


def delete(name: str, workspace: Optional[str] = None) -> dict:
    """
    Delete an endpoint and the upstreams published on it.

    Refused while corerun is still running a deployment behind it — removing a
    name is not how a running model should be taken down. Upstreams go with it,
    since those are entries rather than workloads.
    """
    client = get_client()
    return client.delete(f"/inference-endpoints/{name}", workspace=workspace)


def rotate_key(
    name: str, key: str = "secondary", workspace: Optional[str] = None
) -> dict:
    """
    Replace one of the endpoint's two keys.

    Both are live at once, which is the whole mechanism: regenerate the one
    nobody is using, move callers onto it, then regenerate the other. Rotating
    a single key would mean changing it and every caller in the same instant.

    Args:
        key: "secondary" (default) or "primary"
    """
    client = get_client()
    return client.post(
        f"/inference-endpoints/{name}/rotate-key",
        json={"key": key},
        workspace=workspace,
    )


def add_upstream(
    name: str,
    *,
    model: str,
    base_url: str = "",
    api_key: str = "",
    upstream_name: str = "",
    provider: str = "",
    pricing: Optional[Dict[str, float]] = None,
    workspace: Optional[str] = None,
) -> dict:
    """
    Publish a model this platform does not run.

    Args:
        name: the endpoint to publish it on
        model: the name callers will ask for
        base_url: the OpenAI-compatible root, e.g. https://api.openai.com/v1.
            May be left out for a ``provider`` whose address is known
            (``corerun.prices.providers()``).
        api_key: the provider's credential — stored encrypted, never returned
        upstream_name: what the provider is asked for, when it differs from
            ``model``. This is what lets the thing behind a name be replaced
            without anybody's code changing.
        provider: where it runs, as ``corerun.prices.providers()`` keys it
            ("openai", "anthropic"...): its address when ``base_url`` is left
            out, and its list price, which prices calls unless the
            organisation or this endpoint set another.
        pricing: this endpoint's own price for it, as :func:`price` takes it,
            in place of the list price.
    """
    body: Dict[str, Any] = {
        "name": model,
        "base_url": base_url,
        "api_key": api_key,
        "upstream_name": upstream_name,
        "provider": provider,
    }
    if pricing is not None:
        body["pricing"] = pricing
    client = get_client()
    return client.post(f"/inference-endpoints/{name}/upstreams", json=body, workspace=workspace)


def _segment(model: str) -> str:
    """A model name as one path segment: "org/model" keeps its slash encoded."""
    return quote(model, safe="")


def price(
    name: str,
    model: str,
    *,
    input: float,
    output: float,
    cache_read: Optional[float] = None,
    cache_write: Optional[float] = None,
    workspace: Optional[str] = None,
) -> dict:
    """
    Set what a model behind an endpoint costs, in US dollars per million
    tokens, as the provider lists it -- or an internal rate for a model the
    platform runs. Calls are priced when they are recorded, so this applies
    from now on and never reprices calls already made.

    Args:
        name: the endpoint
        model: the model, as callers ask for it
        input: per million input tokens not read from a cache
        output: per million output tokens
        cache_read: per million tokens read from the provider's prompt
            cache. Left out, they are charged as input.
        cache_write: per million tokens written to it. Left out, as input.
    """
    body: Dict[str, float] = {"input": input, "output": output}
    if cache_read is not None:
        body["cache_read"] = cache_read
    if cache_write is not None:
        body["cache_write"] = cache_write
    client = get_client()
    return client.put(f"/inference-endpoints/{name}/pricing/{_segment(model)}", json=body, workspace=workspace)


def clear_price(name: str, model: str, workspace: Optional[str] = None) -> dict:
    """Stop pricing a model. Calls already recorded keep their cost."""
    client = get_client()
    return client.delete(f"/inference-endpoints/{name}/pricing/{_segment(model)}", workspace=workspace)


def update_upstream(
    name: str,
    model: str,
    *,
    new_name: Optional[str] = None,
    upstream_name: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    workspace: Optional[str] = None,
) -> dict:
    """
    Change an upstream the endpoint publishes: the name callers use, what the
    provider is asked for, where it is, or its credential. Anything left out is
    kept -- the stored key in particular, which is never returned.

    Args:
        name: the endpoint
        model: the upstream's current name, as callers ask for it
        new_name: a new name for callers to ask for
        upstream_name: what the provider is asked for
        base_url: the OpenAI-compatible root
        api_key: a new provider credential
    """
    body = {k: v for k, v in {"name": new_name, "upstream_name": upstream_name,
                              "base_url": base_url, "api_key": api_key}.items() if v is not None}
    if not body:
        raise ValueError("nothing to change: name at least one setting")
    client = get_client()
    return client.patch(f"/inference-endpoints/{name}/upstreams/{_segment(model)}", json=body, workspace=workspace)


def metrics(name: str, range: str = "24h", workspace: Optional[str] = None) -> dict:
    """
    What every model behind an endpoint has been doing, one series each:
    deployed servers from their own metrics, and upstream models from the
    calls made to them. See ``corerun.inference.metrics`` for the figures.

    Args:
        name: the endpoint
        range: 1h, 6h, 24h, 7d, 30d or 90d, within what the plan keeps
    """
    client = get_client()
    return client.get(f"/inference-endpoints/{name}/metrics", params={"range": range}, workspace=workspace)


def remove_upstream(name: str, model: str, workspace: Optional[str] = None) -> dict:
    """Stop publishing an upstream."""
    client = get_client()
    return client.delete(
        f"/inference-endpoints/{name}/upstreams/{_segment(model)}", workspace=workspace
    )


def models(name: str, workspace: Optional[str] = None) -> List[str]:
    """
    What the endpoint serves right now, asked of the endpoint itself.

    Goes through the gateway rather than the management API, so it answers the
    question a caller would actually be asking: not what is configured, but what
    would be routable if they sent a request this second.
    """
    endpoint = get(name, workspace=workspace)
    with httpx.Client(timeout=30, verify=_verify()) as http:
        response = _request(
            http,
            "GET",
            _address(endpoint, "/v1/models"),
            headers={"Authorization": f"Bearer {endpoint.api_key}"},
        )
        response.raise_for_status()
        return [m["id"] for m in response.json().get("data", [])]


def complete(
    name: str,
    model: str,
    prompt: str,
    *,
    max_tokens: int = 256,
    temperature: Optional[float] = None,
    workspace: Optional[str] = None,
) -> str:
    """
    Send one chat completion through the endpoint and return the reply.

    The point of this is to exercise the real path — the endpoint's own URL and
    key, the router picking a model, the credential swap — rather than the
    management API. If this works, an application pointed at the same two values
    works.
    """
    body: Dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
    }
    if temperature is not None:
        body["temperature"] = temperature

    for message in stream(name, model, prompt, _once=body, workspace=workspace):
        return message
    return ""


def stream(
    name: str,
    model: str,
    prompt: str,
    *,
    max_tokens: int = 256,
    workspace: Optional[str] = None,
    on_reasoning: Optional[Callable[[str], None]] = None,
    _once: Optional[Dict[str, Any]] = None,
):
    """
    Yield the reply as it arrives.

    Streaming is worth testing separately: it is the case where a gateway that
    buffers turns a model that types into one that waits and then pastes.

    What is yielded is the answer. A reasoning model's thinking goes to
    on_reasoning if a caller passes one, and is otherwise held back: a
    programmatic caller asked for the reply, and mixing the two into one
    stream would put the model's notes in the middle of it.
    """
    endpoint = get(name, workspace=workspace)
    url = _address(endpoint, "/v1/chat/completions")
    headers = {
        "Authorization": f"Bearer {endpoint.api_key}",
        "Content-Type": "application/json",
    }

    if _once is not None:
        with httpx.Client(timeout=300, verify=_verify()) as http:
            response = _request(http, "POST", url, headers=headers, json=_once)
            response.raise_for_status()
            yield _reply(response.json())
        return

    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "stream": True,
    }
    import json as _json

    # A reasoning model thinks before it answers, and both arrive on the same
    # stream. The answer is what was asked for, so that is what is yielded.
    #
    # The thinking is handed to on_reasoning as it arrives when a caller wants
    # it. Without that a reasoning model looks like a broken stream: it can
    # spend a whole token budget thinking, emit no answer at all, and so
    # produce nothing to yield until the very end -- which is exactly what a
    # gateway that buffers looks like, and the reason to tell them apart.
    thinking: List[str] = []
    answered = False

    with httpx.Client(timeout=300, verify=_verify()) as http:
        with _stream(http, "POST", url, headers=headers, json=body) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith("data: "):
                    continue
                chunk = line[6:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    delta = _json.loads(chunk)["choices"][0].get("delta", {})
                except (ValueError, KeyError, IndexError):
                    continue

                if delta.get("content"):
                    answered = True
                    yield delta["content"]
                    continue
                for field in ("reasoning_content", "reasoning"):
                    if delta.get(field):
                        if on_reasoning is not None:
                            on_reasoning(delta[field])
                        else:
                            thinking.append(delta[field])
                        break

    if not answered and thinking:
        yield "".join(thinking)


def benchmark(
    name: str,
    model: str,
    *,
    pattern: str = "synthetic",
    isl: Optional[int] = None,
    osl: Optional[int] = None,
    turns: Optional[int] = None,
    turn_delay_ms: Optional[int] = None,
    dataset: Optional[str] = None,
    concurrency: Optional[list] = None,
    duration_seconds: int = 60,
    compute: Optional[str] = None,
    shapes: Optional[list] = None,
    workspace: Optional[str] = None,
) -> dict:
    """
    Start a performance benchmark (aiperf) against an endpoint.

    ``shapes`` sweeps several input/output lengths in one synthetic run, each
    over every concurrency: ``[(512, 256), (4096, 256), (256, 2048)]``.

    Runs on the platform's cluster, which the plan limits, unless ``compute``
    names one of the workspace's own clusters, which it does not. Calls go to
    the endpoint's public address and count toward the plan's model calls.

    Args:
        name: the endpoint
        model: which of its models, by the name callers use
        pattern: synthetic, multi_turn, agentic or sharegpt
        isl, osl: input and output tokens (synthetic, multi_turn)
        turns, turn_delay_ms: conversation shape (multi_turn)
        dataset: claude-code, claude-code-subagents or exgentic (agentic)
        concurrency: the points to measure, e.g. [1, 4, 16]
        duration_seconds: how long each point runs
        compute: run on this compute target instead of the platform's cluster
    """
    body = {
        "model": model,
        "pattern": pattern,
        "runner": "workspace" if compute else "platform",
        "compute_name": compute,
        "isl": isl,
        "osl": osl,
        "turns": turns,
        "turn_delay_ms": turn_delay_ms,
        "dataset": dataset,
        "concurrency": concurrency,
        "duration_seconds": duration_seconds,
        "shapes": [{"isl": int(i), "osl": int(o)} for i, o in shapes] if shapes else None,
    }
    body = {k: v for k, v in body.items() if v is not None}
    return get_client().post(f"/inference-endpoints/{name}/benchmarks", json=body, workspace=workspace)


def benchmarks(name: str, workspace: Optional[str] = None) -> dict:
    """An endpoint's benchmark runs, newest first, and what the plan leaves."""
    return get_client().get(f"/inference-endpoints/{name}/benchmarks", workspace=workspace) or {}


def benchmark_run(name: str, run_id: str, workspace: Optional[str] = None) -> dict:
    """One benchmark run with its points."""
    return get_client().get(f"/inference-endpoints/{name}/benchmarks/{run_id}", workspace=workspace)


def capacity(name: str, model: str, workspace: Optional[str] = None) -> dict:
    """
    What a model behind an endpoint can hold, as its engine reported at start:
    KV-cache tokens and memory, the context it serves, how many requests of
    that length fit at once, and the weights' memory. Empty for a provider's
    model, whose capacity is the provider's.
    """
    return get_client().get(
        f"/inference-endpoints/{name}/benchmark-hints", params={"model": model}, workspace=workspace
    )
