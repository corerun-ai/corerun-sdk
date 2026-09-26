"""
What hosted models cost: providers, and their models' published prices.
"""

from typing import Optional

import typer
from rich.table import Table

from corerun.cli import output
from corerun.cli.endpoints import price_summary

console = output.console
app = typer.Typer(help="Providers, and what their models cost")


def _init_client():
    try:
        from corerun import init

        return init()
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        console.print("Run 'corerun login' to authenticate")
        raise typer.Exit(1)


@app.command("providers")
def providers():
    """The providers a model can be published from, and where each one's API is."""
    _init_client()
    import corerun.prices as prices

    answer = prices.providers()

    def render():
        t = Table(title="Providers")
        for col in ("Key", "Name", "Base URL", "Priced models"):
            t.add_column(col, justify="right" if col == "Priced models" else "left")
        for p in answer:
            t.add_row(
                p["key"],
                p["name"],
                p.get("base_url") or f"[dim]{p.get('base_url_hint', '')}[/dim]",
                f"{p.get('models') or 0:,}" if p.get("priced_as") else "—",
            )
        console.print(t)

    output.emit(answer, render)


@app.command("search")
def search(
    provider: str = typer.Argument(
        ..., help="The provider, as `corerun prices providers` lists it"
    ),
    query: Optional[str] = typer.Argument(None, help="A fragment of the model's name"),
    limit: int = typer.Option(50, "--limit", "-n"),
):
    """
    A provider's models and their list prices per million tokens, with your
    organisation's own rate where it pays one.

    Example:
        corerun prices search openai gpt-4o
        corerun prices search anthropic sonnet
    """
    _init_client()
    import corerun.prices as prices

    # The provider's key, or the name the list prices it under.
    from corerun.prices import providers as all_providers

    priced_as = next(
        (p.get("priced_as") or p["key"] for p in all_providers() if p["key"] == provider), provider
    )
    answer = prices.search(priced_as, query or "", limit=limit)

    def render():
        if not answer:
            console.print(f"No priced model of {provider} matches.")
            return
        t = Table(title=f"{provider}: price per 1M tokens")
        for col in ("Model", "List price", "Your organisation", "As of"):
            t.add_column(col)
        for p in answer:
            t.add_row(
                p["model"],
                price_summary(p["price"]),
                price_summary(p.get("organisation")) or "—",
                (p.get("as_of") or "")[:10],
            )
        console.print(t)

    output.emit(answer, render)
