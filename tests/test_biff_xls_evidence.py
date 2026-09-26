"""Synthetic tests for the read-only BIFF recovery/formula verifier."""

from __future__ import annotations

import hashlib
import io
import json
import struct
from dataclasses import replace
from pathlib import Path

import pytest
import xlrd  # type: ignore[import-untyped]
from xlrd.compdoc import CompDocError  # type: ignore[import-untyped]

from portfolio_advisor.workbook_source import biff_evidence as evidence_module
from portfolio_advisor.workbook_source.biff_evidence import (
    FREE_SECTOR,
    BiffEvidenceError,
    WorkbookExpectation,
    inspect_biff_stream,
    inspect_compound_file,
    load_inventory,
    verify_workbook,
)
from portfolio_advisor.workbook_source.biff_xls import (
    MODEL_HEADERS,
    BiffXlsEnvelope,
    parse_biff_xls,
)
from scripts import verify_biff_xls_evidence as verifier_cli
from tests.fixtures.biff_xls_fixture import (
    write_biff_fixture,
    write_root_ministream_overlap_fixture,
)

INVENTORY_PATH = (
    Path(__file__).parents[1]
    / "data/knowledge/biff_xls_processed_expected_inventory_v1.json"
)


def test_valid_workbook_verification_is_deterministic_and_typed(
    tmp_path: Path,
) -> None:
    workbook = write_biff_fixture(tmp_path)
    expectation = _expectation(workbook)

    first = verify_workbook(path=workbook, expectation=expectation)
    second = verify_workbook(path=workbook, expectation=expectation)

    assert first == second
    assert first["strict_open"] is True
    assert first["formula_record_count"] == 0
    assert first["data_cell_types"] == {
        "blank": 7,
        "empty": 1,
        "error": 1,
        "number": 43,
        "text": 30,
    }
    assert first["comparison"] == {
        "calamine_collapsed_missing": 8,
        "calamine_exact_values": 115,
        "cell_property_mismatches": 0,
        "compared_cells_including_headers": 123,
        "raw_cell_properties_exact": 123,
        "value_mismatches": 0,
    }


def test_root_ministream_workbook_allocation_overlap_is_detected(
    tmp_path: Path,
) -> None:
    workbook = write_root_ministream_overlap_fixture(tmp_path)
    payload = workbook.read_bytes()

    compound = inspect_compound_file(payload, filename=workbook.name)

    assert compound.overlap_sectors == compound.workbook_chain
    assert compound.root_entry.size == compound.sector_size
    with pytest.raises(CompDocError, match="Workbook corruption: seen"):
        xlrd.open_workbook(
            file_contents=payload,
            formatting_info=True,
            ignore_workbook_corruption=False,
            logfile=io.StringIO(),
        )


def test_invalid_fat_sector_marker_fails_closed(tmp_path: Path) -> None:
    workbook = write_biff_fixture(tmp_path)
    payload = bytearray(workbook.read_bytes())
    sector_size = 1 << struct.unpack_from("<H", payload, 30)[0]
    fat_sector = struct.unpack_from("<I", payload, 76)[0]
    fat_offset = (fat_sector + 1) * sector_size
    struct.pack_into("<I", payload, fat_offset + fat_sector * 4, FREE_SECTOR)

    with pytest.raises(BiffEvidenceError, match="INVALID_FAT_MARKER"):
        inspect_compound_file(bytes(payload), filename=workbook.name)


def test_wrong_biff_substream_type_fails_closed(tmp_path: Path) -> None:
    workbook = write_biff_fixture(tmp_path)
    compound = inspect_compound_file(workbook.read_bytes(), filename=workbook.name)
    inspection = inspect_biff_stream(compound.workbook_stream, filename=workbook.name)
    stream = bytearray(compound.workbook_stream)
    struct.pack_into("<H", stream, inspection.sheets[0].offset + 6, 0x0020)

    with pytest.raises(BiffEvidenceError, match="UNSUPPORTED_BIFF_BOF"):
        inspect_biff_stream(bytes(stream), filename=workbook.name)


def test_changed_bytes_fail_before_inspection(tmp_path: Path) -> None:
    workbook = write_biff_fixture(tmp_path)
    expectation = _expectation(workbook)
    workbook.write_bytes(workbook.read_bytes() + b"changed")

    with pytest.raises(BiffEvidenceError, match="WORKBOOK_HASH_MISMATCH"):
        verify_workbook(path=workbook, expectation=expectation)


def test_calamine_crosscheck_reads_the_exact_hashed_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workbook = write_biff_fixture(tmp_path)
    expected_bytes = workbook.read_bytes()
    original_load_workbook = evidence_module.load_workbook
    observed: list[bytes] = []

    def recording_load_workbook(source: object) -> object:
        assert isinstance(source, io.BytesIO)
        observed.append(source.getvalue())
        return original_load_workbook(source)

    monkeypatch.setattr(evidence_module, "load_workbook", recording_load_workbook)

    verify_workbook(path=workbook, expectation=_expectation(workbook))

    assert observed == [expected_bytes]


@pytest.mark.parametrize("damage", ["truncated-record", "missing-eof"])
def test_truncated_or_missing_biff_records_fail_closed(
    tmp_path: Path, damage: str
) -> None:
    workbook = write_biff_fixture(tmp_path)
    compound = inspect_compound_file(workbook.read_bytes(), filename=workbook.name)
    inspection = inspect_biff_stream(compound.workbook_stream, filename=workbook.name)
    stream = bytearray(compound.workbook_stream)
    if damage == "truncated-record":
        first_sheet = inspection.sheets[0]
        stream[first_sheet.offset + 2 : first_sheet.offset + 4] = b"\xff\xff"
        expected_code = "TRUNCATED_BIFF_RECORD"
    else:
        last_sheet = inspection.sheets[-1]
        stream[last_sheet.end_offset - 4 : last_sheet.end_offset - 2] = b"\x00\x00"
        expected_code = "MISSING_BIFF_EOF"

    with pytest.raises(BiffEvidenceError, match=expected_code):
        inspect_biff_stream(bytes(stream), filename=workbook.name)


def test_formula_records_are_independently_detected_and_rejected(
    tmp_path: Path,
) -> None:
    workbook = write_biff_fixture(tmp_path, include_formula=True)
    compound = inspect_compound_file(workbook.read_bytes(), filename=workbook.name)
    inspection = inspect_biff_stream(compound.workbook_stream, filename=workbook.name)

    formulas = [item for sheet in inspection.sheets for item in sheet.formula_records]
    assert [item["record"] for item in formulas] == ["FORMULA"]
    assert formulas[0]["coordinate"] == "J2"
    with pytest.raises(BiffEvidenceError, match="FORMULA_INVENTORY_MISMATCH"):
        verify_workbook(path=workbook, expectation=_expectation(workbook))


def test_blank_and_absent_cell_type_mismatch_fails_closed(tmp_path: Path) -> None:
    workbook = write_biff_fixture(tmp_path)

    def changed_type(path: Path) -> BiffXlsEnvelope:
        envelope = parse_biff_xls(path)
        model = envelope.sheets[0]
        row = model.rows[1]
        index = MODEL_HEADERS.index("YTD")
        cells = list(row.cells)
        cells[index] = replace(cells[index], cell_type="empty", biff_type_code=0)
        rows = list(model.rows)
        rows[1] = replace(row, cells=tuple(cells))
        sheets = list(envelope.sheets)
        sheets[0] = replace(model, rows=tuple(rows))
        return replace(envelope, sheets=tuple(sheets))

    with pytest.raises(BiffEvidenceError, match="CELL_TYPE_MISMATCH"):
        verify_workbook(
            path=workbook,
            expectation=_expectation(workbook),
            source_parser=changed_type,
        )


def test_unaccounted_explicit_blank_record_fails_closed(tmp_path: Path) -> None:
    workbook = write_biff_fixture(tmp_path, extra_model_blank=True)

    with pytest.raises(BiffEvidenceError, match="UNACCOUNTED_CELL_RECORD"):
        verify_workbook(path=workbook, expectation=_expectation(workbook))


def test_builtin_number_format_string_mismatch_fails_closed(tmp_path: Path) -> None:
    workbook = write_biff_fixture(tmp_path)

    def changed_format(path: Path) -> BiffXlsEnvelope:
        envelope = parse_biff_xls(path)
        model = envelope.sheets[0]
        row = model.rows[0]
        index = MODEL_HEADERS.index("YTD")
        cells = list(row.cells)
        cells[index] = replace(cells[index], number_format="wrong")
        rows = list(model.rows)
        rows[0] = replace(row, cells=tuple(cells))
        sheets = list(envelope.sheets)
        sheets[0] = replace(model, rows=tuple(rows))
        return replace(envelope, sheets=tuple(sheets))

    with pytest.raises(BiffEvidenceError, match="NUMBER_FORMAT_MISMATCH"):
        verify_workbook(
            path=workbook,
            expectation=_expectation(workbook),
            source_parser=changed_format,
        )


def test_independent_value_comparison_failure_is_nonzero_evidence(
    tmp_path: Path,
) -> None:
    workbook = write_biff_fixture(tmp_path)

    def changed_value(path: Path) -> BiffXlsEnvelope:
        envelope = parse_biff_xls(path)
        model = envelope.sheets[0]
        row = model.rows[0]
        cells = list(row.cells)
        cells[0] = replace(cells[0], raw_value="Changed portfolio")
        rows = list(model.rows)
        rows[0] = replace(row, cells=tuple(cells))
        sheets = list(envelope.sheets)
        sheets[0] = replace(model, rows=tuple(rows))
        return replace(envelope, sheets=tuple(sheets))

    with pytest.raises(BiffEvidenceError, match="VALUE_MISMATCH"):
        verify_workbook(
            path=workbook,
            expectation=_expectation(workbook),
            source_parser=changed_value,
        )


def test_inventory_rejects_type_coercion(tmp_path: Path) -> None:
    payload = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    payload["workbooks"][0]["strict_error"] = 123
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BiffEvidenceError, match="INVALID_INVENTORY"):
        load_inventory(inventory)


def test_report_creation_does_not_overwrite_a_racing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "evidence.json"

    def racing_verification(**_kwargs: object) -> dict[str, object]:
        output.write_text("concurrent output\n", encoding="utf-8")
        return {"report_fingerprint": "unused"}

    monkeypatch.setattr(verifier_cli, "verify_corpus", racing_verification)

    assert verifier_cli.main(["--output", str(output)]) == 2
    assert output.read_text(encoding="utf-8") == "concurrent output\n"
    assert "OUTPUT_ALREADY_EXISTS" in capsys.readouterr().err


def test_recovery_parser_suppresses_incidental_xlrd_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workbook = write_root_ministream_overlap_fixture(tmp_path)

    envelope = parse_biff_xls(workbook)

    assert [sheet.exact_name for sheet in envelope.sheets] == [
        " modell portfóliók",
        " shortlist",
    ]
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def _expectation(path: Path) -> WorkbookExpectation:
    return WorkbookExpectation(
        filename=path.name,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        strict_open=True,
        strict_error=None,
        overlap_sector_count=0,
        model_rows=2,
        shortlist_rows=2,
        data_fields=82,
        formula_record_count=0,
    )
