"""Read-only, hash-bound evidence verification for retained BIFF-XLS files.

The independent inspector in this module parses the CFBF allocation graph and
the relevant BIFF records directly from bytes.  It does not use the published
source envelope to discover sheets, cells, allocation defects, or formulas.
The published parser and Rust-backed Calamine reader are invoked only as
explicit cross-checks after the independent inspection succeeds.

Nothing in this module admits a workbook, grants a recovery exception, or
connects to a database.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.metadata
import io
import json
import math
import struct
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypedDict, cast

import xlrd  # type: ignore[import-untyped]
from python_calamine import load_workbook  # type: ignore[import-untyped]
from xlrd.compdoc import CompDocError  # type: ignore[import-untyped]

from portfolio_advisor.canonical import canonical_fingerprint
from portfolio_advisor.workbook_source.biff_xls import (
    ANALYTICAL_SHORTLIST_ROLE,
    MODEL_HEADERS,
    MODEL_PORTFOLIO_ROLE,
    MODEL_SHEET_NAME,
    PARSER_NAME,
    PARSER_VERSION,
    SHORTLIST_HEADERS,
    SHORTLIST_SHEET_NAME,
    BiffXlsEnvelope,
    SourceCell,
    parse_biff_xls,
)

REPORT_CONTRACT_NAME: Final = "BIFF_XLS_RECOVERY_FORMULA_EVIDENCE_REPORT"
REPORT_CONTRACT_VERSION: Final = 1
VERIFIER_NAME: Final = "portfolio_advisor.workbook_source.biff_evidence"
VERIFIER_VERSION: Final = 1
REPORT_STATUS: Final = "READ_ONLY_EVIDENCE_NOT_ADMISSION_APPROVAL"
ADMISSION_APPROVAL: Final = "NOT_GRANTED"
RECOVERY_EXCEPTION_APPROVAL: Final = "NOT_GRANTED"
FORMULA_ORIGIN_APPROVAL: Final = "NOT_GRANTED"

FREE_SECTOR: Final = 0xFFFFFFFF
END_OF_CHAIN: Final = 0xFFFFFFFE
FAT_SECTOR: Final = 0xFFFFFFFD
DIFAT_SECTOR: Final = 0xFFFFFFFC
_SPECIAL_SECTORS: Final = frozenset(
    {FREE_SECTOR, END_OF_CHAIN, FAT_SECTOR, DIFAT_SECTOR}
)

_FORMULA_RECORDS: Final = {
    0x0006: "FORMULA",
    0x0206: "FORMULA3",
    0x0406: "FORMULA4",
    0x0221: "ARRAY",
    0x0021: "ARRAY2",
    0x04BC: "SHRFMLA",
    0x0236: "TABLEOP",
    0x0037: "TABLEOP2",
    0x0036: "TABLEOP_B2",
    0x0207: "STRING",
}
_SINGLE_CELL_RECORDS: Final = {
    0x0201: "blank",
    0x0203: "number",
    0x00FD: "text",
    0x0204: "text",
    0x00D6: "text",
    0x0205: "boolerr",
    0x027E: "number",
    0x0006: "formula",
    0x0206: "formula",
    0x0406: "formula",
}
_EXPECTED_SHEETS: Final = (
    (MODEL_PORTFOLIO_ROLE, MODEL_SHEET_NAME, MODEL_HEADERS),
    (ANALYTICAL_SHORTLIST_ROLE, SHORTLIST_SHEET_NAME, SHORTLIST_HEADERS),
)
_MAX_EXAMPLES: Final = 12
_INVENTORY_KEYS: Final = frozenset({"contract", "expected_aggregate", "workbooks"})
_WORKBOOK_EXPECTATION_KEYS: Final = frozenset(
    {
        "data_fields",
        "filename",
        "formula_record_count",
        "model_rows",
        "overlap_sector_count",
        "sha256",
        "shortlist_rows",
        "strict_error",
        "strict_open",
    }
)
_AGGREGATE_KEYS: Final = frozenset(
    {
        "data_fields",
        "formula_records",
        "model_rows",
        "recovery_required",
        "shortlist_rows",
        "strict_open",
        "workbooks",
    }
)
_STANDARD_NUMBER_FORMATS: Final = {
    0x00: "General",
    0x01: "0",
    0x02: "0.00",
    0x03: "#,##0",
    0x04: "#,##0.00",
    0x05: "$#,##0_);($#,##0)",
    0x06: "$#,##0_);[Red]($#,##0)",
    0x07: "$#,##0.00_);($#,##0.00)",
    0x08: "$#,##0.00_);[Red]($#,##0.00)",
    0x09: "0%",
    0x0A: "0.00%",
    0x0B: "0.00E+00",
    0x0C: "# ?/?",
    0x0D: "# ??/??",
    0x0E: "m/d/yy",
    0x0F: "d-mmm-yy",
    0x10: "d-mmm",
    0x11: "mmm-yy",
    0x12: "h:mm AM/PM",
    0x13: "h:mm:ss AM/PM",
    0x14: "h:mm",
    0x15: "h:mm:ss",
    0x16: "m/d/yy h:mm",
    0x25: "#,##0_);(#,##0)",
    0x26: "#,##0_);[Red](#,##0)",
    0x27: "#,##0.00_);(#,##0.00)",
    0x28: "#,##0.00_);[Red](#,##0.00)",
    0x29: '_(* #,##0_);_(* (#,##0);_(* "-"_);_(@_)',
    0x2A: '_($* #,##0_);_($* (#,##0);_($* "-"_);_(@_)',
    0x2B: '_(* #,##0.00_);_(* (#,##0.00);_(* "-"??_);_(@_)',
    0x2C: '_($* #,##0.00_);_($* (#,##0.00);_($* "-"??_);_(@_)',
    0x2D: "mm:ss",
    0x2E: "[h]:mm:ss",
    0x2F: "mm:ss.0",
    0x30: "##0.0E+0",
    0x31: "@",
}

type JsonScalar = str | int | float | bool | None
type SourceParser = Callable[[Path], BiffXlsEnvelope]


class _BoundSheetRecord(TypedDict):
    offset: int
    visibility: int
    sheet_type: int
    name: str


class BiffEvidenceError(ValueError):
    """The hash-bound evidence contract could not be verified."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        filename: str | None = None,
        sheet: str | None = None,
        coordinate: str | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.filename = filename
        self.sheet = sheet
        self.coordinate = coordinate
        location = " / ".join(
            value for value in (filename, sheet, coordinate) if value is not None
        )
        super().__init__(f"[{code}] {location + ': ' if location else ''}{message}")


@dataclass(frozen=True, slots=True)
class WorkbookExpectation:
    """One exact retained-workbook expectation from the committed inventory."""

    filename: str
    sha256: str
    strict_open: bool
    strict_error: str | None
    overlap_sector_count: int
    model_rows: int
    shortlist_rows: int
    data_fields: int
    formula_record_count: int


@dataclass(frozen=True, slots=True)
class DirectoryEntry:
    """Minimal CFBF directory evidence."""

    did: int
    name: str
    entry_type: int
    start_sector: int
    size: int


@dataclass(frozen=True, slots=True)
class RawCell:
    """One independently located BIFF cell record."""

    kind: str
    xf_index: int
    opcode: int
    record_offset: int
    raw_number: float | None = None
    bool_value: bool | None = None
    error_code: int | None = None


@dataclass(frozen=True, slots=True)
class RawSheet:
    """One complete BIFF worksheet substream."""

    name: str
    offset: int
    end_offset: int
    visibility: int
    sheet_type: int
    record_count: int
    cells: Mapping[tuple[int, int], RawCell]
    row_default_xfs: Mapping[int, int]
    column_default_xfs: tuple[tuple[int, int, int], ...]
    formula_records: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class CompoundInspection:
    """Independent compound-document allocation and Workbook bytes."""

    sector_size: int
    directory_entries: tuple[DirectoryEntry, ...]
    workbook_stream: bytes
    workbook_entry: DirectoryEntry
    root_entry: DirectoryEntry
    workbook_chain: tuple[int, ...]
    root_chain: tuple[int, ...]
    overlap_sectors: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class BiffInspection:
    """Independent BIFF workbook and worksheet evidence."""

    sheets: tuple[RawSheet, ...]
    xf_format_keys: tuple[int, ...]
    custom_formats: Mapping[int, str]
    global_end_offset: int
    trailing_padding_bytes: int
    global_formula_records: tuple[dict[str, object], ...]


def load_inventory(
    path: Path,
) -> tuple[dict[str, object], tuple[WorkbookExpectation, ...]]:
    """Load and strictly validate the committed expected inventory."""
    source = Path(path)
    try:
        raw = source.read_bytes()
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise BiffEvidenceError("INVALID_INVENTORY", str(error)) from error
    if not isinstance(payload, dict):
        raise BiffEvidenceError("INVALID_INVENTORY", "root must be an object")
    if set(payload) != _INVENTORY_KEYS:
        raise BiffEvidenceError(
            "INVALID_INVENTORY",
            f"unexpected root keys: {sorted(set(payload) ^ _INVENTORY_KEYS)!r}",
        )
    if payload.get("contract") != {
        "name": "BIFF_XLS_PROCESSED_EXPECTED_INVENTORY",
        "version": 1,
    }:
        raise BiffEvidenceError("INVALID_INVENTORY", "unexpected contract identity")
    rows = payload.get("workbooks")
    if not isinstance(rows, list) or len(rows) != 33:
        raise BiffEvidenceError(
            "INVALID_INVENTORY", "inventory must contain exactly 33 workbooks"
        )
    expectations: list[WorkbookExpectation] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise BiffEvidenceError(
                "INVALID_INVENTORY", f"workbooks[{index}] must be an object"
            )
        if set(row) != _WORKBOOK_EXPECTATION_KEYS:
            raise BiffEvidenceError(
                "INVALID_INVENTORY",
                f"unexpected workbooks[{index}] keys: "
                f"{sorted(set(row) ^ _WORKBOOK_EXPECTATION_KEYS)!r}",
            )
        filename = row["filename"]
        sha256 = row["sha256"]
        strict_open = row["strict_open"]
        strict_error = row["strict_error"]
        integer_names = (
            "overlap_sector_count",
            "model_rows",
            "shortlist_rows",
            "data_fields",
            "formula_record_count",
        )
        if (
            not isinstance(filename, str)
            or not isinstance(sha256, str)
            or type(strict_open) is not bool
            or (strict_error is not None and not isinstance(strict_error, str))
            or any(
                type(row[name]) is not int or row[name] < 0 for name in integer_names
            )
        ):
            raise BiffEvidenceError(
                "INVALID_INVENTORY",
                f"workbooks[{index}] contains a value with the wrong type or range",
            )
        expectation = WorkbookExpectation(
            filename=filename,
            sha256=sha256,
            strict_open=strict_open,
            strict_error=strict_error,
            overlap_sector_count=row["overlap_sector_count"],
            model_rows=row["model_rows"],
            shortlist_rows=row["shortlist_rows"],
            data_fields=row["data_fields"],
            formula_record_count=row["formula_record_count"],
        )
        if len(expectation.sha256) != 64 or any(
            character not in "0123456789abcdef" for character in expectation.sha256
        ):
            raise BiffEvidenceError(
                "INVALID_INVENTORY", f"invalid SHA-256 for {expectation.filename}"
            )
        if expectation.strict_open != (expectation.strict_error is None):
            raise BiffEvidenceError(
                "INVALID_INVENTORY",
                f"inconsistent strict-open result for {expectation.filename}",
            )
        expectations.append(expectation)
    filenames = [item.filename for item in expectations]
    hashes = [item.sha256 for item in expectations]
    if filenames != sorted(filenames) or len(set(filenames)) != len(filenames):
        raise BiffEvidenceError(
            "INVALID_INVENTORY", "filenames must be unique and sorted"
        )
    if len(set(hashes)) != len(hashes):
        raise BiffEvidenceError("INVALID_INVENTORY", "hashes must be unique")
    aggregate = payload.get("expected_aggregate")
    if (
        not isinstance(aggregate, dict)
        or set(aggregate) != _AGGREGATE_KEYS
        or any(
            type(aggregate.get(name)) is not int or aggregate[name] < 0
            for name in _AGGREGATE_KEYS
        )
    ):
        raise BiffEvidenceError(
            "INVALID_INVENTORY", "expected_aggregate has invalid keys or values"
        )
    expected_aggregate = {
        "workbooks": len(expectations),
        "strict_open": sum(item.strict_open for item in expectations),
        "recovery_required": sum(not item.strict_open for item in expectations),
        "model_rows": sum(item.model_rows for item in expectations),
        "shortlist_rows": sum(item.shortlist_rows for item in expectations),
        "data_fields": sum(item.data_fields for item in expectations),
        "formula_records": sum(item.formula_record_count for item in expectations),
    }
    if aggregate != expected_aggregate:
        raise BiffEvidenceError(
            "INVALID_INVENTORY",
            f"aggregate does not reconcile: {aggregate!r} != {expected_aggregate!r}",
        )
    metadata = {
        "inventory_sha256": hashlib.sha256(raw).hexdigest(),
        "contract": payload["contract"],
        "expected_aggregate": expected_aggregate,
    }
    return metadata, tuple(expectations)


def verify_corpus(
    *,
    source_dir: Path,
    inventory_path: Path,
    source_parser: SourceParser = parse_biff_xls,
) -> dict[str, object]:
    """Verify the exact expected corpus and return a deterministic report."""
    directory = Path(source_dir)
    if directory.is_symlink() or not directory.is_dir():
        raise BiffEvidenceError(
            "INVALID_SOURCE_DIRECTORY", "source must be an ordinary directory"
        )
    inventory_meta, expectations = load_inventory(inventory_path)
    actual_paths = sorted(
        (item for item in directory.iterdir() if item.suffix.casefold() == ".xls"),
        key=lambda item: item.name,
    )
    if any(path.is_symlink() or not path.is_file() for path in actual_paths):
        raise BiffEvidenceError(
            "INVALID_SOURCE_FILE", "every .xls source must be an ordinary file"
        )
    actual_names = [path.name for path in actual_paths]
    expected_names = [item.filename for item in expectations]
    if actual_names != expected_names:
        missing = sorted(set(expected_names) - set(actual_names))
        unexpected = sorted(set(actual_names) - set(expected_names))
        raise BiffEvidenceError(
            "INVENTORY_MISMATCH",
            f"missing={missing!r}; unexpected={unexpected!r}",
        )

    workbook_reports = [
        verify_workbook(
            path=path,
            expectation=expectation,
            source_parser=source_parser,
        )
        for path, expectation in zip(actual_paths, expectations, strict=True)
    ]
    aggregate_types: Counter[str] = Counter()
    for item in workbook_reports:
        aggregate_types.update(cast(dict[str, int], item["data_cell_types"]))
    aggregate = {
        "workbooks": len(workbook_reports),
        "strict_open": sum(bool(item["strict_open"]) for item in workbook_reports),
        "recovery_required": sum(
            not bool(item["strict_open"]) for item in workbook_reports
        ),
        "model_rows": sum(cast(int, item["model_rows"]) for item in workbook_reports),
        "shortlist_rows": sum(
            cast(int, item["shortlist_rows"]) for item in workbook_reports
        ),
        "data_fields": sum(cast(int, item["data_fields"]) for item in workbook_reports),
        "header_fields": sum(
            cast(int, item["header_fields"]) for item in workbook_reports
        ),
        "formula_records": sum(
            cast(int, item["formula_record_count"]) for item in workbook_reports
        ),
        "value_mismatches": sum(
            int(cast(dict[str, int], item["comparison"])["value_mismatches"])
            for item in workbook_reports
        ),
        "cell_property_mismatches": sum(
            int(cast(dict[str, int], item["comparison"])["cell_property_mismatches"])
            for item in workbook_reports
        ),
        "data_cell_types": dict(sorted(aggregate_types.items())),
    }
    expected = cast(dict[str, int], inventory_meta["expected_aggregate"])
    for key in (
        "workbooks",
        "strict_open",
        "recovery_required",
        "model_rows",
        "shortlist_rows",
        "data_fields",
        "formula_records",
    ):
        if aggregate[key] != expected[key]:
            raise BiffEvidenceError(
                "AGGREGATE_MISMATCH",
                f"{key}: actual {aggregate[key]!r}, expected {expected[key]!r}",
            )
    payload: dict[str, object] = {
        "admission_approval": ADMISSION_APPROVAL,
        "contract": {
            "name": REPORT_CONTRACT_NAME,
            "version": REPORT_CONTRACT_VERSION,
        },
        "formula_origin_approval": FORMULA_ORIGIN_APPROVAL,
        "formula_scope": {
            "proves": (
                "The exact retained Workbook streams contain the reported BIFF "
                "formula-related record inventory in the two complete worksheet "
                "substreams. Zero means those bytes contain no worksheet formula "
                "records or formula-result STRING records."
            ),
            "does_not_prove": (
                "It does not prove pre-export authoring history, whether values were "
                "previously calculated and pasted, or any state not encoded in the "
                "exact retained bytes."
            ),
        },
        "inventory": inventory_meta,
        "recovery_exception_approval": RECOVERY_EXCEPTION_APPROVAL,
        "report_status": REPORT_STATUS,
        "tool": {
            "name": VERIFIER_NAME,
            "version": VERIFIER_VERSION,
            "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "published_parser_name": PARSER_NAME,
            "published_parser_version": PARSER_VERSION,
            "xlrd_version": xlrd.__version__,
            "python_calamine_version": importlib.metadata.version("python-calamine"),
        },
        "aggregate": aggregate,
        "discrepancies": [],
        "workbooks": workbook_reports,
    }
    return {**payload, "report_fingerprint": canonical_fingerprint(payload)}


def verify_workbook(
    *,
    path: Path,
    expectation: WorkbookExpectation,
    source_parser: SourceParser = parse_biff_xls,
) -> dict[str, object]:
    """Verify one exact workbook against independent and cross-check readers."""
    source = Path(path)
    if source.name != expectation.filename:
        raise BiffEvidenceError(
            "FILENAME_MISMATCH",
            f"expected {expectation.filename!r}",
            filename=source.name,
        )
    data = source.read_bytes()
    actual_hash = hashlib.sha256(data).hexdigest()
    if actual_hash != expectation.sha256:
        raise BiffEvidenceError(
            "WORKBOOK_HASH_MISMATCH",
            f"actual {actual_hash}, expected {expectation.sha256}",
            filename=source.name,
        )

    compound = inspect_compound_file(data, filename=source.name)
    biff = inspect_biff_stream(compound.workbook_stream, filename=source.name)
    formula_records = sum(len(sheet.formula_records) for sheet in biff.sheets) + len(
        biff.global_formula_records
    )
    formula_examples = [
        {"scope": "workbook_globals", **item}
        for item in biff.global_formula_records[:_MAX_EXAMPLES]
    ]
    for sheet in biff.sheets:
        formula_examples.extend(
            {"scope": sheet.name, **item}
            for item in sheet.formula_records[:_MAX_EXAMPLES]
        )
    if formula_records != expectation.formula_record_count:
        raise BiffEvidenceError(
            "FORMULA_INVENTORY_MISMATCH",
            f"found {formula_records}, expected {expectation.formula_record_count}: "
            f"{formula_examples[:_MAX_EXAMPLES]!r}",
            filename=source.name,
        )
    strict_open, strict_error = _strict_open(data)
    if strict_open != expectation.strict_open:
        raise BiffEvidenceError(
            "STRICT_OPEN_MISMATCH",
            f"actual {strict_open}, expected {expectation.strict_open}",
            filename=source.name,
        )
    if strict_error != expectation.strict_error:
        raise BiffEvidenceError(
            "STRICT_ERROR_MISMATCH",
            f"actual {strict_error!r}, expected {expectation.strict_error!r}",
            filename=source.name,
        )
    _verify_allocation_signature(compound, expectation, filename=source.name)

    calamine = load_workbook(io.BytesIO(data))
    with (
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        envelope = source_parser(source)
    if envelope.workbook_sha256 != actual_hash:
        raise BiffEvidenceError(
            "PARSER_HASH_MISMATCH",
            f"parser returned {envelope.workbook_sha256}",
            filename=source.name,
        )

    comparison = _compare_readers(
        filename=source.name,
        biff=biff,
        calamine=calamine,
        envelope=envelope,
    )
    model_rows = cast(int, comparison["model_rows"])
    shortlist_rows = cast(int, comparison["shortlist_rows"])
    data_fields = cast(int, comparison["data_fields"])
    actual_counts = {
        "model_rows": model_rows,
        "shortlist_rows": shortlist_rows,
        "data_fields": data_fields,
        "formula_record_count": formula_records,
    }
    expected_counts = {
        "model_rows": expectation.model_rows,
        "shortlist_rows": expectation.shortlist_rows,
        "data_fields": expectation.data_fields,
        "formula_record_count": expectation.formula_record_count,
    }
    if actual_counts != expected_counts:
        raise BiffEvidenceError(
            "WORKBOOK_COUNT_MISMATCH",
            f"actual {actual_counts!r}, expected {expected_counts!r}",
            filename=source.name,
        )
    overlap = compound.overlap_sectors
    allocation = {
        "defect": ("ROOT_MINISTREAM_WORKBOOK_CHAIN_OVERLAP" if overlap else "NONE"),
        "overlap_sector_count": len(overlap),
        "overlap_first_sector": overlap[0] if overlap else None,
        "overlap_last_sector": overlap[-1] if overlap else None,
        "overlap_sector_fingerprint": canonical_fingerprint(list(overlap)),
        "root_start_sector": compound.root_entry.start_sector,
        "root_declared_size": compound.root_entry.size,
        "workbook_start_sector": compound.workbook_entry.start_sector,
        "workbook_declared_size": compound.workbook_entry.size,
    }
    return {
        "allocation": allocation,
        "byte_length": len(data),
        "comparison": {
            "calamine_exact_values": comparison["calamine_exact_values"],
            "calamine_collapsed_missing": comparison["calamine_collapsed_missing"],
            "cell_property_mismatches": 0,
            "compared_cells_including_headers": comparison[
                "compared_cells_including_headers"
            ],
            "raw_cell_properties_exact": comparison["raw_cell_properties_exact"],
            "value_mismatches": 0,
        },
        "data_cell_types": comparison["data_cell_types"],
        "data_fields": data_fields,
        "filename": source.name,
        "formula_record_count": formula_records,
        "formula_record_examples": formula_examples[:_MAX_EXAMPLES],
        "formula_status": (
            "NO_FORMULA_RECORDS_IN_EXACT_BYTES"
            if formula_records == 0
            else "FORMULA_RECORDS_PRESENT_IN_EXACT_BYTES"
        ),
        "format_key_counts_including_headers": comparison[
            "format_key_counts_including_headers"
        ],
        "header_fields": comparison["header_fields"],
        "model_rows": model_rows,
        "recovery_open": True,
        "sha256": actual_hash,
        "sheet_evidence": comparison["sheet_evidence"],
        "shortlist_rows": shortlist_rows,
        "strict_error": strict_error,
        "strict_open": strict_open,
        "trailing_workbook_padding_bytes": biff.trailing_padding_bytes,
        "unreadable_regions": [],
        "verdict": (
            "INDEPENDENTLY_VERIFIED_EXAMINED_PROPERTIES"
            if strict_open
            else "RECOVERY_EXCEPTION_APPROVAL_REQUIRED"
        ),
    }


def inspect_compound_file(
    data: bytes, *, filename: str = "<bytes>"
) -> CompoundInspection:
    """Independently parse CFBF allocation and extract the Workbook stream."""
    if data[:8] != bytes.fromhex("d0cf11e0a1b11ae1"):
        raise BiffEvidenceError(
            "INVALID_CFBF_SIGNATURE", "not an OLE compound document", filename=filename
        )
    if len(data) < 512:
        raise BiffEvidenceError(
            "TRUNCATED_CFBF", "compound header is incomplete", filename=filename
        )
    major = _u16(data, 26)
    if major not in (3, 4) or _u16(data, 28) != 0xFFFE:
        raise BiffEvidenceError(
            "UNSUPPORTED_CFBF_HEADER",
            "unsupported version or byte order",
            filename=filename,
        )
    sector_size = 1 << _u16(data, 30)
    mini_sector_size = 1 << _u16(data, 32)
    if sector_size not in (512, 4096) or mini_sector_size != 64:
        raise BiffEvidenceError(
            "UNSUPPORTED_CFBF_SECTOR_SIZE",
            f"sector={sector_size}, mini-sector={mini_sector_size}",
            filename=filename,
        )
    if len(data) % sector_size:
        raise BiffEvidenceError(
            "TRUNCATED_CFBF", "file length is not sector aligned", filename=filename
        )
    sector_count = len(data) // sector_size - 1
    if _u32(data, 56) != 4096:
        raise BiffEvidenceError(
            "UNSUPPORTED_CFBF_HEADER",
            f"mini-stream cutoff is {_u32(data, 56)}, expected 4096",
            filename=filename,
        )
    directory_sector_count = _u32(data, 40)
    if major == 3 and directory_sector_count != 0:
        raise BiffEvidenceError(
            "INVALID_CFBF_HEADER",
            f"version 3 directory-sector count is {directory_sector_count}, expected 0",
            filename=filename,
        )

    def sector(sector_id: int) -> bytes:
        if not 0 <= sector_id < sector_count:
            raise BiffEvidenceError(
                "CFBF_SECTOR_OUT_OF_RANGE",
                f"sector {sector_id}, count {sector_count}",
                filename=filename,
            )
        start = (sector_id + 1) * sector_size
        return data[start : start + sector_size]

    fat_count = _u32(data, 44)
    difat_ids = [
        _u32(data, 76 + 4 * index)
        for index in range(109)
        if _u32(data, 76 + 4 * index) != FREE_SECTOR
    ]
    first_difat = _u32(data, 68)
    difat_count = _u32(data, 72)
    next_difat = first_difat
    seen_difat: set[int] = set()
    for _ in range(difat_count):
        if next_difat in _SPECIAL_SECTORS or next_difat in seen_difat:
            raise BiffEvidenceError(
                "INVALID_DIFAT_CHAIN", f"sector {next_difat}", filename=filename
            )
        seen_difat.add(next_difat)
        block = sector(next_difat)
        difat_ids.extend(
            _u32(block, 4 * index)
            for index in range(sector_size // 4 - 1)
            if _u32(block, 4 * index) != FREE_SECTOR
        )
        next_difat = _u32(block, sector_size - 4)
    if next_difat != END_OF_CHAIN:
        raise BiffEvidenceError(
            "INVALID_DIFAT_CHAIN",
            f"chain does not end after {difat_count} sectors: {next_difat:#x}",
            filename=filename,
        )
    if (difat_count == 0) != (first_difat == END_OF_CHAIN):
        raise BiffEvidenceError(
            "INVALID_DIFAT_CHAIN",
            "header DIFAT start/count are inconsistent",
            filename=filename,
        )
    if len(difat_ids) != fat_count:
        raise BiffEvidenceError(
            "TRUNCATED_FAT",
            f"DIFAT identifies {len(difat_ids)} FAT sectors, expected {fat_count}",
            filename=filename,
        )
    if len(set(difat_ids)) != len(difat_ids):
        raise BiffEvidenceError(
            "INVALID_DIFAT", "duplicate FAT sector identifiers", filename=filename
        )
    if set(difat_ids) & seen_difat:
        raise BiffEvidenceError(
            "INVALID_DIFAT",
            "FAT and DIFAT sector identities overlap",
            filename=filename,
        )
    fat: list[int] = []
    for sector_id in difat_ids:
        block = sector(sector_id)
        fat.extend(_u32(block, offset) for offset in range(0, sector_size, 4))
    if len(fat) < sector_count:
        raise BiffEvidenceError(
            "TRUNCATED_FAT",
            f"FAT covers {len(fat)} sectors, file contains {sector_count}",
            filename=filename,
        )
    for sector_id in difat_ids:
        if fat[sector_id] != FAT_SECTOR:
            raise BiffEvidenceError(
                "INVALID_FAT_MARKER",
                f"FAT sector {sector_id} has marker {fat[sector_id]:#x}",
                filename=filename,
            )
    for sector_id in seen_difat:
        if fat[sector_id] != DIFAT_SECTOR:
            raise BiffEvidenceError(
                "INVALID_DIFAT_MARKER",
                f"DIFAT sector {sector_id} has marker {fat[sector_id]:#x}",
                filename=filename,
            )

    directory_chain = _allocation_chain(
        _u32(data, 48), fat, "directory", filename=filename
    )
    if major == 4 and len(directory_chain) != directory_sector_count:
        raise BiffEvidenceError(
            "DIRECTORY_CHAIN_SIZE_MISMATCH",
            f"chain {len(directory_chain)}, declared {directory_sector_count}",
            filename=filename,
        )
    first_mini_fat = _u32(data, 60)
    mini_fat_sector_count = _u32(data, 64)
    if mini_fat_sector_count:
        mini_fat_chain = _allocation_chain(
            first_mini_fat, fat, "MiniFAT", filename=filename
        )
        if len(mini_fat_chain) != mini_fat_sector_count:
            raise BiffEvidenceError(
                "MINIFAT_CHAIN_SIZE_MISMATCH",
                f"chain {len(mini_fat_chain)}, declared {mini_fat_sector_count}",
                filename=filename,
            )
    else:
        if first_mini_fat != END_OF_CHAIN:
            raise BiffEvidenceError(
                "INVALID_MINIFAT_CHAIN",
                "zero MiniFAT sectors require an end-of-chain start",
                filename=filename,
            )
        mini_fat_chain = ()
    allocation_groups = {
        "FAT": set(difat_ids),
        "DIFAT": seen_difat,
        "directory": set(directory_chain),
        "MiniFAT": set(mini_fat_chain),
    }
    names = tuple(allocation_groups)
    for left_index, left_name in enumerate(names):
        for right_name in names[left_index + 1 :]:
            allocation_overlap = (
                allocation_groups[left_name] & allocation_groups[right_name]
            )
            if allocation_overlap:
                raise BiffEvidenceError(
                    "CFBF_ALLOCATION_OVERLAP",
                    f"{left_name}/{right_name}: "
                    f"{sorted(allocation_overlap)[:_MAX_EXAMPLES]!r}",
                    filename=filename,
                )
    directory_bytes = b"".join(sector(item) for item in directory_chain)
    entries: list[DirectoryEntry] = []
    for offset in range(0, len(directory_bytes), 128):
        raw = directory_bytes[offset : offset + 128]
        if len(raw) < 128:
            raise BiffEvidenceError(
                "TRUNCATED_DIRECTORY", f"offset {offset}", filename=filename
            )
        name_length = _u16(raw, 64)
        if name_length > 64 or name_length % 2:
            raise BiffEvidenceError(
                "INVALID_DIRECTORY_ENTRY",
                f"entry {offset // 128} has name length {name_length}",
                filename=filename,
            )
        try:
            name = raw[: name_length - 2].decode("utf-16le") if name_length >= 2 else ""
        except UnicodeDecodeError as error:
            raise BiffEvidenceError(
                "INVALID_DIRECTORY_ENTRY",
                f"entry {offset // 128} has invalid UTF-16: {error}",
                filename=filename,
            ) from error
        size = _u64(raw, 120) if major == 4 else _u32(raw, 120)
        entries.append(
            DirectoryEntry(
                did=offset // 128,
                name=name,
                entry_type=raw[66],
                start_sector=_u32(raw, 116),
                size=size,
            )
        )
    roots = [item for item in entries if item.entry_type == 5]
    workbooks = [
        item
        for item in entries
        if item.entry_type == 2 and item.name in ("Workbook", "Book")
    ]
    if len(roots) != 1 or len(workbooks) != 1:
        raise BiffEvidenceError(
            "AMBIGUOUS_CFBF_STREAM",
            f"roots={len(roots)}, workbooks={len(workbooks)}",
            filename=filename,
        )
    root = roots[0]
    workbook = workbooks[0]
    if workbook.size < _u32(data, 56):
        raise BiffEvidenceError(
            "UNSUPPORTED_MINISTREAM_WORKBOOK",
            f"Workbook size {workbook.size} is below the FAT-stream cutoff",
            filename=filename,
        )
    workbook_chain = _allocation_chain(
        workbook.start_sector, fat, "Workbook", filename=filename
    )
    required_workbook_sectors = math.ceil(workbook.size / sector_size)
    if len(workbook_chain) != required_workbook_sectors:
        raise BiffEvidenceError(
            "WORKBOOK_CHAIN_SIZE_MISMATCH",
            f"chain {len(workbook_chain)}, declared {required_workbook_sectors}",
            filename=filename,
        )
    workbook_stream = b"".join(sector(item) for item in workbook_chain)[: workbook.size]
    root_chain = (
        _allocation_chain(root.start_sector, fat, "root mini-stream", filename=filename)
        if root.size and root.start_sector not in _SPECIAL_SECTORS
        else ()
    )
    reserved_sectors = set().union(*allocation_groups.values())
    for label, chain in (
        ("Workbook", workbook_chain),
        ("root mini-stream", root_chain),
    ):
        reserved_overlap = reserved_sectors & set(chain)
        if reserved_overlap:
            raise BiffEvidenceError(
                "CFBF_ALLOCATION_OVERLAP",
                f"{label}/reserved: {sorted(reserved_overlap)[:_MAX_EXAMPLES]!r}",
                filename=filename,
            )
    overlap = tuple(sorted(set(root_chain) & set(workbook_chain)))
    return CompoundInspection(
        sector_size=sector_size,
        directory_entries=tuple(entries),
        workbook_stream=workbook_stream,
        workbook_entry=workbook,
        root_entry=root,
        workbook_chain=tuple(workbook_chain),
        root_chain=tuple(root_chain),
        overlap_sectors=overlap,
    )


def inspect_biff_stream(stream: bytes, *, filename: str = "<bytes>") -> BiffInspection:
    """Independently inspect BIFF globals, worksheets, cells, and formulas."""
    global_records, global_end = _record_substream(
        stream, 0, "workbook globals", expected_type=0x0005, filename=filename
    )
    boundsheets: list[_BoundSheetRecord] = []
    xf_format_keys: list[int] = []
    custom_formats: dict[int, str] = {}
    global_formulas: list[dict[str, object]] = []
    for offset, opcode, payload in global_records:
        if opcode == 0x0085:
            if len(payload) < 8:
                raise BiffEvidenceError(
                    "TRUNCATED_BOUNDSHEET", f"offset {offset}", filename=filename
                )
            name = _decode_short_unicode(payload, 6, filename=filename)
            boundsheets.append(
                {
                    "offset": _u32(payload, 0),
                    "visibility": payload[4],
                    "sheet_type": payload[5],
                    "name": name,
                }
            )
        elif opcode == 0x00E0:
            if len(payload) < 4:
                raise BiffEvidenceError(
                    "TRUNCATED_XF", f"offset {offset}", filename=filename
                )
            xf_format_keys.append(_u16(payload, 2))
        elif opcode == 0x041E:
            if len(payload) < 5:
                raise BiffEvidenceError(
                    "TRUNCATED_FORMAT", f"offset {offset}", filename=filename
                )
            custom_formats[_u16(payload, 0)] = _decode_unicode_no_length(
                payload, 4, _u16(payload, 2), filename=filename
            )
        if opcode in _FORMULA_RECORDS:
            global_formulas.append(_formula_record(offset, opcode, payload))
    if len(boundsheets) != 2:
        raise BiffEvidenceError(
            "UNEXPECTED_BOUNDSHEET_COUNT",
            f"found {len(boundsheets)}, expected 2",
            filename=filename,
        )
    offsets = [item["offset"] for item in boundsheets]
    if offsets != sorted(offsets) or len(set(offsets)) != len(offsets):
        raise BiffEvidenceError(
            "INVALID_BOUNDSHEET_OFFSETS", repr(offsets), filename=filename
        )
    if global_end != offsets[0]:
        raise BiffEvidenceError(
            "SKIPPED_BIFF_REGION",
            f"globals end {global_end}, first sheet starts {offsets[0]}",
            filename=filename,
        )
    sheets: list[RawSheet] = []
    for index, item in enumerate(boundsheets):
        start = item["offset"]
        records, end = _record_substream(
            stream,
            start,
            f"sheet {index} {item['name']!r}",
            expected_type=0x0010,
            filename=filename,
        )
        if index + 1 < len(boundsheets) and end != offsets[index + 1]:
            raise BiffEvidenceError(
                "SKIPPED_BIFF_REGION",
                f"sheet {index} ends {end}, next starts {offsets[index + 1]}",
                filename=filename,
            )
        cells, rows, columns, formulas = _inspect_sheet_records(
            records, filename=filename, sheet=str(item["name"])
        )
        sheets.append(
            RawSheet(
                name=str(item["name"]),
                offset=start,
                end_offset=end,
                visibility=item["visibility"],
                sheet_type=item["sheet_type"],
                record_count=len(records),
                cells=cells,
                row_default_xfs=rows,
                column_default_xfs=columns,
                formula_records=formulas,
            )
        )
    last_end = sheets[-1].end_offset
    trailing = stream[last_end:]
    if any(byte != 0 for byte in trailing):
        raise BiffEvidenceError(
            "NONZERO_TRAILING_BIFF_BYTES",
            f"{len(trailing)} trailing bytes include nonzero data",
            filename=filename,
        )
    if not xf_format_keys:
        raise BiffEvidenceError("MISSING_XF_TABLE", "no XF records", filename=filename)
    return BiffInspection(
        sheets=tuple(sheets),
        xf_format_keys=tuple(xf_format_keys),
        custom_formats=custom_formats,
        global_end_offset=global_end,
        trailing_padding_bytes=len(trailing),
        global_formula_records=tuple(global_formulas),
    )


def _verify_allocation_signature(
    compound: CompoundInspection,
    expectation: WorkbookExpectation,
    *,
    filename: str,
) -> None:
    overlap = compound.overlap_sectors
    if len(overlap) != expectation.overlap_sector_count:
        raise BiffEvidenceError(
            "ALLOCATION_SIGNATURE_MISMATCH",
            f"overlap sectors {len(overlap)}, expected {expectation.overlap_sector_count}",
            filename=filename,
        )
    if expectation.strict_open:
        if overlap:
            raise BiffEvidenceError(
                "UNEXPECTED_ALLOCATION_OVERLAP", repr(overlap[:5]), filename=filename
            )
        return
    if not overlap or tuple(overlap) != compound.workbook_chain:
        raise BiffEvidenceError(
            "UNEXPECTED_RECOVERY_DEFECT",
            "root mini-stream must overlap the complete Workbook chain",
            filename=filename,
        )
    if (
        compound.root_entry.start_sector != 1
        or compound.root_entry.size != 512
        or compound.workbook_entry.start_sector != 2
    ):
        raise BiffEvidenceError(
            "UNEXPECTED_RECOVERY_DEFECT",
            "expected root(start=1,size=512) and Workbook(start=2)",
            filename=filename,
        )


def _compare_readers(
    *,
    filename: str,
    biff: BiffInspection,
    calamine: object,
    envelope: BiffXlsEnvelope,
) -> dict[str, object]:
    metadata = list(cast(object, calamine).sheets_metadata)  # type: ignore[attr-defined]
    calamine_names = [str(item.name) for item in metadata]
    raw_names = [sheet.name for sheet in biff.sheets]
    expected_names = [item[1] for item in _EXPECTED_SHEETS]
    parser_names = [sheet.exact_name for sheet in envelope.sheets]
    if (
        raw_names != expected_names
        or calamine_names != expected_names
        or parser_names != expected_names
    ):
        raise BiffEvidenceError(
            "SHEET_IDENTITY_MISMATCH",
            f"raw={raw_names!r}, calamine={calamine_names!r}, parser={parser_names!r}",
            filename=filename,
        )
    compared = 0
    exact_values = 0
    collapsed_missing = 0
    raw_exact = 0
    header_fields = 0
    data_fields = 0
    data_types: Counter[str] = Counter()
    format_keys: Counter[int] = Counter()
    role_rows: dict[str, int] = {}
    sheet_evidence: list[dict[str, object]] = []
    for index, (role, exact_name, headers) in enumerate(_EXPECTED_SHEETS):
        raw_sheet = biff.sheets[index]
        parser_sheet = envelope.sheet(role)
        value_sheet = cast(object, calamine).get_sheet_by_index(index)  # type: ignore[attr-defined]
        if raw_sheet.visibility != 0 or raw_sheet.sheet_type != 0:
            raise BiffEvidenceError(
                "UNSUPPORTED_SHEET_METADATA",
                f"visibility={raw_sheet.visibility}, type={raw_sheet.sheet_type}",
                filename=filename,
                sheet=exact_name,
            )
        start = tuple(value_sheet.start) if value_sheet.start is not None else None
        if start != (0, 0):
            raise BiffEvidenceError(
                "UNEXPECTED_SHEET_ORIGIN",
                repr(start),
                filename=filename,
                sheet=exact_name,
            )
        value_rows = value_sheet.to_python()
        if (
            not value_rows
            or tuple(_value_at(value_rows, 1, col + 1) for col in range(len(headers)))
            != headers
        ):
            raise BiffEvidenceError(
                "HEADER_MISMATCH",
                "independent reader header mismatch",
                filename=filename,
                sheet=exact_name,
            )
        for row_number, row in enumerate(value_rows, start=1):
            extras = row[len(headers) :]
            if any(not _is_missing_value(value) for value in extras):
                raise BiffEvidenceError(
                    "DATA_OUTSIDE_SUPPORTED_COLUMNS",
                    f"row {row_number}",
                    filename=filename,
                    sheet=exact_name,
                )
        applicable_rows = [
            row_number
            for row_number in range(2, len(value_rows) + 1)
            if any(
                not _is_missing_value(_value_at(value_rows, row_number, column + 1))
                for column in range(len(headers))
            )
        ]
        parser_rows = [row.source_row for row in parser_sheet.rows]
        if parser_rows != applicable_rows:
            raise BiffEvidenceError(
                "ROW_COVERAGE_MISMATCH",
                f"parser={parser_rows[:_MAX_EXAMPLES]!r}, independent={applicable_rows[:_MAX_EXAMPLES]!r}",
                filename=filename,
                sheet=exact_name,
            )
        expected_coordinates = {(1, column + 1) for column in range(len(headers))} | {
            (row_number, column + 1)
            for row_number in applicable_rows
            for column in range(len(headers))
        }
        extra_records = sorted(set(raw_sheet.cells) - expected_coordinates)
        if extra_records:
            raise BiffEvidenceError(
                "UNACCOUNTED_CELL_RECORD",
                repr(extra_records[:_MAX_EXAMPLES]),
                filename=filename,
                sheet=exact_name,
            )
        parser_cells = list(parser_sheet.headers) + [
            cell for row in parser_sheet.rows for cell in row.cells
        ]
        if len(parser_cells) != len(expected_coordinates):
            raise BiffEvidenceError(
                "PARSER_CELL_COVERAGE_MISMATCH",
                f"parser={len(parser_cells)}, expected={len(expected_coordinates)}",
                filename=filename,
                sheet=exact_name,
            )
        for cell in parser_cells:
            coordinate = (cell.source_row, cell.source_column)
            raw_cell = raw_sheet.cells.get(coordinate)
            _compare_cell_properties(
                filename=filename,
                sheet=exact_name,
                parser_cell=cell,
                raw_cell=raw_cell,
                raw_sheet=raw_sheet,
                xf_format_keys=biff.xf_format_keys,
                custom_formats=biff.custom_formats,
            )
            raw_exact += 1
            format_keys[cell.format_key] += 1
            independent_value = _value_at(value_rows, *coordinate)
            if cell.cell_type in ("empty", "blank"):
                if not _is_missing_value(independent_value):
                    raise BiffEvidenceError(
                        "VALUE_MISMATCH",
                        f"parser {cell.cell_type}, independent {independent_value!r}",
                        filename=filename,
                        sheet=exact_name,
                        coordinate=cell.coordinate,
                    )
                collapsed_missing += 1
            elif cell.cell_type == "error":
                if raw_cell is None or raw_cell.error_code != cell.raw_value:
                    raise BiffEvidenceError(
                        "VALUE_MISMATCH",
                        "raw BIFF error code disagrees with parser",
                        filename=filename,
                        sheet=exact_name,
                        coordinate=cell.coordinate,
                    )
                exact_values += 1
            elif not _values_equal(cell, independent_value, raw_cell):
                raise BiffEvidenceError(
                    "VALUE_MISMATCH",
                    f"parser {cell.raw_value!r}, independent {independent_value!r}",
                    filename=filename,
                    sheet=exact_name,
                    coordinate=cell.coordinate,
                )
            else:
                exact_values += 1
            compared += 1
            if cell.source_row == 1:
                header_fields += 1
            else:
                data_fields += 1
                data_types[cell.cell_type] += 1
        role_rows[role] = len(applicable_rows)
        sheet_evidence.append(
            {
                "role": role,
                "exact_name": exact_name,
                "sheet_index": index,
                "visibility": "visible",
                "header_fields": len(headers),
                "rows": len(applicable_rows),
                "first_data_row": applicable_rows[0],
                "last_data_row": applicable_rows[-1],
                "coordinate_coverage": (
                    f"A1:{_coordinate(applicable_rows[-1], len(headers))}"
                ),
                "data_fields": len(applicable_rows) * len(headers),
                "raw_record_count": raw_sheet.record_count,
                "explicit_cell_records": sum(
                    coordinate in raw_sheet.cells for coordinate in expected_coordinates
                ),
                "absent_cells": sum(
                    coordinate not in raw_sheet.cells
                    for coordinate in expected_coordinates
                ),
                "formula_records": len(raw_sheet.formula_records),
                "substream_start": raw_sheet.offset,
                "substream_end": raw_sheet.end_offset,
                "reached_eof": True,
            }
        )
    return {
        "calamine_exact_values": exact_values,
        "calamine_collapsed_missing": collapsed_missing,
        "compared_cells_including_headers": compared,
        "raw_cell_properties_exact": raw_exact,
        "header_fields": header_fields,
        "data_fields": data_fields,
        "model_rows": role_rows[MODEL_PORTFOLIO_ROLE],
        "shortlist_rows": role_rows[ANALYTICAL_SHORTLIST_ROLE],
        "data_cell_types": dict(sorted(data_types.items())),
        "format_key_counts_including_headers": {
            str(key): value for key, value in sorted(format_keys.items())
        },
        "sheet_evidence": sheet_evidence,
    }


def _compare_cell_properties(
    *,
    filename: str,
    sheet: str,
    parser_cell: SourceCell,
    raw_cell: RawCell | None,
    raw_sheet: RawSheet,
    xf_format_keys: Sequence[int],
    custom_formats: Mapping[int, str],
) -> None:
    expected_kind = {
        "empty": None,
        "blank": "blank",
        "text": "text",
        "number": "number",
        "date_serial": "number",
        "boolean": "boolean",
        "error": "error",
    }[parser_cell.cell_type]
    actual_kind: str | None
    if raw_cell is None:
        actual_kind = None
    elif raw_cell.kind == "boolerr":
        actual_kind = "error" if raw_cell.error_code is not None else "boolean"
    else:
        actual_kind = raw_cell.kind
    if actual_kind != expected_kind:
        raise BiffEvidenceError(
            "CELL_TYPE_MISMATCH",
            f"raw {actual_kind!r}, parser {parser_cell.cell_type!r}",
            filename=filename,
            sheet=sheet,
            coordinate=parser_cell.coordinate,
        )
    if raw_cell is None:
        effective_xf = _effective_empty_xf(
            raw_sheet, parser_cell.source_row, parser_cell.source_column
        )
    else:
        effective_xf = raw_cell.xf_index
    if effective_xf != parser_cell.xf_index:
        raise BiffEvidenceError(
            "XF_MISMATCH",
            f"raw {effective_xf}, parser {parser_cell.xf_index}",
            filename=filename,
            sheet=sheet,
            coordinate=parser_cell.coordinate,
        )
    if not 0 <= effective_xf < len(xf_format_keys):
        raise BiffEvidenceError(
            "INVALID_XF_REFERENCE",
            f"XF {effective_xf}, table length {len(xf_format_keys)}",
            filename=filename,
            sheet=sheet,
            coordinate=parser_cell.coordinate,
        )
    format_key = xf_format_keys[effective_xf]
    if format_key != parser_cell.format_key:
        raise BiffEvidenceError(
            "FORMAT_REFERENCE_MISMATCH",
            f"raw {format_key}, parser {parser_cell.format_key}",
            filename=filename,
            sheet=sheet,
            coordinate=parser_cell.coordinate,
        )
    expected_format = custom_formats.get(
        format_key, _STANDARD_NUMBER_FORMATS.get(format_key)
    )
    if expected_format is None:
        raise BiffEvidenceError(
            "UNRESOLVED_NUMBER_FORMAT",
            f"format key {format_key} has no independent format string",
            filename=filename,
            sheet=sheet,
            coordinate=parser_cell.coordinate,
        )
    if parser_cell.number_format != expected_format:
        raise BiffEvidenceError(
            "NUMBER_FORMAT_MISMATCH",
            f"raw {expected_format!r}, parser {parser_cell.number_format!r}",
            filename=filename,
            sheet=sheet,
            coordinate=parser_cell.coordinate,
        )


def _values_equal(
    parser_cell: SourceCell, independent: object, raw_cell: RawCell | None
) -> bool:
    if parser_cell.cell_type == "text":
        return isinstance(independent, str) and independent == parser_cell.raw_value
    if parser_cell.cell_type == "boolean":
        return (
            isinstance(independent, bool)
            and independent == parser_cell.raw_value
            and raw_cell is not None
            and raw_cell.bool_value == parser_cell.raw_value
        )
    if parser_cell.cell_type == "number":
        if isinstance(independent, bool) or not isinstance(independent, (int, float)):
            return False
        if float(independent) != float(cast(float, parser_cell.raw_value)):
            return False
        return (
            raw_cell is not None
            and raw_cell.raw_number is not None
            and raw_cell.raw_number == float(cast(float, parser_cell.raw_value))
        )
    if parser_cell.cell_type == "date_serial":
        return raw_cell is not None and raw_cell.raw_number == parser_cell.raw_value
    return False


def _inspect_sheet_records(
    records: Sequence[tuple[int, int, bytes]], *, filename: str, sheet: str
) -> tuple[
    dict[tuple[int, int], RawCell],
    dict[int, int],
    tuple[tuple[int, int, int], ...],
    tuple[dict[str, object], ...],
]:
    cells: dict[tuple[int, int], RawCell] = {}
    row_defaults: dict[int, int] = {}
    column_defaults: list[tuple[int, int, int]] = []
    formulas: list[dict[str, object]] = []

    def add(row: int, column: int, cell: RawCell) -> None:
        coordinate = (row + 1, column + 1)
        if coordinate in cells:
            raise BiffEvidenceError(
                "DUPLICATE_CELL_RECORD",
                f"record offsets {cells[coordinate].record_offset} and {cell.record_offset}",
                filename=filename,
                sheet=sheet,
                coordinate=_coordinate(*coordinate),
            )
        cells[coordinate] = cell

    for offset, opcode, payload in records:
        if opcode in _FORMULA_RECORDS:
            formulas.append(_formula_record(offset, opcode, payload))
        if opcode in _SINGLE_CELL_RECORDS:
            if len(payload) < 6:
                raise BiffEvidenceError(
                    "TRUNCATED_CELL_RECORD",
                    f"offset {offset}",
                    filename=filename,
                    sheet=sheet,
                )
            row, column, xf_index = struct.unpack_from("<HHH", payload, 0)
            kind = _SINGLE_CELL_RECORDS[opcode]
            raw_number = None
            bool_value = None
            error_code = None
            if opcode == 0x0203:
                if len(payload) < 14:
                    raise BiffEvidenceError(
                        "TRUNCATED_CELL_RECORD",
                        f"offset {offset}",
                        filename=filename,
                        sheet=sheet,
                    )
                raw_number = struct.unpack_from("<d", payload, 6)[0]
            elif opcode == 0x027E:
                if len(payload) < 10:
                    raise BiffEvidenceError(
                        "TRUNCATED_CELL_RECORD",
                        f"offset {offset}",
                        filename=filename,
                        sheet=sheet,
                    )
                raw_number = _decode_rk(_u32(payload, 6))
            elif opcode == 0x0205:
                if len(payload) < 8:
                    raise BiffEvidenceError(
                        "TRUNCATED_CELL_RECORD",
                        f"offset {offset}",
                        filename=filename,
                        sheet=sheet,
                    )
                if payload[7]:
                    error_code = payload[6]
                else:
                    bool_value = bool(payload[6])
            add(
                row,
                column,
                RawCell(
                    kind, xf_index, opcode, offset, raw_number, bool_value, error_code
                ),
            )
        elif opcode == 0x00BD:
            if len(payload) < 12 or (len(payload) - 6) % 6:
                raise BiffEvidenceError(
                    "TRUNCATED_MULRK",
                    f"offset {offset}",
                    filename=filename,
                    sheet=sheet,
                )
            row, first_column = _u16(payload, 0), _u16(payload, 2)
            count = (len(payload) - 6) // 6
            if _u16(payload, len(payload) - 2) - first_column + 1 != count:
                raise BiffEvidenceError(
                    "INVALID_MULRK_WIDTH",
                    f"offset {offset}",
                    filename=filename,
                    sheet=sheet,
                )
            for index in range(count):
                position = 4 + 6 * index
                add(
                    row,
                    first_column + index,
                    RawCell(
                        "number",
                        _u16(payload, position),
                        opcode,
                        offset,
                        _decode_rk(_u32(payload, position + 2)),
                    ),
                )
        elif opcode == 0x00BE:
            if len(payload) < 8 or (len(payload) - 6) % 2:
                raise BiffEvidenceError(
                    "TRUNCATED_MULBLANK",
                    f"offset {offset}",
                    filename=filename,
                    sheet=sheet,
                )
            row, first_column = _u16(payload, 0), _u16(payload, 2)
            count = (len(payload) - 6) // 2
            if _u16(payload, len(payload) - 2) - first_column + 1 != count:
                raise BiffEvidenceError(
                    "INVALID_MULBLANK_WIDTH",
                    f"offset {offset}",
                    filename=filename,
                    sheet=sheet,
                )
            for index in range(count):
                add(
                    row,
                    first_column + index,
                    RawCell("blank", _u16(payload, 4 + 2 * index), opcode, offset),
                )
        elif opcode == 0x0208:
            if len(payload) < 16:
                raise BiffEvidenceError(
                    "TRUNCATED_ROW",
                    f"offset {offset}",
                    filename=filename,
                    sheet=sheet,
                )
            row = _u16(payload, 0)
            bits = _u32(payload, 12)
            if (bits >> 7) & 1:
                row_defaults[row + 1] = (bits >> 16) & 0xFFF
        elif opcode == 0x007D:
            if len(payload) < 10:
                raise BiffEvidenceError(
                    "TRUNCATED_COLINFO",
                    f"offset {offset}",
                    filename=filename,
                    sheet=sheet,
                )
            column_defaults.append(
                (_u16(payload, 0) + 1, _u16(payload, 2) + 1, _u16(payload, 6))
            )
    return cells, row_defaults, tuple(column_defaults), tuple(formulas)


def _strict_open(data: bytes) -> tuple[bool, str | None]:
    try:
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            xlrd.open_workbook(
                file_contents=data,
                formatting_info=True,
                ignore_workbook_corruption=False,
                logfile=io.StringIO(),
                on_demand=False,
            )
    except CompDocError as error:
        return False, f"{type(error).__name__}: {error}"
    except Exception as error:
        raise BiffEvidenceError(
            "UNEXPECTED_STRICT_OPEN_ERROR", f"{type(error).__name__}: {error}"
        ) from error
    return True, None


def _record_substream(
    stream: bytes,
    start: int,
    label: str,
    *,
    expected_type: int,
    filename: str,
) -> tuple[list[tuple[int, int, bytes]], int]:
    if not 0 <= start < len(stream):
        raise BiffEvidenceError(
            "BIFF_OFFSET_OUT_OF_RANGE", f"{label}: {start}", filename=filename
        )
    position = start
    records: list[tuple[int, int, bytes]] = []
    while position + 4 <= len(stream):
        opcode, size = struct.unpack_from("<HH", stream, position)
        end = position + 4 + size
        if end > len(stream):
            raise BiffEvidenceError(
                "TRUNCATED_BIFF_RECORD",
                f"{label}: opcode {opcode:#06x} at {position} ends {end}",
                filename=filename,
            )
        payload = stream[position + 4 : end]
        records.append((position, opcode, payload))
        position = end
        if opcode == 0x000A:
            break
    else:
        raise BiffEvidenceError(
            "MISSING_BIFF_EOF", f"{label}: start {start}", filename=filename
        )
    if not records or records[0][1] != 0x0809 or len(records[0][2]) < 16:
        raise BiffEvidenceError(
            "MISSING_BIFF_BOF", f"{label}: start {start}", filename=filename
        )
    bof_version, substream_type = struct.unpack_from("<HH", records[0][2], 0)
    if bof_version != 0x0600 or substream_type != expected_type:
        raise BiffEvidenceError(
            "UNSUPPORTED_BIFF_BOF",
            f"{label}: version {bof_version:#06x}, type {substream_type:#06x}",
            filename=filename,
        )
    if records[-1][1] != 0x000A:
        raise BiffEvidenceError(
            "MISSING_BIFF_EOF", f"{label}: start {start}", filename=filename
        )
    if records[-1][2]:
        raise BiffEvidenceError(
            "INVALID_BIFF_EOF", f"{label}: EOF contains data", filename=filename
        )
    return records, position


def _allocation_chain(
    start: int, table: Sequence[int], label: str, *, filename: str
) -> tuple[int, ...]:
    if start in _SPECIAL_SECTORS:
        raise BiffEvidenceError(
            "INVALID_ALLOCATION_CHAIN", f"{label}: start {start:#x}", filename=filename
        )
    result: list[int] = []
    seen: set[int] = set()
    current = start
    while current != END_OF_CHAIN:
        if current in _SPECIAL_SECTORS or not 0 <= current < len(table):
            raise BiffEvidenceError(
                "INVALID_ALLOCATION_CHAIN",
                f"{label}: sector {current:#x}",
                filename=filename,
            )
        if current in seen:
            raise BiffEvidenceError(
                "ALLOCATION_CYCLE", f"{label}: sector {current}", filename=filename
            )
        seen.add(current)
        result.append(current)
        current = table[current]
    return tuple(result)


def _formula_record(offset: int, opcode: int, payload: bytes) -> dict[str, object]:
    item: dict[str, object] = {
        "record": _FORMULA_RECORDS[opcode],
        "opcode": f"0x{opcode:04x}",
        "record_offset": offset,
    }
    if opcode in (0x0006, 0x0206, 0x0406) and len(payload) >= 6:
        row, column = _u16(payload, 0) + 1, _u16(payload, 2) + 1
        item.update(
            {"coordinate": _coordinate(row, column), "xf_index": _u16(payload, 4)}
        )
    return item


def _effective_empty_xf(sheet: RawSheet, row: int, column: int) -> int:
    if row in sheet.row_default_xfs:
        return sheet.row_default_xfs[row]
    for first, last, xf_index in sheet.column_default_xfs:
        if first <= column <= last:
            return xf_index if xf_index != 0xFFFF else 15
    return 15


def _decode_rk(value: int) -> float:
    divide = bool(value & 1)
    if value & 2:
        signed = value if value < 0x80000000 else value - 0x100000000
        result = float(signed >> 2)
    else:
        result = struct.unpack("<d", struct.pack("<II", 0, value & 0xFFFFFFFC))[0]
    return result / 100.0 if divide else result


def _decode_short_unicode(payload: bytes, offset: int, *, filename: str) -> str:
    count = payload[offset]
    return _decode_unicode_no_length(payload, offset + 1, count, filename=filename)


def _decode_unicode_no_length(
    payload: bytes, offset: int, count: int, *, filename: str
) -> str:
    if offset >= len(payload):
        raise BiffEvidenceError("TRUNCATED_UNICODE", "missing flags", filename=filename)
    flags = payload[offset]
    offset += 1
    rich_runs = 0
    extension_size = 0
    if flags & 0x08:
        if offset + 2 > len(payload):
            raise BiffEvidenceError(
                "TRUNCATED_UNICODE", "missing rich runs", filename=filename
            )
        rich_runs = _u16(payload, offset)
        offset += 2
    if flags & 0x04:
        if offset + 4 > len(payload):
            raise BiffEvidenceError(
                "TRUNCATED_UNICODE", "missing extension", filename=filename
            )
        extension_size = _u32(payload, offset)
        offset += 4
    width = 2 if flags & 1 else 1
    end = offset + count * width
    complete_end = end + rich_runs * 4 + extension_size
    if complete_end > len(payload):
        raise BiffEvidenceError(
            "TRUNCATED_UNICODE", "string exceeds record", filename=filename
        )
    try:
        return payload[offset:end].decode("utf-16le" if width == 2 else "latin-1")
    except UnicodeDecodeError as error:
        raise BiffEvidenceError(
            "INVALID_UNICODE", f"cannot decode string: {error}", filename=filename
        ) from error


def _value_at(rows: Sequence[Sequence[object]], row: int, column: int) -> object:
    if row - 1 >= len(rows) or column - 1 >= len(rows[row - 1]):
        return None
    return rows[row - 1][column - 1]


def _is_missing_value(value: object) -> bool:
    return value is None or value == ""


def _coordinate(row: int, column: int) -> str:
    label = ""
    value = column
    while value:
        value, remainder = divmod(value - 1, 26)
        label = chr(65 + remainder) + label
    return f"{label}{row}"


def _u16(data: bytes, offset: int = 0) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int = 0) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _u64(data: bytes, offset: int = 0) -> int:
    return struct.unpack_from("<Q", data, offset)[0]
