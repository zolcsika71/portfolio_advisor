"""Pure normalization candidates for typed BIFF-XLS source envelopes.

The adapter in this module has no database or filesystem surface.  It turns an
already parsed :class:`BiffXlsEnvelope` into a deterministic candidate while
keeping every source cell available as evidence.  A candidate is never an
admission decision, correction binding, or permission to use an operational
writer.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date
from typing import Final, Literal, cast

from portfolio_advisor.canonical import canonical_fingerprint, canonical_json
from portfolio_advisor.workbook_source.biff_xls import (
    ADMISSION_STATUS as SOURCE_ADMISSION_STATUS,
)
from portfolio_advisor.workbook_source.biff_xls import (
    ANALYTICAL_SHORTLIST_ROLE,
    METRIC_HEADERS,
    MODEL_HEADERS,
    MODEL_PORTFOLIO_ROLE,
    MODEL_SHEET_NAME,
    PARSER_NAME,
    PARSER_VERSION,
    SHORTLIST_HEADERS,
    SHORTLIST_SHEET_NAME,
    BiffXlsEnvelope,
    EnvelopeDiagnostic,
    ParsedSheet,
    SourceCell,
    SourceRow,
)
from portfolio_advisor.workbook_source.biff_xls import (
    CONTRACT_NAME as SOURCE_CONTRACT_NAME,
)
from portfolio_advisor.workbook_source.biff_xls import (
    CONTRACT_VERSION as SOURCE_CONTRACT_VERSION,
)

CONTRACT_NAME: Final = "BIFF_XLS_NORMALIZATION_CANDIDATE_V1"
CONTRACT_VERSION: Final = 1
POLICY_NAME: Final = "BIFF_XLS_NORMALIZATION_ADMISSION_POLICY"
POLICY_VERSION: Final = 1
CANDIDATE_STATUS: Final = "NOT_EVALUATED_NORMALIZATION_CANDIDATE_ONLY"
ADMISSION_APPROVAL: Final = "NOT_GRANTED"

APPROVED_SHORTLIST_MAPPING_ID: Final = "SHORTLIST_EFFECTIVE_CLASSIFICATION_ENGLISH_V1"
APPROVED_SHORTLIST_MAPPING_AUTHORIZATION: Final = (
    "USER_APPROVED_ENGLISH_CLASSIFICATION_MAPPING_2026_09_23"
)
APPROVED_SHORTLIST_MAPPING_DATASET_FINGERPRINT: Final = (
    "32216038d2f69dbf4c6436e91782025ae117dc59fe466e8a31ebc3719f93e8b2"
)
APPROVED_SHORTLIST_MAPPING_SHA256: Final = (
    "bada863f56c6f7f2ec084dcbd7e4c0f25319ddb321977cba2cd65783ccbad470"
)

_SHA256 = re.compile(r"[0-9a-f]{64}")
_DATE_FROM_FILENAME = re.compile(
    r"^(?P<prefix>.+?)(?<!\d)(?P<date>\d{8})\.xls$", re.IGNORECASE
)
_ISIN = re.compile(r"[A-Z]{2}[A-Z0-9]{9}[0-9]")
_EXPECTED_CURRENCY_RISK: Final = frozenset(
    {"fedezve", "nincs fedezve", "részben fedezve"}
)
_EXPECTED_SUSTAINABILITY: Final = frozenset(
    {
        "0: nem minősített",
        "1: esg-minimum standard",
        "2: esg-plusz",
        "3: esg-impact",
    }
)
_BIFF_TYPE_CODE_BY_KIND: Final = {
    "empty": 0,
    "text": 1,
    "number": 2,
    "date_serial": 3,
    "boolean": 4,
    "error": 5,
    "blank": 6,
}
_METRIC_IDENTITIES: Final = {
    "YTD": "YTD",
    "1yr": "RETURN_1Y",
    "3yr": "RETURN_3Y",
    "5yr": "RETURN_5Y",
    "1Y Sharpe": "SHARPE_RATIO_1Y",
    "3Y Sharpe": "SHARPE_RATIO_3Y",
    "5Y Sharpe": "SHARPE_RATIO_5Y",
    "1Y Vol.": "VOLATILITY_1Y",
    "3Y Vol.": "VOLATILITY_3Y",
    "Down. risk": "DOWNSIDE_RISK",
    "Info. ratio": "INFORMATION_RATIO",
    "Max. drawd.": "MAXIMUM_DRAWDOWN",
}
_MODEL_TARGET_FIELDS: Final = {
    "Portfólió neve": "portfolio_name",
    "Termék": "product_name",
    "ISIN": "isin",
    "Hányad (%)": "reported_weight",
    "Eszközosztály": "original_asset_class",
    "Aleszközosztály": "original_sub_asset_class",
    "Deviza": "original_currency",
    "Devizakockázat": "original_currency_risk",
    "Fenntarthatóság": "original_sustainability",
}
_SHORTLIST_TARGET_FIELDS: Final = {
    "Termék": "product_name",
    "ISIN": "isin",
    "Eszközosztály": "original_asset_class",
    "Aleszközosztály": "original_sub_asset_class",
    "Termék típus": "original_product_type",
    "Deviza": "original_currency",
    "Devizakockázat": "original_currency_risk",
    "Fenntarthatóság": "original_sustainability",
}
_REQUIRED_TEXT_HEADERS: Final = frozenset(
    {"Portfólió neve", "Termék", "ISIN", "Eszközosztály", "Aleszközosztály"}
)

type JsonScalar = str | int | float | bool | None
type DiagnosticSeverity = Literal["WARNING", "UNRESOLVED", "REJECTED"]
type FieldStatus = Literal[
    "NORMALIZED", "SOURCE_MISSING", "UNRESOLVED_POLICY", "REJECTED"
]


class BiffXlsNormalizationError(ValueError):
    """The source envelope or optional mapping does not satisfy the contract."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True)
class MappingReference:
    """Exact approved manifest identity, never an inherited admission."""

    mapping_id: str
    authorization_reference: str
    dataset_fingerprint: str
    manifest_sha256: str
    admission_scope: str

    def to_dict(self) -> dict[str, str]:
        return {
            "admission_scope": self.admission_scope,
            "authorization_reference": self.authorization_reference,
            "dataset_fingerprint": self.dataset_fingerprint,
            "manifest_sha256": self.manifest_sha256,
            "mapping_id": self.mapping_id,
        }


@dataclass(frozen=True, slots=True)
class SourceEnvelopeBinding:
    """Immutable identity of the parser envelope normalized by this adapter."""

    source_filename: str
    snapshot_date: str
    workbook_sha256: str
    byte_length: int
    envelope_fingerprint: str
    source_contract_name: str
    source_contract_version: int
    source_admission_status: str
    parser_name: str
    parser_version: int
    parser_library: str
    parser_library_version: str
    compound_document_recovery: str
    source_diagnostics: tuple[EnvelopeDiagnostic, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "byte_length": self.byte_length,
            "envelope_fingerprint": self.envelope_fingerprint,
            "parser": {
                "compound_document_recovery": self.compound_document_recovery,
                "library": self.parser_library,
                "library_version": self.parser_library_version,
                "name": self.parser_name,
                "version": self.parser_version,
            },
            "source_admission_status": self.source_admission_status,
            "source_contract": {
                "name": self.source_contract_name,
                "version": self.source_contract_version,
            },
            "source_diagnostics": [
                diagnostic.to_dict() for diagnostic in self.source_diagnostics
            ],
            "source_filename": self.source_filename,
            "snapshot_date": self.snapshot_date,
            "workbook_sha256": self.workbook_sha256,
        }


@dataclass(frozen=True, slots=True)
class CandidateDiagnostic:
    """Stable normalization warning, rejection, or unresolved policy gate."""

    severity: DiagnosticSeverity
    code: str
    message: str
    role: str | None = None
    sheet_name: str | None = None
    occurrence_index: int | None = None
    source_reference: str | None = None
    header: str | None = None
    source_row: int | None = None
    source_column: int | None = None
    coordinate: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "coordinate": self.coordinate,
            "header": self.header,
            "message": self.message,
            "occurrence_index": self.occurrence_index,
            "role": self.role,
            "severity": self.severity,
            "sheet_name": self.sheet_name,
            "source_column": self.source_column,
            "source_reference": self.source_reference,
            "source_row": self.source_row,
        }


@dataclass(frozen=True, slots=True)
class NormalizedFieldCandidate:
    """One typed source cell and its proposed normalized-original value."""

    header: str
    target_field: str
    semantic_unit: str | None
    normalized_type: Literal["TEXT", "REAL", "NULL"]
    status: FieldStatus
    normalized_value: JsonScalar
    field_occurrence_id: str
    target_source_reference: str | None
    source_cell: SourceCell

    def to_dict(self) -> dict[str, object]:
        return {
            "field_occurrence_id": self.field_occurrence_id,
            "header": self.header,
            "normalized_value": self.normalized_value,
            "normalized_type": self.normalized_type,
            "semantic_unit": self.semantic_unit,
            "source_cell": self.source_cell.to_dict(),
            "status": self.status,
            "target_field": self.target_field,
            "target_source_reference": self.target_source_reference,
        }


@dataclass(frozen=True, slots=True)
class ClassificationCandidate:
    """Original pair plus a non-admitted English mapping candidate, if exact."""

    original_asset_class: str | None
    original_sub_asset_class: str | None
    english_asset_class_candidate: str | None
    english_sub_asset_class_candidate: str | None
    mapping_status: str
    mapping_identity: MappingReference | None

    def to_dict(self) -> dict[str, object]:
        return {
            "english_asset_class_candidate": self.english_asset_class_candidate,
            "english_sub_asset_class_candidate": self.english_sub_asset_class_candidate,
            "mapping_identity": (
                self.mapping_identity.to_dict()
                if self.mapping_identity is not None
                else None
            ),
            "mapping_status": self.mapping_status,
            "original_asset_class": self.original_asset_class,
            "original_sub_asset_class": self.original_sub_asset_class,
        }


@dataclass(frozen=True, slots=True)
class NormalizedRowCandidate:
    """One source-order occurrence; duplicates are never collapsed."""

    occurrence_index: int
    source_row: int
    source_reference: str
    row_fingerprint: str
    occurrence_id: str
    fields: tuple[NormalizedFieldCandidate, ...]
    classification: ClassificationCandidate

    def to_dict(self) -> dict[str, object]:
        return {
            "classification": self.classification.to_dict(),
            "fields": [field.to_dict() for field in self.fields],
            "occurrence_id": self.occurrence_id,
            "occurrence_index": self.occurrence_index,
            "row_fingerprint": self.row_fingerprint,
            "source_reference": self.source_reference,
            "source_row": self.source_row,
        }


@dataclass(frozen=True, slots=True)
class NormalizedSheetCandidate:
    """Candidate rows for one exact source worksheet."""

    role: str
    exact_name: str
    sheet_index: int
    visibility_code: int
    visibility: str
    header_row: int
    header_start_column: int
    headers: tuple[SourceCell, ...]
    sheet_fingerprint: str
    status: str
    rows: tuple[NormalizedRowCandidate, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "exact_name": self.exact_name,
            "header_row": self.header_row,
            "header_start_column": self.header_start_column,
            "headers": [header.to_dict() for header in self.headers],
            "role": self.role,
            "rows": [row.to_dict() for row in self.rows],
            "sheet_fingerprint": self.sheet_fingerprint,
            "sheet_index": self.sheet_index,
            "status": self.status,
            "visibility": self.visibility,
            "visibility_code": self.visibility_code,
        }


@dataclass(frozen=True, slots=True)
class BiffXlsNormalizationCandidate:
    """Deterministic, non-admitting normalization output."""

    source_binding: SourceEnvelopeBinding
    mapping_reference: MappingReference | None
    sheets: tuple[NormalizedSheetCandidate, ...]
    diagnostics: tuple[CandidateDiagnostic, ...]

    def _payload(self) -> dict[str, object]:
        return {
            "admission_approval": ADMISSION_APPROVAL,
            "candidate_status": CANDIDATE_STATUS,
            "contract": {"name": CONTRACT_NAME, "version": CONTRACT_VERSION},
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "mapping_reference": (
                self.mapping_reference.to_dict()
                if self.mapping_reference is not None
                else None
            ),
            "normalization_policy": {
                "name": POLICY_NAME,
                "version": POLICY_VERSION,
            },
            "sheets": [sheet.to_dict() for sheet in self.sheets],
            "source_binding": self.source_binding.to_dict(),
        }

    @property
    def candidate_fingerprint(self) -> str:
        return canonical_fingerprint(self._payload())

    def to_dict(self) -> dict[str, object]:
        return {**self._payload(), "candidate_fingerprint": self.candidate_fingerprint}

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    def sheet(self, role: str) -> NormalizedSheetCandidate:
        try:
            return next(sheet for sheet in self.sheets if sheet.role == role)
        except StopIteration as error:  # pragma: no cover - constructor invariant
            raise KeyError(role) from error


@dataclass(frozen=True, slots=True)
class _MappingEntry:
    english_asset_class: str
    english_sub_asset_class: str


@dataclass(frozen=True, slots=True)
class _ApprovedMapping:
    identity: MappingReference
    entries: dict[tuple[str, str], _MappingEntry]


def normalize_biff_xls_envelope(
    envelope: BiffXlsEnvelope,
    *,
    expected_workbook_sha256: str,
    approved_shortlist_mapping_manifest: bytes | None = None,
) -> BiffXlsNormalizationCandidate:
    """Create a pure candidate from one parser envelope.

    ``expected_workbook_sha256`` is supplied by the caller that controls the
    source bytes.  The function never opens a path and never performs database
    work.  Optional mapping bytes must be the exact tracked approved manifest;
    even then, the result is only an English candidate and not a correction
    admission for this source.
    """
    _validate_envelope(envelope, expected_workbook_sha256)
    mapping = (
        _load_approved_mapping(approved_shortlist_mapping_manifest)
        if approved_shortlist_mapping_manifest is not None
        else None
    )
    diagnostics: list[CandidateDiagnostic] = [
        CandidateDiagnostic(
            severity="UNRESOLVED",
            code="RECOVERY_MODE_ADMISSION_REVIEW_REQUIRED",
            message=(
                "the parser used xlrd recovery mode; this candidate cannot prove "
                "whether recovery was required or waive independent admission evidence"
            ),
            source_reference=(
                f"BIFF_XLS_V{SOURCE_CONTRACT_VERSION}:"
                f"{envelope.workbook_sha256}:WORKBOOK"
            ),
        ),
        CandidateDiagnostic(
            severity="UNRESOLVED",
            code="FORMULA_ORIGIN_NOT_ESTABLISHED",
            message=(
                "cached BIFF values do not establish formula text, reliable formula "
                "presence, or literal-cell origin"
            ),
            source_reference=(
                f"BIFF_XLS_V{SOURCE_CONTRACT_VERSION}:"
                f"{envelope.workbook_sha256}:WORKBOOK"
            ),
        ),
    ]
    sheets = tuple(
        _normalize_sheet(envelope, sheet, mapping, diagnostics)
        for sheet in envelope.sheets
    )
    source_payload = envelope.to_dict()
    parser = cast(dict[str, object], source_payload["parser"])
    source_binding = SourceEnvelopeBinding(
        source_filename=envelope.source_filename,
        snapshot_date=envelope.snapshot_date,
        workbook_sha256=envelope.workbook_sha256,
        byte_length=envelope.byte_length,
        envelope_fingerprint=envelope.envelope_fingerprint,
        source_contract_name=SOURCE_CONTRACT_NAME,
        source_contract_version=SOURCE_CONTRACT_VERSION,
        source_admission_status=SOURCE_ADMISSION_STATUS,
        parser_name=PARSER_NAME,
        parser_version=PARSER_VERSION,
        parser_library=cast(str, parser["library"]),
        parser_library_version=cast(str, parser["library_version"]),
        compound_document_recovery=cast(str, parser["compound_document_recovery"]),
        source_diagnostics=envelope.diagnostics,
    )
    return BiffXlsNormalizationCandidate(
        source_binding=source_binding,
        mapping_reference=mapping.identity if mapping is not None else None,
        sheets=sheets,
        diagnostics=tuple(diagnostics),
    )


def _normalize_sheet(
    envelope: BiffXlsEnvelope,
    sheet: ParsedSheet,
    mapping: _ApprovedMapping | None,
    diagnostics: list[CandidateDiagnostic],
) -> NormalizedSheetCandidate:
    headers = MODEL_HEADERS if sheet.role == MODEL_PORTFOLIO_ROLE else SHORTLIST_HEADERS
    rows = tuple(
        _normalize_row(envelope, sheet, headers, row, mapping, diagnostics)
        for row in sheet.rows
    )
    rejected = any(field.status == "REJECTED" for row in rows for field in row.fields)
    return NormalizedSheetCandidate(
        role=sheet.role,
        exact_name=sheet.exact_name,
        sheet_index=sheet.sheet_index,
        visibility_code=sheet.visibility_code,
        visibility=sheet.visibility,
        header_row=sheet.header_row,
        header_start_column=sheet.header_start_column,
        headers=sheet.headers,
        sheet_fingerprint=sheet.sheet_fingerprint,
        status=(
            "FIELD_NORMALIZATION_REJECTED"
            if rejected
            else "NORMALIZATION_CANDIDATE_PRODUCED"
        ),
        rows=rows,
    )


def _normalize_row(
    envelope: BiffXlsEnvelope,
    sheet: ParsedSheet,
    headers: tuple[str, ...],
    row: SourceRow,
    mapping: _ApprovedMapping | None,
    diagnostics: list[CandidateDiagnostic],
) -> NormalizedRowCandidate:
    fields = tuple(
        _normalize_field(envelope, sheet, row, header, cell, diagnostics)
        for header, cell in zip(headers, row.cells, strict=True)
    )
    by_header = {field.header: field for field in fields}
    asset = _normalized_text(by_header["Eszközosztály"])
    sub_asset = _normalized_text(by_header["Aleszközosztály"])
    classification = _classification_candidate(
        sheet, row, asset, sub_asset, mapping, diagnostics
    )
    return NormalizedRowCandidate(
        occurrence_index=row.occurrence_index,
        source_row=row.source_row,
        source_reference=row.source_reference,
        row_fingerprint=row.row_fingerprint,
        occurrence_id=(f"{row.source_reference}:OCCURRENCE:{row.occurrence_index}"),
        fields=fields,
        classification=classification,
    )


def _normalize_field(
    envelope: BiffXlsEnvelope,
    sheet: ParsedSheet,
    row: SourceRow,
    header: str,
    cell: SourceCell,
    diagnostics: list[CandidateDiagnostic],
) -> NormalizedFieldCandidate:
    target_field = _target_field(sheet.role, header)
    semantic_unit = (
        "RATIO"
        if header in METRIC_HEADERS
        else "PERCENTAGE_POINTS"
        if header == "Hányad (%)"
        else None
    )
    target_reference = (
        f"SHORTLIST:{envelope.workbook_sha256}:{sheet.exact_name}:"
        f"{row.source_row}:{header}"
        if sheet.role == ANALYTICAL_SHORTLIST_ROLE and header in METRIC_HEADERS
        else None
    )
    field_occurrence_id = (
        f"{row.source_reference}:CELL:{cell.coordinate}:HEADER:{header}"
    )

    if header in METRIC_HEADERS:
        status, value = _normalize_metric(sheet, row, header, cell, diagnostics)
    elif header == "Hányad (%)":
        status, value = _normalize_weight(sheet, row, header, cell, diagnostics)
    elif header in {"Devizakockázat", "Fenntarthatóság"}:
        status, value = _normalize_governed_text(sheet, row, header, cell, diagnostics)
    else:
        status, value = _normalize_text(
            sheet,
            row,
            header,
            cell,
            required=header in _REQUIRED_TEXT_HEADERS,
            diagnostics=diagnostics,
        )
        if header == "ISIN" and status == "NORMALIZED":
            assert isinstance(value, str)
            if _ISIN.fullmatch(value) is None:
                status = "REJECTED"
                value = None
                diagnostics.append(
                    _cell_diagnostic(
                        "REJECTED",
                        "MALFORMED_ISIN",
                        "ISIN must match the exact 12-character uppercase source form",
                        sheet,
                        row,
                        header,
                        cell,
                    )
                )
    return NormalizedFieldCandidate(
        header=header,
        target_field=target_field,
        semantic_unit=semantic_unit,
        normalized_type=_normalized_type(value),
        status=status,
        normalized_value=value,
        field_occurrence_id=field_occurrence_id,
        target_source_reference=target_reference,
        source_cell=cell,
    )


def _normalize_metric(
    sheet: ParsedSheet,
    row: SourceRow,
    header: str,
    cell: SourceCell,
    diagnostics: list[CandidateDiagnostic],
) -> tuple[FieldStatus, JsonScalar]:
    if cell.cell_type in {"empty", "blank"}:
        return "SOURCE_MISSING", None
    if cell.cell_type == "text" and cell.raw_value == "":
        diagnostics.append(
            _cell_diagnostic(
                "WARNING",
                "EMPTY_TEXT_AS_MISSING",
                "empty text is source-distinct but has no normalized observation",
                sheet,
                row,
                header,
                cell,
            )
        )
        return "SOURCE_MISSING", None
    if cell.cell_type != "number" or not isinstance(cell.raw_value, (int, float)):
        diagnostics.append(
            _cell_diagnostic(
                "REJECTED",
                "INVALID_METRIC_CELL_TYPE",
                "metric normalization requires a finite BIFF number; no coercion applied",
                sheet,
                row,
                header,
                cell,
            )
        )
        return "REJECTED", None
    value = float(cell.raw_value)
    if not math.isfinite(value):
        diagnostics.append(
            _cell_diagnostic(
                "REJECTED",
                "NON_FINITE_METRIC_VALUE",
                "non-finite metric values are not normalization candidates",
                sheet,
                row,
                header,
                cell,
            )
        )
        return "REJECTED", None
    if value == 0.0 and sheet.role == MODEL_PORTFOLIO_ROLE:
        diagnostics.append(
            _cell_diagnostic(
                "UNRESOLVED",
                "UNRESOLVED_MODEL_ZERO_SEMANTICS",
                "model numeric zero is preserved; zero-to-absence is not approved for real sources",
                sheet,
                row,
                header,
                cell,
            )
        )
    if header in {"3yr", "5yr"}:
        diagnostics.append(
            _cell_diagnostic(
                "UNRESOLVED",
                "UNRESOLVED_METRIC_HORIZON_SEMANTICS",
                "the source header does not establish cumulative or annualized meaning",
                sheet,
                row,
                header,
                cell,
            )
        )
    return "NORMALIZED", value


def _normalize_weight(
    sheet: ParsedSheet,
    row: SourceRow,
    header: str,
    cell: SourceCell,
    diagnostics: list[CandidateDiagnostic],
) -> tuple[FieldStatus, JsonScalar]:
    if cell.cell_type != "number" or not isinstance(cell.raw_value, (int, float)):
        diagnostics.append(
            _cell_diagnostic(
                "REJECTED",
                "INVALID_REPORTED_WEIGHT",
                "reported weight requires a finite non-negative BIFF number",
                sheet,
                row,
                header,
                cell,
            )
        )
        return "REJECTED", None
    value = float(cell.raw_value)
    if not math.isfinite(value) or value < 0:
        diagnostics.append(
            _cell_diagnostic(
                "REJECTED",
                "INVALID_REPORTED_WEIGHT",
                "reported weight requires a finite non-negative BIFF number",
                sheet,
                row,
                header,
                cell,
            )
        )
        return "REJECTED", None
    return "NORMALIZED", value


def _normalize_governed_text(
    sheet: ParsedSheet,
    row: SourceRow,
    header: str,
    cell: SourceCell,
    diagnostics: list[CandidateDiagnostic],
) -> tuple[FieldStatus, JsonScalar]:
    if cell.cell_type in {"empty", "blank"}:
        return "SOURCE_MISSING", None
    if cell.cell_type != "text" or not isinstance(cell.raw_value, str):
        code = (
            "ANOMALOUS_CURRENCY_RISK_VALUE"
            if header == "Devizakockázat"
            else "ANOMALOUS_SUSTAINABILITY_VALUE"
        )
        diagnostics.append(
            _cell_diagnostic(
                "UNRESOLVED",
                code,
                "non-text source value is preserved but has no authorized repair",
                sheet,
                row,
                header,
                cell,
            )
        )
        return "UNRESOLVED_POLICY", None
    value = cell.raw_value.strip()
    if not value:
        diagnostics.append(
            _cell_diagnostic(
                "WARNING",
                "EMPTY_TEXT_AS_MISSING",
                "empty text is source-distinct but has no normalized value",
                sheet,
                row,
                header,
                cell,
            )
        )
        return "SOURCE_MISSING", None
    status: FieldStatus = "NORMALIZED"
    normalized = value.casefold()
    expected = (
        _EXPECTED_CURRENCY_RISK
        if header == "Devizakockázat"
        else _EXPECTED_SUSTAINABILITY
    )
    if normalized not in expected:
        code = (
            "ANOMALOUS_CURRENCY_RISK_VALUE"
            if header == "Devizakockázat"
            else "ANOMALOUS_SUSTAINABILITY_VALUE"
        )
        diagnostics.append(
            _cell_diagnostic(
                "UNRESOLVED",
                code,
                "source value is preserved but has no authorized repair or disposition",
                sheet,
                row,
                header,
                cell,
            )
        )
        status = "UNRESOLVED_POLICY"
    if header == "Devizakockázat":
        diagnostics.append(
            _cell_diagnostic(
                "UNRESOLVED",
                "UNAPPROVED_CURRENCY_RISK_TRANSLATION",
                "no English currency-risk translation is authorized for this candidate",
                sheet,
                row,
                header,
                cell,
            )
        )
        status = "UNRESOLVED_POLICY"
    return status, value


def _normalize_text(
    sheet: ParsedSheet,
    row: SourceRow,
    header: str,
    cell: SourceCell,
    *,
    required: bool,
    diagnostics: list[CandidateDiagnostic],
) -> tuple[FieldStatus, JsonScalar]:
    if cell.cell_type in {"empty", "blank"}:
        if required:
            diagnostics.append(
                _cell_diagnostic(
                    "REJECTED",
                    "MISSING_REQUIRED_TEXT",
                    "required text is absent",
                    sheet,
                    row,
                    header,
                    cell,
                )
            )
            return "REJECTED", None
        return "SOURCE_MISSING", None
    if cell.cell_type != "text" or not isinstance(cell.raw_value, str):
        diagnostics.append(
            _cell_diagnostic(
                "REJECTED",
                "INVALID_TEXT_CELL_TYPE",
                "text normalization does not coerce another BIFF cell type",
                sheet,
                row,
                header,
                cell,
            )
        )
        return "REJECTED", None
    value = cell.raw_value.strip()
    if value:
        return "NORMALIZED", value
    if required:
        diagnostics.append(
            _cell_diagnostic(
                "REJECTED",
                "MISSING_REQUIRED_TEXT",
                "required text is empty after trimming",
                sheet,
                row,
                header,
                cell,
            )
        )
        return "REJECTED", None
    diagnostics.append(
        _cell_diagnostic(
            "WARNING",
            "EMPTY_TEXT_AS_MISSING",
            "empty text is source-distinct but has no normalized value",
            sheet,
            row,
            header,
            cell,
        )
    )
    return "SOURCE_MISSING", None


def _classification_candidate(
    sheet: ParsedSheet,
    row: SourceRow,
    asset: str | None,
    sub_asset: str | None,
    mapping: _ApprovedMapping | None,
    diagnostics: list[CandidateDiagnostic],
) -> ClassificationCandidate:
    if sheet.role != ANALYTICAL_SHORTLIST_ROLE:
        return ClassificationCandidate(
            original_asset_class=asset,
            original_sub_asset_class=sub_asset,
            english_asset_class_candidate=None,
            english_sub_asset_class_candidate=None,
            mapping_status="NOT_APPLICABLE_TO_MODEL_ROLE",
            mapping_identity=None,
        )
    if asset is None or sub_asset is None:
        return ClassificationCandidate(
            original_asset_class=asset,
            original_sub_asset_class=sub_asset,
            english_asset_class_candidate=None,
            english_sub_asset_class_candidate=None,
            mapping_status="SOURCE_PAIR_UNAVAILABLE",
            mapping_identity=None,
        )
    if mapping is None:
        diagnostics.append(
            _row_diagnostic(
                "UNRESOLVED",
                "APPROVED_CLASSIFICATION_MAPPING_NOT_SUPPLIED",
                "no English pair candidate was produced without the exact approved manifest",
                sheet,
                row,
            )
        )
        return ClassificationCandidate(
            original_asset_class=asset,
            original_sub_asset_class=sub_asset,
            english_asset_class_candidate=None,
            english_sub_asset_class_candidate=None,
            mapping_status="MAPPING_NOT_SUPPLIED",
            mapping_identity=None,
        )
    entry = mapping.entries.get((asset, sub_asset))
    if entry is None:
        diagnostics.append(
            _row_diagnostic(
                "UNRESOLVED",
                "UNRESOLVED_SHORTLIST_CLASSIFICATION_MAPPING",
                "the exact original pair has no entry in the approved manifest",
                sheet,
                row,
            )
        )
        return ClassificationCandidate(
            original_asset_class=asset,
            original_sub_asset_class=sub_asset,
            english_asset_class_candidate=None,
            english_sub_asset_class_candidate=None,
            mapping_status="EXACT_PAIR_NOT_MAPPED",
            mapping_identity=mapping.identity,
        )
    return ClassificationCandidate(
        original_asset_class=asset,
        original_sub_asset_class=sub_asset,
        english_asset_class_candidate=entry.english_asset_class,
        english_sub_asset_class_candidate=entry.english_sub_asset_class,
        mapping_status="APPROVED_MANIFEST_REFERENCE_CANDIDATE_ONLY",
        mapping_identity=mapping.identity,
    )


def _normalized_text(field: NormalizedFieldCandidate) -> str | None:
    return field.normalized_value if isinstance(field.normalized_value, str) else None


def _target_field(role: str, header: str) -> str:
    if header in _METRIC_IDENTITIES:
        return _METRIC_IDENTITIES[header]
    mapping = (
        _MODEL_TARGET_FIELDS
        if role == MODEL_PORTFOLIO_ROLE
        else _SHORTLIST_TARGET_FIELDS
    )
    return mapping[header]


def _normalized_type(value: JsonScalar) -> Literal["TEXT", "REAL", "NULL"]:
    if isinstance(value, str):
        return "TEXT"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "REAL"
    return "NULL"


def _cell_diagnostic(
    severity: DiagnosticSeverity,
    code: str,
    message: str,
    sheet: ParsedSheet,
    row: SourceRow,
    header: str,
    cell: SourceCell,
) -> CandidateDiagnostic:
    return CandidateDiagnostic(
        severity=severity,
        code=code,
        message=message,
        role=sheet.role,
        sheet_name=sheet.exact_name,
        occurrence_index=row.occurrence_index,
        source_reference=row.source_reference,
        header=header,
        source_row=cell.source_row,
        source_column=cell.source_column,
        coordinate=cell.coordinate,
    )


def _row_diagnostic(
    severity: DiagnosticSeverity,
    code: str,
    message: str,
    sheet: ParsedSheet,
    row: SourceRow,
) -> CandidateDiagnostic:
    return CandidateDiagnostic(
        severity=severity,
        code=code,
        message=message,
        role=sheet.role,
        sheet_name=sheet.exact_name,
        occurrence_index=row.occurrence_index,
        source_reference=row.source_reference,
        source_row=row.source_row,
    )


def _load_approved_mapping(manifest_bytes: bytes) -> _ApprovedMapping:
    digest = hashlib.sha256(manifest_bytes).hexdigest()
    if digest != APPROVED_SHORTLIST_MAPPING_SHA256:
        raise BiffXlsNormalizationError(
            "UNAPPROVED_CLASSIFICATION_MAPPING_MANIFEST",
            "mapping bytes do not match the exact approved manifest SHA-256",
        )
    try:
        payload = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BiffXlsNormalizationError(
            "INVALID_CLASSIFICATION_MAPPING_MANIFEST", str(error)
        ) from error
    if not isinstance(payload, dict):
        raise BiffXlsNormalizationError(
            "INVALID_CLASSIFICATION_MAPPING_MANIFEST", "manifest must be an object"
        )
    expected = {
        "schema_version": 1,
        "mapping_id": APPROVED_SHORTLIST_MAPPING_ID,
        "authorization_reference": APPROVED_SHORTLIST_MAPPING_AUTHORIZATION,
        "dataset_fingerprint": APPROVED_SHORTLIST_MAPPING_DATASET_FINGERPRINT,
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise BiffXlsNormalizationError(
            "INVALID_CLASSIFICATION_MAPPING_MANIFEST",
            "manifest identity does not match the approved contract",
        )
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list) or len(raw_entries) != 77:
        raise BiffXlsNormalizationError(
            "INVALID_CLASSIFICATION_MAPPING_MANIFEST",
            "approved manifest must contain exactly 77 pair entries",
        )
    entries: dict[tuple[str, str], _MappingEntry] = {}
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            raise BiffXlsNormalizationError(
                "INVALID_CLASSIFICATION_MAPPING_MANIFEST",
                "each mapping entry must be an object",
            )
        values = tuple(
            raw_entry.get(key)
            for key in (
                "prior_asset_class",
                "prior_sub_asset_class",
                "effective_asset_class",
                "effective_sub_asset_class",
            )
        )
        if not all(isinstance(value, str) and value for value in values):
            raise BiffXlsNormalizationError(
                "INVALID_CLASSIFICATION_MAPPING_MANIFEST",
                "mapping pair values must be non-empty strings",
            )
        prior_asset, prior_sub_asset, english_asset, english_sub_asset = cast(
            tuple[str, str, str, str], values
        )
        key = (prior_asset, prior_sub_asset)
        if key in entries:
            raise BiffXlsNormalizationError(
                "INVALID_CLASSIFICATION_MAPPING_MANIFEST",
                "approved manifest contains a duplicate prior pair",
            )
        entries[key] = _MappingEntry(english_asset, english_sub_asset)
    identity = MappingReference(
        admission_scope="REFERENCE_ONLY_EXISTING_DATASET_BINDING_NOT_REUSED",
        authorization_reference=APPROVED_SHORTLIST_MAPPING_AUTHORIZATION,
        dataset_fingerprint=APPROVED_SHORTLIST_MAPPING_DATASET_FINGERPRINT,
        manifest_sha256=APPROVED_SHORTLIST_MAPPING_SHA256,
        mapping_id=APPROVED_SHORTLIST_MAPPING_ID,
    )
    return _ApprovedMapping(identity=identity, entries=entries)


def _validate_envelope(
    envelope: BiffXlsEnvelope, expected_workbook_sha256: str
) -> None:
    if not isinstance(envelope, BiffXlsEnvelope):
        raise BiffXlsNormalizationError(
            "INVALID_SOURCE_ENVELOPE_TYPE", "expected a BiffXlsEnvelope instance"
        )
    if _SHA256.fullmatch(expected_workbook_sha256) is None:
        raise BiffXlsNormalizationError(
            "INVALID_EXPECTED_WORKBOOK_SHA256",
            "expected workbook SHA-256 must be 64 lowercase hexadecimal characters",
        )
    if envelope.workbook_sha256 != expected_workbook_sha256:
        raise BiffXlsNormalizationError(
            "WORKBOOK_SHA256_MISMATCH",
            "source-envelope workbook hash does not match the caller's byte binding",
        )
    payload = envelope.to_dict()
    if payload.get("contract") != {
        "name": SOURCE_CONTRACT_NAME,
        "version": SOURCE_CONTRACT_VERSION,
    }:
        raise BiffXlsNormalizationError(
            "UNSUPPORTED_SOURCE_CONTRACT",
            "only the implemented v1 envelope is accepted",
        )
    if payload.get("admission_status") != SOURCE_ADMISSION_STATUS:
        raise BiffXlsNormalizationError(
            "INVALID_SOURCE_ADMISSION_STATUS",
            "source envelope must retain its parser-only admission status",
        )
    parser = payload.get("parser")
    if not isinstance(parser, dict) or (
        parser.get("name") != PARSER_NAME
        or parser.get("version") != PARSER_VERSION
        or parser.get("compound_document_recovery") != "XLRD_IGNORE_WORKBOOK_CORRUPTION"
    ):
        raise BiffXlsNormalizationError(
            "UNSUPPORTED_SOURCE_PARSER",
            "parser identity or recovery declaration changed",
        )
    if envelope.byte_length <= 0 or _SHA256.fullmatch(envelope.workbook_sha256) is None:
        raise BiffXlsNormalizationError(
            "INVALID_SOURCE_BINDING", "source byte length or workbook hash is invalid"
        )
    match = _DATE_FROM_FILENAME.fullmatch(envelope.source_filename)
    if match is None:
        raise BiffXlsNormalizationError(
            "INVALID_SOURCE_FILENAME",
            "source filename must retain a terminal YYYYMMDD.xls date",
        )
    raw_date = match.group("date")
    try:
        filename_date = date(
            int(raw_date[:4]), int(raw_date[4:6]), int(raw_date[6:])
        ).isoformat()
    except ValueError as error:
        raise BiffXlsNormalizationError(
            "INVALID_SOURCE_SNAPSHOT_DATE", "source filename date is invalid"
        ) from error
    if envelope.snapshot_date != filename_date:
        raise BiffXlsNormalizationError(
            "SOURCE_SNAPSHOT_DATE_MISMATCH",
            "source snapshot date does not match the terminal filename date",
        )
    expected_sheets = {
        MODEL_PORTFOLIO_ROLE: (MODEL_SHEET_NAME, MODEL_HEADERS),
        ANALYTICAL_SHORTLIST_ROLE: (SHORTLIST_SHEET_NAME, SHORTLIST_HEADERS),
    }
    if len(envelope.sheets) != 2 or {sheet.role for sheet in envelope.sheets} != set(
        expected_sheets
    ):
        raise BiffXlsNormalizationError(
            "INVALID_SOURCE_SHEET_SET", "exactly the two real source roles are required"
        )
    sheet_indices = tuple(sheet.sheet_index for sheet in envelope.sheets)
    if len(set(sheet_indices)) != len(sheet_indices) or sheet_indices != tuple(
        sorted(sheet.sheet_index for sheet in envelope.sheets)
    ):
        raise BiffXlsNormalizationError(
            "INVALID_SOURCE_SHEET_ORDER", "source sheets must remain in workbook order"
        )
    _validate_workbook_sheet_inventory(envelope)
    for sheet in envelope.sheets:
        expected_name, expected_headers = expected_sheets[sheet.role]
        _validate_sheet(envelope, sheet, expected_name, expected_headers)


def _validate_sheet(
    envelope: BiffXlsEnvelope,
    sheet: ParsedSheet,
    expected_name: str,
    expected_headers: tuple[str, ...],
) -> None:
    if (
        sheet.exact_name != expected_name
        or sheet.visibility != "visible"
        or sheet.visibility_code != 0
    ):
        raise BiffXlsNormalizationError(
            "INVALID_SOURCE_SHEET_IDENTITY", "sheet name or visibility changed"
        )
    if tuple(cell.raw_value for cell in sheet.headers) != expected_headers:
        raise BiffXlsNormalizationError(
            "INVALID_SOURCE_HEADERS", "exact ordered source headers changed"
        )
    if any(cell.cell_type != "text" for cell in sheet.headers):
        raise BiffXlsNormalizationError(
            "INVALID_SOURCE_HEADERS", "every source header must retain BIFF text type"
        )
    if sheet.header_row < 1 or sheet.header_start_column < 1:
        raise BiffXlsNormalizationError(
            "INVALID_SOURCE_HEADER_PROVENANCE",
            "header row and start column must be positive",
        )
    for expected_column, cell in enumerate(
        sheet.headers, start=sheet.header_start_column
    ):
        if (
            cell.source_row != sheet.header_row
            or cell.source_column != expected_column
            or cell.coordinate != f"{_column_label(expected_column)}{sheet.header_row}"
        ):
            raise BiffXlsNormalizationError(
                "INVALID_SOURCE_HEADER_PROVENANCE",
                "header cells no longer match the declared row and columns",
            )
        _validate_source_cell(cell)
    if not sheet.rows:
        raise BiffXlsNormalizationError(
            "INVALID_SOURCE_ROWS", "each source role requires an applicable row"
        )
    previous_source_row = sheet.header_row
    for expected_occurrence, row in enumerate(sheet.rows, start=1):
        if row.source_row <= previous_source_row:
            raise BiffXlsNormalizationError(
                "INVALID_SOURCE_ROW_ORDER",
                "source rows must remain strictly increasing after the header",
            )
        previous_source_row = row.source_row
        expected_reference = (
            f"BIFF_XLS_V{SOURCE_CONTRACT_VERSION}:{envelope.workbook_sha256}:"
            f"{sheet.sheet_index}:{row.source_row}"
        )
        if (
            row.occurrence_index != expected_occurrence
            or row.source_reference != expected_reference
            or len(row.cells) != len(expected_headers)
        ):
            raise BiffXlsNormalizationError(
                "INVALID_SOURCE_ROW_PROVENANCE",
                "row occurrence, source reference, or cell count changed",
            )
        for expected_column, cell in enumerate(
            row.cells, start=sheet.header_start_column
        ):
            if (
                cell.source_row != row.source_row
                or cell.source_column != expected_column
                or cell.coordinate
                != f"{_column_label(expected_column)}{row.source_row}"
            ):
                raise BiffXlsNormalizationError(
                    "INVALID_SOURCE_CELL_PROVENANCE",
                    "cell coordinates no longer match row and column provenance",
                )
            _validate_source_cell(cell)
        row_payload = {
            "cells": [cell.to_dict() for cell in row.cells],
            "occurrence_index": row.occurrence_index,
            "source_reference": row.source_reference,
            "source_row": row.source_row,
        }
        if row.row_fingerprint != canonical_fingerprint(row_payload):
            raise BiffXlsNormalizationError(
                "INVALID_SOURCE_ROW_FINGERPRINT", "row fingerprint does not match cells"
            )
    sheet_payload = {
        "exact_name": sheet.exact_name,
        "header_row": sheet.header_row,
        "header_start_column": sheet.header_start_column,
        "headers": [header.to_dict() for header in sheet.headers],
        "merged_ranges": list(sheet.merged_ranges),
        "role": sheet.role,
        "rows": [row.to_dict() for row in sheet.rows],
        "sheet_index": sheet.sheet_index,
        "visibility": sheet.visibility,
        "visibility_code": sheet.visibility_code,
    }
    if sheet.sheet_fingerprint != canonical_fingerprint(sheet_payload):
        raise BiffXlsNormalizationError(
            "INVALID_SOURCE_SHEET_FINGERPRINT",
            "sheet fingerprint does not match content",
        )


def _validate_workbook_sheet_inventory(envelope: BiffXlsEnvelope) -> None:
    inventory = envelope.workbook_sheets
    if not inventory:
        raise BiffXlsNormalizationError(
            "INVALID_WORKBOOK_SHEET_INVENTORY",
            "workbook sheet inventory must not be empty",
        )
    for expected_index, item in enumerate(inventory):
        if not isinstance(item, dict) or item.get("sheet_index") != expected_index:
            raise BiffXlsNormalizationError(
                "INVALID_WORKBOOK_SHEET_INVENTORY",
                "workbook sheet inventory indices must be complete and ordered",
            )
    for sheet in envelope.sheets:
        matching = [
            item for item in inventory if item.get("normalized_role") == sheet.role
        ]
        expected_item = {
            "exact_name": sheet.exact_name,
            "normalized_role": sheet.role,
            "sheet_index": sheet.sheet_index,
            "visibility": sheet.visibility,
            "visibility_code": sheet.visibility_code,
        }
        if len(matching) != 1 or matching[0] != expected_item:
            raise BiffXlsNormalizationError(
                "INVALID_WORKBOOK_SHEET_INVENTORY",
                "parsed sheet identity does not match the workbook inventory",
            )


def _validate_source_cell(cell: SourceCell) -> None:
    expected_code = _BIFF_TYPE_CODE_BY_KIND.get(cell.cell_type)
    if expected_code is None or cell.biff_type_code != expected_code:
        raise BiffXlsNormalizationError(
            "INVALID_SOURCE_CELL_TYPE_BINDING",
            "semantic cell type does not match its BIFF type code",
        )
    value = cell.raw_value
    valid_value = (
        value is None
        if cell.cell_type in {"empty", "blank"}
        else isinstance(value, str)
        if cell.cell_type == "text"
        else isinstance(value, (int, float)) and not isinstance(value, bool)
        if cell.cell_type in {"number", "date_serial"}
        else isinstance(value, bool)
        if cell.cell_type == "boolean"
        else isinstance(value, int) and not isinstance(value, bool)
    )
    if not valid_value:
        raise BiffXlsNormalizationError(
            "INVALID_SOURCE_CELL_VALUE",
            "raw value is inconsistent with the declared BIFF cell type",
        )
    if (cell.cell_type == "error") != (cell.error_text is not None):
        raise BiffXlsNormalizationError(
            "INVALID_SOURCE_ERROR_METADATA",
            "error text must be present only for a BIFF error cell",
        )


def _column_label(one_based_column: int) -> str:
    label = ""
    column = one_based_column
    while column:
        column, remainder = divmod(column - 1, 26)
        label = chr(ord("A") + remainder) + label
    return label
