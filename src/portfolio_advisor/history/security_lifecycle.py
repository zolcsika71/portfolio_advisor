"""Offline, fail-closed lifecycle evidence extraction for securities.

This module deliberately models lifecycle evidence separately from NAV and
price observations.  In particular, a maturity date does not create a price,
redemption cash flow, or a backtest return series.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

TARGET_ISIN = "HU0000554795"
AKK_AUTHORITY = "Államadósság Kezelő Központ Zártkörűen Működő Részvénytársaság"
AKK_DOCUMENT_TYPE = "ÁKK issuance-results notice"
_ISIN_PATTERN = re.compile(r"^[A-Z]{2}[A-Z0-9]{10}$")
_DATE_PATTERN = re.compile(r"^(\d{4})\.\s*(\d{2})\.\s*(\d{2})\.$")


class SecurityLifecycleError(RuntimeError):
    """Raised when local lifecycle evidence cannot be validated safely."""


def _normalise_layout(value: str) -> str:
    return " ".join(value.split())


def _normalise_label(value: str) -> str:
    return _normalise_layout(value).casefold()


def sha256_file(path: Path) -> str:
    """Return the content hash for retained local provenance."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class SecurityLifecycleEvidence:
    """Validated lifecycle metadata; unknown source fields remain ``None``."""

    isin: str
    series: str | None
    instrument_name: str | None
    currency: str | None
    instrument_type: str | None
    issuer: str | None
    issue_date: date | None
    maturity_date: date | None
    redemption_date: date | None
    redemption_value: Decimal | None
    coupon_rate: Decimal | None
    coupon_frequency: str | None
    source_authority: str | None
    source_host: str | None
    source_document: str
    source_document_sha256: str
    source_document_type: str | None
    evidence_status: str
    validation_warnings: tuple[str, ...]

    def __post_init__(self) -> None:
        if not _ISIN_PATTERN.fullmatch(self.isin):
            raise SecurityLifecycleError("LIFECYCLE_IDENTITY_CONFLICT: invalid ISIN")
        if not re.fullmatch(r"[0-9a-f]{64}", self.source_document_sha256):
            raise SecurityLifecycleError("LIFECYCLE_DOCUMENT_UNUSABLE: invalid source hash")
        if self.issue_date is not None and self.maturity_date is not None and self.issue_date >= self.maturity_date:
            raise SecurityLifecycleError("LIFECYCLE_DATE_CONFLICT: issue date must precede maturity date")
        if self.redemption_value is not None and self.redemption_value <= 0:
            raise SecurityLifecycleError("LIFECYCLE_DOCUMENT_UNUSABLE: redemption value must be positive")

    @property
    def maturity_validated(self) -> bool:
        return self.maturity_date is not None and self.evidence_status.startswith("LIFECYCLE_VALIDATED")

    @property
    def redemption_mechanics_validated(self) -> bool:
        return self.redemption_date is not None and self.redemption_value is not None

    def as_audit_dict(self) -> dict[str, object]:
        result = asdict(self)
        for field in ("issue_date", "maturity_date", "redemption_date"):
            value = result[field]
            result[field] = value.isoformat() if isinstance(value, date) else None
        for field in ("redemption_value", "coupon_rate"):
            value = result[field]
            result[field] = str(value) if isinstance(value, Decimal) else None
        result["validation_warnings"] = list(self.validation_warnings)
        result["maturity_validated"] = self.maturity_validated
        result["redemption_mechanics_validated"] = self.redemption_mechanics_validated
        return result


def extract_pdf_layout_text(path: Path) -> str:
    """Validate a retained PDF and return deterministic layout-preserving text."""
    if not path.exists() or not path.is_file() or path.stat().st_size <= 0:
        raise SecurityLifecycleError("LIFECYCLE_DOCUMENT_UNUSABLE: local source file is missing or empty")
    try:
        header = path.read_bytes()[:5]
    except OSError as exc:
        raise SecurityLifecycleError(f"LIFECYCLE_DOCUMENT_UNUSABLE: cannot read source: {exc}") from exc
    if header != b"%PDF-":
        raise SecurityLifecycleError("LIFECYCLE_DOCUMENT_UNUSABLE: source is not a PDF")
    for command in (("pdfinfo", str(path)), ("pdftotext", "-layout", str(path), "-")):
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SecurityLifecycleError(f"LIFECYCLE_DOCUMENT_UNUSABLE: PDF validation failed: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or "unknown PDF utility error"
            raise SecurityLifecycleError(f"LIFECYCLE_DOCUMENT_UNUSABLE: {detail}")
        if command[0] == "pdftotext":
            if not completed.stdout.strip():
                raise SecurityLifecycleError("LIFECYCLE_DOCUMENT_UNUSABLE: PDF text extraction is empty")
            return completed.stdout
    raise AssertionError("pdftotext result was not reached")


def _columns_for_row(line: str, expected_label: str) -> list[str] | None:
    parts = [part.strip() for part in re.split(r"(?:\t+| {2,})", line.strip()) if part.strip()]
    if not parts or _normalise_label(parts[0]) != _normalise_label(expected_label):
        return None
    return parts[1:]


def _single_row_columns(lines: list[str], label: str) -> list[str]:
    matches = [columns for line in lines if (columns := _columns_for_row(line, label)) is not None]
    if len(matches) != 1:
        raise SecurityLifecycleError(f"LIFECYCLE_IDENTITY_CONFLICT: expected one {label!r} table row")
    return matches[0]


def _parse_hungarian_date(value: str, field: str) -> date:
    match = _DATE_PATTERN.fullmatch(_normalise_layout(value))
    if match is None:
        raise SecurityLifecycleError(f"LIFECYCLE_DATE_CONFLICT: malformed {field}")
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError as exc:
        raise SecurityLifecycleError(f"LIFECYCLE_DATE_CONFLICT: invalid {field}") from exc


def _parse_decimal(value: str, field: str) -> Decimal:
    try:
        parsed = Decimal(value.replace(" ", "").replace(",", "."))
    except InvalidOperation as exc:
        raise SecurityLifecycleError(f"LIFECYCLE_DOCUMENT_UNUSABLE: malformed {field}") from exc
    if not parsed.is_finite():
        raise SecurityLifecycleError(f"LIFECYCLE_DOCUMENT_UNUSABLE: malformed {field}")
    return parsed


def _target_table_lines(text: str) -> list[str]:
    pages = text.split("\f")
    matching_pages = [
        page
        for page in pages
        if sum(_normalise_label(line) == "jegyzések" for line in page.splitlines()) == 1
    ]
    if len(matching_pages) != 1:
        raise SecurityLifecycleError("LIFECYCLE_IDENTITY_CONFLICT: expected one Jegyzések table")
    lines = matching_pages[0].splitlines()
    heading = next(index for index, line in enumerate(lines) if _normalise_label(line) == "jegyzések")
    return lines[heading + 1 :]


def parse_akk_issuance_lifecycle_text(
    text: str, source_document: str, source_document_sha256: str, target_isin: str = TARGET_ISIN
) -> SecurityLifecycleEvidence:
    """Extract lifecycle fields by the exact ISIN's table-column position.

    The `ISIN kód` row determines the target data-column index.  Every later
    field is selected from that same index, so values in neighbouring security
    columns cannot be associated merely by textual proximity.
    """
    normalised_document = _normalise_layout(text)
    if _normalise_layout(AKK_AUTHORITY) not in normalised_document:
        raise SecurityLifecycleError("LIFECYCLE_DOCUMENT_UNUSABLE: ÁKK authority is absent")
    if "forgalomba hozatalok eredményéről" not in normalised_document.casefold():
        raise SecurityLifecycleError("LIFECYCLE_DOCUMENT_UNUSABLE: unsupported document type")
    if not _ISIN_PATTERN.fullmatch(target_isin):
        raise SecurityLifecycleError("LIFECYCLE_IDENTITY_CONFLICT: requested ISIN is malformed")

    lines = _target_table_lines(text)
    isin_values = _single_row_columns(lines, "ISIN kód")
    matches = [index for index, value in enumerate(isin_values) if value.upper() == target_isin]
    if len(matches) != 1:
        raise SecurityLifecycleError("LIFECYCLE_IDENTITY_CONFLICT: exact ISIN is absent or ambiguous")
    target_column = matches[0]

    def column_value(label: str, required: bool = True) -> str | None:
        values = _single_row_columns(lines, label)
        if target_column >= len(values):
            if required:
                raise SecurityLifecycleError(f"LIFECYCLE_IDENTITY_CONFLICT: {label} has no target column")
            return None
        return values[target_column]

    series = column_value("A sorozat száma")
    currency = column_value("A sorozat devizaneme")
    issue_date = _parse_hungarian_date(str(column_value("A kibocsátás napja")), "issue date")
    maturity_date = _parse_hungarian_date(str(column_value("Lejárat napja")), "maturity date")
    coupon_text = column_value("Kamat mértéke (%)", required=False)
    coupon_rate = _parse_decimal(coupon_text, "coupon rate") if coupon_text else None

    return SecurityLifecycleEvidence(
        isin=target_isin,
        series=series,
        instrument_name=None,
        currency=currency,
        instrument_type=None,
        issuer=None,
        issue_date=issue_date,
        maturity_date=maturity_date,
        redemption_date=None,
        redemption_value=None,
        coupon_rate=coupon_rate,
        coupon_frequency=None,
        source_authority=AKK_AUTHORITY,
        source_host=None,
        source_document=source_document,
        source_document_sha256=source_document_sha256,
        source_document_type=AKK_DOCUMENT_TYPE,
        evidence_status="LIFECYCLE_VALIDATED_REDEMPTION_METHODOLOGY_REQUIRED",
        validation_warnings=("REDEMPTION_METHODOLOGY_REQUIRED",),
    )


def load_akk_issuance_lifecycle(path: Path, target_isin: str = TARGET_ISIN) -> SecurityLifecycleEvidence:
    """Load one local source document.  This function performs no network I/O."""
    text = extract_pdf_layout_text(path)
    return parse_akk_issuance_lifecycle_text(text, str(path), sha256_file(path), target_isin)


def require_consistent_lifecycle_evidence(
    evidence: tuple[SecurityLifecycleEvidence, ...]
) -> SecurityLifecycleEvidence:
    """Return one consistent record, rejecting contradictory authoritative PDFs."""
    if not evidence:
        raise SecurityLifecycleError("MATURITY_NOT_VALIDATED: no exact-ISIN lifecycle evidence")
    first = evidence[0]
    for candidate in evidence[1:]:
        if candidate.isin != first.isin or candidate.currency != first.currency or candidate.series != first.series:
            raise SecurityLifecycleError("LIFECYCLE_IDENTITY_CONFLICT: local lifecycle records disagree")
        if candidate.maturity_date != first.maturity_date:
            raise SecurityLifecycleError("LIFECYCLE_DATE_CONFLICT: local maturity dates disagree")
    return first
