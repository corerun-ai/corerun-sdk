"""
Jobs module for corerun SDK.

Provides functions for job management similar to MLflow/W&B patterns.

Usage:
    import corerun

    corerun.init(auth_token="...")

    # Submit a job
    job = corerun.jobs.submit(
        name="train-model",
        image="pytorch/pytorch:2.0.0-cuda11.7-cudnn8-runtime",
        command=["python", "train.py"],
        gpu=1,
        datasets=["mnist"],
    )

    # Submit a job with local training code
    job = corerun.jobs.submit(
        name="train-model",
        image="pytorch/pytorch:2.0.0-cuda11.7-cudnn8-runtime",
        source_directory="./src",  # Upload local code
        command=["python", "train.py"],
        gpu=1,
    )

    # Wait for completion
    job = corerun.jobs.wait(job.id)

    # Get logs
    logs = corerun.jobs.logs(job.id)

    # List jobs
    jobs = corerun.jobs.list(status="running")
"""

import os
import time
import tarfile
import tempfile
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable, Union

from corerun.config import get_client
from corerun.models import Job, JobStatus, JobLogs, CreateJobRequest


# Default patterns to exclude when creating source tarball
DEFAULT_EXCLUDE_PATTERNS = [
    "__pycache__",
    "*.pyc",
    "*.pyo",
    ".git",
    ".svn",
    ".hg",
    ".venv",
    "venv",
    "env",
    ".env",
    "node_modules",
    ".DS_Store",
    "*.egg-info",
    ".pytest_cache",
    ".mypy_cache",
    ".tox",
    "*.log",
    ".coverage",
    "htmlcov",
    "dist",
    "build",
    "*.so",
    "*.dylib",
]


def _should_exclude(path: Path, exclude_patterns: List[str]) -> bool:
    """Check if a path should be excluded based on patterns."""
    import fnmatch

    name = path.name
    for pattern in exclude_patterns:
        if fnmatch.fnmatch(name, pattern):
            return True
    return False


def _create_source_tarball(
    source_directory: Union[str, Path],
    exclude_patterns: Optional[List[str]] = None,
) -> Path:
    """
    Create a tar.gz archive of the source directory.

    Args:
        source_directory: Path to directory to archive
        exclude_patterns: Patterns to exclude (defaults to common dev files)

    Returns:
        Path to the created tarball (in temp directory)
    """
    source_path = Path(source_directory).resolve()
    if not source_path.is_dir():
        raise ValueError(f"Source directory does not exist: {source_directory}")

    if exclude_patterns is None:
        exclude_patterns = DEFAULT_EXCLUDE_PATTERNS

    # Create tarball in temp directory
    temp_dir = tempfile.mkdtemp(prefix="corerun_")
    tarball_path = Path(temp_dir) / "source.tar.gz"

    def filter_fn(tarinfo):
        """Filter function for tarfile to exclude patterns."""
        path = Path(tarinfo.name)
        if _should_exclude(path, exclude_patterns):
            return None
        return tarinfo

    with tarfile.open(tarball_path, "w:gz") as tar:
        # Add files with relative paths
        for item in source_path.iterdir():
            if not _should_exclude(item, exclude_patterns):
                tar.add(
                    item,
                    arcname=item.name,
                    filter=filter_fn,
                )

    return tarball_path


def _upload_source_code(
    tarball_path: Path,
    job_name: str,
    workspace: Optional[str] = None,
) -> str:
    """
    Upload source code tarball to storage.

    Args:
        tarball_path: Path to the tarball file
        job_name: Name of the job (used in storage path)
        workspace: Workspace ID

    Returns:
        Storage path where code was uploaded
    """
    client = get_client()

    # Read the tarball
    with open(tarball_path, "rb") as f:
        tarball_data = f.read()

    # Upload via multipart form
    import io

    files = {
        "file": ("source.tar.gz", io.BytesIO(tarball_data), "application/gzip"),
    }
    data = {
        "job_name": job_name,
    }

    response = client.post(
        "/jobs/upload-code",
        files=files,
        data=data,
        workspace=workspace,
    )

    return response.get("code_path", "")


def list(
    status: Optional[str] = None,
    compute: Optional[str] = None,
    workspace: Optional[str] = None,
) -> List[Job]:
    """
    List jobs in the workspace.

    Args:
        status: Filter by status (pending, running, succeeded, failed, cancelled)
        compute: Filter by compute target name
        workspace: Workspace ID (uses default if not specified)

    Returns:
        List of Job objects

    Example:
        # All jobs
        jobs = corerun.jobs.list()

        # Running jobs only
        running = corerun.jobs.list(status="running")

        # Jobs on specific compute
        gpu_jobs = corerun.jobs.list(compute="dgx-cluster")
    """
    client = get_client()

    params = {}
    if status:
        params["status"] = status
    if compute:
        params["compute"] = compute

    response = client.get("/jobs", params=params, workspace=workspace)
    return [Job(**j) for j in (response.get("jobs") or [])]


def get(job_id: str, workspace: Optional[str] = None) -> Job:
    """
    Get a job by ID.

    Args:
        job_id: Job ID
        workspace: Workspace ID (uses default if not specified)

    Returns:
        Job object

    Raises:
        NotFoundError: If job doesn't exist

    Example:
        job = corerun.jobs.get("abc123")
        print(f"Status: {job.status}")
    """
    client = get_client()
    response = client.get(f"/jobs/{job_id}", workspace=workspace)
    return Job(**response)


def submit(
    name: str,
    image: str,
    compute_name: str,
    command: Optional[List[str]] = None,
    args: Optional[List[str]] = None,
    gpu: float = 0,
    profile: Optional[str] = None,
    experiment: Optional[str] = None,
    environment: Optional[Dict[str, str]] = None,
    datasets: Optional[List[str]] = None,
    parameters: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
    source_directory: Optional[Union[str, Path]] = None,
    working_dir: Optional[str] = None,
    exclude_patterns: Optional[List[str]] = None,
    workspace: Optional[str] = None,
) -> Job:
    """
    Submit a new job.

    Args:
        name: Job name
        image: Docker image
        compute_name: Compute target name (cluster)
        command: Command to run (overrides image CMD)
        args: Arguments to command
        gpu: Number of GPUs (0 for CPU-only)
        profile: Resource profile name from cluster (defines CPU/memory limits)
        experiment: Experiment name for grouping outputs (default: "default")
        environment: Environment variables
        datasets: List of dataset names to mount
        parameters: Job parameters (passed as JSON)
        config: Additional configuration
        source_directory: Local directory containing training code to upload.
            Code is uploaded to storage and mounted at /code in the container.
            Common patterns like __pycache__, .git, venv are excluded.
        working_dir: Working directory in container (default: /code if source_directory is provided)
        exclude_patterns: Patterns to exclude when uploading source_directory.
            Defaults to common dev files (__pycache__, .git, .venv, etc.)
        workspace: Workspace ID (uses default if not specified)

    Returns:
        Job object

    Note:
        Job outputs are automatically mounted at:
        - /outputs      - Training outputs (models, predictions)
        - /checkpoints  - Checkpoints for resuming training
        - /logs         - Training logs (tensorboard, etc.)
        - /artifacts    - Other artifacts

        Environment variables are set:
        - CORERUN_OUTPUT_DIR=/outputs
        - CORERUN_CHECKPOINT_DIR=/checkpoints
        - CORERUN_LOG_DIR=/logs
        - CORERUN_ARTIFACT_DIR=/artifacts
        - CORERUN_EXPERIMENT_NAME={experiment}

        If source_directory is provided:
        - Code is uploaded to workspace storage
        - Mounted at /code in the container
        - Working directory defaults to /code
        - CORERUN_CODE_DIR=/code is set

    Example:
        # Simple job with Docker image command
        job = corerun.jobs.submit(
            name="train-resnet",
            image="pytorch/pytorch:2.0.0-cuda11.7-cudnn8-runtime",
            compute_name="dgx-cluster",
            command=["python", "train.py"],
            args=["--epochs", "100", "--lr", "0.001"],
            gpu=1,
            experiment="resnet-experiments",
            datasets=["imagenet"],
            environment={"WANDB_API_KEY": "xxx"},
        )

        # Job with local training code
        job = corerun.jobs.submit(
            name="train-custom",
            image="pytorch/pytorch:2.0.0-cuda11.7-cudnn8-runtime",
            compute_name="dgx-cluster",
            source_directory="./src",  # Upload local code
            command=["python", "train.py"],
            gpu=1,
            datasets=["mnist"],
        )
    """
    client = get_client()

    # Handle source_directory upload
    code_path = None
    if source_directory is not None:
        import shutil

        # Create tarball of source directory
        tarball_path = _create_source_tarball(source_directory, exclude_patterns)
        try:
            # Upload to storage
            code_path = _upload_source_code(tarball_path, name, workspace)
        finally:
            # Clean up temp tarball
            shutil.rmtree(tarball_path.parent, ignore_errors=True)

        # Set working directory to /code if not specified
        if working_dir is None:
            working_dir = "/code"

    # Build config with code_path and working_dir if set
    job_config = config.copy() if config else {}
    if code_path:
        job_config["code_path"] = code_path
    if working_dir:
        job_config["working_dir"] = working_dir

    request = CreateJobRequest(
        name=name,
        image=image,
        compute_name=compute_name,
        command=command,
        args=args,
        gpu=gpu,
        profile=profile,
        experiment=experiment,
        environment=environment,
        datasets=datasets,
        parameters=parameters,
        config=job_config if job_config else None,
    )

    response = client.post(
        "/jobs",
        json=request.model_dump(exclude_none=True),
        workspace=workspace,
    )
    return Job(**response)


def cancel(job_id: str, workspace: Optional[str] = None) -> Job:
    """
    Cancel a running job.

    Args:
        job_id: Job ID
        workspace: Workspace ID (uses default if not specified)

    Returns:
        Updated Job object

    Example:
        job = corerun.jobs.cancel("abc123")

    The route is `/stop`, not `/cancel`. This asked for `/cancel` and got a
    404 page back -- so `corerun jobs cancel` had never once stopped a job,
    and the only sign of it was an error that reads like a missing job rather
    than a missing route.
    """
    client = get_client()
    response = client.post(f"/jobs/{job_id}/stop", workspace=workspace)
    return Job(**response)


def delete(job_id: str, workspace: Optional[str] = None) -> None:
    """
    Delete a job.

    Args:
        job_id: Job ID
        workspace: Workspace ID (uses default if not specified)

    Example:
        corerun.jobs.delete("abc123")
    """
    client = get_client()
    client.delete(f"/jobs/{job_id}", workspace=workspace)


def logs(
    job_id: str,
    follow: bool = False,
    tail: Optional[int] = None,
    workspace: Optional[str] = None,
) -> str:
    """
    Get job logs.

    Args:
        job_id: Job ID
        follow: Stream logs (not yet implemented)
        tail: Number of lines from end
        workspace: Workspace ID (uses default if not specified)

    Returns:
        Log content as string

    Example:
        logs = corerun.jobs.logs("abc123")
        print(logs)

        # Last 100 lines
        logs = corerun.jobs.logs("abc123", tail=100)
    """
    client = get_client()

    params = {}
    if tail:
        params["tail"] = tail

    response = client.get(f"/jobs/{job_id}/logs", params=params, workspace=workspace)
    return response.get("logs", "")


def wait(
    job_id: str,
    timeout: Optional[int] = None,
    poll_interval: int = 5,
    callback: Optional[Callable[[Job], None]] = None,
    workspace: Optional[str] = None,
) -> Job:
    """
    Wait for a job to complete.

    Args:
        job_id: Job ID
        timeout: Maximum wait time in seconds (None = infinite)
        poll_interval: Seconds between status checks
        callback: Function called on each poll with current Job
        workspace: Workspace ID (uses default if not specified)

    Returns:
        Final Job object

    Raises:
        TimeoutError: If timeout exceeded

    Example:
        # Simple wait
        job = corerun.jobs.wait("abc123")

        # With timeout
        job = corerun.jobs.wait("abc123", timeout=3600)

        # With progress callback
        def on_update(job):
            print(f"Status: {job.status}")
        job = corerun.jobs.wait("abc123", callback=on_update)
    """
    from corerun.exceptions import TimeoutError

    start = time.time()

    while True:
        job = get(job_id, workspace=workspace)

        if callback:
            callback(job)

        if job.is_finished:
            return job

        if timeout and (time.time() - start) > timeout:
            raise TimeoutError(f"Job {job_id} did not complete within {timeout}s")

        time.sleep(poll_interval)


def run(
    name: str,
    image: str,
    compute_name: str,
    command: Optional[List[str]] = None,
    args: Optional[List[str]] = None,
    gpu: float = 0,
    profile: Optional[str] = None,
    experiment: Optional[str] = None,
    environment: Optional[Dict[str, str]] = None,
    datasets: Optional[List[str]] = None,
    parameters: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
    source_directory: Optional[Union[str, Path]] = None,
    working_dir: Optional[str] = None,
    exclude_patterns: Optional[List[str]] = None,
    timeout: Optional[int] = None,
    workspace: Optional[str] = None,
) -> Job:
    """
    Submit a job and wait for completion.

    Convenience function that combines submit() and wait().

    Args:
        name: Job name
        image: Docker image
        compute_name: Compute target name
        command: Command to run
        args: Arguments
        gpu: Number of GPUs
        profile: Resource profile name from cluster
        experiment: Experiment name for grouping outputs
        environment: Environment variables
        datasets: Datasets to mount
        parameters: Job parameters
        config: Additional configuration
        source_directory: Local directory containing training code to upload
        working_dir: Working directory in container
        exclude_patterns: Patterns to exclude when uploading source_directory
        timeout: Maximum wait time
        workspace: Workspace ID

    Returns:
        Completed Job object

    Example:
        job = corerun.jobs.run(
            name="quick-task",
            image="python:3.11",
            compute_name="cpu-cluster",
            command=["python", "-c", "print('Hello')"],
        )
        if job.status == "succeeded":
            print("Success!")

        # With local training code
        job = corerun.jobs.run(
            name="train-model",
            image="pytorch/pytorch:2.0.0-cuda11.7-cudnn8-runtime",
            compute_name="dgx-cluster",
            source_directory="./training",
            command=["python", "train.py"],
            gpu=1,
        )
    """
    job = submit(
        name=name,
        image=image,
        compute_name=compute_name,
        command=command,
        args=args,
        gpu=gpu,
        profile=profile,
        experiment=experiment,
        environment=environment,
        datasets=datasets,
        parameters=parameters,
        config=config,
        source_directory=source_directory,
        working_dir=working_dir,
        exclude_patterns=exclude_patterns,
        workspace=workspace,
    )
    return wait(job.id, timeout=timeout, workspace=workspace)
