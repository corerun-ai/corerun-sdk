"""
Inference Servers module for corerun SDK.

Provides functions for deploying and managing inference servers (vLLM, Ollama).

Usage:
    import corerun

    corerun.init(auth_token="...")

    # Deploy a vLLM inference server
    server = corerun.inference.deploy(
        name="mistral-7b",
        model_id="mistralai/Mistral-7B-Instruct-v0.2",
        compute_name="dgx-cluster",
        gpu=1,
    )

    # Wait for it to be ready
    server = corerun.inference.wait_for_running(server.id)

    # Use the OpenAI-compatible API
    from openai import OpenAI
    client = OpenAI(base_url=server.external_url, api_key=server.api_key)
    response = client.chat.completions.create(
        model=server.model_id,
        messages=[{"role": "user", "content": "Hello!"}]
    )

    # Scale the server
    corerun.inference.scale(server.id, min_replicas=2, max_replicas=4)

    # Delete the server
    corerun.inference.delete(server.id)
"""

import time
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel

from corerun.config import get_client


class LoRAModuleInfo(BaseModel):
    """LoRA adapter information in server response."""
    name: str
    source: str
    path: Optional[str] = None
    job_id: Optional[str] = None
    model_name: Optional[str] = None
    model_version: Optional[int] = None


class InferenceServer(BaseModel):
    """Inference server information."""

    id: str
    name: str
    server_type: str  # vllm, ollama
    model_source: str  # huggingface, mlflow, registry, path
    model_id: str
    model_version: Optional[str] = None
    compute_name: str
    cluster_id: Optional[str] = None
    image: str
    gpu: float = 0
    replicas: int = 1
    min_replicas: int = 1
    max_replicas: int = 1
    status: str  # pending, running, stopped, failed
    internal_url: Optional[str] = None
    external_path: Optional[str] = None
    api_key: Optional[str] = None  # Only shown on create
    error: Optional[str] = None

    # What the engine was launched with. `corerun inference get` has printed
    # these since it was written and the model never carried them, so reaching
    # that line raised AttributeError -- which is every call, because a server
    # deployed from a recipe always has engine args.
    extra_args: List[str] = []
    recipe_source: Optional[str] = None
    recipe_note: Optional[str] = None
    max_model_len: Optional[int] = None
    tensor_parallel: Optional[int] = None
    quantization: Optional[str] = None
    accelerator_family: Optional[str] = None
    accelerator_note: Optional[str] = None
    # Where callers reach it, what the model said about itself (its README's
    # base model, its precision, its MTP head), the recipe's opt-in features
    # it runs with, and the engine inside its image.
    endpoint_name: Optional[str] = None
    model_facts: Optional[dict] = None
    features: List[str] = []
    engine_version: Optional[str] = None
    lora_modules: List[LoRAModuleInfo] = []  # LoRA adapters loaded on base model
    owner_id: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    started_at: Optional[datetime] = None

    @property
    def is_running(self) -> bool:
        """Check if server is running."""
        return self.status == "running"

    @property
    def is_stopped(self) -> bool:
        """Check if server is stopped."""
        return self.status == "stopped"

    @property
    def is_failed(self) -> bool:
        """Check if server failed."""
        return self.status == "failed"

    @property
    def is_ready(self) -> bool:
        """Check if server is ready to receive requests."""
        return self.status == "running" and self.external_path is not None

    @property
    def openai_base_url(self) -> Optional[str]:
        """Get the OpenAI-compatible API base URL."""
        if self.external_path:
            # The external path typically includes /v1 for OpenAI compatibility
            return self.external_path.rstrip("/")
        return None

    def __repr__(self) -> str:
        return f"InferenceServer(name='{self.name}', model='{self.model_id}', status='{self.status}')"


class LoRAModule(BaseModel):
    """LoRA adapter configuration for fine-tuned models."""
    name: str  # Unique name for the adapter (used in API model field)
    source: str  # "job", "registry", "huggingface", "path"
    path: Optional[str] = None  # Direct path or HuggingFace ID
    job_id: Optional[str] = None  # Fine-tune job ID (when source="job")
    model_name: Optional[str] = None  # Registry model name (when source="registry")
    model_version: Optional[int] = None  # Registry version (when source="registry")


class CreateInferenceServerRequest(BaseModel):
    """Request to create an inference server."""
    name: str
    server_type: str = "vllm"  # vllm, ollama
    model_source: str = "huggingface"  # huggingface, mlflow, registry, path
    model_id: str
    model_version: Optional[str] = None
    compute_name: str
    cluster_id: Optional[str] = None
    image: Optional[str] = None
    gpu: float = 1
    min_replicas: int = 1
    max_replicas: int = 1
    endpoint_name: Optional[str] = None
    extra_args: Optional[List[str]] = None
    served_model_names: Optional[List[str]] = None
    features: Optional[List[str]] = None
    dry_run: Optional[bool] = None

    # Where some of those extra args came from, when they came from the model's
    # published recipe rather than from a person.
    recipe_source: Optional[str] = None
    recipe_note: Optional[str] = None

    # vLLM-specific options
    max_model_len: Optional[int] = None
    tensor_parallel: Optional[int] = None
    quantization: Optional[str] = None
    gpu_memory_util: Optional[float] = None
    enforce_eager: bool = False  # Disable CUDA graphs for unsupported GPUs

    # LoRA adapter configuration
    lora_modules: Optional[List[LoRAModule]] = None


class ScaleRequest(BaseModel):
    """Request to scale an inference server."""
    min_replicas: int
    max_replicas: int


def _server_from_response(data: dict) -> InferenceServer:
    """Convert API response to InferenceServer object."""
    # Parse LoRA modules
    lora_modules = []
    if "lora_modules" in data and data["lora_modules"]:
        lora_modules = [
            LoRAModuleInfo(
                name=m.get("name", ""),
                source=m.get("source", ""),
                path=m.get("path"),
                job_id=m.get("job_id"),
                model_name=m.get("model_name"),
                model_version=m.get("model_version"),
            )
            for m in data["lora_modules"]
        ]

    return InferenceServer(
        id=data.get("id", ""),
        name=data.get("name", ""),
        server_type=data.get("server_type", "vllm"),
        model_source=data.get("model_source", "huggingface"),
        model_id=data.get("model_id", ""),
        model_version=data.get("model_version"),
        compute_name=data.get("compute_name", ""),
        cluster_id=data.get("cluster_id"),
        image=data.get("image", ""),
        gpu=data.get("gpu", 0),
        replicas=data.get("replicas", 1),
        min_replicas=data.get("min_replicas", 1),
        max_replicas=data.get("max_replicas", 1),
        status=data.get("status", "pending"),
        internal_url=data.get("internal_url"),
        external_path=data.get("external_path"),
        api_key=data.get("api_key"),
        error=data.get("error"),
        extra_args=data.get("extra_args") or [],
        recipe_source=data.get("recipe_source"),
        recipe_note=data.get("recipe_note"),
        max_model_len=data.get("max_model_len"),
        tensor_parallel=data.get("tensor_parallel"),
        quantization=data.get("quantization"),
        accelerator_family=data.get("accelerator_family"),
        accelerator_note=data.get("accelerator_note"),
        endpoint_name=data.get("endpoint_name"),
        model_facts=data.get("model_facts"),
        features=data.get("features") or [],
        engine_version=data.get("engine_version"),
        lora_modules=lora_modules,
        owner_id=data.get("owner_id", ""),
        created_at=data.get("created_at"),
        updated_at=data.get("updated_at"),
        started_at=data.get("started_at"),
    )


def list(
    status: Optional[str] = None,
    workspace: Optional[str] = None,
) -> List[InferenceServer]:
    """
    List inference servers in the workspace.

    Args:
        status: Filter by status (pending, running, stopped, failed)
        workspace: Workspace ID (uses default if not specified)

    Returns:
        List of InferenceServer objects

    Example:
        # All servers
        servers = corerun.inference.list()

        # Running servers only
        running = corerun.inference.list(status="running")
    """
    client = get_client()

    params = {}
    if status:
        params["status"] = status

    response = client.get("/inference-servers", params=params, workspace=workspace)
    return [_server_from_response(s) for s in response.get("servers", [])]


def get(server_id: str, workspace: Optional[str] = None) -> InferenceServer:
    """
    Get an inference server by ID.

    Args:
        server_id: Server ID
        workspace: Workspace ID (uses default if not specified)

    Returns:
        InferenceServer object

    Example:
        server = corerun.inference.get("abc123")
        print(f"Status: {server.status}")
    """
    client = get_client()
    response = client.get(f"/inference-servers/{server_id}", workspace=workspace)
    return _server_from_response(response)


def deploy(
    name: str,
    model_id: str,
    compute_name: str,
    server_type: str = "vllm",
    model_source: str = "huggingface",
    model_version: Optional[str] = None,
    image: Optional[str] = None,
    gpu: float = 1,
    min_replicas: int = 1,
    max_replicas: int = 1,
    max_model_len: Optional[int] = None,
    tensor_parallel: Optional[int] = None,
    quantization: Optional[str] = None,
    gpu_memory_util: Optional[float] = None,
    enforce_eager: bool = False,
    lora_modules: Optional[List[LoRAModule]] = None,
    endpoint: Optional[str] = None,
    extra_args: Optional[List[str]] = None,
    served_names: Optional[List[str]] = None,
    features: Optional[List[str]] = None,
    dry_run: bool = False,
    wait: bool = False,
    timeout: int = 600,
    workspace: Optional[str] = None,
):
    """
    Deploy a new inference server.

    The engine's arguments come from the model's published recipe, found by
    its id or -- for a path or an unpublished name -- by what its own README
    and config say it is. Pass dry_run=True to see them first: the answer is
    the plan (recipe, arguments, the opt-in features the model offers here,
    what the model said about itself), and nothing is created.

    Args:
        name: Server name
        model_id: Model identifier (HuggingFace ID, MLflow URI, or registry name)
        compute_name: Compute target name (cluster)
        server_type: Server type ("vllm" or "ollama")
        model_source: Model source ("huggingface", "mlflow", "registry", "path").
            "path" serves weights already on the machine — model_id is then the
            directory, and nothing is downloaded.
        served_names: What callers ask for, when that differs from model_id.
            Required in spirit for path-sourced models, which would otherwise
            be published under their directory. Several are allowed and the
            endpoint answers to each.
        extra_args: Flags passed to the serving engine exactly as given, after
            everything the platform sets — so a repeated flag resolves to yours.
            The platform models a handful of vLLM's options; this is for the
            rest, e.g. ["--kv-cache-dtype", "fp8", "--enable-prefix-caching"].
        endpoint: The endpoint this model answers behind. Defaults to a new one
            named after the deployment; naming an existing one adds this model
            to it, and callers pick between them by model name.
        model_version: Version for registry models
        image: Docker image (uses default for server type if not specified)
        gpu: Number of GPUs
        min_replicas: Minimum number of replicas for autoscaling
        max_replicas: Maximum number of replicas for autoscaling
        max_model_len: Maximum model context length (vLLM)
        tensor_parallel: Tensor parallel size (vLLM)
        quantization: Leave None for a checkpoint that declares its own
            quantization in config.json (compressed-tensors, NVFP4, FP8, AWQ,
            GPTQ); the engine reads it. Only to force one.
        gpu_memory_util: GPU memory utilization (vLLM, 0.0-1.0)
        enforce_eager: Disable CUDA graphs (vLLM, for unsupported GPUs)
        lora_modules: List of LoRA adapters to load on top of base model
        wait: Wait for server to be running (default: False)
        timeout: Timeout in seconds when waiting (default: 600)
        workspace: Workspace ID (uses default if not specified)

    Returns:
        InferenceServer object (includes api_key only on create)

    Example:
        # Deploy a HuggingFace model
        server = corerun.inference.deploy(
            name="mistral-7b",
            model_id="mistralai/Mistral-7B-Instruct-v0.2",
            compute_name="dgx-cluster",
            gpu=1,
            wait=True,
        )

        # Deploy from MLflow registry
        server = corerun.inference.deploy(
            name="my-finetuned-model",
            model_id="my-model@champion",  # MLflow alias
            model_source="mlflow",
            compute_name="dgx-cluster",
            gpu=1,
        )

        # Deploy with autoscaling
        server = corerun.inference.deploy(
            name="production-llm",
            model_id="meta-llama/Llama-2-7b-chat-hf",
            compute_name="dgx-cluster",
            gpu=1,
            min_replicas=2,
            max_replicas=8,
        )

        # Deploy with LoRA adapter from fine-tune job
        server = corerun.inference.deploy(
            name="finetuned-llm",
            model_id="meta-llama/Llama-2-7b-chat-hf",  # Base model
            compute_name="dgx-cluster",
            gpu=1,
            lora_modules=[
                LoRAModule(
                    name="my-adapter",
                    source="job",
                    job_id="abc123",  # Fine-tune job ID
                ),
            ],
        )

        # Use the server with OpenAI client
        from openai import OpenAI
        client = OpenAI(
            base_url=f"{server.external_path}",
            api_key=server.api_key,
        )
    """
    client = get_client()

    request = CreateInferenceServerRequest(
        name=name,
        server_type=server_type,
        model_source=model_source,
        model_id=model_id,
        model_version=model_version,
        compute_name=compute_name,
        image=image,
        gpu=gpu,
        min_replicas=min_replicas,
        max_replicas=max_replicas,
        max_model_len=max_model_len,
        tensor_parallel=tensor_parallel,
        quantization=quantization,
        gpu_memory_util=gpu_memory_util,
        enforce_eager=enforce_eager,
        lora_modules=lora_modules,
        endpoint_name=endpoint,
        extra_args=extra_args,
        served_model_names=served_names,
        features=features,
        dry_run=dry_run or None,
    )

    response = client.post(
        "/inference-servers",
        json=request.model_dump(exclude_none=True),
        workspace=workspace,
    )
    if dry_run:
        return response
    server = _server_from_response(response)

    if wait:
        server = wait_for_running(server.id, timeout=timeout, workspace=workspace)

    return server


def scale(
    server_id: str,
    min_replicas: int,
    max_replicas: int,
    workspace: Optional[str] = None,
) -> InferenceServer:
    """
    Scale an inference server.

    Args:
        server_id: Server ID
        min_replicas: Minimum number of replicas
        max_replicas: Maximum number of replicas
        workspace: Workspace ID (uses default if not specified)

    Returns:
        Updated InferenceServer object

    Example:
        server = corerun.inference.scale(
            "abc123",
            min_replicas=2,
            max_replicas=4,
        )
    """
    client = get_client()

    request = ScaleRequest(
        min_replicas=min_replicas,
        max_replicas=max_replicas,
    )

    response = client.post(
        f"/inference-servers/{server_id}/scale",
        json=request.model_dump(),
        workspace=workspace,
    )
    return _server_from_response(response)


def stop(server_id: str, workspace: Optional[str] = None) -> InferenceServer:
    """
    Stop an inference server.

    Args:
        server_id: Server ID
        workspace: Workspace ID (uses default if not specified)

    Returns:
        Updated InferenceServer object

    Example:
        server = corerun.inference.stop("abc123")
    """
    client = get_client()
    response = client.post(f"/inference-servers/{server_id}/stop", workspace=workspace)
    return _server_from_response(response)


def start(server_id: str, workspace: Optional[str] = None) -> InferenceServer:
    """
    Start a stopped inference server.

    Args:
        server_id: Server ID
        workspace: Workspace ID (uses default if not specified)

    Returns:
        Updated InferenceServer object

    Example:
        server = corerun.inference.start("abc123")
    """
    client = get_client()
    response = client.post(f"/inference-servers/{server_id}/start", workspace=workspace)
    return _server_from_response(response)


def restart(server_id: str, workspace: Optional[str] = None) -> dict:
    """
    Perform a rolling restart of a running inference server.

    Args:
        server_id: Server ID
        workspace: Workspace ID (uses default if not specified)

    Returns:
        Dict with message and server_id

    Example:
        corerun.inference.restart("abc123")
    """
    client = get_client()
    return client.post(f"/inference-servers/{server_id}/restart", workspace=workspace)


def update(
    server_id: str,
    *,
    image: Optional[str] = None,
    extra_args: Optional[List[str]] = None,
    gpu: Optional[float] = None,
    max_model_len: Optional[int] = None,
    enforce_eager: Optional[bool] = None,
    gpu_memory_util: Optional[float] = None,
    served_model_names: Optional[List[str]] = None,
    features: Optional[List[str]] = None,
    endpoint: Optional[str] = None,
    workspace: Optional[str] = None,
) -> InferenceServer:
    """
    Change how an inference server runs, and deploy it again.

    Moving it behind another endpoint (endpoint=) is the exception: that is
    routing, takes effect at once, and redeploys nothing.

    The engine reads its flags when it starts, so a change means replacing the
    workload -- but not the server. It keeps its ID, its endpoint and its key,
    which is what callers hold; making a new server instead would move every
    one of them.

    Args:
        server_id: Server ID
        image: Container image to run instead
        extra_args: Engine flags, passed through as given. This is how a model
            is launched with something the platform does not model -- tool
            calling, most commonly: vLLM needs --enable-auto-tool-choice and a
            --tool-call-parser before it will accept tools at all.
        gpu: GPUs to allocate
        max_model_len: Maximum sequence length
        enforce_eager: Disable CUDA graphs, for GPUs that need it
        gpu_memory_util: Fraction of GPU memory the engine may take, above 0
            and at most 1 (vLLM)
        served_model_names: The names callers ask for this model by. The
            endpoint routes by them, so renaming one moves its callers.
        workspace: Workspace ID (uses default if not specified)

    Returns:
        The server, deploying the change

    Example:
        # Give a served model the ability to call tools
        corerun.inference.update(
            "abc123",
            extra_args=["--enable-auto-tool-choice", "--tool-call-parser", "hermes"],
        )
    """
    body = {
        key: value
        for key, value in {
            "image": image,
            "extra_args": extra_args,
            "gpu": gpu,
            "max_model_len": max_model_len,
            "enforce_eager": enforce_eager,
            "gpu_memory_util": gpu_memory_util,
            "served_model_names": served_model_names,
            "features": features,
            "endpoint_name": endpoint,
        }.items()
        if value is not None
    }
    if not body:
        raise ValueError("nothing to change: name at least one setting")

    client = get_client()
    response = client.put(f"/inference-servers/{server_id}", json=body, workspace=workspace)
    # The update endpoint answers with the server itself, unlike restart, which
    # answers with a message: this one returns something that changed.
    return _server_from_response(response)


def metrics(server_id: str, range: str = "24h", workspace: Optional[str] = None) -> dict:
    """
    What a model server has been doing, minute by minute: requests served and
    failed, tokens, time to first token, time between tokens, end-to-end
    latency (seconds, p50/p90/p99), and -- on plans with advanced metrics --
    request lengths, the queue and the caches.

    Args:
        server_id: Server ID
        range: 1h, 6h, 24h, 7d, 30d or 90d, within what the plan keeps
        workspace: Workspace ID (uses default if not specified)

    Returns:
        ``{"step_seconds", "advanced", "retention_days", "servers": [{"server_id",
        "name", "model", "kind", "points": [...]}]}``
    """
    client = get_client()
    return client.get(f"/inference-servers/{server_id}/metrics", params={"range": range}, workspace=workspace)


def list_types(workspace: Optional[str] = None) -> list:
    """
    List available inference server types and their default images.

    Returns:
        List of server type dicts (server_type, image, description)

    Example:
        types = corerun.inference.list_types()
        for t in types:
            print(t['server_type'], t['image'])
    """
    client = get_client()
    response = client.get("/inference-servers/types", workspace=workspace)
    types = response.get("types", response) if isinstance(response, dict) else response
    # Normalize field names (API uses "name"/"default_image", SDK exposes "server_type"/"image")
    return [
        {
            "server_type": t.get("name", t.get("server_type", "")),
            "display_name": t.get("display_name", ""),
            "description": t.get("description", ""),
            "image": t.get("default_image", t.get("image", "")),
            "api_format": t.get("api_format", ""),
            "features": t.get("features", []),
        }
        for t in (types or [])
    ]


class ServingRecipe(BaseModel):
    """How a model's publisher says it should be served."""

    model_id: str
    found: bool
    # Everything below is empty when found is False, and reason says why.
    hf_id: str = ""
    title: str = ""
    provider: str = ""
    args: List[str] = []
    image: str = ""
    min_vllm_version: str = ""
    context_length: int = 0
    hardware: str = ""
    note: str = ""
    source: str = ""
    reason: str = ""


def recipe(model_id: str, workspace: Optional[str] = None) -> ServingRecipe:
    """
    How a model's publisher says it should be served.

    Deployments take these engine arguments as defaults: a flag the deployment
    sets itself -- in its own extra args, or through a field the platform has a
    name for -- wins, and the recipe fills in the rest. Worth reading before a
    deployment, because the flag that matters most is invisible without it:
    a model launched without its tool-call parser cannot call tools at all.

    Args:
        model_id: Model id as the registry or HuggingFace knows it
        workspace: Workspace ID (uses default if not specified)

    Returns:
        ServingRecipe, with found=False when nobody has published one

    Example:
        r = corerun.inference.recipe("Qwen/Qwen3.8-27B")
        if r.found:
            print(r.args, r.hardware)
    """
    client = get_client()
    response = client.get(
        "/inference-servers/recipe", params={"model": model_id}, workspace=workspace
    )
    response = response or {}
    body = response.get("recipe") or {}
    return ServingRecipe(
        model_id=response.get("model", model_id),
        found=bool(response.get("found")),
        hf_id=body.get("hf_id", ""),
        title=body.get("title", ""),
        provider=body.get("provider", ""),
        args=body.get("args") or [],
        image=body.get("image", ""),
        min_vllm_version=body.get("min_vllm_version", ""),
        context_length=body.get("context_length") or 0,
        hardware=body.get("hardware", ""),
        note=body.get("note", ""),
        source=body.get("source", ""),
        reason=response.get("reason", ""),
    )


def plan(server_id: str, workspace: Optional[str] = None) -> dict:
    """
    What this server's next deployment would run with.

    The engine arguments a deployment would use, resolved the same way the
    deployment resolves them: the server's own fields, the accelerator's own
    arguments, and the model's published recipe, with the provenance of each.
    Nothing is deployed, so this is how to see the arguments of a server that
    is already serving -- or to check what a change would do before making it.

    Args:
        server_id: Server ID
        workspace: Workspace ID (uses default if not specified)

    Returns:
        Dict with image, extra_args and where each part came from

    Example:
        plan = corerun.inference.plan("abc123")
        print(" ".join(plan["extra_args"]))
    """
    client = get_client()
    return client.get(f"/inference-servers/{server_id}/plan", workspace=workspace) or {}


class CatalogueModel(BaseModel):
    """A model the platform already knows something about."""

    slug: str
    name: str
    description: Optional[str] = None
    source: Optional[str] = None
    external_id: Optional[str] = None
    quantization: Optional[str] = None
    parameter_count: Optional[str] = None
    context_length: int = 0
    requires_engine: Optional[str] = None
    min_engine_version: Optional[str] = None
    # What this build needs to fit and how many cards it wants, as the
    # publisher stated for this variant -- the numbers a deployment is really
    # choosing between when it picks a precision.
    min_gpu_memory_gb: int = 0
    tensor_parallel: int = 0
    tags: Optional[dict] = None

    @property
    def features(self) -> List[str]:
        """Capabilities this model has, as the publisher lists them."""
        labels = (self.tags or {}).get("labels") or []
        return [l[len("supports "):] for l in labels if l.startswith("supports ")]

    @property
    def variants(self) -> dict:
        """The other builds of this model, by name."""
        return (self.tags or {}).get("variants") or {}


def catalogue(
    limit: int = 0,
    engine: Optional[str] = None,
    workspace: Optional[str] = None,
) -> List[CatalogueModel]:
    """
    The models available to deploy, as the catalogue lists them.

    Not the same thing as the model registry: the registry holds models this
    workspace has, with weights in its own storage, while the catalogue holds
    models the platform knows something about -- how much context they hold,
    which engine serves them, which checkpoint they came from. A deployment can
    name either.

    Args:
        limit: Only the first N, for a quick look
        engine: Only models this engine serves (vllm, sglang, triton)
        workspace: Workspace ID (uses default if not specified)

    Returns:
        List of CatalogueModel

    Example:
        for model in corerun.inference.catalogue(engine="vllm")[:10]:
            print(model.name, model.external_id)
    """
    client = get_client()
    params = {"engine": engine} if engine else None
    response = client.get("/inference-servers/catalog/models", params=params, workspace=workspace)
    rows = (response or {}).get("models") or []
    models = [
        CatalogueModel(
            slug=row.get("Slug", ""),
            name=row.get("Name", ""),
            description=row.get("Description"),
            source=row.get("Source"),
            external_id=row.get("ExternalID"),
            quantization=row.get("Quantization"),
            parameter_count=row.get("ParameterCount"),
            context_length=row.get("ContextLength") or 0,
            requires_engine=row.get("RequiresEngine"),
            min_engine_version=row.get("MinEngineVersion"),
            min_gpu_memory_gb=row.get("MinGPUMemoryGB") or 0,
            tensor_parallel=row.get("TensorParallel") or 0,
            tags=row.get("Tags"),
        )
        for row in rows
    ]
    return models[:limit] if limit > 0 else models


def catalogue_entry(reference: str, workspace: Optional[str] = None) -> Optional[CatalogueModel]:
    """
    One catalogue entry, by slug or by the model id its weights are fetched by.

    Example:
        model = corerun.inference.catalogue_entry("qwen3-8-27b-nvfp4")
    """
    wanted = reference.strip().lower()
    for model in catalogue(workspace=workspace):
        if model.slug.lower() == wanted or (model.external_id or "").lower() == wanted:
            return model
    return None


def delete(server_id: str, workspace: Optional[str] = None) -> None:
    """
    Delete an inference server.

    Args:
        server_id: Server ID
        workspace: Workspace ID (uses default if not specified)

    Example:
        corerun.inference.delete("abc123")
    """
    client = get_client()
    client.delete(f"/inference-servers/{server_id}", workspace=workspace)


def wait_for_running(
    server_id: str,
    timeout: int = 600,
    poll_interval: int = 10,
    workspace: Optional[str] = None,
) -> InferenceServer:
    """
    Wait for an inference server to be running.

    Args:
        server_id: Server ID
        timeout: Maximum wait time in seconds (default: 600, models take time to load)
        poll_interval: Seconds between status checks
        workspace: Workspace ID (uses default if not specified)

    Returns:
        InferenceServer object (in running state)

    Raises:
        TimeoutError: If server doesn't start within timeout
        Exception: If server fails to start

    Example:
        server = corerun.inference.deploy(...)
        server = corerun.inference.wait_for_running(server.id)
        print(f"API endpoint: {server.external_path}")
    """
    start_time = time.time()

    while time.time() - start_time < timeout:
        server = get(server_id, workspace=workspace)

        if server.is_running and server.external_path:
            return server

        if server.is_failed:
            raise Exception(f"Inference server failed: {server.error}")

        time.sleep(poll_interval)

    raise TimeoutError(f"Inference server {server_id} did not start within {timeout}s")

