"""
Model Registry CLI commands
"""

import typer
from rich.console import Console

from corerun.cli import output
from rich.table import Table
from typing import Optional, List
import json as json_lib

console = output.console
app = typer.Typer(help="Model registry commands")


def _init_client():
    """Initialize client, handling errors gracefully"""
    try:
        from corerun import init
        return init()
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        console.print("Run 'corerun login' to authenticate")
        raise typer.Exit(1)


def _stage_style(stage: str) -> str:
    """Get style for model stage"""
    styles = {
        "none": "dim",
        "staging": "yellow",
        "production": "green",
        "archived": "dim",
    }
    return styles.get(stage.lower(), "white")


def _format_size(bytes_size: int) -> str:
    """Format size in human-readable format"""
    if bytes_size < 1024:
        return f"{bytes_size} B"
    elif bytes_size < 1024 * 1024:
        return f"{bytes_size / 1024:.1f} KB"
    elif bytes_size < 1024 * 1024 * 1024:
        return f"{bytes_size / (1024 * 1024):.1f} MB"
    else:
        return f"{bytes_size / (1024 * 1024 * 1024):.2f} GB"


# =============================================================================
# Model Commands
# =============================================================================


@app.command("list")
def list_models(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    List registered models.

    Example:
        corerun models list
        corerun models list --json
    """
    _init_client()

    import corerun.registry as registry

    try:
        models = registry.list_models(workspace=workspace)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if json_output:
        output.set_json(True)
    if output.json_mode():
        payload = [m.model_dump(mode="json") for m in models]
        output.emit(payload)
        return

    if output.json_mode():
        output.emit(models)
        return

    if not models:
        console.print("No models found")
        return

    table = Table(title="Registered Models")
    table.add_column("Name", style="cyan")
    table.add_column("Versions", justify="right")
    table.add_column("Latest", justify="right")
    table.add_column("Description", max_width=40)
    table.add_column("Created")

    for model in models:
        table.add_row(
            model.name,
            str(model.version_count),
            str(model.latest_version),
            (model.description or "-")[:40],
            model.created_at.strftime("%Y-%m-%d %H:%M"),
        )

    console.print(table)


@app.command("get")
def get_model(
    name: str = typer.Argument(..., help="Model name"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    Get model details.

    Example:
        corerun models get my-model
    """
    _init_client()

    import corerun.registry as registry

    try:
        model = registry.get_model(name, workspace=workspace)
        versions = registry.list_versions(name, workspace=workspace)
        aliases = registry.list_aliases(name, workspace=workspace)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if json_output:
        output.set_json(True)
    if output.json_mode():
        payload = model.model_dump(mode="json")
        payload["versions"] = [v.model_dump(mode="json") for v in versions]
        payload["aliases"] = [a.model_dump(mode="json") for a in aliases]
        output.emit(payload)
        return

    console.print(f"[bold]Model: {model.name}[/bold]")
    console.print(f"  Description: {model.description or '-'}")
    console.print(f"  Versions: {model.version_count}")
    console.print(f"  Latest Version: {model.latest_version}")
    if model.tags:
        console.print(f"  Tags: {model.tags}")
    console.print(f"  Created: {model.created_at}")

    if aliases:
        console.print("\n[bold]Aliases:[/bold]")
        for alias in aliases:
            console.print(f"  @{alias.alias} -> v{alias.version}")

    if versions:
        console.print("\n[bold]Versions:[/bold]")
        table = Table()
        table.add_column("Version", justify="right")
        table.add_column("Stage")
        table.add_column("Framework")
        table.add_column("Size", justify="right")
        table.add_column("Created")

        for v in versions[:10]:  # Show latest 10
            stage_text = f"[{_stage_style(v.stage)}]{v.stage}[/]"
            table.add_row(
                str(v.version),
                stage_text,
                v.framework or "-",
                _format_size(v.size_bytes),
                v.created_at.strftime("%Y-%m-%d %H:%M"),
            )

        console.print(table)
        if len(versions) > 10:
            console.print(f"  ... and {len(versions) - 10} more versions")


@app.command("create")
def create_model(
    name: str = typer.Argument(..., help="Model name"),
    description: Optional[str] = typer.Option(None, "--description", "-d", help="Description"),
    tags: Optional[List[str]] = typer.Option(None, "--tag", "-t", help="Tags (KEY=VALUE)"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Create a new registered model.

    Example:
        corerun models create my-model --description "My ML model"
        corerun models create my-model -t framework=pytorch -t task=classification
    """
    _init_client()

    import corerun.registry as registry

    # Parse tags
    tag_dict = {}
    if tags:
        for tag in tags:
            if "=" in tag:
                k, v = tag.split("=", 1)
                tag_dict[k] = v

    try:
        model = registry.create_model(
            name=name,
            description=description,
            tags=tag_dict if tag_dict else None,
            workspace=workspace,
        )
        console.print(f"[green]Created model '{model.name}'[/green]")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@app.command("delete")
def delete_model(
    name: str = typer.Argument(..., help="Model name"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    force: bool = typer.Option(False, "--yes", "-y", "--force", "-f", help="Do not ask for confirmation"),
):
    """
    Delete a model and all its versions.

    Example:
        corerun models delete old-model
        corerun models delete old-model --force
    """
    _init_client()

    import corerun.registry as registry

    if not force:
        output.confirm(f"Delete model '{name}' and all versions?")

    try:
        registry.delete_model(name, workspace=workspace)
        console.print(f"[green]Deleted model '{name}'[/green]")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


# =============================================================================
# Version Commands
# =============================================================================


@app.command("versions")
def list_versions(
    name: str = typer.Argument(..., help="Model name"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    List model versions.

    Example:
        corerun models versions my-model
    """
    _init_client()

    import corerun.registry as registry

    try:
        versions = registry.list_versions(name, workspace=workspace)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if json_output:
        output.set_json(True)
    if output.json_mode():
        payload = [v.model_dump(mode="json") for v in versions]
        output.emit(payload)
        return

    if not versions:
        console.print(f"No versions found for model '{name}'")
        return

    table = Table(title=f"Versions of '{name}'")
    table.add_column("Version", justify="right")
    table.add_column("Stage")
    table.add_column("Framework")
    table.add_column("Size", justify="right")
    table.add_column("Run ID", no_wrap=True)
    table.add_column("Description", max_width=30)
    table.add_column("Created")

    for v in versions:
        stage_text = f"[{_stage_style(v.stage)}]{v.stage}[/]"
        table.add_row(
            str(v.version),
            stage_text,
            v.framework or "-",
            _format_size(v.size_bytes),
            v.run_id or "-",
            (v.description or "-")[:30],
            v.created_at.strftime("%Y-%m-%d %H:%M"),
        )

    console.print(table)


@app.command("pull")
def pull_model(
    reference: str = typer.Argument(
        ..., help="Model name, name@version, or a repository URL"
    ),
    dest: Optional[str] = typer.Argument(None, help="Destination directory"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    revision: Optional[str] = typer.Option(
        None, "--revision", help="Commit to check out (defaults to the version's own)"
    ),
):
    """
    Pull a model's files out of the registry.

    Fetches the repository and its weights to a local directory, which is what
    a model loader wants to be pointed at.

    Example:
        corerun models pull Qwen3-0.6B-FP8
        corerun models pull Qwen3-0.6B-FP8@1 ./qwen
        corerun models pull https://corerun.ai/api/v1/git/<tenant>/<model>.git
    """
    from pathlib import Path

    from corerun.config import get_config
    from corerun.pull import PullError, public_repo_url

    _init_client()
    config = get_config()

    name, _, requested_version = reference.partition("@")
    commit = revision or ""

    try:
        if "://" in reference:
            # A URL is taken as given: the caller has already said exactly which
            # repository they mean, and there may be no registry record for it.
            repo_url = reference
            label = reference.rsplit("/", 1)[-1].removesuffix(".git")
        else:
            import corerun.registry as registry

            versions = registry.list_versions(name, workspace=workspace)
            if not versions:
                console.print(f"[red]Error:[/red] {name} has no versions")
                raise typer.Exit(1)

            if requested_version:
                wanted = int(requested_version)
                version = next((v for v in versions if v.version == wanted), None)
                if version is None:
                    console.print(f"[red]Error:[/red] {name} has no version {wanted}")
                    raise typer.Exit(1)
            else:
                # The newest version that finished importing, not simply the
                # newest. A version whose import failed leaves a record with no
                # commit and an empty repository, and picking it fails in git
                # with "pathspec 'HEAD' did not match any file(s)" -- which
                # reads as a broken tool rather than an unfinished import.
                complete = [v for v in versions if v.commit_sha]
                if not complete:
                    console.print(
                        f"[red]Error:[/red] no version of {name} finished importing.\n"
                        "Check 'corerun models versions " + name + "' and re-import."
                    )
                    raise typer.Exit(1)
                version = max(complete, key=lambda v: v.version)

            if not version.commit_sha:
                console.print(
                    f"[red]Error:[/red] {name} v{version.version} did not finish importing, "
                    "so there is nothing to pull yet."
                )
                raise typer.Exit(1)

            if not version.repo_url:
                console.print(
                    f"[red]Error:[/red] {name} v{version.version} is not stored in the "
                    "git plane, so there is no repository to pull.\n"
                    "Its artifacts are in object storage; use 'corerun models get' to see where."
                )
                raise typer.Exit(1)

            repo_url = public_repo_url(version.repo_url, config.api_url)
            commit = commit or version.commit_sha
            label = f"{name} v{version.version}"

        target = Path(dest) if dest else Path(name.rsplit("/", 1)[-1])

        console.print(f"Pulling [bold]{label}[/bold] to {target}")

        _run_pull(repo_url, target, config.auth_token, commit)

    except PullError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


def _run_pull(repo_url: str, target, api_key: str, commit: str) -> None:
    """Drive a pull, rendering its progress."""
    from rich.progress import (
        BarColumn,
        DownloadColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeRemainingColumn,
        TransferSpeedColumn,
    )

    from corerun.pull import pull as pull_repo

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        # git-lfs reports bytes per file, so each file gets its own bar. That is
        # also what makes a stalled transfer legible: the file it stalled on is
        # named, rather than a single total that stops moving.
        bars = {}
        setup = progress.add_task("Preparing", total=None)

        def report(event):
            if event.stage in ("clone", "checkout"):
                progress.update(setup, description=event.stage.capitalize())
                return
            if event.stage == "done":
                progress.update(setup, description="Done", completed=1, total=1)
                return

            key = event.detail
            if key not in bars:
                progress.update(setup, visible=False)
                bars[key] = progress.add_task(
                    _short_name(key), total=event.bytes_total or None
                )
            progress.update(bars[key], completed=event.bytes_done, total=event.bytes_total or None)

        model_dir = pull_repo(repo_url, target, api_key, revision=commit, report=report)

    console.print(f"[green]Pulled to {model_dir}[/green]")
    console.print(f"  Serve it with: corerun inference deploy --model-path {model_dir}")


def _short_name(path: str) -> str:
    """Keep a file name readable in a progress bar."""
    name = path.rsplit("/", 1)[-1]
    return name if len(name) <= 32 else name[:29] + "..."


@app.command("push")
def push_model(
    name: str = typer.Argument(..., help="Model name"),
    source: str = typer.Argument(..., help="Directory of model files"),
    description: Optional[str] = typer.Option(None, "--description", "-d", help="Version description"),
    framework: Optional[str] = typer.Option(None, "--framework", "-f", help="Framework (transformers, pytorch, ...)"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Push a directory of model files as a new version.

    Creates the model if it does not exist yet. Weights are stored as LFS
    objects, so pushing a large checkpoint does not bloat the history.

    Example:
        corerun models push my-llm ./checkpoint
        corerun models push my-llm ./checkpoint -d "after 3 epochs" -f transformers
    """
    from pathlib import Path

    from corerun.config import get_config
    from corerun.pull import PullError, public_repo_url, push as push_repo

    _init_client()
    config = get_config()

    import corerun.registry as registry

    local = Path(source)
    if not local.is_dir():
        console.print(f"[red]Error:[/red] Not a directory: {source}")
        raise typer.Exit(1)

    try:
        # Creating the model is also what provisions its repository, so this has
        # to happen before there is anywhere to push to.
        try:
            registry.get_model(name, workspace=workspace)
        except Exception:
            console.print(f"Creating model [bold]{name}[/bold]")
            registry.create_model(name=name, description=description, workspace=workspace)

        versions = registry.list_versions(name, workspace=workspace)
        repo_url = next((v.repo_url for v in versions if v.repo_url), "")
        if not repo_url:
            # No version has recorded one yet, so it is derived the same way the
            # platform does: one org per tenant, one repo per model.
            tenant = _tenant_id(workspace)
            repo_url = config.api_url.rstrip("/") + f"/git/{tenant}/{name}.git"
        else:
            repo_url = public_repo_url(repo_url, config.api_url)

        size = sum(f.stat().st_size for f in local.rglob("*") if f.is_file())
        console.print(f"Pushing {_format_size(size)} from {local} to [bold]{name}[/bold]")

        with console.status("Uploading..."):
            commit = push_repo(
                repo_url,
                local,
                dest_subdir="model",
                api_key=config.auth_token,
                message=description or f"push {local.name}",
            )

        version = registry.create_version(
            model_name=name,
            storage_path="",
            repo_url=repo_url,
            commit_sha=commit,
            framework=framework,
            description=description,
            size_bytes=size,
            workspace=workspace,
        )
        console.print(f"[green]Pushed {name} v{version.version}[/green] at {commit[:12]}")
        console.print(f"  Pull it with: corerun models pull {name}@{version.version}")

    except PullError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


def _tenant_id(workspace: Optional[str]) -> str:
    """Find the tenant whose registry this model belongs to."""
    from corerun.config import get_client

    me = get_client().get("/auth/me", workspace=workspace)
    tenant = me.get("tenant_id") or me.get("user", {}).get("tenant_id")
    if not tenant:
        raise Exception("Could not determine your tenant; pass a repository URL instead")
    return tenant


# =============================================================================
# Alias Commands
# =============================================================================


STAGES = ("none", "staging", "production", "archived")


@app.command("stage")
def set_stage(
    name: str = typer.Argument(..., help="Model name"),
    version: int = typer.Argument(..., help="Version to move"),
    stage: str = typer.Argument(..., help=f"One of: {', '.join(STAGES)}"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask"),
):
    """
    Move a version to a stage.

    A stage says how far through its life a version is. An alias says which
    version something should use. They are different questions: promoting to
    production does not repoint @champion, and repointing @champion does not
    promote anything.

    Example:
        corerun models stage my-model 5 staging
        corerun models stage my-model 5 production
        corerun models stage my-model 2 archived
    """
    if stage not in STAGES:
        console.print(f"[red]Error:[/red] stage must be one of: {', '.join(STAGES)}")
        raise typer.Exit(1)

    _init_client()

    import corerun.registry as registry

    # Production is what live traffic gets. Asked about rather than done,
    # because a wrong version here is not a wrong local file.
    if stage == "production" and not yes:
        current = ""
        try:
            for v in registry.list_versions(name, workspace=workspace):
                if getattr(v, "stage", "") == "production":
                    current = f" (replacing v{v.version})"
                    break
        except Exception:
            pass
        output.confirm(
            f"Move {name} v{version} to production{current}?"
        )

    try:
        registry.set_version_stage(name, version, stage, workspace=workspace)
        console.print(f"[green]{name} v{version} -> {stage}[/green]")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@app.command("alias")
def set_alias(
    name: str = typer.Argument(..., help="Model name"),
    alias: str = typer.Argument(..., help="Alias name"),
    version: int = typer.Argument(..., help="Version to point to"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Set an alias to point to a version.

    Example:
        corerun models alias my-model champion 5
        corerun models alias my-model challenger 6
    """
    _init_client()

    import corerun.registry as registry

    try:
        registry.set_alias(name, alias, version, workspace=workspace)
        console.print(f"[green]Set @{alias} -> v{version}[/green]")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@app.command("unalias")
def delete_alias(
    name: str = typer.Argument(..., help="Model name"),
    alias: str = typer.Argument(..., help="Alias name"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Remove an alias.

    Example:
        corerun models unalias my-model challenger
    """
    _init_client()

    import corerun.registry as registry

    try:
        registry.delete_alias(name, alias, workspace=workspace)
        console.print(f"[green]Removed @{alias}[/green]")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@app.command("aliases")
def list_aliases(
    name: str = typer.Argument(..., help="Model name"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    List model aliases.

    Example:
        corerun models aliases my-model
    """
    _init_client()

    import corerun.registry as registry

    try:
        aliases = registry.list_aliases(name, workspace=workspace)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if json_output:
        output.set_json(True)
    if output.json_mode():
        payload = [a.model_dump(mode="json") for a in aliases]
        output.emit(payload)
        return

    if not aliases:
        console.print(f"No aliases found for model '{name}'")
        return

    table = Table(title=f"Aliases for '{name}'")
    table.add_column("Alias", style="cyan")
    table.add_column("Version", justify="right")
    table.add_column("Created")

    for alias in aliases:
        table.add_row(
            f"@{alias.alias}",
            str(alias.version),
            alias.created_at.strftime("%Y-%m-%d %H:%M"),
        )

    console.print(table)


# =============================================================================
# Inference Commands
# =============================================================================


def _default_model_name(huggingface_id: str) -> str:
    """The registry name for a HuggingFace id, when none was given.

    "meta-llama/Meta-Llama-3-8B" becomes "meta-llama-3-8b". The org is dropped
    because the registry is already scoped to a workspace, and the result is
    lowercased because a name that differs only in case from another is a
    confusion waiting to happen.
    """
    tail = huggingface_id.rstrip("/").split("/")[-1]
    return tail.lower()


@app.command("import")
def import_model(
    model_id: str = typer.Argument(..., help="Source model, e.g. meta-llama/Meta-Llama-3-8B"),
    source: str = typer.Option("huggingface", "--source", "-s", help="Where to import from"),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Registry name (default: from the model id)"),
    revision: Optional[str] = typer.Option(None, "--revision", "-r", help="Branch, tag or commit"),
    token: Optional[str] = typer.Option(None, "--token", help="Token for a gated repository (or set HF_TOKEN)"),
    description: Optional[str] = typer.Option(None, "--description", "-d", help="Version description"),
    local: bool = typer.Option(
        False,
        "--local",
        help="Transfer through this machine instead of the platform",
    ),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Follow progress until the import finishes"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Import a model into the registry.

    The platform does the transfer, so the weights go from the source into your
    workspace's storage without passing through this machine. That matters for
    anything large, and it works from a shell that could not hold the file.

    --local downloads here and pushes, which is what you want when the source is
    reachable from your machine and not from the platform.

    Example:
        corerun model import meta-llama/Meta-Llama-3-8B
        corerun model import meta-llama/Meta-Llama-3-8B --name llama3 -r main
        corerun model import bert-base-uncased --local
    """
    import os
    import time

    if source != "huggingface":
        console.print(f"[red]Unknown source:[/red] {source}")
        console.print("Supported: huggingface")
        raise typer.Exit(1)

    _init_client()

    import corerun.registry as registry

    model_name = name or _default_model_name(model_id)
    hf_token = token or os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")

    if local:
        _import_locally(model_name, model_id, revision, hf_token, description, workspace)
        return

    console.print(f"Importing [bold]{model_id}[/bold] into [bold]{model_name}[/bold]...")
    try:
        started = registry.import_from_huggingface(
            name=model_name,
            huggingface_id=model_id,
            revision=revision,
            hf_token=hf_token,
            description=description,
            workspace=workspace,
        )
    except Exception as e:
        console.print(f"[red]Could not start the import:[/red] {e}")
        raise typer.Exit(1)

    version = started.get("version")
    console.print(f"  Started as version [bold]{version}[/bold].")

    if not wait:
        console.print(f"  Follow it with: corerun model versions {model_name}")
        return

    _follow_import(model_name, version, workspace)


def _follow_import(model_name: str, version, workspace: Optional[str]) -> None:
    """Show progress until the version is ready, or the import fails.

    Progress is written onto the version rather than returned by the request
    that started it, because the transfer outlives that request.
    """
    import time

    import corerun.registry as registry

    last = ""
    while True:
        try:
            v = registry.get_version(model_name, version, workspace=workspace)
        except Exception as e:
            console.print(f"[yellow]Lost track of the import:[/yellow] {e}")
            console.print(f"  Check with: corerun model versions {model_name}")
            raise typer.Exit(1)

        status = (getattr(v, "status", "") or "").upper()
        progress = getattr(v, "progress", None) or {}
        stage = progress.get("stage_label") or progress.get("stage") or status.title()
        percent = progress.get("percent")

        line = f"  {stage}" + (f" {percent:.0f}%" if isinstance(percent, (int, float)) else "")
        if line != last:
            console.print(line, style="dim")
            last = line

        if status == "READY":
            console.print(f"[green]Imported.[/green] {model_name} version {version}")
            return
        if status.startswith("FAILED"):
            message = getattr(v, "status_message", "") or "no reason given"
            console.print(f"[red]Import failed:[/red] {message}")
            raise typer.Exit(1)

        time.sleep(3)


def _import_locally(
    model_name: str,
    model_id: str,
    revision: Optional[str],
    hf_token: Optional[str],
    description: Optional[str],
    workspace: Optional[str],
) -> None:
    """Download here, then push.

    The slower path, and the only one that works when the source is reachable
    from this machine and not from the platform -- a model behind a VPN, or one
    whose licence was accepted with a token that should not leave the laptop.
    """
    import subprocess
    import tempfile
    from pathlib import Path

    if not _have("git") or not _have("git-lfs"):
        console.print("[red]--local needs git and git-lfs on this machine.[/red]")
        console.print("Without them, drop --local and let the platform fetch it.")
        raise typer.Exit(1)

    url = f"https://huggingface.co/{model_id}"
    if hf_token:
        url = f"https://user:{hf_token}@huggingface.co/{model_id}"

    with tempfile.TemporaryDirectory(prefix="corerun-import-") as tmp:
        target = Path(tmp) / "model"
        cmd = ["git", "clone", "--depth", "1"]
        if revision:
            cmd += ["--branch", revision]
        cmd += [url, str(target)]

        console.print(f"Cloning [bold]{model_id}[/bold]...")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            # The token is in the URL, so the error is not safe to print whole.
            detail = result.stderr.replace(hf_token, "***") if hf_token else result.stderr
            console.print(f"[red]Clone failed:[/red] {detail.strip()[:300]}")
            raise typer.Exit(1)

        console.print(f"Pushing to [bold]{model_name}[/bold]...")
        push_model(
            name=model_name,
            source=str(target),
            description=description or f"Imported from {model_id}",
            framework=None,
            workspace=workspace,
        )


def _have(binary: str) -> bool:
    import shutil

    return shutil.which(binary) is not None


@app.command("publish")
def publish_model(
    name: str = typer.Argument(..., help="Model in the registry to publish"),
    to: str = typer.Option(..., "--to", help="Destination on HuggingFace, as org/repo"),
    version: Optional[int] = typer.Option(None, "--version", "-v", help="Which version (default: the latest)"),
    token: Optional[str] = typer.Option(None, "--token", help="HuggingFace write token (or set HF_TOKEN)"),
    public: bool = typer.Option(False, "--public", help="Create the repository public rather than private"),
    message: Optional[str] = typer.Option(None, "--message", "-m", help="Commit message"),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Follow progress until the push finishes"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
):
    """
    Publish a model version to a HuggingFace repository.

    The other direction from 'import': what the registry holds goes out to
    HuggingFace, weights and all. The push runs on the platform, so it does not
    matter whether this machine could hold the model.

    The repository is created if it is not there, private unless --public.
    Publishing the same version twice changes nothing.

        corerun models publish llama-3-8b-ft --to acme/llama-3-8b-ft
    """
    import os

    import corerun.registry as registry

    hf_token = token or os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if not hf_token:
        console.print("[red]Error:[/red] a HuggingFace write token is needed.")
        console.print("  Pass --token, or set HF_TOKEN.")
        raise typer.Exit(1)

    if "/" not in to.strip("/"):
        console.print(f"[red]Error:[/red] --to should be org/repo, not '{to}'.")
        raise typer.Exit(1)

    try:
        started = registry.publish_to_huggingface(
            name,
            huggingface_id=to,
            hf_token=hf_token,
            version=version,
            private=not public,
            message=message,
            workspace=workspace,
        )
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    published_version = started.get("version")
    console.print(f"Publishing {name} v{published_version} to [bold]{to}[/bold]...")

    if not wait:
        console.print(f"  Follow it with: corerun models versions {name}")
        return

    _follow_publish(name, published_version, started.get("url", ""), workspace)


def _follow_publish(model_name: str, version, url: str, workspace: Optional[str]) -> None:
    """Show progress until the push finishes, or fails.

    Progress is on the version rather than in the response, because the push
    outlives the request that started it -- the same reason as the import.
    """
    import time

    import corerun.registry as registry

    last = ""
    while True:
        try:
            v = registry.get_version(model_name, version, workspace=workspace)
        except Exception as e:
            console.print(f"[yellow]Lost track of the push:[/yellow] {e}")
            raise typer.Exit(1)

        status = (getattr(v, "publish_status", "") or "").upper()
        progress = getattr(v, "publish_progress", None) or {}
        stage = progress.get("stage_label") or progress.get("stage") or "Publishing"
        percent = progress.get("percent")

        line = f"  {stage}" + (f" {percent:.0f}%" if isinstance(percent, (int, float)) else "")
        if line != last:
            console.print(line, style="dim")
            last = line

        if status == "PUBLISHED":
            console.print(f"[green]Published.[/green] {url or model_name}")
            return
        if status.startswith("FAILED"):
            console.print(f"[red]Publish failed:[/red] {getattr(v, 'publish_message', '') or 'no reason given'}")
            raise typer.Exit(1)

        time.sleep(2)
