"""One-shot, resumable acquisition of Phase E Erste Market NAV evidence."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from portfolio_advisor.canonical import canonical_fingerprint, canonical_json

from .nav_provenance import (
    ERSTE_MARKET_SOURCE_GOVERNANCE,
    PHASE_E_CUTOFF,
    PHASE_E_IDENTITY_URL,
    PHASE_E_INDEX_SCHEMA_VERSION,
    PHASE_E_MAX_RESPONSE_BYTES,
    PHASE_E_PROVIDER_CHART_IDS,
    PHASE_E_SERIES_URL,
    PHASE_E_SOURCE_CODE,
    CohortMember,
    NavProvenanceError,
    _evidence_from_entry,
    _parse_identity,
    _parse_series,
    assess_erste_market_quarantined_chart,
    prepare_bundles,
    select_phase_e_cohorts,
)

MAX_RESPONSE_BYTES = PHASE_E_MAX_RESPONSE_BYTES
TIMEOUT = (10, 30)
MAX_REDIRECTS = 5
VALID_NAV_RESPONSE = "VALID_NAV_RESPONSE"
QUARANTINED_REJECTED_RESPONSE = "QUARANTINED_REJECTED_RESPONSE"
NETWORK_FAILURE = "NETWORK_FAILURE"
_SAFE_RESPONSE_HEADERS = (
    "Cache-Control",
    "Content-Encoding",
    "Content-Length",
    "Content-Type",
    "Date",
    "ETag",
    "Last-Modified",
    "Location",
    "Retry-After",
)


@dataclass(frozen=True, slots=True)
class QuarantinedResponse:
    """Exact response bytes and non-sensitive transport metadata retained before parsing."""

    body: bytes
    body_complete: bool
    content_type: str
    final_url: str
    http_status: int
    raw_reference: str
    raw_sha256: str
    receipt_reference: str
    receipt_sha256: str
    redirect_history: tuple[dict[str, object], ...]
    response_format: str
    transport_error: str | None


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _write_json_atomic(path: Path, value: object) -> None:
    _write_atomic(path, (canonical_json(value) + "\n").encode("utf-8"))


def _safe_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {
        name.lower(): str(headers[name])
        for name in _SAFE_RESPONSE_HEADERS
        if name in headers
    }


def _response_format(body: bytes, *, status: int, content_type: str) -> str:
    if not body:
        return "EMPTY"
    sample = body[:64 * 1024].decode("utf-8", errors="replace").lower()
    if status == 429 or "rate limit" in sample or "too many requests" in sample:
        return "RATE_LIMIT_RESPONSE"
    if any(marker in sample for marker in ("access denied", "request blocked", "forbidden")):
        return "ACCESS_DENIAL_PAGE"
    if any(marker in sample for marker in ("cookie consent", "consent-manager", "süti hozzájárul")):
        return "CONSENT_PAGE"
    try:
        json.loads(body.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        if "html" in content_type.lower() or "<html" in sample or "<!doctype html" in sample:
            return "HTML"
        return "OTHER"
    return "JSON"


def _redirect_history(response: requests.Response) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "content_type": str(item.headers.get("Content-Type", "")),
            "location": str(item.headers.get("Location", "")),
            "status": int(item.status_code),
            "url": str(item.url),
        }
        for item in response.history
    )


def _capture_response_to_quarantine(
    response: requests.Response,
    *,
    repository_root: Path,
    raw_directory: Path,
    requested_url: str,
    requested_isin: str,
    role: str,
    max_response_bytes: int = MAX_RESPONSE_BYTES,
    retrieval_timestamp: str | None = None,
) -> QuarantinedResponse:
    """Retain response bytes and a receipt before any semantic or media-type check."""
    if max_response_bytes <= 0:
        raise ValueError("max_response_bytes must be positive")
    chunks: list[bytes] = []
    size = 0
    body_complete = True
    transport_error: str | None = None
    try:
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            remaining = max_response_bytes + 1 - size
            if remaining > 0:
                retained = chunk[:remaining]
                chunks.append(retained)
                size += len(retained)
            if size > max_response_bytes or len(chunk) > remaining:
                body_complete = False
                break
    except requests.RequestException as error:
        body_complete = False
        transport_error = type(error).__name__
    finally:
        response.close()
    body = b"".join(chunks)
    raw_sha = hashlib.sha256(body).hexdigest()
    quarantine_directory = raw_directory / "quarantine"
    raw_path = quarantine_directory / f"{raw_sha}.response.bin"
    if raw_path.exists():
        if raw_path.read_bytes() != body:
            raise NavProvenanceError("content-addressed quarantine artifact collision")
    else:
        _write_atomic(raw_path, body)
    raw_reference = raw_path.resolve().relative_to(repository_root.resolve()).as_posix()
    content_type = str(response.headers.get("Content-Type", ""))
    history = _redirect_history(response)
    timestamp = retrieval_timestamp or datetime.now(UTC).isoformat(timespec="microseconds")
    receipt = {
        "body_complete": body_complete,
        "byte_count": len(body),
        "content_encoding": str(response.headers.get("Content-Encoding", "")),
        "content_type": content_type,
        "final_url": str(response.url),
        "http_status": int(response.status_code),
        "max_response_bytes": max_response_bytes,
        "provider": PHASE_E_SOURCE_CODE,
        "raw_artifact_reference": raw_reference,
        "raw_artifact_sha256": raw_sha,
        "redirect_history": list(history),
        "request_role": role,
        "requested_isin": requested_isin,
        "requested_url": requested_url,
        "response_headers": _safe_headers(response.headers),
        "retention_status": "QUARANTINED_RESPONSE",
        "retrieval_timestamp": timestamp,
        "schema_version": 1,
        "transport_error": transport_error,
    }
    receipt_bytes = (canonical_json(receipt) + "\n").encode("utf-8")
    receipt_sha = hashlib.sha256(receipt_bytes).hexdigest()
    receipt_path = quarantine_directory / f"{receipt_sha}.quarantine.receipt.json"
    if receipt_path.exists():
        if receipt_path.read_bytes() != receipt_bytes:
            raise NavProvenanceError("content-addressed quarantine receipt collision")
    else:
        _write_atomic(receipt_path, receipt_bytes)
    return QuarantinedResponse(
        body=body,
        body_complete=body_complete,
        content_type=content_type,
        final_url=str(response.url),
        http_status=int(response.status_code),
        raw_reference=raw_reference,
        raw_sha256=raw_sha,
        receipt_reference=receipt_path.resolve().relative_to(repository_root.resolve()).as_posix(),
        receipt_sha256=receipt_sha,
        redirect_history=history,
        response_format=_response_format(body, status=response.status_code, content_type=content_type),
        transport_error=transport_error,
    )


def _validate_quarantined_response(
    captured: QuarantinedResponse,
    *,
    member: CohortMember,
    role: str,
    requested_url: str,
    provider_instrument_id: str,
) -> tuple[str, str | None]:
    if captured.transport_error is not None or not captured.body_complete:
        return NETWORK_FAILURE, "response body was not obtained completely"
    if captured.http_status != 200:
        return QUARANTINED_REJECTED_RESPONSE, "unexpected HTTP status"
    if captured.final_url != requested_url or captured.redirect_history:
        return QUARANTINED_REJECTED_RESPONSE, "unexpected effective URL or redirect"
    expected = "text/html" if role == "identity" else "application/json"
    if not captured.content_type.lower().startswith(expected):
        return QUARANTINED_REJECTED_RESPONSE, "wrong media type"
    if not captured.body:
        return QUARANTINED_REJECTED_RESPONSE, "empty response"
    try:
        if role == "identity":
            returned_id, _ = _parse_identity(captured.body, member)
            if provider_instrument_id and returned_id != provider_instrument_id:
                raise NavProvenanceError("identity page instrument ID changed")
        else:
            _parse_series(captured.body, member, provider_instrument_id)
    except NavProvenanceError as error:
        return QUARANTINED_REJECTED_RESPONSE, str(error)
    return VALID_NAV_RESPONSE, None


def _promote_valid_response(
    captured: QuarantinedResponse,
    *,
    repository_root: Path,
    raw_directory: Path,
    member: CohortMember,
    role: str,
    url: str,
) -> dict[str, str]:
    suffix = "html" if role == "identity" else "json"
    raw_path = raw_directory / f"{captured.raw_sha256}.{suffix}"
    if raw_path.exists():
        if raw_path.read_bytes() != captured.body:
            raise NavProvenanceError("content-addressed NAV artifact collision")
    else:
        _write_atomic(raw_path, captured.body)
    raw_reference = raw_path.resolve().relative_to(repository_root.resolve()).as_posix()
    quarantine_receipt = json.loads(
        (repository_root / captured.receipt_reference).read_text(encoding="utf-8")
    )
    receipt = {
        "byte_count": len(captured.body),
        "content_type": captured.content_type,
        "http_status": captured.http_status,
        "provider": PHASE_E_SOURCE_CODE,
        "raw_artifact_reference": raw_reference,
        "raw_artifact_sha256": captured.raw_sha256,
        "request_role": role,
        "request_url": url,
        "requested_isin": member.isin,
        "response_headers": quarantine_receipt["response_headers"],
        "retrieval_timestamp": quarantine_receipt["retrieval_timestamp"],
        "schema_version": 1,
    }
    receipt_bytes = (canonical_json(receipt) + "\n").encode("utf-8")
    receipt_sha = hashlib.sha256(receipt_bytes).hexdigest()
    receipt_path = raw_directory / f"{receipt_sha}.{role}.receipt.json"
    if receipt_path.exists():
        if receipt_path.read_bytes() != receipt_bytes:
            raise NavProvenanceError("content-addressed NAV receipt collision")
    else:
        _write_atomic(receipt_path, receipt_bytes)
    return {
        "raw_artifact_reference": raw_reference,
        "raw_artifact_sha256": captured.raw_sha256,
        "receipt_reference": receipt_path.resolve().relative_to(repository_root.resolve()).as_posix(),
        "receipt_sha256": receipt_sha,
    }


def _fetch(
    session: requests.Session,
    *,
    repository_root: Path,
    raw_directory: Path,
    member: CohortMember,
    role: str,
    url: str,
    referer: str | None = None,
) -> dict[str, str]:
    headers = {
        "Accept": "text/html" if role == "identity" else "application/json",
        "User-Agent": "PortfolioAdvisor-PhaseE-NAVEvidence/1.0",
    }
    if referer is not None:
        headers["Referer"] = referer
        headers["X-Requested-With"] = "XMLHttpRequest"
    response = session.get(
        url,
        headers=headers,
        timeout=TIMEOUT,
        allow_redirects=True,
        stream=True,
    )
    captured = _capture_response_to_quarantine(
        response,
        repository_root=repository_root,
        raw_directory=raw_directory,
        requested_url=url,
        requested_isin=member.isin,
        role=role,
    )
    provider_instrument_id = "" if role == "identity" else url.rstrip("/").rsplit("/", 1)[-1]
    classification, reason = _validate_quarantined_response(
        captured,
        member=member,
        role=role,
        requested_url=url,
        provider_instrument_id=provider_instrument_id,
    )
    if classification != VALID_NAV_RESPONSE:
        raise NavProvenanceError(
            f"{role} response for {member.isin} was quarantined: {classification}: {reason}"
        )
    return _promote_valid_response(
        captured,
        repository_root=repository_root,
        raw_directory=raw_directory,
        member=member,
        role=role,
        url=url,
    )


def _initial_index() -> dict[str, Any]:
    return {
        "bundles": [],
        "evidence_cutoff": PHASE_E_CUTOFF.isoformat(),
        "provider": PHASE_E_SOURCE_CODE,
        "schema_version": PHASE_E_INDEX_SCHEMA_VERSION,
    }


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _initial_index()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise NavProvenanceError("existing Phase E acquisition index is corrupt") from error
    if not isinstance(value, dict):
        raise NavProvenanceError("existing Phase E acquisition index is not an object")
    for key, expected in (
        ("schema_version", PHASE_E_INDEX_SCHEMA_VERSION),
        ("provider", PHASE_E_SOURCE_CODE),
        ("evidence_cutoff", PHASE_E_CUTOFF.isoformat()),
    ):
        if value.get(key) != expected:
            raise NavProvenanceError("existing Phase E acquisition index identity changed")
    if not isinstance(value.get("bundles"), list):
        raise NavProvenanceError("existing Phase E acquisition index has no bundle list")
    return value


def _entry_for(state: dict[str, Any], member: CohortMember) -> dict[str, Any]:
    bundles = state["bundles"]
    assert isinstance(bundles, list)
    matches = [item for item in bundles if isinstance(item, dict) and item.get("isin") == member.isin]
    if len(matches) > 1:
        raise NavProvenanceError("acquisition index has a duplicate ISIN")
    if matches:
        existing_entry = matches[0]
        if existing_entry.get("currency") != member.currency:
            raise NavProvenanceError("acquisition index currency changed")
        return existing_entry
    entry: dict[str, Any] = {"currency": member.currency, "isin": member.isin}
    bundles.append(entry)
    bundles.sort(key=lambda item: (str(item.get("currency")), str(item.get("isin"))))
    return entry


def _existing_role(
    *, repository_root: Path, entry: dict[str, Any], member: CohortMember, role: str
) -> bytes | None:
    raw = entry.get(role)
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise NavProvenanceError(f"retained {role} acquisition entry is malformed")
    evidence = _evidence_from_entry(repository_root, raw, isin=member.isin, role=role)
    return (repository_root / evidence.raw_reference).read_bytes()


def _prior_quarantine_receipt(
    raw_directory: Path, *, requested_url: str, requested_isin: str
) -> Path | None:
    quarantine = raw_directory / "quarantine"
    if not quarantine.is_dir():
        return None
    for path in sorted(quarantine.glob("*.quarantine.receipt.json")):
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise NavProvenanceError("existing quarantine receipt is corrupt") from error
        if not isinstance(receipt, dict):
            raise NavProvenanceError("existing quarantine receipt is malformed")
        if (
            receipt.get("requested_url") == requested_url
            and receipt.get("requested_isin") == requested_isin
        ):
            return path
    return None


def _retained_quarantined_response(
    *, repository_root: Path, receipt_path: Path
) -> QuarantinedResponse:
    """Reconstruct a captured response without altering its immutable evidence."""
    receipt_bytes = receipt_path.read_bytes()
    receipt_sha = hashlib.sha256(receipt_bytes).hexdigest()
    if receipt_path.name != f"{receipt_sha}.quarantine.receipt.json":
        raise NavProvenanceError("quarantine receipt is not content-addressed")
    try:
        receipt = json.loads(receipt_bytes.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise NavProvenanceError("existing quarantine receipt is corrupt") from error
    expected = {
        "body_complete",
        "byte_count",
        "content_encoding",
        "content_type",
        "final_url",
        "http_status",
        "max_response_bytes",
        "provider",
        "raw_artifact_reference",
        "raw_artifact_sha256",
        "redirect_history",
        "request_role",
        "requested_isin",
        "requested_url",
        "response_headers",
        "retention_status",
        "retrieval_timestamp",
        "schema_version",
        "transport_error",
    }
    if not isinstance(receipt, dict) or set(receipt) != expected:
        raise NavProvenanceError("existing quarantine receipt fields are invalid")
    raw_reference = str(receipt["raw_artifact_reference"])
    raw_path = repository_root / raw_reference
    body = raw_path.read_bytes()
    raw_sha = hashlib.sha256(body).hexdigest()
    if (
        receipt["schema_version"] != 1
        or receipt["provider"] != PHASE_E_SOURCE_CODE
        or receipt["retention_status"] != "QUARANTINED_RESPONSE"
        or raw_sha != receipt["raw_artifact_sha256"]
        or raw_path.name != f"{raw_sha}.response.bin"
        or receipt["byte_count"] != len(body)
        or receipt["max_response_bytes"] != MAX_RESPONSE_BYTES
    ):
        raise NavProvenanceError("existing quarantine response does not reconcile")
    history = receipt["redirect_history"]
    if not isinstance(history, list) or any(not isinstance(item, dict) for item in history):
        raise NavProvenanceError("existing quarantine redirect history is malformed")
    content_type = str(receipt["content_type"])
    status = int(receipt["http_status"])
    return QuarantinedResponse(
        body=body,
        body_complete=receipt["body_complete"] is True,
        content_type=content_type,
        final_url=str(receipt["final_url"]),
        http_status=status,
        raw_reference=raw_reference,
        raw_sha256=raw_sha,
        receipt_reference=receipt_path.resolve().relative_to(repository_root.resolve()).as_posix(),
        receipt_sha256=receipt_sha,
        redirect_history=tuple(history),
        response_format=_response_format(body, status=status, content_type=content_type),
        transport_error=(
            None if receipt["transport_error"] is None else str(receipt["transport_error"])
        ),
    )


def _write_content_addressed_json(
    *, repository_root: Path, directory: Path, suffix: str, value: object
) -> dict[str, str]:
    body = (canonical_json(value) + "\n").encode("utf-8")
    digest = hashlib.sha256(body).hexdigest()
    path = directory / f"{digest}.{suffix}.json"
    if path.exists():
        if path.read_bytes() != body:
            raise NavProvenanceError("content-addressed JSON artifact collision")
    else:
        _write_atomic(path, body)
    return {
        "reference": path.resolve().relative_to(repository_root.resolve()).as_posix(),
        "sha256": digest,
    }


def _semantic_series_entry(
    *,
    repository_root: Path,
    database_path: Path,
    raw_directory: Path,
    index_path: Path,
    member: CohortMember,
    captured: QuarantinedResponse,
) -> tuple[dict[str, str], dict[str, object]]:
    assessment = assess_erste_market_quarantined_chart(
        repository_root=repository_root,
        database_path=database_path,
        index_path=index_path,
        isin=member.isin,
        raw_reference=captured.raw_reference,
        raw_sha256=captured.raw_sha256,
        receipt_reference=captured.receipt_reference,
        receipt_sha256=captured.receipt_sha256,
    )
    semantic_receipt = {
        "assessment": assessment,
        "raw_artifact_reference": captured.raw_reference,
        "raw_artifact_sha256": captured.raw_sha256,
        "receipt_type": "ERSTE_MARKET_CHART_SEMANTIC_ADMISSION",
        "schema_version": 1,
        "transport_receipt_reference": captured.receipt_reference,
        "transport_receipt_sha256": captured.receipt_sha256,
    }
    retained = _write_content_addressed_json(
        repository_root=repository_root,
        directory=raw_directory / "semantic",
        suffix="semantic.receipt",
        value=semantic_receipt,
    )
    return (
        {
            "raw_artifact_reference": captured.raw_reference,
            "raw_artifact_sha256": captured.raw_sha256,
            "receipt_reference": retained["reference"],
            "receipt_sha256": retained["sha256"],
        },
        assessment,
    )


def _request_to_quarantine(
    session: requests.Session,
    *,
    repository_root: Path,
    raw_directory: Path,
    member: CohortMember,
    role: str,
    url: str,
    referer: str | None = None,
) -> QuarantinedResponse:
    headers = {
        "Accept": "text/html" if role == "identity" else "application/json",
        "User-Agent": "PortfolioAdvisor-PhaseE-NAVEvidence/1.0",
    }
    if referer is not None:
        headers["Referer"] = referer
        headers["X-Requested-With"] = "XMLHttpRequest"
    try:
        response = session.get(
            url,
            headers=headers,
            timeout=TIMEOUT,
            allow_redirects=True,
            stream=True,
        )
    except requests.RequestException as error:
        raise NavProvenanceError(
            f"NETWORK_FAILURE after one {role} request for {member.isin}: {type(error).__name__}"
        ) from error
    return _capture_response_to_quarantine(
        response,
        repository_root=repository_root,
        raw_directory=raw_directory,
        requested_url=url,
        requested_isin=member.isin,
        role=role,
    )


def _retained_or_requested_response(
    session: requests.Session,
    *,
    repository_root: Path,
    raw_directory: Path,
    member: CohortMember,
    role: str,
    url: str,
    referer: str | None = None,
) -> tuple[QuarantinedResponse, bool]:
    prior = _prior_quarantine_receipt(
        raw_directory, requested_url=url, requested_isin=member.isin
    )
    if prior is not None:
        return (
            _retained_quarantined_response(
                repository_root=repository_root, receipt_path=prior
            ),
            False,
        )
    return (
        _request_to_quarantine(
            session,
            repository_root=repository_root,
            raw_directory=raw_directory,
            member=member,
            role=role,
            url=url,
            referer=referer,
        ),
        True,
    )


def recover_at0000673322_chart(
    *,
    repository_root: Path,
    database_path: Path,
    raw_directory: Path,
    index_path: Path,
    session: requests.Session | None = None,
) -> dict[str, object]:
    """Make the one authorized recovery request without admitting its bytes."""
    raw_directory = raw_directory.resolve()
    expected_root = (repository_root / "data" / "raw" / "nav" / "erste_market").resolve()
    if raw_directory != expected_root or index_path.resolve().parent != expected_root:
        raise NavProvenanceError("Phase E acquisition paths must use the approved ignored directory")
    state = _load_state(index_path)
    member = next(
        item
        for item in select_phase_e_cohorts(database_path)["EUR"]
        if item.isin == "AT0000673322"
    )
    entry = _entry_for(state, member)
    identity_body = _existing_role(
        repository_root=repository_root,
        entry=entry,
        member=member,
        role="identity",
    )
    if identity_body is None:
        raise NavProvenanceError("retained AT0000673322 identity evidence is missing")
    if entry.get("series") is not None:
        raise NavProvenanceError("AT0000673322 already has admitted series evidence")
    instrument_id, _ = _parse_identity(identity_body, member)
    if instrument_id != "11752":
        raise NavProvenanceError("retained AT0000673322 identity no longer resolves chart 11752")
    requested_url = PHASE_E_SERIES_URL.format(instrument_id=instrument_id)
    prior = _prior_quarantine_receipt(
        raw_directory, requested_url=requested_url, requested_isin=member.isin
    )
    if prior is not None:
        raise NavProvenanceError(
            f"controlled recovery request was already retained: {prior.relative_to(repository_root)}"
        )
    headers = {
        "Accept": "application/json",
        "Referer": PHASE_E_IDENTITY_URL.format(isin=member.isin),
        "User-Agent": "PortfolioAdvisor-PhaseE-NAVEvidence/1.0",
        "X-Requested-With": "XMLHttpRequest",
    }
    owned_session = session is None
    active_session = session or requests.Session()
    active_session.trust_env = False
    active_session.max_redirects = MAX_REDIRECTS
    try:
        try:
            response = active_session.get(
                requested_url,
                headers=headers,
                timeout=TIMEOUT,
                allow_redirects=True,
                stream=True,
            )
        except requests.RequestException as error:
            return {
                "classification": NETWORK_FAILURE,
                "error_type": type(error).__name__,
                "provider": PHASE_E_SOURCE_CODE,
                "requested_isin": member.isin,
                "requested_url": requested_url,
                "requests_made": 1,
                "source_governance": ERSTE_MARKET_SOURCE_GOVERNANCE,
            }
        captured = _capture_response_to_quarantine(
            response,
            repository_root=repository_root,
            raw_directory=raw_directory,
            requested_url=requested_url,
            requested_isin=member.isin,
            role="series",
        )
    finally:
        if owned_session:
            active_session.close()
    classification, reason = _validate_quarantined_response(
        captured,
        member=member,
        role="series",
        requested_url=requested_url,
        provider_instrument_id=instrument_id,
    )
    return {
        "body_complete": captured.body_complete,
        "classification": classification,
        "content_type": captured.content_type,
        "final_url": captured.final_url,
        "http_status": captured.http_status,
        "parse_error": reason,
        "provider": PHASE_E_SOURCE_CODE,
        "raw_artifact_reference": captured.raw_reference,
        "raw_artifact_sha256": captured.raw_sha256,
        "receipt_reference": captured.receipt_reference,
        "receipt_sha256": captured.receipt_sha256,
        "redirect_history": list(captured.redirect_history),
        "requested_isin": member.isin,
        "requested_url": requested_url,
        "response_format": captured.response_format,
        "response_size": len(captured.body),
        "requests_made": 1,
        "source_governance": ERSTE_MARKET_SOURCE_GOVERNANCE,
        "strict_identity_and_nav_validation": classification == VALID_NAV_RESPONSE,
    }


def audit_phase_e_acquisition(
    *, repository_root: Path, database_path: Path, index_path: Path
) -> dict[str, object]:
    """Replay all external evidence and return one deterministic readiness audit."""
    prepared = prepare_bundles(
        repository_root=repository_root,
        database_path=database_path,
        index_path=index_path,
    )
    currency_results: dict[str, object] = {}
    currency_fingerprints: dict[str, str] = {}
    currency_readiness: dict[str, object] = {}
    all_inventory: list[dict[str, object]] = []
    for currency in ("EUR", "HUF"):
        bundles = [item for item in prepared if item.member.currency == currency]
        if len(bundles) != 8:
            raise NavProvenanceError(f"{currency} acquisition bundle is incomplete")
        group_counts: dict[tuple[str, str], int] = {}
        inventory: list[dict[str, object]] = []
        for bundle in bundles:
            group_counts[bundle.member.group] = group_counts.get(bundle.member.group, 0) + 1
            semantic_path = repository_root / bundle.series.receipt_reference
            semantic = json.loads(semantic_path.read_text(encoding="utf-8"))
            if not isinstance(semantic, dict):
                raise NavProvenanceError("semantic-admission receipt is malformed")
            item = {
                "asset_class": bundle.member.asset_class,
                "currency": currency,
                "dataset_fingerprint": bundle.dataset_fingerprint,
                "first_observation_date": bundle.observations[0].observation_date.isoformat(),
                "identity_raw_reference": bundle.identity.raw_reference,
                "identity_raw_sha256": bundle.identity.raw_sha256,
                "identity_receipt_reference": bundle.identity.receipt_reference,
                "identity_receipt_sha256": bundle.identity.receipt_sha256,
                "isin": bundle.member.isin,
                "last_observation_date": bundle.observations[-1].observation_date.isoformat(),
                "observation_count": len(bundle.observations),
                "provider_instrument_id": bundle.provider_instrument_id,
                "semantic_receipt_reference": bundle.series.receipt_reference,
                "semantic_receipt_sha256": bundle.series.receipt_sha256,
                "series_raw_reference": bundle.series.raw_reference,
                "series_raw_sha256": bundle.series.raw_sha256,
                "sub_asset_class": bundle.member.sub_asset_class,
                "transport_classification": semantic["assessment"]["transport_classification"],
                "transport_receipt_reference": semantic["transport_receipt_reference"],
                "transport_receipt_sha256": semantic["transport_receipt_sha256"],
            }
            inventory.append(item)
            all_inventory.append(item)
        groups = [
            {"asset_class": group[0], "security_count": count, "sub_asset_class": group[1]}
            for group, count in sorted(group_counts.items())
        ]
        if len(groups) < 3 or max(group_counts.values()) > 4:
            raise NavProvenanceError(f"{currency} evidence fails cohort group constraints")
        core = {
            "currency": currency,
            "evidence_cutoff": PHASE_E_CUTOFF.isoformat(),
            "instruments": inventory,
            "security_count": len(inventory),
            "source_governance": ERSTE_MARKET_SOURCE_GOVERNANCE,
            "asset_subasset_groups": groups,
        }
        bundle_fingerprint = canonical_fingerprint(core)
        readiness_status = "READY_FOR_DISPOSABLE_CANDIDATE_BUILD"
        currency_fingerprints[currency] = bundle_fingerprint
        currency_readiness[currency] = {
            "asset_subasset_group_count": len(groups),
            "maximum_group_size": max(group_counts.values()),
            "security_count": len(inventory),
            "status": readiness_status,
        }
        currency_results[currency] = {
            **core,
            "bundle_fingerprint": bundle_fingerprint,
            "readiness": readiness_status,
        }
    index_sha = hashlib.sha256(index_path.read_bytes()).hexdigest()
    combined_core = {
        "currencies": currency_fingerprints,
        "evidence_cutoff": PHASE_E_CUTOFF.isoformat(),
        "index_sha256": index_sha,
        "instrument_count": len(all_inventory),
        "source_governance": ERSTE_MARKET_SOURCE_GOVERNANCE,
    }
    readiness = {
        "currencies": currency_readiness,
        "portfolio_constructed": False,
        "sqlite_rows_created": False,
    }
    return {
        "audit_contract": "MILESTONE_11C_PHASE_E_ACQUISITION_V1",
        "combined_bundle_fingerprint": canonical_fingerprint(combined_core),
        "currencies": currency_results,
        "evidence_cutoff": PHASE_E_CUTOFF.isoformat(),
        "index_sha256": index_sha,
        "instrument_count": len(all_inventory),
        "provider": PHASE_E_SOURCE_CODE,
        "readiness": readiness,
        "source_governance": ERSTE_MARKET_SOURCE_GOVERNANCE,
    }


def _persist_acquisition_manifests(
    *, repository_root: Path, raw_directory: Path, audit: dict[str, object]
) -> dict[str, object]:
    manifest_directory = raw_directory / "manifests"
    currencies = audit["currencies"]
    assert isinstance(currencies, dict)
    retained: dict[str, object] = {}
    for currency in ("EUR", "HUF"):
        retained[currency] = _write_content_addressed_json(
            repository_root=repository_root,
            directory=manifest_directory,
            suffix=f"{currency.lower()}.bundle.manifest",
            value={
                "manifest_type": "PHASE_E_CURRENCY_ACQUISITION_BUNDLE",
                "schema_version": 1,
                **currencies[currency],
            },
        )
    readiness = _write_content_addressed_json(
        repository_root=repository_root,
        directory=manifest_directory,
        suffix="cohort.readiness.audit",
        value={
            "audit_contract": audit["audit_contract"],
            "readiness": audit["readiness"],
            "schema_version": 1,
            "source_governance": audit["source_governance"],
        },
    )
    combined_payload = {
        "audit_contract": audit["audit_contract"],
        "combined_bundle_fingerprint": audit["combined_bundle_fingerprint"],
        "currency_manifests": retained,
        "evidence_cutoff": audit["evidence_cutoff"],
        "index_sha256": audit["index_sha256"],
        "instrument_count": audit["instrument_count"],
        "provider": audit["provider"],
        "readiness_audit": readiness,
        "schema_version": 1,
        "source_governance": audit["source_governance"],
    }
    combined = _write_content_addressed_json(
        repository_root=repository_root,
        directory=manifest_directory,
        suffix="combined.acquisition.manifest",
        value=combined_payload,
    )
    return {
        "combined": combined,
        "currencies": retained,
        "readiness": readiness,
    }


def acquire_phase_e_nav(
    *, repository_root: Path, database_path: Path, raw_directory: Path, index_path: Path
) -> dict[str, object]:
    """Acquire every missing response once, fail closed, and retain offline bundles."""
    raw_directory = raw_directory.resolve()
    expected_root = (repository_root / "data" / "raw" / "nav" / "erste_market").resolve()
    if raw_directory != expected_root or index_path.resolve().parent != expected_root:
        raise NavProvenanceError("Phase E acquisition paths must use the approved ignored directory")
    raw_directory.mkdir(parents=True, exist_ok=True)
    state = _load_state(index_path)
    cohorts = select_phase_e_cohorts(database_path)
    required = [member for currency in ("EUR", "HUF") for member in cohorts[currency]]
    required_isins = {member.isin for member in required}
    bundles = state["bundles"]
    assert isinstance(bundles, list)
    if any(not isinstance(item, dict) or item.get("isin") not in required_isins for item in bundles):
        raise NavProvenanceError("acquisition index contains a non-cohort bundle")
    request_count = 0
    completed_request_count = 0
    reused_endpoint_count = 0
    session = requests.Session()
    session.trust_env = False
    session.max_redirects = MAX_REDIRECTS
    try:
        for member in required:
            entry = _entry_for(state, member)
            identity_body = _existing_role(
                repository_root=repository_root,
                entry=entry,
                member=member,
                role="identity",
            )
            if identity_body is not None:
                reused_endpoint_count += 1
            identity_url = PHASE_E_IDENTITY_URL.format(isin=member.isin)
            if identity_body is None:
                captured, requested = _retained_or_requested_response(
                    session,
                    repository_root=repository_root,
                    raw_directory=raw_directory,
                    member=member,
                    role="identity",
                    url=identity_url,
                )
                request_count += int(requested)
                completed_request_count += int(requested)
                reused_endpoint_count += int(not requested)
                classification, reason = _validate_quarantined_response(
                    captured,
                    member=member,
                    role="identity",
                    requested_url=identity_url,
                    provider_instrument_id="",
                )
                if classification != VALID_NAV_RESPONSE:
                    raise NavProvenanceError(
                        f"identity response for {member.isin} was retained but rejected: "
                        f"{classification}: {reason}"
                    )
                entry["identity"] = _promote_valid_response(
                    captured,
                    repository_root=repository_root,
                    raw_directory=raw_directory,
                    member=member,
                    role="identity",
                    url=identity_url,
                )
                _write_json_atomic(index_path, state)
                identity_body = _existing_role(
                    repository_root=repository_root,
                    entry=entry,
                    member=member,
                    role="identity",
                )
                assert identity_body is not None
            instrument_id, _ = _parse_identity(identity_body, member)
            if PHASE_E_PROVIDER_CHART_IDS.get(member.isin) != instrument_id:
                raise NavProvenanceError(
                    f"identity evidence for {member.isin} resolves unexpected chart {instrument_id}"
                )
            series_body = _existing_role(
                repository_root=repository_root,
                entry=entry,
                member=member,
                role="series",
            )
            if series_body is not None:
                reused_endpoint_count += 1
            if series_body is None:
                series_url = PHASE_E_SERIES_URL.format(instrument_id=instrument_id)
                captured, requested = _retained_or_requested_response(
                    session,
                    repository_root=repository_root,
                    raw_directory=raw_directory,
                    member=member,
                    role="series",
                    url=series_url,
                    referer=identity_url,
                )
                request_count += int(requested)
                completed_request_count += int(requested)
                reused_endpoint_count += int(not requested)
                entry["series"], _ = _semantic_series_entry(
                    repository_root=repository_root,
                    database_path=database_path,
                    raw_directory=raw_directory,
                    index_path=index_path,
                    member=member,
                    captured=captured,
                )
                _write_json_atomic(index_path, state)
    finally:
        session.close()
    _write_json_atomic(index_path, state)
    audit = audit_phase_e_acquisition(
        repository_root=repository_root,
        database_path=database_path,
        index_path=index_path,
    )
    manifests = _persist_acquisition_manifests(
        repository_root=repository_root,
        raw_directory=raw_directory,
        audit=audit,
    )
    return {
        "audit": audit,
        "bundle_count": len(state["bundles"]),
        "completed_requests": completed_request_count,
        "index_reference": index_path.resolve().relative_to(repository_root.resolve()).as_posix(),
        "index_sha256": hashlib.sha256(index_path.read_bytes()).hexdigest(),
        "manifests": manifests,
        "provider": PHASE_E_SOURCE_CODE,
        "requests_made": request_count,
        "reused_complete_endpoints": reused_endpoint_count,
        "source_governance": ERSTE_MARKET_SOURCE_GOVERNANCE,
        "status": "PHASE_E_NAV_EVIDENCE_ACQUIRED",
    }
