"""
corerun compute targets.

A compute target names a place to run work. It maps to a cluster and carries
the resource profiles jobs and notebooks select from.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from corerun.config import get_client


class ComputeTarget(BaseModel):
    """A named place to run workloads."""

    id: str = ""
    name: str = ""
    type: str = ""
    description: Optional[str] = None
    cluster_id: Optional[str] = None
    scope: str = ""
    config: Dict[str, Any] = Field(default_factory=dict)
    tags: Dict[str, str] = Field(default_factory=dict)
    # GPUs its operator found, and what they are; absent without any.
    gpus: int = 0
    gpu_model: Optional[str] = None
    # A host whose operator is behind the platform's version.
    operator_update_available: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def __repr__(self) -> str:
        return f"<ComputeTarget {self.name} ({self.type})>"


def _from_response(data: dict) -> ComputeTarget:
    return ComputeTarget.model_validate(data)


def list(workspace: Optional[str] = None) -> List[ComputeTarget]:
    """
    List the compute targets available to the workspace.

    Includes both tenant-wide targets and targets scoped to this workspace.

    Args:
        workspace: Workspace ID (uses default if not specified)

    Returns:
        List of ComputeTarget objects

    Example:
        for t in corerun.compute.list():
            print(t.name, t.type)
    """
    client = get_client()
    response = client.get("/compute", workspace=workspace)
    return [_from_response(t) for t in response.get("compute_targets", [])]


def get(name: str, workspace: Optional[str] = None) -> ComputeTarget:
    """
    Get one compute target by name.

    Args:
        name: Compute target name
        workspace: Workspace ID (uses default if not specified)

    Returns:
        ComputeTarget object

    Example:
        target = corerun.compute.get("dgx")
    """
    client = get_client()
    return _from_response(client.get(f"/compute/{name}", workspace=workspace))


def types(workspace: Optional[str] = None) -> list:
    """
    List the compute target types this platform supports.

    Args:
        workspace: Workspace ID (uses default if not specified)

    Returns:
        List of type descriptors
    """
    client = get_client()
    response = client.get("/compute/types", workspace=workspace)
    if isinstance(response, dict):
        return response.get("types", response.get("compute_types", []))
    return response


def health(name: str, workspace: Optional[str] = None) -> dict:
    """
    Check whether a compute target is reachable.

    Args:
        name: Compute target name
        workspace: Workspace ID (uses default if not specified)

    Returns:
        Health payload as returned by the API
    """
    client = get_client()
    return client.get(f"/compute/{name}/health", workspace=workspace)
