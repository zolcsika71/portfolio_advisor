"""The sole explicit network acquisition path for official daily SOFR evidence."""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from .contracts import canonical_request_parameters
from .sofr import (
    SOFR_MACHINE_URL,
    SOFR_MAX_RESPONSE_BYTES,
    SOFR_REQUEST_PARAMETERS,
    SofrAcquisitionReceipt,
    SofrError,
    _has_symlink_component,
    _lexical_absolute,
    _validate_json_content_type,
    load_sofr_receipt,
    parse_sofr_json,
    receipt_json,
)

_TIMEOUT = (10.0, 60.0)
_CHUNK_SIZE = 64 * 1024


@dataclass(frozen=True, slots=True)
class SofrAcquisitionResult:
    """Paths and immutable identity created by one bounded official response."""

    raw_artifact: Path
    receipt_path: Path
    receipt: SofrAcquisitionReceipt
    observation_count: int
    first_observation_date: str
    last_observation_date: str
    reused_files: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "byte_count": self.receipt.byte_count,
            "first_observation_date": self.first_observation_date,
            "last_observation_date": self.last_observation_date,
            "observation_count": self.observation_count,
            "raw_artifact_reference": self.receipt.raw_artifact_reference,
            "raw_artifact_sha256": self.receipt.raw_artifact_sha256,
            "receipt_fingerprint": self.receipt.fingerprint,
            "receipt_reference": self.receipt_path.name,
            "reused_files": self.reused_files,
        }


def acquire_sofr(
    *,
    repository_root: Path,
    raw_directory: Path,
    client: Any | None = None,
    clock: Callable[[], datetime] | None = None,
) -> SofrAcquisitionResult:
    """Perform one bounded GET and retain exact response bytes plus receipt."""
    root = _lexical_absolute(repository_root)
    raw_dir = _lexical_absolute(raw_directory)
    approved = root / "data" / "raw" / "reference_rates" / "new_york_fed" / "sofr"
    if raw_dir != approved or root.is_symlink() or _has_symlink_component(raw_dir, root):
        raise SofrError("SOFR acquisition target must be the dedicated approved directory")
    owned_client = client is None
    http_client = client if client is not None else requests.Session()
    if owned_client:
        http_client.trust_env = False
    try:
        response = http_client.get(
            SOFR_MACHINE_URL,
            params=dict(SOFR_REQUEST_PARAMETERS),
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "User-Agent": "portfolio-advisor-reference-evidence/2.0",
            },
            timeout=_TIMEOUT,
            allow_redirects=False,
            stream=True,
        )
        raw_bytes = _validated_response_bytes(response)
        dataset = parse_sofr_json(raw_bytes)
    except requests.RequestException as error:
        raise SofrError("New York Fed request failed without retained evidence") from error
    finally:
        if owned_client:
            http_client.close()
    timestamp = (clock or _utc_now)()
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise SofrError("SOFR acquisition clock must return a timezone-aware timestamp")
    retrieval_timestamp = timestamp.astimezone(UTC).replace(microsecond=0).isoformat()
    digest = hashlib.sha256(raw_bytes).hexdigest()
    raw_path = raw_dir / f"sofr-{digest}.json"
    receipt_path = raw_dir / f"sofr-{digest}.receipt.json"
    raw_reference = raw_path.relative_to(root).as_posix()
    headers = _response_headers(response)
    content_length_text = headers.get("content-length")
    receipt = SofrAcquisitionReceipt(
        receipt_schema_version=1,
        request_url=SOFR_MACHINE_URL,
        request_parameters=canonical_request_parameters(SOFR_REQUEST_PARAMETERS),
        effective_url=str(response.url),
        retrieval_timestamp=retrieval_timestamp,
        http_status=int(response.status_code),
        response_content_type=headers["content-type"],
        content_encoding=headers.get("content-encoding", ""),
        content_length=int(content_length_text) if content_length_text is not None else None,
        response_date=headers.get("date"),
        last_modified=headers.get("last-modified"),
        etag=headers.get("etag"),
        byte_count=len(raw_bytes),
        raw_artifact_reference=raw_reference,
        raw_artifact_sha256=digest,
    )
    raw_dir.mkdir(parents=True, exist_ok=True)
    if _has_symlink_component(raw_dir, root):
        raise SofrError("SOFR acquisition directory gained a symlink component")
    reused = _retain_pair(raw_path, raw_bytes, receipt_path, receipt)
    return SofrAcquisitionResult(
        raw_artifact=raw_path,
        receipt_path=receipt_path,
        receipt=receipt,
        observation_count=dataset.observation_count,
        first_observation_date=dataset.first_observation_date.isoformat(),
        last_observation_date=dataset.last_observation_date.isoformat(),
        reused_files=reused,
    )


def _validated_response_bytes(response: Any) -> bytes:
    status = getattr(response, "status_code", None)
    if type(status) is not int or status != 200:
        raise SofrError("New York Fed response status must be exactly 200")
    effective_url = getattr(response, "url", None)
    if not isinstance(effective_url, str):
        raise SofrError("New York Fed response effective URL is unavailable")
    SofrAcquisitionReceipt(
        receipt_schema_version=1,
        request_url=SOFR_MACHINE_URL,
        request_parameters=canonical_request_parameters(SOFR_REQUEST_PARAMETERS),
        effective_url=effective_url,
        retrieval_timestamp="2000-01-01T00:00:00+00:00",
        http_status=200,
        response_content_type=_response_headers(response).get("content-type", ""),
        content_encoding=_response_headers(response).get("content-encoding", ""),
        content_length=None,
        response_date=None,
        last_modified=None,
        etag=None,
        byte_count=1,
        raw_artifact_reference="data/raw/reference_rates/new_york_fed/sofr/placeholder.json",
        raw_artifact_sha256="0" * 64,
    )
    headers = _response_headers(response)
    if "content-type" not in headers:
        raise SofrError("New York Fed response is missing Content-Type")
    _validate_json_content_type(headers["content-type"])
    if headers.get("content-encoding", "") not in {"", "identity"}:
        raise SofrError("New York Fed Content-Encoding must be absent or identity")
    declared_length: int | None = None
    if "content-length" in headers:
        value = headers["content-length"]
        if not value.isascii() or not value.isdecimal():
            raise SofrError("New York Fed Content-Length is malformed")
        declared_length = int(value)
        if not 0 < declared_length <= SOFR_MAX_RESPONSE_BYTES:
            raise SofrError("New York Fed Content-Length is outside the admitted bound")
    chunks: list[bytes] = []
    size = 0
    for chunk in _response_chunks(response):
        if not isinstance(chunk, bytes):
            raise SofrError("New York Fed response yielded a non-byte chunk")
        if not chunk:
            continue
        size += len(chunk)
        if size > SOFR_MAX_RESPONSE_BYTES:
            raise SofrError("New York Fed response exceeded the admitted byte bound")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise SofrError("New York Fed response body is empty")
    if declared_length is not None and declared_length != len(data):
        raise SofrError("New York Fed Content-Length differs from received bytes")
    return data


def _response_chunks(response: Any) -> Iterator[bytes]:
    iterator = getattr(response, "iter_content", None)
    if callable(iterator):
        yield from iterator(chunk_size=_CHUNK_SIZE)
        return
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        yield content
        return
    raise SofrError("New York Fed response has no byte stream")


def _response_headers(response: Any) -> dict[str, str]:
    headers: object = getattr(response, "headers", None)
    if not isinstance(headers, Mapping):
        raise SofrError("New York Fed response headers are unavailable")
    result: dict[str, str] = {}
    for key, value in headers.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise SofrError("New York Fed response headers must be text")
        lowered = key.lower()
        if lowered in result:
            raise SofrError("New York Fed response contains a duplicate normalized header")
        result[lowered] = value.strip()
    return result


def _retain_pair(
    raw_path: Path,
    raw_bytes: bytes,
    receipt_path: Path,
    receipt: SofrAcquisitionReceipt,
) -> bool:
    if raw_path.is_symlink() or receipt_path.is_symlink():
        raise SofrError("SOFR retained evidence path must not be a symlink")
    if raw_path.exists() or receipt_path.exists():
        if not raw_path.is_file() or not receipt_path.is_file():
            raise SofrError("SOFR retained evidence pair is partial or not regular")
        if raw_path.read_bytes() != raw_bytes:
            raise SofrError("SOFR content-addressed raw path has conflicting bytes")
        if load_sofr_receipt(receipt_path) != receipt:
            raise SofrError("SOFR retained receipt conflicts with this acquisition provenance")
        return True
    raw_temporary: Path | None = None
    receipt_temporary: Path | None = None
    try:
        raw_temporary = _atomic_temporary(raw_path, raw_bytes)
        receipt_temporary = _atomic_temporary(
            receipt_path, receipt_json(receipt).encode("utf-8")
        )
        os.replace(raw_temporary, raw_path)
        raw_temporary = None
        os.replace(receipt_temporary, receipt_path)
        receipt_temporary = None
    except OSError as error:
        raw_path.unlink(missing_ok=True)
        receipt_path.unlink(missing_ok=True)
        raise SofrError("unable to retain SOFR raw evidence atomically") from error
    finally:
        if raw_temporary is not None:
            raw_temporary.unlink(missing_ok=True)
        if receipt_temporary is not None:
            receipt_temporary.unlink(missing_ok=True)
    return False


def _atomic_temporary(target: Path, data: bytes) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".partial", dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _utc_now() -> datetime:
    return datetime.now(UTC)
