"""
The organisation you administer: usage, and its own security settings.
People, sign-in, service accounts, the licence, workspace limits and shared
git hosts are in org_admin.
"""

import copy
import re
from typing import List, Optional

import typer
from rich.table import Table

from corerun.cli import output
from corerun.cli.metrics_view import dollars

console = output.console
app = typer.Typer(help="Your organisation: people, sign-in, service accounts, licence, limits, usage and security")
security_app = typer.Typer(help="How strict signing in to this organisation is")
app.add_typer(security_app, name="security")
prices_app = typer.Typer(help="The organisation's own prices for providers' models, over the list")
app.add_typer(prices_app, name="prices")

from corerun.cli import org_admin  # noqa: E402

org_admin.register(app)


def _init_client():
    try:
        from corerun import init

        return init()
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        console.print("Run 'corerun login' to authenticate")
        raise typer.Exit(1)


@app.command("usage")
def usage():
    """This month's use against the plan, model calls and tokens included."""
    _init_client()
    import corerun.org as org

    answer = org.usage()

    def render():
        plan = ((answer.get("subscription") or {}).get("plan") or {}).get("display_name", "")
        t = Table(title=f"Usage{f' on {plan}' if plan else ''}")
        t.add_column("Resource")
        t.add_column("Used", justify="right")
        t.add_column("Limit", justify="right")
        for key, row in (answer.get("usage") or {}).items():
            if isinstance(row, dict) and "current" in row:
                limit = row.get("limit") or 0
                t.add_row(
                    key.replace("_", " "),
                    f"{row['current']:,}",
                    f"{limit:,}" if limit else "unlimited",
                )
        console.print(t)
        per = answer.get("model_usage") or []
        if per:
            e = Table(title="Model calls this month, by endpoint")
            for col in (
                "Endpoint",
                "Calls",
                "Prompt tokens",
                "Cache read",
                "Completion tokens",
                "Cost",
            ):
                e.add_column(col, justify="left" if col == "Endpoint" else "right")
            for r in per:
                e.add_row(
                    r["endpoint"],
                    f"{r['calls']:,}",
                    f"{r['prompt_tokens']:,}",
                    f"{r.get('cache_read_tokens') or 0:,}",
                    f"{r['completion_tokens']:,}",
                    dollars(r.get("cost") or 0),
                )
            console.print(e)
            # The organisation's own prices, set per model on each endpoint,
            # applied when each call was made.
            total = answer.get("model_cost") or 0
            console.print(
                f"Model spend this month: [bold]{dollars(total)}[/bold]"
                if total
                else "[dim]No model is priced; set prices with `corerun endpoints price`.[/dim]"
            )

    output.emit(answer, render)


def _duration(value) -> str:
    """The server's durations are Go's ("720h0m0s"); say "720h"."""
    text = str(value)
    if re.fullmatch(r"(\d+h)?(\d+m)?(\d+(\.\d+)?s)?", text) and text:
        text = re.sub(r"(?<=[hm])0s$", "", text)
        text = re.sub(r"(?<=h)0m$", "", text)
    return text


def _show(answer: dict) -> None:
    eff, inst, own = answer["effective"], answer["installation"], answer.get("policy") or {}

    def row(t: Table, label: str, effective, installation, set_here: bool):
        t.add_row(
            label,
            _duration(effective),
            _duration(installation),
            "[green]this organisation[/green]" if set_here else "installation",
        )

    t = Table(title=f"Security settings (version {answer.get('version', 0)})")
    for col in ("Setting", "In force", "Installation", "Set by"):
        t.add_column(col)
    tok, otok = eff["tokens"], own.get("tokens") or {}
    row(
        t,
        "access token lifetime",
        tok["access_ttl"],
        inst["tokens"]["access_ttl"],
        "access_ttl" in otok,
    )
    row(
        t,
        "session refresh limit",
        tok["refresh_ttl"],
        inst["tokens"]["refresh_ttl"],
        "refresh_ttl" in otok,
    )
    row(
        t,
        "browser session",
        eff["sessions"]["browser_ttl"],
        inst["sessions"]["browser_ttl"],
        bool(own.get("sessions")),
    )
    row(
        t,
        "API key longest",
        tok["service_max_ttl"],
        inst["tokens"]["service_max_ttl"],
        "service_max_ttl" in otok,
    )
    row(
        t,
        "API keys must expire",
        tok["require_service_expiry"],
        inst["tokens"]["require_service_expiry"],
        "require_service_expiry" in otok,
    )
    lock, olock = eff["sign_in"]["lockout"], own.get("lockout") or {}
    row(
        t,
        "lockout after",
        f"{lock['attempts']} wrong passwords",
        f"{inst['sign_in']['lockout']['attempts']}",
        "attempts" in olock,
    )
    row(t, "locked for", lock["window"], inst["sign_in"]["lockout"]["window"], "window" in olock)
    row(
        t,
        "password minimum",
        eff["passwords"]["min_length"],
        inst["passwords"]["min_length"],
        bool(own.get("passwords")),
    )
    console.print(t)
    nets = own.get("networks") or {}
    if nets:
        c = nets.get("countries") or {}
        for label, value in (
            ("only from", nets.get("allow")),
            ("never from", nets.get("deny")),
            ("only countries", c.get("allow")),
            ("never countries", c.get("deny")),
        ):
            if value:
                console.print(f"  {label}: {', '.join(value)}")
    else:
        console.print("  networks: any (no organisation rules)")


@security_app.command("show")
def security_show():
    """The organisation's security settings, the ones in force, and the installation's."""
    _init_client()
    import corerun.org as org

    answer = org.security_policy()
    output.emit(answer, lambda: _show(answer))


@security_app.command("set")
def security_set(
    access_ttl: Optional[str] = typer.Option(
        None, "--access-ttl", help='Access token lifetime, e.g. "30m"'
    ),
    refresh_ttl: Optional[str] = typer.Option(
        None, "--refresh-ttl", help='Longest a session is refreshed, e.g. "168h"'
    ),
    browser_ttl: Optional[str] = typer.Option(
        None, "--browser-ttl", help='Browser session, e.g. "8h"'
    ),
    key_max_ttl: Optional[str] = typer.Option(
        None, "--key-max-ttl", help='Longest an API key may last, e.g. "2160h"'
    ),
    require_key_expiry: Optional[bool] = typer.Option(
        None, "--require-key-expiry/--no-require-key-expiry"
    ),
    lockout_attempts: Optional[int] = typer.Option(
        None, "--lockout-attempts", help="Wrong passwords before lockout"
    ),
    lockout_window: Optional[str] = typer.Option(
        None, "--lockout-window", help='How long, e.g. "30m"'
    ),
    min_password: Optional[int] = typer.Option(
        None, "--min-password", help="Password minimum length"
    ),
    allow_network: Optional[List[str]] = typer.Option(
        None, "--allow-network", help="Only from this CIDR (repeatable; replaces the list)"
    ),
    deny_network: Optional[List[str]] = typer.Option(
        None, "--deny-network", help="Never from this CIDR (repeatable; replaces the list)"
    ),
    allow_country: Optional[List[str]] = typer.Option(
        None, "--allow-country", help="Only from this ISO country (repeatable)"
    ),
    deny_country: Optional[List[str]] = typer.Option(
        None, "--deny-country", help="Never from this ISO country (repeatable)"
    ),
    clear: bool = typer.Option(
        False, "--clear", help="Remove the organisation's settings; the installation's apply"
    ),
):
    """
    Make signing in to this organisation stricter. Only the settings named
    change; the rest keep what the organisation set before. Anything looser
    than the installation allows is refused, with the field that is.

    Example:
        corerun org security set --access-ttl 30m --lockout-attempts 5
        corerun org security set --allow-network 10.0.0.0/8 --allow-network 203.0.113.7
    """
    _init_client()
    import corerun.org as org

    current = org.security_policy()
    version = current.get("version", 0)
    if clear:
        policy = None
    else:
        policy = copy.deepcopy(current.get("policy") or {})

        def put(section: str, key: str, value):
            if value is not None:
                policy.setdefault(section, {})[key] = value

        put("tokens", "access_ttl", access_ttl)
        put("tokens", "refresh_ttl", refresh_ttl)
        put("tokens", "service_max_ttl", key_max_ttl)
        put("tokens", "require_service_expiry", require_key_expiry)
        put("sessions", "browser_ttl", browser_ttl)
        put("lockout", "attempts", lockout_attempts)
        put("lockout", "window", lockout_window)
        put("passwords", "min_length", min_password)
        put("networks", "allow", allow_network or None)
        put("networks", "deny", deny_network or None)
        if allow_country or deny_country:
            countries = policy.setdefault("networks", {}).setdefault("countries", {})
            if allow_country:
                countries["allow"] = [c.upper() for c in allow_country]
            if deny_country:
                countries["deny"] = [c.upper() for c in deny_country]
        if policy == (current.get("policy") or {}):
            console.print("[yellow]Nothing to change.[/yellow] Name a setting, or --clear.")
            raise typer.Exit(1)

    try:
        answer = org.set_security_policy(policy, version)
    except Exception as e:
        console.print(f"[red]Refused:[/red] {e}")
        raise typer.Exit(1)
    output.emit(
        answer,
        lambda: console.print(
            f"[green]Saved[/green] (version {answer.get('version')}). "
            "New sign-ins and refreshes follow it within a minute."
        ),
    )


@prices_app.command("list")
def prices_list():
    """The organisation's own prices, each beside the list price it replaces."""
    _init_client()
    import corerun.org as org
    from corerun.cli.endpoints import price_summary

    answer = org.model_prices()

    def render():
        if not answer:
            console.print("Every model is priced at its list price.")
            return
        t = Table(title="Your organisation's prices per 1M tokens")
        for col in ("Provider", "Model", "Your price", "List price"):
            t.add_column(col)
        for r in answer:
            t.add_row(
                r["provider"],
                r["model"],
                price_summary(r["price"]),
                price_summary(r.get("list")) or "not listed",
            )
        console.print(t)

    output.emit(answer, render)


@prices_app.command("set")
def prices_set(
    provider: str = typer.Argument(
        ..., help="As the price list names it: openai, anthropic, deepseek…"
    ),
    model: str = typer.Argument(..., help="The provider's name for the model"),
    input_: float = typer.Option(..., "--input", help="US$ per 1M input tokens"),
    output_: float = typer.Option(..., "--output", help="US$ per 1M output tokens"),
    cache_read: Optional[float] = typer.Option(
        None, "--cache-read", help="US$ per 1M cached tokens read (default: as input)"
    ),
    cache_write: Optional[float] = typer.Option(
        None, "--cache-write", help="US$ per 1M tokens written to the cache (default: as input)"
    ),
):
    """
    Price a provider's model for the whole organisation, in place of the list
    price, on every endpoint without a price of its own. From now on.

    Example:
        corerun org prices set openai gpt-4o --input 2.0 --output 8.0 --cache-read 1.0
    """
    _init_client()
    import corerun.org as org

    if any(v is not None and v < 0 for v in (input_, output_, cache_read, cache_write)):
        console.print("[red]Error:[/red] prices are US dollars per million tokens, none negative.")
        raise typer.Exit(1)
    answer = org.set_model_price(
        provider,
        model,
        input=input_,
        output=output_,
        cache_read=cache_read,
        cache_write=cache_write,
    )
    output.emit(
        answer,
        lambda: console.print(
            f"[green]Priced[/green] {provider}/{model} for the organisation, from now on"
        ),
    )


@prices_app.command("clear")
def prices_clear(
    provider: str = typer.Argument(...),
    model: str = typer.Argument(...),
):
    """Back to the list price for the organisation, from now on."""
    _init_client()
    import corerun.org as org

    answer = org.clear_model_price(provider, model)
    output.emit(
        answer,
        lambda: console.print(f"[green]{provider}/{model}[/green] is at its list price again"),
    )
