"""Synthetic projection tests; no retained workbook, database, or audit input."""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from portfolio_advisor.canonical import canonical_fingerprint
from portfolio_advisor.workbook_source import (
    MODEL_METRIC_ORIGINAL_V1 as ORIGINAL,
)
from portfolio_advisor.workbook_source import (
    MODEL_METRIC_ZERO_TO_ABSENCE_COMPATIBILITY_V1 as COMPATIBILITY,
)
from portfolio_advisor.workbook_source import (
    ModelMetricProjectionError,
    project_model_metrics,
)
from portfolio_advisor.workbook_source.biff_xls import (
    ANALYTICAL_SHORTLIST_ROLE,
    METRIC_HEADERS,
    MODEL_PORTFOLIO_ROLE,
    BiffXlsEnvelope,
    SourceCell,
    parse_biff_xls,
)
from portfolio_advisor.workbook_source.model_metric_projection import ProjectionChoice
from portfolio_advisor.workbook_source.normalization import (
    BiffXlsNormalizationCandidate,
    normalize_biff_xls_envelope,
)
from tests.fixtures.biff_xls_fixture import write_biff_fixture


@pytest.fixture
def envelope(tmp_path: Path) -> BiffXlsEnvelope:
    return parse_biff_xls(
        write_biff_fixture(tmp_path, duplicate_shortlist_first_row=True)
    )


def _normalize(envelope: BiffXlsEnvelope) -> BiffXlsNormalizationCandidate:
    return normalize_biff_xls_envelope(
        envelope, expected_workbook_sha256=envelope.workbook_sha256
    )


def _cell(
    envelope: BiffXlsEnvelope,
    header: str,
    *,
    role: str = MODEL_PORTFOLIO_ROLE,
    occurrence: int = 1,
    **changes: Any,
) -> BiffXlsEnvelope:
    sheet = envelope.sheet(role)
    row = sheet.rows[occurrence - 1]
    index = next(i for i, cell in enumerate(sheet.headers) if cell.raw_value == header)
    cells = list(row.cells)
    cells[index] = replace(cells[index], **changes)
    return _cells(envelope, role, occurrence, tuple(cells))


def _cells(
    envelope: BiffXlsEnvelope, role: str, occurrence: int, cells: tuple[SourceCell, ...]
) -> BiffXlsEnvelope:
    sheet = envelope.sheet(role)
    rows = list(sheet.rows)
    row = replace(rows[occurrence - 1], cells=cells)
    payload = row.to_dict()
    payload.pop("row_fingerprint")
    rows[occurrence - 1] = replace(row, row_fingerprint=canonical_fingerprint(payload))
    sheet = replace(sheet, rows=tuple(rows))
    payload = sheet.to_dict()
    payload.pop("sheet_fingerprint")
    sheet = replace(sheet, sheet_fingerprint=canonical_fingerprint(payload))
    return replace(
        envelope, sheets=tuple(sheet if s.role == role else s for s in envelope.sheets)
    )


@pytest.mark.parametrize("header", METRIC_HEADERS)
@pytest.mark.parametrize("mode", [ORIGINAL, COMPATIBILITY])
def test_all_twelve_zero_metrics_require_explicit_projection(
    envelope: BiffXlsEnvelope,
    header: str,
    mode: ProjectionChoice,
) -> None:
    candidate = _normalize(
        _cell(envelope, header, raw_value=-0.0, cell_type="number", biff_type_code=2)
    )
    before = candidate.to_json()
    result = project_model_metrics(
        candidate,
        projection=mode,
        expected_candidate_fingerprint=candidate.candidate_fingerprint,
    )
    metric = next(
        item for item in result.rows[0].metrics if item.original.header == header
    )
    assert len(result.rows[0].metrics) == 12
    assert metric.original.normalized_value == 0.0
    assert math.copysign(1, float(metric.original.source_cell.raw_value)) == -1  # type: ignore[arg-type]
    assert metric.zero_policy_applied
    if mode == COMPATIBILITY:
        assert metric.value is None
        assert metric.disposition == "OMITTED_NUMERIC_ZERO_FOR_LEGACY_COMPATIBILITY"
    else:
        assert metric.value == 0.0
        assert metric.disposition == "PRESENT"
        assert math.copysign(1, metric.value) == -1
    assert candidate.to_json() == before
    assert result.original_candidate is candidate
    assert result.original_candidate.diagnostics == candidate.diagnostics
    assert any(
        item.code == "UNRESOLVED_MODEL_ZERO_SEMANTICS" for item in candidate.diagnostics
    )
    assert (
        result.admission_approval
        == result.to_dict()["admission_approval"]
        == "NOT_GRANTED"
    )


@pytest.mark.parametrize("header", METRIC_HEADERS)
@pytest.mark.parametrize("value", [0.12345678901234567, -1.2345678901234567, 5e-324])
def test_nonzero_numbers_are_never_scaled_or_omitted(
    envelope: BiffXlsEnvelope,
    header: str,
    value: float,
) -> None:
    candidate = _normalize(
        _cell(envelope, header, raw_value=value, cell_type="number", biff_type_code=2)
    )
    for mode in (ORIGINAL, COMPATIBILITY):
        result = project_model_metrics(
            candidate,
            projection=mode,
            expected_candidate_fingerprint=candidate.candidate_fingerprint,
        )
        metric = next(
            item for item in result.rows[0].metrics if item.original.header == header
        )
        assert (metric.value, metric.disposition, metric.zero_policy_applied) == (
            value,
            "PRESENT",
            False,
        )


@pytest.mark.parametrize(
    ("kind", "code", "value", "error", "disposition", "diagnostic"),
    [
        ("text", 1, "0", None, "REJECTED", "INVALID_METRIC_CELL_TYPE"),
        ("text", 1, "", None, "SOURCE_MISSING", "EMPTY_TEXT_AS_MISSING"),
        ("blank", 6, None, None, "SOURCE_MISSING", None),
        ("empty", 0, None, None, "SOURCE_MISSING", None),
        ("error", 5, 15, "#VALUE!", "REJECTED", "INVALID_METRIC_CELL_TYPE"),
        ("date_serial", 3, 0.0, None, "REJECTED", "INVALID_METRIC_CELL_TYPE"),
        ("boolean", 4, False, None, "REJECTED", "INVALID_METRIC_CELL_TYPE"),
        ("number", 2, float("inf"), None, "REJECTED", "NON_FINITE_METRIC_VALUE"),
        ("number", 2, float("-inf"), None, "REJECTED", "NON_FINITE_METRIC_VALUE"),
        ("number", 2, float("nan"), None, "REJECTED", "NON_FINITE_METRIC_VALUE"),
    ],
)
def test_excluded_cell_states_keep_existing_semantics(
    envelope: BiffXlsEnvelope,
    kind: str,
    code: int,
    value: object,
    error: str | None,
    disposition: str,
    diagnostic: str | None,
) -> None:
    candidate = _normalize(
        _cell(
            envelope,
            "YTD",
            cell_type=kind,
            biff_type_code=code,
            raw_value=value,
            error_text=error,
        )
    )
    for mode in (ORIGINAL, COMPATIBILITY):
        result = project_model_metrics(
            candidate,
            projection=mode,
            expected_candidate_fingerprint=candidate.candidate_fingerprint,
        )
        metric = result.rows[0].metrics[0]
        assert (metric.value, metric.disposition, metric.zero_policy_applied) == (
            None,
            disposition,
            False,
        )
        assert metric.original.source_cell.cell_type == kind
        assert metric.original.source_cell.biff_type_code == code
        assert result.original_candidate.to_json() == candidate.to_json()
        if diagnostic:
            assert diagnostic in {
                item.code for item in result.original_candidate.diagnostics
            }


def test_all_zero_duplicate_rows_and_unrelated_fields_survive(
    envelope: BiffXlsEnvelope,
) -> None:
    for header in (*METRIC_HEADERS, "Hányad (%)"):
        envelope = _cell(
            envelope, header, raw_value=0.0, cell_type="number", biff_type_code=2
        )
    first, second = envelope.sheet(MODEL_PORTFOLIO_ROLE).rows
    duplicate_cells = tuple(
        replace(cell, source_row=other.source_row, coordinate=other.coordinate)
        for cell, other in zip(first.cells, second.cells, strict=True)
    )
    candidate = _normalize(_cells(envelope, MODEL_PORTFOLIO_ROLE, 2, duplicate_cells))
    result = project_model_metrics(
        candidate,
        projection=COMPATIBILITY,
        expected_candidate_fingerprint=candidate.candidate_fingerprint,
    )
    assert len(result.rows) == 2
    assert (
        result.rows[0].original.occurrence_id != result.rows[1].original.occurrence_id
    )
    for projected, original in zip(result.rows, candidate.sheets[0].rows, strict=True):
        assert projected.original is original
        assert all(metric.value is None for metric in projected.metrics)
        assert all(metric.zero_policy_applied for metric in projected.metrics)
        weight = next(
            field for field in projected.original.fields if field.header == "Hányad (%)"
        )
        assert weight.normalized_value == 0.0
    assert result.original_candidate.sheet(
        ANALYTICAL_SHORTLIST_ROLE
    ) is candidate.sheet(ANALYTICAL_SHORTLIST_ROLE)
    assert len(result.original_candidate.sheet(ANALYTICAL_SHORTLIST_ROLE).rows) == 3
    assert any(
        field.normalized_value == 0.0
        for row in candidate.sheets[1].rows
        for field in row.fields
    )


def test_provenance_determinism_and_deep_immutability(
    envelope: BiffXlsEnvelope, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _normalize(envelope)
    fingerprint = candidate.candidate_fingerprint

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("pure projection attempted I/O")

    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr("sqlite3.connect", forbidden)
    first = project_model_metrics(
        candidate, projection=COMPATIBILITY, expected_candidate_fingerprint=fingerprint
    )
    second = project_model_metrics(
        candidate, projection=COMPATIBILITY, expected_candidate_fingerprint=fingerprint
    )
    original = project_model_metrics(
        candidate, projection=ORIGINAL, expected_candidate_fingerprint=fingerprint
    )
    assert first.to_json() == second.to_json()
    assert first.projection_fingerprint == canonical_fingerprint(first._payload())
    assert first.projection_fingerprint != original.projection_fingerprint
    payload = first.to_dict()
    assert payload["model_sheet"] == {
        "exact_name": " modell portfóliók",
        "role": MODEL_PORTFOLIO_ROLE,
        "sheet_index": 0,
        "sheet_fingerprint": candidate.sheets[0].sheet_fingerprint,
    }
    assert (
        candidate.source_binding.workbook_sha256
        in first.rows[0].original.source_reference
    )
    assert "2026-09-26" in first.to_json()
    assert "FORMULA_ORIGIN_NOT_ESTABLISHED" in first.to_json()
    assert "UNRESOLVED_METRIC_HORIZON_SEMANTICS" in first.to_json()
    assert "ANOMALOUS_CURRENCY_RISK_VALUE" in first.to_json()
    for obj, key in (
        (first, "projection"),
        (first.rows[0], "metrics"),
        (first.rows[0].metrics[0], "value"),
    ):
        with pytest.raises(AttributeError):
            setattr(obj, key, "changed")
    payload.clear()
    assert first.to_json() == second.to_json()


@pytest.mark.parametrize(
    "choice",
    [None, "", "original", "compatibility", "MODEL_METRIC_ORIGINAL_V2", True, []],
)
def test_unsupported_projection_is_rejected(
    envelope: BiffXlsEnvelope, choice: Any
) -> None:
    candidate = _normalize(envelope)
    with pytest.raises(ModelMetricProjectionError, match="UNSUPPORTED_PROJECTION"):
        project_model_metrics(
            candidate,
            projection=choice,
            expected_candidate_fingerprint=candidate.candidate_fingerprint,
        )


def test_selection_and_binding_are_required(envelope: BiffXlsEnvelope) -> None:
    candidate = _normalize(envelope)
    with pytest.raises(TypeError):
        project_model_metrics(
            candidate, expected_candidate_fingerprint=candidate.candidate_fingerprint
        )  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        project_model_metrics(candidate, projection=ORIGINAL)  # type: ignore[call-arg]
    with pytest.raises(
        ModelMetricProjectionError, match="CANDIDATE_FINGERPRINT_MISMATCH"
    ):
        project_model_metrics(
            candidate, projection=ORIGINAL, expected_candidate_fingerprint="0" * 64
        )


@pytest.mark.parametrize(
    "damage",
    [
        "list",
        "bool_version",
        "wrong_hash",
        "date",
        "role",
        "name",
        "coordinates",
        "missing_field",
        "wrong_target",
        "altered_value",
        "nonfinite_value",
        "wrong_type",
        "wrong_status",
        "row_identity",
        "field_identity",
        "dropped_diagnostic",
        "missing_sheet",
    ],
)
def test_malformed_candidates_fail_even_with_recomputed_fingerprint(
    envelope: BiffXlsEnvelope,
    damage: str,
) -> None:
    candidate = _normalize(envelope)
    sheet = candidate.sheets[0]
    row = sheet.rows[0]
    metric_index = next(i for i, item in enumerate(row.fields) if item.header == "YTD")
    field = row.fields[metric_index]
    if damage == "list":
        candidate = replace(candidate, sheets=list(candidate.sheets))  # type: ignore[arg-type]
    elif damage == "bool_version":
        candidate = replace(
            candidate,
            source_binding=replace(
                candidate.source_binding, source_contract_version=True
            ),
        )
    elif damage == "wrong_hash":
        candidate = replace(
            candidate,
            source_binding=replace(candidate.source_binding, workbook_sha256="f" * 64),
        )
    elif damage == "date":
        candidate = replace(
            candidate,
            source_binding=replace(
                candidate.source_binding, snapshot_date="2026-01-16"
            ),
        )
    elif damage == "missing_sheet":
        candidate = replace(candidate, sheets=(sheet,))
    elif damage == "dropped_diagnostic":
        candidate = replace(
            candidate,
            diagnostics=tuple(
                item
                for item in candidate.diagnostics
                if item.code != "UNRESOLVED_MODEL_ZERO_SEMANTICS"
            ),
        )
    else:
        if damage == "role":
            sheet = replace(sheet, role=ANALYTICAL_SHORTLIST_ROLE)
        elif damage == "name":
            sheet = replace(sheet, exact_name="modell portfóliók")
        elif damage == "row_identity":
            row = replace(row, occurrence_id="fabricated")
        elif damage == "missing_field":
            row = replace(row, fields=row.fields[:-1])
        else:
            variants: dict[str, dict[str, Any]] = {
                "coordinates": {
                    "source_cell": replace(field.source_cell, coordinate="J999")
                },
                "wrong_target": {"target_field": "reported_weight"},
                "altered_value": {"normalized_value": 10.0},
                "nonfinite_value": {"normalized_value": float("inf")},
                "wrong_type": {
                    "source_cell": replace(field.source_cell, raw_value=False)
                },
                "wrong_status": {"status": "SOURCE_MISSING"},
                "field_identity": {"field_occurrence_id": "fabricated"},
            }
            altered = replace(field, **variants[damage])
            row = replace(
                row,
                fields=tuple(
                    altered if i == metric_index else f
                    for i, f in enumerate(row.fields)
                ),
            )
        sheet = replace(sheet, rows=(row, *sheet.rows[1:]))
        candidate = replace(candidate, sheets=(sheet, candidate.sheets[1]))
    with pytest.raises(ModelMetricProjectionError):
        project_model_metrics(
            candidate,
            projection=COMPATIBILITY,
            expected_candidate_fingerprint=candidate.candidate_fingerprint,
        )


@pytest.mark.parametrize(
    ("damage", "reason"),
    [
        ("coordinates", "invalid cell coordinates"),
        ("typed_value", "INVALID_SOURCE_CELL_VALUE"),
        ("source_reference", "invalid row occurrence provenance"),
    ],
)
def test_semantic_damage_fails_with_both_fingerprints_recomputed(
    envelope: BiffXlsEnvelope, damage: str, reason: str
) -> None:
    candidate = _normalize(envelope)
    sheet = candidate.sheets[0]
    row = sheet.rows[0]
    if damage == "source_reference":
        row = replace(row, source_reference="fabricated")
    else:
        fields = tuple(
            replace(
                field,
                source_cell=replace(
                    field.source_cell,
                    **(
                        {"coordinate": "J999"}
                        if damage == "coordinates"
                        else {"raw_value": False}
                    ),
                ),
            )
            if field.header == "YTD"
            else field
            for field in row.fields
        )
        row = replace(row, fields=fields)
    row = replace(
        row,
        row_fingerprint=canonical_fingerprint(
            {
                "occurrence_index": row.occurrence_index,
                "source_row": row.source_row,
                "source_reference": row.source_reference,
                "cells": [field.source_cell.to_dict() for field in row.fields],
            }
        ),
    )
    candidate = replace(
        candidate,
        sheets=(replace(sheet, rows=(row, *sheet.rows[1:])), candidate.sheets[1]),
    )
    with pytest.raises(ModelMetricProjectionError, match=reason):
        project_model_metrics(
            candidate,
            projection=COMPATIBILITY,
            expected_candidate_fingerprint=candidate.candidate_fingerprint,
        )


@pytest.mark.parametrize("candidate", [None, {}, [], "candidate"])
def test_untyped_inputs_fail_clearly(candidate: Any) -> None:
    with pytest.raises(ModelMetricProjectionError, match="MALFORMED_CANDIDATE"):
        project_model_metrics(
            candidate, projection=COMPATIBILITY, expected_candidate_fingerprint="0" * 64
        )


def test_future_source_has_no_inherited_admission_authority(tmp_path: Path) -> None:
    envelope = parse_biff_xls(
        write_biff_fixture(tmp_path, filename="Synthetic_Future_20310115.xls")
    )
    candidate = _normalize(envelope)
    result = project_model_metrics(
        candidate,
        projection=COMPATIBILITY,
        expected_candidate_fingerprint=candidate.candidate_fingerprint,
    )
    assert result.original_candidate.source_binding.snapshot_date == "2031-01-15"
    assert result.admission_approval == "NOT_GRANTED"
    assert {metric.original.header for metric in result.rows[0].metrics} == {
        "YTD",
        "1yr",
        "3yr",
        "5yr",
        "1Y Sharpe",
        "3Y Sharpe",
        "5Y Sharpe",
        "1Y Vol.",
        "3Y Vol.",
        "Down. risk",
        "Info. ratio",
        "Max. drawd.",
    }
    assert result.original_candidate.diagnostics == candidate.diagnostics


def test_unsupported_normalization_version_fails_closed(
    envelope: BiffXlsEnvelope, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _normalize(envelope)
    monkeypatch.setattr(
        "portfolio_advisor.workbook_source.normalization.CONTRACT_VERSION", 2
    )
    with pytest.raises(
        ModelMetricProjectionError, match="unsupported normalization contract"
    ):
        project_model_metrics(
            candidate,
            projection=ORIGINAL,
            expected_candidate_fingerprint=candidate.candidate_fingerprint,
        )
