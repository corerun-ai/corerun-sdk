"""
Policy commands: what agents may do with their tools.

A policy is a list of rules in YAML. Policies apply at the organisation's level
(`--org`), a workspace's, and one agent's (`corerun agents policy`); the
strictest rule that matches wins, deny over ask over allow, and a request
nothing allows is refused. These commands are for the first two.
"""

from pathlib import Path
from typing import Optional

import typer
import yaml
from rich.table import Table

from corerun.cli import output

console = output.console
app = typer.Typer(help="Policies: what agents may do with their tools")

ORG = typer.Option(False, "--org", help="The organisation's policies (organisation administrators)")
RULES = typer.Option(..., "--file", "-f", help="YAML: a list of rules, or a mapping with 'rules'", exists=True, dir_okay=False)


def _init_client():
    try:
        from corerun import init
        return init()
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        console.print("Run 'corerun login' to authenticate")
        raise typer.Exit(1)


def _rules(path: Path) -> list:
    import corerun.policies as policies

    try:
        return policies.load_rules(path.read_text(encoding="utf-8"))
    except (ValueError, yaml.YAMLError) as e:
        raise output.fail(f"{path}: {e}")


def _find(name: str, org: bool):
    import corerun.policies as policies

    try:
        return policies.find(name, org=org)
    except Exception as e:
        raise output.fail(str(e))


@app.command("list")
def list_policies(org: bool = ORG):
    """
    The policies that apply here: the organisation's and this workspace's.

    Example:
        corerun policies list
    """
    _init_client()
    import corerun.policies as policies

    try:
        found = policies.list(org=org)
    except Exception as e:
        raise output.fail(str(e))

    def render():
        if not found:
            console.print("No policies. Agents may only read, unless an agent's own policy says more.")
            return
        table = Table(title="Policies")
        table.add_column("Name")
        table.add_column("Level")
        table.add_column("Rules", justify="right")
        table.add_column("Enabled")
        table.add_column("Description")
        for p in found:
            table.add_row(p.name, p.level, str(len(p.rules)), "yes" if p.enabled else "[dim]no[/dim]", p.description or "")
        console.print(table)

    output.emit([p.model_dump() for p in found], render)


@app.command("get")
def get_policy(name: str = typer.Argument(..., help="Policy name or id"), org: bool = ORG):
    """
    Show a policy's rules, as YAML that `create -f` and `update -f` read back.

    Example:
        corerun policies get careful > careful.yaml
    """
    _init_client()
    p = _find(name, org)
    output.emit(p.model_dump(), lambda: console.print(
        yaml.safe_dump({"rules": p.rules}, sort_keys=False, default_flow_style=False).rstrip(),
        markup=False, highlight=False,
    ))


@app.command("create")
def create_policy(
    name: str = typer.Argument(..., help="A name for it"),
    file: Path = RULES,
    description: Optional[str] = typer.Option(None, "--description", "-d"),
    disabled: bool = typer.Option(False, "--disabled", help="Keep it, but do not apply it yet"),
    org: bool = ORG,
):
    """
    Add a policy from a YAML file of rules.

    A rule: name, effect (allow, ask or deny), match (field: [globs]),
    optionally except and message. Fields: adapter, action, group, resource,
    subresource, namespace, name, labels, flags, connector, agent.

    Example:
        corerun policies create careful -f careful.yaml
        corerun policies create guardrails -f guardrails.yaml --org
    """
    rules = _rules(file)
    _init_client()
    import corerun.policies as policies

    try:
        p = policies.create(name, rules, description=description, enabled=not disabled, org=org)
    except Exception as e:
        raise output.fail(str(e))
    output.emit(p.model_dump(), lambda: console.print(f"[green]Added[/green] {p.name} ({p.level}, {len(p.rules)} rules)"))


@app.command("update")
def update_policy(
    name: str = typer.Argument(..., help="Policy name or id"),
    file: Optional[Path] = typer.Option(None, "--file", "-f", help="Replace its rules with this YAML", exists=True, dir_okay=False),
    rename: Optional[str] = typer.Option(None, "--name", help="A new name"),
    description: Optional[str] = typer.Option(None, "--description", "-d"),
    enable: Optional[bool] = typer.Option(None, "--enable/--disable", help="Apply it, or keep it without applying"),
    org: bool = ORG,
):
    """
    Change a policy; what is not given stays.

    Example:
        corerun policies update careful -f careful.yaml
        corerun policies update careful --disable
    """
    rules = _rules(file) if file else None
    _init_client()
    import corerun.policies as policies

    p = _find(name, org)
    try:
        p = policies.update(p.id, name=rename, rules=rules, description=description, enabled=enable, org=org)
    except Exception as e:
        raise output.fail(str(e))
    output.emit(p.model_dump(), lambda: console.print(
        f"[green]Updated[/green] {p.name} ({len(p.rules)} rules, {'enabled' if p.enabled else 'disabled'})"
    ))


@app.command("delete")
def delete_policy(
    name: str = typer.Argument(..., help="Policy name or id"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask"),
    org: bool = ORG,
):
    """
    Remove a policy.

    Example:
        corerun policies delete careful --yes
    """
    _init_client()
    import corerun.policies as policies

    p = _find(name, org)
    if not yes:
        output.confirm(f"Remove the policy {p.name}?")
    try:
        policies.delete(p.id, org=org)
    except Exception as e:
        raise output.fail(str(e))
    output.emit({"deleted": p.id}, lambda: console.print(f"Removed {p.name}."))


_EFFECT_WORDS = {"allow": "[green]Allowed[/green]", "ask": "[yellow]Asked first[/yellow]", "deny": "[red]Refused[/red]"}


def _layer_words(layer: str) -> str:
    if layer == "floor":
        return "the platform's floor"
    if layer == "policy":
        return "no rule allows it"
    if layer in ("agent", "agent:default"):
        return "the agent's own rules" if layer == "agent" else "the agent's default (read only)"
    kind, _, name = layer.partition(":")
    return f"the {'organisation' if kind == 'organization' else kind}'s policy “{name}”" if name else layer


@app.command("try")
def try_command(
    command: str = typer.Argument(..., help='A kubectl or helm command, quoted: "kubectl delete deploy api -n prod"'),
    agent: Optional[str] = typer.Option(None, "--agent", "-a", help="Decide as this agent's request (name or id)"),
    connector: Optional[str] = typer.Option(None, "--connector", "-c", help="Which of the agent's clusters (when it has several)"),
    file: Optional[Path] = typer.Option(
        None, "--file", "-f", exists=True, dir_okay=False,
        help="Unsaved rules (YAML) to try: the agent's own with --agent, else a new workspace policy",
    ),
):
    """
    What a policy would decide for a command, without running it.

    The platform's policy engine decides, with every rule that applies --
    the organisation's, the workspace's, the agent's own with --agent, and
    the platform's floor. Nothing is sent to a cluster.

    Example:
        corerun policies try "kubectl delete deploy api -n prod" --agent sre
        corerun policies try "kubectl scale deploy web --replicas 3 -n dev" -f careful.yaml
    """
    import corerun.kubectl as kubectl

    try:
        request, note = kubectl.parse(command)
    except kubectl.ParseError as e:
        raise output.fail(str(e))
    draft_rules = _rules(file) if file else None
    _init_client()
    import corerun.policies as policies

    try:
        if agent:
            import corerun.agents as agents
            import corerun.connectors as connectors
            from corerun.cli import resolve

            agent_id = resolve.by_name_or_id(agent, lambda: agents.list(), "agent")
            given = [t["connector"] for t in agents.tools(agent_id) if t.get("enabled") and (t.get("connector") or {}).get("kind") == "kubernetes"]
            if connector:
                c = connectors.find(connector)
                connector_id = c.id
            elif len(given) == 1:
                connector_id = given[0]["id"]
            elif not given:
                raise output.fail(f"{agent} is given no Kubernetes cluster; nothing it runs reaches one")
            else:
                raise output.fail("it has several clusters; name one with --connector: " + ", ".join(g["name"] for g in given))
            result = policies.explain(
                request, agent_id=agent_id, connector_id=connector_id,
                draft={"policy": "agent", "rules": draft_rules} if draft_rules is not None else None,
            )
        else:
            rules = []
            for p in policies.list():
                if p.enabled and p.level in ("organization", "workspace"):
                    rules += [dict(r, policy=f"{p.level}:{p.name}") for r in p.rules]
            if draft_rules is not None:
                rules += [dict(r, policy="workspace:draft") for r in draft_rules]
            result = policies.explain(request, rules=rules)
    except typer.Exit:
        raise
    except Exception as e:
        raise output.fail(str(e))

    def render():
        console.print(f"The request: {kubectl.describe(request)}" + (f" [dim]({note})[/dim]" if note else ""))
        rule = f", rule “{result.get('rule')}”" if result.get("rule") else ""
        console.print(f"{_EFFECT_WORDS.get(result.get('effect'), result.get('effect'))} -- decided by {_layer_words(result.get('layer', ''))}{rule}")
        if result.get("message"):
            console.print(f"  {result['message']}")
        console.print("[dim]Nothing was sent to a cluster.[/dim]")

    output.emit({"request": request, "note": note, **result}, render)
