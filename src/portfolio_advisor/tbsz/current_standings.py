"""One-time, provenance-backed current TBSZ standings SQLite read model."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

CURRENT_STANDINGS_SCHEMA_VERSION = 1
EXPECTED_SOURCE_FILENAMES = (
    "523 - George - george.ersteinvestment.hu.pdf",
    "524 - George - george.ersteinvestment.hu.pdf",
    "525 - George - george.ersteinvestment.hu.pdf",
    "526 - George - george.ersteinvestment.hu.pdf",
    "527 - George - george.ersteinvestment.hu.pdf",
    "528 - George - george.ersteinvestment.hu.pdf",
    "529 - George - george.ersteinvestment.hu.pdf",
    "530 - George - george.ersteinvestment.hu.pdf",
)
EXPECTED_ACCOUNTS = ("TBSZ 2024", "TBSZ 2024 (2019)", "TBSZ 2025")
_ACCOUNT_LABELS = {
    "TBSZ 2024": "TBSZ 2024",
    "TBSZ 2024(2019)": "TBSZ 2024 (2019)",
    "TBSZ 2024 (2019)": "TBSZ 2024 (2019)",
    "TBSZ 2025": "TBSZ 2025",
}
_ALLOWED_CURRENCIES = frozenset({"EUR", "USD", "HUF"})
_EXPECTED_COUNTS = {
    "accounts": 3,
    "source_documents": 8,
    "instruments": 13,
    "position_snapshots": 17,
    "cash_snapshots": 6,
}


class CurrentStandingsError(RuntimeError):
    """The isolated current-standings database cannot be created safely."""


class OutputAlreadyExistsError(CurrentStandingsError):
    """The one-time output exists and replacement was not explicitly requested."""


@dataclass(frozen=True, slots=True)
class CurrentStandingsCreationResult:
    output_path: Path
    backup_path: Path | None
    account_count: int
    source_document_count: int
    instrument_count: int
    position_count: int
    cash_count: int


@dataclass(frozen=True, slots=True)
class _Position:
    asset_name: str
    currency: str | None
    amount: Decimal | None
    huf_display_value: Decimal | None


@dataclass(frozen=True, slots=True)
class _Cash:
    currency: str
    amount: Decimal


@dataclass(frozen=True, slots=True)
class _SourceDocument:
    filename: str
    sha256: str
    account_label: str
    view_type: str
    source_date: date | None
    positions: tuple[_Position, ...]
    cash: tuple[_Cash, ...]


def create_current_standings_database(
    *,
    source_directory: Path,
    confirmations_path: Path,
    output_path: Path,
    force: bool = False,
) -> CurrentStandingsCreationResult:
    """Create one isolated current-holdings database from confirmed local PDFs.

    All source documents are retained as hashes. Only the deterministic current
    position/cash payload per account/view is materialized; equal undated retry
    documents are accepted, while conflicting ones fail closed.
    """
    documents = _load_confirmed_documents(source_directory, confirmations_path)
    if output_path.exists() and not force:
        raise OutputAlreadyExistsError("OUTPUT_ALREADY_EXISTS; use --force only to replace after a verified backup")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = _backup_existing_output(output_path) if output_path.exists() else None
    temporary_path = output_path.parent / f".{output_path.name}.{uuid.uuid4().hex}.tmp"
    if temporary_path.exists():
        raise CurrentStandingsError("refusing to overwrite an existing temporary current-standings database")
    try:
        with sqlite3.connect(temporary_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(_SCHEMA)
            _insert_current_standings(connection, documents)
            connection.execute(f"PRAGMA user_version = {CURRENT_STANDINGS_SCHEMA_VERSION}")
            _verify_database(connection)
        temporary_path.replace(output_path)
    finally:
        # The output itself is not replaced unless the fully verified build succeeds.
        if temporary_path.exists():
            temporary_path.unlink()
    return _creation_result(output_path, backup_path)


def _load_confirmed_documents(source_directory: Path, confirmations_path: Path) -> tuple[_SourceDocument, ...]:
    if not source_directory.is_dir():
        raise CurrentStandingsError("TBSZ source directory does not exist")
    actual_pdfs = {path.name for path in source_directory.iterdir() if path.is_file() and path.suffix.casefold() == ".pdf"}
    required = set(EXPECTED_SOURCE_FILENAMES)
    if missing := sorted(required - actual_pdfs):
        raise CurrentStandingsError("REQUIRED_SOURCE_PDF_MISSING: " + ", ".join(missing))
    if unexpected := sorted(actual_pdfs - required):
        raise CurrentStandingsError("unexpected PDF outside the approved current-standings source set: " + ", ".join(unexpected))
    confirmations = _load_confirmations(confirmations_path)
    if set(confirmations) != required:
        raise CurrentStandingsError("manual confirmation manifest must contain exactly the required source filenames")
    documents = tuple(
        _source_document(source_directory / filename, confirmations[filename])
        for filename in EXPECTED_SOURCE_FILENAMES
    )
    if {item.account_label for item in documents} != set(EXPECTED_ACCOUNTS):
        raise CurrentStandingsError("confirmed source set does not establish exactly the approved TBSZ accounts")
    _validate_document_topology(documents)
    return documents


def _load_confirmations(path: Path) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CurrentStandingsError("manual confirmation manifest is unavailable or invalid") from error
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise CurrentStandingsError("manual confirmation manifest requires schema_version 1")
    entries = payload.get("documents")
    if not isinstance(entries, list):
        raise CurrentStandingsError("manual confirmation manifest requires a documents list")
    documents: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise CurrentStandingsError("manual confirmation document must be an object")
        filename = entry.get("source_filename")
        if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith(".pdf"):
            raise CurrentStandingsError("manual confirmation source filename is invalid")
        if filename in documents:
            raise CurrentStandingsError("manual confirmation source filenames must be unique")
        documents[filename] = entry
    return documents


def _source_document(path: Path, value: dict[str, Any]) -> _SourceDocument:
    if not value.get("manual_confirmed"):
        raise CurrentStandingsError(f"manual confirmation is required for {path.name}")
    raw_account = _required_string(value, "account_label")
    try:
        account_label = _ACCOUNT_LABELS[raw_account]
    except KeyError as error:
        raise CurrentStandingsError("Normal/Normál or unapproved account is not admitted") from error
    view_type = _required_string(value, "view_type")
    if view_type not in {"POSITIONS", "CASH"}:
        raise CurrentStandingsError("source view type must be POSITIONS or CASH")
    source_date = _optional_date(value.get("source_date"))
    positions = tuple(_position(item) for item in _list(value, "positions"))
    cash = tuple(_cash(item) for item in _list(value, "cash"))
    if view_type == "POSITIONS" and cash:
        raise CurrentStandingsError("position source cannot contain cash rows")
    if view_type == "CASH" and positions:
        raise CurrentStandingsError("cash source cannot contain position rows")
    return _SourceDocument(path.name, _sha256(path), account_label, view_type, source_date, positions, cash)


def _position(value: Any) -> _Position:
    if not isinstance(value, dict):
        raise CurrentStandingsError("position confirmation must be an object")
    asset_name = _required_string(value, "provider_name")
    amount = _decimal_or_none(value.get("market_value"), "market_value")
    currency = _currency_or_none(value.get("market_currency"))
    if (amount is None) != (currency is None):
        raise CurrentStandingsError("market value and market currency must be present together or absent together")
    reporting_value = _decimal_or_none(value.get("reporting_value"), "reporting_value")
    reporting_currency = _currency_or_none(value.get("reporting_currency"))
    if (reporting_value is None) != (reporting_currency is None):
        raise CurrentStandingsError("reporting value and reporting currency must be present together or absent together")
    huf_display_value = reporting_value if reporting_currency == "HUF" else amount if currency == "HUF" else None
    return _Position(asset_name, currency, amount, huf_display_value)


def _cash(value: Any) -> _Cash:
    if not isinstance(value, dict):
        raise CurrentStandingsError("cash confirmation must be an object")
    currency = _currency_or_none(value.get("currency"))
    amount = _decimal_or_none(value.get("balance"), "cash balance")
    if currency is None or amount is None:
        raise CurrentStandingsError("cash currency and balance are required")
    return _Cash(currency, amount)


def _validate_document_topology(documents: tuple[_SourceDocument, ...]) -> None:
    expected_views = {
        "TBSZ 2024": {"POSITIONS": 1, "CASH": 1},
        "TBSZ 2024 (2019)": {"POSITIONS": 1, "CASH": 1},
        "TBSZ 2025": {"POSITIONS": 2, "CASH": 2},
    }
    actual: dict[str, dict[str, int]] = {}
    for document in documents:
        actual.setdefault(document.account_label, {}).setdefault(document.view_type, 0)
        actual[document.account_label][document.view_type] += 1
    if actual != expected_views:
        raise CurrentStandingsError("confirmed source topology does not match the approved TBSZ current snapshot set")


def _insert_current_standings(connection: sqlite3.Connection, documents: tuple[_SourceDocument, ...]) -> None:
    account_ids = {
        label: _insert_account(connection, label, documents)
        for label in EXPECTED_ACCOUNTS
    }
    source_ids = {
        document.filename: _insert_source_document(connection, account_ids[document.account_label], document)
        for document in documents
    }
    for account_label in EXPECTED_ACCOUNTS:
        for view_type in ("POSITIONS", "CASH"):
            selected = _select_current_document(
                tuple(item for item in documents if item.account_label == account_label and item.view_type == view_type)
            )
            if selected is None:
                continue
            source_id = source_ids[selected.filename]
            if view_type == "POSITIONS":
                _insert_positions(connection, account_ids[account_label], source_id, selected)
            else:
                _insert_cash(connection, account_ids[account_label], source_id, selected)


def _insert_account(connection: sqlite3.Connection, label: str, documents: tuple[_SourceDocument, ...]) -> int:
    positions = _select_current_document(
        tuple(item for item in documents if item.account_label == label and item.view_type == "POSITIONS")
    )
    assert positions is not None
    investment_subtotal = sum((item.huf_display_value or Decimal() for item in positions.positions), Decimal())
    cursor = connection.execute(
        """INSERT INTO accounts (
            account_label, account_type, total_value_huf, investment_subtotal_huf, source_date, captured_at
        ) VALUES (?, 'TBSZ', NULL, ?, ?, ?)""",
        (label, _decimal_text(investment_subtotal), _date_text(positions.source_date), datetime.now(UTC).isoformat()),
    )
    if cursor.lastrowid is None:
        raise CurrentStandingsError("SQLite did not return an account id")
    return int(cursor.lastrowid)


def _insert_source_document(connection: sqlite3.Connection, account_id: int, document: _SourceDocument) -> int:
    cursor = connection.execute(
        """INSERT INTO source_documents (
            account_id, source_filename, source_sha256, view_type, notes
        ) VALUES (?, ?, ?, ?, ?)""",
        (account_id, document.filename, document.sha256, document.view_type, "MANUALLY_CONFIRMED_CURRENT_STANDINGS"),
    )
    if cursor.lastrowid is None:
        raise CurrentStandingsError("SQLite did not return a source document id")
    return int(cursor.lastrowid)


def _select_current_document(documents: tuple[_SourceDocument, ...]) -> _SourceDocument | None:
    if not documents:
        return None
    payloads = {_document_payload(document) for document in documents}
    dates = {document.source_date for document in documents}
    if len(payloads) > 1 and len(dates) == 1 and None in dates:
        raise CurrentStandingsError("undated duplicate source documents conflict; current state is not established")
    return max(documents, key=lambda item: (item.source_date is not None, item.source_date or date.min, item.filename))


def _document_payload(document: _SourceDocument) -> tuple[object, ...]:
    if document.view_type == "POSITIONS":
        return tuple((item.asset_name, item.currency, item.amount, item.huf_display_value) for item in document.positions)
    return tuple((item.currency, item.amount) for item in document.cash)


def _insert_positions(connection: sqlite3.Connection, account_id: int, source_id: int, document: _SourceDocument) -> None:
    for position in document.positions:
        connection.execute("INSERT OR IGNORE INTO instruments (asset_name, isin) VALUES (?, NULL)", (position.asset_name,))
        instrument_row = connection.execute(
            "SELECT instrument_id FROM instruments WHERE asset_name = ?", (position.asset_name,)
        ).fetchone()
        if instrument_row is None:
            raise CurrentStandingsError("SQLite did not return an instrument id")
        connection.execute(
            """INSERT INTO position_snapshots (
                account_id, source_id, instrument_id, currency, amount, huf_display_value,
                quantity, unit_price, roi_percent, source_date
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?)""",
            (
                account_id,
                source_id,
                int(instrument_row[0]),
                position.currency,
                _decimal_text(position.amount),
                _decimal_text(position.huf_display_value),
                _date_text(document.source_date),
            ),
        )


def _insert_cash(connection: sqlite3.Connection, account_id: int, source_id: int, document: _SourceDocument) -> None:
    for cash in document.cash:
        connection.execute(
            """INSERT INTO cash_snapshots (account_id, source_id, currency, amount, source_date)
            VALUES (?, ?, ?, ?, ?)""",
            (account_id, source_id, cash.currency, _decimal_text(cash.amount), _date_text(document.source_date)),
        )


def _backup_existing_output(output_path: Path) -> Path:
    backup_directory = output_path.parent / "backups"
    backup_directory.mkdir(parents=True, exist_ok=True)
    backup_path = backup_directory / (
        f"{output_path.stem}-before-force-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}-{uuid.uuid4().hex}.sqlite"
    )
    with (
        sqlite3.connect(f"file:{output_path.resolve()}?mode=ro", uri=True) as source,
        sqlite3.connect(backup_path) as destination,
    ):
        source.backup(destination)
    with sqlite3.connect(f"file:{backup_path.resolve()}?mode=ro", uri=True) as backup:
        _verify_sqlite_health(backup)
    return backup_path


def _creation_result(output_path: Path, backup_path: Path | None) -> CurrentStandingsCreationResult:
    with sqlite3.connect(f"file:{output_path.resolve()}?mode=ro", uri=True) as connection:
        _verify_database(connection)
        counts = {table: _count(connection, table) for table in _EXPECTED_COUNTS}
    return CurrentStandingsCreationResult(
        output_path=output_path,
        backup_path=backup_path,
        account_count=counts["accounts"],
        source_document_count=counts["source_documents"],
        instrument_count=counts["instruments"],
        position_count=counts["position_snapshots"],
        cash_count=counts["cash_snapshots"],
    )


def _verify_database(connection: sqlite3.Connection) -> None:
    _verify_sqlite_health(connection)
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version != CURRENT_STANDINGS_SCHEMA_VERSION:
        raise CurrentStandingsError("current-standings schema version is incorrect")
    counts = {table: _count(connection, table) for table in _EXPECTED_COUNTS}
    if counts != _EXPECTED_COUNTS:
        raise CurrentStandingsError(f"current-standings row counts differ from the approved source set: {counts}")
    tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    forbidden = {"recommendations", "target_allocations", "transactions"} & tables
    if forbidden:
        raise CurrentStandingsError("forbidden non-current-standings tables exist: " + ", ".join(sorted(forbidden)))


def _verify_sqlite_health(connection: sqlite3.Connection) -> None:
    integrity = tuple(str(row[0]) for row in connection.execute("PRAGMA integrity_check"))
    if integrity != ("ok",):
        raise CurrentStandingsError("integrity_check did not return ok")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise CurrentStandingsError("foreign_key_check reported a violation")


def _count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0])


def _required_string(value: dict[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise CurrentStandingsError(f"manual confirmation requires {key}")
    return result


def _list(value: dict[str, Any], key: str) -> list[Any]:
    result = value.get(key, [])
    if not isinstance(result, list):
        raise CurrentStandingsError(f"manual confirmation {key} must be a list")
    return result


def _decimal_or_none(value: Any, field: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise CurrentStandingsError(f"{field} must be a decimal or null")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise CurrentStandingsError(f"{field} must be a decimal or null") from error
    if not result.is_finite() or result < 0:
        raise CurrentStandingsError(f"{field} must be finite and non-negative")
    return result


def _currency_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value.upper() not in _ALLOWED_CURRENCIES:
        raise CurrentStandingsError("only EUR, USD, and HUF are admitted to the current-standings initializer")
    return value.upper()


def _optional_date(value: Any) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise CurrentStandingsError("source_date must be an ISO date or null")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise CurrentStandingsError("source_date must be an ISO date or null") from error


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _decimal_text(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _date_text(value: date | None) -> str | None:
    return value.isoformat() if value else None


_SCHEMA = """
CREATE TABLE accounts (
    account_id INTEGER PRIMARY KEY,
    account_label TEXT NOT NULL UNIQUE,
    account_type TEXT NOT NULL CHECK(account_type = 'TBSZ'),
    total_value_huf TEXT NULL,
    investment_subtotal_huf TEXT NULL,
    source_date TEXT NULL,
    captured_at TEXT NOT NULL
);
CREATE TABLE source_documents (
    source_id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(account_id),
    source_filename TEXT NOT NULL UNIQUE,
    source_sha256 TEXT NOT NULL,
    view_type TEXT NOT NULL CHECK(view_type IN ('POSITIONS', 'CASH')),
    notes TEXT NOT NULL
);
CREATE TABLE instruments (
    instrument_id INTEGER PRIMARY KEY,
    asset_name TEXT NOT NULL UNIQUE,
    isin TEXT NULL
);
CREATE TABLE position_snapshots (
    position_id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(account_id),
    source_id INTEGER NOT NULL REFERENCES source_documents(source_id),
    instrument_id INTEGER NOT NULL REFERENCES instruments(instrument_id),
    currency TEXT NULL CHECK(currency IN ('EUR', 'USD', 'HUF')),
    amount TEXT NULL,
    huf_display_value TEXT NULL,
    quantity TEXT NULL,
    unit_price TEXT NULL,
    roi_percent TEXT NULL,
    source_date TEXT NULL
);
CREATE TABLE cash_snapshots (
    cash_id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(account_id),
    source_id INTEGER NOT NULL REFERENCES source_documents(source_id),
    currency TEXT NOT NULL CHECK(currency IN ('EUR', 'USD', 'HUF')),
    amount TEXT NOT NULL,
    source_date TEXT NULL,
    UNIQUE(account_id, currency)
);
CREATE VIEW current_holdings AS
SELECT
    account.account_label AS tbsz_name,
    'ASSET' AS record_type,
    instrument.asset_name AS asset_name,
    instrument.isin AS isin,
    position.currency AS currency,
    position.amount AS amount,
    position.huf_display_value AS huf_display_value,
    position.roi_percent AS roi_percent,
    position.source_date AS source_date
FROM position_snapshots AS position
JOIN accounts AS account ON account.account_id = position.account_id
JOIN instruments AS instrument ON instrument.instrument_id = position.instrument_id
UNION ALL
SELECT
    account.account_label AS tbsz_name,
    'CASH' AS record_type,
    'CASH' AS asset_name,
    NULL AS isin,
    cash.currency AS currency,
    cash.amount AS amount,
    NULL AS huf_display_value,
    NULL AS roi_percent,
    cash.source_date AS source_date
FROM cash_snapshots AS cash
JOIN accounts AS account ON account.account_id = cash.account_id;
"""
