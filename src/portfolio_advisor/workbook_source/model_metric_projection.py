"""Explicit, in-memory model metric projections; never admission authority.

Input fingerprints bind caller-supplied candidates, not freshly inspected XLS
bytes. The complete candidate remains immutable evidence in either projection.
"""

from __future__ import annotations

import re
import types
from dataclasses import dataclass, fields, is_dataclass
from datetime import date
from functools import cache
from typing import Final, Literal, TypeAliasType, get_args, get_origin, get_type_hints

from portfolio_advisor.canonical import canonical_fingerprint, canonical_json
from portfolio_advisor.workbook_source import biff_xls as source
from portfolio_advisor.workbook_source import normalization

MODEL_METRIC_ORIGINAL_V1: Final = "MODEL_METRIC_ORIGINAL_V1"
MODEL_METRIC_ZERO_TO_ABSENCE_COMPATIBILITY_V1: Final = (
    "MODEL_METRIC_ZERO_TO_ABSENCE_COMPATIBILITY_V1"
)
POLICY_NAME: Final = "MODEL_METRIC_ZERO_HANDLING_POLICY_V1"
POLICY_APPROVAL_DATE: Final = "2026-09-26"
CONTRACT_NAME: Final = "MODEL_METRIC_PROJECTION"
CONTRACT_VERSION: Final = 1
_METRIC_SCOPE: Final = (
    ("YTD", "YTD"),
    ("1yr", "RETURN_1Y"),
    ("3yr", "RETURN_3Y"),
    ("5yr", "RETURN_5Y"),
    ("1Y Sharpe", "SHARPE_RATIO_1Y"),
    ("3Y Sharpe", "SHARPE_RATIO_3Y"),
    ("5Y Sharpe", "SHARPE_RATIO_5Y"),
    ("1Y Vol.", "VOLATILITY_1Y"),
    ("3Y Vol.", "VOLATILITY_3Y"),
    ("Down. risk", "DOWNSIDE_RISK"),
    ("Info. ratio", "INFORMATION_RATIO"),
    ("Max. drawd.", "MAXIMUM_DRAWDOWN"),
)
_METRIC_HEADERS: Final = tuple(header for header, _ in _METRIC_SCOPE)

type ProjectionChoice = Literal[
    "MODEL_METRIC_ORIGINAL_V1", "MODEL_METRIC_ZERO_TO_ABSENCE_COMPATIBILITY_V1"
]
type MetricDisposition = Literal[
    "PRESENT",
    "OMITTED_NUMERIC_ZERO_FOR_LEGACY_COMPATIBILITY",
    "SOURCE_MISSING",
    "REJECTED",
]


class ModelMetricProjectionError(ValueError):
    """An unsupported choice or inconsistent normalization candidate."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"[{code}] {message}")


@dataclass(frozen=True, slots=True)
class ProjectedModelMetric:
    """An observation disposition with its unchanged typed original."""

    original: normalization.NormalizedFieldCandidate
    disposition: MetricDisposition
    value: float | None
    zero_policy_applied: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "original": self.original.to_dict(),
            "disposition": self.disposition,
            "value": self.value,
            "zero_policy_applied": self.zero_policy_applied,
        }


@dataclass(frozen=True, slots=True)
class ProjectedModelRow:
    """Every model occurrence survives, even when all metrics are omitted."""

    original: normalization.NormalizedRowCandidate
    metrics: tuple[ProjectedModelMetric, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "occurrence_id": self.original.occurrence_id,
            "occurrence_index": self.original.occurrence_index,
            "source_row": self.original.source_row,
            "source_reference": self.original.source_reference,
            "row_fingerprint": self.original.row_fingerprint,
            "metrics": [metric.to_dict() for metric in self.metrics],
        }


@dataclass(frozen=True, slots=True)
class ModelMetricProjection:
    """Immutable projection plus the complete, unchanged two-sheet candidate."""

    original_candidate: normalization.BiffXlsNormalizationCandidate
    projection: ProjectionChoice
    rows: tuple[ProjectedModelRow, ...]

    @property
    def admission_approval(self) -> str:
        return "NOT_GRANTED"

    def _payload(self) -> dict[str, object]:
        sheet = self.original_candidate.sheet(source.MODEL_PORTFOLIO_ROLE)
        return {
            "contract": {"name": CONTRACT_NAME, "version": CONTRACT_VERSION},
            "projection": {"name": self.projection, "version": 1},
            "policy": {
                "name": POLICY_NAME,
                "version": 1,
                "approved_on": POLICY_APPROVAL_DATE,
            },
            "admission_approval": self.admission_approval,
            "status": "NOT_EVALUATED_PROJECTION_ONLY",
            "original_candidate": self.original_candidate.to_dict(),
            "model_sheet": {
                "exact_name": sheet.exact_name,
                "role": sheet.role,
                "sheet_index": sheet.sheet_index,
                "sheet_fingerprint": sheet.sheet_fingerprint,
            },
            "rows": [row.to_dict() for row in self.rows],
        }

    @property
    def projection_fingerprint(self) -> str:
        return canonical_fingerprint(self._payload())

    def to_dict(self) -> dict[str, object]:
        return {
            **self._payload(),
            "projection_fingerprint": self.projection_fingerprint,
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())


def project_model_metrics(
    candidate: normalization.BiffXlsNormalizationCandidate,
    *,
    projection: ProjectionChoice,
    expected_candidate_fingerprint: str,
) -> ModelMetricProjection:
    """Select original or compatibility metrics, without selecting any consumer.

    Both keyword arguments are required. The fingerprint is an input binding,
    not approval or evidence of workbook inspection. All source diagnostics are
    retained verbatim; ``zero_policy_applied`` records this policy's resolution
    of an eligible zero only, including in original mode. It waives no other gate.
    """
    if type(projection) is not str or projection not in (
        MODEL_METRIC_ORIGINAL_V1,
        MODEL_METRIC_ZERO_TO_ABSENCE_COMPATIBILITY_V1,
    ):
        raise ModelMetricProjectionError(
            "UNSUPPORTED_PROJECTION", "select an exact v1 projection name"
        )
    try:
        _validate_candidate(candidate, expected_candidate_fingerprint)
    except ModelMetricProjectionError:
        raise
    except (
        AttributeError,
        TypeError,
        ValueError,
        OverflowError,
        RecursionError,
    ) as error:
        raise ModelMetricProjectionError("MALFORMED_CANDIDATE", str(error)) from error
    return ModelMetricProjection(
        original_candidate=candidate,
        projection=projection,
        rows=tuple(
            ProjectedModelRow(
                original=row,
                metrics=tuple(
                    _project_metric(field, projection)
                    for field in row.fields
                    if field.header in _METRIC_HEADERS
                ),
            )
            for row in candidate.sheet(source.MODEL_PORTFOLIO_ROLE).rows
        ),
    )


def _project_metric(
    field: normalization.NormalizedFieldCandidate, projection: ProjectionChoice
) -> ProjectedModelMetric:
    if field.status == "SOURCE_MISSING":
        return ProjectedModelMetric(field, "SOURCE_MISSING", None, False)
    if field.status == "REJECTED":
        return ProjectedModelMetric(field, "REJECTED", None, False)
    # Validation replays the existing normalizer, so this is a finite BIFF number.
    assert type(field.normalized_value) is float
    zero = field.normalized_value == 0.0
    if zero and projection == MODEL_METRIC_ZERO_TO_ABSENCE_COMPATIBILITY_V1:
        return ProjectedModelMetric(
            field, "OMITTED_NUMERIC_ZERO_FOR_LEGACY_COMPATIBILITY", None, True
        )
    return ProjectedModelMetric(field, "PRESENT", field.normalized_value, zero)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ModelMetricProjectionError("MALFORMED_CANDIDATE", message)


def _hash(value: str) -> bool:
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


@cache
def _annotations(cls: type) -> dict[str, object]:
    return get_type_hints(cls)


def _typed(value: object, annotation: object) -> bool:
    """Reject mutable containers, subclasses, and bool-as-int at this boundary."""
    if isinstance(annotation, TypeAliasType):
        return _typed(value, annotation.__value__)
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is types.UnionType:
        return any(_typed(value, arg) for arg in args)
    if origin is Literal:
        return any(type(value) is type(arg) and value == arg for arg in args)
    if origin is tuple:
        return type(value) is tuple and all(_typed(item, args[0]) for item in value)
    if isinstance(annotation, type) and is_dataclass(annotation):
        return type(value) is annotation and all(
            _typed(getattr(value, field.name), _annotations(annotation)[field.name])
            for field in fields(annotation)
        )
    return type(value) is annotation


def _validate_candidate(
    candidate: normalization.BiffXlsNormalizationCandidate, expected_fingerprint: str
) -> None:
    _require(
        _typed(candidate, normalization.BiffXlsNormalizationCandidate),
        "expected an immutable typed v1 candidate",
    )
    _require(
        normalization.CONTRACT_NAME == "BIFF_XLS_NORMALIZATION_CANDIDATE_V1"
        and normalization.CONTRACT_VERSION == 1
        and normalization.POLICY_VERSION == 1
        and tuple(source.METRIC_HEADERS) == _METRIC_HEADERS,
        "unsupported normalization contract or metric scope",
    )
    _require(
        _hash(expected_fingerprint),
        "expected candidate fingerprint must be lowercase SHA-256",
    )
    if candidate.candidate_fingerprint != expected_fingerprint:
        raise ModelMetricProjectionError(
            "CANDIDATE_FINGERPRINT_MISMATCH",
            "candidate differs from its expected binding",
        )
    binding = candidate.source_binding
    _require(
        _hash(binding.workbook_sha256)
        and _hash(binding.envelope_fingerprint)
        and binding.byte_length > 0,
        "invalid source binding",
    )
    _require(
        (
            binding.source_contract_name,
            binding.source_contract_version,
            binding.source_admission_status,
            binding.parser_name,
            binding.parser_version,
            binding.parser_library,
            binding.compound_document_recovery,
        )
        == (
            source.CONTRACT_NAME,
            1,
            source.ADMISSION_STATUS,
            source.PARSER_NAME,
            1,
            "xlrd",
            "XLRD_IGNORE_WORKBOOK_CORRUPTION",
        )
        and bool(binding.parser_library_version),
        "unsupported source contract or parser",
    )
    match = re.fullmatch(
        r".+?(?<!\d)(\d{8})\.xls", binding.source_filename, re.IGNORECASE
    )
    _require(match is not None, "invalid dated source filename")
    assert match is not None
    _require(
        date.fromisoformat(match[1]).isoformat() == binding.snapshot_date,
        "snapshot date differs from filename",
    )
    specs = {
        source.MODEL_PORTFOLIO_ROLE: (source.MODEL_SHEET_NAME, source.MODEL_HEADERS),
        source.ANALYTICAL_SHORTLIST_ROLE: (
            source.SHORTLIST_SHEET_NAME,
            source.SHORTLIST_HEADERS,
        ),
    }
    _require(
        len(candidate.sheets) == 2
        and {sheet.role for sheet in candidate.sheets} == set(specs),
        "expected both source roles exactly once",
    )
    indices = tuple(sheet.sheet_index for sheet in candidate.sheets)
    _require(indices[0] >= 0 and indices[0] < indices[1], "invalid source sheet order")
    # Minimal contexts for replay of the existing field normalizer. Missing
    # envelope-only metadata is never reconstructed as evidence or fingerprinted.
    context = source.BiffXlsEnvelope(
        source_filename=binding.source_filename,
        snapshot_date=binding.snapshot_date,
        workbook_sha256=binding.workbook_sha256,
        byte_length=binding.byte_length,
        workbook_metadata={},
        workbook_sheets=(),
        sheets=(),
        diagnostics=(),
    )
    required_diagnostics: list[normalization.CandidateDiagnostic] = []
    for sheet in candidate.sheets:
        name, headers = specs[sheet.role]
        _require(
            sheet.exact_name == name
            and sheet.visibility == "visible"
            and sheet.visibility_code == 0,
            "invalid exact sheet identity",
        )
        _require(
            _hash(sheet.sheet_fingerprint)
            and sheet.header_row > 0
            and sheet.header_start_column > 0,
            "invalid sheet provenance",
        )
        _require(
            tuple(cell.raw_value for cell in sheet.headers) == headers
            and bool(sheet.rows),
            "invalid headers or empty sheet",
        )
        for column, cell in enumerate(sheet.headers, sheet.header_start_column):
            _validate_cell(cell, sheet.header_row, column)
            _require(cell.cell_type == "text", "header is not text")
        parsed_sheet = source.ParsedSheet(
            role=sheet.role,
            exact_name=sheet.exact_name,
            sheet_index=sheet.sheet_index,
            visibility_code=sheet.visibility_code,
            visibility=sheet.visibility,
            header_row=sheet.header_row,
            header_start_column=sheet.header_start_column,
            headers=sheet.headers,
            merged_ranges=(),
            rows=(),
            sheet_fingerprint=sheet.sheet_fingerprint,
        )
        previous_row = sheet.header_row
        for index, row in enumerate(sheet.rows, 1):
            reference = f"BIFF_XLS_V1:{binding.workbook_sha256}:{sheet.sheet_index}:{row.source_row}"
            _require(
                row.source_row > previous_row
                and row.occurrence_index == index
                and row.source_reference == reference
                and row.occurrence_id == f"{reference}:OCCURRENCE:{index}",
                "invalid row occurrence provenance",
            )
            previous_row = row.source_row
            _require(
                tuple(field.header for field in row.fields) == headers,
                "invalid field order or scope",
            )
            _require(
                tuple(
                    (field.header, field.target_field)
                    for field in row.fields
                    if field.header in _METRIC_HEADERS
                )
                == _METRIC_SCOPE,
                "metric identities differ from approved v1 scope",
            )
            source_row = source.SourceRow(
                index,
                row.source_row,
                reference,
                tuple(field.source_cell for field in row.fields),
                row.row_fingerprint,
            )
            row_payload = source_row.to_dict()
            row_payload.pop("row_fingerprint")
            _require(
                row.row_fingerprint == canonical_fingerprint(row_payload),
                "row fingerprint differs from source cells",
            )
            for column, field in enumerate(row.fields, sheet.header_start_column):
                _validate_cell(field.source_cell, row.source_row, column)
                expected = normalization._normalize_field(
                    context,
                    parsed_sheet,
                    source_row,
                    field.header,
                    field.source_cell,
                    required_diagnostics,
                )
                _require(
                    canonical_json(field.to_dict())
                    == canonical_json(expected.to_dict()),
                    "field normalization or provenance differs from v1 source semantics",
                )
        rejected = any(
            field.status == "REJECTED" for row in sheet.rows for field in row.fields
        )
        _require(
            sheet.status
            == (
                "FIELD_NORMALIZATION_REJECTED"
                if rejected
                else "NORMALIZATION_CANDIDATE_PRODUCED"
            ),
            "inconsistent sheet status",
        )
    supplied_diagnostics = {
        canonical_json(item.to_dict()) for item in candidate.diagnostics
    }
    _require(
        all(
            canonical_json(item.to_dict()) in supplied_diagnostics
            for item in required_diagnostics
        ),
        "required field diagnostic missing or altered",
    )
    for code in (
        "RECOVERY_MODE_ADMISSION_REVIEW_REQUIRED",
        "FORMULA_ORIGIN_NOT_ESTABLISHED",
    ):
        _require(
            any(
                item.code == code
                and item.severity == "UNRESOLVED"
                and item.source_reference
                == f"BIFF_XLS_V1:{binding.workbook_sha256}:WORKBOOK"
                for item in candidate.diagnostics
            ),
            "required workbook diagnostic missing",
        )


def _validate_cell(cell: source.SourceCell, row: int, column: int) -> None:
    normalization._validate_source_cell(cell)
    _require(
        cell.source_row == row
        and cell.source_column == column
        and cell.coordinate == f"{normalization._column_label(column)}{row}",
        "invalid cell coordinates",
    )
    _require(
        cell.xf_index >= 0 and cell.format_key >= 0, "invalid cell format reference"
    )
