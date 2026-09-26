"""Synthetic tests for the non-admitting BIFF normalization candidate."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from portfolio_advisor.canonical import canonical_fingerprint, canonical_json
from portfolio_advisor.workbook_source.biff_xls import (
    ANALYTICAL_SHORTLIST_ROLE,
    MODEL_PORTFOLIO_ROLE,
    BiffXlsEnvelope,
    CellKind,
    JsonScalar,
    ParsedSheet,
    SourceRow,
    parse_biff_xls,
)
from portfolio_advisor.workbook_source.normalization import (
    ADMISSION_APPROVAL,
    APPROVED_SHORTLIST_MAPPING_SHA256,
    CANDIDATE_STATUS,
    CONTRACT_NAME,
    CONTRACT_VERSION,
    BiffXlsNormalizationCandidate,
    BiffXlsNormalizationError,
    NormalizedFieldCandidate,
    normalize_biff_xls_envelope,
)
from tests.fixtures.biff_xls_fixture import write_biff_fixture

_MAPPING_PATH = Path(
    "data/knowledge/validated_rules/shortlist_classification_english_mapping_v1.json"
)


def test_candidate_preserves_evidence_and_zero_missing_distinctions(
    tmp_path: Path,
) -> None:
    workbook = write_biff_fixture(tmp_path)
    envelope = parse_biff_xls(workbook)
    input_before = envelope.to_json()

    candidate = _normalize(envelope)

    assert envelope.to_json() == input_before
    assert candidate.to_dict()["contract"] == {
        "name": CONTRACT_NAME,
        "version": CONTRACT_VERSION,
    }
    assert candidate.to_dict()["candidate_status"] == CANDIDATE_STATUS
    assert candidate.to_dict()["admission_approval"] == ADMISSION_APPROVAL
    assert candidate.source_binding.workbook_sha256 == envelope.workbook_sha256
    assert candidate.source_binding.envelope_fingerprint == (
        envelope.envelope_fingerprint
    )

    model = candidate.sheet(MODEL_PORTFOLIO_ROLE)
    shortlist = candidate.sheet(ANALYTICAL_SHORTLIST_ROLE)
    assert (model.exact_name, shortlist.exact_name) == (
        " modell portfóliók",
        " shortlist",
    )
    assert (model.sheet_index, model.visibility_code, model.visibility) == (
        0,
        0,
        "visible",
    )
    assert (shortlist.sheet_index, shortlist.visibility_code, shortlist.visibility) == (
        1,
        0,
        "visible",
    )
    assert tuple(cell.raw_value for cell in model.headers) == tuple(
        field.header for field in model.rows[0].fields
    )
    weight = _field(model.rows[0].fields, "Hányad (%)")
    assert (weight.normalized_value, weight.normalized_type, weight.semantic_unit) == (
        50.0,
        "REAL",
        "PERCENTAGE_POINTS",
    )

    model_zero = _field(model.rows[0].fields, "YTD")
    assert (model_zero.status, model_zero.normalized_value) == ("NORMALIZED", 0.0)
    assert (model_zero.normalized_type, model_zero.semantic_unit) == ("REAL", "RATIO")
    assert model_zero.source_cell.cell_type == "number"
    assert "UNRESOLVED_MODEL_ZERO_SEMANTICS" in _codes(candidate)

    model_blank = _field(model.rows[1].fields, "YTD")
    model_empty = _field(model.rows[1].fields, "5yr")
    assert (model_blank.status, model_blank.source_cell.cell_type) == (
        "SOURCE_MISSING",
        "blank",
    )
    assert (model_empty.status, model_empty.source_cell.cell_type) == (
        "SOURCE_MISSING",
        "empty",
    )

    text_zero = _field(shortlist.rows[0].fields, "YTD")
    numeric_zero = _field(shortlist.rows[1].fields, "YTD")
    error = _field(shortlist.rows[1].fields, "Info. ratio")
    assert (
        text_zero.status,
        text_zero.normalized_value,
        text_zero.normalized_type,
        text_zero.source_cell.cell_type,
        text_zero.source_cell.raw_value,
    ) == ("REJECTED", None, "NULL", "text", "0")
    assert (
        numeric_zero.status,
        numeric_zero.normalized_value,
        numeric_zero.source_cell.cell_type,
    ) == ("NORMALIZED", 0.0, "number")
    assert (
        error.status,
        error.source_cell.cell_type,
        error.source_cell.error_text,
    ) == (
        "REJECTED",
        "error",
        "#VALUE!",
    )
    assert numeric_zero.target_source_reference == (
        f"SHORTLIST:{envelope.workbook_sha256}: shortlist:3:YTD"
    )
    assert numeric_zero.source_cell.raw_value == 0.0
    assert _field(shortlist.rows[1].fields, "5yr").normalized_value == 0.2
    assert "INVALID_METRIC_CELL_TYPE" in _codes(candidate)


def test_serialization_and_fingerprint_are_deterministic(tmp_path: Path) -> None:
    workbook = write_biff_fixture(tmp_path)
    envelope = parse_biff_xls(workbook)

    first = _normalize(envelope)
    second = _normalize(envelope)

    assert first.to_json() == second.to_json()
    assert first.candidate_fingerprint == second.candidate_fingerprint
    assert (
        first.candidate_fingerprint
        == hashlib.sha256(canonical_json(first._payload()).encode("utf-8")).hexdigest()
    )
    assert "tmp" not in first.to_json()


def test_candidate_provenance_objects_are_deeply_immutable(tmp_path: Path) -> None:
    candidate = _normalize(parse_biff_xls(write_biff_fixture(tmp_path)))
    field = candidate.sheets[0].rows[0].fields[0]

    mutations = (
        (candidate.source_binding, "workbook_sha256", "0" * 64),
        (candidate.sheets[0], "exact_name", "changed"),
        (field.source_cell, "raw_value", "changed"),
    )
    for target, attribute, value in mutations:
        with pytest.raises(AttributeError):
            setattr(target, attribute, value)

    payload = candidate.to_dict()
    source_binding = payload["source_binding"]
    assert isinstance(source_binding, dict)
    source_binding["workbook_sha256"] = "0" * 64
    assert candidate.source_binding.workbook_sha256 != "0" * 64


def test_duplicate_occurrences_remain_distinct_and_in_source_order(
    tmp_path: Path,
) -> None:
    workbook = write_biff_fixture(tmp_path, duplicate_shortlist_first_row=True)
    shortlist = _normalize(parse_biff_xls(workbook)).sheet(ANALYTICAL_SHORTLIST_ROLE)

    first, duplicate = shortlist.rows[:2]
    assert [_cell_content(field) for field in first.fields] == [
        _cell_content(field) for field in duplicate.fields
    ]
    assert [first.occurrence_index, duplicate.occurrence_index] == [1, 2]
    assert first.source_row != duplicate.source_row
    assert first.source_reference != duplicate.source_reference
    assert first.occurrence_id != duplicate.occurrence_id


def test_approved_mapping_is_exact_reference_only_and_unknown_pair_unresolved(
    tmp_path: Path,
) -> None:
    workbook = write_biff_fixture(tmp_path)
    manifest = _MAPPING_PATH.read_bytes()
    assert hashlib.sha256(manifest).hexdigest() == APPROVED_SHORTLIST_MAPPING_SHA256

    candidate = normalize_biff_xls_envelope(
        parse_biff_xls(workbook),
        expected_workbook_sha256=hashlib.sha256(workbook.read_bytes()).hexdigest(),
        approved_shortlist_mapping_manifest=manifest,
    )
    shortlist = candidate.sheet(ANALYTICAL_SHORTLIST_ROLE)
    mapped = shortlist.rows[0].classification
    unknown = shortlist.rows[1].classification

    assert (mapped.original_asset_class, mapped.original_sub_asset_class) == (
        "Kötvény-befektetési kategória",
        "Globál",
    )
    assert (
        mapped.english_asset_class_candidate,
        mapped.english_sub_asset_class_candidate,
    ) == ("Investment Grade Bond", "Global")
    assert mapped.mapping_status == "APPROVED_MANIFEST_REFERENCE_CANDIDATE_ONLY"
    assert mapped.mapping_identity is not None
    assert mapped.mapping_identity.admission_scope == (
        "REFERENCE_ONLY_EXISTING_DATASET_BINDING_NOT_REUSED"
    )
    assert unknown.original_sub_asset_class == "Fejl?d? piacok"
    assert unknown.english_asset_class_candidate is None
    assert unknown.mapping_status == "EXACT_PAIR_NOT_MAPPED"
    assert "UNRESOLVED_SHORTLIST_CLASSIFICATION_MAPPING" in _codes(candidate)
    assert all(
        row.classification.mapping_status == "NOT_APPLICABLE_TO_MODEL_ROLE"
        for row in candidate.sheet(MODEL_PORTFOLIO_ROLE).rows
    )


def test_mapping_bytes_must_match_the_exact_approved_manifest(tmp_path: Path) -> None:
    workbook = write_biff_fixture(tmp_path)
    envelope = parse_biff_xls(workbook)

    with pytest.raises(BiffXlsNormalizationError) as caught:
        normalize_biff_xls_envelope(
            envelope,
            expected_workbook_sha256=envelope.workbook_sha256,
            approved_shortlist_mapping_manifest=b'{"schema_version":1}',
        )

    assert caught.value.code == "UNAPPROVED_CLASSIFICATION_MAPPING_MANIFEST"


def test_non_finite_metric_is_preserved_as_source_and_rejected(tmp_path: Path) -> None:
    workbook = write_biff_fixture(tmp_path)
    envelope = parse_biff_xls(workbook)
    modified = _replace_cell_raw_value(
        envelope,
        role=ANALYTICAL_SHORTLIST_ROLE,
        occurrence_index=2,
        header="1yr",
        raw_value=float("inf"),
    )

    candidate = _normalize(modified)
    field = _field(candidate.sheet(ANALYTICAL_SHORTLIST_ROLE).rows[1].fields, "1yr")

    assert field.status == "REJECTED"
    assert field.normalized_value is None
    assert field.source_cell.raw_value == float("inf")
    assert "NON_FINITE_METRIC_VALUE" in _codes(candidate)


@pytest.mark.parametrize(
    ("cell_type", "biff_type_code", "raw_value", "expected_status", "code"),
    [
        ("text", 1, "", "SOURCE_MISSING", "EMPTY_TEXT_AS_MISSING"),
        ("boolean", 4, True, "REJECTED", "INVALID_METRIC_CELL_TYPE"),
        ("date_serial", 3, 46_000.0, "REJECTED", "INVALID_METRIC_CELL_TYPE"),
    ],
)
def test_empty_text_boolean_and_date_metric_sources_remain_distinguishable(
    tmp_path: Path,
    cell_type: CellKind,
    biff_type_code: int,
    raw_value: JsonScalar,
    expected_status: str,
    code: str,
) -> None:
    envelope = parse_biff_xls(write_biff_fixture(tmp_path))
    modified = _replace_cell(
        envelope,
        role=ANALYTICAL_SHORTLIST_ROLE,
        occurrence_index=2,
        header="1yr",
        raw_value=raw_value,
        cell_type=cell_type,
        biff_type_code=biff_type_code,
    )

    candidate = _normalize(modified)
    field = _field(candidate.sheet(ANALYTICAL_SHORTLIST_ROLE).rows[1].fields, "1yr")

    assert field.status == expected_status
    assert field.source_cell.cell_type == cell_type
    assert field.source_cell.raw_value == raw_value
    assert code in _codes(candidate)


def test_source_hash_mismatch_and_invalid_envelope_provenance_fail_clearly(
    tmp_path: Path,
) -> None:
    workbook = write_biff_fixture(tmp_path)
    envelope = parse_biff_xls(workbook)

    with pytest.raises(BiffXlsNormalizationError) as mismatch:
        normalize_biff_xls_envelope(
            envelope,
            expected_workbook_sha256="0" * 64,
        )
    assert mismatch.value.code == "WORKBOOK_SHA256_MISMATCH"

    model = envelope.sheet(MODEL_PORTFOLIO_ROLE)
    damaged_row = replace(model.rows[0], source_reference="fabricated")
    damaged_model = replace(model, rows=(damaged_row, *model.rows[1:]))
    damaged_envelope = replace(
        envelope,
        sheets=tuple(
            damaged_model if sheet.role == MODEL_PORTFOLIO_ROLE else sheet
            for sheet in envelope.sheets
        ),
    )
    with pytest.raises(BiffXlsNormalizationError) as provenance:
        _normalize(damaged_envelope)
    assert provenance.value.code == "INVALID_SOURCE_ROW_PROVENANCE"

    with pytest.raises(BiffXlsNormalizationError) as date_mismatch:
        _normalize(replace(envelope, snapshot_date="2026-01-16"))
    assert date_mismatch.value.code == "SOURCE_SNAPSHOT_DATE_MISMATCH"


def test_recomputed_fingerprints_do_not_hide_malformed_typed_provenance(
    tmp_path: Path,
) -> None:
    envelope = parse_biff_xls(write_biff_fixture(tmp_path))
    model = envelope.sheet(MODEL_PORTFOLIO_ROLE)

    headers = list(model.headers)
    headers[0] = replace(headers[0], source_row=2, coordinate="A2")
    bad_header = _replace_sheet(
        envelope,
        _refingerprint_sheet(replace(model, headers=tuple(headers))),
    )
    with pytest.raises(BiffXlsNormalizationError) as header_error:
        _normalize(bad_header)
    assert header_error.value.code == "INVALID_SOURCE_HEADER_PROVENANCE"

    reversed_rows = tuple(
        _refingerprint_row(replace(row, occurrence_index=index))
        for index, row in enumerate(reversed(model.rows), start=1)
    )
    bad_order = _replace_sheet(
        envelope,
        _refingerprint_sheet(replace(model, rows=reversed_rows)),
    )
    with pytest.raises(BiffXlsNormalizationError) as order_error:
        _normalize(bad_order)
    assert order_error.value.code == "INVALID_SOURCE_ROW_ORDER"

    workbook_sheets = list(envelope.workbook_sheets)
    inventory_item = dict(workbook_sheets[model.sheet_index])
    inventory_item["exact_name"] = "fabricated"
    workbook_sheets[model.sheet_index] = inventory_item
    with pytest.raises(BiffXlsNormalizationError) as inventory_error:
        _normalize(replace(envelope, workbook_sheets=tuple(workbook_sheets)))
    assert inventory_error.value.code == "INVALID_WORKBOOK_SHEET_INVENTORY"

    bad_number = _replace_cell(
        envelope,
        role=ANALYTICAL_SHORTLIST_ROLE,
        occurrence_index=2,
        header="1yr",
        raw_value=True,
        cell_type="number",
        biff_type_code=2,
    )
    with pytest.raises(BiffXlsNormalizationError) as cell_error:
        _normalize(bad_number)
    assert cell_error.value.code == "INVALID_SOURCE_CELL_VALUE"


def test_unresolved_policy_diagnostics_are_stable_and_occurrence_specific(
    tmp_path: Path,
) -> None:
    candidate = _normalize(parse_biff_xls(write_biff_fixture(tmp_path)))
    diagnostics = candidate.diagnostics

    assert [item.code for item in diagnostics[:2]] == [
        "RECOVERY_MODE_ADMISSION_REVIEW_REQUIRED",
        "FORMULA_ORIGIN_NOT_ESTABLISHED",
    ]
    assert all(item.source_reference is not None for item in diagnostics[:2])
    horizon = [
        item
        for item in diagnostics
        if item.code == "UNRESOLVED_METRIC_HORIZON_SEMANTICS"
    ]
    assert horizon
    assert all(
        item.header in {"3yr", "5yr"}
        and item.source_reference is not None
        and item.coordinate is not None
        for item in horizon
    )
    currency = [
        item
        for item in diagnostics
        if item.code == "UNAPPROVED_CURRENCY_RISK_TRANSLATION"
    ]
    assert currency
    assert all(item.header == "Devizakockázat" for item in currency)
    assert "ANOMALOUS_CURRENCY_RISK_VALUE" in _codes(candidate)
    assert "ANOMALOUS_SUSTAINABILITY_VALUE" in _codes(candidate)


def _normalize(envelope: BiffXlsEnvelope) -> BiffXlsNormalizationCandidate:
    return normalize_biff_xls_envelope(
        envelope,
        expected_workbook_sha256=envelope.workbook_sha256,
    )


def _field(
    fields: tuple[NormalizedFieldCandidate, ...], header: str
) -> NormalizedFieldCandidate:
    return next(field for field in fields if field.header == header)


def _codes(candidate: BiffXlsNormalizationCandidate) -> list[str]:
    return [item.code for item in candidate.diagnostics]


def _cell_content(field: NormalizedFieldCandidate) -> tuple[object, ...]:
    cell = field.source_cell
    return (
        cell.cell_type,
        cell.biff_type_code,
        cell.raw_value,
        cell.xf_index,
        cell.format_key,
        cell.number_format,
        cell.error_text,
    )


def _replace_cell_raw_value(
    envelope: BiffXlsEnvelope,
    *,
    role: str,
    occurrence_index: int,
    header: str,
    raw_value: float,
) -> BiffXlsEnvelope:
    return _replace_cell(
        envelope,
        role=role,
        occurrence_index=occurrence_index,
        header=header,
        raw_value=raw_value,
    )


def _replace_cell(
    envelope: BiffXlsEnvelope,
    *,
    role: str,
    occurrence_index: int,
    header: str,
    raw_value: JsonScalar,
    cell_type: CellKind | None = None,
    biff_type_code: int | None = None,
) -> BiffXlsEnvelope:
    sheets: list[ParsedSheet] = []
    for sheet in envelope.sheets:
        if sheet.role != role:
            sheets.append(sheet)
            continue
        headers = tuple(cell.raw_value for cell in sheet.headers)
        column = headers.index(header)
        rows: list[SourceRow] = []
        for row in sheet.rows:
            if row.occurrence_index != occurrence_index:
                rows.append(row)
                continue
            cells = list(row.cells)
            original_cell = cells[column]
            cells[column] = replace(
                original_cell,
                raw_value=raw_value,
                cell_type=(
                    cell_type if cell_type is not None else original_cell.cell_type
                ),
                biff_type_code=(
                    biff_type_code
                    if biff_type_code is not None
                    else original_cell.biff_type_code
                ),
            )
            row_payload = {
                "cells": [cell.to_dict() for cell in cells],
                "occurrence_index": row.occurrence_index,
                "source_reference": row.source_reference,
                "source_row": row.source_row,
            }
            rows.append(
                replace(
                    row,
                    cells=tuple(cells),
                    row_fingerprint=canonical_fingerprint(row_payload),
                )
            )
        sheet_payload = {
            "exact_name": sheet.exact_name,
            "header_row": sheet.header_row,
            "header_start_column": sheet.header_start_column,
            "headers": [cell.to_dict() for cell in sheet.headers],
            "merged_ranges": list(sheet.merged_ranges),
            "role": sheet.role,
            "rows": [row.to_dict() for row in rows],
            "sheet_index": sheet.sheet_index,
            "visibility": sheet.visibility,
            "visibility_code": sheet.visibility_code,
        }
        sheets.append(
            replace(
                sheet,
                rows=tuple(rows),
                sheet_fingerprint=canonical_fingerprint(sheet_payload),
            )
        )
    return replace(envelope, sheets=tuple(sheets))


def _refingerprint_row(row: SourceRow) -> SourceRow:
    payload = {
        "cells": [cell.to_dict() for cell in row.cells],
        "occurrence_index": row.occurrence_index,
        "source_reference": row.source_reference,
        "source_row": row.source_row,
    }
    return replace(row, row_fingerprint=canonical_fingerprint(payload))


def _refingerprint_sheet(sheet: ParsedSheet) -> ParsedSheet:
    payload = {
        "exact_name": sheet.exact_name,
        "header_row": sheet.header_row,
        "header_start_column": sheet.header_start_column,
        "headers": [cell.to_dict() for cell in sheet.headers],
        "merged_ranges": list(sheet.merged_ranges),
        "role": sheet.role,
        "rows": [row.to_dict() for row in sheet.rows],
        "sheet_index": sheet.sheet_index,
        "visibility": sheet.visibility,
        "visibility_code": sheet.visibility_code,
    }
    return replace(sheet, sheet_fingerprint=canonical_fingerprint(payload))


def _replace_sheet(
    envelope: BiffXlsEnvelope, replacement: ParsedSheet
) -> BiffXlsEnvelope:
    return replace(
        envelope,
        sheets=tuple(
            replacement if sheet.role == replacement.role else sheet
            for sheet in envelope.sheets
        ),
    )
