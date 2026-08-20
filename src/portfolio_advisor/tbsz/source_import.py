"""Fail-closed import of manually confirmed facts from George PDF evidence.

George's exported screens in this workflow can be image-only PDFs.  This module
intentionally does not use OCR or fuzzy extraction: a field enters the local
database only after a human has transcribed and marked the corresponding
source-document entry as confirmed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .models import SourceCashInput, SourceDocumentInput, SourcePositionInput, TbszError
from .repository import TbszPortfolioRepository, validate_source_document

NO_PDFS_FOUND = "NO_PDFS_FOUND"
SOURCE_FIELD_REQUIRES_MANUAL_CONFIRMATION = "SOURCE_FIELD_REQUIRES_MANUAL_CONFIRMATION"
IMPORTED = "IMPORTED"
ALREADY_IMPORTED_IDENTICAL = "ALREADY_IMPORTED_IDENTICAL"


@dataclass(frozen=True, slots=True)
class SourceImportResult:
    """One deterministic source-directory import outcome."""

    status: str
    discovered_filenames: tuple[str, ...]
    imported_filenames: tuple[str, ...]
    already_imported_filenames: tuple[str, ...]
    confirmation_required_filenames: tuple[str, ...]


def discover_george_pdfs(source_directory: Path) -> tuple[Path, ...]:
    """Discover only direct child PDF files in the explicitly scoped source directory."""
    if not source_directory.exists():
        return ()
    if not source_directory.is_dir():
        raise TbszError(f"TBSZ source path is not a directory: {source_directory}")
    return tuple(sorted((path for path in source_directory.iterdir() if path.is_file() and path.suffix.casefold() == ".pdf"), key=lambda path: path.name.casefold()))


def write_manual_confirmation_template(source_directory: Path, destination: Path) -> tuple[str, ...]:
    """Write a safe filename-only template; it never derives financial values."""
    if destination.exists():
        raise TbszError("manual confirmation already exists; refusing to overwrite it")
    filenames = tuple(path.name for path in discover_george_pdfs(source_directory))
    payload = {
        "schema_version": 1,
        "documents": [
            {
                "source_filename": filename,
                "manual_confirmed": False,
                "account_label": None,
                "view_type": None,
                "source_date": None,
                "evidence_status": "SOURCE_FIELD_REQUIRES_MANUAL_CONFIRMATION",
                "positions": [],
                "cash": [],
            }
            for filename in filenames
        ],
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return filenames


def import_george_pdf_directory(
    repository: TbszPortfolioRepository,
    source_directory: Path,
    confirmations_path: Path,
) -> SourceImportResult:
    """Import all confirmed evidence or return a no-write confirmation-needed result.

    Requiring confirmation for the entire source set avoids a partial initial
    import where an unconfirmed document might later alter an undated state.
    """
    documents = discover_george_pdfs(source_directory)
    filenames = tuple(path.name for path in documents)
    if not documents:
        return SourceImportResult(NO_PDFS_FOUND, (), (), (), ())
    confirmations = _load_confirmations(confirmations_path)
    missing_or_unconfirmed = tuple(
        path.name
        for path in documents
        if path.name not in confirmations or not bool(confirmations[path.name].get("manual_confirmed"))
    )
    extras = sorted(set(confirmations) - set(filenames), key=str.casefold)
    if extras:
        raise TbszError("manual confirmation contains source filenames outside data/tbsz/source")
    if missing_or_unconfirmed:
        return SourceImportResult(
            SOURCE_FIELD_REQUIRES_MANUAL_CONFIRMATION,
            filenames,
            (),
            (),
            missing_or_unconfirmed,
        )

    source_documents = tuple(
        _document_from_confirmation(path, confirmations[path.name]) for path in documents
    )
    # Validate all local confirmation records before any database mutation.
    for document in source_documents:
        validate_source_document(document)

    imported: list[str] = []
    already_imported: list[str] = []
    results = repository.import_source_documents(source_documents)
    for document, (_, inserted) in zip(source_documents, results, strict=True):
        (imported if inserted else already_imported).append(document.source_filename)
    status = IMPORTED if imported else ALREADY_IMPORTED_IDENTICAL
    return SourceImportResult(status, filenames, tuple(imported), tuple(already_imported), ())


def _load_confirmations(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TbszError(f"manual confirmation file is not valid JSON: {path}") from error
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise TbszError("manual confirmation requires schema_version 1")
    entries = payload.get("documents")
    if not isinstance(entries, list):
        raise TbszError("manual confirmation requires a documents list")
    result: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("source_filename"), str):
            raise TbszError("manual confirmation document requires source_filename")
        filename = entry["source_filename"]
        if Path(filename).name != filename or not filename.casefold().endswith(".pdf"):
            raise TbszError("manual confirmation source filename must be a plain PDF filename")
        if filename in result:
            raise TbszError("manual confirmation source filenames must be unique")
        result[filename] = entry
    return result


def _document_from_confirmation(path: Path, value: dict[str, Any]) -> SourceDocumentInput:
    if not value.get("manual_confirmed"):
        raise TbszError("manual source confirmation is required")
    account_label = _required_string(value, "account_label")
    view_type = _required_string(value, "view_type")
    evidence_status = _required_string(value, "evidence_status")
    raw_date = value.get("source_date")
    if raw_date is not None and not isinstance(raw_date, str):
        raise TbszError("manual confirmation source_date must be an ISO date string or null")
    try:
        source_date = date.fromisoformat(raw_date) if raw_date else None
    except ValueError as error:
        raise TbszError("manual confirmation source_date must be an ISO date string or null") from error
    positions = tuple(_position(item) for item in _list(value, "positions"))
    cash = tuple(_cash(item) for item in _list(value, "cash"))
    return SourceDocumentInput(
        source_filename=path.name,
        content_sha256=_sha256(path),
        account_label=account_label,
        view_type=view_type,
        source_date=source_date,
        evidence_status=evidence_status,
        positions=positions,
        cash=cash,
    )


def _required_string(value: dict[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise TbszError(f"manual confirmation requires {key}")
    return result


def _list(value: dict[str, Any], key: str) -> list[Any]:
    result = value.get(key, [])
    if not isinstance(result, list):
        raise TbszError(f"manual confirmation {key} must be a list")
    return result


def _position(value: Any) -> SourcePositionInput:
    if not isinstance(value, dict):
        raise TbszError("manual confirmation position must be an object")
    return SourcePositionInput(
        provider_name=_required_string(value, "provider_name"),
        market_value=_decimal_or_none(value.get("market_value"), "market_value"),
        market_currency=_optional_currency(value.get("market_currency")),
        reporting_value=_decimal_or_none(value.get("reporting_value"), "reporting_value"),
        reporting_currency=_optional_currency(value.get("reporting_currency")),
        quantity=_decimal_or_none(value.get("quantity"), "quantity"),
        unit_price=_decimal_or_none(value.get("unit_price"), "unit_price"),
        isin=_optional_string(value.get("isin")),
        data_quality_status=str(value.get("data_quality_status", "MANUALLY_CONFIRMED_FROM_VISIBLE_PDF")),
    )


def _cash(value: Any) -> SourceCashInput:
    if not isinstance(value, dict):
        raise TbszError("manual confirmation cash row must be an object")
    balance = _decimal_or_none(value.get("balance"), "balance")
    if balance is None:
        raise TbszError("manual confirmation cash balance is required when a cash row is supplied")
    return SourceCashInput(
        currency=_required_string(value, "currency").upper(),
        balance=balance,
        data_quality_status=str(value.get("data_quality_status", "MANUALLY_CONFIRMED_FROM_VISIBLE_PDF")),
    )


def _decimal_or_none(value: Any, key: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise TbszError(f"manual confirmation {key} must be a decimal or null")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise TbszError(f"manual confirmation {key} must be a decimal or null") from error
    if not result.is_finite() or result < 0:
        raise TbszError(f"manual confirmation {key} must be finite and non-negative")
    return result


def _optional_currency(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TbszError("currency must be a string or null")
    return value.upper()


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TbszError("value must be a string or null")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()
