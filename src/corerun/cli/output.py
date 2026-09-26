"""
How commands print.

A CLI gets used two ways: read by a person, and piped into something else. The
same command has to serve both, so every command renders through here rather
than printing directly, and a single global flag decides which form comes out.

JSON mode also has to be *only* JSON. A progress spinner, a heading, or a
"[green]done[/green]" on stdout makes the output unparseable, so in JSON mode
everything that is not the payload goes to stderr, where a person can still see
it and a pipe ignores it.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable, Optional

from rich.console import Console

# Rendered output for a person. In JSON mode this is pointed at stderr so the
# payload on stdout stays machine-readable.
console = Console()

# Diagnostics: progress, warnings, errors. Always stderr, so they never mix into
# piped output whichever mode is in force.
errors = Console(stderr=True)

_json_mode = False
_emitted = False


def set_json(enabled: bool) -> None:
    """Choose the output form for this invocation."""
    global _json_mode
    _json_mode = enabled

    # The Console object is mutated rather than replaced. Modules bind it at
    # import time -- console = output.console -- so rebinding the name here
    # would leave every one of them holding the old object and printing to
    # stdout regardless, which is precisely what JSON mode must not do.
    #
    # Not enabled is None rather than sys.stdout, which is rich's own default
    # and means "the stdout in force when something is written". Naming the
    # object instead freezes whichever stream that was at this moment, so a
    # caller that later replaces stdout -- a redirect, a wrapper, a test
    # harness -- leaves this writing to a stream nobody is reading, and to a
    # closed one it raises.
    console.file = sys.stderr if enabled else None

    if enabled:
        # Every command that has been taught to emit JSON does so through
        # emit(). One that has not would otherwise print its table to stderr and
        # leave stdout empty -- a script would read that as "no results" rather
        # than "this command cannot do that yet", which is the worse of the two
        # failures by far. Saying so costs one line and cannot be mistaken.
        import atexit

        atexit.register(_warn_if_silent)


def _warn_if_silent() -> None:
    if _json_mode and not _emitted:
        json.dump(
            {"error": "--json is not supported for this command yet; "
                      "its output was printed in readable form instead"},
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")


def json_mode() -> bool:
    return _json_mode


def emit(payload: Any, render: Optional[Callable[[], None]] = None) -> None:
    """Print a result: as JSON, or by calling render for a person.

    payload is what the command actually produced -- the thing a script wants.
    render draws the same thing as a table or lines. Commands pass both and let
    the mode decide, rather than each one branching on a flag.
    """
    global _emitted
    if _json_mode:
        _emitted = True
        json.dump(_plain(payload), sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
        return
    if render is not None:
        render()


def _plain(value: Any) -> Any:
    """Reduce pydantic models and their containers to plain data."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def fail(message: str, code: int = 1):
    """Report a failure in the form the caller can use.

    A script checking exit status gets an error document on stdout rather than
    having to parse prose out of stderr; a person gets the prose.
    """
    import typer

    global _emitted
    if _json_mode:
        _emitted = True
        json.dump({"error": message}, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        errors.print(f"[red]Error:[/red] {message}")
    return typer.Exit(code)


def confirm(prompt: str) -> None:
    """Ask before something that cannot be undone, or stop.

    Declined is exit 1, so a script can tell it from success. With no
    terminal to ask on -- an agent, CI, a pipe -- it fails at once, saying
    to pass --yes, rather than reading an empty answer as a "no" that exits
    as if the thing had been done.
    """
    import typer

    if not sys.stdin.isatty():
        errors.print(
            f"[red]Error:[/red] {prompt} This needs confirming and there is no "
            "terminal to ask on; pass --yes to go ahead."
        )
        raise typer.Exit(2)
    if not typer.confirm(prompt):
        errors.print("Cancelled; nothing was changed.")
        raise typer.Exit(1)
