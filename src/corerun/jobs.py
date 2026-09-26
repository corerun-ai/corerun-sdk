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
        source_directory="./src",  # pushed to the workspace repository "jobs"
        command=["python", "train.py"],
        gpu=1,
    )

    # Or from a repository: the workspace's own, or one on a connected host
    job = corerun.jobs.submit(..., repo="train", ref="main", path="src")
    job = corerun.jobs.submit(..., git_url="acme/trainer", connection="github")

    # Wait for completion
    job = corerun.jobs.wait(job.id)

    # Get logs
    logs = corerun.jobs.logs(job.id)

    # List jobs
    jobs = corerun.jobs.list(status="running")
"""

import time
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable, Union

from corerun.config import get_client
from corerun.models import Job, JobStatus, JobLogs, CreateJobRequest


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


def _code_source(
    name: str,
    source_directory: Optional[Union[str, Path]],
    exclude_patterns: Optional[List[str]],
    repo: Optional[str],
    git_url: Optional[str],
    connection: Optional[str],
    ref: Optional[str],
    path: Optional[str],
    workspace: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Where the job's code comes from, as the API takes it.

    A local directory is pushed first, and the job is pinned to the commit
    that push made, so what runs is exactly what was on disk at submit.
    """
    from corerun import repos

    if git_url and (repo or source_directory):
        raise ValueError("git_url names another host's repository; it cannot be combined with repo or source_directory")
    if source_directory is not None:
        target = repo or "jobs"
        repos.ensure(target, workspace=workspace)
        commit = repos.push(
            target,
            source_directory,
            branch=ref or f"job/{name}",
            message=f"job {name}",
            excludes=exclude_patterns,
            workspace=workspace,
        )
        return {"source": "repo", "repo": target, "ref": commit, "path": path}
    if repo:
        return {"source": "repo", "repo": repo, "ref": ref, "path": path}
    if git_url:
        code: Dict[str, Any] = {"source": "git", "url": git_url, "ref": ref, "path": path}
        if connection:
            code["connection_id"] = _connection_id(connection, workspace)
        return code
    return None


def _connection_id(reference: str, workspace: Optional[str]) -> str:
    """A git connection's ID from its name or its ID."""
    found = get_client().get("/git-connections", workspace=workspace).get("connections") or []
    for c in found:
        if reference in (c.get("id"), c.get("name")):
            return c["id"]
    raise ValueError(f"no git connection named {reference!r} in this workspace or its organisation")


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
    repo: Optional[str] = None,
    git_url: Optional[str] = None,
    connection: Optional[str] = None,
    ref: Optional[str] = None,
    path: Optional[str] = None,
    priority: Optional[str] = None,
    max_runtime_minutes: Optional[int] = None,
    install_requirements: Optional[bool] = None,
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
        source_directory: Local directory of training code. It is committed to
            a workspace repository (``repo``, default "jobs") on the branch
            ``job/<name>`` and the job clones that commit into /code.
            The directory's .gitignore applies; see corerun.repos.DEFAULT_EXCLUDES.
        working_dir: Working directory in container (default: /code when the
            job has code from a repository)
        exclude_patterns: Patterns (gitignore syntax) left out of a pushed
            source_directory, in place of the defaults.
        workspace: Workspace ID (uses default if not specified)
        repo: A workspace repository to clone into /code.
        git_url: A repository on another host: "owner/name" (with a
            connection) or a full https URL.
        connection: The git connection (name or ID) whose token reads git_url.
            A public https repository needs none.
        ref: Branch, tag or commit to check out (the default branch otherwise).
        path: A directory within the repository to use as /code.
        priority: "low", "normal" or "high".
        max_runtime_minutes: Stop the job after this long.
        install_requirements: Install requirements.txt from the working
            directory before the command. On by default when the job has code;
            False turns it off.

    Returns:
        Job object

    Note:
        A job works on local disk, and what lasts is kept in object storage:
        - /code         - the repository, cloned at start
        - /outputs      - pushed to the workspace's storage when the job ends
        - /checkpoints  - each complete checkpoint uploaded as it is written,
                          and restored when the job starts again
        - /scratch      - local only, gone with the job

        Environment variables are set:
        - CORERUN_OUTPUT_DIR=/outputs
        - CORERUN_CHECKPOINT_DIR=/checkpoints
        - CORERUN_EXPERIMENT_NAME={experiment}

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
            source_directory="./src",  # pushed to the workspace repository "jobs"
            command=["python", "train.py"],
            gpu=1,
            datasets=["mnist"],
        )
    """
    client = get_client()

    code = _code_source(
        name, source_directory, exclude_patterns, repo, git_url, connection, ref, path, workspace
    )
    if code and code["source"] != "volume" and working_dir is None:
        working_dir = "/code"

    job_config = config.copy() if config else {}
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
        code=code,
        priority=priority,
        max_runtime_minutes=max_runtime_minutes,
        install_requirements=install_requirements,
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
        source_directory: Local directory of training code, pushed to a workspace repository
        working_dir: Working directory in container
        exclude_patterns: Patterns (gitignore syntax) left out of a pushed source_directory
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
