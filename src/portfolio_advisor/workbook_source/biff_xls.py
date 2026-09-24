"""Pure, lossless BIFF-XLS extraction for retained dual-sheet workbooks.

This module does not normalize financial values, translate classifications,
admit data, write a database, move a workbook, or invoke the operational
importer.  It binds one deterministic envelope to the exact bytes supplied to
``xlrd`` and preserves the BIFF cell values and formatting metadata exposed by
that reader.

``xlrd`` returns a formula cell's cached result and does not expose the formula
text or a reliable formula-present flag.  This parser never evaluates formulas
and records that limitation in every envelope.
"""

from __future__ import annotations

import hashlib
import re
import struct
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final, Literal, cast

import xlrd  # type: ignore[import-untyped]
from xlrd.biffh import XLRDError  # type: ignore[import-untyped]
from xlrd.compdoc import CompDocError  # type: ignore[import-untyped]

from portfolio_advisor.canonical import canonical_fingerprint, canonical_json

CONTRACT_NAME: Final = "PORTFOLIO_ADVISOR_BIFF_XLS_DUAL_SHEET_ENVELOPE"
CONTRACT_VERSION: Final = 1
PARSER_NAME: Final = "portfolio_advisor.workbook_source.biff_xls"
PARSER_VERSION: Final = 1
ADMISSION_STATUS: Final = "NOT_EVALUATED_PARSER_ONLY"

MODEL_PORTFOLIO_ROLE: Final = "MODEL_PORTFOLIO"
ANALYTICAL_SHORTLIST_ROLE: Final = "ANALYTICAL_SHORTLIST"
MODEL_SHEET_NAME: Final = " modell portfóliók"
SHORTLIST_SHEET_NAME: Final = " shortlist"

METRIC_HEADERS: Final = (
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
)
MODEL_HEADERS: Final = (
    "Portfólió neve",
    "Termék",
    "ISIN",
    "Hányad (%)",
    "Eszközosztály",
    "Aleszközosztály",
    "Deviza",
    "Devizakockázat",
    "Fenntarthatóság",
    *METRIC_HEADERS,
)
SHORTLIST_HEADERS: Final = (
    "Termék",
    "ISIN",
    "Eszközosztály",
    "Aleszközosztály",
    "Termék típus",
    "Deviza",
    "Devizakockázat",
    "Fenntarthatóság",
    *METRIC_HEADERS,
)

_SHEET_SPECS: Final = (
    (MODEL_PORTFOLIO_ROLE, MODEL_SHEET_NAME, MODEL_HEADERS),
    (ANALYTICAL_SHORTLIST_ROLE, SHORTLIST_SHEET_NAME, SHORTLIST_HEADERS),
)
_ROLE_BY_NORMALIZED_NAME: Final = {
    MODEL_SHEET_NAME.strip().casefold(): MODEL_PORTFOLIO_ROLE,
    SHORTLIST_SHEET_NAME.strip().casefold(): ANALYTICAL_SHORTLIST_ROLE,
}
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
_DATE_FROM_FILENAME: Final = re.compile(
    r"^(?P<prefix>.+?)(?<!\d)(?P<date>\d{8})\.xls$", re.IGNORECASE
)
_CELL_KIND: Final = {
    xlrd.XL_CELL_EMPTY: "empty",
    xlrd.XL_CELL_TEXT: "text",
    xlrd.XL_CELL_NUMBER: "number",
    xlrd.XL_CELL_DATE: "date_serial",
    xlrd.XL_CELL_BOOLEAN: "boolean",
    xlrd.XL_CELL_ERROR: "error",
    xlrd.XL_CELL_BLANK: "blank",
}
_VISIBILITY: Final = {0: "visible", 1: "hidden", 2: "very_hidden"}

type CellKind = Literal[
    "empty", "text", "number", "date_serial", "boolean", "error", "blank"
]
type JsonScalar = str | int | float | bool | None


class BiffXlsParseError(ValueError):
    """A BIFF source could not satisfy the parser-only envelope contract."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        filename: str | None = None,
        sheet: str | None = None,
        row: int | None = None,
        column: int | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.filename = filename
        self.sheet = sheet
        self.row = row
        self.column = column
        location = " / ".join(
            item
            for item in (
                filename,
                sheet,
                f"row {row}" if row is not None else None,
                f"column {column}" if column is not None else None,
            )
            if item is not None
        )
        super().__init__(f"[{code}] {location + ': ' if location else ''}{message}")

    def to_dict(self) -> dict[str, JsonScalar]:
        """Return a stable diagnostic representation."""
        return {
            "code": self.code,
            "column": self.column,
            "filename": self.filename,
            "message": self.message,
            "row": self.row,
            "sheet": self.sheet,
        }


@dataclass(frozen=True, slots=True)
class SourceCell:
    """One BIFF cell as exposed by xlrd, without value normalization."""

    source_row: int
    source_column: int
    coordinate: str
    cell_type: CellKind
    biff_type_code: int
    raw_value: JsonScalar
    xf_index: int
    format_key: int
    number_format: str
    error_text: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "biff_type_code": self.biff_type_code,
            "cell_type": self.cell_type,
            "coordinate": self.coordinate,
            "error_text": self.error_text,
            "format_key": self.format_key,
            "number_format": self.number_format,
            "raw_value": self.raw_value,
            "source_column": self.source_column,
            "source_row": self.source_row,
            "xf_index": self.xf_index,
        }


@dataclass(frozen=True, slots=True)
class SourceRow:
    """One applicable worksheet occurrence in source order."""

    occurrence_index: int
    source_row: int
    source_reference: str
    cells: tuple[SourceCell, ...]
    row_fingerprint: str

    def to_dict(self) -> dict[str, object]:
        return {
            "cells": [cell.to_dict() for cell in self.cells],
            "occurrence_index": self.occurrence_index,
            "row_fingerprint": self.row_fingerprint,
            "source_reference": self.source_reference,
            "source_row": self.source_row,
        }


@dataclass(frozen=True, slots=True)
class EnvelopeDiagnostic:
    """A non-fatal source anomaly preserved alongside its raw cell."""

    severity: Literal["WARNING"]
    code: str
    message: str
    role: str
    sheet_name: str
    source_row: int
    source_column: int
    coordinate: str
    header: str
    cell_type: CellKind
    raw_value: JsonScalar

    def to_dict(self) -> dict[str, object]:
        return {
            "cell_type": self.cell_type,
            "code": self.code,
            "coordinate": self.coordinate,
            "header": self.header,
            "message": self.message,
            "raw_value": self.raw_value,
            "role": self.role,
            "severity": self.severity,
            "sheet_name": self.sheet_name,
            "source_column": self.source_column,
            "source_row": self.source_row,
        }


@dataclass(frozen=True, slots=True)
class ParsedSheet:
    """A strictly identified source worksheet and all applicable rows."""

    role: str
    exact_name: str
    sheet_index: int
    visibility_code: int
    visibility: str
    header_row: int
    header_start_column: int
    headers: tuple[SourceCell, ...]
    merged_ranges: tuple[dict[str, int], ...]
    rows: tuple[SourceRow, ...]
    sheet_fingerprint: str

    def to_dict(self) -> dict[str, object]:
        return {
            "exact_name": self.exact_name,
            "header_row": self.header_row,
            "header_start_column": self.header_start_column,
            "headers": [header.to_dict() for header in self.headers],
            "merged_ranges": list(self.merged_ranges),
            "role": self.role,
            "rows": [row.to_dict() for row in self.rows],
            "sheet_fingerprint": self.sheet_fingerprint,
            "sheet_index": self.sheet_index,
            "visibility": self.visibility,
            "visibility_code": self.visibility_code,
        }


@dataclass(frozen=True, slots=True)
class BiffXlsEnvelope:
    """Versioned deterministic extraction result; never an admission receipt."""

    source_filename: str
    snapshot_date: str
    workbook_sha256: str
    byte_length: int
    workbook_metadata: dict[str, JsonScalar]
    workbook_sheets: tuple[dict[str, object], ...]
    sheets: tuple[ParsedSheet, ...]
    diagnostics: tuple[EnvelopeDiagnostic, ...]

    def _payload(self) -> dict[str, object]:
        return {
            "admission_status": ADMISSION_STATUS,
            "byte_length": self.byte_length,
            "contract": {"name": CONTRACT_NAME, "version": CONTRACT_VERSION},
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "extraction_limits": {
                "formula_evaluation": "PROHIBITED",
                "formula_text": "UNAVAILABLE_FROM_XLRD",
                "formula_presence": "NOT_RELIABLY_DISTINGUISHABLE_FROM_CACHED_RESULT",
                "formula_value": "CACHED_RESULT_ONLY_WHEN_PRESENT_IN_BIFF",
                "normalized_projection": "NOT_PRODUCED",
            },
            "parser": {
                "compound_document_recovery": "XLRD_IGNORE_WORKBOOK_CORRUPTION",
                "library": "xlrd",
                "library_version": xlrd.__version__,
                "name": PARSER_NAME,
                "version": PARSER_VERSION,
            },
            "sheets": [sheet.to_dict() for sheet in self.sheets],
            "snapshot_date": self.snapshot_date,
            "source_filename": self.source_filename,
            "workbook_sha256": self.workbook_sha256,
            "workbook_metadata": self.workbook_metadata,
            "workbook_sheets": list(self.workbook_sheets),
        }

    @property
    def envelope_fingerprint(self) -> str:
        """SHA-256 of the canonical envelope payload excluding this property."""
        return canonical_fingerprint(self._payload())

    def to_dict(self) -> dict[str, object]:
        return {**self._payload(), "envelope_fingerprint": self.envelope_fingerprint}

    def to_json(self) -> str:
        """Return stable canonical JSON with no timestamps or local paths."""
        return canonical_json(self.to_dict())

    def sheet(self, role: str) -> ParsedSheet:
        """Return exactly one parsed source sheet by normalized role."""
        try:
            return next(sheet for sheet in self.sheets if sheet.role == role)
        except StopIteration as error:  # pragma: no cover - constructor invariant
            raise KeyError(role) from error


def parse_biff_xls(path: Path) -> BiffXlsEnvelope:
    """Parse one ordinary BIFF ``.xls`` file into a source-only envelope."""
    source_path = Path(path)
    if source_path.is_symlink() or not source_path.is_file():
        raise BiffXlsParseError(
            "SOURCE_NOT_ORDINARY_FILE",
            "source must be an existing ordinary file, not a link",
            filename=source_path.name,
        )
    snapshot_date = _snapshot_date(source_path.name)
    try:
        source_bytes = source_path.read_bytes()
    except OSError as error:
        raise BiffXlsParseError(
            "SOURCE_READ_FAILED",
            str(error),
            filename=source_path.name,
        ) from error
    workbook_sha256 = hashlib.sha256(source_bytes).hexdigest()
    try:
        workbook = xlrd.open_workbook(
            file_contents=source_bytes,
            formatting_info=True,
            ignore_workbook_corruption=True,
            on_demand=False,
        )
    except (
        CompDocError,
        XLRDError,
        OSError,
        ValueError,
        IndexError,
        struct.error,
    ) as error:
        raise BiffXlsParseError(
            "MALFORMED_OR_UNSUPPORTED_BIFF_XLS",
            str(error),
            filename=source_path.name,
        ) from error

    workbook_sheets = tuple(
        {
            "exact_name": sheet.name,
            "normalized_role": _ROLE_BY_NORMALIZED_NAME.get(
                sheet.name.strip().casefold()
            ),
            "sheet_index": index,
            "visibility": _visibility_name(sheet.visibility),
            "visibility_code": int(sheet.visibility),
        }
        for index, sheet in enumerate(workbook.sheets())
    )
    located = _locate_target_sheets(workbook, source_path.name)
    diagnostics: list[EnvelopeDiagnostic] = []
    parsed_sheets = tuple(
        _parse_sheet(
            workbook,
            workbook.sheet_by_index(sheet_index),
            filename=source_path.name,
            workbook_sha256=workbook_sha256,
            role=role,
            expected_name=expected_name,
            expected_headers=expected_headers,
            sheet_index=sheet_index,
            diagnostics=diagnostics,
        )
        for role, expected_name, expected_headers, sheet_index in located
    )
    return BiffXlsEnvelope(
        source_filename=source_path.name,
        snapshot_date=snapshot_date,
        workbook_sha256=workbook_sha256,
        byte_length=len(source_bytes),
        workbook_metadata={
            "biff_version": int(workbook.biff_version),
            "codepage": (
                int(workbook.codepage) if workbook.codepage is not None else None
            ),
            "datemode": int(workbook.datemode),
            "encoding": (
                str(workbook.encoding) if workbook.encoding is not None else None
            ),
        },
        workbook_sheets=workbook_sheets,
        sheets=parsed_sheets,
        diagnostics=tuple(diagnostics),
    )


def _snapshot_date(filename: str) -> str:
    match = _DATE_FROM_FILENAME.fullmatch(filename)
    if match is None:
        raise BiffXlsParseError(
            "INVALID_SNAPSHOT_FILENAME",
            "filename must end in one valid YYYYMMDD date followed by .xls",
            filename=filename,
        )
    raw_date = match.group("date")
    try:
        parsed = date(int(raw_date[:4]), int(raw_date[4:6]), int(raw_date[6:]))
    except ValueError as error:
        raise BiffXlsParseError(
            "INVALID_SNAPSHOT_DATE",
            f"invalid calendar date {raw_date!r}",
            filename=filename,
        ) from error
    return parsed.isoformat()


def _locate_target_sheets(
    workbook: xlrd.book.Book, filename: str
) -> tuple[tuple[str, str, tuple[str, ...], int], ...]:
    located: list[tuple[str, str, tuple[str, ...], int]] = []
    for role, exact_name, headers in _SHEET_SPECS:
        normalized_name = exact_name.strip().casefold()
        candidates = [
            index
            for index, sheet in enumerate(workbook.sheets())
            if sheet.name.strip().casefold() == normalized_name
        ]
        if not candidates:
            raise BiffXlsParseError(
                "MISSING_REQUIRED_SHEET",
                f"required exact sheet {exact_name!r} is absent",
                filename=filename,
            )
        if len(candidates) != 1:
            names = [workbook.sheet_by_index(index).name for index in candidates]
            raise BiffXlsParseError(
                "AMBIGUOUS_REQUIRED_SHEET",
                f"multiple sheets normalize to role {role}: {names!r}",
                filename=filename,
            )
        index = candidates[0]
        sheet = workbook.sheet_by_index(index)
        if sheet.name != exact_name:
            raise BiffXlsParseError(
                "UNSUPPORTED_SHEET_NAME_VARIANT",
                f"expected exact sheet name {exact_name!r}, found {sheet.name!r}",
                filename=filename,
                sheet=sheet.name,
            )
        if sheet.visibility != 0:
            raise BiffXlsParseError(
                "REQUIRED_SHEET_NOT_VISIBLE",
                f"required sheet visibility is {_visibility_name(sheet.visibility)!r}",
                filename=filename,
                sheet=sheet.name,
            )
        located.append((role, exact_name, headers, index))
    return tuple(sorted(located, key=lambda item: item[3]))


def _parse_sheet(
    workbook: xlrd.book.Book,
    sheet: xlrd.sheet.Sheet,
    *,
    filename: str,
    workbook_sha256: str,
    role: str,
    expected_name: str,
    expected_headers: tuple[str, ...],
    sheet_index: int,
    diagnostics: list[EnvelopeDiagnostic],
) -> ParsedSheet:
    candidates = _header_candidates(sheet, expected_headers)
    if not candidates:
        raise BiffXlsParseError(
            "MISSING_OR_INVALID_HEADER",
            f"exact ordered header signature was not found: {expected_headers!r}",
            filename=filename,
            sheet=sheet.name,
        )
    if len(candidates) != 1:
        coordinates = [
            f"{_column_label(column + 1)}{row + 1}" for row, column in candidates
        ]
        raise BiffXlsParseError(
            "AMBIGUOUS_HEADER",
            f"header signature occurs more than once at {coordinates!r}",
            filename=filename,
            sheet=sheet.name,
        )
    header_row_index, start_column_index = candidates[0]
    _reject_extra_header_values(
        sheet,
        header_row_index,
        start_column_index,
        len(expected_headers),
        filename,
    )
    headers = tuple(
        _source_cell(
            workbook,
            sheet,
            header_row_index,
            column_index,
            filename=filename,
        )
        for column_index in range(
            start_column_index, start_column_index + len(expected_headers)
        )
    )
    rows: list[SourceRow] = []
    for row_index in range(header_row_index + 1, sheet.nrows):
        _reject_extra_row_values(
            sheet,
            row_index,
            start_column_index,
            len(expected_headers),
            filename,
        )
        source_cells = tuple(
            _source_cell(
                workbook,
                sheet,
                row_index,
                column_index,
                filename=filename,
            )
            for column_index in range(
                start_column_index, start_column_index + len(expected_headers)
            )
        )
        if not any(_has_source_value(cell) for cell in source_cells):
            continue
        source_row = row_index + 1
        source_reference = (
            f"BIFF_XLS_V{CONTRACT_VERSION}:{workbook_sha256}:{sheet_index}:{source_row}"
        )
        row_payload = {
            "cells": [cell.to_dict() for cell in source_cells],
            "occurrence_index": len(rows) + 1,
            "source_reference": source_reference,
            "source_row": source_row,
        }
        source_row_item = SourceRow(
            occurrence_index=len(rows) + 1,
            source_row=source_row,
            source_reference=source_reference,
            cells=source_cells,
            row_fingerprint=canonical_fingerprint(row_payload),
        )
        rows.append(source_row_item)
        _append_anomaly_diagnostics(
            diagnostics,
            role=role,
            sheet_name=expected_name,
            headers=expected_headers,
            cells=source_cells,
        )

    if not rows:
        raise BiffXlsParseError(
            "NO_APPLICABLE_ROWS",
            "sheet contains no non-empty data rows after its header",
            filename=filename,
            sheet=sheet.name,
        )
    merged_ranges = tuple(
        {
            "first_column": first_column + 1,
            "first_row": first_row + 1,
            "last_column": last_column,
            "last_row": last_row,
        }
        for first_row, last_row, first_column, last_column in sheet.merged_cells
    )
    base = {
        "exact_name": expected_name,
        "header_row": header_row_index + 1,
        "header_start_column": start_column_index + 1,
        "headers": [header.to_dict() for header in headers],
        "merged_ranges": list(merged_ranges),
        "role": role,
        "rows": [row.to_dict() for row in rows],
        "sheet_index": sheet_index,
        "visibility": _visibility_name(sheet.visibility),
        "visibility_code": int(sheet.visibility),
    }
    return ParsedSheet(
        role=role,
        exact_name=expected_name,
        sheet_index=sheet_index,
        visibility_code=int(sheet.visibility),
        visibility=_visibility_name(sheet.visibility),
        header_row=header_row_index + 1,
        header_start_column=start_column_index + 1,
        headers=headers,
        merged_ranges=merged_ranges,
        rows=tuple(rows),
        sheet_fingerprint=canonical_fingerprint(base),
    )


def _header_candidates(
    sheet: xlrd.sheet.Sheet, expected_headers: tuple[str, ...]
) -> list[tuple[int, int]]:
    candidates: list[tuple[int, int]] = []
    width = len(expected_headers)
    for row_index in range(sheet.nrows):
        for start_column in range(max(0, sheet.ncols - width + 1)):
            actual = tuple(
                _exact_text(sheet.cell(row_index, column_index))
                for column_index in range(start_column, start_column + width)
            )
            if actual == expected_headers:
                candidates.append((row_index, start_column))
    return candidates


def _reject_extra_header_values(
    sheet: xlrd.sheet.Sheet,
    row_index: int,
    start_column: int,
    width: int,
    filename: str,
) -> None:
    allowed = range(start_column, start_column + width)
    for column_index in range(sheet.ncols):
        if column_index not in allowed and _xlrd_cell_has_value(
            sheet.cell(row_index, column_index)
        ):
            raise BiffXlsParseError(
                "UNEXPECTED_HEADER_CELL",
                "non-empty cell lies outside the supported header signature",
                filename=filename,
                sheet=sheet.name,
                row=row_index + 1,
                column=column_index + 1,
            )


def _reject_extra_row_values(
    sheet: xlrd.sheet.Sheet,
    row_index: int,
    start_column: int,
    width: int,
    filename: str,
) -> None:
    allowed = range(start_column, start_column + width)
    for column_index in range(sheet.ncols):
        if column_index not in allowed and _xlrd_cell_has_value(
            sheet.cell(row_index, column_index)
        ):
            raise BiffXlsParseError(
                "UNSUPPORTED_DATA_OUTSIDE_HEADER",
                "non-empty data cell lies outside the supported header columns",
                filename=filename,
                sheet=sheet.name,
                row=row_index + 1,
                column=column_index + 1,
            )


def _source_cell(
    workbook: xlrd.book.Book,
    sheet: xlrd.sheet.Sheet,
    row_index: int,
    column_index: int,
    *,
    filename: str,
) -> SourceCell:
    cell = sheet.cell(row_index, column_index)
    cell_type = cast(CellKind | None, _CELL_KIND.get(cell.ctype))
    if cell_type is None:
        raise BiffXlsParseError(
            "UNSUPPORTED_CELL_TYPE",
            f"xlrd returned unknown BIFF cell type {cell.ctype}",
            filename=filename,
            sheet=sheet.name,
            row=row_index + 1,
            column=column_index + 1,
        )
    raw_value: JsonScalar
    error_text: str | None = None
    if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
        raw_value = None
    elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
        raw_value = bool(cell.value)
    elif cell.ctype == xlrd.XL_CELL_ERROR:
        raw_value = int(cell.value)
        error_text = xlrd.error_text_from_code.get(int(cell.value), "#UNKNOWN!")
    elif cell.ctype in (xlrd.XL_CELL_NUMBER, xlrd.XL_CELL_DATE):
        raw_value = float(cell.value)
    else:
        raw_value = str(cell.value)
    if cell.xf_index is None:
        raise BiffXlsParseError(
            "UNRESOLVED_NUMBER_FORMAT",
            "xlrd did not expose an XF index despite formatting mode",
            filename=filename,
            sheet=sheet.name,
            row=row_index + 1,
            column=column_index + 1,
        )
    xf_index = int(cell.xf_index)
    try:
        format_key = int(workbook.xf_list[xf_index].format_key)
        number_format = str(workbook.format_map[format_key].format_str)
    except (IndexError, KeyError, AttributeError) as error:
        raise BiffXlsParseError(
            "UNRESOLVED_NUMBER_FORMAT",
            f"cannot resolve XF {xf_index}: {error}",
            filename=filename,
            sheet=sheet.name,
            row=row_index + 1,
            column=column_index + 1,
        ) from error
    return SourceCell(
        source_row=row_index + 1,
        source_column=column_index + 1,
        coordinate=f"{_column_label(column_index + 1)}{row_index + 1}",
        cell_type=cell_type,
        biff_type_code=int(cell.ctype),
        raw_value=raw_value,
        xf_index=xf_index,
        format_key=format_key,
        number_format=number_format,
        error_text=error_text,
    )


def _append_anomaly_diagnostics(
    diagnostics: list[EnvelopeDiagnostic],
    *,
    role: str,
    sheet_name: str,
    headers: tuple[str, ...],
    cells: tuple[SourceCell, ...],
) -> None:
    for header, expected, code, message in (
        (
            "Devizakockázat",
            _EXPECTED_CURRENCY_RISK,
            "ANOMALOUS_CURRENCY_RISK_VALUE",
            "value is outside the observed source vocabulary; preserved unchanged",
        ),
        (
            "Fenntarthatóság",
            _EXPECTED_SUSTAINABILITY,
            "ANOMALOUS_SUSTAINABILITY_VALUE",
            "value is outside the observed sustainability vocabulary; preserved unchanged",
        ),
    ):
        index = headers.index(header)
        cell = cells[index]
        if cell.raw_value is None:
            continue
        normalized = (
            cell.raw_value.strip().casefold()
            if isinstance(cell.raw_value, str)
            else None
        )
        if normalized in expected:
            continue
        diagnostics.append(
            EnvelopeDiagnostic(
                severity="WARNING",
                code=code,
                message=message,
                role=role,
                sheet_name=sheet_name,
                source_row=cell.source_row,
                source_column=cell.source_column,
                coordinate=cell.coordinate,
                header=header,
                cell_type=cell.cell_type,
                raw_value=cell.raw_value,
            )
        )
    for header in METRIC_HEADERS:
        cell = cells[headers.index(header)]
        if cell.cell_type == "text" and cell.raw_value == "0":
            diagnostics.append(
                EnvelopeDiagnostic(
                    severity="WARNING",
                    code="TEXT_ZERO_METRIC_VALUE",
                    message="text zero is preserved and is not a numeric observation",
                    role=role,
                    sheet_name=sheet_name,
                    source_row=cell.source_row,
                    source_column=cell.source_column,
                    coordinate=cell.coordinate,
                    header=header,
                    cell_type=cell.cell_type,
                    raw_value=cell.raw_value,
                )
            )


def _has_source_value(cell: SourceCell) -> bool:
    return cell.cell_type not in {"empty", "blank"} and not (
        cell.cell_type == "text" and cell.raw_value == ""
    )


def _xlrd_cell_has_value(cell: xlrd.sheet.Cell) -> bool:
    return cell.ctype not in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK) and not (
        cell.ctype == xlrd.XL_CELL_TEXT and cell.value == ""
    )


def _exact_text(cell: xlrd.sheet.Cell) -> str | None:
    return str(cell.value) if cell.ctype == xlrd.XL_CELL_TEXT else None


def _visibility_name(code: int) -> str:
    return _VISIBILITY.get(int(code), f"unknown:{int(code)}")


def _column_label(one_based_column: int) -> str:
    label = ""
    column = one_based_column
    while column:
        column, remainder = divmod(column - 1, 26)
        label = chr(ord("A") + remainder) + label
    return label
