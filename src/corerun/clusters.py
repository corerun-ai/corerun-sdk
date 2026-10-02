"""
corerun clusters.

The clusters a workspace can schedule onto, including live resource data
reported by each operator -- and what it takes to add one.

A cluster is not created with a kubeconfig. It is *prepared*: a record is made
with an operator credential, and the platform hands back something to apply on the
target. Applying it makes the operator phone home and the cluster registers itself,
which is why nothing here takes a cluster's address or its credentials.
"""

from typing import List, Optional

from pydantic import BaseModel, Field

from corerun.config import get_client


class GPUInfo(BaseModel):
    """A GPU model present on a cluster, with how many of them there are."""

    vendor: str = ""
    model: str = ""
    count: int = 0


class StorageClassInfo(BaseModel):
    """A storage class workloads on this cluster can request."""

    name: str = ""
    provisioner: str = ""
    is_default: bool = False


class ClusterResources(BaseModel):
    """Capacity discovered on a cluster, and how much of it is spoken for.

    Present only while the cluster's operator is connected; a cluster that has
    never connected reports no resources at all.
    """

    node_count: int = 0
    total_cpu: int = 0
    total_memory_mb: int = 0
    total_gpus: int = 0
    allocated_cpu: int = 0
    allocated_memory_mb: int = 0
    allocated_gpus: int = 0
    gpus: List[GPUInfo] = Field(default_factory=list)
    storage_classes: List[StorageClassInfo] = Field(default_factory=list)
    discovered_at: Optional[str] = None

    @property
    def available_gpus(self) -> int:
        """GPUs not currently allocated to a running workload."""
        return max(self.total_gpus - self.allocated_gpus, 0)


class Cluster(BaseModel):
    """A cluster registered with the platform."""

    id: str = ""
    name: str = ""
    # What this is: "kubernetes" or "host". Named type rather than backend
    # because it says what the row is, not which driver runs its work.
    type: str = ""
    namespace: str = ""
    status: str = ""
    scope: str = ""
    initialized: bool = False
    operator_connected: bool = False
    # The operator's version, and whether it is behind the platform's: the
    # host's owner then runs `sudo corerun-host-operator upgrade` there.
    operator_version: Optional[str] = None
    operator_update_available: bool = False
    operator_latest: Optional[str] = None
    gpu_strategy: str = ""
    architecture: Optional[str] = None
    cluster_type: Optional[str] = None
    default_profile: Optional[str] = None
    resources: Optional[ClusterResources] = None

    @property
    def is_ready(self) -> bool:
        """Whether the cluster can accept work right now."""
        return self.status == "ready" and self.operator_connected

    def __repr__(self) -> str:
        return f"<Cluster {self.name} ({self.status})>"


def _from_response(data: dict) -> Cluster:
    return Cluster.model_validate(data)


def list(workspace: Optional[str] = None) -> List[Cluster]:
    """
    List the clusters available to the workspace.

    Includes both tenant-wide clusters and clusters scoped to this workspace.

    Args:
        workspace: Workspace ID (uses default if not specified)

    Returns:
        List of Cluster objects

    Example:
        for c in corerun.clusters.list():
            print(c.name, c.status)
    """
    client = get_client()
    response = client.get("/clusters", workspace=workspace)
    return [_from_response(c) for c in response.get("clusters", [])]


def get(name: str, workspace: Optional[str] = None) -> Cluster:
    """
    Get one cluster by name.

    Args:
        name: Cluster name
        workspace: Workspace ID (uses default if not specified)

    Returns:
        Cluster object, including live resources when its operator is connected

    Example:
        c = corerun.clusters.get("gb10dgx01")
        if c.resources:
            print(c.resources.available_gpus, "GPUs free")
    """
    client = get_client()
    return _from_response(client.get(f"/clusters/{name}", workspace=workspace))


def profiles(name: str, workspace: Optional[str] = None) -> list:
    """
    List the pod profiles (CPU/memory/GPU presets) a cluster offers.

    Args:
        name: Cluster name
        workspace: Workspace ID (uses default if not specified)

    Returns:
        List of profile dicts
    """
    client = get_client()
    response = client.get(f"/clusters/{name}/profiles", workspace=workspace)
    if isinstance(response, dict):
        return response.get("profiles", [])
    return response


class ClusterType(BaseModel):
    """A label a cluster can be given, carrying defaults like its GPU strategy."""

    id: str = ""
    name: str = ""
    display_name: str = ""
    gpu_strategy: str = ""


class HostEnrollment(BaseModel):
    """What a bare host needs in order to join.

    Both commands come from the API rather than being composed here: the install
    one depends on where this deployment serves its operator binaries, which is a
    platform setting the client has no business guessing.
    """

    name: str = ""
    type: str = "host"
    architecture: str = ""
    os: str = ""
    enrollment_token: str = ""
    install_command: str = ""
    connect_command: str = ""
    note: str = ""


def types(workspace: Optional[str] = None) -> List[ClusterType]:
    """
    List the cluster types available to add a cluster as.

    Args:
        workspace: Workspace ID (uses default if not specified)

    Returns:
        List of ClusterType objects, platform-wide ones first

    Example:
        for t in corerun.clusters.types():
            print(t.id, t.display_name or t.name)
    """
    client = get_client()
    response = client.get("/cluster-types", workspace=workspace)
    if isinstance(response, dict):
        return [ClusterType.model_validate(t) for t in response.get("cluster_types", [])]
    return []


def _scope(tenant_wide: bool) -> bool:
    """
    The workspace_scope field, as the API reads it.

    True means "put this in the caller's workspace"; false means "put it in the
    tenant, visible to every workspace", which the API then checks as a tenant
    administrator action. Sending false without that standing is a 403, not a
    cluster, so the default is the narrower of the two.
    """
    return not tenant_wide


def prepare(
    name: str,
    namespace: str = "corerun",
    architecture: str = "amd64",
    accelerator_family: Optional[str] = None,
    cluster_type_id: Optional[str] = None,
    tenant_wide: bool = False,
    workspace: Optional[str] = None,
) -> str:
    """
    Prepare a Kubernetes cluster for its operator, and return the manifest.

    The cluster record is created here, but nothing is connected yet: what comes
    back is a manifest to apply on the target cluster. Its operator connects to the
    platform on its own, and the cluster becomes ready when it does.

    Args:
        name: Cluster name, unique within the tenant
        namespace: Namespace to install the operator into
        architecture: "amd64" or "arm64"
        accelerator_family: A family (hopper) or a card (h100). Anything
            unrecognised is not an error -- the cluster is simply left to be
            identified from what its operator reports.
        cluster_type_id: UUID of a cluster type from types()
        tenant_wide: Create it for the whole tenant rather than this workspace
        workspace: Workspace ID (uses default if not specified)

    Returns:
        The operator manifest, as YAML

    Example:
        manifest = corerun.clusters.prepare("gpu1", accelerator_family="h100")
        open("gpu1.yaml", "w").write(manifest)
    """
    client = get_client()
    body = {
        "name": name,
        "type": "kubernetes",
        "namespace": namespace,
        "architecture": architecture,
        "workspace_scope": _scope(tenant_wide),
    }
    if accelerator_family:
        body["accelerator_family"] = accelerator_family
    if cluster_type_id:
        body["cluster_type_id"] = cluster_type_id

    # The success body is the manifest itself, not JSON: the API answers
    # text/yaml here, and the ordinary path would fail on the first line.
    return client.post("/clusters/prepare", json=body, workspace=workspace, response_type="text")


def prepare_host(
    name: str,
    architecture: Optional[str] = None,
    os: str = "linux",
    accelerator_family: Optional[str] = None,
    tenant_wide: bool = False,
    workspace: Optional[str] = None,
) -> HostEnrollment:
    """
    Prepare a bare-metal host for its operator, and return what it needs to join.

    A host has no Kubernetes to apply a manifest to, so it is onboarded by
    running two commands on the machine itself: one installs the operator, and
    the second joins it with the enrollment token.

    Args:
        name: Cluster name, unique within the tenant
        architecture: "amd64" or "arm64". Usually left out: the installer
            detects it, and the platform takes what the connected host reports.
        os: "linux" or "darwin"
        accelerator_family: What this machine's cards are -- a family (hopper)
            or a card (h100). A host has no pod profiles, so this is the only
            place it can declare its hardware; left unset it is identified from
            what its operator reports, which is a guess the machine makes about
            itself.
        tenant_wide: Create it for the whole tenant rather than this workspace
        workspace: Workspace ID (uses default if not specified)

    Returns:
        HostEnrollment, holding the install and connect commands

    Example:
        e = corerun.clusters.prepare_host("dgx1")
        print(e.install_command)
        print(e.connect_command)
    """
    client = get_client()
    response = client.post(
        "/clusters/prepare",
        json={
            "name": name,
            "type": "host",
            "os": os,
            "workspace_scope": _scope(tenant_wide),
            **({"architecture": architecture} if architecture else {}),
            **({"accelerator_family": accelerator_family} if accelerator_family else {}),
        },
        workspace=workspace,
    )
    return HostEnrollment.model_validate(response)


def remove(
    name: str,
    clean_resources: bool = True,
    delete_namespace: bool = False,
    clean_kueue: bool = False,
    clean_kai: bool = False,
    tenant_wide: bool = False,
    workspace: Optional[str] = None,
) -> dict:
    """
    Remove a cluster.

    Args:
        name: Cluster name
        clean_resources: Uninstall the operator's Helm release and RBAC
        delete_namespace: Delete the Kubernetes namespace as well. Destructive,
            and off by default: the namespace may hold more than the operator.
        clean_kueue: Delete the cluster's Kueue queues and flavours
        clean_kai: Uninstall the KAI scheduler
        tenant_wide: Remove one the whole tenant owns. A workspace's route
            refuses those -- it only removes what the workspace itself created
            -- so the tenant's own route is the one that has to be used.
        workspace: Workspace ID (uses default if not specified)

    Returns:
        The API's response, including a per-resource cleanup report

    Example:
        corerun.clusters.remove("gpu1", confirm_name="gpu1")
    """
    client = get_client()
    # Every field is sent explicitly. The API defaults clean_resources to true
    # only when it cannot parse a body at all, so omitting it here would turn
    # off the cleanup a caller expects to have happened.
    # Which route removes it is decided by who owns it, not by who is asking:
    # /clusters/:name answers 403 tenant_scoped for a tenant-wide cluster, so a
    # caller who created one with tenant_wide=True has to say so again here.
    path = f"/tenant/shared/clusters/{name}" if tenant_wide else f"/clusters/{name}"

    return client.delete(
        path,
        json={
            "confirm_name": name,
            "clean_resources": clean_resources,
            "delete_namespace": delete_namespace,
            "clean_kueue": clean_kueue,
            "clean_kai": clean_kai,
        },
        workspace=workspace,
    )


def operator_manifest(name: str, workspace: Optional[str] = None) -> str:
    """
    The onboarding artefact for a cluster, as it stands now.

    YAML for a Kubernetes cluster; the installer script for a host-backed one.
    Re-fetching is worth doing after rotating a token, since the manifest you
    applied carries the credential of the day it was generated.

    Args:
        name: Cluster name
        workspace: Workspace ID (uses default if not specified)

    Returns:
        The manifest or installer, as text

    Example:
        print(corerun.clusters.operator_manifest("gpu1"))
    """
    client = get_client()
    return client.get(
        f"/clusters/{name}/operator-manifest", workspace=workspace, response_type="text"
    )


def rotate_token(name: str, workspace: Optional[str] = None) -> str:
    """
    Replace a cluster's operator token, and return the new one.

    The previous token stops working immediately. There is no grace period and
    no way for a connected operator to learn the replacement, so a cluster that is
    currently connected will drop off until it is given the new token. Re-apply
    the manifest from operator_manifest() afterwards.

    Args:
        name: Cluster name
        workspace: Workspace ID (uses default if not specified)

    Returns:
        The new operator token

    Example:
        corerun.clusters.rotate_token("gpu1")
    """
    client = get_client()
    response = client.post(f"/clusters/{name}/token", workspace=workspace)
    return response.get("token", "")


def revoke_token(name: str, workspace: Optional[str] = None) -> None:
    """
    Clear a cluster's operator token, so its operator can no longer connect.

    Note that a token the operator has *already* been given as a replacement -- by
    a cluster that re-onboarded, say -- is not cleared by this, and the hub will
    still honour it. This stops the cluster's current credential, not every
    credential that has ever been valid.

    Args:
        name: Cluster name
        workspace: Workspace ID (uses default if not specified)

    Example:
        corerun.clusters.revoke_token("gpu1")
    """
    client = get_client()
    client.delete(f"/clusters/{name}/token", workspace=workspace)


def set_scope(
    name: str,
    workspace_id: Optional[str] = None,
    organization: bool = False,
    workspace: Optional[str] = None,
) -> dict:
    """
    Move a cluster or server between one workspace and the whole organisation.

    Nothing is reinstalled on the machine. Giving a shared one to a single
    workspace is refused while other workspaces have work running on it.
    Needs an organisation administrator.

    Args:
        name: Cluster name
        workspace_id: The workspace it should belong to alone
        organization: Share it with every workspace instead
    """
    if organization == bool(workspace_id):
        raise ValueError("give it to one workspace (workspace_id) or share it (organization=True)")
    body = {"scope": "organization"} if organization else {"scope": "workspace", "workspace_id": workspace_id}
    return get_client().put(f"/tenant/shared/clusters/{name}/scope", json=body, workspace=workspace)


def organisation_workspaces(workspace: Optional[str] = None) -> list:
    """Every workspace in the organisation, for an administrator."""
    return get_client().get("/tenant/workspaces", workspace=workspace).get("workspaces") or []
