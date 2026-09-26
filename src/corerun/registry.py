"""
Model Registry module for corerun SDK.

Provides functions for managing registered models, versions, and aliases.

Usage:
    import corerun

    corerun.init(auth_token="...")

    # Create a registered model
    model = corerun.registry.create_model(
        name="my-finetuned-llm",
        description="Fine-tuned LLaMA for customer support",
    )

    # Publish a version from a fine-tune job
    version = corerun.registry.publish_from_job(
        model_name="my-finetuned-llm",
        job_id="abc123",
        description="Trained on support tickets v2",
    )

    # Set an alias
    corerun.registry.set_alias(
        model_name="my-finetuned-llm",
        alias="champion",
        version=version.version,
    )

    # Deploy the model
    server = corerun.inference.deploy(
        name="support-llm",
        model_id="my-finetuned-llm",
        model_source="registry",
        model_version="1",  # or use alias via MLflow
        compute_name="dgx-cluster",
        gpu=1,
    )
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, field_validator
from datetime import datetime

from corerun.config import get_client


class _NullTolerant(BaseModel):
    """A model whose absent collections arrive as null rather than empty.

    Go marshals a nil map and a nil slice to `null`, not to `{}` or `[]`, so a
    model with no tags and a version with no metadata come back with null in
    those fields. Pydantic rejects null for a non-optional dict, which made
    `corerun models push` fail on its first call for any model created without
    tags -- before it had uploaded anything, with a validation error naming a
    field the user never set.
    """

    @field_validator("*", mode="before")
    @classmethod
    def _null_is_the_default(cls, value, info):
        if value is not None:
            return value
        field = cls.model_fields.get(info.field_name)
        if field is None or field.is_required():
            return value
        return field.get_default(call_default_factory=True)


class RegisteredModel(_NullTolerant):
    """Registered model information."""
    name: str
    description: Optional[str] = None
    tags: Dict[str, str] = {}
    latest_version: int = 0
    version_count: int = 0
    created_by: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ModelVersion(_NullTolerant):
    """Model version information."""
    model_name: str
    version: int
    run_id: Optional[str] = None
    storage_target_id: Optional[str] = None
    storage_path: str = ""
    stage: str = "none"  # none, staging, production, archived
    framework: Optional[str] = None
    description: Optional[str] = None
    metadata: Dict[str, Any] = {}
    size_bytes: int = 0
    created_by: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    # Set when the version's artifacts live in the platform's git plane rather
    # than object storage. The commit is what a version actually names; the
    # branch it sits on may have moved since.
    repo_url: str = ""
    commit_sha: str = ""

    # How the version came to exist, for one that is still arriving.
    #
    # An import returns as soon as it has started, so these are the only way to
    # tell a version being fetched from one that is ready to use. The SDK
    # dropped them, which made a running import look like a finished one that
    # simply had no files.
    status: str = ""
    status_message: str = ""
    progress: Dict[str, Any] = {}

    # Publishing outward, which is a different question from whether the
    # version exists here: a version is READY as soon as it is imported, and
    # may or may not have been pushed to HuggingFace since.
    publish_status: str = ""
    publish_target: str = ""
    publish_message: str = ""
    publish_progress: Dict[str, Any] = {}


class ModelAlias(BaseModel):
    """Model alias information."""
    model_name: str
    alias: str
    version: int
    created_by: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


def _model_from_response(data: dict) -> RegisteredModel:
    """Convert API response to RegisteredModel object."""
    return RegisteredModel(
        name=data.get("name", ""),
        description=data.get("description"),
        tags=data.get("tags", {}),
        latest_version=data.get("latest_version", 0),
        version_count=data.get("version_count", 0),
        created_by=data.get("created_by", ""),
        created_at=data.get("created_at"),
        updated_at=data.get("updated_at"),
    )


def _version_from_response(data: dict) -> ModelVersion:
    """Convert API response to ModelVersion object."""
    return ModelVersion(
        model_name=data.get("model_name", ""),
        version=data.get("version", 0),
        run_id=data.get("run_id"),
        storage_target_id=data.get("storage_target_id"),
        storage_path=data.get("storage_path", ""),
        stage=data.get("stage", "none"),
        framework=data.get("framework"),
        description=data.get("description"),
        metadata=data.get("metadata", {}),
        size_bytes=data.get("size_bytes", 0),
        created_by=data.get("created_by", ""),
        created_at=data.get("created_at"),
        updated_at=data.get("updated_at"),
        repo_url=data.get("repo_url", ""),
        commit_sha=data.get("commit_sha", ""),
        status=data.get("status", ""),
        status_message=data.get("status_message", ""),
        progress=data.get("progress") or {},
        publish_status=data.get("publish_status", ""),
        publish_target=data.get("publish_target", ""),
        publish_message=data.get("publish_message", ""),
        publish_progress=data.get("publish_progress") or {},
    )


def _alias_from_response(data: dict) -> ModelAlias:
    """Convert API response to ModelAlias object."""
    return ModelAlias(
        model_name=data.get("model_name", ""),
        alias=data.get("alias", ""),
        version=data.get("version", 0),
        created_by=data.get("created_by", ""),
        created_at=data.get("created_at"),
        updated_at=data.get("updated_at"),
    )


# ============================================================================
# Models API
# ============================================================================


def list_models(workspace: Optional[str] = None) -> List[RegisteredModel]:
    """
    List all registered models in the workspace.

    Args:
        workspace: Workspace ID (uses default if not specified)

    Returns:
        List of RegisteredModel objects

    Example:
        models = corerun.registry.list_models()
        for model in models:
            print(f"{model.name}: {model.version_count} versions")
    """
    client = get_client()
    response = client.get("/registry/models", workspace=workspace)
    return [_model_from_response(m) for m in response.get("models", [])]


def get_model(name: str, workspace: Optional[str] = None) -> RegisteredModel:
    """
    Get a registered model by name.

    Args:
        name: Model name
        workspace: Workspace ID (uses default if not specified)

    Returns:
        RegisteredModel object

    Example:
        model = corerun.registry.get_model("my-llm")
        print(f"Latest version: {model.latest_version}")
    """
    client = get_client()
    response = client.get(f"/registry/models/{name}", workspace=workspace)
    return _model_from_response(response)


def create_model(
    name: str,
    description: Optional[str] = None,
    tags: Optional[Dict[str, str]] = None,
    workspace: Optional[str] = None,
) -> RegisteredModel:
    """
    Create a new registered model.

    Args:
        name: Model name (unique within workspace)
        description: Model description
        tags: Optional tags
        workspace: Workspace ID (uses default if not specified)

    Returns:
        Created RegisteredModel object

    Example:
        model = corerun.registry.create_model(
            name="my-finetuned-llm",
            description="Fine-tuned for customer support",
            tags={"task": "chat", "base": "llama-2-7b"},
        )
    """
    client = get_client()
    data = {"name": name}
    if description:
        data["description"] = description
    if tags:
        data["tags"] = tags

    response = client.post("/registry/models", json=data, workspace=workspace)
    return _model_from_response(response)


def delete_model(name: str, workspace: Optional[str] = None) -> None:
    """
    Delete a registered model and all its versions.

    Args:
        name: Model name
        workspace: Workspace ID (uses default if not specified)

    Example:
        corerun.registry.delete_model("my-old-model")
    """
    client = get_client()
    client.delete(f"/registry/models/{name}", workspace=workspace)


# ============================================================================
# Versions API
# ============================================================================


def list_versions(
    model_name: str,
    workspace: Optional[str] = None,
) -> List[ModelVersion]:
    """
    List all versions of a model.

    Args:
        model_name: Model name
        workspace: Workspace ID (uses default if not specified)

    Returns:
        List of ModelVersion objects (sorted by version descending)

    Example:
        versions = corerun.registry.list_versions("my-llm")
        for v in versions:
            print(f"v{v.version}: {v.stage}")
    """
    client = get_client()
    response = client.get(f"/registry/models/{model_name}/versions", workspace=workspace)
    return [_version_from_response(v) for v in response.get("versions", [])]


def get_version(
    model_name: str,
    version: int,
    workspace: Optional[str] = None,
) -> ModelVersion:
    """
    Get a specific model version.

    Args:
        model_name: Model name
        version: Version number
        workspace: Workspace ID (uses default if not specified)

    Returns:
        ModelVersion object
    """
    client = get_client()
    response = client.get(f"/registry/models/{model_name}/versions/{version}", workspace=workspace)
    return _version_from_response(response)


def create_version(
    model_name: str,
    storage_path: str,
    run_id: Optional[str] = None,
    storage_target_id: Optional[str] = None,
    framework: Optional[str] = None,
    description: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    size_bytes: int = 0,
    repo_url: str = "",
    commit_sha: str = "",
    workspace: Optional[str] = None,
) -> ModelVersion:
    """
    Create a new model version.

    Either storage_path (artifacts in object storage) or repo_url with
    commit_sha (artifacts pushed to the git plane) identifies where the
    version's files actually are.

    Args:
        model_name: Model name
        storage_path: Path to model artifacts in storage
        run_id: Optional MLflow run ID
        storage_target_id: Storage target ID for the artifacts
        framework: Model framework (pytorch, tensorflow, etc.)
        description: Version description
        metadata: Additional metadata (input/output schema, etc.)
        size_bytes: Size of model artifacts
        workspace: Workspace ID (uses default if not specified)

    Returns:
        Created ModelVersion object

    Example:
        version = corerun.registry.create_version(
            model_name="my-llm",
            storage_path="s3://models/my-llm/v1",
            framework="pytorch",
            description="Initial release",
        )
    """
    client = get_client()
    data = {"storage_path": storage_path}
    if run_id:
        data["run_id"] = run_id
    if storage_target_id:
        data["storage_target_id"] = storage_target_id
    if framework:
        data["framework"] = framework
    if description:
        data["description"] = description
    if metadata:
        data["metadata"] = metadata
    if size_bytes:
        data["size_bytes"] = size_bytes
    if repo_url:
        data["repo_url"] = repo_url
    if commit_sha:
        data["commit_sha"] = commit_sha

    response = client.post(f"/registry/models/{model_name}/versions", json=data, workspace=workspace)
    return _version_from_response(response)


def publish_from_job(
    model_name: str,
    job_id: str,
    description: Optional[str] = None,
    workspace: Optional[str] = None,
) -> ModelVersion:
    """
    Register what a finished job wrote to its outputs folder as a new version.

    The model is created if it does not exist. The version names the run the
    job logged into, so its page links back to the metrics that justified it.
    A job already published can be published again: another version, or a
    version of another model.

    Example:
        version = corerun.registry.publish_from_job("churn-model", job_id="0b1c...")
    """
    client = get_client()
    body: Dict[str, Any] = {"job_id": job_id}
    if description:
        body["description"] = description
    response = client.post(f"/registry/models/{model_name}/publish-from-job", json=body, workspace=workspace)
    return _version_from_response(response)


def versions_from_runs(run_ids: List[str], workspace: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Registry versions trained in these runs (run or job ids), across every
    model, each with `location`: the git commit or object-store path its
    files are at.
    """
    client = get_client()
    out: List[Dict[str, Any]] = []
    ids = [i for i in dict.fromkeys(run_ids) if i]
    for start in range(0, len(ids), 500):
        response = client.get("/registry/versions", params={"run_id": ids[start:start + 500]}, workspace=workspace)
        out.extend(response.get("versions", []) or [])
    return out


def set_version_stage(
    model_name: str,
    version: int,
    stage: str,
    workspace: Optional[str] = None,
) -> ModelVersion:
    """
    Set the stage of a model version.

    Args:
        model_name: Model name
        version: Version number
        stage: New stage ("none", "staging", "production", "archived")
        workspace: Workspace ID (uses default if not specified)

    Returns:
        Updated ModelVersion object

    Example:
        # Promote to production
        corerun.registry.set_version_stage("my-llm", 3, "production")
    """
    client = get_client()
    response = client.put(
        f"/registry/models/{model_name}/versions/{version}/stage",
        json={"stage": stage},
        workspace=workspace,
    )
    return _version_from_response(response)


# ============================================================================
# Aliases API
# ============================================================================


def list_aliases(
    model_name: str,
    workspace: Optional[str] = None,
) -> List[ModelAlias]:
    """
    List all aliases for a model.

    Args:
        model_name: Model name
        workspace: Workspace ID (uses default if not specified)

    Returns:
        List of ModelAlias objects

    Example:
        aliases = corerun.registry.list_aliases("my-llm")
        for alias in aliases:
            print(f"@{alias.alias} -> v{alias.version}")
    """
    client = get_client()
    response = client.get(f"/registry/models/{model_name}/aliases", workspace=workspace)
    return [_alias_from_response(a) for a in response.get("aliases", [])]


def set_alias(
    model_name: str,
    alias: str,
    version: int,
    workspace: Optional[str] = None,
) -> ModelAlias:
    """
    Set or update a model alias.

    Args:
        model_name: Model name
        alias: Alias name (e.g., "champion", "challenger", "latest")
        version: Version number to point to
        workspace: Workspace ID (uses default if not specified)

    Returns:
        ModelAlias object

    Example:
        # Point "champion" alias to version 3
        corerun.registry.set_alias("my-llm", "champion", 3)
    """
    client = get_client()
    response = client.post(
        f"/registry/models/{model_name}/aliases",
        json={"alias": alias, "version": version},
        workspace=workspace,
    )
    return _alias_from_response(response)


def delete_alias(
    model_name: str,
    alias: str,
    workspace: Optional[str] = None,
) -> None:
    """
    Delete a model alias.

    Args:
        model_name: Model name
        alias: Alias name
        workspace: Workspace ID (uses default if not specified)
    """
    client = get_client()
    client.delete(f"/registry/models/{model_name}/aliases/{alias}", workspace=workspace)


# ============================================================================
# Import API
# ============================================================================


def import_from_huggingface(
    name: str,
    huggingface_id: str,
    revision: Optional[str] = None,
    hf_token: Optional[str] = None,
    description: Optional[str] = None,
    workspace: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Ask the platform to import a model from HuggingFace.

    The transfer happens on the platform, not here: weights go from
    HuggingFace into the workspace's own storage without passing through
    whichever machine ran the command. For a large checkpoint that is the
    difference between minutes on a cluster link and hours on a laptop, and
    it works from a shell that could not hold the file at all.

    Returns immediately with the version that will hold the import; the model
    is not ready until that version reports READY. Poll with get_version.

    Args:
        name: Model name in the registry. Created if it does not exist.
        huggingface_id: The source, as "org/model".
        revision: Branch, tag or commit. Defaults to the repository's default.
        hf_token: Token for a gated or private repository.
        description: Description for the new version.
        workspace: Workspace ID (uses default if not specified)

    Returns:
        {"message", "version", "repo_url"}

    Example:
        started = corerun.registry.import_from_huggingface(
            "llama-3-8b", "meta-llama/Meta-Llama-3-8B")
        print(started["version"])
    """
    client = get_client()
    payload: Dict[str, Any] = {"huggingface_id": huggingface_id}
    if revision:
        payload["revision"] = revision
    if hf_token:
        payload["hf_token"] = hf_token
    if description:
        payload["description"] = description

    return client.post(
        f"/registry/models/{name}/import-huggingface",
        json=payload,
        workspace=workspace,
    )


def publish_to_huggingface(
    name: str,
    huggingface_id: str,
    hf_token: str,
    version: Optional[int] = None,
    private: bool = True,
    message: Optional[str] = None,
    workspace: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Publish a registry version to a HuggingFace repository.

    The mirror of import_from_huggingface, and the transfer happens on the
    platform for the same reason: the weights go straight from the workspace's
    storage to HuggingFace without passing through whichever machine ran the
    command.

    Returns as soon as the push has started. Poll get_version and read
    publish_status, which is PUBLISHING until it is PUBLISHED or FAILED.

    Args:
        name: Model in the registry to publish.
        huggingface_id: Destination, as "org/repo". Created if it does not exist.
        hf_token: A HuggingFace token with write access to that repository.
        version: Which version; defaults to the latest.
        private: Whether to create the repository private. Only consulted when
            it has to be created -- an existing repository keeps its visibility.
        message: Commit message. Defaults to naming the model and version.
        workspace: Workspace ID (uses default if not specified)

    Returns:
        {"message", "model", "version", "huggingface_id", "url"}

    Example:
        started = corerun.registry.publish_to_huggingface(
            "llama-3-8b-ft", "acme/llama-3-8b-ft", hf_token=token)
        print(started["url"])
    """
    client = get_client()
    payload: Dict[str, Any] = {
        "huggingface_id": huggingface_id,
        "hf_token": hf_token,
        "private": private,
    }
    if version:
        payload["version"] = version
    if message:
        payload["message"] = message

    return client.post(
        f"/registry/models/{name}/publish-huggingface",
        json=payload,
        workspace=workspace,
    )
