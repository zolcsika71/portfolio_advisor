"""SQLite persistence for actual TBSZ evidence; never brokerage execution."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .models import (
    CashCorrectionInput,
    CashSnapshot,
    IdentityStatus,
    Instrument,
    ManualTransaction,
    PositionSnapshot,
    ScreenshotArtifactInput,
    ScreenshotArtifactRole,
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
CURRENT_SCHEMA_VERSION = 3


class TbszSchemaMigrationError(TbszError):
    """The local evidence schema cannot be safely recognized or upgraded."""


class TbszPortfolioRepository:
    """Append-only local evidence store with explicit source and identity states."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> Path | None:
        """Create or migrate the local schema, returning any verified backup.

        This is deliberately separate from the read-only comparison path.  An
        existing older ledger is backed up before any live migration is attempted.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        backup_path = self._create_migration_backup_if_needed()
        with self._write_connection() as connection:
            _initialize_schema(connection)
        return backup_path

    def _create_migration_backup_if_needed(self) -> Path | None:
        """Back up only a recognized existing schema that needs migration."""
        if not self.path.is_file():
            return None
        with self._read_connection() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            existing_tables = _application_tables(connection)
            if version == CURRENT_SCHEMA_VERSION:
                _require_current_schema(connection)
                return None
            if version not in {0, 1, 2}:
                raise TbszSchemaMigrationError(
                    f"unsupported TBSZ schema version {version}; expected 0, 1, 2, or {CURRENT_SCHEMA_VERSION}"
                )
            if not existing_tables:
                return None
            if version == 2:
                _require_v2_schema(connection)
            else:
                _require_v1_schema(connection)
        return _create_verified_backup(
            self.path,
            source_version=version,
            target_version=CURRENT_SCHEMA_VERSION,
        )

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

    def admit_cash_correction(
        self,
        correction: CashCorrectionInput,
        *,
        evidence_root: Path,
    ) -> tuple[SourceSnapshot, bool]:
        """Append one screenshot-backed cash correction atomically.

        Ordinary PDF imports retain their undated-conflict rule.  This separate
        path admits only an explicitly linked successor and verifies every
        retained PNG before opening the database for writes.
        """
        artifacts = validate_cash_correction(correction, evidence_root=evidence_root)
        fingerprint = _cash_correction_fingerprint(correction)
        primary = artifacts[ScreenshotArtifactRole.PRIMARY_ACCOUNT_CONTEXT]
        with self._write_connection() as connection:
            existing = connection.execute(
                "SELECT successor_snapshot_id FROM source_snapshot_supersessions WHERE correction_id = ?",
                (correction.correction_id,),
            ).fetchone()
            if existing is not None:
                successor = connection.execute(
                    "SELECT * FROM source_snapshots WHERE snapshot_id = ?",
                    (int(existing["successor_snapshot_id"]),),
                ).fetchone()
                assert successor is not None
                if _stored_cash_correction_matches(
                    connection,
                    correction=correction,
                    artifacts=artifacts,
                    fingerprint=fingerprint,
                    successor=successor,
                ):
                    return _source_snapshot(successor), False
                raise SourceConflictError(
                    "cash correction identity already exists with conflicting evidence or payload"
                )

            account_id = self._account_id(connection, correction.account_label, create=False)
            predecessor = connection.execute(
                "SELECT * FROM source_snapshots WHERE snapshot_id = ?",
                (correction.predecessor_snapshot_id,),
            ).fetchone()
            if predecessor is None:
                raise SourceConflictError("cash correction predecessor does not exist")
            if int(predecessor["account_id"]) != account_id or predecessor["view_type"] != "CASH":
                raise SourceConflictError(
                    "cash correction predecessor must be CASH evidence for the same account"
                )
            selected = _current_cash_snapshot_row(connection, account_id)
            if selected is None or int(selected["snapshot_id"]) != correction.predecessor_snapshot_id:
                raise SourceConflictError(
                    "cash correction predecessor is not the currently selected cash observation"
                )
            if connection.execute(
                "SELECT 1 FROM source_snapshot_supersessions WHERE predecessor_snapshot_id = ?",
                (correction.predecessor_snapshot_id,),
            ).fetchone():
                raise SourceConflictError("cash correction predecessor already has a successor")

            timestamp = _now().isoformat()
            cursor = connection.execute(
                "INSERT INTO source_snapshots "
                "(account_id, source_filename, content_sha256, source_type, view_type, source_date, ingested_at, evidence_status, evidence_fingerprint) "
                "VALUES (?, ?, ?, 'ERSTE_SCREENSHOT', 'CASH', ?, ?, ?, ?)",
                (
                    account_id,
                    primary.source_filename,
                    primary.content_sha256,
                    correction.source_date.isoformat() if correction.source_date else None,
                    timestamp,
                    correction.evidence_status,
                    fingerprint,
                ),
            )
            successor_id = _last_row_id(cursor)
            for cash in correction.cash:
                connection.execute(
                    "INSERT INTO cash_snapshots "
                    "(snapshot_id, account_id, currency, balance, data_quality_status) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        successor_id,
                        account_id,
                        cash.currency.upper(),
                        _decimal_text(cash.balance),
                        cash.data_quality_status,
                    ),
                )
            for artifact in correction.artifacts:
                connection.execute(
                    "INSERT INTO screenshot_artifacts "
                    "(snapshot_id, artifact_role, source_filename, retained_path, content_sha256, media_type, byte_count) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        successor_id,
                        artifact.role.value,
                        artifact.source_filename,
                        artifact.retained_path,
                        artifact.content_sha256,
                        artifact.media_type,
                        artifact.byte_count,
                    ),
                )
            connection.execute(
                "INSERT INTO source_snapshot_supersessions "
                "(correction_id, account_id, scope_view_type, predecessor_snapshot_id, successor_snapshot_id, reason, recorded_at) "
                "VALUES (?, ?, 'CASH', ?, ?, ?, ?)",
                (
                    correction.correction_id,
                    account_id,
                    correction.predecessor_snapshot_id,
                    successor_id,
                    correction.reason,
                    timestamp,
                ),
            )
            selected_after = _current_cash_snapshot_row(connection, account_id)
            if selected_after is None or int(selected_after["snapshot_id"]) != successor_id:
                raise SourceConflictError("cash correction did not become the deterministic selection")
            successor = connection.execute(
                "SELECT * FROM source_snapshots WHERE snapshot_id = ?", (successor_id,)
            ).fetchone()
            assert successor is not None
            return _source_snapshot(successor), True

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
                "(snapshot_id, account_id, instrument_id, provider_name, normalized_provider_name, quantity, unit_price, market_value, market_currency, reporting_value, reporting_currency, observed_roi, data_quality_status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    _decimal_text(position.observed_roi),
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

    def schema_version(self) -> int:
        """Read the declared schema version without opening the ledger for writes."""
        with self._read_connection() as connection:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])

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
            row = _current_cash_snapshot_row(connection, account.account_id)
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
        _nullable_finite(position.observed_roi, "observed ROI")
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


def validate_cash_correction(
    correction: CashCorrectionInput,
    *,
    evidence_root: Path,
) -> dict[ScreenshotArtifactRole, ScreenshotArtifactInput]:
    """Validate a screenshot correction and its retained immutable bytes."""
    _validate_tbsz_label(correction.account_label)
    if not correction.correction_id.strip():
        raise TbszError("cash correction identity is required")
    if correction.predecessor_snapshot_id <= 0:
        raise TbszError("cash correction predecessor must be a positive snapshot id")
    if not correction.reason.strip():
        raise TbszError("cash correction reason is required")
    if not correction.evidence_status.strip():
        raise TbszError("cash correction evidence status is required")
    if not correction.cash:
        raise TbszError("cash correction requires at least one cash row")
    currencies: set[str] = set()
    for cash in correction.cash:
        currency = _validate_currency(cash.currency)
        if currency in currencies:
            raise TbszError("cash correction currencies must be unique")
        currencies.add(currency)
        _nullable_nonnegative(cash.balance, "cash correction balance")
        if not cash.data_quality_status.strip():
            raise TbszError("cash correction data quality status is required")

    artifacts: dict[ScreenshotArtifactRole, ScreenshotArtifactInput] = {}
    for artifact in correction.artifacts:
        if artifact.role in artifacts:
            raise TbszError("screenshot artifact roles must be unique")
        if Path(artifact.source_filename).name != artifact.source_filename:
            raise TbszError("screenshot source filename must be a plain filename")
        if not artifact.source_filename.casefold().endswith(".png"):
            raise TbszError("screenshot evidence must retain its PNG filename")
        _validate_sha256(artifact.content_sha256, "screenshot content SHA-256")
        if artifact.media_type != "image/png":
            raise TbszError("screenshot media type must be image/png")
        if artifact.byte_count <= 0:
            raise TbszError("screenshot byte count must be positive")
        _verify_retained_screenshot(artifact, evidence_root=evidence_root)
        artifacts[artifact.role] = artifact
    expected_roles = {
        ScreenshotArtifactRole.PRIMARY_ACCOUNT_CONTEXT,
        ScreenshotArtifactRole.SUPPORTING_CROP,
    }
    if set(artifacts) != expected_roles:
        raise TbszError("cash correction requires primary account context and supporting crop artifacts")
    if len({artifact.content_sha256 for artifact in artifacts.values()}) != 2:
        raise TbszError("primary and supporting screenshots must be distinct bytes")
    if len({artifact.retained_path for artifact in artifacts.values()}) != 2:
        raise TbszError("primary and supporting screenshots must have distinct retained paths")
    return artifacts


def _verify_retained_screenshot(
    artifact: ScreenshotArtifactInput,
    *,
    evidence_root: Path,
) -> None:
    if not evidence_root.is_dir() or evidence_root.is_symlink():
        raise TbszError("screenshot evidence root must be an existing non-symlink directory")
    relative_path = Path(artifact.retained_path)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise TbszError("retained screenshot path must stay below the evidence root")
    root = evidence_root.resolve(strict=True)
    candidate = evidence_root / relative_path
    if candidate.is_symlink() or not candidate.is_file():
        raise TbszError("retained screenshot must be an existing non-symlink file")
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise TbszError("retained screenshot escapes the evidence root") from error
    current = candidate.parent
    while current != evidence_root:
        if current.is_symlink():
            raise TbszError("retained screenshot path contains a symlink")
        current = current.parent
    if candidate.stat().st_size != artifact.byte_count:
        raise SourceConflictError("retained screenshot byte count differs from admission evidence")
    if _sha256_file(candidate) != artifact.content_sha256:
        raise SourceConflictError("retained screenshot hash differs from admission evidence")
    with candidate.open("rb") as source:
        if source.read(8) != b"\x89PNG\r\n\x1a\n":
            raise TbszError("retained screenshot is not a PNG byte stream")


def _current_cash_snapshot_row(
    connection: sqlite3.Connection,
    account_id: int,
) -> sqlite3.Row | None:
    """Select the base PDF observation, then follow its explicit correction chain."""
    row = connection.execute(
        "SELECT * FROM source_snapshots "
        "WHERE account_id = ? AND view_type = 'CASH' AND source_type = 'GEORGE_PDF' "
        "ORDER BY source_date IS NULL, source_date DESC, snapshot_id DESC LIMIT 1",
        (account_id,),
    ).fetchone()
    if row is None:
        return None
    seen = {int(row["snapshot_id"])}
    while True:
        successors = connection.execute(
            "SELECT successor.*, edge.account_id AS supersession_account_id, "
            "edge.scope_view_type AS supersession_scope_view_type "
            "FROM source_snapshot_supersessions AS edge "
            "JOIN source_snapshots AS successor ON successor.snapshot_id = edge.successor_snapshot_id "
            "WHERE edge.predecessor_snapshot_id = ?",
            (int(row["snapshot_id"]),),
        ).fetchall()
        if not successors:
            return row
        if len(successors) != 1:
            raise TbszError("cash correction graph has ambiguous competing successors")
        successor = successors[0]
        successor_id = int(successor["snapshot_id"])
        if successor_id in seen:
            raise TbszError("cash correction graph contains a cycle")
        if (
            int(successor["supersession_account_id"]) != account_id
            or successor["supersession_scope_view_type"] != "CASH"
            or
            int(successor["account_id"]) != account_id
            or successor["view_type"] != "CASH"
            or successor["source_type"] != "ERSTE_SCREENSHOT"
        ):
            raise TbszError("cash correction successor crosses its account or CASH scope")
        seen.add(successor_id)
        row = successor


def _cash_correction_fingerprint(correction: CashCorrectionInput) -> str:
    payload = {
        "correction_id": correction.correction_id,
        "account_label": correction.account_label,
        "predecessor_snapshot_id": correction.predecessor_snapshot_id,
        "reason": correction.reason,
        "source_date": correction.source_date.isoformat() if correction.source_date else None,
        "evidence_status": correction.evidence_status,
        "cash": [
            {
                "currency": item.currency.upper(),
                "balance": _decimal_text(item.balance),
                "data_quality_status": item.data_quality_status,
            }
            for item in sorted(correction.cash, key=lambda item: item.currency.upper())
        ],
        "artifacts": [
            {
                "role": item.role.value,
                "source_filename": item.source_filename,
                "retained_path": item.retained_path,
                "content_sha256": item.content_sha256,
                "media_type": item.media_type,
                "byte_count": item.byte_count,
            }
            for item in sorted(correction.artifacts, key=lambda item: item.role.value)
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _stored_cash_correction_matches(
    connection: sqlite3.Connection,
    *,
    correction: CashCorrectionInput,
    artifacts: dict[ScreenshotArtifactRole, ScreenshotArtifactInput],
    fingerprint: str,
    successor: sqlite3.Row,
) -> bool:
    account = connection.execute(
        "SELECT label FROM tbsz_accounts WHERE account_id = ?",
        (int(successor["account_id"]),),
    ).fetchone()
    edge = connection.execute(
        "SELECT * FROM source_snapshot_supersessions WHERE correction_id = ?",
        (correction.correction_id,),
    ).fetchone()
    stored_cash = tuple(
        (str(row["currency"]), str(row["balance"]), str(row["data_quality_status"]))
        for row in connection.execute(
            "SELECT currency, balance, data_quality_status FROM cash_snapshots "
            "WHERE snapshot_id = ? ORDER BY currency",
            (int(successor["snapshot_id"]),),
        )
    )
    expected_cash = tuple(
        (item.currency.upper(), _decimal_text(item.balance), item.data_quality_status)
        for item in sorted(correction.cash, key=lambda item: item.currency.upper())
    )
    stored_artifacts = tuple(
        (
            str(row["artifact_role"]),
            str(row["source_filename"]),
            str(row["retained_path"]),
            str(row["content_sha256"]),
            str(row["media_type"]),
            int(row["byte_count"]),
        )
        for row in connection.execute(
            "SELECT artifact_role, source_filename, retained_path, content_sha256, media_type, byte_count "
            "FROM screenshot_artifacts WHERE snapshot_id = ? ORDER BY artifact_role",
            (int(successor["snapshot_id"]),),
        )
    )
    expected_artifacts = tuple(
        (
            role.value,
            artifact.source_filename,
            artifact.retained_path,
            artifact.content_sha256,
            artifact.media_type,
            artifact.byte_count,
        )
        for role, artifact in sorted(artifacts.items(), key=lambda item: item[0].value)
    )
    primary = artifacts[ScreenshotArtifactRole.PRIMARY_ACCOUNT_CONTEXT]
    return bool(
        account
        and edge
        and account["label"] == correction.account_label
        and int(edge["account_id"]) == int(successor["account_id"])
        and edge["scope_view_type"] == "CASH"
        and int(edge["predecessor_snapshot_id"]) == correction.predecessor_snapshot_id
        and edge["reason"] == correction.reason
        and successor["source_filename"] == primary.source_filename
        and successor["content_sha256"] == primary.content_sha256
        and successor["source_type"] == "ERSTE_SCREENSHOT"
        and successor["view_type"] == "CASH"
        and successor["source_date"]
        == (correction.source_date.isoformat() if correction.source_date else None)
        and successor["evidence_status"] == correction.evidence_status
        and successor["evidence_fingerprint"] == fingerprint
        and stored_cash == expected_cash
        and stored_artifacts == expected_artifacts
    )


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


def _validate_sha256(value: str, field: str) -> str:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise TbszError(f"{field} is malformed")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


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


def _nullable_finite(value: Decimal | None, field: str) -> Decimal | None:
    if value is None:
        return None
    if not value.is_finite():
        raise TbszError(f"{field} must be finite when supplied")
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
                "observed_roi": _decimal_text(item.observed_roi),
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
        observed_roi=_decimal(row["observed_roi"]),
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


_V2_SCHEMA = """
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
    observed_roi TEXT NULL,
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

_SCREENSHOT_SCHEMA = """
CREATE TABLE IF NOT EXISTS screenshot_artifacts (
    artifact_id INTEGER PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES source_snapshots(snapshot_id),
    artifact_role TEXT NOT NULL CHECK(artifact_role IN ('PRIMARY_ACCOUNT_CONTEXT', 'SUPPORTING_CROP')),
    source_filename TEXT NOT NULL,
    retained_path TEXT NOT NULL UNIQUE,
    content_sha256 TEXT NOT NULL UNIQUE,
    media_type TEXT NOT NULL CHECK(media_type = 'image/png'),
    byte_count INTEGER NOT NULL CHECK(byte_count > 0),
    UNIQUE(snapshot_id, artifact_role)
);
CREATE TABLE IF NOT EXISTS source_snapshot_supersessions (
    supersession_id INTEGER PRIMARY KEY,
    correction_id TEXT NOT NULL UNIQUE,
    account_id INTEGER NOT NULL REFERENCES tbsz_accounts(account_id),
    scope_view_type TEXT NOT NULL CHECK(scope_view_type = 'CASH'),
    predecessor_snapshot_id INTEGER NOT NULL REFERENCES source_snapshots(snapshot_id),
    successor_snapshot_id INTEGER NOT NULL REFERENCES source_snapshots(snapshot_id),
    reason TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    CHECK(predecessor_snapshot_id <> successor_snapshot_id),
    UNIQUE(predecessor_snapshot_id),
    UNIQUE(successor_snapshot_id)
);
"""

_SCHEMA = _V2_SCHEMA.replace(
    "CHECK(source_type = 'GEORGE_PDF')",
    "CHECK(source_type IN ('GEORGE_PDF', 'ERSTE_SCREENSHOT'))",
) + _SCREENSHOT_SCHEMA

# Historical schemas are derived recognition fixtures, never parallel authorities.
_V1_SCHEMA = _V2_SCHEMA.replace("    observed_roi TEXT NULL,\n", "")


_EXPECTED_V2_COLUMN_CONTRACT: dict[str, tuple[tuple[str, str, int, str | None, int], ...]] = {
    "tbsz_accounts": (
        ("account_id", "INTEGER", 0, None, 1),
        ("label", "TEXT", 1, None, 0),
        ("created_at", "TEXT", 1, "CURRENT_TIMESTAMP", 0),
    ),
    "source_snapshots": (
        ("snapshot_id", "INTEGER", 0, None, 1),
        ("account_id", "INTEGER", 1, None, 0),
        ("source_filename", "TEXT", 1, None, 0),
        ("content_sha256", "TEXT", 1, None, 0),
        ("source_type", "TEXT", 1, None, 0),
        ("view_type", "TEXT", 1, None, 0),
        ("source_date", "TEXT", 0, None, 0),
        ("ingested_at", "TEXT", 1, None, 0),
        ("evidence_status", "TEXT", 1, None, 0),
        ("evidence_fingerprint", "TEXT", 1, None, 0),
    ),
    "instruments": (
        ("instrument_id", "INTEGER", 0, None, 1),
        ("canonical_name", "TEXT", 1, None, 0),
        ("normalized_name", "TEXT", 1, None, 0),
        ("isin", "TEXT", 0, None, 0),
        ("identity_status", "TEXT", 1, None, 0),
    ),
    "instrument_aliases": (
        ("alias_id", "INTEGER", 0, None, 1),
        ("instrument_id", "INTEGER", 1, None, 0),
        ("alias_name", "TEXT", 1, None, 0),
        ("normalized_alias", "TEXT", 1, None, 0),
        ("mapping_method", "TEXT", 1, None, 0),
        ("source_snapshot_id", "INTEGER", 0, None, 0),
    ),
    "position_snapshots": (
        ("position_id", "INTEGER", 0, None, 1),
        ("snapshot_id", "INTEGER", 1, None, 0),
        ("account_id", "INTEGER", 1, None, 0),
        ("instrument_id", "INTEGER", 1, None, 0),
        ("provider_name", "TEXT", 1, None, 0),
        ("normalized_provider_name", "TEXT", 1, None, 0),
        ("quantity", "TEXT", 0, None, 0),
        ("unit_price", "TEXT", 0, None, 0),
        ("market_value", "TEXT", 0, None, 0),
        ("market_currency", "TEXT", 0, None, 0),
        ("reporting_value", "TEXT", 0, None, 0),
        ("reporting_currency", "TEXT", 0, None, 0),
        ("data_quality_status", "TEXT", 1, None, 0),
        ("observed_roi", "TEXT", 0, None, 0),
    ),
    "cash_snapshots": (
        ("cash_id", "INTEGER", 0, None, 1),
        ("snapshot_id", "INTEGER", 1, None, 0),
        ("account_id", "INTEGER", 1, None, 0),
        ("currency", "TEXT", 1, None, 0),
        ("balance", "TEXT", 1, None, 0),
        ("data_quality_status", "TEXT", 1, None, 0),
    ),
    "transactions": (
        ("transaction_id", "TEXT", 0, None, 1),
        ("account_id", "INTEGER", 1, None, 0),
        ("instrument_id", "INTEGER", 1, None, 0),
        ("action", "TEXT", 1, None, 0),
        ("quantity", "TEXT", 1, None, 0),
        ("price", "TEXT", 1, None, 0),
        ("currency", "TEXT", 1, None, 0),
        ("transaction_date", "TEXT", 1, None, 0),
        ("recorded_at", "TEXT", 1, None, 0),
        ("client_reference", "TEXT", 0, None, 0),
        ("record_type", "TEXT", 1, None, 0),
    ),
}

_EXPECTED_COLUMN_CONTRACT: dict[str, tuple[tuple[str, str, int, str | None, int], ...]] = {
    **_EXPECTED_V2_COLUMN_CONTRACT,
    "screenshot_artifacts": (
        ("artifact_id", "INTEGER", 0, None, 1),
        ("snapshot_id", "INTEGER", 1, None, 0),
        ("artifact_role", "TEXT", 1, None, 0),
        ("source_filename", "TEXT", 1, None, 0),
        ("retained_path", "TEXT", 1, None, 0),
        ("content_sha256", "TEXT", 1, None, 0),
        ("media_type", "TEXT", 1, None, 0),
        ("byte_count", "INTEGER", 1, None, 0),
    ),
    "source_snapshot_supersessions": (
        ("supersession_id", "INTEGER", 0, None, 1),
        ("correction_id", "TEXT", 1, None, 0),
        ("account_id", "INTEGER", 1, None, 0),
        ("scope_view_type", "TEXT", 1, None, 0),
        ("predecessor_snapshot_id", "INTEGER", 1, None, 0),
        ("successor_snapshot_id", "INTEGER", 1, None, 0),
        ("reason", "TEXT", 1, None, 0),
        ("recorded_at", "TEXT", 1, None, 0),
    ),
}

_EXPECTED_FOREIGN_KEYS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "tbsz_accounts": (),
    "source_snapshots": (("account_id", "tbsz_accounts", "account_id"),),
    "instruments": (),
    "instrument_aliases": (
        ("instrument_id", "instruments", "instrument_id"),
        ("source_snapshot_id", "source_snapshots", "snapshot_id"),
    ),
    "position_snapshots": (
        ("account_id", "tbsz_accounts", "account_id"),
        ("instrument_id", "instruments", "instrument_id"),
        ("snapshot_id", "source_snapshots", "snapshot_id"),
    ),
    "cash_snapshots": (
        ("account_id", "tbsz_accounts", "account_id"),
        ("snapshot_id", "source_snapshots", "snapshot_id"),
    ),
    "screenshot_artifacts": (("snapshot_id", "source_snapshots", "snapshot_id"),),
    "source_snapshot_supersessions": (
        ("account_id", "tbsz_accounts", "account_id"),
        ("predecessor_snapshot_id", "source_snapshots", "snapshot_id"),
        ("successor_snapshot_id", "source_snapshots", "snapshot_id"),
    ),
    "transactions": (
        ("account_id", "tbsz_accounts", "account_id"),
        ("instrument_id", "instruments", "instrument_id"),
    ),
}

_EXPECTED_UNIQUE_CONSTRAINTS: dict[str, tuple[tuple[str, ...], ...]] = {
    "tbsz_accounts": (("label",),),
    "source_snapshots": (("source_filename",),),
    "instruments": (("isin",), ("normalized_name",)),
    "instrument_aliases": (("instrument_id", "normalized_alias", "mapping_method", "source_snapshot_id"),),
    "position_snapshots": (("snapshot_id", "normalized_provider_name"),),
    "cash_snapshots": (("snapshot_id", "currency"),),
    "screenshot_artifacts": (
        ("content_sha256",),
        ("retained_path",),
        ("snapshot_id", "artifact_role"),
    ),
    "source_snapshot_supersessions": (
        ("correction_id",),
        ("predecessor_snapshot_id",),
        ("successor_snapshot_id",),
    ),
    "transactions": (("account_id", "client_reference"),),
}

_EXPECTED_CHECK_SNIPPETS: dict[str, tuple[str, ...]] = {
    "source_snapshots": (
        "check(source_typein('george_pdf','erste_screenshot'))",
        "check(view_typein('positions','cash'))",
    ),
    "screenshot_artifacts": (
        "check(artifact_rolein('primary_account_context','supporting_crop'))",
        "check(media_type='image/png')",
        "check(byte_count>0)",
    ),
    "source_snapshot_supersessions": (
        "check(scope_view_type='cash')",
        "check(predecessor_snapshot_id<>successor_snapshot_id)",
    ),
    "instruments": (
        "check(identity_statusin('exact_isin','manual_confirmed','provider_name_exact_candidate','identity_unresolved'))",
    ),
    "instrument_aliases": ("check(mapping_methodin('exact_provider_name','manual_confirmed'))",),
    "transactions": (
        "check(actionin('buy','sell'))",
        "check(record_type='manual_user_executed')",
    ),
}

_EXPECTED_V2_CHECK_SNIPPETS: dict[str, tuple[str, ...]] = {
    **{
        table: snippets
        for table, snippets in _EXPECTED_CHECK_SNIPPETS.items()
        if table not in {"source_snapshots", "screenshot_artifacts", "source_snapshot_supersessions"}
    },
    "source_snapshots": (
        "check(source_type='george_pdf')",
        "check(view_typein('positions','cash'))",
    ),
}


_EXPECTED_V1_COLUMN_CONTRACT: dict[str, tuple[tuple[str, str, int, str | None, int], ...]] = {
    **_EXPECTED_V2_COLUMN_CONTRACT,
    "position_snapshots": tuple(
        column
        for column in _EXPECTED_V2_COLUMN_CONTRACT["position_snapshots"]
        if column[0] != "observed_roi"
    ),
}


def tbsz_schema_issues(connection: sqlite3.Connection) -> tuple[str, ...]:
    """Return structural differences from the canonical current TBSZ schema."""
    return _schema_issues(connection, _EXPECTED_COLUMN_CONTRACT, _EXPECTED_CHECK_SNIPPETS)


def tbsz_v2_schema_issues(connection: sqlite3.Connection) -> tuple[str, ...]:
    """Recognize the exact screenshot-predecessor schema."""
    return _schema_issues(connection, _EXPECTED_V2_COLUMN_CONTRACT, _EXPECTED_V2_CHECK_SNIPPETS)


def tbsz_v1_schema_issues(connection: sqlite3.Connection) -> tuple[str, ...]:
    """Recognize the only supported migration source schema exactly."""
    return _schema_issues(connection, _EXPECTED_V1_COLUMN_CONTRACT, _EXPECTED_V2_CHECK_SNIPPETS)


def _schema_issues(
    connection: sqlite3.Connection,
    expected_columns: dict[str, tuple[tuple[str, str, int, str | None, int], ...]],
    expected_checks: dict[str, tuple[str, ...]],
) -> tuple[str, ...]:
    actual_tables = _application_tables(connection)
    expected_tables = set(expected_columns)
    issues: list[str] = []
    if missing := sorted(expected_tables - actual_tables):
        issues.append(f"missing tables: {', '.join(missing)}")
    if unexpected := sorted(actual_tables - expected_tables):
        issues.append(f"unexpected tables: {', '.join(unexpected)}")
    for table in sorted(expected_tables & actual_tables):
        columns = tuple(
            (str(row[1]), str(row[2]).upper(), int(row[3]), row[4], int(row[5]))
            for row in connection.execute(f"PRAGMA table_info({_quote_identifier(table)})")
        )
        if columns != expected_columns[table]:
            issues.append(f"column contract differs for {table}")
        foreign_keys = tuple(
            sorted(
                (str(row[3]), str(row[2]), str(row[4]))
                for row in connection.execute(f"PRAGMA foreign_key_list({_quote_identifier(table)})")
            )
        )
        if foreign_keys != _EXPECTED_FOREIGN_KEYS[table]:
            issues.append(f"foreign-key contract differs for {table}")
        unique_constraints = tuple(
            sorted(
                tuple(
                    str(column[2])
                    for column in connection.execute(f"PRAGMA index_info({_quote_identifier(str(index[1]))})")
                )
                for index in connection.execute(f"PRAGMA index_list({_quote_identifier(table)})")
                if str(index[3]) == "u"
            )
        )
        if unique_constraints != _EXPECTED_UNIQUE_CONSTRAINTS[table]:
            issues.append(f"unique-constraint contract differs for {table}")
        ddl_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        ddl = _normalized_sql(str(ddl_row[0])) if ddl_row and ddl_row[0] else ""
        if any(check not in ddl for check in expected_checks.get(table, ())):
            issues.append(f"check-constraint contract differs for {table}")
    return tuple(issues)


def _initialize_schema(connection: sqlite3.Connection) -> None:
    """Create v3 or apply the supported data-preserving upgrade chain."""
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version == CURRENT_SCHEMA_VERSION:
        _require_current_schema(connection)
        return
    if version not in {0, 1, 2}:
        raise TbszSchemaMigrationError(
            f"unsupported TBSZ schema version {version}; expected 0, 1, 2, or {CURRENT_SCHEMA_VERSION}"
        )
    existing_tables = _application_tables(connection)
    if existing_tables:
        if version == 2:
            _require_v2_schema(connection)
        else:
            _require_v1_schema(connection)

    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.execute("BEGIN IMMEDIATE")
        if not existing_tables:
            _execute_schema(connection, _SCHEMA)
        else:
            if version == 0:
                connection.execute("PRAGMA user_version = 1")
            if version in {0, 1}:
                connection.execute(
                    "ALTER TABLE position_snapshots ADD COLUMN observed_roi TEXT NULL"
                )
            _migrate_v2_to_v3(connection)
        connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")
        _require_current_schema(connection)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")
    if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
        raise TbszSchemaMigrationError("could not restore foreign-key enforcement after TBSZ migration")


def _execute_schema(connection: sqlite3.Connection, schema: str) -> None:
    for statement in schema.split(";"):
        if statement := statement.strip():
            connection.execute(statement)


def _migrate_v2_to_v3(connection: sqlite3.Connection) -> None:
    """Widen source provenance and add append-only screenshot correction lineage."""
    connection.execute(
        """CREATE TABLE source_snapshots_v3 (
            snapshot_id INTEGER PRIMARY KEY,
            account_id INTEGER NOT NULL REFERENCES tbsz_accounts(account_id),
            source_filename TEXT NOT NULL UNIQUE,
            content_sha256 TEXT NOT NULL,
            source_type TEXT NOT NULL CHECK(source_type IN ('GEORGE_PDF', 'ERSTE_SCREENSHOT')),
            view_type TEXT NOT NULL CHECK(view_type IN ('POSITIONS', 'CASH')),
            source_date TEXT NULL,
            ingested_at TEXT NOT NULL,
            evidence_status TEXT NOT NULL,
            evidence_fingerprint TEXT NOT NULL
        )"""
    )
    connection.execute(
        "INSERT INTO source_snapshots_v3 SELECT * FROM source_snapshots"
    )
    connection.execute("DROP TABLE source_snapshots")
    connection.execute("ALTER TABLE source_snapshots_v3 RENAME TO source_snapshots")
    _execute_schema(connection, _SCREENSHOT_SCHEMA)


def _require_current_schema(connection: sqlite3.Connection) -> None:
    _require_schema(connection, tbsz_schema_issues)


def _require_v2_schema(connection: sqlite3.Connection) -> None:
    _require_schema(connection, tbsz_v2_schema_issues)


def _require_v1_schema(connection: sqlite3.Connection) -> None:
    _require_schema(connection, tbsz_v1_schema_issues)


def _require_schema(
    connection: sqlite3.Connection,
    issue_detector: Callable[[sqlite3.Connection], tuple[str, ...]],
) -> None:
    if issues := issue_detector(connection):
        raise TbszSchemaMigrationError(
            "TBSZ schema is not a recognized migration source: " + "; ".join(issues)
        )
    integrity = tuple(str(row[0]) for row in connection.execute("PRAGMA integrity_check"))
    if integrity != ("ok",):
        raise TbszSchemaMigrationError("TBSZ schema integrity_check did not return ok; migration is blocked")
    foreign_key_violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_key_violations:
        raise TbszSchemaMigrationError("TBSZ schema has foreign-key violations; migration is blocked")


def _application_tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _create_verified_backup(
    path: Path,
    *,
    source_version: int,
    target_version: int,
) -> Path:
    """Create an SQLite-consistent, verified, non-overwriting migration backup."""
    backup_directory = path.parent / "backups"
    backup_directory.mkdir(parents=True, exist_ok=True)
    timestamp = _now().strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = backup_directory / (
        f"{path.stem}-v{source_version}-before-v{target_version}-"
        f"{timestamp}-{uuid.uuid4().hex}.sqlite"
    )
    temporary_path = backup_directory / f".{backup_path.name}.tmp"
    if backup_path.exists() or temporary_path.exists():
        raise TbszSchemaMigrationError("refusing to overwrite an existing TBSZ migration backup")
    try:
        with (
            sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as source,
            sqlite3.connect(temporary_path) as destination,
        ):
            source.backup(destination)
        with sqlite3.connect(f"file:{temporary_path.resolve()}?mode=ro", uri=True) as backup:
            if int(backup.execute("PRAGMA user_version").fetchone()[0]) != source_version:
                raise TbszSchemaMigrationError("TBSZ migration backup version does not match the migration source")
            if source_version == 2:
                _require_v2_schema(backup)
            else:
                _require_v1_schema(backup)
        temporary_path.rename(backup_path)
    except (OSError, sqlite3.Error) as error:
        raise TbszSchemaMigrationError("could not create and verify TBSZ migration backup") from error
    return backup_path


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _normalized_sql(value: str) -> str:
    return "".join(value.casefold().split())
