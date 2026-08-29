"""Temporary-database tests for the Milestone 5 schema-v3 foundation."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from portfolio_advisor.database.migrations.backup import create_verified_backup
from portfolio_advisor.database.migrations.v2_to_v3 import (
    CutoverNotAuthorized,
    UnsupportedMigrationSource,
    dry_run_v2_to_v3,
    execute_v2_to_v3,
)
from portfolio_advisor.database.migrations.validation import (
    MigrationValidationError,
    validate_integrity,
)
from portfolio_advisor.database.schema.v3 import (
    SCHEMA_VERSION,
    AnalyticalHoldingProjection,
    ProjectionError,
    SchemaVersionError,
    connect,
    create_analytical_holding_projection,
    detect_schema_version,
    initialize_schema,
    insert_instrument,
    transaction,
    validate_schema,
)


def _connection(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "temporary-v3.sqlite")
    initialize_schema(connection)
    return connection


def _source(connection: sqlite3.Connection) -> int:
    connection.execute(
        "INSERT INTO source_file (filename, sha256, source_type, source_date) VALUES (?, ?, ?, ?)",
        ("fixture.xls", "a" * 64, "MODEL_XLS", "2024-09-17"),
    )
    connection.execute("INSERT INTO source_sheet (source_file_id, sheet_name) VALUES (1, 'modell portfóliók')")
    return 1


def _snapshot_and_instrument(connection: sqlite3.Connection) -> tuple[int, int]:
    source_sheet_id = _source(connection)
    instrument_id = insert_instrument(connection, "US0378331005", "Apple Inc.")
    connection.execute("INSERT INTO portfolio (portfolio_name, portfolio_type) VALUES ('Model', 'MODEL')")
    connection.execute(
        "INSERT INTO portfolio_snapshot (portfolio_id, snapshot_date, source_sheet_id) VALUES (1, '2024-09-17', ?)",
        (source_sheet_id,),
    )
    return 1, instrument_id


def _occurrence(
    connection: sqlite3.Connection,
    *,
    source_row: int,
    snapshot_id: int,
    instrument_id: int,
    weight: float,
    semantics: str = "UNRESOLVED_DUPLICATE_SEMANTICS",
) -> int:
    connection.execute(
        """INSERT INTO portfolio_holding_source_occurrence (
               portfolio_snapshot_id, instrument_id, source_sheet_id, source_row_number,
               reported_weight, source_payload_sha256, source_semantics_status
           ) VALUES (?, ?, 1, ?, ?, ?, ?)""",
        (
            snapshot_id,
            instrument_id,
            source_row,
            weight,
            hashlib.sha256(str(source_row).encode()).hexdigest(),
            semantics,
        ),
    )
    return int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])


def test_complete_schema_creation_and_idempotent_initialization(tmp_path: Path) -> None:
    connection = connect(tmp_path / "temporary-v3.sqlite")
    assert detect_schema_version(connection) == 0
    initialize_schema(connection)
    initialize_schema(connection)

    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}

    assert SCHEMA_VERSION == detect_schema_version(connection)
    assert {
        "source_file", "source_sheet", "instrument", "instrument_alias", "portfolio",
        "portfolio_snapshot", "portfolio_holding_source_occurrence", "portfolio_holding",
        "portfolio_holding_lineage", "portfolio_cash", "metric_definition",
        "instrument_metric_observation", "portfolio_metric_observation", "shortlist_snapshot",
        "shortlist_entry",
    } <= tables
    validate_schema(connection)


def test_foreign_keys_and_source_file_sha_uniqueness_are_enforced(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("INSERT INTO source_sheet (source_file_id, sheet_name) VALUES (999, 'missing')")
    _source(connection)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO source_file (filename, sha256, source_type, source_date) VALUES ('other.xls', ?, 'MODEL_XLS', '2024-09-18')",
            ("a" * 64,),
        )


def test_isin_uniqueness_cash_rejection_and_alias_ambiguity(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    _source(connection)
    first = insert_instrument(connection, "US0378331005", "Apple Inc.")
    with pytest.raises(sqlite3.IntegrityError):
        insert_instrument(connection, "US0378331005", "Apple duplicate")
    with pytest.raises(ValueError, match="cash"):
        insert_instrument(connection, "CASH", "Cash")
    second = insert_instrument(connection, "US5949181045", "Microsoft Corp.")
    connection.execute(
        """INSERT INTO instrument_alias
           (instrument_id, source_file_id, source_type, source_name, normalized_source_name, mapping_status)
           VALUES (?, 1, 'MODEL_XLS', 'Apple', 'apple', 'EXACT_ALIAS_CONFIRMED')""",
        (first,),
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """INSERT INTO instrument_alias
               (instrument_id, source_file_id, source_type, source_name, normalized_source_name, mapping_status)
               VALUES (?, 1, 'MODEL_XLS', 'Apple', 'apple', 'EXACT_ALIAS_CONFIRMED')""",
            (second,),
        )
    connection.execute(
        """INSERT INTO instrument_alias
           (instrument_id, source_file_id, source_type, source_name, normalized_source_name, mapping_status)
           VALUES (NULL, 1, 'MODEL_XLS', 'Unknown', 'unknown', 'IDENTITY_UNRESOLVED')"""
    )


def test_multiple_source_occurrences_are_allowed_but_source_rows_are_unique(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    snapshot_id, instrument_id = _snapshot_and_instrument(connection)
    _occurrence(connection, source_row=33, snapshot_id=snapshot_id, instrument_id=instrument_id, weight=11.5)
    _occurrence(connection, source_row=35, snapshot_id=snapshot_id, instrument_id=instrument_id, weight=6.0)

    assert connection.execute("SELECT COUNT(*) FROM portfolio_holding_source_occurrence").fetchone()[0] == 2
    with pytest.raises(sqlite3.IntegrityError):
        _occurrence(connection, source_row=35, snapshot_id=snapshot_id, instrument_id=instrument_id, weight=6.0)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        connection.execute(
            "UPDATE portfolio_holding_source_occurrence SET reported_weight = 0 WHERE source_row_number = 33"
        )


def test_projection_requires_approved_complete_lineage_and_analytical_uniqueness(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    snapshot_id, instrument_id = _snapshot_and_instrument(connection)
    first = _occurrence(connection, source_row=33, snapshot_id=snapshot_id, instrument_id=instrument_id, weight=11.5)
    second = _occurrence(connection, source_row=35, snapshot_id=snapshot_id, instrument_id=instrument_id, weight=6.0)

    with pytest.raises(ProjectionError, match="approved derivation"):
        create_analytical_holding_projection(connection, AnalyticalHoldingProjection(
            snapshot_id, instrument_id, 17.5, "UNRESOLVED_DUPLICATE_SEMANTICS", "v1", "none", (first, second)
        ))
    with pytest.raises(ProjectionError, match="every source occurrence"):
        create_analytical_holding_projection(connection, AnalyticalHoldingProjection(
            snapshot_id, instrument_id, 11.5, "APPROVED_AGGREGATION", "v1", "approved", (first,)
        ))
    with pytest.raises(ProjectionError, match="unresolved or conflicting"):
        create_analytical_holding_projection(connection, AnalyticalHoldingProjection(
            snapshot_id, instrument_id, 17.5, "APPROVED_AGGREGATION", "v1", "human-approval-1", (first, second)
        ))

    approved_instrument = insert_instrument(connection, "US5949181045", "Microsoft Corp.")
    approved_first = _occurrence(
        connection,
        source_row=37,
        snapshot_id=snapshot_id,
        instrument_id=approved_instrument,
        weight=10.0,
        semantics="DISTINCT_SOURCE_ROWS_FOR_SAME_INSTRUMENT",
    )
    approved_second = _occurrence(
        connection,
        source_row=39,
        snapshot_id=snapshot_id,
        instrument_id=approved_instrument,
        weight=7.5,
        semantics="DISTINCT_SOURCE_ROWS_FOR_SAME_INSTRUMENT",
    )

    holding_id = create_analytical_holding_projection(connection, AnalyticalHoldingProjection(
        snapshot_id,
        approved_instrument,
        17.5,
        "APPROVED_AGGREGATION",
        "v1",
        "human-approval-1",
        (approved_first, approved_second),
    ))

    assert connection.execute(
        "SELECT COUNT(*) FROM portfolio_holding_lineage WHERE portfolio_holding_id = ?", (holding_id,)
    ).fetchone()[0] == 2
    with pytest.raises(sqlite3.IntegrityError):
        create_analytical_holding_projection(connection, AnalyticalHoldingProjection(
            snapshot_id,
            approved_instrument,
            17.5,
            "APPROVED_AGGREGATION",
            "v1",
            "human-approval-2",
            (approved_first, approved_second),
        ))


def test_cash_is_independent_from_instruments_and_separated_by_currency(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    snapshot_id, _ = _snapshot_and_instrument(connection)
    connection.execute(
        "INSERT INTO portfolio_cash (portfolio_snapshot_id, currency_code, amount, cash_role, source) VALUES (?, 'EUR', 12.5, 'AVAILABLE', 'SOURCE')",
        (snapshot_id,),
    )
    connection.execute(
        "INSERT INTO portfolio_cash (portfolio_snapshot_id, currency_code, amount, cash_role, source) VALUES (?, 'USD', 3.0, 'AVAILABLE', 'SOURCE')",
        (snapshot_id,),
    )
    assert connection.execute("SELECT COUNT(*) FROM portfolio_cash").fetchone()[0] == 2


def test_metric_observations_retain_supported_provenance(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    snapshot_id, instrument_id = _snapshot_and_instrument(connection)
    connection.execute(
        "INSERT INTO metric_definition (metric_code, name, unit, description) VALUES ('VOL', 'Volatility', 'PERCENT', 'fixture')"
    )
    connection.execute(
        """INSERT INTO instrument_metric_observation
           (instrument_id, metric_id, observation_date, value, provenance_type, source_reference)
           VALUES (?, 1, '2024-09-17', 12.3, 'PROVIDER_REPORTED', 'source row 33')""",
        (instrument_id,),
    )
    connection.execute(
        """INSERT INTO portfolio_metric_observation
           (portfolio_snapshot_id, metric_id, value, provenance_type, source_reference)
           VALUES (?, 1, 8.1, 'CALCULATED', 'calculation v1')""",
        (snapshot_id,),
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """INSERT INTO instrument_metric_observation
               (instrument_id, metric_id, observation_date, value, provenance_type)
               VALUES (?, 1, '2024-09-17', 12.3, 'INVENTED')""",
            (instrument_id,),
        )


def test_transaction_rolls_back_and_validation_detects_foreign_key_violations(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    with pytest.raises(RuntimeError, match="abort"), transaction(connection):
        connection.execute(
            "INSERT INTO source_file (filename, sha256, source_type, source_date) VALUES ('rollback.xls', ?, 'MODEL_XLS', '2024-09-17')",
            ("b" * 64,),
        )
        raise RuntimeError("abort")
    assert connection.execute("SELECT COUNT(*) FROM source_file").fetchone()[0] == 0
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("INSERT INTO source_sheet (source_file_id, sheet_name) VALUES (999, 'broken')")
    with pytest.raises(MigrationValidationError, match="foreign_key_check"):
        validate_integrity(connection)


def test_verified_backup_dry_run_and_cutover_guard(tmp_path: Path) -> None:
    source = tmp_path / "v2.sqlite"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE old_data (id INTEGER PRIMARY KEY)")
        connection.execute("PRAGMA user_version = 2")
    digest_before = hashlib.sha256(source.read_bytes()).hexdigest()

    plan = dry_run_v2_to_v3(source)
    backup = create_verified_backup(source, tmp_path / "backup.sqlite")

    assert plan.action == "DRY_RUN_ONLY_NO_DATA_MIGRATED"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest_before
    assert hashlib.sha256(backup.read_bytes()).hexdigest() == digest_before
    with pytest.raises(CutoverNotAuthorized):
        execute_v2_to_v3(source)


def test_unsupported_schema_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "unsupported.sqlite"
    with sqlite3.connect(source) as connection:
        connection.execute("PRAGMA user_version = 99")
    with pytest.raises(UnsupportedMigrationSource):
        dry_run_v2_to_v3(source)
    connection = connect(source)
    with pytest.raises(SchemaVersionError):
        initialize_schema(connection)
