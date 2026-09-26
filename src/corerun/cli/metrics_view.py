"""One table for a model server's metrics, whichever command asked."""

from typing import Any, Dict, List

from rich.table import Table


def _weighted(points: List[Dict[str, Any]], key: str) -> float:
    total, weight = 0.0, 0.0
    for p in points:
        w = (p.get("requests") or 0) + (p.get("failed") or 0)
        total += (p.get(key) or 0) * w
        weight += w
    return total / weight if weight else float("nan")


def _ms(seconds: float) -> str:
    return "—" if seconds != seconds else f"{seconds * 1000:,.0f} ms"


def _speed(itl: float) -> str:
    return "—" if itl != itl or itl <= 0 else f"{1 / itl:,.1f} tok/s"


def table(answer: Dict[str, Any], title: str) -> Table:
    """
    Totals and request-weighted latencies over the range, one row per model:
    a deployed server from its own metrics, an upstream from the calls made to it.
    """
    t = Table(title=title)
    for col in (
        "Model",
        "Kind",
        "Requests",
        "Failed",
        "Tokens in / out",
        "Cache read",
        "First token p50 / p90 / p99",
        "End-to-end p50",
        "Decode p50",
        "Cost",
    ):
        t.add_column(col, justify="right" if col not in ("Model", "Kind") else "left")
    for s in answer.get("servers") or []:
        pts = s.get("points") or []
        req = sum(p.get("requests") or 0 for p in pts)
        failed = sum(p.get("failed") or 0 for p in pts)
        tin = sum(p.get("prompt_tokens") or 0 for p in pts)
        tout = sum(p.get("completion_tokens") or 0 for p in pts)
        cached = sum(p.get("cache_read_tokens") or 0 for p in pts)
        # What the calls cost at the price in force when each was made.
        cost = sum(p.get("cost") or 0 for p in pts)
        t.add_row(
            s.get("model") or s.get("name") or s.get("server_id", ""),
            s.get("kind") or "server",
            f"{req:,.0f}",
            f"[red]{failed:,.0f}[/red]" if failed else "0",
            f"{tin:,.0f} / {tout:,.0f}",
            f"{cached:,.0f}" if cached else "—",
            " / ".join(_ms(_weighted(pts, k)) for k in ("ttft_p50", "ttft_p90", "ttft_p99")),
            _ms(_weighted(pts, "e2e_p50")),
            _speed(_weighted(pts, "itl_p50")),
            dollars(cost) if cost or s.get("pricing") else "[dim]not priced[/dim]",
        )
    return t


def dollars(n: float) -> str:
    """As precisely as a small amount needs."""
    if n == 0:
        return "$0"
    if abs(n) < 0.01:
        return f"${n:.4f}"
    return f"${n:,.2f}"


def caption(answer: Dict[str, Any]) -> str:
    kept = answer.get("retention_days") or 0
    parts = [
        f"step {answer.get('step_seconds', 60)}s",
        f"kept {kept} days" if kept else "kept until removed",
    ]
    if not answer.get("advanced"):
        parts.append("request lengths, queue and cache are on the Advanced plan")
    return " · ".join(parts)
