"""
Fine-tuning module for corerun SDK.

Provides functions for creating and managing fine-tuning jobs.

Usage:
    import corerun

    corerun.init(auth_token="...")

    # Create a fine-tuning job
    job = corerun.finetune.create(
        name="llama-finetuned",
        framework="hf_trainer",
        base_model="meta-llama/Llama-3.2-3B-Instruct",
        dataset_id="<dataset-uuid>",
        compute_name="dgx-cluster",
        gpu=1,
        epochs=3,
    )

    # Wait for completion
    job = corerun.finetune.wait(job.id)
    print(f"Status: {job.status}")

    # List all fine-tune jobs
    jobs = corerun.finetune.list()

    # One-shot: create and wait
    job = corerun.finetune.run(
        name="llama-finetuned",
        framework="hf_trainer",
        base_model="meta-llama/Llama-3.2-3B-Instruct",
        dataset_id="<dataset-uuid>",
        compute_name="dgx-cluster",
    )
"""

import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel

from corerun.config import get_client


class FineTuneJob(BaseModel):
    """Fine-tuning job information."""

    id: str
    name: str
    framework: str          # "hf_trainer"; "unsloth" where a cluster offers its image
    base_model: str
    method: str             # "lora", "qlora", "full"
    dataset_id: str
    dataset_name: Optional[str] = None
    status: str             # "pending", "running", "succeeded", "failed"
    compute_name: str
    gpu: float = 1.0
    epochs: int = 1
    batch_size: int = 2
    learning_rate: float = 2e-4
    lora_r: int = 16
    lora_alpha: int = 16
    max_seq_length: int = 2048
    experiment: Optional[str] = None
    error: Optional[str] = None
    result_name: Optional[str] = None
    eval_fraction: Optional[float] = None
    priority: Optional[str] = None
    job_id: Optional[str] = None    # Underlying compute job ID
    owner_id: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def is_finished(self) -> bool:
        return self.status in ("succeeded", "failed", "cancelled", "stopped")

    def __repr__(self) -> str:
        return f"FineTuneJob(name='{self.name}', model='{self.base_model}', status='{self.status}')"


def create(
    name: str,
    framework: str,
    base_model: str,
    dataset_id: str,
    compute_name: str,
    method: str = "lora",
    gpu: float = 1.0,
    profile: Optional[str] = None,
    epochs: int = 1,
    batch_size: int = 2,
    learning_rate: float = 2e-4,
    lora_r: int = 16,
    lora_alpha: int = 16,
    max_seq_length: int = 2048,
    experiment: Optional[str] = None,
    environment: Optional[Dict[str, str]] = None,
    workspace: Optional[str] = None,
    result_name: Optional[str] = None,
    eval_fraction: float = 0.0,
    priority: Optional[str] = None,
    max_runtime_minutes: Optional[int] = None,
) -> FineTuneJob:
    """
    Create and start a fine-tuning job.

    Args:
        name: Job name
        framework: Training framework -- "hf_trainer". "unsloth" only where the
            cluster's profile names an image for it; otherwise it is refused.
        base_model: Hugging Face model id (e.g. "meta-llama/Llama-3.2-3B-Instruct"), or registry://name
        dataset_id: Dataset ID from workspace
        compute_name: Compute target name (cluster)
        method: Fine-tuning method — "lora", "qlora", or "full" (default: "lora")
        gpu: Number of GPUs (default: 1)
        profile: Resource profile name from cluster
        epochs: Training epochs (default: 1)
        batch_size: Batch size per device (default: 2)
        learning_rate: Learning rate (default: 2e-4)
        lora_r: LoRA rank (default: 16)
        lora_alpha: LoRA alpha (default: 16)
        max_seq_length: Maximum sequence length (default: 2048)
        experiment: Experiment name for output grouping
        environment: Extra environment variables
        workspace: Workspace ID (uses default if not specified)
        result_name: Register the result in the model registry under this
            name, as its next version, when the job succeeds
        eval_fraction: Share of the dataset held out for evaluation (0.1 is a
            90/10 split); 0 for none
        priority: "low", "normal" or "high"
        max_runtime_minutes: Stop the job after this long

    Returns:
        FineTuneJob object

    Example:
        job = corerun.finetune.create(
            name="llama3-finetuned",
            framework="hf_trainer",
            base_model="meta-llama/Llama-3.2-3B-Instruct",
            dataset_id="abc123",
            compute_name="dgx-cluster",
            gpu=1,
            epochs=3,
            method="qlora",
        )
    """
    client = get_client()

    body: Dict[str, Any] = {
        "name": name,
        "framework": framework,
        "base_model": base_model,
        "dataset_id": dataset_id,
        "compute_name": compute_name,
        "method": method,
        "gpu": gpu,
        "epochs": epochs,
        "batch_size": batch_size,
        "learning_rate": learning_rate,
        "lora_r": lora_r,
        "lora_alpha": lora_alpha,
        "max_seq_length": max_seq_length,
    }
    if profile:
        body["profile"] = profile
    if experiment:
        body["experiment"] = experiment
    if environment:
        body["environment"] = environment
    if result_name:
        body["result_name"] = result_name
    if eval_fraction:
        body["eval_fraction"] = eval_fraction
    if priority:
        body["priority"] = priority
    if max_runtime_minutes:
        body["max_runtime_minutes"] = max_runtime_minutes

    response = client.post("/finetune", json=body, workspace=workspace)
    return FineTuneJob(**response)


def dataset_id(reference: str, workspace: Optional[str] = None) -> str:
    """A dataset's id, from its name or its id."""
    import re

    if re.fullmatch(r"[0-9a-fA-F-]{36}", reference):
        return reference
    found = get_client().get(f"/data/{reference}", workspace=workspace)
    if not found.get("id"):
        raise ValueError(f"no dataset named {reference!r}")
    return found["id"]


def list(
    status: Optional[str] = None,
    framework: Optional[str] = None,
    workspace: Optional[str] = None,
) -> List[FineTuneJob]:
    """
    List fine-tuning jobs in the workspace.

    Args:
        status: Filter by status (pending, running, succeeded, failed)
        framework: Filter by framework (hf_trainer, unsloth)
        workspace: Workspace ID (uses default if not specified)

    Returns:
        List of FineTuneJob objects

    Example:
        jobs = corerun.finetune.list()
        running = corerun.finetune.list(status="running")
    """
    client = get_client()

    params: Dict[str, str] = {}
    if status:
        params["status"] = status
    if framework:
        params["framework"] = framework

    response = client.get("/finetune", params=params, workspace=workspace)
    return [FineTuneJob(**j) for j in response.get("jobs", [])]


def get(job_id: str, workspace: Optional[str] = None) -> FineTuneJob:
    """
    Get a fine-tuning job by ID.

    Args:
        job_id: Fine-tuning job ID
        workspace: Workspace ID (uses default if not specified)

    Returns:
        FineTuneJob object

    Example:
        job = corerun.finetune.get("abc123")
        print(f"Status: {job.status}")
    """
    client = get_client()
    response = client.get(f"/finetune/{job_id}", workspace=workspace)
    return FineTuneJob(**response.get("job", response))


def delete(job_id: str, workspace: Optional[str] = None) -> None:
    """
    Delete a fine-tuning job (cancels if running).

    Args:
        job_id: Fine-tuning job ID
        workspace: Workspace ID (uses default if not specified)

    Example:
        corerun.finetune.delete("abc123")
    """
    client = get_client()
    client.delete(f"/finetune/{job_id}", workspace=workspace)


def stop(job_id: str, workspace: Optional[str] = None) -> Dict[str, Any]:
    """
    Stop a running fine-tune. It keeps its record and what it wrote so far;
    delete() removes it.
    """
    return get_client().post(f"/finetune/{job_id}/stop", workspace=workspace)


def logs(job_id: str, tail: Optional[int] = None, workspace: Optional[str] = None) -> str:
    """The trainer's output, or its last ``tail`` lines."""
    params = {"tail": tail} if tail else None
    response = get_client().get(f"/finetune/{job_id}/logs", params=params, workspace=workspace)
    if isinstance(response, dict):
        return response.get("logs") or ""
    return str(response)


def wait(
    job_id: str,
    timeout: Optional[int] = None,
    poll_interval: int = 10,
    callback: Optional[Callable[[FineTuneJob], None]] = None,
    workspace: Optional[str] = None,
) -> FineTuneJob:
    """
    Wait for a fine-tuning job to complete.

    Args:
        job_id: Fine-tuning job ID
        timeout: Maximum wait time in seconds (None = infinite)
        poll_interval: Seconds between status checks (default: 10)
        callback: Function called on each poll with current FineTuneJob
        workspace: Workspace ID (uses default if not specified)

    Returns:
        Final FineTuneJob object

    Raises:
        TimeoutError: If timeout exceeded

    Example:
        job = corerun.finetune.wait("abc123", timeout=7200)
        if job.status == "succeeded":
            print("Fine-tuning complete!")
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
            raise TimeoutError(f"Fine-tune job {job_id} did not complete within {timeout}s")

        time.sleep(poll_interval)


def run(
    name: str,
    framework: str,
    base_model: str,
    dataset_id: str,
    compute_name: str,
    method: str = "lora",
    gpu: float = 1.0,
    profile: Optional[str] = None,
    epochs: int = 1,
    batch_size: int = 2,
    learning_rate: float = 2e-4,
    lora_r: int = 16,
    lora_alpha: int = 16,
    max_seq_length: int = 2048,
    experiment: Optional[str] = None,
    environment: Optional[Dict[str, str]] = None,
    timeout: Optional[int] = None,
    workspace: Optional[str] = None,
) -> FineTuneJob:
    """
    Create a fine-tuning job and wait for completion.

    Convenience function that combines create() and wait().

    Returns:
        Completed FineTuneJob object

    Example:
        job = corerun.finetune.run(
            name="llama3-tuned",
            framework="hf_trainer",
            base_model="meta-llama/Llama-3.2-3B-Instruct",
            dataset_id="abc123",
            compute_name="dgx-cluster",
            epochs=3,
            timeout=7200,
        )
        print(f"Final status: {job.status}")
    """
    job = create(
        name=name,
        framework=framework,
        base_model=base_model,
        dataset_id=dataset_id,
        compute_name=compute_name,
        method=method,
        gpu=gpu,
        profile=profile,
        epochs=epochs,
        batch_size=batch_size,
        learning_rate=learning_rate,
        lora_r=lora_r,
        lora_alpha=lora_alpha,
        max_seq_length=max_seq_length,
        experiment=experiment,
        environment=environment,
        workspace=workspace,
    )
    return wait(job.id, timeout=timeout, workspace=workspace)
