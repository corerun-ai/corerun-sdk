"""
corerun workspace quota.

Reports what a workspace is allowed to consume and what it is consuming now.
Worth checking before submitting work: creation is refused once a limit is hit.
"""

from typing import Optional

from pydantic import BaseModel

from corerun.config import get_client

#: Limit value meaning "no ceiling".
UNLIMITED = -1


class ResourceQuota(BaseModel):
    """One resource's current usage against its limit."""

    used: int = 0
    limit: int = 0
    unlimited: bool = False

    @property
    def is_unlimited(self) -> bool:
        """Whether this resource has no ceiling.

        -1 on the wire; a server from before that was settled sent 0 for a
        limit the plan does not set, which is no ceiling too -- never a
        ceiling of nothing.
        """
        return self.unlimited or self.limit <= 0

    @property
    def available(self) -> Optional[int]:
        """How much room is left, or None when unlimited."""
        if self.is_unlimited:
            return None
        return max(self.limit - self.used, 0)

    @property
    def is_exhausted(self) -> bool:
        """Whether the next request of this resource would be refused."""
        return not self.is_unlimited and self.used >= self.limit

    def __repr__(self) -> str:
        limit = "unlimited" if self.is_unlimited else str(self.limit)
        return f"<ResourceQuota {self.used}/{limit}>"


class WorkspaceQuota(BaseModel):
    """Every limit that applies to a workspace, with current usage."""

    jobs: ResourceQuota = ResourceQuota()
    notebooks: ResourceQuota = ResourceQuota()
    inference_servers: ResourceQuota = ResourceQuota()
    gpus: ResourceQuota = ResourceQuota()
    storage_gb: ResourceQuota = ResourceQuota()


def get(workspace: Optional[str] = None) -> WorkspaceQuota:
    """
    Get the workspace's quota and current usage.

    Args:
        workspace: Workspace ID (uses default if not specified)

    Returns:
        WorkspaceQuota object

    Example:
        q = corerun.quota.get()
        if q.gpus.is_exhausted:
            print("No GPU room left")
    """
    client = get_client()
    return WorkspaceQuota.model_validate(client.get("/quota", workspace=workspace))


def gpu_usage(workspace: Optional[str] = None) -> dict:
    """
    Get GPU usage broken down by the workloads holding them.

    Args:
        workspace: Workspace ID (uses default if not specified)

    Returns:
        Payload with used/max GPUs and a per-resource breakdown
    """
    client = get_client()
    return client.get("/quota/gpu-usage", workspace=workspace)
