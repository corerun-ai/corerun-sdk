"""
Data models for corerun SDK.

Uses Pydantic for validation and serialization.
"""

from datetime import datetime
from typing import Optional, List, Dict, Any, Annotated
from enum import Enum

from pydantic import BaseModel, Field, BeforeValidator, field_validator


def parse_datetime(v):
    """Parse datetime strings, handling Go's format with trailing 'Z'"""
    if isinstance(v, datetime):
        return v
    if isinstance(v, str):
        # Remove trailing 'Z' and extra timezone suffix
        v = v.rstrip("Z")
        if "+00:00" in v:
            v = v.replace("+00:00", "")
        try:
            return datetime.fromisoformat(v)
        except ValueError:
            # Try parsing with microseconds
            for fmt in [
                "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S",
            ]:
                try:
                    return datetime.strptime(v, fmt)
                except ValueError:
                    continue
    return v


DateTime = Annotated[datetime, BeforeValidator(parse_datetime)]


# =============================================================================
# Enums
# =============================================================================

class JobStatus(str, Enum):
    """Job status values.

    "stopped" and "deploying" are what a host reports: a container that was
    asked to stop, and one still being created. Leaving them out made the whole
    job list unreadable -- one unlisted status fails the model for every job in
    the response, not just that one.
    """
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STOPPED = "stopped"
    DEPLOYING = "deploying"

    def __str__(self) -> str:
        # Without this an f-string prints "JobStatus.RUNNING": a str-Enum's
        # __format__ gives the member, not its value, and every table in the
        # CLI shows this one.
        return self.value


class DatasetSource(str, Enum):
    """Dataset source types"""
    UPLOAD = "upload"
    STORAGE = "storage"
    HUGGINGFACE = "huggingface"
    KAGGLE = "kaggle"
    URL = "url"
    MARKETPLACE = "marketplace"


# =============================================================================
# Dataset Models
# =============================================================================

class Dataset(BaseModel):
    """Dataset information"""
    id: str
    name: str
    mount_path: str
    source: str
    storage_target: Optional[str] = None
    storage_path: Optional[str] = None
    description: Optional[str] = None
    current_version: Optional[str] = None
    size_bytes: int = 0
    file_count: int = 0
    config: Optional[Dict[str, Any]] = None
    created_at: DateTime
    updated_at: DateTime

    @property
    def size_mb(self) -> float:
        """Size in megabytes"""
        return self.size_bytes / (1024 * 1024)

    @property
    def size_gb(self) -> float:
        """Size in gigabytes"""
        return self.size_bytes / (1024 * 1024 * 1024)

    def __repr__(self) -> str:
        return f"Dataset(name='{self.name}', source='{self.source}', size={self.size_mb:.1f}MB)"


class DatasetStats(BaseModel):
    """Dataset statistics"""
    record_count: Optional[int] = None
    feature_count: Optional[int] = None
    size_bytes: Optional[int] = None
    file_count: Optional[int] = None
    missing_data_pct: Optional[float] = None


class DatasetColumn(BaseModel):
    """Dataset column info"""
    name: str
    type: str


class DatasetViewerResponse(BaseModel):
    """Response from dataset viewer"""
    rows: List[Dict[str, Any]]
    total_rows: int
    page: int
    page_size: int
    total_pages: int
    splits: List[str]
    columns: List[DatasetColumn]
    current_split: Optional[str] = None


class DatasetMetadata(BaseModel):
    """Dataset metadata."""
    dataset_id: str
    format: Optional[str] = None
    data_type: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    stats: Optional[DatasetStats] = None
    data_schema: Optional[Dict[str, Any]] = Field(None, alias="schema")


# =============================================================================
# Job Models
# =============================================================================

class Job(BaseModel):
    """Job information"""
    id: str
    name: str
    status: JobStatus
    compute_name: str
    cluster_id: Optional[str] = None
    profile: Optional[str] = None     # Resource profile name from cluster
    experiment: Optional[str] = None  # Experiment name for grouping job outputs
    image: str
    gpu: float = 0
    config: Optional[Dict[str, Any]] = None
    parameters: Optional[Dict[str, Any]] = None
    environment: Optional[Dict[str, str]] = None
    datasets: Optional[List[str]] = Field(default_factory=list)
    error: Optional[str] = None
    exit_code: Optional[int] = None
    owner_id: str
    started_at: Optional[DateTime] = None
    ended_at: Optional[DateTime] = None
    created_at: DateTime
    updated_at: DateTime

    @property
    def duration_seconds(self) -> Optional[float]:
        """Job duration in seconds"""
        if self.started_at:
            end = self.ended_at or datetime.utcnow()
            return (end - self.started_at).total_seconds()
        return None

    @property
    def is_finished(self) -> bool:
        """Check if job has finished"""
        return self.status in (JobStatus.SUCCEEDED, JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED)

    def __repr__(self) -> str:
        return f"Job(name='{self.name}', status='{self.status}', gpu={self.gpu})"


class JobLogs(BaseModel):
    """Job logs"""
    job_id: str
    logs: str
    timestamp: Optional[datetime] = None


# =============================================================================
# Compute Models
# =============================================================================

class ComputeTarget(BaseModel):
    """Compute target (cluster) information"""
    id: str
    name: str
    cluster_type: str
    status: str
    gpu_available: int = 0
    gpu_total: int = 0
    description: Optional[str] = None


# =============================================================================
# Request Models
# =============================================================================

class CreateJobRequest(BaseModel):
    """Request to create a job"""
    name: str
    compute_name: str
    image: str
    gpu: float = 0
    profile: Optional[str] = None     # Resource profile name from cluster
    experiment: Optional[str] = None  # Experiment name for grouping job outputs
    command: Optional[List[str]] = None
    args: Optional[List[str]] = None
    environment: Optional[Dict[str, str]] = None
    datasets: Optional[List[str]] = None
    parameters: Optional[Dict[str, Any]] = None
    config: Optional[Dict[str, Any]] = None
    code: Optional[Dict[str, Any]] = None    # {source: repo|git|volume, repo, connection_id, url, ref, path}
    priority: Optional[str] = None           # low | normal | high
    max_runtime_minutes: Optional[int] = None
    install_requirements: Optional[bool] = None  # None: on when the job has code


class ImportHuggingFaceRequest(BaseModel):
    """Request to import HuggingFace dataset"""
    name: str
    repo_id: str
    mount_path: Optional[str] = None
    subset: Optional[str] = None
    split: Optional[str] = None
    description: Optional[str] = None


class ImportKaggleRequest(BaseModel):
    """Request to import Kaggle dataset"""
    name: str
    dataset_id: str
    mount_path: Optional[str] = None
    description: Optional[str] = None


class ImportURLRequest(BaseModel):
    """Request to import dataset from URL"""
    name: str
    url: str
    mount_path: Optional[str] = None
    description: Optional[str] = None


class DatasetFile(BaseModel):
    """One file in a dataset, and where it sits within it."""
    path: str
    size: int = 0

    @property
    def size_mb(self) -> float:
        return self.size / (1024 * 1024)


class DatasetFiles(BaseModel):
    """What a dataset is made of."""
    files: List[DatasetFile]
    total: int
    size_bytes: int = 0

    def __iter__(self):
        return iter(self.files)

    def __len__(self) -> int:
        return len(self.files)


class ImportStatus(BaseModel):
    """How far a dataset import has got.

    An import answers before it finishes: the server records the dataset and
    downloads it in the background. Until this reads `completed`, the dataset's
    size and file count are still the zeros the record was created with, so a
    caller that reports them straight after starting an import reports nothing.
    """
    status: str  # pending, downloading, converting, uploading, completed, failed
    progress: Optional[float] = None
    message: Optional[str] = None
    error: Optional[str] = None

    @property
    def finished(self) -> bool:
        """Whether the import has stopped, either way."""
        return self.status in ("completed", "failed")


# =============================================================================
# Model Registry Enums
# =============================================================================

class ModelStage(str, Enum):
    """Model version stage values"""
    NONE = "none"
    STAGING = "staging"
    PRODUCTION = "production"
    ARCHIVED = "archived"


# =============================================================================
# Model Registry Models
# =============================================================================

class RegisteredModel(BaseModel):
    """Registered model information"""
    id: str = Field(..., alias="ID")
    name: str = Field(..., alias="Name")
    description: Optional[str] = Field(None, alias="Description")
    tags: Optional[Dict[str, str]] = Field(default_factory=dict, alias="Tags")
    latest_version: int = Field(0, alias="LatestVersion")
    version_count: int = Field(0, alias="VersionCount")
    created_by: Optional[str] = Field(None, alias="CreatedBy")
    created_at: DateTime = Field(..., alias="CreatedAt")
    updated_at: DateTime = Field(..., alias="UpdatedAt")

    model_config = {"populate_by_name": True}

    @field_validator("tags", mode="before")
    @classmethod
    def tags_or_empty(cls, v):
        return v or {}

    def __repr__(self) -> str:
        return f"RegisteredModel(name='{self.name}', versions={self.version_count})"


class ModelVersion(BaseModel):
    """Model version information"""
    id: str = Field(..., alias="ID")
    model_name: str = Field(..., alias="ModelName")
    version: int = Field(..., alias="Version")
    run_id: Optional[str] = Field(None, alias="RunID")
    storage_path: str = Field(..., alias="StoragePath")
    stage: ModelStage = Field(ModelStage.NONE, alias="Stage")
    framework: Optional[str] = Field(None, alias="Framework")
    description: Optional[str] = Field(None, alias="Description")
    size_bytes: int = Field(0, alias="SizeBytes")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, alias="Metadata")
    created_by: Optional[str] = Field(None, alias="CreatedBy")
    created_at: DateTime = Field(..., alias="CreatedAt")
    updated_at: DateTime = Field(..., alias="UpdatedAt")

    model_config = {"populate_by_name": True}

    @field_validator("metadata", mode="before")
    @classmethod
    def metadata_or_empty(cls, v):
        return v or {}

    @property
    def size_mb(self) -> float:
        """Size in megabytes"""
        return self.size_bytes / (1024 * 1024)

    @property
    def size_gb(self) -> float:
        """Size in gigabytes"""
        return self.size_bytes / (1024 * 1024 * 1024)

    def __repr__(self) -> str:
        return f"ModelVersion(name='{self.model_name}', version={self.version}, stage='{self.stage}')"


class ModelAlias(BaseModel):
    """Model alias pointing to a specific version"""
    id: str = Field(..., alias="ID")
    model_name: str = Field(..., alias="ModelName")
    alias: str = Field(..., alias="Alias")
    version: int = Field(..., alias="Version")
    created_by: Optional[str] = Field(None, alias="CreatedBy")
    created_at: DateTime = Field(..., alias="CreatedAt")
    updated_at: DateTime = Field(..., alias="UpdatedAt")

    model_config = {"populate_by_name": True}

    def __repr__(self) -> str:
        return f"ModelAlias(model='{self.model_name}', alias='{self.alias}', version={self.version})"


# =============================================================================
# Model Registry Request Models
# =============================================================================
