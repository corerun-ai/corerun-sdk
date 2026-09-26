"""
Dataset CLI commands
"""

import typer
from rich.console import Console

from corerun.cli import output
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from typing import Optional

console = output.console
app = typer.Typer(help="Dataset management commands")


def _init_client():
    """Initialize client, handling errors gracefully"""
    try:
        from corerun import init
        return init()
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        console.print("Run 'corerun login' to authenticate")
        raise typer.Exit(1)


def _format_size(size_bytes: int) -> str:
    """Format bytes to human readable"""
    if size_bytes >= 1024 * 1024 * 1024:
        return f"{size_bytes / (1024**3):.1f} GB"
    elif size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024**2):.1f} MB"
    elif size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"


def _follow_import(ds, workspace: Optional[str], wait: bool, label: str):
    """Report an import that has started, and follow it to the end if asked.

    The server answers an import request as soon as it has written the dataset
    record; the download runs behind that answer. Reading the returned record's
    size at that moment reads the zeros it was created with, which is what this
    used to print -- "Imported dataset 'x', Size: 0 B, Files: 0" for an import
    that had not begun.
    """
    import corerun.datasets as datasets

    if not wait:
        console.print(f"[green]✓[/green] Started importing '{ds.name}'")
        console.print(f"  Mount Path: {ds.mount_path}")
        console.print(f"  Progress: corerun datasets import-status {ds.name}")
        return

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task(f"Importing {label}...", total=None)

        def report(status):
            detail = status.message or status.status
            if status.progress:
                detail = f"{detail} ({status.progress:.0f}%)"
            progress.update(task, description=detail)

        try:
            ds = datasets.wait_for_import(ds.name, workspace=workspace, on_progress=report)
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)

    console.print(f"[green]✓[/green] Imported dataset '{ds.name}'")
    console.print(f"  Size: {_format_size(ds.size_bytes)}")
    console.print(f"  Files: {ds.file_count}")
    console.print(f"  Mount Path: {ds.mount_path}")


@app.command("list")
def list_datasets(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    List all datasets in the workspace.

    Example:
        corerun datasets list
        corerun datasets list --json
    """
    _init_client()

    import corerun.datasets as datasets

    try:
        ds_list = datasets.list(workspace=workspace)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if json_output:
        output.set_json(True)
    if output.json_mode():
        import json
        payload = [d.model_dump(mode="json") for d in ds_list]
        output.emit(payload)
        return

    if not ds_list:
        console.print("No datasets found")
        return

    table = Table(title="Datasets")
    table.add_column("Name", style="cyan")
    table.add_column("Source", style="green")
    table.add_column("Size", justify="right")
    table.add_column("Files", justify="right")
    table.add_column("Mount Path")
    table.add_column("Created")

    for ds in ds_list:
        table.add_row(
            ds.name,
            ds.source,
            _format_size(ds.size_bytes),
            str(ds.file_count),
            ds.mount_path,
            ds.created_at.strftime("%Y-%m-%d %H:%M"),
        )

    console.print(table)


@app.command("get")
def get_dataset(
    name: str = typer.Argument(..., help="Dataset name"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    Get dataset details.

    Example:
        corerun datasets get mnist
    """
    _init_client()

    import corerun.datasets as datasets

    try:
        ds = datasets.get(name, workspace=workspace)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if json_output:
        output.set_json(True)
    if output.json_mode():
        import json
        output.emit(ds.model_dump(mode="json"))
        return

    console.print(f"[bold]Dataset: {ds.name}[/bold]")
    console.print(f"  ID: {ds.id}")
    console.print(f"  Source: {ds.source}")
    console.print(f"  Size: {_format_size(ds.size_bytes)}")
    console.print(f"  Files: {ds.file_count}")
    console.print(f"  Mount Path: {ds.mount_path}")
    if ds.storage_target:
        console.print(f"  Storage Target: {ds.storage_target}")
    if ds.description:
        console.print(f"  Description: {ds.description}")
    console.print(f"  Created: {ds.created_at}")


@app.command("delete")
def delete_dataset(
    name: str = typer.Argument(..., help="Dataset name"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    force: bool = typer.Option(False, "--yes", "-y", "--force", "-f", help="Do not ask for confirmation"),
):
    """
    Delete a dataset.

    Example:
        corerun datasets delete mnist
        corerun datasets delete mnist --force
    """
    _init_client()

    import corerun.datasets as datasets

    if not force:
        output.confirm(f"Delete dataset '{name}'?")

    try:
        datasets.delete(name, workspace=workspace)
        console.print(f"[green]✓[/green] Deleted dataset '{name}'")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@app.command("view")
def view_dataset(
    name: str = typer.Argument(..., help="Dataset name"),
    split: Optional[str] = typer.Option(None, "--split", "-s", help="Data split"),
    page: int = typer.Option(1, "--page", "-p", help="Page number"),
    page_size: int = typer.Option(10, "--size", "-n", help="Rows per page"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    View dataset rows with pagination.

    Example:
        corerun datasets view mnist
        corerun datasets view mnist --split train --page 2
    """
    _init_client()

    import corerun.datasets as datasets

    try:
        viewer = datasets.view(name, split=split, page=page, page_size=page_size, workspace=workspace)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if json_output:
        output.set_json(True)
    if output.json_mode():
        import json
        output.emit(viewer.model_dump(mode="json"))
        return

    console.print(f"[bold]Dataset: {name}[/bold]")
    console.print(f"Split: {viewer.current_split or 'default'} | Splits: {', '.join(viewer.splits)}")
    console.print(f"Page {viewer.page}/{viewer.total_pages} ({viewer.total_rows} total rows)")
    console.print()

    if not viewer.rows:
        console.print("No data")
        return

    # Create table
    table = Table()
    for col in viewer.columns:
        table.add_column(col.name, overflow="fold")

    for row in viewer.rows:
        values = []
        for col in viewer.columns:
            val = row.get(col.name, "")
            # Truncate long values (like base64 images)
            if isinstance(val, str) and len(val) > 50:
                if val.startswith("data:image"):
                    val = "[image]"
                else:
                    val = val[:47] + "..."
            values.append(str(val))
        table.add_row(*values)

    console.print(table)


# =============================================================================
# Import subcommands
# =============================================================================

import_app = typer.Typer(help="Import datasets from various sources")
app.add_typer(import_app, name="import")


@import_app.command("huggingface")
def import_huggingface(
    repo_id: str = typer.Argument(..., help="HuggingFace repo ID (e.g., ylecun/mnist)"),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Dataset name"),
    subset: Optional[str] = typer.Option(None, "--subset", help="Dataset subset"),
    split: Optional[str] = typer.Option(None, "--split", help="Data split"),
    description: Optional[str] = typer.Option(None, "--description", "-d", help="Description"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    no_wait: bool = typer.Option(False, "--no-wait", help="Return as soon as the import starts, without following it"),
):
    """
    Import a dataset from HuggingFace Hub.

    Example:
        corerun datasets import huggingface ylecun/mnist
        corerun datasets import huggingface ylecun/mnist --name my-mnist
        corerun datasets import huggingface squad --subset plain_text
    """
    _init_client()

    import corerun.datasets as datasets

    try:
        ds = datasets.from_huggingface(
            repo_id=repo_id,
            name=name,
            subset=subset,
            split=split,
            description=description,
            workspace=workspace,
        )
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    _follow_import(ds, workspace=workspace, wait=not no_wait, label=repo_id)


@import_app.command("kaggle")
def import_kaggle(
    dataset_id: str = typer.Argument(..., help="Kaggle dataset ID (e.g., uciml/iris)"),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Dataset name"),
    description: Optional[str] = typer.Option(None, "--description", "-d", help="Description"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    no_wait: bool = typer.Option(False, "--no-wait", help="Return as soon as the import starts, without following it"),
):
    """
    Import a dataset from Kaggle.

    Requires KAGGLE_USERNAME and KAGGLE_KEY environment variables.

    Example:
        corerun datasets import kaggle uciml/iris
        corerun datasets import kaggle heptapod/titanic --name titanic
    """
    _init_client()

    import corerun.datasets as datasets

    try:
        ds = datasets.from_kaggle(
            dataset_id=dataset_id,
            name=name,
            description=description,
            workspace=workspace,
        )
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    _follow_import(ds, workspace=workspace, wait=not no_wait, label=dataset_id)


@import_app.command("url")
def import_url(
    url: str = typer.Argument(..., help="URL to download"),
    name: str = typer.Option(..., "--name", "-n", help="Dataset name"),
    description: Optional[str] = typer.Option(None, "--description", "-d", help="Description"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    no_wait: bool = typer.Option(False, "--no-wait", help="Return as soon as the import starts, without following it"),
):
    """
    Import a dataset from a URL.

    Supports direct downloads and archives (zip, tar.gz).

    Example:
        corerun datasets import url https://example.com/data.zip --name my-data
    """
    _init_client()

    import corerun.datasets as datasets

    try:
        ds = datasets.from_url(
            url=url,
            name=name,
            description=description,
            workspace=workspace,
        )
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    _follow_import(ds, workspace=workspace, wait=not no_wait, label=url)


@app.command("files")
def list_files(
    name: str = typer.Argument(..., help="Dataset name"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    List the files a dataset is made of.

    Example:
        corerun datasets files mnist
    """
    _init_client()

    import corerun.datasets as datasets

    try:
        listing = datasets.files(name, workspace=workspace)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if json_output:
        import json
        console.print(json.dumps(listing.model_dump(), indent=2))
        return

    if not listing.files:
        console.print(f"Dataset '{name}' has no files")
        return

    table = Table(title=f"{name} — {listing.total} file(s), {_format_size(listing.size_bytes)}")
    table.add_column("Path")
    table.add_column("Size", justify="right")
    for entry in listing.files:
        table.add_row(entry.path, _format_size(entry.size))
    console.print(table)


@app.command("download")
def download_dataset(
    name: str = typer.Argument(..., help="Dataset name"),
    dest: Optional[str] = typer.Option(None, "--dest", "-d", help="Directory to download into (default: ./<name>)"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    overwrite: bool = typer.Option(False, "--overwrite", help="Fetch files already present at the right size"),
):
    """
    Download a dataset's files.

    The alternative to mounting it: use this when the compute has no mount, or
    when the data should be local.

    Example:
        corerun datasets download mnist
        corerun datasets download mnist --dest /data/mnist
    """
    _init_client()

    import corerun.datasets as datasets

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task(f"Downloading {name}...", total=None)

        def report(path: str, index: int, total: int):
            progress.update(task, description=f"{path} ({index}/{total})")

        try:
            root = datasets.download(
                name, dest=dest, workspace=workspace,
                overwrite=overwrite, on_progress=report,
            )
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)

    console.print(f"[green]✓[/green] Downloaded '{name}' to {root}")


@app.command("import-status")
def import_status(
    name: str = typer.Argument(..., help="Dataset name"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace ID"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    Show how far a dataset import has got.

    Example:
        corerun datasets import-status mnist
    """
    _init_client()

    import corerun.datasets as datasets

    try:
        status = datasets.import_status(name, workspace=workspace)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if json_output:
        import json
        console.print(json.dumps(status.model_dump(), indent=2))
        return

    console.print(f"Status: {status.status}")
    if status.progress is not None:
        console.print(f"Progress: {status.progress:.0f}%")
    if status.message:
        console.print(f"Message: {status.message}")
    if status.error:
        console.print(f"[red]Error:[/red] {status.error}")
