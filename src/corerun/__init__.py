"""
corerun Python SDK

A Python client library for the corerun ML Platform.

Usage:
    import corerun

    # Initialize with API key
    corerun.init(auth_token="...", workspace="my-workspace")

    # Or use environment variables
    # CORERUN_AUTH_TOKEN, CORERUN_WORKSPACE

    # List datasets
    datasets = corerun.datasets.list()

    # Import from HuggingFace
    ds = corerun.datasets.from_huggingface("ylecun/mnist", name="mnist")

    # Submit a training job with experiment tracking
    job = corerun.jobs.submit(
        name="train-model",
        image="pytorch/pytorch:2.0.0-cuda11.7-cudnn8-runtime",
        command=["python", "train.py"],
        gpu=1,
        experiment="my-experiment",  # Group outputs by experiment
        datasets=["mnist"],
    )

    # Wait for completion
    job.wait()

Model Registry:
    import corerun

    corerun.init()

    # Register a version of a model whose files are already in storage
    version = corerun.registry.create_version(
        "my-model",
        storage_path="models/my-model/1",
        framework="pytorch",
    )

    # Set alias and stage
    corerun.registry.set_alias("my-model", "champion", version.version)
    corerun.registry.set_version_stage("my-model", version.version, "production")

    # Moving files in and out is done from the CLI, which needs git-lfs:
    #   corerun models push my-model ./checkpoint
    #   corerun models pull my-model

Notebooks:
    import corerun

    corerun.init()

    # Create a Jupyter notebook
    notebook = corerun.notebooks.create(
        name="data-exploration",
        compute_name="dgx-cluster",
        gpu=1,
        datasets=["mnist"],
    )

    # Wait for it to be ready
    notebook = corerun.notebooks.wait_for_running(notebook.id)
    print(f"Open: {notebook.url}")

    # Create VSCode notebook
    notebook = corerun.notebooks.create(
        name="dev-env",
        notebook_type="code-server",
        compute_name="dgx-cluster",
        gpu=1,
    )

    # Stop/start/delete
    corerun.notebooks.stop(notebook.id)
    corerun.notebooks.start(notebook.id)
    corerun.notebooks.delete(notebook.id)

Inference Servers:
    import corerun

    corerun.init()

    # Deploy a vLLM inference server
    server = corerun.inference.deploy(
        name="mistral-7b",
        model_id="mistralai/Mistral-7B-Instruct-v0.2",
        compute_name="dgx-cluster",
        gpu=1,
        wait=True,
    )

    # Use with OpenAI client
    from openai import OpenAI
    client = OpenAI(base_url=server.external_path, api_key=server.api_key)
    response = client.chat.completions.create(
        model="mistral-7b",
        messages=[{"role": "user", "content": "Hello!"}]
    )

    # Scale and manage
    corerun.inference.scale(server.id, min_replicas=2, max_replicas=4)
    corerun.inference.stop(server.id)
    corerun.inference.delete(server.id)
"""

__version__ = "0.1.0"

from corerun import (
    clusters,
    compute,
    datasets,
    endpoints,
    org,
    prices,
    genai,
    tokens,
    finetune,
    inference,
    jobs,
    notebooks,
    quota,
    registry,
    repos,
    runs,
)
from corerun.client import CoreRunClient
from corerun.config import get_client, get_config, init

__all__ = [
    "__version__",
    "CoreRunClient",
    "init",
    "get_client",
    "get_config",
    "datasets",
    "jobs",
    "registry",
    "repos",
    "notebooks",
    "endpoints",
    "org",
    "prices",
    "inference",
    "finetune",
    "genai",
    "runs",
    "tokens",
    "clusters",
    "compute",
    "quota",
]
