"""
Custom MCP server commands.

A custom server is built from the APIs it calls and run by the platform: each
tool's call is made to its API with that API's own credential, and agents never
hold either. Or it runs an MCP server's own image (`run`) on a cluster or host
of yours, held to the hosts it is given; its tools are chosen from what it
lists (`tools`, `choose`). Every change is a draft beside the running version
-- tested with `call`, then `publish`-ed -- and `rollback` publishes an earlier
version again.
"""

import json
from typing import List, Optional

import typer
from rich.table import Table

from corerun.cli import output

console = output.console
app = typer.Typer(
    help="Custom MCP servers: built from API descriptions, or run from an image, by the platform"
)


def _init_client():
    try:
        from corerun import init

        return init()
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        console.print("Run 'corerun login' to authenticate")
        raise typer.Exit(1)


def _find(name: str):
    import corerun.mcp_servers as servers

    try:
        return servers.find(name)
    except LookupError as e:
        raise output.fail(str(e))
    except Exception as e:
        raise output.fail(str(e))


def _headers(pairs: Optional[List[str]]) -> Optional[dict]:
    if not pairs:
        return None
    out = {}
    for pair in pairs:
        if "=" not in pair:
            raise output.fail(f"--header takes NAME=VALUE, not {pair!r}")
        k, v = pair.split("=", 1)
        out[k.strip()] = v
    return out


def _pairs(flag: str, pairs: Optional[List[str]]) -> dict:
    out = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise output.fail(f"{flag} takes NAME=VALUE, not {pair!r}")
        k, v = pair.split("=", 1)
        out[k.strip()] = v
    return out


def _version(s) -> str:
    v = f"v{s.running}" if s.running else "not published"
    return v + (f", draft v{s.draft}" if s.draft else "")


def _pick(operations: List[dict], tools: Optional[str], reads: bool) -> List[dict]:
    """The operations named (by tool name or METHOD /path), or every read."""
    if not tools and not reads:
        raise output.fail("Name the operations with --tools, or take every read with --reads")
    wanted = {t.strip() for t in (tools or "").split(",") if t.strip()}
    picked, found = [], set()
    for op in operations:
        key = f"{op['method']} {op['path']}"
        if op["name"] in wanted or key in wanted:
            picked.append(op)
            found.update({op["name"], key})
        elif reads and not op.get("changes"):
            picked.append(op)
    missing = wanted - found
    if missing:
        raise output.fail(f"the description has no operation {', '.join(sorted(missing))}")
    return picked


@app.command("list")
def list_servers():
    """
    List this workspace's custom servers.

    Example:
        corerun mcp-servers list
    """
    _init_client()
    import corerun.mcp_servers as servers

    try:
        found = servers.list()
    except Exception as e:
        raise output.fail(str(e))

    def render():
        if not found:
            console.print(
                "No custom servers. Make one with: corerun mcp-servers create NAME --spec URL --tools ..."
            )
            return
        table = Table(title="Custom MCP servers")
        table.add_column("Name")
        table.add_column("Made from")
        table.add_column("Tools", justify="right")
        table.add_column("Version")
        for s in found:
            if s.kind == "image" and s.image:
                made = f"{s.image['ref']} on {s.image['compute']}"
            else:
                made = ", ".join(a.name + (f" (via {a.via})" if a.via else "") for a in s.apis)
            table.add_row(s.name, made or "-", str(len(s.tools)) if s.running else "-", _version(s))
        console.print(table)

    output.emit([s.model_dump() for s in found], render)


@app.command("get")
def get_server(name: str = typer.Argument(..., help="Server name or id")):
    """
    Show a server: its APIs, its tools, any draft and its history.

    Example:
        corerun mcp-servers get ERP
    """
    _init_client()
    s = _find(name)

    def render():
        console.print(f"[bold]{s.name}[/bold]  [dim]{s.id}[/dim]  {_version(s)}")
        if s.description:
            console.print(f"  {s.description}")
        if s.kind == "image" and s.image:
            img = s.image
            console.print(
                f"  Image: {img['ref']}"
                + (f" @ {img['digest']}" if img.get("digest") else " (by its tag)")
            )
            console.print(
                f"  Command: {' '.join(img.get('command') or [])} ({img.get('transport')})"
            )
            console.print(f"  Runs on: {img.get('compute')} ({img.get('size') or 'small'})")
            reaches = (img.get("hosts") or []) + (img.get("tcp") or [])
            console.print(f"  Reaches: {', '.join(reaches) or 'nothing outside its container'}")
            for st in img.get("settings") or []:
                if st.get("secret"):
                    state = "set" if st["name"] in (s.secrets or []) else "NO VALUE"
                    console.print(f"    {st['name']} = <secret, {state}>")
                else:
                    console.print(f"    {st['name']} = {st.get('value', '')}")
            for label, w in (("running", s.running_workload), ("draft", s.draft_workload)):
                if w:
                    extra = w.get("error") or w.get("progress") or ""
                    console.print(
                        f"  Server ({label}): {w.get('state')}" + (f" -- {extra}" if extra else "")
                    )
            console.print(f"  Tools{' (draft v%d)' % s.draft if s.draft else ''}:")
            for t in s.working:
                console.print(f"    - {t['name']}  \\[{t['default']}]")
            for v in s.versions or []:
                console.print(f"  v{v['number']} {v['state']}: {v.get('note') or ''}")
            return
        console.print("  APIs:")
        for a in s.apis:
            where = (
                "internet"
                if a.location == "internet"
                else (f"via {a.via}" if a.via else "directly")
            )
            console.print(f"    - {a.name}: {a.base_url} ({where}, {a.auth})")
        console.print(f"  Tools{' (draft v%d)' % s.draft if s.draft else ''}:")
        for t in s.working:
            console.print(
                f"    - {t['name']}: {t['method']} {t['api']}{t['path']}  \\[{t['default']}]"
            )
        if s.changes:
            console.print(
                "  The draft changes: " + ", ".join(f"{c['kind']} {c['tool']}" for c in s.changes)
            )
        for v in s.versions or []:
            console.print(f"  v{v['number']} {v['state']}: {v.get('note') or ''}")

    output.emit(s.model_dump(), render)


@app.command("create")
def create_server(
    name: str = typer.Argument(..., help="A name agents see it by"),
    spec: Optional[str] = typer.Option(
        None, "--spec", help="Its first API's OpenAPI or Swagger description: an address or a file"
    ),
    api: Optional[str] = typer.Option(
        None, "--api", help="The API's name (lowercase); from the description's title if omitted"
    ),
    base_url: Optional[str] = typer.Option(
        None, "--base-url", help="Where its calls go; the description's own server if omitted"
    ),
    tools: Optional[str] = typer.Option(
        None,
        "--tools",
        help="Operations to make tools: their names or 'METHOD /path', comma-separated",
    ),
    reads: bool = typer.Option(False, "--reads", help="Make every read operation a tool"),
    via: Optional[str] = typer.Option(
        None, "--via", help="An API in your network: the connected cluster or host that reaches it"
    ),
    direct: bool = typer.Option(
        False,
        "--direct",
        help="An API in your network the platform reaches itself (on-premises only)",
    ),
    auth: str = typer.Option(
        "none", "--auth", help="none, bearer, basic, headers or client_credentials"
    ),
    token: Optional[str] = typer.Option(None, "--token", help="Bearer token (--auth bearer)"),
    header: Optional[List[str]] = typer.Option(
        None, "--header", help="NAME=VALUE (--auth headers; repeatable)"
    ),
    username: Optional[str] = typer.Option(None, "--username", help="--auth basic"),
    password: Optional[str] = typer.Option(None, "--password", help="--auth basic"),
    token_url: Optional[str] = typer.Option(
        None, "--token-url", help="--auth client_credentials: the token endpoint"
    ),
    client_id: Optional[str] = typer.Option(None, "--client-id", help="--auth client_credentials"),
    client_secret: Optional[str] = typer.Option(
        None, "--client-secret", help="--auth client_credentials"
    ),
    scope: Optional[List[str]] = typer.Option(
        None, "--scope", help="--auth client_credentials (repeatable)"
    ),
    description: Optional[str] = typer.Option(None, "--description", "-d"),
):
    """
    Make a custom server from an API's description, with a draft of its first
    version. Test it with `call`, then `publish` it; until then no agent sees it.

    Example:
        corerun mcp-servers create ERP --spec https://erp.example.com/openapi.json --reads --auth bearer --token $T
        corerun mcp-servers create Mail --spec ./mail.yaml --tools send_mail,list_folders --via gb10dgx01-host --auth headers --header X-Api-Key=$K
    """
    _init_client()
    import re

    import corerun.mcp_servers as servers

    if via and direct:
        raise output.fail(
            "an API in your network is reached --via a compute, or --direct: not both"
        )
    if not spec:
        raise output.fail("--spec names the API's description; add calls by hand in the console")
    try:
        doc = servers.read(spec, api=api or "api")
    except Exception as e:
        raise output.fail(f"the description could not be read: {e}")
    api_name = api or (
        re.sub(r"[^a-z0-9]+", "_", (doc.get("title") or "api").lower())
        .strip("_")
        .lstrip("0123456789_")[:32]
        or "api"
    )
    base = base_url or doc.get("base_url")
    if not base:
        raise output.fail("the description names no server; give --base-url")
    picked = _pick(doc.get("operations") or [], tools, reads)
    location = "network" if (via or direct) else "internet"
    made = None
    try:
        made = servers.create(name, description=description)
        servers.add_api(
            made.id,
            api_name,
            base,
            spec=spec,
            location=location,
            via=(via or "") if location == "network" else None,
            auth=auth,
            token=token,
            headers=_headers(header),
            username=username,
            password=password,
            token_url=token_url,
            client_id=client_id,
            client_secret=client_secret,
            scopes=scope,
        )
        s = servers.put_draft(made.id, [{**t, "api": api_name} for t in picked])
    except Exception as e:
        if made is not None:
            try:
                servers.delete(made.id)
            except Exception:
                pass
        raise output.fail(str(e))

    output.emit(
        s.model_dump(),
        lambda: console.print(
            f"[green]✓[/green] Made {s.name} with draft v{s.draft}: {len(picked)} tools calling {api_name}.\n"
            f"  Test:    corerun mcp-servers call {s.name!r} {picked[0]['name']} --args '{{}}'\n"
            f"  Publish: corerun mcp-servers publish {s.name!r}"
        ),
    )


@app.command("catalogue")
def show_catalogue():
    """
    The MCP servers the platform runs from images, and what each needs.

    Example:
        corerun mcp-servers catalogue
    """
    _init_client()
    import corerun.mcp_servers as servers

    try:
        found = servers.catalogue()
    except Exception as e:
        raise output.fail(str(e))

    def render():
        for c in found:
            console.print(
                f"[bold]{c['id']}[/bold]  {c['name']} ({c.get('publisher', '')})"
                f"  [dim]{c['image']}[/dim]"
            )
            console.print(f"  {c.get('description', '')}")
            for f in c.get("fields") or []:
                need = (
                    "required"
                    if f.get("required")
                    else f"default {f['default']!r}" if f.get("default") else "optional"
                )
                kind = ", secret" if f.get("secret") else ""
                console.print(f"    --field {f['key']}=...  {f['label']} ({need}{kind})")

    output.emit(found, render)


@app.command("run")
def run_server(
    name: str = typer.Argument(..., help="A name agents see it by"),
    image: str = typer.Option(
        ..., "--image", help="A catalogue server's id (see `catalogue`), or an image reference"
    ),
    on: str = typer.Option(..., "--on", help="The cluster or host it runs on"),
    field: Optional[List[str]] = typer.Option(
        None, "--field", help="A catalogue server's field, KEY=VALUE (repeatable)"
    ),
    command: Optional[str] = typer.Option(
        None, "--command", help="Your own image: the server's command"
    ),
    transport: str = typer.Option("stdio", "--transport", help="Your own image: stdio, or http"),
    port: Optional[int] = typer.Option(
        None, "--port", help="--transport http: the port it listens on"
    ),
    path: str = typer.Option("/mcp", "--path", help="--transport http: the path it answers at"),
    setting: Optional[List[str]] = typer.Option(
        None, "--set", help="Your own image: a setting, NAME=VALUE (repeatable)"
    ),
    secret: Optional[List[str]] = typer.Option(
        None, "--secret", help="Your own image: a secret setting, NAME=VALUE (repeatable)"
    ),
    host: Optional[List[str]] = typer.Option(
        None, "--host", help="A host:port it reaches over HTTPS (repeatable)"
    ),
    tcp: Optional[List[str]] = typer.Option(
        None, "--tcp", help="A host:port it connects to, a database (repeatable)"
    ),
    size: str = typer.Option("small", "--size", help="small or medium"),
    description: Optional[str] = typer.Option(None, "--description", "-d"),
):
    """
    Run an MCP server's image on a cluster or host of yours, as a custom server
    with a draft of its first version. Once it runs, choose its tools with
    `choose`, test them with `call`, and `publish` it.

    Example:
        corerun mcp-servers run GitHub --image github --on gb10dgx01-host --field token=$T
        corerun mcp-servers run Sales --image postgres --on datacore \\
            --field host=db.internal --field database=sales --field user=ro --field password=$PW
        corerun mcp-servers run Orders --image ghcr.io/acme/orders-mcp:1.4 \\
            --command "orders-mcp --stdio" --on datacore --secret T=$T --host api.acme.com:443
    """
    _init_client()
    import corerun.mcp_servers as servers

    if size not in ("small", "medium"):
        raise output.fail("--size is small or medium")
    catalogued = {c["id"] for c in servers.catalogue()}
    if image in catalogued:
        spec = {"catalogue": image, "fields": _pairs("--field", field), "compute": on, "size": size}
        if command or setting or secret:
            raise output.fail(
                "a catalogue server takes --field;"
                " --command, --set and --secret are for your own image"
            )
    else:
        if not command:
            raise output.fail(f"{image!r} is not in the catalogue; give your image's --command")
        settings = [
            {"name": k, "value": v, "secret": False} for k, v in _pairs("--set", setting).items()
        ]
        settings += [
            {"name": k, "value": v, "secret": True} for k, v in _pairs("--secret", secret).items()
        ]
        spec = {
            "image": image,
            "command": command.split(),
            "transport": transport,
            "settings": settings,
            "compute": on,
            "size": size,
        }
        if transport == "http":
            if not port:
                raise output.fail("--transport http needs the --port it listens on")
            spec.update({"port": port, "path": path})
    if host:
        spec["hosts"] = host
    if tcp:
        spec["tcp"] = tcp
    try:
        s = servers.create_image(name, spec, description=description)
    except Exception as e:
        raise output.fail(str(e))
    output.emit(
        s.model_dump(),
        lambda: console.print(
            f"[green]✓[/green] {s.name}: draft v{s.draft} is starting on {on}.\n"
            f"  Its tools, once it runs: corerun mcp-servers tools {s.name!r}\n"
            f"  Choose them:             corerun mcp-servers choose {s.name!r} --reads"
        ),
    )


@app.command("tools")
def live_tools(
    name: str = typer.Argument(..., help="Server name or id"),
    version: Optional[int] = typer.Option(
        None, "--version", help="A version; the draft's, or the running one, if omitted"
    ),
):
    """
    What an image server lists now: every tool its server offers, read or change.

    Example:
        corerun mcp-servers tools GitHub
    """
    _init_client()
    import corerun.mcp_servers as servers

    s = _find(name)
    n = version or s.draft or s.running
    try:
        found = servers.live_tools(s.id, n)
    except Exception as e:
        raise output.fail(str(e))
    kept = {t["name"] for t in s.working}

    def render():
        table = Table(title=f"{s.name} v{n}: what its server lists")
        table.add_column("Tool")
        table.add_column("Kind")
        table.add_column("Starts as")
        table.add_column("Kept")
        for t in found:
            table.add_row(
                t["name"],
                "changes" if t.get("changes") else "reads",
                t["default"],
                "yes" if t["name"] in kept else "",
            )
        console.print(table)

    output.emit(found, render)


@app.command("choose")
def choose_tools(
    name: str = typer.Argument(..., help="Server name or id"),
    tools: Optional[str] = typer.Option(None, "--tools", help="Tool names, comma-separated"),
    reads: bool = typer.Option(False, "--reads", help="Every tool that only reads"),
    ask: Optional[str] = typer.Option(
        None, "--ask", help="Of those, these asked first (comma-separated)"
    ),
    allow: Optional[str] = typer.Option(
        None, "--allow", help="Of those, these allowed (comma-separated)"
    ),
):
    """
    Choose an image server's tools for its draft, from what its server lists.
    Agents are offered only these; any other is turned off for them.

    Example:
        corerun mcp-servers choose GitHub --reads
        corerun mcp-servers choose Sales --tools list_schemas,execute_sql --ask execute_sql
    """
    _init_client()
    import corerun.mcp_servers as servers

    s = _find(name)
    if s.kind != "image":
        raise output.fail(f"{s.name} is made from APIs: use add-tools")
    try:
        listed = servers.live_tools(s.id, s.draft or s.running)
    except Exception as e:
        raise output.fail(str(e))
    wanted = {t.strip() for t in (tools or "").split(",") if t.strip()}
    if not wanted and not reads:
        raise output.fail("Name the tools with --tools, or take every read with --reads")
    unknown = wanted - {t["name"] for t in listed}
    if unknown:
        raise output.fail(f"its server lists no {', '.join(sorted(unknown))}")
    picked = [t for t in listed if t["name"] in wanted or (reads and not t.get("changes"))]
    if not picked:
        # A server that marks none of its tools read-only (Postgres's does
        # not) offers nothing to --reads; every tool starts as a change.
        raise output.fail(
            "its server marks none of its tools read-only, so --reads takes nothing;"
            " name them with --tools (they start asked first; --allow the ones that only read)"
        )
    for t in picked:
        if t["name"] in {x.strip() for x in (ask or "").split(",")}:
            t["default"] = "ask"
        if t["name"] in {x.strip() for x in (allow or "").split(",")}:
            t["default"] = "allow"
    try:
        s = servers.put_image_draft(s.id, picked)
    except Exception as e:
        raise output.fail(str(e))
    output.emit(
        s.model_dump(),
        lambda: console.print(
            f"[green]✓[/green] {s.name} draft v{s.draft}: {len(picked)} tools.\n"
            f"  Test:    corerun mcp-servers call {s.name!r} {picked[0]['name']} --args '{{}}'\n"
            f"  Publish: corerun mcp-servers publish {s.name!r}"
        ),
    )


@app.command("secret")
def set_secret(
    name: str = typer.Argument(..., help="Server name or id"),
    values: List[str] = typer.Argument(
        ..., help="NAME=VALUE, one or more (an empty value removes one)"
    ),
):
    """
    Set an image server's secret settings. Not a new version: its running server
    and any draft start again with them.

    Example:
        corerun mcp-servers secret GitHub GITHUB_PERSONAL_ACCESS_TOKEN=$NEW_TOKEN
    """
    _init_client()
    import corerun.mcp_servers as servers

    s = _find(name)
    try:
        s = servers.set_secrets(s.id, _pairs("a secret", values))
    except Exception as e:
        raise output.fail(str(e))
    output.emit(
        s.model_dump(),
        lambda: console.print(f"[green]✓[/green] {s.name}: set; its server starts again"),
    )


@app.command("operations")
def list_operations(
    name: str = typer.Argument(..., help="Server name or id"),
    api: str = typer.Option(..., "--api", help="Which of its APIs"),
    refresh: bool = typer.Option(
        False, "--refresh", help="Read the description again from its address"
    ),
):
    """
    List an API's operations, and which are tools already.

    Example:
        corerun mcp-servers operations ERP --api erp --refresh
    """
    _init_client()
    import corerun.mcp_servers as servers

    s = _find(name)
    try:
        doc = servers.operations(s.id, api, refresh=refresh)
    except Exception as e:
        raise output.fail(str(e))
    taken = {f"{t['method']} {t['path']}" for t in s.working if t["api"] == api}

    def render():
        table = Table(title=f"{s.name} · {api}")
        table.add_column("Tool")
        table.add_column("Call")
        table.add_column("Kind")
        table.add_column("")
        for op in doc.get("operations") or []:
            key = f"{op['method']} {op['path']}"
            table.add_row(
                op["name"],
                key,
                "changes" if op.get("changes") else "reads",
                "a tool" if key in taken else "",
            )
        console.print(table)

    output.emit(doc, render)


@app.command("add-tools")
def add_tools(
    name: str = typer.Argument(..., help="Server name or id"),
    api: str = typer.Option(..., "--api", help="Which of its APIs"),
    tools: Optional[str] = typer.Option(
        None, "--tools", help="Operations: names or 'METHOD /path', comma-separated"
    ),
    reads: bool = typer.Option(False, "--reads", help="Every read operation not yet a tool"),
):
    """
    Add operations of one of its APIs to the draft of the next version.

    Example:
        corerun mcp-servers add-tools ERP --api erp --tools list_goods_receipts,block_invoice
    """
    _init_client()
    import corerun.mcp_servers as servers

    s = _find(name)
    doc = servers.operations(s.id, api)
    taken = {f"{t['method']} {t['path']}" for t in s.working if t["api"] == api}
    ops = [
        op for op in (doc.get("operations") or []) if f"{op['method']} {op['path']}" not in taken
    ]
    picked = _pick(ops, tools, reads)
    try:
        s = servers.put_draft(s.id, s.working + [{**t, "api": api} for t in picked])
    except Exception as e:
        raise output.fail(str(e))
    output.emit(
        s.model_dump(),
        lambda: console.print(
            f"[green]✓[/green] Draft v{s.draft} of {s.name}: {len(s.working)} tools ({len(picked)} added)"
        ),
    )


@app.command("add-api")
def add_api(
    name: str = typer.Argument(..., help="Server name or id"),
    api: str = typer.Option(..., "--api", help="A name for the API (lowercase)"),
    base_url: str = typer.Option(..., "--base-url", help="Where its calls go"),
    spec: Optional[str] = typer.Option(
        None, "--spec", help="Its description: an address or a file"
    ),
    via: Optional[str] = typer.Option(
        None, "--via", help="In your network: the connected cluster or host that reaches it"
    ),
    direct: bool = typer.Option(
        False, "--direct", help="In your network, reached by the platform itself (on-premises only)"
    ),
    auth: str = typer.Option(
        "none", "--auth", help="none, bearer, basic, headers or client_credentials"
    ),
    token: Optional[str] = typer.Option(None, "--token"),
    header: Optional[List[str]] = typer.Option(None, "--header", help="NAME=VALUE (repeatable)"),
    username: Optional[str] = typer.Option(None, "--username"),
    password: Optional[str] = typer.Option(None, "--password"),
    token_url: Optional[str] = typer.Option(None, "--token-url"),
    client_id: Optional[str] = typer.Option(None, "--client-id"),
    client_secret: Optional[str] = typer.Option(None, "--client-secret"),
    scope: Optional[List[str]] = typer.Option(None, "--scope"),
):
    """
    Add another API to a server, with its own credential. Then add its
    operations with `add-tools`.

    Example:
        corerun mcp-servers add-api ERP --api coupa --base-url https://acme.coupahost.com/api --spec https://acme.coupahost.com/api/openapi.json --auth bearer --token $T
    """
    _init_client()
    import corerun.mcp_servers as servers

    if via and direct:
        raise output.fail("--via or --direct, not both")
    s = _find(name)
    location = "network" if (via or direct) else "internet"
    try:
        a = servers.add_api(
            s.id,
            api,
            base_url,
            spec=spec,
            location=location,
            via=(via or "") if location == "network" else None,
            auth=auth,
            token=token,
            headers=_headers(header),
            username=username,
            password=password,
            token_url=token_url,
            client_id=client_id,
            client_secret=client_secret,
            scopes=scope,
        )
    except Exception as e:
        raise output.fail(str(e))
    output.emit(
        a.model_dump(),
        lambda: console.print(f"[green]✓[/green] {s.name} calls {a.name} at {a.base_url}"),
    )


@app.command("call")
def call_tool(
    name: str = typer.Argument(..., help="Server name or id"),
    tool: str = typer.Argument(..., help="The tool"),
    args: str = typer.Option("{}", "--args", help="Its arguments, as JSON"),
    version: Optional[int] = typer.Option(
        None, "--version", help="A version; the draft's, or the running one, if omitted"
    ),
):
    """
    Call one tool as you, with its API's credential, as an agent's call would be
    made. Recorded as a test.

    Example:
        corerun mcp-servers call ERP list_invoices --args '{"status": "open"}'
    """
    _init_client()
    import corerun.mcp_servers as servers

    s = _find(name)
    try:
        arguments = json.loads(args)
    except ValueError:
        raise output.fail("--args is JSON")
    try:
        answer = servers.call(s.id, tool, arguments, version=version)
    except Exception as e:
        raise output.fail(str(e))
    result = answer.get("result") or {}

    def render():
        mark = "[red]error[/red]" if result.get("isError") else "[green]ok[/green]"
        console.print(f"v{answer.get('version')} {tool}: {mark} in {answer.get('ms')} ms")
        console.print(
            "\n".join(c.get("text", "") for c in result.get("content") or []), markup=False
        )

    output.emit(answer, render)
    if result.get("isError"):
        raise typer.Exit(1)


@app.command("publish")
def publish_server(name: str = typer.Argument(..., help="Server name or id")):
    """
    Publish the draft: every agent given the server calls it from now on.

    Example:
        corerun mcp-servers publish ERP
    """
    _init_client()
    import corerun.mcp_servers as servers

    s = _find(name)
    try:
        s = servers.publish(s.id)
    except Exception as e:
        raise output.fail(str(e))
    output.emit(
        s.model_dump(), lambda: console.print(f"[green]✓[/green] {s.name} v{s.running} is running")
    )


@app.command("rollback")
def rollback_server(
    name: str = typer.Argument(..., help="Server name or id"),
    version: int = typer.Argument(..., help="The version to go back to"),
):
    """
    Publish an earlier version again, as the newest.

    Example:
        corerun mcp-servers rollback ERP 3
    """
    _init_client()
    import corerun.mcp_servers as servers

    s = _find(name)
    try:
        s = servers.restore(s.id, version)
    except Exception as e:
        raise output.fail(str(e))
    output.emit(
        s.model_dump(),
        lambda: console.print(
            f"[green]✓[/green] {s.name} v{s.running} is v{version}'s tools again"
        ),
    )


@app.command("discard")
def discard_draft(name: str = typer.Argument(..., help="Server name or id")):
    """Throw the draft away; the running version stays."""
    _init_client()
    import corerun.mcp_servers as servers

    s = _find(name)
    try:
        s = servers.discard(s.id)
    except Exception as e:
        raise output.fail(str(e))
    output.emit(
        s.model_dump(), lambda: console.print(f"[green]✓[/green] {s.name}: draft discarded")
    )


@app.command("delete")
def delete_server(
    name: str = typer.Argument(..., help="Server name or id"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask"),
):
    """Delete a server, its versions and its APIs' credentials; agents given it lose it."""
    _init_client()
    import corerun.mcp_servers as servers

    s = _find(name)
    if not yes and not typer.confirm(f"Delete {s.name}? Agents given it lose its tools."):
        raise typer.Exit(1)
    try:
        servers.delete(s.id)
    except Exception as e:
        raise output.fail(str(e))
    output.emit({"deleted": s.id}, lambda: console.print(f"[green]✓[/green] Deleted {s.name}"))
