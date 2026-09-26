"""
A range-parallel transfer for LFS objects.

git-lfs fetches an object as one stream, and names each attempt's temporary
file with a random suffix, so an interrupted transfer is neither parallel nor
resumable: the next attempt downloads the whole object again. For a model that
is one large file -- rather than the shards a big checkpoint is usually split
into -- a dropped connection therefore costs everything.

This fetches one object as several byte ranges at once, recording which are
done, so a second attempt asks only for what is missing. Both properties come
from the same mechanism: a range is a unit of work and a unit of progress.

The bytes are verified against the object's SHA-256 before being accepted. That
is not belt-and-braces here: ranges are written concurrently at different
offsets and resumed across processes, so "the file is the right length" is a
much weaker statement than usual.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qsl, urlparse

import httpx

POINTER_PREAMBLE = b"version https://git-lfs.github.com/spec/v1"

# Big enough that per-request overhead is noise against the transfer, small
# enough that losing one to a dropped connection costs little.
DEFAULT_CHUNK_SIZE = 16 * 1024 * 1024

# Matches git-lfs's own default. The real ceiling is the server: unless objects
# are served straight from the store, every range is a request through the
# platform's proxy, served by one process.
DEFAULT_JOBS = 8

# Status codes worth trying again. All of them mean "not now" rather than "not
# ever": a proxy under load sheds requests, and a transfer of many gigabytes
# through one will meet that at some point.
#
# 520-524 are Cloudflare's: the origin was unreachable or slow to answer through
# the edge -- the same "not now", from the proxy in front of the platform.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524})

# Enough attempts to ride out a proxy restart, not so many that a genuine
# failure takes minutes to report.
MAX_ATTEMPTS = 5


class TransferError(Exception):
    """A transfer that could not be completed, phrased for whoever ran it."""


class _Transient(Exception):
    """A failure worth trying again, not worth reporting."""


@dataclass
class Pointer:
    """An LFS pointer standing in for a file's real contents."""

    oid: str
    size: int
    path: Path


def parse_pointer(path: Path) -> Optional[Pointer]:
    """Read an LFS pointer, or return None if this is an ordinary file.

    A pointer is small and begins with a fixed line, so anything large is
    ordinary by inspection and never read into memory.
    """
    try:
        if path.stat().st_size > 1024:
            return None
        with path.open("rb") as handle:
            head = handle.read(1024)
    except OSError:
        return None

    if not head.startswith(POINTER_PREAMBLE):
        return None

    oid = None
    size = None
    for line in head.decode("utf-8", "replace").splitlines():
        if line.startswith("oid sha256:"):
            oid = line.split(":", 1)[1].strip()
        elif line.startswith("size "):
            try:
                size = int(line.split(" ", 1)[1])
            except ValueError:
                return None
    if not oid or size is None:
        return None
    return Pointer(oid=oid, size=size, path=path)


def find_pointers(checkout: Path) -> List[Pointer]:
    """Every file in the checkout still standing in for its contents."""
    pointers = []
    for path in sorted(checkout.rglob("*")):
        if ".git" in path.parts or not path.is_file():
            continue
        pointer = parse_pointer(path)
        if pointer:
            pointers.append(pointer)
    return pointers


def batch(
    repo_url: str, api_key: str, pointers: List[Pointer], client: httpx.Client
) -> Dict[str, Tuple[str, Dict[str, str]]]:
    """Ask the server where each object can be downloaded from."""
    response = client.post(
        repo_url.rstrip("/") + "/info/lfs/objects/batch",
        headers={
            "Accept": "application/vnd.git-lfs+json",
            "Content-Type": "application/vnd.git-lfs+json",
        },
        auth=("corerun", api_key),
        json={
            "operation": "download",
            "transfers": ["basic"],
            "objects": [{"oid": p.oid, "size": p.size} for p in pointers],
        },
    )
    if response.status_code >= 400:
        raise TransferError(f"the server refused the object list ({response.status_code})")

    located: Dict[str, Tuple[str, Dict[str, str]]] = {}
    for entry in response.json().get("objects", []):
        oid = entry.get("oid", "")
        error = entry.get("error")
        if error:
            raise TransferError(f"{oid[:12]}: {error.get('message', 'unavailable')}")
        action = (entry.get("actions") or {}).get("download")
        if not action:
            continue
        located[oid] = (action["href"], action.get("header") or {})
    return located


def _presigned(href: str) -> bool:
    """Whether this URL carries its own authorisation.

    Matched on the parameter combinations each scheme actually uses rather than
    on anything that looks like a signature. "signature" and "sig" alone are
    words an ordinary URL may contain, and mistaking one for a presigned URL
    would mean withholding the credential the request does need.
    """
    query = {name.lower() for name, _ in parse_qsl(urlparse(href).query)}
    return (
        "x-amz-signature" in query  # S3 and compatibles, SigV4
        or "x-goog-signature" in query  # Google Cloud Storage, V4
        or {"signature", "awsaccesskeyid"} <= query  # S3, the older SigV2
        or {"signature", "key-pair-id"} <= query  # CloudFront, and HuggingFace
        or {"sig", "sv"} <= query  # Azure blob SAS
    )


def _ranges(size: int, chunk_size: int) -> List[Tuple[int, int]]:
    """Split a file into inclusive byte ranges."""
    return [(start, min(start + chunk_size, size) - 1) for start in range(0, size, chunk_size)]


class _Counter:
    """Byte counter shared by the threads fetching one object."""

    def __init__(self, done: int, total: int, name: str, report) -> None:
        self._done = done
        self._total = total
        self._name = name
        self._report = report
        self._lock = threading.Lock()
        self._emit()

    def advance(self, count: int) -> None:
        with self._lock:
            self._done += count
            self._emit()

    def mark(self) -> int:
        """The count as it stands, to roll back to if an attempt fails."""
        with self._lock:
            return self._done

    def reset(self, done: int) -> None:
        with self._lock:
            self._done = done
            self._emit()

    def _emit(self) -> None:
        if self._report:
            self._report(self._name, min(self._done, self._total), self._total)


def download_object(
    href: str,
    headers: Dict[str, str],
    oid: str,
    size: int,
    destination: Path,
    scratch: Path,
    api_key: str,
    client: httpx.Client,
    jobs: int = DEFAULT_JOBS,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    report: Optional[Callable[[str, int, int], None]] = None,
) -> None:
    """Fetch one object in parallel, resuming whatever a previous run finished."""
    scratch.mkdir(parents=True, exist_ok=True)
    partial = scratch / f"{oid}.partial"
    ledger = scratch / f"{oid}.parts"

    chunks = _ranges(size, chunk_size)
    done: Set[int] = set()

    # The ledger is trusted only when it agrees with the file it describes. A
    # partial of the wrong length means the two are out of step, and re-fetching
    # is cheaper than reasoning about which of them is right.
    if partial.exists() and partial.stat().st_size == size and ledger.exists():
        try:
            done = {int(i) for i in json.loads(ledger.read_text())}
        except (OSError, ValueError):
            done = set()
    else:
        # Preallocated, so ranges can be written at their true offsets from the
        # start and in any order.
        with partial.open("wb") as handle:
            handle.truncate(size)
        ledger.unlink(missing_ok=True)

    outstanding = [i for i in range(len(chunks)) if i not in done]
    counter = _Counter(min(len(done) * chunk_size, size), size, destination.name, report)

    if outstanding:
        request_headers = dict(headers)
        # Our credential goes only to our own proxy. A presigned URL carries its
        # authorisation in the query string, and object stores reject a request
        # that also presents an Authorization header -- one authentication
        # mechanism at a time -- so sending it would break exactly the direct
        # path that makes presigning worth having. And it should not be sent to
        # a third-party host in any case.
        if "Authorization" not in request_headers and not _presigned(href):
            credential = base64.b64encode(f"corerun:{api_key}".encode()).decode()
            request_headers["Authorization"] = f"Basic {credential}"

        fd = os.open(partial, os.O_WRONLY)
        guard = threading.Lock()

        def fetch_once(index: int) -> None:
            start, end = chunks[index]
            with client.stream(
                "GET", href, headers={**request_headers, "Range": f"bytes={start}-{end}"}
            ) as response:
                if response.status_code == 200 and len(chunks) > 1:
                    # The range was ignored and the whole object is coming to
                    # every worker; each would write the file's beginning at its
                    # own offset. Nothing to retry: it will not work next time.
                    raise TransferError("this server does not support ranged requests")
                if response.status_code in RETRYABLE_STATUS:
                    raise _Transient(f"range {index} returned {response.status_code}")
                if response.status_code not in (200, 206):
                    raise TransferError(f"range {index} returned {response.status_code}")

                offset = start
                for block in response.iter_bytes(1024 * 1024):
                    # pwrite, so concurrent writers at different offsets need
                    # not share a file position.
                    os.pwrite(fd, block, offset)
                    offset += len(block)
                    counter.advance(len(block))

        def fetch(index: int) -> int:
            """Fetch one range, trying again while the failure looks temporary.

            A retried range restarts from its own beginning rather than the
            file's, which is the point of ranges being small: a shed request
            costs one chunk, not the download. The progress counter is rolled
            back by whatever the failed attempt had written, so the bar does not
            climb past the total.
            """
            for attempt in range(1, MAX_ATTEMPTS + 1):
                before = counter.mark()
                try:
                    fetch_once(index)
                    return index
                except (_Transient, httpx.TransportError, httpx.HTTPError) as e:
                    counter.reset(before)
                    if attempt == MAX_ATTEMPTS:
                        raise TransferError(
                            f"range {index} failed after {MAX_ATTEMPTS} attempts: {e}"
                        ) from e
                    # Backing off matters more than usual here: the thing that
                    # rejected the request is very likely struggling because of
                    # this download, so retrying immediately makes it worse.
                    time.sleep(min(2 ** attempt, 30) + random.random())
            return index

        try:
            with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
                futures = [pool.submit(fetch, i) for i in outstanding]
                for future in as_completed(futures):
                    index = future.result()
                    with guard:
                        done.add(index)
                        # Written as each range lands rather than at the end:
                        # the point of it is to survive being killed.
                        ledger.write_text(json.dumps(sorted(done)))
        finally:
            os.close(fd)

    _accept(partial, ledger, destination, oid)


def _accept(partial: Path, ledger: Path, destination: Path, oid: str) -> None:
    """Verify the assembled object and put it in place."""
    digest = hashlib.sha256()
    with partial.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)

    if digest.hexdigest() != oid:
        # Refusing it is the whole point of checking: a wrong file of the right
        # length would otherwise be served as the model.
        partial.unlink(missing_ok=True)
        ledger.unlink(missing_ok=True)
        raise TransferError(
            f"{destination.name} failed its checksum and was discarded; run the pull again"
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(partial, destination)
    ledger.unlink(missing_ok=True)


def fetch_all(
    checkout: Path,
    repo_url: str,
    api_key: str,
    jobs: int = DEFAULT_JOBS,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    report: Optional[Callable[[str, int, int], None]] = None,
) -> int:
    """Replace every LFS pointer in the checkout with its contents.

    Objects are taken one at a time and parallelised within, rather than several
    at once: a model's weights are a handful of large files, so the concurrency
    is worth more inside one of them than spread across them.
    """
    pointers = find_pointers(checkout)
    if not pointers:
        return 0

    scratch = checkout / ".git" / "lfs" / "incomplete"

    # Generous, and per-read rather than per-transfer: a range of a large object
    # over a slow link is still making progress long after a default timeout.
    timeout = httpx.Timeout(30.0, read=300.0)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        located = batch(repo_url, api_key, pointers, client)

        for pointer in pointers:
            if pointer.oid not in located:
                raise TransferError(f"the server did not offer {pointer.path.name}")
            href, headers = located[pointer.oid]
            download_object(
                href=href,
                headers=headers,
                oid=pointer.oid,
                size=pointer.size,
                destination=pointer.path,
                scratch=scratch,
                api_key=api_key,
                client=client,
                jobs=jobs,
                chunk_size=chunk_size,
                report=report,
            )
    return len(pointers)


# ── Uploading ────────────────────────────────────────────────────────────────
#
# The other half, so a push needs git and nothing else. git-lfs does this as a
# clean filter on commit plus a pre-push hook; here the pointers are written
# directly and the objects sent before the refs, which is the order the
# protocol wants: a pushed commit must never name an object the store lacks.


@dataclass
class LocalObject:
    """A file to be stored as an LFS object, and the pointer that stands in for it."""

    oid: str
    size: int
    source: Path


def hash_file(path: Path) -> LocalObject:
    """The object id and size of a file, read once in blocks."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return LocalObject(oid=digest.hexdigest(), size=size, source=path)


def pointer_text(obj: LocalObject) -> str:
    """The pointer git-lfs itself would write, byte for byte."""
    return f"version https://git-lfs.github.com/spec/v1\noid sha256:{obj.oid}\nsize {obj.size}\n"


def _credential_for(href: str, headers: Dict[str, str], api_key: str) -> Dict[str, str]:
    """Our credential for our own server; nothing extra for a presigned URL."""
    out = dict(headers)
    if "Authorization" not in out and not _presigned(href):
        out["Authorization"] = "Basic " + base64.b64encode(f"corerun:{api_key}".encode()).decode()
    return out


def upload_all(
    repo_url: str,
    api_key: str,
    objects: List[LocalObject],
    report: Optional[Callable[[str, int, int], None]] = None,
) -> int:
    """Send every object the server does not already have; return how many were sent.

    The server answers an object it already holds with no actions, so a model
    that shares its base weights with another uploads only what is new.
    """
    unique = list({o.oid: o for o in objects}.values())
    if not unique:
        return 0

    timeout = httpx.Timeout(30.0, write=300.0, read=300.0)
    sent = 0
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.post(
            repo_url.rstrip("/") + "/info/lfs/objects/batch",
            headers={"Accept": "application/vnd.git-lfs+json", "Content-Type": "application/vnd.git-lfs+json"},
            auth=("corerun", api_key),
            # multipart-basic first: a large object then arrives as parts no
            # bigger than the server says, which is what gets it past a store
            # or an edge that refuses one request that large.
            json={"operation": "upload", "transfers": ["multipart-basic", "basic"], "objects": [{"oid": o.oid, "size": o.size} for o in unique]},
        )
        if response.status_code >= 400:
            raise TransferError(f"the server refused the object list ({response.status_code})")

        wanted = {o.oid: o for o in unique}
        for entry in response.json().get("objects", []):
            obj = wanted.get(entry.get("oid", ""))
            if obj is None:
                continue
            if entry.get("error"):
                raise TransferError(f"{obj.source.name}: {entry['error'].get('message', 'refused')}")
            actions = entry.get("actions") or {}
            if actions.get("parts"):
                _put_parts(client, actions, obj, api_key, report)
                sent += 1
                continue
            upload = actions.get("upload")
            if not upload:
                continue  # already stored
            _put(client, upload["href"], upload.get("header") or {}, obj, api_key, report)
            verify = actions.get("verify")
            if verify:
                answer = client.post(
                    verify["href"],
                    headers=_credential_for(verify["href"], {**(verify.get("header") or {}), "Content-Type": "application/vnd.git-lfs+json"}, api_key),
                    json={"oid": obj.oid, "size": obj.size},
                )
                if answer.status_code >= 400:
                    raise TransferError(f"{obj.source.name} did not arrive intact ({answer.status_code})")
            sent += 1
    return sent


def _put(
    client: httpx.Client,
    href: str,
    headers: Dict[str, str],
    obj: LocalObject,
    api_key: str,
    report: Optional[Callable[[str, int, int], None]],
) -> None:
    """One object as one PUT, streamed from disk, retried while the failure looks temporary.

    A single request, as git-lfs's basic transfer makes: an object store takes
    a presigned PUT of up to 5 GB, which covers the shards a checkpoint is
    normally split into.
    """
    request_headers = _credential_for(href, headers, api_key)
    # An explicit length, so the body is sent as-is rather than chunked, which
    # a presigned S3 PUT rejects.
    request_headers["Content-Length"] = str(obj.size)
    request_headers.setdefault("Content-Type", "application/octet-stream")

    for attempt in range(1, MAX_ATTEMPTS + 1):
        counter = _Counter(0, obj.size, obj.source.name, report)

        def body():
            with obj.source.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    counter.advance(len(block))
                    yield block

        try:
            response = client.put(href, headers=request_headers, content=body())
            if response.status_code in RETRYABLE_STATUS:
                raise _Transient(f"{response.status_code}")
            if response.status_code >= 400:
                raise TransferError(f"{obj.source.name}: the store refused it ({response.status_code})")
            return
        except (_Transient, httpx.TransportError) as e:
            if attempt == MAX_ATTEMPTS:
                raise TransferError(f"{obj.source.name} failed after {MAX_ATTEMPTS} attempts: {e}") from e
            time.sleep(min(2 ** attempt, 30) + random.random())


class _FileSlice:
    """A byte range of a file, read in blocks as it is sent."""

    def __init__(self, path: Path, pos: int, size: int, counter: "_Counter"):
        self.path, self.pos, self.size, self.counter = path, pos, size, counter

    def __iter__(self):
        remaining = self.size
        with self.path.open("rb") as handle:
            handle.seek(self.pos)
            while remaining > 0:
                block = handle.read(min(1024 * 1024, remaining))
                if not block:
                    break
                remaining -= len(block)
                self.counter.advance(len(block))
                yield block


def _put_parts(
    client: httpx.Client,
    actions: Dict,
    obj: LocalObject,
    api_key: str,
    report: Optional[Callable[[str, int, int], None]],
    jobs: int = 4,
) -> None:
    """Send an object as the parts the server laid out, then commit them.

    Parts go several at a time, each retried on its own while the failure
    looks temporary, so a dropped connection costs one part rather than the
    object. Anything that fails for good aborts the upload, so the store is
    not left holding parts nobody will commit.
    """
    parts = actions["parts"]
    counter = _Counter(0, obj.size, obj.source.name, report)
    etags: List[Optional[str]] = [None] * len(parts)

    def send(index: int) -> None:
        part = parts[index]
        href = part["href"]
        headers = _credential_for(href, part.get("header") or {}, api_key)
        headers["Content-Length"] = str(part["size"])
        headers.setdefault("Content-Type", "application/octet-stream")
        for attempt in range(1, MAX_ATTEMPTS + 1):
            before = counter.mark()
            try:
                response = client.put(href, headers=headers, content=_FileSlice(obj.source, part["pos"], part["size"], counter))
                if response.status_code in RETRYABLE_STATUS:
                    raise _Transient(str(response.status_code))
                if response.status_code >= 400:
                    raise TransferError(f"{obj.source.name}: part {index + 1} refused ({response.status_code})")
                # Azure answers a block with no ETag worth keeping; the
                # commit names blocks by position, so any value serves.
                etags[index] = response.headers.get("ETag") or f"part-{index + 1}"
                return
            except (_Transient, httpx.TransportError) as e:
                counter.reset(before)
                if attempt == MAX_ATTEMPTS:
                    raise TransferError(f"{obj.source.name}: part {index + 1} failed after {MAX_ATTEMPTS} attempts: {e}") from e
                time.sleep(min(2 ** attempt, 30) + random.random())

    def abort() -> None:
        action = actions.get("abort")
        if action:
            try:
                client.post(action["href"], headers=_credential_for(action["href"], action.get("header") or {}, api_key))
            except httpx.HTTPError:
                pass

    try:
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            for future in as_completed([pool.submit(send, i) for i in range(len(parts))]):
                future.result()
        commit = actions["commit"]
        response = client.post(
            commit["href"],
            headers=_credential_for(commit["href"], {**(commit.get("header") or {}), "Content-Type": "application/vnd.git-lfs+json"}, api_key),
            json={
                "oid": obj.oid,
                "size": obj.size,
                "parts": [{"part_number": i + 1, "etag": e} for i, e in enumerate(etags)],
            },
        )
        if response.status_code >= 400:
            raise TransferError(f"{obj.source.name}: assembling the parts failed ({response.status_code})")
    except BaseException:
        abort()
        raise

    verify = actions.get("verify")
    if verify:
        answer = client.post(
            verify["href"],
            headers=_credential_for(verify["href"], {**(verify.get("header") or {}), "Content-Type": "application/vnd.git-lfs+json"}, api_key),
            json={"oid": obj.oid, "size": obj.size},
        )
        if answer.status_code >= 400:
            raise TransferError(f"{obj.source.name} did not arrive intact ({answer.status_code})")
