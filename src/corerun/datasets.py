"""
Datasets module for corerun SDK.

Provides functions for dataset management similar to HuggingFace datasets API.

Usage:
    import corerun

    corerun.init(auth_token="...")

    # List datasets
    datasets = corerun.datasets.list()

    # Import from HuggingFace
    ds = corerun.datasets.from_huggingface("ylecun/mnist", name="mnist")

    # Get dataset info
    ds = corerun.datasets.get("mnist")

    # Upload a local directory, file or archive
    ds = corerun.datasets.upload("./reviews", name="reviews")

    # View dataset rows
    viewer = corerun.datasets.view("mnist", page=1, page_size=10)

    # Delete dataset
    corerun.datasets.delete("mnist")
"""

import time
from pathlib import Path
from typing import Callable, Optional, List, Dict, Any

from corerun.config import get_client
from corerun.exceptions import CoreRunError
from corerun.models import (
    Dataset,
    DatasetFile,
    DatasetFiles,
    DatasetMetadata,
    DatasetViewerResponse,
    ImportHuggingFaceRequest,
    ImportKaggleRequest,
    ImportStatus,
    ImportURLRequest,
)


def list(workspace: Optional[str] = None) -> List[Dataset]:
    """
    List all datasets in the workspace.

    Args:
        workspace: Workspace ID (uses default if not specified)

    Returns:
        List of Dataset objects

    Example:
        datasets = corerun.datasets.list()
        for ds in datasets:
            print(f"{ds.name}: {ds.size_mb:.1f}MB")
    """
    client = get_client()
    response = client.get("/data", workspace=workspace)
    return [Dataset(**d) for d in response.get("datasets", [])]


def get(name: str, workspace: Optional[str] = None) -> Dataset:
    """
    Get a dataset by name.

    Args:
        name: Dataset name
        workspace: Workspace ID (uses default if not specified)

    Returns:
        Dataset object

    Raises:
        NotFoundError: If dataset doesn't exist

    Example:
        ds = corerun.datasets.get("mnist")
        print(f"Size: {ds.size_mb:.1f}MB, Files: {ds.file_count}")
    """
    client = get_client()
    response = client.get(f"/data/{name}", workspace=workspace)
    return Dataset(**response)


def delete(name: str, workspace: Optional[str] = None) -> None:
    """
    Delete a dataset.

    Args:
        name: Dataset name
        workspace: Workspace ID (uses default if not specified)

    Raises:
        NotFoundError: If dataset doesn't exist

    Example:
        corerun.datasets.delete("old-dataset")
    """
    client = get_client()
    client.delete(f"/data/{name}", workspace=workspace)


def from_huggingface(
    repo_id: str,
    name: Optional[str] = None,
    mount_path: Optional[str] = None,
    subset: Optional[str] = None,
    split: Optional[str] = None,
    description: Optional[str] = None,
    workspace: Optional[str] = None,
) -> Dataset:
    """
    Import a dataset from HuggingFace Hub.

    Args:
        repo_id: HuggingFace dataset repo ID (e.g., "ylecun/mnist")
        name: Dataset name (defaults to repo_id slug)
        mount_path: Mount path in containers (auto-generated if not specified)
        subset: Dataset subset/config name
        split: Specific split to download
        description: Optional description
        workspace: Workspace ID (uses default if not specified)

    Returns:
        Dataset object

    Example:
        ds = corerun.datasets.from_huggingface("ylecun/mnist", name="mnist")
        ds = corerun.datasets.from_huggingface(
            "squad",
            subset="plain_text",
            split="train",
        )
    """
    client = get_client()

    # Default name from repo_id
    if name is None:
        name = repo_id.split("/")[-1].replace(".", "-").lower()

    request = ImportHuggingFaceRequest(
        name=name,
        repo_id=repo_id,
        mount_path=mount_path,
        subset=subset,
        split=split,
        description=description,
    )

    response = client.post(
        "/data/import/huggingface",
        json=request.model_dump(exclude_none=True),
        workspace=workspace,
    )
    return Dataset(**response)


def from_kaggle(
    dataset_id: str,
    name: Optional[str] = None,
    mount_path: Optional[str] = None,
    description: Optional[str] = None,
    workspace: Optional[str] = None,
) -> Dataset:
    """
    Import a dataset from Kaggle.

    Requires Kaggle credentials configured (KAGGLE_USERNAME, KAGGLE_KEY).

    Args:
        dataset_id: Kaggle dataset ID (e.g., "uciml/iris")
        name: Dataset name (defaults to dataset_id slug)
        mount_path: Mount path in containers
        description: Optional description
        workspace: Workspace ID (uses default if not specified)

    Returns:
        Dataset object

    Example:
        ds = corerun.datasets.from_kaggle("uciml/iris", name="iris")
    """
    client = get_client()

    if name is None:
        name = dataset_id.split("/")[-1].replace(".", "-").lower()

    request = ImportKaggleRequest(
        name=name,
        dataset_id=dataset_id,
        mount_path=mount_path,
        description=description,
    )

    response = client.post(
        "/data/import/kaggle",
        json=request.model_dump(exclude_none=True),
        workspace=workspace,
    )
    return Dataset(**response)


def from_url(
    url: str,
    name: str,
    mount_path: Optional[str] = None,
    description: Optional[str] = None,
    workspace: Optional[str] = None,
) -> Dataset:
    """
    Import a dataset from a URL.

    Supports direct downloads and archives (zip, tar.gz).

    Args:
        url: URL to download
        name: Dataset name
        mount_path: Mount path in containers
        description: Optional description
        workspace: Workspace ID (uses default if not specified)

    Returns:
        Dataset object

    Example:
        ds = corerun.datasets.from_url(
            "https://example.com/data.zip",
            name="my-dataset"
        )
    """
    client = get_client()

    request = ImportURLRequest(
        name=name,
        url=url,
        mount_path=mount_path,
        description=description,
    )

    response = client.post(
        "/data/import/url",
        json=request.model_dump(exclude_none=True),
        workspace=workspace,
    )
    return Dataset(**response)


# What the data service extracts. Anything else -- a directory, a CSV, a
# JSONL file -- is packed into a .tar.gz here first, because an unpacked file
# is refused there with "Unsupported file format".
_ARCHIVE_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz")


def _default_name(path: Path) -> str:
    """A dataset name from a path: its last part, without an archive suffix."""
    base = path.name
    for suffix in sorted(_ARCHIVE_SUFFIXES, key=len, reverse=True):
        if base.lower().endswith(suffix):
            base = base[: -len(suffix)]
            break
    return base.replace(".", "-").replace(" ", "-").lower() or "dataset"


def _pack(path: Path, into: Path) -> None:
    """Write path as a .tar.gz whose root is what the dataset should hold.

    A directory's contents go at the root rather than under the directory's
    own name; the service flattens a single top-level folder anyway, and
    doing it here keeps a directory holding one folder from being flattened
    twice. A single file sits at the root under its own name.
    """
    import tarfile

    with tarfile.open(into, "w:gz") as tar:
        if path.is_dir():
            for child in sorted(path.iterdir()):
                tar.add(child, arcname=child.name)
        else:
            tar.add(path, arcname=path.name)


def upload(
    path: str,
    name: Optional[str] = None,
    mount_path: Optional[str] = None,
    description: Optional[str] = None,
    workspace: Optional[str] = None,
) -> Dataset:
    """
    Upload a local directory, file or archive as a new dataset.

    The data service accepts .zip, .tar, .tar.gz and .tgz and extracts them
    into the workspace's storage, converting tabular and image files to
    Parquet on the way. An archive is sent as it is; a directory or any other
    file is packed into a .tar.gz first.

    Unlike an import, this answers once the data is stored: the returned
    dataset already carries its size, file count and detected format.

    Args:
        path: A directory, a file, or a .zip/.tar/.tar.gz/.tgz archive
        name: Dataset name (defaults to the path's last part, without the
            archive suffix)
        mount_path: Where containers see it (default /data/<name>)
        description: Optional description
        workspace: Workspace ID (uses default if not specified)

    Returns:
        Dataset object

    Raises:
        FileNotFoundError: If path does not exist
        ValidationError: If a dataset of that name exists, or the workspace
            has no storage room left

    Example:
        ds = corerun.datasets.upload("./reviews")
        ds = corerun.datasets.upload("images.zip", name="product-images")
    """
    import tempfile

    source = Path(path).expanduser()
    if not source.exists():
        raise FileNotFoundError(f"No such file or directory: {path}")

    name = name or _default_name(source.resolve())
    mount_path = mount_path or f"/data/{name}"

    fields = {"name": name, "mount_path": mount_path}
    if description:
        fields["description"] = description

    client = get_client()
    with tempfile.TemporaryDirectory(prefix="corerun-upload-") as scratch:
        if source.is_file() and source.name.lower().endswith(_ARCHIVE_SUFFIXES):
            archive = source
        else:
            archive = Path(scratch) / f"{name}.tar.gz"
            _pack(source, archive)

        with open(archive, "rb") as handle:
            response = client.post(
                "/data/upload",
                files={"file": (archive.name, handle, "application/octet-stream")},
                data=fields,
                workspace=workspace,
                # The service answers after it has extracted, converted and
                # stored everything, which for a large archive is minutes.
                timeout=None,
            )
    return Dataset(**response)


def update_metadata(
    name: str,
    *,
    tags: Optional[List[str]] = None,
    license: Optional[str] = None,
    access_level: Optional[str] = None,
    format: Optional[str] = None,
    data_type: Optional[str] = None,
    custom: Optional[Dict[str, Any]] = None,
    unset: Optional[List[str]] = None,
    workspace: Optional[str] = None,
) -> DatasetMetadata:
    """
    Change what is recorded about a dataset. Only the fields given change.

    Args:
        name: Dataset name
        tags: The dataset's tags -- replaces the list, not added to it
        license: e.g. MIT, Apache-2.0, CC-BY-4.0, proprietary
        access_level: private, team or public
        format: What the files are (csv, parquet, images, text, ...)
        data_type: What the data is (tabular, text, chat, instruction, ...)
        custom: Key/value pairs to record, merged into those already there
        unset: Custom keys to remove
        workspace: Workspace ID (uses default if not specified)

    Returns:
        The metadata as it stands after the change

    Example:
        corerun.datasets.update_metadata("reviews", tags=["nlp", "en"], license="CC-BY-4.0")
        corerun.datasets.update_metadata("reviews", custom={"owner": "search-team"})
    """
    client = get_client()

    body: Dict[str, Any] = {}
    if tags is not None:
        body["tags"] = tags
    if license is not None:
        body["license"] = license
    if access_level is not None:
        body["access_level"] = access_level
    if format is not None:
        body["format"] = format
    if data_type is not None:
        body["data_type"] = data_type
    if custom or unset:
        # The service replaces the custom map whole, so a change to one key
        # has to carry every other key with it or they are lost.
        merged = dict(metadata(name, workspace=workspace).custom or {})
        merged.update(custom or {})
        for key in unset or []:
            merged.pop(key, None)
        body["custom"] = merged

    response = client.put(f"/data/{name}/metadata", json=body, workspace=workspace)
    return DatasetMetadata(**response)


def view(
    name: str,
    split: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    workspace: Optional[str] = None,
) -> DatasetViewerResponse:
    """
    View dataset rows with pagination.

    Similar to HuggingFace dataset viewer.

    Args:
        name: Dataset name
        split: Data split (train, test, etc.)
        page: Page number (1-indexed)
        page_size: Rows per page
        workspace: Workspace ID (uses default if not specified)

    Returns:
        DatasetViewerResponse with rows and pagination info

    Example:
        viewer = corerun.datasets.view("mnist", page=1, page_size=10)
        print(f"Total rows: {viewer.total_rows}")
        for row in viewer.rows:
            print(row)
    """
    client = get_client()

    params = {"page": page, "page_size": page_size}
    if split:
        params["split"] = split

    response = client.get(f"/data/{name}/viewer", params=params, workspace=workspace)
    return DatasetViewerResponse(**response)


def import_status(name: str, workspace: Optional[str] = None) -> ImportStatus:
    """
    Get the progress of a dataset import.

    Args:
        name: Dataset name
        workspace: Workspace ID (uses default if not specified)

    Returns:
        ImportStatus object

    Example:
        status = corerun.datasets.import_status("mnist")
        print(f"{status.status}: {status.progress}%")
    """
    client = get_client()
    response = client.get(f"/data/import-status/{name}", workspace=workspace)
    return ImportStatus(**response)


def wait_for_import(
    name: str,
    workspace: Optional[str] = None,
    timeout: float = 3600.0,
    poll_interval: float = 2.0,
    on_progress: Optional[Callable[[ImportStatus], None]] = None,
) -> Dataset:
    """
    Wait for a dataset import to finish, and return the finished dataset.

    The import functions return as soon as the server has recorded the dataset;
    the download runs behind them. A caller that wants the dataset itself --
    its size, its files, anything read from it -- has to wait for that, and
    this is the wait.

    Args:
        name: Dataset name, as passed to the import
        workspace: Workspace ID (uses default if not specified)
        timeout: Seconds to wait before giving up
        poll_interval: Seconds between status checks
        on_progress: Called with each status, for progress display

    Returns:
        The imported Dataset

    Raises:
        CoreRunError: If the import failed
        TimeoutError: If it had not finished within `timeout`

    Example:
        corerun.datasets.from_huggingface("ylecun/mnist", name="mnist")
        ds = corerun.datasets.wait_for_import("mnist")
    """
    from corerun.exceptions import TimeoutError

    deadline = time.monotonic() + timeout

    while True:
        status = import_status(name, workspace=workspace)
        if on_progress is not None:
            on_progress(status)

        if status.status == "completed":
            return get(name, workspace=workspace)
        if status.status == "failed":
            raise CoreRunError(status.error or status.message or f"Import of '{name}' failed")

        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Import of '{name}' was still {status.status} after {timeout:.0f}s. "
                f"It is still running on the server; check with "
                f"corerun datasets import-status {name}"
            )
        time.sleep(poll_interval)


def files(name: str, workspace: Optional[str] = None) -> DatasetFiles:
    """
    List the files a dataset is made of.

    Args:
        name: Dataset name
        workspace: Workspace ID (uses default if not specified)

    Returns:
        DatasetFiles — iterate it for DatasetFile entries

    Example:
        for f in corerun.datasets.files("mnist"):
            print(f.path, f.size)
    """
    client = get_client()
    response = client.get(f"/data/{name}/files", workspace=workspace)
    return DatasetFiles(**response)


def download(
    name: str,
    dest: Optional[str] = None,
    workspace: Optional[str] = None,
    overwrite: bool = False,
    on_progress: Optional[Callable[[str, int, int], None]] = None,
) -> Path:
    """
    Download a dataset's files, and return the directory holding them.

    The layout inside `dest` is the dataset's own, so anything that reads a
    directory of parquet or CSV reads this one unchanged.

    This is the alternative to mounting, not a replacement for it. A mount
    costs nothing until something reads, and is what a notebook launched with
    `datasets=[...]` gets; a download is what to reach for when the compute
    target has no mount, or when the data should be local for speed.

    Args:
        name: Dataset name
        dest: Directory to download into. Defaults to ./<name>.
        workspace: Workspace ID (uses default if not specified)
        overwrite: Fetch files that are already present at the right size
        on_progress: Called with (path, index, total) as each file starts

    Returns:
        Path to the directory the dataset was written to

    Example:
        path = corerun.datasets.download("mnist")
        import pyarrow.dataset as ds
        table = ds.dataset(path, format="parquet").to_table()
    """
    client = get_client()
    listing = files(name, workspace=workspace)

    root = Path(dest) if dest else Path.cwd() / name
    root.mkdir(parents=True, exist_ok=True)

    for index, entry in enumerate(listing.files, start=1):
        target = root / entry.path
        if on_progress is not None:
            on_progress(entry.path, index, listing.total)

        # A file already here at the right size is the same file. Re-fetching
        # it is the slowest way to arrive at what is already on disk, and a
        # download interrupted halfway is the ordinary reason to run this twice.
        if not overwrite and target.exists() and target.stat().st_size == entry.size:
            continue

        client.download(
            f"/data/{name}/download",
            str(target),
            params={"path": entry.path},
            workspace=workspace,
        )

    return root


# The splits a file name can carry. This is the convention every dataset
# exported as parquet follows -- <config>-<split>-<shard>-of-<n>.parquet -- and
# it is the only record of the split that survives the export.
_SPLIT_NAMES = ("train", "test", "validation", "valid", "dev", "eval")


def _by_split(data_files: List[str]) -> Any:
    """Group a dataset's files by the split each belongs to.

    Handed a flat list, HuggingFace concatenates everything into one `train`
    split -- so mnist arrives as 70,000 rows with its 10,000 test rows mixed
    into the 60,000 it should be trained on, which is the kind of mistake that
    does not announce itself until the reported accuracy is too good.

    Falls back to the flat list when no name says which split it is, because a
    wrong split is worse than no split.
    """
    grouped: Dict[str, List[str]] = {}
    for path in data_files:
        stem = Path(path).stem.lower()
        for candidate in _SPLIT_NAMES:
            if f"-{candidate}-" in stem or stem.endswith(f"-{candidate}") or stem == candidate:
                grouped.setdefault(candidate, []).append(path)
                break
        else:
            return data_files  # one file unaccounted for, so trust none of it

    return grouped or data_files


def load(
    name: str,
    dest: Optional[str] = None,
    workspace: Optional[str] = None,
    split: Optional[str] = None,
):
    """
    Download a dataset and open it, ready to train on.

    Returns a `datasets.Dataset` when the HuggingFace `datasets` package is
    installed, and a `pyarrow.Table` otherwise. Both read the same files; the
    first is what most training code expects.

    Args:
        name: Dataset name
        dest: Where to keep the downloaded copy. Defaults to ./<name>.
        workspace: Workspace ID (uses default if not specified)
        split: Which split to return, if the dataset has more than one

    Raises:
        CoreRunError: If neither `datasets` nor `pyarrow` is available, or the
            dataset holds no file either can read

    Example:
        ds = corerun.datasets.load("mnist")
        for row in ds:
            ...
    """
    path = download(name, dest=dest, workspace=workspace)

    data_files = sorted(
        str(p) for p in path.rglob("*")
        if p.is_file() and p.suffix.lower() in (".parquet", ".csv", ".json", ".jsonl")
    )
    if not data_files:
        raise CoreRunError(
            f"Dataset '{name}' has no parquet, csv or json file to load. "
            f"Its files are in {path}; open them however they need to be opened."
        )

    fmt = Path(data_files[0]).suffix.lower().lstrip(".")
    if fmt == "jsonl":
        fmt = "json"

    try:
        from datasets import load_dataset  # type: ignore
    except ImportError:
        pass
    else:
        loaded = load_dataset(fmt, data_files=_by_split(data_files))
        return loaded[split] if split else loaded

    if fmt != "parquet":
        raise CoreRunError(
            f"Dataset '{name}' is {fmt}, which needs the HuggingFace 'datasets' "
            f"package to load. Install it, or read {path} directly."
        )
    try:
        import pyarrow.parquet as pq  # type: ignore
    except ImportError:
        raise CoreRunError(
            "Loading a dataset needs either the 'datasets' or 'pyarrow' package. "
            f"Neither is installed; the files are in {path}."
        )
    return pq.read_table(data_files)


def metadata(name: str, workspace: Optional[str] = None) -> DatasetMetadata:
    """
    Get dataset metadata.

    Args:
        name: Dataset name
        workspace: Workspace ID (uses default if not specified)

    Returns:
        DatasetMetadata object

    Example:
        meta = corerun.datasets.metadata("mnist")
        print(f"Format: {meta.format}, Type: {meta.data_type}")
    """
    client = get_client()
    response = client.get(f"/data/{name}/metadata", workspace=workspace)
    return DatasetMetadata(**response)


def info(
    provider: str,
    repo_id: Optional[str] = None,
    dataset_id: Optional[str] = None,
    url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Get dataset info from provider without downloading.

    Args:
        provider: Provider name (huggingface, kaggle, url)
        repo_id: HuggingFace repo ID (for huggingface provider)
        dataset_id: Kaggle dataset ID (for kaggle provider)
        url: URL (for url provider)

    Returns:
        Dataset info dict

    Example:
        info = corerun.datasets.info("huggingface", repo_id="ylecun/mnist")
        print(info["description"])
    """
    client = get_client()

    params = {}
    if repo_id:
        params["repo_id"] = repo_id
    if dataset_id:
        params["dataset_id"] = dataset_id
    if url:
        params["url"] = url

    return client.get(f"/data/providers/{provider}/info", params=params)
