"""The sole explicit network acquisition path for official ECB €STR CSV evidence."""

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
from .ecb_estr import (
    ECB_ESTR_MACHINE_URL,
    ECB_ESTR_MAX_RESPONSE_BYTES,
    ECB_ESTR_REQUEST_PARAMETERS,
    EcbEstrAcquisitionReceipt,
    EcbEstrError,
    load_ecb_estr_receipt,
    parse_ecb_estr_csv,
    receipt_json,
)

_TIMEOUT = (10.0, 60.0)
_CHUNK_SIZE = 64 * 1024


@dataclass(frozen=True, slots=True)
class EcbEstrAcquisitionResult:
    """Paths and immutable identity created by one validated official response."""

    raw_artifact: Path
    receipt_path: Path
    receipt: EcbEstrAcquisitionReceipt
    observation_count: int
    version_count: int
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
            "version_count": self.version_count,
        }


def acquire_ecb_estr(
    *,
    repository_root: Path,
    raw_directory: Path,
    client: Any | None = None,
    clock: Callable[[], datetime] | None = None,
) -> EcbEstrAcquisitionResult:
    """Perform one bounded GET, validate it fully, then retain bytes atomically."""
    root = repository_root.resolve()
    if raw_directory.is_symlink():
        raise EcbEstrError("ECB acquisition directory must not be a symlink")
    raw_dir = raw_directory.resolve()
    approved_root = (root / "data" / "raw" / "reference_rates" / "ecb").resolve()
    if not raw_dir.is_relative_to(approved_root):
        raise EcbEstrError("ECB acquisition target must stay under data/raw/reference_rates/ecb")
    http_client = client if client is not None else requests.Session()
    try:
        response = http_client.get(
            ECB_ESTR_MACHINE_URL,
            params=dict(ECB_ESTR_REQUEST_PARAMETERS),
            headers={
                "Accept": "text/csv",
                "Accept-Encoding": "identity",
                "User-Agent": "portfolio-advisor-reference-evidence/1.0",
            },
            timeout=_TIMEOUT,
            allow_redirects=False,
            stream=True,
        )
        raw_bytes = _validated_response_bytes(response)
    except requests.RequestException as error:
        raise EcbEstrError("ECB request failed without retained evidence") from error
    dataset = parse_ecb_estr_csv(raw_bytes)
    timestamp = (clock or _utc_now)()
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise EcbEstrError("ECB acquisition clock must return a timezone-aware timestamp")
    retrieval_timestamp = timestamp.astimezone(UTC).replace(microsecond=0).isoformat()
    digest = hashlib.sha256(raw_bytes).hexdigest()
    raw_path = raw_dir / f"estr-{digest}.csv"
    receipt_path = raw_dir / f"estr-{digest}.receipt.json"
    raw_reference = raw_path.relative_to(root).as_posix()
    headers = _response_headers(response)
    content_length_text = headers.get("content-length")
    receipt = EcbEstrAcquisitionReceipt(
        receipt_schema_version=1,
        request_url=ECB_ESTR_MACHINE_URL,
        request_parameters=canonical_request_parameters(ECB_ESTR_REQUEST_PARAMETERS),
        effective_url=str(response.url),
        retrieval_timestamp=retrieval_timestamp,
        http_status=int(response.status_code),
        response_content_type=headers["content-type"],
        content_encoding=headers.get("content-encoding", ""),
        content_length=int(content_length_text) if content_length_text is not None else None,
        content_disposition=headers["content-disposition"],
        last_modified=headers["last-modified"],
        etag=headers.get("etag"),
        byte_count=len(raw_bytes),
        raw_artifact_reference=raw_reference,
        raw_artifact_sha256=digest,
    )
    raw_dir.mkdir(parents=True, exist_ok=True)
    if raw_dir.is_symlink():
        raise EcbEstrError("ECB acquisition directory became a symlink")
    reused = _retain_pair(raw_path, raw_bytes, receipt_path, receipt)
    return EcbEstrAcquisitionResult(
        raw_artifact=raw_path,
        receipt_path=receipt_path,
        receipt=receipt,
        observation_count=dataset.observation_count,
        version_count=dataset.version_count,
        first_observation_date=dataset.first_observation_date.isoformat(),
        last_observation_date=dataset.last_observation_date.isoformat(),
        reused_files=reused,
    )


def _validated_response_bytes(response: Any) -> bytes:
    status = getattr(response, "status_code", None)
    if type(status) is not int or status != 200:
        raise EcbEstrError("ECB response status must be exactly 200")
    headers = _response_headers(response)
    if "content-type" not in headers or _media_type(headers["content-type"]) != "text/csv":
        raise EcbEstrError("ECB response Content-Type must be text/csv")
    if headers.get("content-encoding", "") not in {"", "identity"}:
        raise EcbEstrError("ECB response Content-Encoding must be absent or identity")
    if not headers.get("content-disposition"):
        raise EcbEstrError("ECB response is missing Content-Disposition provenance")
    if not headers.get("last-modified"):
        raise EcbEstrError("ECB response is missing Last-Modified dataset provenance")
    declared_length: int | None = None
    if "content-length" in headers:
        value = headers["content-length"]
        if not value.isascii() or not value.isdecimal():
            raise EcbEstrError("ECB Content-Length is malformed")
        declared_length = int(value)
        if not 0 < declared_length <= ECB_ESTR_MAX_RESPONSE_BYTES:
            raise EcbEstrError("ECB Content-Length is outside the admitted bound")
    chunks: list[bytes] = []
    size = 0
    for chunk in _response_chunks(response):
        if not isinstance(chunk, bytes):
            raise EcbEstrError("ECB response yielded a non-byte chunk")
        if not chunk:
            continue
        size += len(chunk)
        if size > ECB_ESTR_MAX_RESPONSE_BYTES:
            raise EcbEstrError("ECB response exceeded the admitted byte bound")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise EcbEstrError("ECB response body is empty")
    if declared_length is not None and declared_length != len(data):
        raise EcbEstrError("ECB Content-Length differs from received bytes")
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
    raise EcbEstrError("ECB response has no byte stream")


def _response_headers(response: Any) -> dict[str, str]:
    headers: object = getattr(response, "headers", None)
    if not isinstance(headers, Mapping):
        raise EcbEstrError("ECB response headers are unavailable")
    result: dict[str, str] = {}
    for key, value in headers.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise EcbEstrError("ECB response headers must be text")
        lowered = key.lower()
        if lowered in result:
            raise EcbEstrError("ECB response contains a duplicate normalized header")
        result[lowered] = value.strip()
    return result


def _retain_pair(
    raw_path: Path,
    raw_bytes: bytes,
    receipt_path: Path,
    receipt: EcbEstrAcquisitionReceipt,
) -> bool:
    if raw_path.is_symlink() or receipt_path.is_symlink():
        raise EcbEstrError("ECB retained evidence path must not be a symlink")
    if raw_path.exists() or receipt_path.exists():
        if not raw_path.is_file() or not receipt_path.is_file():
            raise EcbEstrError("ECB retained evidence pair is partial or not regular")
        if raw_path.read_bytes() != raw_bytes:
            raise EcbEstrError("ECB content-addressed raw path has conflicting bytes")
        existing = load_ecb_estr_receipt(receipt_path)
        if existing != receipt:
            raise EcbEstrError("ECB retained receipt conflicts with this acquisition provenance")
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
        raise EcbEstrError("unable to retain ECB raw evidence atomically") from error
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


def _media_type(value: str) -> str:
    return value.split(";", 1)[0].strip().lower()


def _utc_now() -> datetime:
    return datetime.now(UTC)
