"""Offline MNB/KELER weekly OTC transaction-price evidence.

These observations are period-aggregated transaction prices, deliberately kept
separate from the portfolio NAV types in :mod:`portfolio_advisor.history.models`.
They cannot be used as daily NAVs or return-series checkpoints.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from pathlib import Path
from statistics import median

MNB_OTC_SOURCE = "mnb_otc"
OTC_WEEKLY_TRANSACTION_AVERAGE = "OTC_WEEKLY_TRANSACTION_AVERAGE"
WEEKLY_OTC_AGGREGATE = "WEEKLY_OTC_AGGREGATE"
MNB_OTC_TABLE = "mnb_otc_observations"
TARGET_HU_ISIN = "HU0000554795"
TARGET_HU_NAME = "K250604 Egyéves Magyar Állampapír"
TARGET_HU_SECTION = "Egyéves Magyar Állampapír"

REQUIRED_HEADERS = (
    "Rövid név",
    "ISIN azonosító",
    "Névérték (e Ft)",
    "Vételár (e Ft)",
    "Átlagár/árfolyam",
    "Minimális",
    "Maximális",
    "Tételszám",
)
SECTION_TABLE_HEADERS = REQUIRED_HEADERS[1:]
ISIN_PATTERN = re.compile(r"\b[A-Z]{2}[A-Z0-9]{9}[0-9]\b")
OTC_PRICE_QUANTUM = Decimal("0.000001")


class MnbOtcError(RuntimeError):
    """MNB OTC evidence is malformed or conflicts with persisted provenance."""


@dataclass(frozen=True, slots=True)
class MnbOtcObservation:
    """One weekly MNB/KELER OTC transaction-price aggregate, not a NAV."""

    isin: str
    instrument_name: str
    currency: str
    period_start: date
    period_end: date
    nominal_value_huf_thousand: Decimal
    purchase_value_huf_thousand: Decimal
    average_price: Decimal
    minimum_price: Decimal
    maximum_price: Decimal
    transaction_count: int
    source_document: str
    source_document_hash: str
    source: str = MNB_OTC_SOURCE
    price_type: str = OTC_WEEKLY_TRANSACTION_AVERAGE
    frequency: str = WEEKLY_OTC_AGGREGATE

    def __post_init__(self) -> None:
        normalized_isin = self.isin.strip().upper()
        if (
            self.isin != normalized_isin
            or re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", normalized_isin) is None
        ):
            raise MnbOtcError(f"Invalid exact ISIN: {self.isin!r}")
        if not self.instrument_name.strip():
            raise MnbOtcError("MNB OTC instrument name is required")
        if self.currency.strip().upper() != "HUF":
            raise MnbOtcError("MNB OTC observations must retain HUF currency")
        if self.period_end < self.period_start:
            raise MnbOtcError("MNB OTC period_end must not precede period_start")
        if self.source != MNB_OTC_SOURCE:
            raise MnbOtcError("MNB OTC source identity must be mnb_otc")
        if self.price_type != OTC_WEEKLY_TRANSACTION_AVERAGE:
            raise MnbOtcError("MNB OTC price_type is invalid")
        if self.frequency != WEEKLY_OTC_AGGREGATE:
            raise MnbOtcError("MNB OTC frequency is invalid")
        if (
            not self.source_document.strip()
            or re.fullmatch(r"[0-9a-f]{64}", self.source_document_hash.casefold())
            is None
        ):
            raise MnbOtcError("MNB OTC source document and SHA-256 hash are required")
        for label, value in (
            ("nominal_value_huf_thousand", self.nominal_value_huf_thousand),
            ("purchase_value_huf_thousand", self.purchase_value_huf_thousand),
            ("average_price", self.average_price),
            ("minimum_price", self.minimum_price),
            ("maximum_price", self.maximum_price),
        ):
            if not value.is_finite() or value <= 0:
                raise MnbOtcError(f"MNB OTC {label} must be finite and positive")
        if not self.minimum_price <= self.average_price <= self.maximum_price:
            raise MnbOtcError("MNB OTC minimum <= average <= maximum must hold")
        if isinstance(self.transaction_count, bool) or self.transaction_count <= 0:
            raise MnbOtcError("MNB OTC transaction_count must be positive")

    def identity_key(self) -> tuple[str, str, date, date]:
        return (self.source, self.isin, self.period_start, self.period_end)

    def persisted_values(self) -> tuple[object, ...]:
        """Values that must agree for an idempotent unique-period re-import."""
        return (
            self.instrument_name,
            self.currency,
            decimal_text(self.nominal_value_huf_thousand),
            decimal_text(self.purchase_value_huf_thousand),
            decimal_text(self.average_price),
            decimal_text(self.minimum_price),
            decimal_text(self.maximum_price),
            self.transaction_count,
            self.price_type,
            self.frequency,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "isin": self.isin,
            "instrument_name": self.instrument_name,
            "currency": self.currency,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "nominal_value_huf_thousand": decimal_text(self.nominal_value_huf_thousand),
            "purchase_value_huf_thousand": decimal_text(
                self.purchase_value_huf_thousand
            ),
            "average_price": decimal_text(self.average_price),
            "minimum_price": decimal_text(self.minimum_price),
            "maximum_price": decimal_text(self.maximum_price),
            "transaction_count": self.transaction_count,
            "price_type": self.price_type,
            "frequency": self.frequency,
            "source_document": self.source_document,
            "source_document_hash": self.source_document_hash,
            "nav_equivalent": False,
            "backtest_return_series_approved": False,
        }


def decimal_text(value: Decimal) -> str:
    return format(value, "f")


def parse_decimal(value: str, field: str) -> Decimal:
    normalized = value.replace("\u00a0", "").replace(" ", "").strip()
    if normalized.count(",") == 1 and normalized.count(".") == 0:
        normalized = normalized.replace(",", ".")
    elif normalized.count(",") and normalized.count("."):
        decimal_marker = max(normalized.rfind(","), normalized.rfind("."))
        normalized = (
            normalized[:decimal_marker].replace(",", "").replace(".", "")
            + "."
            + normalized[decimal_marker + 1 :].replace(",", "").replace(".", "")
        )
    try:
        parsed = Decimal(normalized)
    except (InvalidOperation, ValueError) as exc:
        raise MnbOtcError(f"MNB OTC {field} is not a decimal: {value!r}") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise MnbOtcError(f"MNB OTC {field} must be finite and positive: {value!r}")
    return parsed


def parse_transaction_count(value: str) -> int:
    try:
        parsed = parse_decimal(value, "transaction count")
    except MnbOtcError as exc:
        raise MnbOtcError(
            f"MNB OTC transaction count must be positive: {value!r}"
        ) from exc
    if parsed != parsed.to_integral_value():
        raise MnbOtcError(f"MNB OTC transaction count must be positive: {value!r}")
    return int(parsed)


def parse_otc_price(value: str, field: str) -> Decimal:
    """Normalize a reported OTC price to the report's six-decimal price scale."""
    try:
        return parse_decimal(value, field).quantize(OTC_PRICE_QUANTUM)
    except InvalidOperation as exc:
        raise MnbOtcError(
            f"MNB OTC {field} has unsupported precision: {value!r}"
        ) from exc


def parse_period(value: str) -> tuple[date, date]:
    matches = re.findall(r"20\d{2}[./-]\d{2}[./-]\d{2}", value)
    if len(matches) != 2:
        raise MnbOtcError(f"MNB OTC reporting period is ambiguous: {value!r}")
    start_text, end_text = (
        item.replace(".", "-").replace("/", "-") for item in matches
    )
    try:
        period_start = date.fromisoformat(start_text)
        period_end = date.fromisoformat(end_text)
    except ValueError as exc:
        raise MnbOtcError(f"MNB OTC reporting period is invalid: {value!r}") from exc
    if period_end < period_start:
        raise MnbOtcError("MNB OTC reporting period ends before it starts")
    return period_start, period_end


def extract_pdf_text(path: Path) -> str:
    """Extract deterministic text from an already-downloaded PDF via Poppler."""
    if not path.is_file() or path.suffix.casefold() != ".pdf":
        raise MnbOtcError(f"MNB OTC report is not a PDF file: {path}")
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise MnbOtcError(f"MNB OTC PDF text extraction failed: {path}") from exc
    if not result.stdout.strip():
        raise MnbOtcError(
            f"MNB OTC PDF contains no extractable structured text: {path}"
        )
    return result.stdout


def document_hash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise MnbOtcError(f"Unable to read MNB OTC report: {path}") from exc


def _reporting_period_from_text(text: str) -> tuple[date, date]:
    matches = re.findall(
        r"(20\d{2}[./-]\d{2}[./-]\d{2}\s*(?:->|–|to|-)\s*20\d{2}[./-]\d{2}[./-]\d{2})",
        text,
    )
    periods = {parse_period(match) for match in matches}
    if len(periods) != 1:
        raise MnbOtcError(
            "MNB OTC report must contain exactly one unambiguous reporting period"
        )
    return next(iter(periods))


def reporting_period_from_text(text: str) -> tuple[date, date]:
    """Return one unambiguous MNB report period without parsing an observation."""
    return _reporting_period_from_text(text)


def _require_headers(text: str) -> None:
    missing = [header for header in REQUIRED_HEADERS if header not in text]
    if missing:
        raise MnbOtcError(
            "MNB OTC report is missing required columns: " + ", ".join(missing)
        )


def normalize_layout_whitespace(value: str) -> str:
    """Normalize PDF layout whitespace without deleting words or columns."""
    return " ".join(value.replace("\f", " ").split())


def _is_section_table_header(line: str) -> bool:
    normalized = normalize_layout_whitespace(line)
    return all(header in normalized for header in SECTION_TABLE_HEADERS)


def _section_label_candidates(
    lines: Sequence[str], header_index: int
) -> tuple[str, ...]:
    """Recover a section title that Poppler may split around the table columns."""
    header_line = lines[header_index]
    isin_position = header_line.find("ISIN azonosító")
    if isin_position < 0:
        raise MnbOtcError("MNB OTC section header has no ISIN column")
    prefix = normalize_layout_whitespace(header_line[:isin_position])

    following: list[str] = []
    for line in lines[header_index + 1 :]:
        normalized = normalize_layout_whitespace(line)
        if not normalized:
            break
        if _is_section_table_header(line) or ISIN_PATTERN.search(normalized):
            break
        following.append(normalized)
    same_header = normalize_layout_whitespace(" ".join([prefix, *following]))

    preceding: list[str] = []
    for line in reversed(lines[:header_index]):
        normalized = normalize_layout_whitespace(line)
        if not normalized:
            break
        preceding.append(normalized)
    preceding.reverse()
    before_header = normalize_layout_whitespace(" ".join([*preceding, prefix]))
    return tuple(candidate for candidate in (same_header, before_header) if candidate)


def _target_section_bounds(lines: Sequence[str]) -> tuple[int, int]:
    """Return the exact table-body bounds of the validated target section."""
    header_indexes = [
        index for index, line in enumerate(lines) if _is_section_table_header(line)
    ]
    target_indexes = [
        index
        for index in header_indexes
        if TARGET_HU_SECTION in _section_label_candidates(lines, index)
    ]
    if len(target_indexes) != 1:
        raise MnbOtcError(
            "MNB OTC target row is not in one unambiguous Egyéves Magyar Állampapír section"
        )
    target_header = target_indexes[0]
    next_headers = [index for index in header_indexes if index > target_header]
    return target_header + 1, next_headers[0] if next_headers else len(lines)


def _row_from_lines(
    lines: Sequence[str],
    row_index: int,
    source_document: str,
    source_hash: str,
    period: tuple[date, date],
) -> MnbOtcObservation | None:
    line = lines[row_index]
    if TARGET_HU_ISIN not in line:
        return None
    normalized = normalize_layout_whitespace(line)
    pattern = re.compile(
        rf"^(?P<name>.+?)\s+{TARGET_HU_ISIN}\s+"
        r"(?P<nominal>[0-9 .,:]+)\s+"
        r"(?P<purchase>[0-9 .,:]+)\s+"
        r"(?P<average>[0-9 .,:]+)\s+"
        r"(?P<minimum>[0-9 .,:]+)\s+"
        r"(?P<maximum>[0-9 .,:]+)\s+"
        r"(?P<count>[0-9 .,:]+)$"
    )
    match = pattern.match(normalized)
    if match is None:
        raise MnbOtcError("MNB OTC exact-ISIN row is malformed")
    name_parts = [match.group("name")]
    for continuation in lines[row_index + 1 :]:
        continuation_text = normalize_layout_whitespace(continuation)
        if not continuation_text:
            break
        if _is_section_table_header(continuation) or ISIN_PATTERN.search(
            continuation_text
        ):
            break
        name_parts.append(continuation_text)
    instrument_name = normalize_layout_whitespace(" ".join(name_parts))
    if instrument_name != TARGET_HU_NAME:
        raise MnbOtcError("MNB OTC exact ISIN has an unexpected instrument name")
    period_start, period_end = period
    return MnbOtcObservation(
        isin=TARGET_HU_ISIN,
        instrument_name=instrument_name,
        currency="HUF",
        period_start=period_start,
        period_end=period_end,
        nominal_value_huf_thousand=parse_decimal(
            match.group("nominal"), "nominal value"
        ),
        purchase_value_huf_thousand=parse_decimal(
            match.group("purchase"), "purchase value"
        ),
        average_price=parse_otc_price(match.group("average"), "average price"),
        minimum_price=parse_otc_price(match.group("minimum"), "minimum price"),
        maximum_price=parse_otc_price(match.group("maximum"), "maximum price"),
        transaction_count=parse_transaction_count(match.group("count")),
        source_document=source_document,
        source_document_hash=source_hash,
    )


def parse_mnb_otc_report_text(
    text: str, source_document: str, source_hash: str
) -> MnbOtcObservation:
    """Parse one exact HU0000554795 row from structured MNB/KELER report text."""
    _require_headers(text)
    period = _reporting_period_from_text(text)
    lines = text.splitlines()
    section_start, section_end = _target_section_bounds(lines)
    target_rows = [
        item
        for index in range(section_start, section_end)
        if (item := _row_from_lines(lines, index, source_document, source_hash, period))
        is not None
    ]
    if not target_rows:
        raise MnbOtcError("MNB OTC report contains no exact HU0000554795 row")
    first = target_rows[0]
    if any(
        item.persisted_values() != first.persisted_values() for item in target_rows[1:]
    ):
        raise MnbOtcError(
            "MNB OTC report contains conflicting duplicate exact-ISIN rows"
        )
    return first


def parse_mnb_otc_pdf(path: Path) -> MnbOtcObservation:
    """Parse an already-downloaded MNB/KELER weekly report without network I/O."""
    return parse_mnb_otc_report_text(
        extract_pdf_text(path), str(path), document_hash(path)
    )


class MnbOtcRepository:
    """Dedicated SQLite storage for OTC aggregates, never a NAV table."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def ensure_schema(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {MNB_OTC_TABLE} (
                    source TEXT NOT NULL,
                    isin TEXT NOT NULL,
                    instrument_name TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    period_start TEXT NOT NULL,
                    period_end TEXT NOT NULL,
                    nominal_value_huf_thousand TEXT NOT NULL,
                    purchase_value_huf_thousand TEXT NOT NULL,
                    average_price TEXT NOT NULL,
                    minimum_price TEXT NOT NULL,
                    maximum_price TEXT NOT NULL,
                    transaction_count INTEGER NOT NULL,
                    price_type TEXT NOT NULL,
                    frequency TEXT NOT NULL,
                    source_document TEXT NOT NULL,
                    source_document_hash TEXT NOT NULL,
                    PRIMARY KEY (source, isin, period_start, period_end)
                )
                """
            )

    def import_observation(self, observation: MnbOtcObservation) -> bool:
        """Insert once, accept exact re-imports, and reject value conflicts."""
        self.ensure_schema()
        with sqlite3.connect(self.database_path) as connection:
            existing = connection.execute(
                f"""
                SELECT *
                FROM {MNB_OTC_TABLE}
                WHERE source = ? AND isin = ? AND period_start = ? AND period_end = ?
                """,
                (
                    observation.source,
                    observation.isin,
                    observation.period_start.isoformat(),
                    observation.period_end.isoformat(),
                ),
            ).fetchone()
            if existing is not None:
                if (
                    self._from_row(existing).persisted_values()
                    != observation.persisted_values()
                ):
                    raise MnbOtcError(
                        "Conflicting persisted MNB OTC values for unique reporting period"
                    )
                return False
            connection.execute(
                f"""
                INSERT INTO {MNB_OTC_TABLE} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observation.source,
                    observation.isin,
                    observation.instrument_name,
                    observation.currency,
                    observation.period_start.isoformat(),
                    observation.period_end.isoformat(),
                    decimal_text(observation.nominal_value_huf_thousand),
                    decimal_text(observation.purchase_value_huf_thousand),
                    decimal_text(observation.average_price),
                    decimal_text(observation.minimum_price),
                    decimal_text(observation.maximum_price),
                    observation.transaction_count,
                    observation.price_type,
                    observation.frequency,
                    observation.source_document,
                    observation.source_document_hash,
                ),
            )
        return True

    def observations(self, isin: str | None = None) -> tuple[MnbOtcObservation, ...]:
        if not self.database_path.is_file():
            return ()
        with sqlite3.connect(self.database_path) as connection:
            table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
                (MNB_OTC_TABLE,),
            ).fetchone()
            if table is None:
                return ()
            query = f"SELECT * FROM {MNB_OTC_TABLE}"
            parameters: tuple[str, ...] = ()
            if isin is not None:
                query += " WHERE isin = ?"
                parameters = (isin.strip().upper(),)
            query += " ORDER BY period_start, period_end"
            rows = connection.execute(query, parameters).fetchall()
        return tuple(self._from_row(row) for row in rows)

    @staticmethod
    def _from_row(row: sqlite3.Row | tuple[object, ...]) -> MnbOtcObservation:
        return MnbOtcObservation(
            source=str(row[0]),
            isin=str(row[1]),
            instrument_name=str(row[2]),
            currency=str(row[3]),
            period_start=date.fromisoformat(str(row[4])),
            period_end=date.fromisoformat(str(row[5])),
            nominal_value_huf_thousand=parse_decimal(
                str(row[6]), "persisted nominal value"
            ),
            purchase_value_huf_thousand=parse_decimal(
                str(row[7]), "persisted purchase value"
            ),
            average_price=parse_otc_price(str(row[8]), "persisted average price"),
            minimum_price=parse_otc_price(str(row[9]), "persisted minimum price"),
            maximum_price=parse_otc_price(str(row[10]), "persisted maximum price"),
            transaction_count=parse_transaction_count(str(row[11])),
            price_type=str(row[12]),
            frequency=str(row[13]),
            source_document=str(row[14]),
            source_document_hash=str(row[15]),
        )


def quality_summary(observations: Sequence[MnbOtcObservation]) -> dict[str, object]:
    """Describe weekly evidence quality without implying NAV coverage or approval."""
    if not observations:
        return {
            "observation_count": 0,
            "distinct_reporting_period_count": 0,
            "first_period": None,
            "last_period": None,
            "maximum_gap_days": None,
            "median_gap_days": None,
            "zero_transaction_period_count": 0,
            "duplicate_conflict_count": 0,
            "observed_periods": [],
            "minimum_average_price": None,
            "maximum_average_price": None,
            "median_average_price": None,
            "total_transaction_count": 0,
            "minimum_transaction_count": None,
            "maximum_transaction_count": None,
            "median_transaction_count": None,
        }
    ordered = sorted(
        observations, key=lambda item: (item.period_start, item.period_end)
    )
    gaps = [
        max(0, (current.period_start - previous.period_end).days - 1)
        for previous, current in pairwise(ordered)
    ]
    return {
        "observation_count": len(ordered),
        "distinct_reporting_period_count": len(
            {(item.period_start, item.period_end) for item in ordered}
        ),
        "first_period": {
            "start": ordered[0].period_start.isoformat(),
            "end": ordered[0].period_end.isoformat(),
        },
        "last_period": {
            "start": ordered[-1].period_start.isoformat(),
            "end": ordered[-1].period_end.isoformat(),
        },
        "maximum_gap_days": max(gaps) if gaps else 0,
        "median_gap_days": median(gaps) if gaps else 0,
        "zero_transaction_period_count": sum(
            item.transaction_count == 0 for item in ordered
        ),
        "duplicate_conflict_count": 0,
        "observed_periods": [
            {"start": item.period_start.isoformat(), "end": item.period_end.isoformat()}
            for item in ordered
        ],
        "minimum_average_price": decimal_text(
            min(item.average_price for item in ordered)
        ),
        "maximum_average_price": decimal_text(
            max(item.average_price for item in ordered)
        ),
        "median_average_price": decimal_text(
            median([item.average_price for item in ordered])
        ),
        "total_transaction_count": sum(item.transaction_count for item in ordered),
        "minimum_transaction_count": min(item.transaction_count for item in ordered),
        "maximum_transaction_count": max(item.transaction_count for item in ordered),
        "median_transaction_count": _median_transaction_count(
            [item.transaction_count for item in ordered]
        ),
    }


def _median_transaction_count(values: Sequence[int]) -> str:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return str(ordered[middle])
    return decimal_text(Decimal(ordered[middle - 1] + ordered[middle]) / Decimal(2))


def import_pdf_reports(
    repository: MnbOtcRepository, paths: Iterable[Path]
) -> tuple[int, int]:
    """Parse/import a deterministic directory listing of manually downloaded PDFs."""
    imported = 0
    skipped = 0
    for path in sorted(paths):
        observation = parse_mnb_otc_pdf(path)
        if repository.import_observation(observation):
            imported += 1
        else:
            skipped += 1
    return imported, skipped


def utc_import_label() -> str:
    """Retained for audit callers that need an explicit local generation timestamp."""
    return datetime.now(UTC).isoformat()
