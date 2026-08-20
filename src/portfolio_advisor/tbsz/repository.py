"""SQLite persistence for actual TBSZ evidence; never brokerage execution."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .models import (
    CashSnapshot,
    IdentityStatus,
    Instrument,
    ManualTransaction,
    PositionSnapshot,
    SourceConflictError,
    SourceDocumentInput,
    SourcePositionInput,
    SourceSnapshot,
    TbszAccount,
    TbszError,
    TransactionAction,
)

_ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")


class TbszPortfolioRepository:
    """Append-only local evidence store with explicit source and identity states."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._write_connection() as connection:
            connection.executescript(_SCHEMA)

    @contextmanager
    def _write_connection(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def _read_connection(self) -> Iterator[sqlite3.Connection]:
        if not self.path.is_file():
            raise TbszError(f"TBSZ database does not exist: {self.path}")
        connection = sqlite3.connect(f"file:{self.path.resolve()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()

    def import_source_document(self, document: SourceDocumentInput) -> tuple[SourceSnapshot, bool]:
        """Append one PDF-backed snapshot or reject conflicting retained evidence."""
        validate_source_document(document)
        with self._write_connection() as connection:
            return self._import_source_document(connection, document)

    def import_source_documents(
        self, documents: tuple[SourceDocumentInput, ...]
    ) -> tuple[tuple[SourceSnapshot, bool], ...]:
        """Append an all-or-nothing source batch; conflicts roll back every row."""
        for document in documents:
            validate_source_document(document)
        with self._write_connection() as connection:
            return tuple(self._import_source_document(connection, document) for document in documents)

    def _import_source_document(
        self, connection: sqlite3.Connection, document: SourceDocumentInput
    ) -> tuple[SourceSnapshot, bool]:
        fingerprint = _document_fingerprint(document)
        account_id = self._account_id(connection, document.account_label, create=True)
        existing = connection.execute(
            "SELECT * FROM source_snapshots WHERE source_filename = ?", (document.source_filename,)
        ).fetchone()
        if existing is not None:
            if existing["content_sha256"] == document.content_sha256 and existing["evidence_fingerprint"] == fingerprint:
                return _source_snapshot(existing), False
            raise SourceConflictError("source filename already exists with conflicting evidence")
        if document.source_date is None:
            undated = connection.execute(
                "SELECT source_filename FROM source_snapshots "
                "WHERE account_id = ? AND view_type = ? AND source_date IS NULL AND evidence_fingerprint != ?",
                (account_id, document.view_type, fingerprint),
            ).fetchone()
            if undated is not None:
                raise SourceConflictError(
                    "undated source conflicts with existing account/view evidence; establish a source date before import"
                )
        timestamp = _now().isoformat()
        cursor = connection.execute(
            "INSERT INTO source_snapshots "
            "(account_id, source_filename, content_sha256, source_type, view_type, source_date, ingested_at, evidence_status, evidence_fingerprint) "
            "VALUES (?, ?, ?, 'GEORGE_PDF', ?, ?, ?, ?, ?)",
            (
                account_id,
                document.source_filename,
                document.content_sha256,
                document.view_type,
                document.source_date.isoformat() if document.source_date else None,
                timestamp,
                document.evidence_status,
                fingerprint,
            ),
        )
        snapshot_id = _last_row_id(cursor)
        for position in document.positions:
            instrument = self._instrument_for_source(connection, position, snapshot_id)
            connection.execute(
                "INSERT INTO position_snapshots "
                "(snapshot_id, account_id, instrument_id, provider_name, normalized_provider_name, quantity, unit_price, market_value, market_currency, reporting_value, reporting_currency, data_quality_status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    snapshot_id,
                    account_id,
                    instrument.instrument_id,
                    position.provider_name,
                    _normalize(position.provider_name),
                    _decimal_text(position.quantity),
                    _decimal_text(position.unit_price),
                    _decimal_text(position.market_value),
                    position.market_currency,
                    _decimal_text(position.reporting_value),
                    position.reporting_currency,
                    position.data_quality_status,
                ),
            )
        for cash in document.cash:
            connection.execute(
                "INSERT INTO cash_snapshots (snapshot_id, account_id, currency, balance, data_quality_status) VALUES (?, ?, ?, ?, ?)",
                (snapshot_id, account_id, cash.currency, _decimal_text(cash.balance), cash.data_quality_status),
            )
        row = connection.execute("SELECT * FROM source_snapshots WHERE snapshot_id = ?", (snapshot_id,)).fetchone()
        assert row is not None
        return _source_snapshot(row), True

    def accounts(self) -> tuple[TbszAccount, ...]:
        with self._read_connection() as connection:
            rows = connection.execute("SELECT account_id, label FROM tbsz_accounts ORDER BY label").fetchall()
        return tuple(TbszAccount(int(row["account_id"]), str(row["label"])) for row in rows)

    def account(self, label: str) -> TbszAccount:
        with self._read_connection() as connection:
            account_id = self._account_id(connection, label, create=False)
            row = connection.execute("SELECT account_id, label FROM tbsz_accounts WHERE account_id = ?", (account_id,)).fetchone()
        assert row is not None
        return TbszAccount(int(row["account_id"]), str(row["label"]))

    def source_snapshots(self, account_label: str | None = None) -> tuple[SourceSnapshot, ...]:
        with self._read_connection() as connection:
            if account_label is None:
                rows = connection.execute("SELECT * FROM source_snapshots ORDER BY snapshot_id").fetchall()
            else:
                account_id = self._account_id(connection, account_label, create=False)
                rows = connection.execute("SELECT * FROM source_snapshots WHERE account_id = ? ORDER BY snapshot_id", (account_id,)).fetchall()
        return tuple(_source_snapshot(row) for row in rows)

    def positions_for_snapshot(self, snapshot_id: int) -> tuple[PositionSnapshot, ...]:
        with self._read_connection() as connection:
            rows = connection.execute(_POSITION_SELECT + " WHERE position.snapshot_id = ? ORDER BY position.position_id", (snapshot_id,)).fetchall()
        return tuple(_position(row) for row in rows)

    def cash_for_snapshot(self, snapshot_id: int) -> tuple[CashSnapshot, ...]:
        with self._read_connection() as connection:
            rows = connection.execute("SELECT * FROM cash_snapshots WHERE snapshot_id = ? ORDER BY currency", (snapshot_id,)).fetchall()
        return tuple(_cash(row) for row in rows)

    def current_position_snapshot(self, account_label: str) -> SourceSnapshot | None:
        """Return the only/latest position observation; undated conflicts are rejected at import."""
        account = self.account(account_label)
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM source_snapshots WHERE account_id = ? AND view_type = 'POSITIONS' "
                "ORDER BY source_date IS NULL, source_date DESC, snapshot_id DESC LIMIT 1",
                (account.account_id,),
            ).fetchone()
        return _source_snapshot(row) if row is not None else None

    def current_cash_snapshot(self, account_label: str) -> SourceSnapshot | None:
        account = self.account(account_label)
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM source_snapshots WHERE account_id = ? AND view_type = 'CASH' "
                "ORDER BY source_date IS NULL, source_date DESC, snapshot_id DESC LIMIT 1",
                (account.account_id,),
            ).fetchone()
        return _source_snapshot(row) if row is not None else None

    def confirm_instrument_mapping(self, instrument_id: int, isin: str, alias_name: str) -> Instrument:
        isin = _validate_isin(isin)
        if not alias_name.strip():
            raise TbszError("manual mapping requires a non-empty alias")
        with self._write_connection() as connection:
            row = connection.execute("SELECT * FROM instruments WHERE instrument_id = ?", (instrument_id,)).fetchone()
            if row is None:
                raise TbszError("instrument does not exist")
            conflict = connection.execute(
                "SELECT instrument_id FROM instruments WHERE isin = ? AND instrument_id != ?", (isin, instrument_id)
            ).fetchone()
            if conflict is not None:
                raise SourceConflictError("ISIN is already assigned to another instrument")
            connection.execute(
                "UPDATE instruments SET isin = ?, identity_status = ? WHERE instrument_id = ?",
                (isin, IdentityStatus.MANUAL_CONFIRMED.value, instrument_id),
            )
            connection.execute(
                "INSERT OR IGNORE INTO instrument_aliases "
                "(instrument_id, alias_name, normalized_alias, mapping_method, source_snapshot_id) VALUES (?, ?, ?, 'MANUAL_CONFIRMED', NULL)",
                (instrument_id, alias_name, _normalize(alias_name)),
            )
            mapped = connection.execute("SELECT * FROM instruments WHERE instrument_id = ?", (instrument_id,)).fetchone()
        assert mapped is not None
        return _instrument(mapped)

    def record_manual_transaction(
        self,
        *,
        account_label: str,
        action: TransactionAction,
        instrument_id: int,
        quantity: Decimal,
        price: Decimal,
        currency: str,
        transaction_date: date,
        client_reference: str | None = None,
    ) -> ManualTransaction:
        quantity = _positive_decimal(quantity, "quantity")
        price = _positive_decimal(price, "price")
        currency = _validate_currency(currency)
        if client_reference is not None and not client_reference.strip():
            raise TbszError("client reference cannot be blank")
        with self._write_connection() as connection:
            account_id = self._account_id(connection, account_label, create=False)
            if (
                connection.execute(
                    "SELECT 1 FROM position_snapshots WHERE account_id = ? AND instrument_id = ?",
                    (account_id, instrument_id),
                ).fetchone()
                is None
            ):
                raise TbszError("instrument is not evidenced for the requested TBSZ account")
            if client_reference is not None:
                existing = connection.execute(
                    "SELECT * FROM transactions WHERE account_id = ? AND client_reference = ?",
                    (account_id, client_reference),
                ).fetchone()
                if existing is not None:
                    return _transaction(existing)
            transaction_id = str(uuid.uuid4())
            timestamp = _now().isoformat()
            connection.execute(
                "INSERT INTO transactions "
                "(transaction_id, account_id, instrument_id, action, quantity, price, currency, transaction_date, recorded_at, client_reference, record_type) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'MANUAL_USER_EXECUTED')",
                (
                    transaction_id,
                    account_id,
                    instrument_id,
                    action.value,
                    _decimal_text(quantity),
                    _decimal_text(price),
                    currency,
                    transaction_date.isoformat(),
                    timestamp,
                    client_reference,
                ),
            )
            row = connection.execute("SELECT * FROM transactions WHERE transaction_id = ?", (transaction_id,)).fetchone()
        assert row is not None
        return _transaction(row)

    def transactions(self, account_label: str | None = None) -> tuple[ManualTransaction, ...]:
        with self._read_connection() as connection:
            if account_label is None:
                rows = connection.execute("SELECT * FROM transactions ORDER BY recorded_at, transaction_id").fetchall()
            else:
                account_id = self._account_id(connection, account_label, create=False)
                rows = connection.execute(
                    "SELECT * FROM transactions WHERE account_id = ? ORDER BY recorded_at, transaction_id", (account_id,)
                ).fetchall()
        return tuple(_transaction(row) for row in rows)

    def instrument(self, instrument_id: int) -> Instrument:
        with self._read_connection() as connection:
            row = connection.execute("SELECT * FROM instruments WHERE instrument_id = ?", (instrument_id,)).fetchone()
        if row is None:
            raise TbszError("instrument does not exist")
        return _instrument(row)

    def _instrument_for_source(
        self, connection: sqlite3.Connection, position: SourcePositionInput, snapshot_id: int
    ) -> Instrument:
        normalized = _normalize(position.provider_name)
        exact_isin = _validate_isin(position.isin) if position.isin else None
        row = (
            connection.execute("SELECT * FROM instruments WHERE isin = ?", (exact_isin,)).fetchone()
            if exact_isin
            else None
        )
        if row is None:
            row = connection.execute("SELECT * FROM instruments WHERE normalized_name = ?", (normalized,)).fetchone()
        if row is None:
            identity = IdentityStatus.EXACT_ISIN if exact_isin else IdentityStatus.PROVIDER_NAME_EXACT_CANDIDATE
            cursor = connection.execute(
                "INSERT INTO instruments (canonical_name, normalized_name, isin, identity_status) VALUES (?, ?, ?, ?)",
                (position.provider_name, normalized, exact_isin, identity.value),
            )
            instrument_id = _last_row_id(cursor)
            row = connection.execute("SELECT * FROM instruments WHERE instrument_id = ?", (instrument_id,)).fetchone()
            assert row is not None
        elif exact_isin:
            if row["isin"] not in {None, exact_isin}:
                raise SourceConflictError("source exact ISIN conflicts with retained instrument identity")
            if row["isin"] is None:
                connection.execute(
                    "UPDATE instruments SET isin = ?, identity_status = ? WHERE instrument_id = ?",
                    (exact_isin, IdentityStatus.EXACT_ISIN.value, int(row["instrument_id"])),
                )
                row = connection.execute("SELECT * FROM instruments WHERE instrument_id = ?", (int(row["instrument_id"]),)).fetchone()
                assert row is not None
        connection.execute(
            "INSERT OR IGNORE INTO instrument_aliases "
            "(instrument_id, alias_name, normalized_alias, mapping_method, source_snapshot_id) VALUES (?, ?, ?, 'EXACT_PROVIDER_NAME', ?)",
            (int(row["instrument_id"]), position.provider_name, normalized, snapshot_id),
        )
        return _instrument(row)

    def _account_id(self, connection: sqlite3.Connection, label: str, *, create: bool) -> int:
        _validate_tbsz_label(label)
        row = connection.execute("SELECT account_id FROM tbsz_accounts WHERE label = ?", (label,)).fetchone()
        if row is not None:
            return int(row["account_id"])
        if not create:
            raise TbszError(f"TBSZ account does not exist: {label}")
        cursor = connection.execute("INSERT INTO tbsz_accounts (label) VALUES (?)", (label,))
        return _last_row_id(cursor)


def validate_source_document(document: SourceDocumentInput) -> None:
    _validate_tbsz_label(document.account_label)
    if document.view_type not in {"POSITIONS", "CASH"}:
        raise TbszError("source view type must be POSITIONS or CASH")
    if not document.source_filename.casefold().endswith(".pdf"):
        raise TbszError("source document must be a PDF filename")
    if len(document.content_sha256) != 64 or any(char not in "0123456789abcdef" for char in document.content_sha256):
        raise TbszError("source content SHA-256 is malformed")
    if document.view_type == "POSITIONS" and document.cash:
        raise TbszError("position PDF cannot contain cash rows")
    if document.view_type == "CASH" and document.positions:
        raise TbszError("cash PDF cannot contain position rows")
    if not document.evidence_status:
        raise TbszError("source evidence status is required")
    names: set[str] = set()
    for position in document.positions:
        if not position.provider_name.strip() or _normalize(position.provider_name) in names:
            raise TbszError("position provider names must be non-empty and unique per source")
        names.add(_normalize(position.provider_name))
        _nullable_nonnegative(position.market_value, "market value")
        _nullable_nonnegative(position.reporting_value, "reporting value")
        _nullable_nonnegative(position.quantity, "quantity")
        _nullable_nonnegative(position.unit_price, "unit price")
        if position.market_currency is not None:
            _validate_currency(position.market_currency)
        if position.reporting_currency is not None:
            _validate_currency(position.reporting_currency)
        if (position.reporting_value is None) != (position.reporting_currency is None):
            raise TbszError("reporting value and currency must be present together")
    currencies: set[str] = set()
    for cash in document.cash:
        currency = _validate_currency(cash.currency)
        if currency in currencies:
            raise TbszError("cash currencies must be unique per source")
        currencies.add(currency)
        _nullable_nonnegative(cash.balance, "cash balance")


def _validate_tbsz_label(label: str) -> None:
    if not label.startswith("TBSZ"):
        raise TbszError("only explicitly labelled TBSZ accounts are in scope")
    if "normal" in label.casefold() or "normál" in label.casefold():
        raise TbszError("Normal/Normál account is prohibited")


def _validate_currency(value: str) -> str:
    value = value.upper()
    if not _CURRENCY.fullmatch(value):
        raise TbszError("currency must be a three-letter uppercase code")
    return value


def _validate_isin(value: str) -> str:
    value = value.upper()
    if not _ISIN.fullmatch(value):
        raise TbszError("ISIN must be a 12-character identifier")
    return value


def _positive_decimal(value: Decimal, field: str) -> Decimal:
    result = _nullable_nonnegative(value, field)
    assert result is not None
    if result <= 0:
        raise TbszError(f"{field} must be positive")
    return result


def _nullable_nonnegative(value: Decimal | None, field: str) -> Decimal | None:
    if value is None:
        return None
    if not value.is_finite() or value < 0:
        raise TbszError(f"{field} must be finite and non-negative")
    return value


def _decimal_text(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _last_row_id(cursor: sqlite3.Cursor) -> int:
    if cursor.lastrowid is None:
        raise TbszError("SQLite did not return an inserted row id")
    return int(cursor.lastrowid)


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise TbszError("database decimal is malformed") from error
    return result


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def _now() -> datetime:
    return datetime.now(UTC)


def _document_fingerprint(document: SourceDocumentInput) -> str:
    payload = {
        "account_label": document.account_label,
        "view_type": document.view_type,
        "source_date": document.source_date.isoformat() if document.source_date else None,
        "evidence_status": document.evidence_status,
        "positions": [
            {
                "provider_name": item.provider_name,
                "market_value": _decimal_text(item.market_value),
                "market_currency": item.market_currency,
                "reporting_value": _decimal_text(item.reporting_value),
                "reporting_currency": item.reporting_currency,
                "quantity": _decimal_text(item.quantity),
                "unit_price": _decimal_text(item.unit_price),
                "isin": item.isin,
                "data_quality_status": item.data_quality_status,
            }
            for item in document.positions
        ],
        "cash": [
            {"currency": item.currency, "balance": _decimal_text(item.balance), "data_quality_status": item.data_quality_status}
            for item in document.cash
        ],
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _source_snapshot(row: sqlite3.Row) -> SourceSnapshot:
    return SourceSnapshot(
        snapshot_id=int(row["snapshot_id"]),
        account_id=int(row["account_id"]),
        source_filename=str(row["source_filename"]),
        source_type=str(row["source_type"]),
        view_type=str(row["view_type"]),
        source_date=date.fromisoformat(str(row["source_date"])) if row["source_date"] else None,
        ingested_at=datetime.fromisoformat(str(row["ingested_at"])),
        evidence_status=str(row["evidence_status"]),
        evidence_fingerprint=str(row["evidence_fingerprint"]),
    )


def _instrument(row: sqlite3.Row) -> Instrument:
    return Instrument(
        instrument_id=int(row["instrument_id"]),
        canonical_name=str(row["canonical_name"]),
        isin=str(row["isin"]) if row["isin"] else None,
        identity_status=IdentityStatus(str(row["identity_status"])),
    )


_POSITION_SELECT = (
    "SELECT position.*, instrument.canonical_name, instrument.isin, instrument.identity_status "
    "FROM position_snapshots AS position JOIN instruments AS instrument ON instrument.instrument_id = position.instrument_id"
)


def _position(row: sqlite3.Row) -> PositionSnapshot:
    instrument = Instrument(
        int(row["instrument_id"]),
        str(row["canonical_name"]),
        str(row["isin"]) if row["isin"] else None,
        IdentityStatus(str(row["identity_status"])),
    )
    return PositionSnapshot(
        position_id=int(row["position_id"]), snapshot_id=int(row["snapshot_id"]), account_id=int(row["account_id"]),
        instrument=instrument, provider_name=str(row["provider_name"]), quantity=_decimal(row["quantity"]),
        unit_price=_decimal(row["unit_price"]), market_value=_decimal(row["market_value"]),
        market_currency=str(row["market_currency"]) if row["market_currency"] else None,
        reporting_value=_decimal(row["reporting_value"]),
        reporting_currency=str(row["reporting_currency"]) if row["reporting_currency"] else None,
        data_quality_status=str(row["data_quality_status"]),
    )


def _cash(row: sqlite3.Row) -> CashSnapshot:
    return CashSnapshot(
        cash_id=int(row["cash_id"]), snapshot_id=int(row["snapshot_id"]), account_id=int(row["account_id"]),
        currency=str(row["currency"]), balance=_decimal(row["balance"]) or Decimal(),
        data_quality_status=str(row["data_quality_status"]),
    )


def _transaction(row: sqlite3.Row) -> ManualTransaction:
    quantity = _decimal(row["quantity"])
    price = _decimal(row["price"])
    assert quantity is not None and price is not None
    return ManualTransaction(
        transaction_id=str(row["transaction_id"]), account_id=int(row["account_id"]), instrument_id=int(row["instrument_id"]),
        action=TransactionAction(str(row["action"])), quantity=quantity, price=price, currency=str(row["currency"]),
        transaction_date=date.fromisoformat(str(row["transaction_date"])), recorded_at=datetime.fromisoformat(str(row["recorded_at"])),
        client_reference=str(row["client_reference"]) if row["client_reference"] else None,
    )


_SCHEMA = """
CREATE TABLE IF NOT EXISTS tbsz_accounts (
    account_id INTEGER PRIMARY KEY,
    label TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS source_snapshots (
    snapshot_id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES tbsz_accounts(account_id),
    source_filename TEXT NOT NULL UNIQUE,
    content_sha256 TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK(source_type = 'GEORGE_PDF'),
    view_type TEXT NOT NULL CHECK(view_type IN ('POSITIONS', 'CASH')),
    source_date TEXT NULL,
    ingested_at TEXT NOT NULL,
    evidence_status TEXT NOT NULL,
    evidence_fingerprint TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS instruments (
    instrument_id INTEGER PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL UNIQUE,
    isin TEXT NULL UNIQUE,
    identity_status TEXT NOT NULL CHECK(identity_status IN ('EXACT_ISIN', 'MANUAL_CONFIRMED', 'PROVIDER_NAME_EXACT_CANDIDATE', 'IDENTITY_UNRESOLVED'))
);
CREATE TABLE IF NOT EXISTS instrument_aliases (
    alias_id INTEGER PRIMARY KEY,
    instrument_id INTEGER NOT NULL REFERENCES instruments(instrument_id),
    alias_name TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    mapping_method TEXT NOT NULL CHECK(mapping_method IN ('EXACT_PROVIDER_NAME', 'MANUAL_CONFIRMED')),
    source_snapshot_id INTEGER NULL REFERENCES source_snapshots(snapshot_id),
    UNIQUE(instrument_id, normalized_alias, mapping_method, source_snapshot_id)
);
CREATE TABLE IF NOT EXISTS position_snapshots (
    position_id INTEGER PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES source_snapshots(snapshot_id),
    account_id INTEGER NOT NULL REFERENCES tbsz_accounts(account_id),
    instrument_id INTEGER NOT NULL REFERENCES instruments(instrument_id),
    provider_name TEXT NOT NULL,
    normalized_provider_name TEXT NOT NULL,
    quantity TEXT NULL,
    unit_price TEXT NULL,
    market_value TEXT NULL,
    market_currency TEXT NULL,
    reporting_value TEXT NULL,
    reporting_currency TEXT NULL,
    data_quality_status TEXT NOT NULL,
    UNIQUE(snapshot_id, normalized_provider_name)
);
CREATE TABLE IF NOT EXISTS cash_snapshots (
    cash_id INTEGER PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES source_snapshots(snapshot_id),
    account_id INTEGER NOT NULL REFERENCES tbsz_accounts(account_id),
    currency TEXT NOT NULL,
    balance TEXT NOT NULL,
    data_quality_status TEXT NOT NULL,
    UNIQUE(snapshot_id, currency)
);
CREATE TABLE IF NOT EXISTS transactions (
    transaction_id TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES tbsz_accounts(account_id),
    instrument_id INTEGER NOT NULL REFERENCES instruments(instrument_id),
    action TEXT NOT NULL CHECK(action IN ('BUY', 'SELL')),
    quantity TEXT NOT NULL,
    price TEXT NOT NULL,
    currency TEXT NOT NULL,
    transaction_date TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    client_reference TEXT NULL,
    record_type TEXT NOT NULL CHECK(record_type = 'MANUAL_USER_EXECUTED'),
    UNIQUE(account_id, client_reference)
);
"""
