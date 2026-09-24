"""Synthetic validation for the source-only BIFF-XLS envelope."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from portfolio_advisor.canonical import canonical_json
from portfolio_advisor.workbook_source.biff_xls import (
    ADMISSION_STATUS,
    ANALYTICAL_SHORTLIST_ROLE,
    CONTRACT_NAME,
    CONTRACT_VERSION,
    MODEL_HEADERS,
    MODEL_PORTFOLIO_ROLE,
    SHORTLIST_HEADERS,
    BiffXlsParseError,
    SourceCell,
    SourceRow,
    parse_biff_xls,
)
from tests.fixtures.biff_xls_fixture import write_biff_fixture


def test_dual_sheet_envelope_preserves_typed_source_and_occurrences(
    tmp_path: Path,
) -> None:
    workbook = write_biff_fixture(tmp_path)

    envelope = parse_biff_xls(workbook)

    payload = envelope.to_dict()
    assert payload["contract"] == {"name": CONTRACT_NAME, "version": CONTRACT_VERSION}
    assert payload["admission_status"] == ADMISSION_STATUS
    assert envelope.source_filename == workbook.name
    assert envelope.snapshot_date == "2026-01-15"
    assert envelope.workbook_sha256 == hashlib.sha256(workbook.read_bytes()).hexdigest()
    assert envelope.workbook_metadata == {
        "biff_version": 80,
        "codepage": 1200,
        "datemode": 0,
        "encoding": "utf_16_le",
    }
    assert [
        (sheet.role, sheet.exact_name, sheet.sheet_index, sheet.visibility)
        for sheet in envelope.sheets
    ] == [
        (MODEL_PORTFOLIO_ROLE, " modell portfóliók", 0, "visible"),
        (ANALYTICAL_SHORTLIST_ROLE, " shortlist", 1, "visible"),
    ]

    model = envelope.sheet(MODEL_PORTFOLIO_ROLE)
    assert tuple(cell.raw_value for cell in model.headers) == MODEL_HEADERS
    assert [row.source_row for row in model.rows] == [2, 3]
    assert len({row.source_reference for row in model.rows}) == 2
    assert len({row.row_fingerprint for row in model.rows}) == 2
    assert _cell(model.rows[0], MODEL_HEADERS, "Eszközosztály").raw_value == "Kötvény"
    assert _cell(model.rows[0], MODEL_HEADERS, "Devizakockázat").raw_value == 2.0
    numeric_zero = _cell(model.rows[0], MODEL_HEADERS, "YTD")
    assert (
        numeric_zero.cell_type,
        numeric_zero.raw_value,
        numeric_zero.number_format,
    ) == (
        "number",
        0.0,
        "0.00%",
    )
    blank = _cell(model.rows[1], MODEL_HEADERS, "YTD")
    assert (blank.cell_type, blank.raw_value) == ("blank", None)
    empty = _cell(model.rows[1], MODEL_HEADERS, "5yr")
    assert (empty.cell_type, empty.raw_value) == ("empty", None)

    shortlist = envelope.sheet(ANALYTICAL_SHORTLIST_ROLE)
    assert tuple(cell.raw_value for cell in shortlist.headers) == SHORTLIST_HEADERS
    text_zero = _cell(shortlist.rows[0], SHORTLIST_HEADERS, "YTD")
    assert (text_zero.cell_type, text_zero.raw_value, text_zero.number_format) == (
        "text",
        "0",
        "@",
    )
    shortlist_numeric_zero = _cell(shortlist.rows[1], SHORTLIST_HEADERS, "YTD")
    assert (
        shortlist_numeric_zero.cell_type,
        shortlist_numeric_zero.raw_value,
        shortlist_numeric_zero.number_format,
    ) == ("number", 0.0, "0.000%")
    error = _cell(shortlist.rows[1], SHORTLIST_HEADERS, "Info. ratio")
    assert (error.cell_type, error.raw_value, error.error_text) == (
        "error",
        15,
        "#VALUE!",
    )
    assert [item.code for item in envelope.diagnostics] == [
        "ANOMALOUS_CURRENCY_RISK_VALUE",
        "ANOMALOUS_CURRENCY_RISK_VALUE",
        "ANOMALOUS_SUSTAINABILITY_VALUE",
        "TEXT_ZERO_METRIC_VALUE",
    ]


def test_serialization_and_fingerprints_are_deterministic(tmp_path: Path) -> None:
    workbook = write_biff_fixture(tmp_path)

    first = parse_biff_xls(workbook)
    second = parse_biff_xls(workbook)

    assert first.to_json() == second.to_json()
    assert first.envelope_fingerprint == second.envelope_fingerprint
    assert [sheet.sheet_fingerprint for sheet in first.sheets] == [
        sheet.sheet_fingerprint for sheet in second.sheets
    ]
    assert (
        first.envelope_fingerprint
        == hashlib.sha256(canonical_json(first._payload()).encode("utf-8")).hexdigest()
    )


def test_duplicate_rows_remain_distinct_ordered_occurrences(tmp_path: Path) -> None:
    workbook = write_biff_fixture(tmp_path)
    model = parse_biff_xls(workbook).sheet(MODEL_PORTFOLIO_ROLE)

    first_values = [cell.raw_value for cell in model.rows[0].cells]
    second_values = [cell.raw_value for cell in model.rows[1].cells]
    second_values[MODEL_HEADERS.index("YTD")] = 0.0
    assert first_values == second_values
    assert [row.occurrence_index for row in model.rows] == [1, 2]
    assert [row.source_row for row in model.rows] == [2, 3]


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"include_model": False}, "MISSING_REQUIRED_SHEET"),
        ({"shortlist_sheet_name": "shortlist"}, "UNSUPPORTED_SHEET_NAME_VARIANT"),
        ({"duplicate_shortlist_variant": True}, "AMBIGUOUS_REQUIRED_SHEET"),
        ({"hide_shortlist": True}, "REQUIRED_SHEET_NOT_VISIBLE"),
        ({"malformed_model_header": True}, "MISSING_OR_INVALID_HEADER"),
        ({"repeat_model_header": True}, "AMBIGUOUS_HEADER"),
        ({"extra_model_data": True}, "UNSUPPORTED_DATA_OUTSIDE_HEADER"),
        ({"shortlist_sheet_name": "terméklista"}, "MISSING_REQUIRED_SHEET"),
    ],
)
def test_structural_failures_have_explicit_codes(
    tmp_path: Path, kwargs: dict[str, Any], code: str
) -> None:
    workbook = write_biff_fixture(tmp_path, **kwargs)

    with pytest.raises(BiffXlsParseError) as caught:
        parse_biff_xls(workbook)

    assert caught.value.code == code
    assert caught.value.to_dict()["code"] == code


@pytest.mark.parametrize(
    ("filename", "code"),
    [
        ("Synthetic.xls", "INVALID_SNAPSHOT_FILENAME"),
        ("Synthetic_20260230.xls", "INVALID_SNAPSHOT_DATE"),
        ("Synthetic_20260115.xlsx", "INVALID_SNAPSHOT_FILENAME"),
    ],
)
def test_snapshot_filename_is_strict(tmp_path: Path, filename: str, code: str) -> None:
    workbook = write_biff_fixture(tmp_path, filename=filename)

    with pytest.raises(BiffXlsParseError) as caught:
        parse_biff_xls(workbook)

    assert caught.value.code == code


def test_malformed_bytes_fail_with_explicit_diagnostic(tmp_path: Path) -> None:
    workbook = tmp_path / "Malformed_20260115.xls"
    workbook.write_bytes(b"not a BIFF compound document")

    with pytest.raises(BiffXlsParseError) as caught:
        parse_biff_xls(workbook)

    assert caught.value.code == "MALFORMED_OR_UNSUPPORTED_BIFF_XLS"


@pytest.mark.parametrize("retained_bytes", [64, 512])
def test_truncated_biff_failures_keep_the_stable_diagnostic_boundary(
    tmp_path: Path, retained_bytes: int
) -> None:
    workbook = write_biff_fixture(tmp_path)
    source_bytes = workbook.read_bytes()
    workbook.write_bytes(source_bytes[:retained_bytes])

    with pytest.raises(BiffXlsParseError) as caught:
        parse_biff_xls(workbook)

    assert caught.value.code == "MALFORMED_OR_UNSUPPORTED_BIFF_XLS"


def test_formula_limit_is_explicit_and_formula_is_never_evaluated(
    tmp_path: Path,
) -> None:
    workbook = write_biff_fixture(tmp_path, include_formula=True)

    envelope = parse_biff_xls(workbook)
    limits = envelope.to_dict()["extraction_limits"]
    assert isinstance(limits, dict)
    assert limits["formula_evaluation"] == "PROHIBITED"
    assert limits["formula_text"] == "UNAVAILABLE_FROM_XLRD"
    formula_cell = _cell(
        envelope.sheet(ANALYTICAL_SHORTLIST_ROLE).rows[0],
        SHORTLIST_HEADERS,
        "1yr",
    )
    assert formula_cell.raw_value != 2.0


def _cell(row: SourceRow, headers: tuple[str, ...], header: str) -> SourceCell:
    return row.cells[headers.index(header)]
