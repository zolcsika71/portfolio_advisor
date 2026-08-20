"""Local-only inventory for MNB/KELER OTC report artifacts.

This module deliberately has no HTTP client.  It inventories already-present
PDF and extracted-text artifacts and delegates all exact-ISIN validation to
the canonical MNB OTC parser.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .mnb_otc import (
    REQUIRED_HEADERS,
    TARGET_HU_ISIN,
    TARGET_HU_NAME,
    MnbOtcError,
    MnbOtcObservation,
    extract_pdf_text,
    parse_mnb_otc_pdf,
    parse_mnb_otc_report_text,
    reporting_period_from_text,
)

REPORT_SUFFIXES = frozenset({".pdf", ".txt"})


@dataclass(frozen=True, slots=True)
class LocalReportRecord:
    """One local artifact examined without changing source data or SQLite."""

    path: Path
    relative_path: str
    artifact_type: str
    sha256: str
    size_bytes: int
    report_status: str
    parser_status: str
    contains_exact_isin: bool
    reporting_period: tuple[str, str] | None
    imported_status: str
    observation: MnbOtcObservation | None = None
    duplicate_of: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.relative_path,
            "filename": self.path.name,
            "artifact_type": self.artifact_type,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "reporting_period": (
                {"start": self.reporting_period[0], "end": self.reporting_period[1]}
                if self.reporting_period
                else None
            ),
            "contains_exact_isin": self.contains_exact_isin,
            "report_status": self.report_status,
            "parser_status": self.parser_status,
            "imported_status": self.imported_status,
            "duplicate_of": self.duplicate_of,
            "observation_count": 1 if self.observation is not None else 0,
        }


def sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise MnbOtcInventoryError(
            f"Unable to hash MNB OTC artifact {path}: {exc}"
        ) from exc


class MnbOtcInventoryError(RuntimeError):
    """A local artifact cannot be inspected deterministically."""


def _artifact_text(path: Path) -> str:
    try:
        if path.suffix.casefold() == ".pdf":
            return extract_pdf_text(path)
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, MnbOtcError) as exc:
        raise MnbOtcInventoryError(str(exc)) from exc


def _is_report_text(text: str) -> bool:
    return all(header in text for header in REQUIRED_HEADERS[1:])


def _period(text: str) -> tuple[str, str] | None:
    try:
        start, end = reporting_period_from_text(text)
    except MnbOtcError:
        return None
    return start.isoformat(), end.isoformat()


def inspect_local_artifact(
    path: Path,
    *,
    root: Path,
    imported_hashes: frozenset[str] = frozenset(),
) -> LocalReportRecord | None:
    """Return a report candidate record, or ``None`` for unrelated content."""
    if path.suffix.casefold() not in REPORT_SUFFIXES or not path.is_file():
        return None
    relative_path = str(path.relative_to(root))
    artifact_type = "PDF" if path.suffix.casefold() == ".pdf" else "TEXT"
    if artifact_type == "TEXT" and "tests" in path.relative_to(root).parts:
        artifact_type = "TEXT_FIXTURE"
    elif artifact_type == "TEXT":
        # Extracted text is diagnostic material; only a retained source PDF is
        # authoritative/importable evidence for this audit pipeline.
        artifact_type = "TEXT_ARTIFACT_NOT_SOURCE_EVIDENCE"
    digest = sha256_file(path)
    try:
        text = _artifact_text(path)
    except MnbOtcInventoryError:
        return LocalReportRecord(
            path=path,
            relative_path=relative_path,
            artifact_type=artifact_type,
            sha256=digest,
            size_bytes=path.stat().st_size,
            report_status="REPORT_PARSE_FAILED",
            parser_status="TEXT_EXTRACTION_FAILED",
            contains_exact_isin=False,
            reporting_period=None,
            imported_status="NOT_IMPORTED",
        )
    if not _is_report_text(text):
        return None
    exact_isin = TARGET_HU_ISIN in text
    period = _period(text)
    if not exact_isin:
        return LocalReportRecord(
            path=path,
            relative_path=relative_path,
            artifact_type=artifact_type,
            sha256=digest,
            size_bytes=path.stat().st_size,
            report_status="REPORT_ACQUIRED_ISIN_ABSENT",
            parser_status="NOT_APPLICABLE_EXACT_ISIN_ABSENT",
            contains_exact_isin=False,
            reporting_period=period,
            imported_status="NOT_IMPORTED_EXACT_ISIN_ABSENT",
        )
    try:
        observation = (
            parse_mnb_otc_pdf(path)
            if artifact_type == "PDF"
            else parse_mnb_otc_report_text(text, relative_path, digest)
        )
    except MnbOtcError as exc:
        return LocalReportRecord(
            path=path,
            relative_path=relative_path,
            artifact_type=artifact_type,
            sha256=digest,
            size_bytes=path.stat().st_size,
            report_status="REPORT_PARSE_FAILED",
            parser_status=f"PARSE_FAILED: {exc}",
            contains_exact_isin=True,
            reporting_period=period,
            imported_status="NOT_IMPORTED",
        )
    return LocalReportRecord(
        path=path,
        relative_path=relative_path,
        artifact_type=artifact_type,
        sha256=digest,
        size_bytes=path.stat().st_size,
        report_status=(
            "TEST_FIXTURE_NOT_SOURCE_EVIDENCE"
            if artifact_type == "TEXT_FIXTURE"
            else "REPORT_ACQUIRED_ISIN_PRESENT"
        ),
        parser_status="PARSED",
        contains_exact_isin=True,
        reporting_period=period,
        imported_status=(
            "IMPORTED"
            if artifact_type == "PDF" and digest in imported_hashes
            else "NOT_IMPORTED"
        ),
        observation=observation,
    )


def inventory_local_reports(
    root: Path, *, imported_hashes: frozenset[str] = frozenset()
) -> tuple[LocalReportRecord, ...]:
    """Recursively inventory local PDF/TXT report candidates in path order."""
    paths = sorted(
        path
        for path in root.rglob("*")
        if ".git" not in path.parts and path.suffix.casefold() in REPORT_SUFFIXES
    )
    records = [
        record
        for path in paths
        if (
            record := inspect_local_artifact(
                path, root=root, imported_hashes=imported_hashes
            )
        )
        is not None
    ]
    first_by_hash: dict[str, str] = {}
    deduplicated: list[LocalReportRecord] = []
    for record in records:
        duplicate_of = first_by_hash.get(record.sha256)
        if duplicate_of is None:
            first_by_hash[record.sha256] = record.relative_path
        deduplicated.append(
            record if duplicate_of is None else _with_duplicate(record, duplicate_of)
        )
    return tuple(deduplicated)


def _with_duplicate(record: LocalReportRecord, duplicate_of: str) -> LocalReportRecord:
    return LocalReportRecord(
        path=record.path,
        relative_path=record.relative_path,
        artifact_type=record.artifact_type,
        sha256=record.sha256,
        size_bytes=record.size_bytes,
        report_status=record.report_status,
        parser_status=record.parser_status,
        contains_exact_isin=record.contains_exact_isin,
        reporting_period=record.reporting_period,
        imported_status="DUPLICATE_CONTENT_NOT_IMPORTED",
        observation=record.observation,
        duplicate_of=duplicate_of,
    )


def inventory_summary(records: tuple[LocalReportRecord, ...]) -> dict[str, object]:
    """Deterministic missing-report-aware counts for an inventory manifest."""
    source_records = tuple(
        record for record in records if record.artifact_type == "PDF"
    )
    report_status_counts = Counter(record.report_status for record in source_records)
    return {
        "candidate_report_count": len(source_records),
        "reports_inspected": len(source_records),
        "reports_acquired": len(source_records),
        "reports_parsed": sum(
            record.parser_status == "PARSED" for record in source_records
        ),
        "exact_isin_positive_reports": sum(
            record.contains_exact_isin for record in source_records
        ),
        "exact_isin_absent_reports": sum(
            record.report_status == "REPORT_ACQUIRED_ISIN_ABSENT"
            for record in source_records
        ),
        "parse_failure_count": sum(
            record.report_status == "REPORT_PARSE_FAILED" for record in source_records
        ),
        "importable_exact_isin_pdf_reports": sum(
            record.artifact_type == "PDF"
            and record.observation is not None
            and record.duplicate_of is None
            for record in records
        ),
        "duplicate_report_count": sum(
            record.duplicate_of is not None for record in records
        ),
        "report_status_counts": dict(sorted(report_status_counts.items())),
    }


def build_manual_acquisition_manifest(
    records: tuple[LocalReportRecord, ...],
) -> dict[str, object]:
    """Describe a reproducible, offline-only continuation path for source acquisition."""
    return {
        "schema_version": 1,
        "source": "mnb_otc",
        "target_isin": TARGET_HU_ISIN,
        "target_instrument": TARGET_HU_NAME,
        "discovery_method": "LOCAL_INVENTORY_AND_MANUAL_AUTHORITATIVE_DOWNLOAD",
        "remote_discovery_status": "AUTOMATED_DISCOVERY_REJECTED_NO_DOCUMENTED_MACHINE_ARCHIVE",
        "authoritative_listing_host": "kozzetetelek.mnb.hu",
        "instructions": [
            "Download only KELER weekly OTC PDFs from the official MNB publication site.",
            "Place PDFs in data/mnb_otc/raw/ without renaming an existing conflicting file.",
            "Run the local importer, then regenerate the discovery and audit manifests.",
        ],
        "expected_reporting_periods": [],
        "unacquired_period_status": "NOT_DETERMINABLE_WITHOUT_DOCUMENTED_REMOTE_DISCOVERY",
        "local_report_inventory": [record.as_dict() for record in records],
        "summary": inventory_summary(records),
    }
