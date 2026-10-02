"""
Notebooks module for corerun SDK.

Provides functions for managing interactive notebook sessions (Jupyter, VSCode).

Usage:
    import corerun

    corerun.init(auth_token="...")

    # Create a Jupyter notebook
    notebook = corerun.notebooks.create(
        name="my-notebook",
        compute_name="dgx-cluster",
        gpu=1,
        datasets=["mnist"],
    )

    # Wait for it to be ready
    notebook = corerun.notebooks.wait_for_running(notebook.id)
    print(f"Open: {notebook.url}")

    # List notebooks
    notebooks = corerun.notebooks.list()

    # Stop a notebook
    corerun.notebooks.stop("abc123")

    # Delete a notebook
    corerun.notebooks.delete("abc123")
"""

import time
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, field_validator
from datetime import datetime

from corerun.config import get_client


class NotebookEnvVar(BaseModel):
    """Environment variable for notebook."""
    name: str
    value: str  # Masked if secret
    is_secret: bool = False
    description: Optional[str] = None


class Notebook(BaseModel):
    """Notebook session information."""

    id: str
    name: str
    status: str  # pending, running, stopped, failed
    compute_name: str
    cluster_id: Optional[str] = None
    image: str
    gpu: float = 0
    profile: Optional[str] = None
    datasets: Optional[List[str]] = []
    workspace_env_vars: Optional[List[str]] = []  # IDs of workspace env vars
    custom_env_vars: Optional[List[NotebookEnvVar]] = []
    url: Optional[str] = None
    error: Optional[str] = None
    visibility: Optional[str] = None  # "personal" or "shared"
    shared_with: Optional[List[str]] = []  # people it is shared with by name
    owner_id: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    # What a start is doing now ("Pulling the image: 45%"), as the compute
    # reports it; empty once it has settled.
    progress: Optional[str] = None
    # A problem the notebook came up with anyway -- its home could not be
    # mounted from storage, most often.
    warning: Optional[str] = None
    # Where the running notebook's home is: "store" (kept in the workspace's
    # storage) or "disk" (the compute's own; not kept).
    home_location: Optional[str] = None

    @property
    def is_running(self) -> bool:
        """Check if notebook is running."""
        return self.status == "running"

    @property
    def is_stopped(self) -> bool:
        """Check if notebook is stopped."""
        return self.status == "stopped"

    @property
    def is_failed(self) -> bool:
        """Check if notebook failed."""
        return self.status == "failed"

    @property
    def is_ready(self) -> bool:
        """Check if notebook is ready to use (running with URL)."""
        return self.status == "running" and self.url is not None

    @field_validator("datasets", "workspace_env_vars", "custom_env_vars", mode="before")
    @classmethod
    def _null_is_empty(cls, v):
        return [] if v is None else v

    def __repr__(self) -> str:
        return f"Notebook(name='{self.name}', status='{self.status}')"


class CreateNotebookEnvVarRequest(BaseModel):
    """Request to create an environment variable for a notebook."""
    name: str
    value: str
    is_secret: bool = False
    description: Optional[str] = None


class CreateNotebookRequest(BaseModel):
    """Request to create a notebook."""
    name: str
    compute_name: str
    cluster_id: Optional[str] = None
    image: Optional[str] = None
    gpu: float = 0
    profile: Optional[str] = None
    datasets: Optional[List[str]] = None
    workspace_env_vars: Optional[List[str]] = None  # IDs of workspace env vars
    custom_env_vars: Optional[List[CreateNotebookEnvVarRequest]] = None
    visibility: Optional[str] = None  # "personal" or "shared"
    storage_class: Optional[str] = None


def _notebook_from_response(data: dict) -> Notebook:
    """Convert API response to Notebook object."""
    custom_env_vars = []
    if "custom_env_vars" in data and data["custom_env_vars"]:
        custom_env_vars = [NotebookEnvVar(**e) for e in data["custom_env_vars"]]

    return Notebook(
        id=data.get("id", ""),
        name=data.get("name", ""),
        status=data.get("status", "pending"),
        compute_name=data.get("compute_name", ""),
        cluster_id=data.get("cluster_id"),
        image=data.get("image", ""),
        gpu=data.get("gpu", 0),
        profile=data.get("profile"),
        datasets=data.get("datasets", []),
        workspace_env_vars=data.get("workspace_env_vars", []),
        custom_env_vars=custom_env_vars,
        url=data.get("url"),
        error=data.get("error"),
        visibility=data.get("visibility"),
        shared_with=data.get("shared_with") or [],
        owner_id=data.get("owner_id", ""),
        created_at=data.get("created_at"),
        updated_at=data.get("updated_at"),
        progress=data.get("progress"),
        warning=data.get("warning"),
        home_location=data.get("home_location"),
    )


def list(
    status: Optional[str] = None,
    workspace: Optional[str] = None,
) -> List[Notebook]:
    """
    List notebooks in the workspace.

    Args:
        status: Filter by status (pending, running, stopped, failed)
        workspace: Workspace ID (uses default if not specified)

    Returns:
        List of Notebook objects

    Example:
        # All notebooks
        notebooks = corerun.notebooks.list()

        # Running notebooks only
        running = corerun.notebooks.list(status="running")
    """
    client = get_client()

    params = {}
    if status:
        params["status"] = status

    response = client.get("/notebooks", params=params, workspace=workspace)
    return [_notebook_from_response(nb) for nb in response.get("notebooks", [])]


def get(notebook_id: str, workspace: Optional[str] = None) -> Notebook:
    """
    Get a notebook by ID.

    Args:
        notebook_id: Notebook ID
        workspace: Workspace ID (uses default if not specified)

    Returns:
        Notebook object

    Example:
        notebook = corerun.notebooks.get("abc123")
        print(f"Status: {notebook.status}")
    """
    client = get_client()
    response = client.get(f"/notebooks/{notebook_id}", workspace=workspace)
    return _notebook_from_response(response)


def create(
    name: str,
    compute_name: str,
    image: Optional[str] = None,
    gpu: float = 0,
    profile: Optional[str] = None,
    datasets: Optional[List[str]] = None,
    workspace_env_vars: Optional[List[str]] = None,
    custom_env_vars: Optional[List[Dict[str, Any]]] = None,
    visibility: Optional[str] = None,
    storage_class: Optional[str] = None,
    wait: bool = False,
    timeout: int = 300,
    workspace: Optional[str] = None,
) -> Notebook:
    """
    Create a new notebook session.

    A notebook is JupyterLab; VS Code opens from it, at <url>/vscode/, so
    there is no type to choose.

    Args:
        name: Notebook name
        compute_name: Compute target name (cluster)
        image: Docker image (the platform's notebook image if not specified)
        gpu: Number of GPUs (0 for CPU-only)
        profile: Resource profile name from cluster
        datasets: List of dataset names to mount
        workspace_env_vars: List of workspace environment variable IDs to include
        custom_env_vars: List of custom env vars [{"name": "VAR", "value": "val", "is_secret": False}]
        visibility: "personal" (only you) or "shared" (the workspace). Defaults to personal.
        storage_class: Storage class for the notebook's volume (Kubernetes)
        wait: Wait for notebook to be running (default: False)
        timeout: Timeout in seconds when waiting (default: 300)
        workspace: Workspace ID (uses default if not specified)

    Returns:
        Notebook object

    Example:
        # Create a Jupyter notebook
        notebook = corerun.notebooks.create(
            name="data-exploration",
            compute_name="dgx-cluster",
            gpu=1,
            datasets=["mnist"],
        )

        # With custom env vars
        notebook = corerun.notebooks.create(
            name="dev-environment",
            compute_name="dgx-cluster",
            gpu=1,
            custom_env_vars=[
                {"name": "DEBUG", "value": "1"},
                {"name": "API_KEY", "value": "secret", "is_secret": True},
            ],
        )

        # Create and wait for it to be ready
        notebook = corerun.notebooks.create(
            name="quick-notebook",
            compute_name="cpu-cluster",
            wait=True,
        )
        print(f"Ready: {notebook.url}")
    """
    client = get_client()

    # Build env vars list if provided
    env_var_requests = None
    if custom_env_vars:
        env_var_requests = [
            CreateNotebookEnvVarRequest(**ev).model_dump()
            for ev in custom_env_vars
        ]

    request = CreateNotebookRequest(
        name=name,
        compute_name=compute_name,
        image=image,
        gpu=gpu,
        profile=profile,
        datasets=datasets,
        workspace_env_vars=workspace_env_vars,
        custom_env_vars=env_var_requests,
        visibility=visibility,
        storage_class=storage_class,
    )

    response = client.post(
        "/notebooks",
        json=request.model_dump(exclude_none=True),
        workspace=workspace,
    )
    notebook = _notebook_from_response(response)

    if wait:
        notebook = wait_for_running(notebook.id, timeout=timeout, workspace=workspace)

    return notebook


def stop(notebook_id: str, workspace: Optional[str] = None) -> Notebook:
    """
    Stop a running notebook.

    Args:
        notebook_id: Notebook ID
        workspace: Workspace ID (uses default if not specified)

    Returns:
        Updated Notebook object

    Example:
        notebook = corerun.notebooks.stop("abc123")
    """
    client = get_client()
    response = client.post(f"/notebooks/{notebook_id}/stop", workspace=workspace)
    return _notebook_from_response(response)


def start(
    notebook_id: str,
    workspace: Optional[str] = None,
    compute: Optional[str] = None,
    gpu: Optional[float] = None,
    profile: Optional[str] = None,
) -> Notebook:
    """
    Start a stopped notebook -- where it ran, or somewhere else.

    Args:
        notebook_id: Notebook ID
        workspace: Workspace ID (uses default if not specified)
        compute: Start it on this compute target instead. Its files are in
            the person's home, which follows it.
        gpu: GPUs it should have, on whichever compute. With GPUs it runs the
            GPU image, without them the CPU one; the platform picks the image
            that fits the card.
        profile: A resource profile of the (new) compute.

    Returns:
        Updated Notebook object

    Example:
        corerun.notebooks.start("abc123")
        corerun.notebooks.start("abc123", compute="gb10-edge-host", gpu=1)
    """
    client = get_client()
    body: dict = {}
    if compute:
        body["compute_name"] = compute
    if gpu is not None:
        body["gpu"] = gpu
        # The API reads a change of GPUs through its compute choice.
        body.setdefault("compute_name", get(notebook_id, workspace=workspace).compute_name)
    if profile is not None:
        body["profile"] = profile
    response = client.post(f"/notebooks/{notebook_id}/start", json=body or None, workspace=workspace)
    return _notebook_from_response(response)


def set_datasets(
    notebook_id: str,
    datasets: List[str],
    next_start: bool = False,
    workspace: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Make a notebook's datasets exactly these, mounted read-only.

    On a running notebook on a host they change at once. Elsewhere -- a
    Kubernetes cluster, or a host whose connector predates this -- the
    notebook has to restart to change them: that raises ConflictError with
    ``code == "restart_required"``, and ``next_start=True`` records them for
    the next start instead (then stop() and start() it). A stopped notebook
    gets them when it starts.

    Returns:
        {"applied": "live" | "next_start", "datasets": [...]}

    Example:
        corerun.notebooks.set_datasets("abc123", ["mnist", "reviews"])
    """
    client = get_client()
    return client.put(
        f"/notebooks/{notebook_id}/datasets",
        json={"datasets": [*datasets], "next_start": next_start},
        workspace=workspace,
    )


def set_visibility(
    notebook_id: str,
    visibility: str,
    workspace: Optional[str] = None,
) -> Notebook:
    """
    Change who can see a notebook.

    A notebook is personal by default. Making it shared gives everyone in the
    workspace access; going back to personal takes it away again, including from
    anyone it was shared with individually.

    Args:
        notebook_id: Notebook name or ID
        visibility: "personal" or "shared"
        workspace: Workspace ID (uses default if not specified)

    Returns:
        Updated Notebook object

    Example:
        corerun.notebooks.set_visibility("my-notebook", "shared")
    """
    if visibility not in ("personal", "shared"):
        raise ValueError(
            f"Unsupported visibility {visibility!r}. Valid values: personal, shared"
        )
    client = get_client()
    client.put(
        f"/notebooks/{notebook_id}/visibility",
        json={"visibility": visibility},
        workspace=workspace,
    )
    # The endpoint answers with the id and the new visibility, nothing else.
    # Mapping that into a Notebook would hand back an object whose every other
    # field is empty and looks real, so read the notebook back instead.
    return get(notebook_id, workspace=workspace)


def share(
    notebook_id: str,
    user_id: str,
    shared: bool = True,
    workspace: Optional[str] = None,
) -> Notebook:
    """
    Share a notebook with one person in the workspace, or stop sharing it.

    Naming a person is separate from making a notebook shared: this grants that
    one person access while the notebook stays personal to everyone else.

    Args:
        notebook_id: Notebook name or ID
        user_id: The person to share with
        shared: True to grant access, False to withdraw it
        workspace: Workspace ID (uses default if not specified)

    Returns:
        Updated Notebook object

    Example:
        corerun.notebooks.share("my-notebook", "user-uuid")
        corerun.notebooks.share("my-notebook", "user-uuid", shared=False)
    """
    client = get_client()
    client.put(
        f"/notebooks/{notebook_id}/shares",
        json={"user_id": user_id, "shared": shared},
        workspace=workspace,
    )
    return get(notebook_id, workspace=workspace)


def delete(notebook_id: str, workspace: Optional[str] = None) -> None:
    """
    Delete a notebook.

    Args:
        notebook_id: Notebook ID
        workspace: Workspace ID (uses default if not specified)

    Example:
        corerun.notebooks.delete("abc123")
    """
    client = get_client()
    client.delete(f"/notebooks/{notebook_id}", workspace=workspace)


def wait_for_running(
    notebook_id: str,
    timeout: int = 300,
    poll_interval: int = 5,
    workspace: Optional[str] = None,
) -> Notebook:
    """
    Wait for a notebook to be running.

    Args:
        notebook_id: Notebook ID
        timeout: Maximum wait time in seconds
        poll_interval: Seconds between status checks
        workspace: Workspace ID (uses default if not specified)

    Returns:
        Notebook object (in running state)

    Raises:
        TimeoutError: If notebook doesn't start within timeout
        Exception: If notebook fails to start

    Example:
        notebook = corerun.notebooks.create(...)
        notebook = corerun.notebooks.wait_for_running(notebook.id)
        print(f"URL: {notebook.url}")
    """
    start_time = time.time()

    while time.time() - start_time < timeout:
        notebook = get(notebook_id, workspace=workspace)

        if notebook.is_running and notebook.url:
            return notebook

        if notebook.is_failed:
            raise Exception(f"Notebook failed: {notebook.error}")

        time.sleep(poll_interval)

    raise TimeoutError(f"Notebook {notebook_id} did not start within {timeout}s")


def get_url(notebook_id: str, workspace: Optional[str] = None) -> Optional[str]:
    """
    Get the URL for a running notebook.

    Args:
        notebook_id: Notebook ID
        workspace: Workspace ID (uses default if not specified)

    Returns:
        URL string if notebook is running, None otherwise

    Example:
        url = corerun.notebooks.get_url("abc123")
        if url:
            print(f"Open: {url}")
    """
    notebook = get(notebook_id, workspace=workspace)
    return notebook.url if notebook.is_running else None


def open(
    name: str,
    compute_name: str,
    image: Optional[str] = None,
    gpu: float = 0,
    profile: Optional[str] = None,
    datasets: Optional[List[str]] = None,
    workspace: Optional[str] = None,
) -> Notebook:
    """
    Create a notebook and wait for it to be ready.

    Convenience function that combines create() with wait_for_running().

    Args:
        name: Notebook name
        compute_name: Compute target name
        image: Docker image
        gpu: Number of GPUs
        profile: Resource profile name
        datasets: Datasets to mount
        workspace: Workspace ID

    Returns:
        Running Notebook object with URL

    Example:
        notebook = corerun.notebooks.open(
            name="dev-session",
            compute_name="dgx-cluster",
            gpu=1,
        )
        print(f"Open: {notebook.url}")
    """
    return create(
        name=name,
        compute_name=compute_name,
        image=image,
        gpu=gpu,
        profile=profile,
        datasets=datasets,
        wait=True,
        workspace=workspace,
    )


def files(notebook_id: str, path: str = "", workspace: Optional[str] = None) -> dict:
    """
    List a stopped notebook's files, as kept in the workspace's storage.

    Answers ``{"available": bool, "path": str, "entries": [...]}``; each entry
    has ``name``, ``path``, ``type`` (directory, notebook or file), ``size``
    and ``last_modified``. ``available`` is false when the notebook's home is
    not kept in object storage (a host's own disk, a volume): then only the
    running notebook can read them.

    Example:
        corerun.notebooks.files("abc123", "experiments")
    """
    client = get_client()
    return client.get(f"/notebooks/{notebook_id}/files", params={"path": path}, workspace=workspace)


def read_notebook_file(notebook_id: str, path: str, workspace: Optional[str] = None) -> dict:
    """
    Read one .ipynb from a stopped notebook's files, as last saved: the
    notebook's JSON (nbformat), read-only.

    Example:
        nb = corerun.notebooks.read_notebook_file("abc123", "train.ipynb")
        for cell in nb["cells"]: print(cell["cell_type"], "".join(cell["source"]))
    """
    client = get_client()
    return client.get(f"/notebooks/{notebook_id}/files/content", params={"path": path}, workspace=workspace)
