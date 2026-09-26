"""
GenAI observability CLI: experiments, agent traces, sessions and judges.

Traces belong to an experiment, so every command that reads them names one --
`corerun genai experiments` lists what there is. Sending traces needs nothing
from here: the platform accepts OpenTelemetry on /v1/traces.
"""

from typing import List, Optional

import typer
from rich.table import Table

from corerun.cli import output
from corerun.exceptions import CoreRunError

console = output.console
app = typer.Typer(help="Agent traces, sessions and judges")

traces_app = typer.Typer(help="Agent traces")
sessions_app = typer.Typer(help="Conversations, grouped by session id")
review_app = typer.Typer(help="Review queues: traces somebody was asked to look at")
issues_app = typer.Typer(help="Read traces with a model and file what it finds")
app.add_typer(traces_app, name="traces")
app.add_typer(sessions_app, name="sessions")
app.add_typer(review_app, name="review")
app.add_typer(issues_app, name="issues")


def _init_client():
    try:
        from corerun import init

        return init()
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        console.print("Run 'corerun login' to authenticate")
        raise typer.Exit(1)


def _called(work):
    """Run a call to the service, reporting a failure the way the CLI does.

    A missing trace is an ordinary answer to an ordinary question, not a crash,
    and a stack trace in front of it helps nobody holding a trace id that has
    aged out of retention.
    """
    try:
        return work()
    except CoreRunError as e:
        raise output.fail(str(e))


def _duration(ms: Optional[float]) -> str:
    """The unit follows the magnitude, so 20.31s and 60ms sit in one column."""
    if ms is None:
        return "-"
    if ms < 1000:
        return f"{ms:.0f}ms"
    if ms < 60_000:
        return f"{ms / 1000:.2f}s"
    return f"{ms / 60_000:.1f}min"


def _short(text: Optional[str], width: int = 40) -> str:
    if not text:
        return "-"
    flat = " ".join(str(text).split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"


@traces_app.command("list")
def list_traces(
    experiment: str = typer.Option(..., "--experiment", "-e", help="Which experiment's traces"),
    state: Optional[str] = typer.Option(None, "--state", help="OK, ERROR or IN_PROGRESS"),
    session: Optional[str] = typer.Option(None, "--session", help="Only one conversation"),
    limit: int = typer.Option(25, "--limit"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """List agent traces in an experiment, most recent first.

    An experiment is required because the engine reads traces out of one: there
    are no traces of a workspace at large. `corerun genai experiments` lists
    them.
    """
    if json_output:
        output.set_json(True)
    _init_client()
    from corerun import genai

    found = _called(
        lambda: genai.traces(
            experiment=experiment,
            state=state,
            session=session,
            limit=limit,
            workspace=workspace,
        )
    )

    def render():
        if not found:
            console.print("[dim]No traces in this experiment yet.[/dim]")
            console.print(f"[dim]Export to /v1/traces with OTEL_SERVICE_NAME={experiment}[/dim]")
            return
        table = Table(show_header=True, header_style="bold")
        # Shortened, but only because a short id is now a valid one: `get` and
        # `delete` resolve a prefix. A whole id in an 80-column terminal
        # squeezes every other column to nothing, and the id was the only part
        # of the row that survived -- the wrong trade when the rest of the row
        # is what tells you which trace you want.
        table.add_column("Trace", no_wrap=True)
        table.add_column("Input")
        table.add_column("State")
        table.add_column("Duration", justify="right")
        table.add_column("Tokens", justify="right")
        table.add_column("Session")
        for t in found:
            table.add_row(
                t.trace_id[:20],
                _short(t.input_preview, 30),
                "[red]ERROR[/red]" if t.state == "ERROR" else (t.state or "-"),
                _duration(t.duration_ms),
                f"{t.total_tokens:,}" if t.total_tokens else "-",
                t.session_id or "-",
            )
        console.print(table)

    output.emit([t.__dict__ for t in found], render)


# The eighths, so a bar can end part way through a cell. A span that ran for a
# fifth of a column still gets drawn rather than rounded away to nothing.
_EIGHTHS = " \u258f\u258e\u258d\u258c\u258b\u258a\u2589\u2588"


def _bar(start: float, end: float, width: int) -> str:
    """One span drawn on the trace's clock, as a bar in `width` columns.

    Position carries as much as length does: two slow steps that overlapped are
    a different problem from two that ran one after the other, and a list of
    durations cannot tell you which you have.
    """
    if width < 4:
        return ""
    start = max(0.0, min(1.0, start))
    end = max(start, min(1.0, end))

    left = start * width
    right = end * width
    cells = []
    for i in range(width):
        covered = min(right, i + 1) - max(left, i)
        if covered <= 0:
            cells.append(" ")
        else:
            cells.append(_EIGHTHS[max(1, min(8, round(covered * 8)))])
    drawn = "".join(cells)
    # A span too short to fill an eighth still happened, so it gets a mark.
    return drawn if drawn.strip() else " " * min(int(left), width - 1) + "\u258f"


# Past this the columns drift apart rather than becoming easier to read.
LAYOUT_MAX_WIDTH = 104


def _span_lines(detail, width: int):
    """The span tree as lines: guides, name, timings, and a bar.

    Drawn by hand rather than with rich's Tree because the columns have to line
    up across depths, and a tree puts its guides before the label -- so a label
    padded to a fixed width still starts in a different column on every level.
    """
    children: dict = {}
    for span in detail.spans:
        children.setdefault(span.parent_span_id, []).append(span)
    known = {s.span_id for s in detail.spans}
    # A span whose parent is missing is a root: exports are batched, so a child
    # can outrun its parent, and dropping it would hide a step that happened.
    roots = [None] + [p for p in children if p is not None and p not in known]

    ordered = []

    def walk(parent, prefix, depth):
        kids = children.get(parent, [])
        for i, span in enumerate(kids):
            last = i == len(kids) - 1
            joint = "\u2514\u2500 " if last else "\u251c\u2500 "
            ordered.append((prefix + joint if depth else "", span))
            # A root draws no guide, so its children start at the left edge
            # rather than inheriting three columns of padding from a joint
            # that was never drawn.
            below = "" if depth == 0 else prefix + ("   " if last else "\u2502  ")
            walk(span.span_id, below, depth + 1)

    for root in roots:
        walk(root, "", 0)

    if not ordered:
        return None

    def moment(when):
        # A datetime, as the SDK hands it over -- it parses the engine's
        # timestamp once rather than every reader doing it again.
        return when.timestamp() if when is not None else None

    starts = [moment(s.start_time) for _, s in ordered]
    starts = [m for m in starts if m is not None]
    origin = min(starts) if starts else 0.0
    total = max(((detail.duration_ms or 0) / 1000.0), 0.001)

    # Widths: the bar takes what it can, and the name column is only as wide as
    # the longest name in this trace. Padding every name out to the space
    # available leaves a gulf between a short name and its timings, which is
    # the same problem as letting the whole layout fill a wide terminal.
    bar_width = max(10, min(28, width - 52))
    longest = max(len(g + s.name) for g, s in ordered)
    name_width = max(16, min(longest, width - bar_width - 26))

    lines = []
    for guides, span in ordered:
        began = moment(span.start_time)
        offset = ((began - origin) / total) if began is not None else 0.0
        ran = (span.duration_ms or 0) / 1000.0 / total

        label = guides + span.name
        if len(label) > name_width:
            label = label[: name_width - 1] + "\u2026"

        failed = span.status == "ERROR"
        # The opening tag is written inline below, so only the closing one is
        # worth a name. `style` was the other half of a pair that never formed.
        close = "[/bold]" if failed else ""
        mark = "!" if failed else " "
        tokens = f"{span.total_tokens:,}" if span.total_tokens else ""
        lines.append(
            f"{'[bold]' if failed else ''}{mark}{label:<{name_width}}{close}"
            f" [dim]{_duration(span.duration_ms):>9}[/dim]"
            f" [dim]{tokens:>8}[/dim]"
            f" {'[bold]' if failed else ''}{_bar(offset, offset + ran, bar_width)}{close}"
        )
    return lines, name_width, bar_width


def _resolve(trace_id: str, experiment: str, workspace: Optional[str]) -> str:
    """Accept a shortened id and return the whole one.

    A trace id is 32 hex characters, and every place that displays one shortens
    it -- the console shows eight in its chip. Somebody reading an id off a
    screen is holding a prefix, and answering "no trace with that id" to a
    prefix that names exactly one trace is a refusal on a technicality.

    Only a short id costs the extra lookup, and an ambiguous one says so rather
    than picking.
    """
    from corerun import genai

    # A whole id carries the engine's own "tr-" prefix and 32 hex characters.
    if len(trace_id) >= 35:
        return trace_id

    matching = [
        t.trace_id
        for t in _called(
            lambda: genai.traces(experiment=experiment, limit=200, workspace=workspace)
        )
        if t.trace_id.startswith(trace_id)
    ]
    if not matching:
        raise output.fail(f"no trace starting with {trace_id} in experiment {experiment}")
    if len(matching) > 1:
        raise output.fail(
            f"{trace_id} names {len(matching)} traces: " + ", ".join(m[:16] for m in matching[:4])
        )
    return matching[0]


@traces_app.command("get")
def get_trace(
    trace_id: str = typer.Argument(..., help="The trace id, or enough of its start to be unique"),
    experiment: str = typer.Option(..., "--experiment", "-e", help="Which experiment it is in"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """Show a trace and its span tree."""
    if json_output:
        output.set_json(True)
    _init_client()
    from corerun import genai

    detail = _called(
        lambda: genai.trace(_resolve(trace_id, experiment, workspace), workspace=workspace)
    )

    def render():
        # Capped rather than the full terminal: on a wide screen the bars end
        # up in the far corner, a gulf away from the names they belong to --
        # and a name beside its bar is the only reason they share a line.
        width = min(console.width, LAYOUT_MAX_WIDTH)

        state = (detail.state or "").upper()
        mark = "\u2717" if state == "ERROR" else "\u25cf" if state == "IN_PROGRESS" else "\u2713"
        label = {"ERROR": "Error", "IN_PROGRESS": "In progress"}.get(state, "Success")

        console.print()
        root = next((sp for sp in detail.spans if not sp.parent_span_id), None)
        console.print(
            f"[bold]{(root.name if root else None) or 'trace'}[/bold]  [dim]{detail.trace_id}[/dim]"
        )

        facts = [f"{mark} {label}", f"[dim]{_duration(detail.duration_ms)}[/dim]"]
        if detail.total_tokens:
            facts.append(f"[dim]{detail.total_tokens:,} tokens[/dim]")
        model = root.attributes.get("gen_ai.request.model") if root else None
        if model:
            facts.append(f"[dim]{model}[/dim]")
        if detail.cost:
            facts.append(f"[dim]${detail.cost:.4f}[/dim]")
        if detail.session_id:
            facts.append(f"[dim]session {detail.session_id}[/dim]")
        console.print("  " + "  [dim]\u00b7[/dim]  ".join(facts))
        console.print()

        drawn = _span_lines(detail, width)
        if not drawn:
            console.print("[dim]This trace has no spans.[/dim]")
            return
        lines, name_width, bar_width = drawn
        console.print(
            f"[dim] {'SPAN':<{name_width}} {'DURATION':>9} {'TOKENS':>8} "
            f"{'0s':<{max(1, bar_width - len(_duration(detail.duration_ms)))}}"
            f"{_duration(detail.duration_ms)}[/dim]"
        )
        for line in lines:
            console.print(line)
        console.print()

    payload = detail.__dict__.copy()
    payload["spans"] = [s.__dict__ for s in detail.spans]
    output.emit(payload, render)


@traces_app.command("delete")
def delete_trace(
    trace_id: str = typer.Argument(..., help="The trace id"),
    experiment: str = typer.Option(..., "--experiment", "-e", help="Which experiment it is in"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """Delete a trace and everything recorded about it."""
    if json_output:
        output.set_json(True)
    _init_client()
    from corerun import genai

    trace_id = _resolve(trace_id, experiment, workspace)
    if not yes:
        output.confirm(f"Delete trace {trace_id} and everything recorded about it?")
    _called(lambda: genai.delete_traces([trace_id], experiment=experiment, workspace=workspace))
    output.emit({"deleted": trace_id}, lambda: console.print(f"[green]Deleted[/green] {trace_id}"))


@sessions_app.command("list")
def list_sessions(
    experiment: str = typer.Option(..., "--experiment", "-e", help="Which experiment's sessions"),
    limit: int = typer.Option(25, "--limit"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """List conversations, most recently active first."""
    if json_output:
        output.set_json(True)
    _init_client()
    from corerun import genai

    found = _called(lambda: genai.sessions(experiment=experiment, limit=limit, workspace=workspace))

    def render():
        if not found:
            console.print(
                "[dim]No sessions. A session appears once traces carry a session id.[/dim]"
            )
            return
        table = Table(show_header=True, header_style="bold")
        table.add_column("Session")
        table.add_column("Traces", justify="right")
        table.add_column("Errors", justify="right")
        table.add_column("Tokens", justify="right")
        table.add_column("Last active")
        for s in found:
            table.add_row(
                s.session_id,
                str(s.trace_count),
                f"[red]{s.error_count}[/red]" if s.error_count else "-",
                f"{s.total_tokens:,}" if s.total_tokens else "-",
                s.last_seen.strftime("%d %b %H:%M") if s.last_seen else "-",
            )
        console.print(table)

    output.emit([s.__dict__ for s in found], render)


@app.command("experiments")
def list_experiments(
    limit: int = typer.Option(50, "--limit"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """List the experiments traces are collected into."""
    if json_output:
        output.set_json(True)
    _init_client()
    from corerun import genai

    found = _called(lambda: genai.experiments(limit=limit, workspace=workspace))

    def render():
        if not found:
            console.print("[dim]No experiments yet. An exporter creates one by naming it.[/dim]")
            return
        table = Table(show_header=True, header_style="bold")
        table.add_column("ID", no_wrap=True)
        table.add_column("Name")
        table.add_column("Last modified")
        for e in found:
            when = (
                __import__("datetime")
                .datetime.fromtimestamp(e.last_update_time / 1000)
                .strftime("%d %b %H:%M")
                if e.last_update_time
                else "-"
            )
            table.add_row(e.experiment_id, e.name, when)
        console.print(table)

    output.emit([e.__dict__ for e in found], render)


@app.command("judges")
def list_judges(
    experiment: str = typer.Option(..., "--experiment", "-e", help="Which experiment's judges"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """List the judges registered against an experiment."""
    if json_output:
        output.set_json(True)
    _init_client()
    from corerun import genai

    found = _called(lambda: genai.judges(experiment=experiment, workspace=workspace))

    def render():
        if not found:
            console.print("[dim]No judges. Add one from the console to score these traces.[/dim]")
            return
        table = Table(show_header=True, header_style="bold")
        table.add_column("Name")
        table.add_column("Version", justify="right")
        table.add_column("Kind")
        for j in found:
            import json as _json

            try:
                blob = _json.loads(j.serialized_scorer or "{}")
            except ValueError:
                blob = {}
            kind = (
                f"built-in · {blob['builtin_scorer_class']}"
                if blob.get("builtin_scorer_class")
                else (
                    "LLM judge"
                    if blob.get("instructions_judge_pydantic_data")
                    else "custom code" if blob.get("call_source") else "scorer"
                )
            )
            table.add_row(j.name, str(j.version), kind)
        console.print(table)

    output.emit([j.__dict__ for j in found], render)


@app.command("evaluations")
def list_evaluations(
    experiment: str = typer.Option(..., "--experiment", "-e", help="Which experiment's runs"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """List the evaluation runs in an experiment, newest first."""
    if json_output:
        output.set_json(True)
    _init_client()
    from corerun import genai

    found = _called(lambda: genai.evaluation_runs(experiment=experiment, workspace=workspace))

    def render():
        if not found:
            console.print("[dim]No evaluation runs yet.[/dim]")
            console.print("[dim]They appear when an evaluation is scored against a dataset.[/dim]")
            return

        # The scores are the reason to look, so they are columns rather than a
        # detail behind an id -- and which scores exist is whatever the runs
        # carry, since a fixed set would be empty for anything judging
        # something else.
        keys = sorted({k for r in found for k in r.metrics})
        table = Table(show_header=True, header_style="bold")
        table.add_column("Run")
        table.add_column("Status")
        table.add_column("Dataset")
        for key in keys:
            table.add_column(key, justify="right")
        for r in found:
            table.add_row(
                r.name,
                "[red]FAILED[/red]" if r.failed else (r.status or "-"),
                r.dataset or "-",
                *[f"{r.metrics[k]:.2f}" if k in r.metrics else "-" for k in keys],
            )
        console.print(table)

    output.emit([r.__dict__ for r in found], render)


@review_app.command("queues")
def list_review_queues(
    experiment: str = typer.Option(..., "--experiment", "-e", help="Which experiment's queues"),
    mine: Optional[str] = typer.Option(None, "--user", "-u", help="Only this person's queues"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """List the review queues in an experiment."""
    if json_output:
        output.set_json(True)
    _init_client()
    from corerun import genai

    found = _called(
        lambda: genai.review_queues(experiment=experiment, user=mine, workspace=workspace)
    )

    def render():
        if not found:
            console.print("[dim]No review queues. Make one with `corerun genai review new`.[/dim]")
            return
        table = Table(show_header=True, header_style="bold")
        table.add_column("Queue")
        table.add_column("Id", no_wrap=True)
        table.add_column("Kind")
        table.add_column("Owner")
        for q in found:
            table.add_row(
                q.name,
                q.queue_id[:20],
                "yours" if q.queue_type == "USER" else "shared",
                q.created_by or "-",
            )
        console.print(table)

    output.emit([q.__dict__ for q in found], render)


@review_app.command("show")
def show_review_queue(
    queue_id: str = typer.Argument(..., help="The queue to read"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """What is in a queue, and what has been decided about each trace."""
    if json_output:
        output.set_json(True)
    _init_client()
    from corerun import genai

    found = _called(lambda: genai.review_items(queue_id, workspace=workspace))

    def render():
        if not found:
            console.print("[dim]Nothing in this queue.[/dim]")
            return
        waiting = sum(1 for i in found if i.pending)
        table = Table(show_header=True, header_style="bold")
        table.add_column("Trace", no_wrap=True)
        table.add_column("Status")
        table.add_column("Reviewed by")
        for i in found:
            table.add_row(
                i.item_id[:20],
                "[yellow]needs review[/yellow]" if i.pending else (i.status or "-").lower(),
                i.completed_by or "-",
            )
        console.print(table)
        console.print(f"[dim]{waiting} of {len(found)} waiting for review.[/dim]")

    output.emit([i.__dict__ for i in found], render)


@review_app.command("new")
def new_review_queue(
    name: str = typer.Argument(..., help="What to call it"),
    experiment: str = typer.Option(
        ..., "--experiment", "-e", help="Which experiment it belongs to"
    ),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """Make a review queue."""
    _init_client()
    from corerun import genai

    made = _called(
        lambda: genai.create_review_queue(name, experiment=experiment, workspace=workspace)
    )
    console.print(f"[green]Created[/green] {made.name} [dim]{made.queue_id}[/dim]")


@review_app.command("add")
def add_to_review_queue(
    queue_id: str = typer.Argument(..., help="The queue to add to"),
    trace_ids: List[str] = typer.Argument(..., help="Traces to queue"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """Queue traces for somebody to read."""
    _init_client()
    from corerun import genai

    _called(lambda: genai.add_to_review(queue_id, list(trace_ids), workspace=workspace))
    n = len(trace_ids)
    console.print(f"[green]Queued[/green] {n} trace{'' if n == 1 else 's'} for review")


@review_app.command("decide")
def decide_review_item(
    queue_id: str = typer.Argument(..., help="The queue the trace is in"),
    trace_id: str = typer.Argument(..., help="The trace decided about"),
    status: str = typer.Argument(..., help="complete, declined or pending"),
    by: Optional[str] = typer.Option(None, "--by", help="Who reviewed it; required to complete"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """Record a decision about a trace in a queue."""
    _init_client()
    from corerun import genai

    _called(lambda: genai.review(queue_id, trace_id, status, by=by, workspace=workspace))
    console.print(f"[green]Recorded[/green] {status.lower()} for {trace_id[:20]}")


@issues_app.command("detect")
def detect_issues(
    experiment: str = typer.Argument(..., help="The experiment id whose traces to read"),
    endpoint: str = typer.Option(..., "--endpoint", "-e", help="The endpoint that judges"),
    categories: Optional[str] = typer.Option(
        None,
        "--categories",
        "-c",
        help="Comma-separated. Omit for all six. See `corerun genai issues categories`.",
    ),
    sample: int = typer.Option(25, "--sample", "-n", help="How many recent traces to read"),
    trace: Optional[List[str]] = typer.Option(
        None, "--trace", "-t", help="A specific trace. Repeatable; overrides --sample."
    ),
    wait: bool = typer.Option(False, "--wait", help="Stay until it finishes"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """Read a sample of traces with a model, and file what it finds.

    The findings are written as assessments on the traces, so they appear
    beside every other score rather than in a report of their own.

    It costs model calls: one per trace per category. The numbers are printed
    before the call so the size of that is visible rather than implied.

    Example:
        corerun genai issues detect 13 --endpoint qwen27b -c correctness -n 10
    """
    _init_client()
    from corerun import genai as api

    wanted = [c.strip() for c in categories.split(",") if c.strip()] if categories else list(api.ISSUE_CATEGORIES)
    count = len(trace) if trace else sample
    console.print(
        f"Reading [bold]{count}[/bold] traces against "
        f"[bold]{len(wanted)}[/bold] categories — {count * len(wanted)} judgements on {endpoint}."
    )

    started = _called(
        lambda: api.detect_issues(
            experiment=experiment,
            endpoint=endpoint,
            trace_ids=list(trace) if trace else None,
            categories=wanted,
            sample=sample,
            workspace=workspace,
        )
    )

    console.print(f"[green]Started[/green] {started.job_id}")
    if started.run_id:
        console.print(f"  run      {started.run_id}")
    console.print(f"  progress corerun genai issues show {started.job_id}")

    if not wait:
        return

    import time

    while True:
        time.sleep(10)
        state = _called(lambda: api.detection(started.job_id, workspace=workspace))
        if not state.finished:
            continue
        _print_detection(state)
        # A failed detection is a failed command: something scripting this
        # needs to hear about it in the exit code, not only on the screen.
        raise typer.Exit(1 if state.failed else 0)


@issues_app.command("show")
def show_detection(
    job_id: str = typer.Argument(..., help="The job id from `issues detect`"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """How a detection is getting on, and what it found."""
    _init_client()
    from corerun import genai as api

    state = _called(lambda: api.detection(job_id, workspace=workspace))
    _print_detection(state)
    if state.failed:
        raise typer.Exit(1)


def _print_detection(state) -> None:
    console.print(f"[bold]{state.job_id}[/bold]  {state.status or 'unknown'}")
    if state.run_id:
        console.print(f"  run    {state.run_id}")
    if state.error:
        # The engine's own sentence. The useful part is usually at the end --
        # which endpoint could not be reached, which model was refused -- so it
        # is printed rather than replaced with a summary of it.
        console.print(f"[red]Error:[/red] {state.error}")
        return
    if state.result:
        console.print(state.result if isinstance(state.result, str) else str(state.result))
    elif state.finished:
        console.print("  Nothing was filed. The traces read clean, or none matched.")


@issues_app.command("categories")
def issue_categories():
    """The dimensions a trace can be judged against."""
    from corerun import genai as api

    detail = {
        "correctness": "Factually accurate and grounded in the data it was given",
        "latency": "Answers within acceptable time",
        "execution": "Tool calls and API steps actually complete",
        "adherence": "Follows instructions, constraints and formatting",
        "relevance": "Addresses what was asked, and leaves the asker satisfied",
        "safety": "Avoids harmful, sensitive or inappropriate content",
    }
    table = Table(show_header=True, header_style="bold")
    table.add_column("Category")
    table.add_column("What it looks for")
    for name in api.ISSUE_CATEGORIES:
        table.add_row(name, detail.get(name, ""))
    console.print(table)
