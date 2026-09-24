"""Generated BIFF-XLS fixtures for the parser-only Phase 3B.2 slice."""

from __future__ import annotations

from pathlib import Path

import xlwt  # type: ignore[import-untyped]

from portfolio_advisor.workbook_source.biff_xls import (
    MODEL_HEADERS,
    MODEL_SHEET_NAME,
    SHORTLIST_HEADERS,
    SHORTLIST_SHEET_NAME,
)


def write_biff_fixture(
    root: Path,
    *,
    filename: str = "Synthetic_Portfolios_20260115.xls",
    model_sheet_name: str = MODEL_SHEET_NAME,
    shortlist_sheet_name: str = SHORTLIST_SHEET_NAME,
    include_model: bool = True,
    include_shortlist: bool = True,
    duplicate_shortlist_variant: bool = False,
    hide_shortlist: bool = False,
    malformed_model_header: bool = False,
    repeat_model_header: bool = False,
    extra_model_data: bool = False,
    include_formula: bool = False,
) -> Path:
    """Generate a small workbook containing no retained/private evidence."""
    workbook = xlwt.Workbook(encoding="utf-8")
    if include_model:
        model = workbook.add_sheet(model_sheet_name, cell_overwrite_ok=True)
        _write_headers(model, MODEL_HEADERS, malformed=malformed_model_header)
        if repeat_model_header:
            _write_headers(model, MODEL_HEADERS, row=1)
            first_data_row = 2
        else:
            first_data_row = 1
        _write_model_rows(model, first_data_row, extra_model_data=extra_model_data)
    if include_shortlist:
        shortlist = workbook.add_sheet(shortlist_sheet_name, cell_overwrite_ok=True)
        shortlist.visibility = 1 if hide_shortlist else 0
        _write_headers(shortlist, SHORTLIST_HEADERS)
        _write_shortlist_rows(shortlist, include_formula=include_formula)
    if duplicate_shortlist_variant:
        duplicate = workbook.add_sheet("shortlist", cell_overwrite_ok=True)
        _write_headers(duplicate, SHORTLIST_HEADERS)
        _write_shortlist_rows(duplicate, include_formula=False)
    if not include_model and not include_shortlist and not duplicate_shortlist_variant:
        workbook.add_sheet("unrelated")
    path = root / filename
    workbook.save(str(path))
    return path


def _write_headers(
    sheet: object,
    headers: tuple[str, ...],
    *,
    row: int = 0,
    malformed: bool = False,
) -> None:
    for column, header in enumerate(headers):
        value = "Unexpected" if malformed and column == len(headers) - 1 else header
        sheet.write(row, column, value)  # type: ignore[attr-defined]


def _write_model_rows(sheet: object, first_row: int, *, extra_model_data: bool) -> None:
    percentage = xlwt.easyxf(num_format_str="0.00%")
    values: list[object] = [
        "PB Szintetikus",
        "Szintetikus termék",
        "IE00B7KFL990",
        50.0,
        "Kötvény",
        "Globál",
        "USD",
        2,
        "1: ESG-Minimum Standard",
        0.0,
        0.08,
        0.12,
        None,
        0.7,
        0.9,
        None,
        0.05,
        0.07,
        0.04,
        -0.2,
        -0.1,
    ]
    for offset in range(2):
        for column, value in enumerate(values):
            header = MODEL_HEADERS[column]
            if offset == 1 and header == "5yr":
                continue
            if offset == 1 and header == "YTD":
                value = None
            style = percentage if header == "YTD" else xlwt.Style.default_style
            sheet.write(first_row + offset, column, value, style)  # type: ignore[attr-defined]
    if extra_model_data:
        sheet.write(first_row, len(MODEL_HEADERS), "outside")  # type: ignore[attr-defined]


def _write_shortlist_rows(sheet: object, *, include_formula: bool) -> None:
    text_format = xlwt.easyxf(num_format_str="@")
    percentage = xlwt.easyxf(num_format_str="0.000%")
    rows: list[list[object]] = [
        [
            "Szintetikus shortlist egy",
            "IE00B7KFL990",
            "Kötvény-befektetési kategória",
            "Globál",
            "Befektetési alap",
            "USD",
            "Nincs fedezve",
            "Hosszú kötvény alap",
            "0",
            0.08,
            0.12,
            None,
            0.7,
            0.9,
            None,
            0.05,
            0.07,
            0.04,
            -0.2,
            -0.1,
        ],
        [
            "Szintetikus shortlist kettő",
            "IE00B84J9L26",
            "Részvény",
            "Fejl?d? piacok",
            "Befektetési alap",
            "EUR",
            "Fedezve",
            None,
            0.0,
            0.05,
            0.1,
            0.2,
            0.5,
            0.8,
            1.0,
            0.08,
            0.1,
            0.06,
            0.1,
            -0.15,
        ],
    ]
    for row_index, values in enumerate(rows, start=1):
        for column, value in enumerate(values):
            header = SHORTLIST_HEADERS[column]
            style = xlwt.Style.default_style
            if row_index == 1 and header == "YTD":
                style = text_format
            if row_index == 2 and header == "YTD":
                style = percentage
            sheet.write(row_index, column, value, style)  # type: ignore[attr-defined]
    info_column = SHORTLIST_HEADERS.index("Info. ratio")
    sheet.row(2).set_cell_error(info_column, 15)  # type: ignore[attr-defined]
    if include_formula:
        one_year = SHORTLIST_HEADERS.index("1yr")
        sheet.write(1, one_year, xlwt.Formula("1+1"))  # type: ignore[attr-defined]
