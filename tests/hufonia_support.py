"""Synthetic MNB-shaped HUFONIA fixtures; this module never contacts a network."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import xlrd  # type: ignore[import-untyped]

from portfolio_advisor.canonical import canonical_fingerprint
from portfolio_advisor.objectives import (
    CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT,
    load_capital_defensive_construction_policy,
)
from portfolio_advisor.reference_rates import hufonia as hufonia_module
from portfolio_advisor.reference_rates.contracts import canonical_request_parameters
from portfolio_advisor.reference_rates.hufonia import (
    HUFONIA_MACHINE_URL,
    HUFONIA_REQUEST_PARAMETERS,
    HufoniaAcquisitionReceipt,
    _BiffNumericCell,
    receipt_json,
)

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_RAW = b"SYNTHETIC_MNB_HUFONIA_BIFF8_FIXTURE_V1"


@dataclass(frozen=True)
class _Cell:
    xf_index: int


@dataclass(frozen=True)
class _Format:
    format_str: str


@dataclass(frozen=True)
class _Xf:
    format_key: int


class SyntheticSheet:
    def __init__(
        self,
        rows: list[list[object]],
        styles: dict[tuple[int, int], int] | None = None,
    ) -> None:
        self.rows = rows
        self.styles = styles or {}
        self.nrows = len(rows)
        self.ncols = max(len(row) for row in rows)
        self.visibility = 0

    def row_values(self, row: int) -> list[object]:
        return self.rows[row] + [""] * (self.ncols - len(self.rows[row]))

    def cell_value(self, row: int, column: int) -> object:
        values = self.row_values(row)
        return values[column]

    def cell_type(self, row: int, column: int) -> int:
        value = self.cell_value(row, column)
        if value == "" or value is None:
            return xlrd.XL_CELL_EMPTY
        if isinstance(value, str):
            return xlrd.XL_CELL_TEXT
        return xlrd.XL_CELL_NUMBER

    def cell(self, row: int, column: int) -> _Cell:
        return _Cell(self.styles.get((row, column), 0))


class SyntheticBook:
    biff_version = 80
    datemode = 0

    def __init__(self, sheets: dict[str, SyntheticSheet]) -> None:
        self.sheets = sheets
        self.format_map = {
            0: _Format("General"),
            1: _Format("0.00"),
            2: _Format("0.000"),
            3: _Format("General"),
        }
        self.xf_list = [_Xf(0), _Xf(1), _Xf(2), _Xf(3)]

    def sheet_names(self) -> list[str]:
        return list(self.sheets)

    def sheet_by_name(self, name: str) -> SyntheticSheet:
        return self.sheets[name]


Mutation = Callable[
    [SyntheticBook, dict[str, dict[tuple[int, int], _BiffNumericCell]]], None
]


def install_synthetic_workbook(
    monkeypatch: pytest.MonkeyPatch,
    mutation: Mutation | None = None,
) -> tuple[SyntheticBook, dict[str, dict[tuple[int, int], _BiffNumericCell]]]:
    """Replace only workbook decoding with a complete deterministic fake BIFF view."""
    book, numeric = synthetic_workbook()
    if mutation is not None:
        mutation(book, numeric)
    info_values = [book.sheet_by_name("info").cell_value(row, 0) for row in range(3)]
    monkeypatch.setattr(
        hufonia_module, "HUFONIA_INFO_FINGERPRINT", canonical_fingerprint(info_values)
    )
    monkeypatch.setattr(hufonia_module.xlrd, "open_workbook", lambda **_: book)
    monkeypatch.setattr(
        hufonia_module,
        "_biff_numeric_cells",
        lambda _: (numeric, tuple(book.sheet_names())),
    )
    return book, numeric


def synthetic_workbook(
) -> tuple[SyntheticBook, dict[str, dict[tuple[int, int], _BiffNumericCell]]]:
    sheets: dict[str, SyntheticSheet] = {}
    numeric_by_sheet: dict[str, dict[tuple[int, int], _BiffNumericCell]] = {}
    for sheet_name in hufonia_module._EXPECTED_SHEETS:
        if sheet_name == "info":
            sheets[sheet_name] = SyntheticSheet(
                [["Synthetic HUFONIA contract"], ["Unsecured HUF overnight"], ["MNB"]]
            )
            numeric_by_sheet[sheet_name] = {}
            continue
        year = int(sheet_name)
        headers = _headers(year)
        rows: list[list[object]] = [list(row) for row in headers]
        styles: dict[tuple[int, int], int] = {}
        numeric: dict[tuple[int, int], _BiffNumericCell] = {}
        for observation_date in _year_dates(year):
            row_index = len(rows)
            annotation = _annotation(observation_date)
            rate_text = observation_date == date(2023, 4, 26)
            rate: object = "17.880" if rate_text else 2.345
            row = [_serial(observation_date), rate, 1000]
            if annotation is not None:
                row.append(annotation)
            rows.append(row)
            numeric[(row_index, 0)] = _BiffNumericCell(
                Decimal(_serial(observation_date)), hufonia_module._BIFF_RK, 0
            )
            if not rate_text:
                precision = 1 if observation_date < date(2010, 9, 1) else 2
                if observation_date in hufonia_module._GENERAL_FORMAT_DATES:
                    precision = 3
                raw_rate = Decimal("2.345") if precision != 1 else Decimal("2.35")
                numeric[(row_index, 1)] = _BiffNumericCell(
                    raw_rate, hufonia_module._BIFF_NUMBER, precision
                )
                styles[(row_index, 1)] = precision
            numeric[(row_index, 2)] = _BiffNumericCell(
                Decimal(1000), hufonia_module._BIFF_RK, 0
            )
        if year == 2010:
            _append_numeric_row(rows, styles, numeric, date(2011, 1, 3))
        if year == 2026:
            _append_numeric_row(rows, styles, numeric, date(2026, 9, 1))
            _append_numeric_row(rows, styles, numeric, date(2026, 9, 2))
        if year == 2002:
            row_index = len(rows)
            rows.append(["", "", 30625])
            numeric[(row_index, 2)] = _BiffNumericCell(
                Decimal(30625), hufonia_module._BIFF_RK, 0
            )
        sheets[sheet_name] = SyntheticSheet(rows, styles)
        numeric_by_sheet[sheet_name] = numeric
    return SyntheticBook(sheets), numeric_by_sheet


def write_evidence(
    repository_root: Path,
    *,
    raw: bytes = SYNTHETIC_RAW,
    retrieval_timestamp: str = "2026-09-03T12:27:03+00:00",
) -> tuple[Path, Path, HufoniaAcquisitionReceipt]:
    digest = hashlib.sha256(raw).hexdigest()
    directory = repository_root / "data/raw/reference_rates/mnb/hufonia"
    directory.mkdir(parents=True, exist_ok=True)
    raw_path = directory / f"hufonia-{digest}.xls"
    raw_path.write_bytes(raw)
    receipt = HufoniaAcquisitionReceipt(
        receipt_schema_version=1,
        request_url=HUFONIA_MACHINE_URL,
        request_parameters=canonical_request_parameters(HUFONIA_REQUEST_PARAMETERS),
        effective_url=HUFONIA_MACHINE_URL,
        retrieval_timestamp=retrieval_timestamp,
        http_status=200,
        response_content_type="application/vnd.ms-excel",
        content_encoding="",
        content_length=len(raw),
        response_date="Thu, 03 Sep 2026 12:27:03 GMT",
        last_modified="Thu, 03 Sep 2026 08:30:29 GMT",
        etag=None,
        byte_count=len(raw),
        raw_artifact_reference=raw_path.relative_to(repository_root).as_posix(),
        raw_artifact_sha256=digest,
    )
    receipt_path = raw_path.with_suffix(".receipt.json")
    receipt_path.write_text(receipt_json(receipt), encoding="utf-8")
    return raw_path, receipt_path, receipt


def construction_policy():
    return load_capital_defensive_construction_policy(
        ROOT / CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT
    )


def _headers(year: int) -> tuple[tuple[str, str, str], ...]:
    if year >= 2010:
        return (
            ("Date", "HUFONIA", "Turnover (mio HUF)"),
            ("dátum", "HUFONIA", "forgalom (m Ft)"),
        )
    if year >= 2007:
        return (("dátum", "átlag", "forgalom (mFt)"),)
    return (("datum", "atlag", "forgalom (mFt)"),)


def _year_dates(year: int) -> list[date]:
    if year == 2026:
        start = date(year, 1, 1)
        return [start + timedelta(days=index) for index in range(243)]
    count = 249 if year % 2 == 0 else 250
    if year == 2002:
        start = date(year, 1, 2)
        return [start + timedelta(days=index) for index in range(count)]
    if year == 2006:
        start = date(year, 1, 1)
        return sorted(
            [start + timedelta(days=index) for index in range(count - 2)]
            + [date(2006, 11, 6), date(2006, 12, 4)]
        )
    if year == 2015:
        start = date(year, 1, 1)
        return (
            [start + timedelta(days=index) for index in range(224)]
            + [date(2015, 11, 19)]
            + [date(2015, 11, 20) + timedelta(days=index) for index in range(25)]
        )
    if year == 2016:
        start = date(year, 1, 1)
        return (
            [start + timedelta(days=index) for index in range(193)]
            + [date(2016, 10, 4)]
            + [date(2016, 10, 5) + timedelta(days=index) for index in range(55)]
        )
    start = date(year, 1, 1)
    return [start + timedelta(days=index) for index in range(count)]


def _append_numeric_row(
    rows: list[list[object]],
    styles: dict[tuple[int, int], int],
    numeric: dict[tuple[int, int], _BiffNumericCell],
    observation_date: date,
) -> None:
    row_index = len(rows)
    rows.append([_serial(observation_date), 2.345, 1000])
    numeric[(row_index, 0)] = _BiffNumericCell(
        Decimal(_serial(observation_date)), hufonia_module._BIFF_RK, 0
    )
    numeric[(row_index, 1)] = _BiffNumericCell(
        Decimal("2.345"), hufonia_module._BIFF_NUMBER, 2
    )
    numeric[(row_index, 2)] = _BiffNumericCell(
        Decimal(1000), hufonia_module._BIFF_RK, 0
    )
    styles[(row_index, 1)] = 2


def _serial(value: date) -> int:
    return (value - date(1899, 12, 30)).days


def _annotation(value: date) -> str | None:
    if value == date(2015, 11, 19):
        return hufonia_module._CORRECTION_NOTE
    if value == date(2016, 10, 4):
        return hufonia_module._DATE_BASIS_NOTE
    return None
