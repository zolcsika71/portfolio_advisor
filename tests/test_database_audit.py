"""Synthetic tests for read-only local SQLite health auditing and TBSZ migrations."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from portfolio_advisor.database.audit import (
    audit_database_directory,
    audit_named_database,
)
from portfolio_advisor.tbsz.models import TbszError
from portfolio_advisor.tbsz.repository import (
    _V1_SCHEMA,
    CURRENT_SCHEMA_VERSION,
    TbszPortfolioRepository,
)


def _initialize_tbsz(path: Path) -> TbszPortfolioRepository:
    repository = TbszPortfolioRepository(path)
    repository.initialize()
    return repository


def _version(path: Path) -> int:
    with sqlite3.connect(path) as connection:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])


def test_fresh_tbsz_database_receives_current_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "tbsz_portfolio.sqlite"
    _initialize_tbsz(path)
    assert _version(path) == CURRENT_SCHEMA_VERSION


def test_recognized_legacy_tbsz_schema_migrates_idempotently_and_preserves_rows(tmp_path: Path) -> None:
    path = tmp_path / "tbsz_portfolio.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript(_V1_SCHEMA)
        connection.execute("INSERT INTO tbsz_accounts (label) VALUES ('TBSZ synthetic')")
        connection.execute("PRAGMA user_version = 0")

    repository = _initialize_tbsz(path)
    repository.initialize()

    assert _version(path) == CURRENT_SCHEMA_VERSION
    assert [account.label for account in repository.accounts()] == ["TBSZ synthetic"]


def test_unexpected_tbsz_schema_version_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "tbsz_portfolio.sqlite"
    _initialize_tbsz(path)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 99")

    with pytest.raises(TbszError, match="unsupported TBSZ schema version"):
        TbszPortfolioRepository(path).initialize()
    assert _version(path) == 99


def test_unrecognized_legacy_tbsz_schema_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "tbsz_portfolio.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE tbsz_accounts (account_id INTEGER PRIMARY KEY, label TEXT)")

    with pytest.raises(TbszError, match="not a recognized migration source"):
        TbszPortfolioRepository(path).initialize()
    assert _version(path) == 0


def test_read_only_audit_reports_healthy_tbsz_and_never_requires_production_data(tmp_path: Path) -> None:
    database_root = tmp_path / "database"
    database_root.mkdir()
    _initialize_tbsz(database_root / "tbsz_portfolio.sqlite")

    result = audit_named_database(database_root, "tbsz_portfolio.sqlite")

    assert result.healthy is True
    assert result.schema_status == "MATCHES_CURRENT_SCHEMA"
    assert result.user_version == CURRENT_SCHEMA_VERSION
    assert result.foreign_key_violations == ()
    assert result.tbsz_invariant_violations == ()


def test_audit_detects_corrupt_file_and_foreign_key_violation(tmp_path: Path) -> None:
    database_root = tmp_path / "database"
    database_root.mkdir()
    (database_root / "invalid.sqlite").write_bytes(b"not a sqlite database")
    foreign_key_path = database_root / "foreign-key.db"
    with sqlite3.connect(foreign_key_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
        connection.execute("CREATE TABLE child (id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parent(id))")
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("INSERT INTO child VALUES (1, 999)")

    results = {result.relative_path: result for result in audit_database_directory(database_root)}

    assert results["invalid.sqlite"].healthy is False
    assert results["invalid.sqlite"].schema_status == "CORRUPT_OR_INVALID"
    assert len(results["foreign-key.db"].foreign_key_violations) == 1
    assert results["foreign-key.db"].healthy is False


def test_audit_detects_tbsz_schema_drift_and_ignores_outside_symlink(tmp_path: Path) -> None:
    database_root = tmp_path / "database"
    database_root.mkdir()
    drifted = database_root / "tbsz_portfolio.sqlite"
    with sqlite3.connect(drifted) as connection:
        connection.execute("CREATE TABLE tbsz_accounts (account_id INTEGER PRIMARY KEY, label TEXT)")
    external = tmp_path / "outside.sqlite"
    with sqlite3.connect(external) as connection:
        connection.execute("CREATE TABLE outside_data (id INTEGER PRIMARY KEY)")
    (database_root / "outside-link.sqlite").symlink_to(external)

    results = audit_database_directory(database_root)

    assert [result.relative_path for result in results] == ["tbsz_portfolio.sqlite"]
    assert results[0].healthy is False
    assert results[0].schema_status == "SCHEMA_DRIFT"


def test_audit_detects_tbsz_logical_invariant_violations_without_repairing_rows(tmp_path: Path) -> None:
    database_root = tmp_path / "database"
    database_root.mkdir()
    path = database_root / "tbsz_portfolio.sqlite"
    _initialize_tbsz(path)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute("INSERT INTO tbsz_accounts (account_id, label) VALUES (1, 'Normal account')")
        connection.execute(
            """INSERT INTO source_snapshots
               (snapshot_id, account_id, source_filename, content_sha256, source_type, view_type,
                source_date, ingested_at, evidence_status, evidence_fingerprint)
               VALUES (1, 1, 'synthetic.pdf', 'bad', 'GEORGE_PDF', 'CASH', NULL,
                       '2026-01-01T00:00:00+00:00', 'SYNTHETIC', 'fingerprint')"""
        )
        connection.execute(
            """INSERT INTO cash_snapshots
               (cash_id, snapshot_id, account_id, currency, balance, data_quality_status)
               VALUES (1, 1, 1, 'EURO', 'NaN', 'SYNTHETIC')"""
        )
        connection.execute(
            """INSERT INTO instruments
               (instrument_id, canonical_name, normalized_name, isin, identity_status)
               VALUES (1, 'Synthetic', 'synthetic', NULL, 'EXACT_ISIN')"""
        )
        connection.execute(
            """INSERT INTO transactions
               (transaction_id, account_id, instrument_id, action, quantity, price, currency,
                transaction_date, recorded_at, client_reference, record_type)
               VALUES ('synthetic', 1, 1, 'HOLD', '0', 'NaN', 'eur',
                       '2026-01-01', '2026-01-01T00:00:00+00:00', 'duplicate', 'WRONG')"""
        )

    result = audit_named_database(database_root, "tbsz_portfolio.sqlite")

    assert result.healthy is False
    assert any("invalid TBSZ account labels" in item for item in result.tbsz_invariant_violations)
    assert any("invalid source snapshot fields" in item for item in result.tbsz_invariant_violations)
    assert any("invalid cash currencies" in item for item in result.tbsz_invariant_violations)
    assert any("invalid cash_snapshots.balance decimal" in item for item in result.tbsz_invariant_violations)
    assert any("invalid transaction fields" in item for item in result.tbsz_invariant_violations)
    assert any("invalid transactions.quantity decimal" in item for item in result.tbsz_invariant_violations)
