"""Schema v3: provenance-first relational foundation for temporary SQLite databases.

This module intentionally creates no project-local production database and has
no importer. Raw source occurrences remain distinct from analytical holdings.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = 3
_ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_APPROVED_DERIVATION_STATUSES = frozenset({
    "APPROVED_DIRECT_OCCURRENCE",
    "APPROVED_AGGREGATION",
})


class SchemaVersionError(RuntimeError):
    """The database does not match a schema version this module may handle."""


class ProjectionError(RuntimeError):
    """An analytical holding would lose provenance or lacks approved semantics."""


@dataclass(frozen=True, slots=True)
class AnalyticalHoldingProjection:
    """One explicitly approved analytical holding with complete occurrence lineage."""

    portfolio_snapshot_id: int
    instrument_id: int
    reported_weight: float | None
    derivation_status: str
    calculation_version: str
    approval_reference: str
    source_occurrence_ids: tuple[int, ...]


def connect(path: Path) -> sqlite3.Connection:
    """Open one SQLite connection with mandatory foreign-key enforcement."""
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    enable_foreign_keys(connection)
    return connection


def enable_foreign_keys(connection: sqlite3.Connection) -> None:
    """Enable and verify SQLite foreign-key enforcement for every connection."""
    connection.execute("PRAGMA foreign_keys = ON")
    if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
        raise RuntimeError("SQLite foreign-key enforcement could not be enabled")


def detect_schema_version(connection: sqlite3.Connection) -> int:
    """Return 0 for a blank database, v3 for this schema, otherwise reject."""
    enable_foreign_keys(connection)
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if not tables:
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if user_version != 0:
            raise SchemaVersionError(f"unsupported empty database user_version {user_version}")
        return 0
    if "schema_version" not in tables:
        raise SchemaVersionError("database has no recognized schema_version table")
    rows = connection.execute("SELECT version FROM schema_version WHERE singleton = 1").fetchall()
    if len(rows) != 1:
        raise SchemaVersionError("schema_version must contain exactly one singleton row")
    version = int(rows[0][0])
    if version != SCHEMA_VERSION:
        raise SchemaVersionError(f"unsupported schema version {version}; expected {SCHEMA_VERSION}")
    return version


def initialize_schema(connection: sqlite3.Connection) -> None:
    """Idempotently create schema v3 in a blank temporary database only."""
    version = detect_schema_version(connection)
    if version == SCHEMA_VERSION:
        validate_schema(connection)
        return
    with transaction(connection):
        # sqlite3.Connection.executescript() commits an open transaction before
        # it executes its script. Execute statements individually so schema
        # initialization remains inside the explicit transaction above.
        for statement in _SCHEMA_SQL.split(";"):
            if statement.strip():
                connection.execute(statement)
        for statement in _SOURCE_OCCURRENCE_IMMUTABILITY_STATEMENTS:
            connection.execute(statement)
        connection.execute("INSERT INTO schema_version (singleton, version) VALUES (1, ?)", (SCHEMA_VERSION,))
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    validate_schema(connection)


def upgrade_schema_v3_nav_extension(connection: sqlite3.Connection) -> None:
    """Explicit, guarded additive v3 transition for historical NAV evidence."""
    if detect_schema_version(connection) != SCHEMA_VERSION:
        raise SchemaVersionError("historical NAV extension requires recognized schema v3")
    existing = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "instrument_nav_observation" not in existing:
        statement = _SCHEMA_SQL.split("CREATE TABLE instrument_nav_observation", 1)[1]
        with transaction(connection):
            connection.execute("CREATE TABLE instrument_nav_observation" + statement.split(";", 1)[0])
    validate_schema(connection)


def validate_schema(connection: sqlite3.Connection) -> None:
    """Fail closed on a missing table, integrity error, or FK violation."""
    enable_foreign_keys(connection)
    version = detect_schema_version(connection)
    if version != SCHEMA_VERSION:
        raise SchemaVersionError("schema v3 is required")
    names = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    missing = _REQUIRED_TABLES - names
    if missing:
        raise SchemaVersionError(f"schema v3 is missing tables: {', '.join(sorted(missing))}")
    integrity = tuple(str(row[0]) for row in connection.execute("PRAGMA integrity_check"))
    if integrity != ("ok",):
        raise SchemaVersionError("integrity_check did not return ok")
    violations = tuple(connection.execute("PRAGMA foreign_key_check"))
    if violations:
        raise SchemaVersionError(f"foreign_key_check reported {len(violations)} violation(s)")


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Execute an explicit transaction and roll it back on every exception."""
    enable_foreign_keys(connection)
    nested = connection.in_transaction
    if nested:
        connection.execute("SAVEPOINT schema_v3_transaction")
    else:
        connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except BaseException:
        if nested:
            connection.execute("ROLLBACK TO SAVEPOINT schema_v3_transaction")
            connection.execute("RELEASE SAVEPOINT schema_v3_transaction")
        else:
            connection.rollback()
        raise
    else:
        if nested:
            connection.execute("RELEASE SAVEPOINT schema_v3_transaction")
        else:
            connection.commit()


def insert_instrument(connection: sqlite3.Connection, isin: str, canonical_name: str) -> int:
    """Insert one validated canonical security identity; cash cannot enter this table."""
    normalized = isin.strip().upper()
    if not _valid_isin(normalized):
        raise ValueError("instrument requires a valid explicit ISIN; cash is not an instrument")
    if not canonical_name.strip():
        raise ValueError("instrument canonical_name is required")
    cursor = connection.execute(
        "INSERT INTO instrument (isin, canonical_name) VALUES (?, ?)",
        (normalized, canonical_name.strip()),
    )
    if cursor.lastrowid is None:
        raise RuntimeError("SQLite did not return an instrument id")
    return int(cursor.lastrowid)


def create_analytical_holding_projection(
    connection: sqlite3.Connection, projection: AnalyticalHoldingProjection
) -> int:
    """Create one approved, complete, non-renormalized holding projection.

    The supplied occurrence IDs must be exactly all raw occurrences for the
    snapshot/instrument pair. In particular, unresolved duplicate semantics
    cannot be projected through this API.
    """
    if projection.derivation_status not in _APPROVED_DERIVATION_STATUSES:
        raise ProjectionError("projection requires an approved derivation status")
    if not projection.calculation_version.strip() or not projection.approval_reference.strip():
        raise ProjectionError("projection requires calculation_version and approval_reference")
    if not projection.source_occurrence_ids or len(set(projection.source_occurrence_ids)) != len(projection.source_occurrence_ids):
        raise ProjectionError("projection requires a non-empty unique source occurrence set")
    with transaction(connection):
        source_rows = _source_occurrences_for_projection(connection, projection)
        source_ids = tuple(int(row["portfolio_holding_source_occurrence_id"]) for row in source_rows)
        if set(source_ids) != set(projection.source_occurrence_ids):
            raise ProjectionError("projection lineage must include every source occurrence for the snapshot and instrument")
        unresolved_rows = [
            int(row["portfolio_holding_source_occurrence_id"])
            for row in source_rows
            if str(row["source_semantics_status"])
            in {"CONFLICTING_DUPLICATE_ROWS", "UNRESOLVED_DUPLICATE_SEMANTICS"}
        ]
        if unresolved_rows:
            raise ProjectionError(
                "projection cannot aggregate unresolved or conflicting source semantics: "
                + ", ".join(str(row_id) for row_id in unresolved_rows)
            )
        if projection.derivation_status == "APPROVED_DIRECT_OCCURRENCE" and len(source_rows) != 1:
            raise ProjectionError("direct occurrence projection cannot merge multiple source rows")
        source_weight = sum(float(row["reported_weight"]) for row in source_rows if row["reported_weight"] is not None)
        if projection.reported_weight is not None and abs(projection.reported_weight - source_weight) > 1e-12:
            raise ProjectionError("projection reported_weight must equal the source occurrence total; renormalization is prohibited")
        cursor = connection.execute(
            """INSERT INTO portfolio_holding (
                   portfolio_snapshot_id, instrument_id, reported_weight, derivation_status,
                   calculation_version, approval_reference
               ) VALUES (?, ?, ?, ?, ?, ?)""",
            (
                projection.portfolio_snapshot_id, projection.instrument_id, projection.reported_weight,
                projection.derivation_status, projection.calculation_version, projection.approval_reference,
            ),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite did not return an analytical holding id")
        holding_id = int(cursor.lastrowid)
        connection.executemany(
            "INSERT INTO portfolio_holding_lineage (portfolio_holding_id, source_occurrence_id) VALUES (?, ?)",
            [(holding_id, occurrence_id) for occurrence_id in source_ids],
        )
    return holding_id


def _source_occurrences_for_projection(
    connection: sqlite3.Connection, projection: AnalyticalHoldingProjection
) -> Sequence[sqlite3.Row]:
    placeholders = ", ".join("?" for _ in projection.source_occurrence_ids)
    selected = connection.execute(
        f"""SELECT * FROM portfolio_holding_source_occurrence
            WHERE portfolio_holding_source_occurrence_id IN ({placeholders})
            ORDER BY portfolio_holding_source_occurrence_id""",
        projection.source_occurrence_ids,
    ).fetchall()
    if len(selected) != len(projection.source_occurrence_ids):
        raise ProjectionError("projection references a missing source occurrence")
    for row in selected:
        if (
            int(row["portfolio_snapshot_id"]) != projection.portfolio_snapshot_id
            or int(row["instrument_id"]) != projection.instrument_id
        ):
            raise ProjectionError("projection lineage occurrence belongs to another snapshot or instrument")
    all_rows = connection.execute(
        """SELECT * FROM portfolio_holding_source_occurrence
           WHERE portfolio_snapshot_id = ? AND instrument_id = ?
           ORDER BY portfolio_holding_source_occurrence_id""",
        (projection.portfolio_snapshot_id, projection.instrument_id),
    ).fetchall()
    return all_rows


def _valid_isin(isin: str) -> bool:
    if _ISIN.fullmatch(isin) is None:
        return False
    expanded = "".join(str(ord(char) - 55) if char.isalpha() else char for char in isin)
    total = 0
    for offset, char in enumerate(reversed(expanded)):
        value = int(char)
        if offset % 2 == 1:
            value *= 2
            value = value - 9 if value > 9 else value
        total += value
    return total % 10 == 0


_REQUIRED_TABLES = frozenset({
    "schema_version", "source_file", "source_sheet", "instrument", "instrument_alias",
    "portfolio", "portfolio_snapshot", "portfolio_holding_source_occurrence",
    "portfolio_holding", "portfolio_holding_lineage", "portfolio_cash", "metric_definition",
    "instrument_metric_observation", "portfolio_metric_observation", "shortlist_snapshot",
    "shortlist_entry", "migration_build_manifest", "instrument_nav_observation",
})

_SOURCE_OCCURRENCE_IMMUTABILITY_STATEMENTS = (
    """CREATE TRIGGER portfolio_holding_source_occurrence_immutable_update
       BEFORE UPDATE ON portfolio_holding_source_occurrence
       BEGIN
           SELECT RAISE(ABORT, 'source holding occurrences are immutable');
       END""",
    """CREATE TRIGGER portfolio_holding_source_occurrence_immutable_delete
       BEFORE DELETE ON portfolio_holding_source_occurrence
       BEGIN
           SELECT RAISE(ABORT, 'source holding occurrences are immutable');
       END""",
)


_SCHEMA_SQL = """
CREATE TABLE schema_version (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    version INTEGER NOT NULL
);

CREATE TABLE source_file (
    source_file_id INTEGER PRIMARY KEY,
    filename TEXT NOT NULL CHECK(length(trim(filename)) > 0),
    sha256 TEXT NOT NULL UNIQUE CHECK(length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
    source_type TEXT NOT NULL CHECK(length(trim(source_type)) > 0),
    source_date TEXT NOT NULL CHECK(length(trim(source_date)) > 0),
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE source_sheet (
    source_sheet_id INTEGER PRIMARY KEY,
    source_file_id INTEGER NOT NULL REFERENCES source_file(source_file_id),
    sheet_name TEXT NOT NULL CHECK(length(trim(sheet_name)) > 0),
    UNIQUE(source_file_id, sheet_name)
);

CREATE TABLE instrument (
    instrument_id INTEGER PRIMARY KEY,
    isin TEXT NOT NULL UNIQUE CHECK(length(trim(isin)) = 12 AND isin NOT IN ('CASH', 'EUR_CASH', 'USD_CASH', 'HUF_CASH', 'FREE_MONEY')),
    canonical_name TEXT NOT NULL CHECK(length(trim(canonical_name)) > 0),
    instrument_type TEXT NULL,
    base_currency_code TEXT NULL,
    asset_class TEXT NULL,
    sub_asset_class TEXT NULL,
    issuer TEXT NULL,
    active_from TEXT NULL,
    active_to TEXT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE instrument_alias (
    alias_id INTEGER PRIMARY KEY,
    instrument_id INTEGER NULL REFERENCES instrument(instrument_id),
    source_file_id INTEGER NULL REFERENCES source_file(source_file_id),
    source_type TEXT NOT NULL CHECK(length(trim(source_type)) > 0),
    source_name TEXT NOT NULL CHECK(length(trim(source_name)) > 0),
    normalized_source_name TEXT NOT NULL CHECK(length(trim(normalized_source_name)) > 0),
    mapping_status TEXT NOT NULL CHECK(mapping_status IN (
        'EXPLICIT_ISIN_VALID', 'EXACT_ALIAS_CONFIRMED', 'MANUAL_CONFIRMED',
        'IDENTITY_CANDIDATE', 'IDENTITY_AMBIGUOUS', 'IDENTITY_UNRESOLVED'
    )),
    valid_from TEXT NULL,
    valid_to TEXT NULL,
    resolution_evidence TEXT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK(instrument_id IS NOT NULL OR mapping_status IN ('IDENTITY_CANDIDATE', 'IDENTITY_AMBIGUOUS', 'IDENTITY_UNRESOLVED'))
);
CREATE UNIQUE INDEX instrument_alias_confirmed_name_unique
ON instrument_alias(source_type, normalized_source_name)
WHERE mapping_status IN ('EXPLICIT_ISIN_VALID', 'EXACT_ALIAS_CONFIRMED', 'MANUAL_CONFIRMED');

CREATE TABLE portfolio (
    portfolio_id INTEGER PRIMARY KEY,
    portfolio_name TEXT NOT NULL CHECK(length(trim(portfolio_name)) > 0),
    portfolio_type TEXT NOT NULL CHECK(portfolio_type IN ('MODEL', 'SHORTLIST_CONSTRUCTED', 'CUSTOM', 'BENCHMARK', 'SIMULATED')),
    base_currency_code TEXT NULL,
    active_from TEXT NULL,
    active_to TEXT NULL,
    UNIQUE(portfolio_name, portfolio_type)
);

CREATE TABLE portfolio_snapshot (
    portfolio_snapshot_id INTEGER PRIMARY KEY,
    portfolio_id INTEGER NOT NULL REFERENCES portfolio(portfolio_id),
    snapshot_date TEXT NOT NULL CHECK(length(trim(snapshot_date)) > 0),
    source_sheet_id INTEGER NULL REFERENCES source_sheet(source_sheet_id),
    construction_policy_id TEXT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(portfolio_id, snapshot_date)
);

CREATE TABLE portfolio_holding_source_occurrence (
    portfolio_holding_source_occurrence_id INTEGER PRIMARY KEY,
    portfolio_snapshot_id INTEGER NOT NULL REFERENCES portfolio_snapshot(portfolio_snapshot_id),
    instrument_id INTEGER NOT NULL REFERENCES instrument(instrument_id),
    source_sheet_id INTEGER NOT NULL REFERENCES source_sheet(source_sheet_id),
    source_row_number INTEGER NOT NULL CHECK(source_row_number > 0),
    reported_weight REAL NULL CHECK(reported_weight IS NULL OR reported_weight >= 0),
    observed_product_name TEXT NULL,
    observed_currency_code TEXT NULL,
    observed_currency_risk TEXT NULL,
    observed_asset_class TEXT NULL,
    observed_sub_asset_class TEXT NULL,
    source_payload_json TEXT NOT NULL DEFAULT '{}',
    source_payload_sha256 TEXT NOT NULL CHECK(length(source_payload_sha256) = 64 AND source_payload_sha256 NOT GLOB '*[^0-9a-f]*'),
    source_semantics_status TEXT NOT NULL DEFAULT 'SOURCE_REPORTED' CHECK(source_semantics_status IN (
        'SOURCE_REPORTED', 'EXACT_DUPLICATE_SOURCE_ROWS',
        'DISTINCT_SOURCE_ROWS_FOR_SAME_INSTRUMENT', 'CONFLICTING_DUPLICATE_ROWS',
        'UNRESOLVED_DUPLICATE_SEMANTICS'
    )),
    UNIQUE(source_sheet_id, source_row_number)
);
CREATE INDEX portfolio_holding_source_occurrence_snapshot_instrument
ON portfolio_holding_source_occurrence(portfolio_snapshot_id, instrument_id);

CREATE TABLE portfolio_holding (
    portfolio_holding_id INTEGER PRIMARY KEY,
    portfolio_snapshot_id INTEGER NOT NULL REFERENCES portfolio_snapshot(portfolio_snapshot_id),
    instrument_id INTEGER NOT NULL REFERENCES instrument(instrument_id),
    reported_weight REAL NULL CHECK(reported_weight IS NULL OR reported_weight >= 0),
    derivation_status TEXT NOT NULL CHECK(derivation_status IN ('APPROVED_DIRECT_OCCURRENCE', 'APPROVED_AGGREGATION')),
    calculation_version TEXT NOT NULL CHECK(length(trim(calculation_version)) > 0),
    approval_reference TEXT NOT NULL CHECK(length(trim(approval_reference)) > 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(portfolio_snapshot_id, instrument_id)
);

CREATE TABLE portfolio_holding_lineage (
    portfolio_holding_id INTEGER NOT NULL REFERENCES portfolio_holding(portfolio_holding_id) ON DELETE CASCADE,
    source_occurrence_id INTEGER NOT NULL REFERENCES portfolio_holding_source_occurrence(portfolio_holding_source_occurrence_id),
    PRIMARY KEY(portfolio_holding_id, source_occurrence_id)
);

CREATE TABLE portfolio_cash (
    portfolio_cash_id INTEGER PRIMARY KEY,
    portfolio_snapshot_id INTEGER NOT NULL REFERENCES portfolio_snapshot(portfolio_snapshot_id),
    currency_code TEXT NOT NULL CHECK(length(currency_code) = 3),
    amount REAL NULL,
    weight REAL NULL CHECK(weight IS NULL OR weight >= 0),
    cash_role TEXT NOT NULL CHECK(cash_role IN ('AVAILABLE', 'RESERVE', 'PORTFOLIO_ALLOCATION', 'PENDING_INVESTMENT')),
    source TEXT NOT NULL CHECK(length(trim(source)) > 0),
    CHECK(amount IS NOT NULL OR weight IS NOT NULL),
    UNIQUE(portfolio_snapshot_id, currency_code, cash_role)
);

CREATE TABLE metric_definition (
    metric_id INTEGER PRIMARY KEY,
    metric_code TEXT NOT NULL UNIQUE CHECK(length(trim(metric_code)) > 0),
    name TEXT NOT NULL CHECK(length(trim(name)) > 0),
    unit TEXT NOT NULL CHECK(length(trim(unit)) > 0),
    description TEXT NOT NULL CHECK(length(trim(description)) > 0),
    direction TEXT NULL CHECK(direction IS NULL OR direction IN ('HIGHER_BETTER', 'LOWER_BETTER'))
);

CREATE TABLE instrument_metric_observation (
    instrument_metric_observation_id INTEGER PRIMARY KEY,
    instrument_id INTEGER NOT NULL REFERENCES instrument(instrument_id),
    metric_id INTEGER NOT NULL REFERENCES metric_definition(metric_id),
    observation_date TEXT NOT NULL,
    value REAL NOT NULL,
    provenance_type TEXT NOT NULL CHECK(provenance_type IN ('PROVIDER_REPORTED', 'CALCULATED', 'DERIVED', 'OBSERVED')),
    source_file_id INTEGER NULL REFERENCES source_file(source_file_id),
    calculation_version TEXT NULL,
    source_reference TEXT NOT NULL DEFAULT '',
    UNIQUE(instrument_id, metric_id, observation_date, provenance_type, source_reference)
);

CREATE TABLE portfolio_metric_observation (
    portfolio_metric_observation_id INTEGER PRIMARY KEY,
    portfolio_snapshot_id INTEGER NOT NULL REFERENCES portfolio_snapshot(portfolio_snapshot_id),
    metric_id INTEGER NOT NULL REFERENCES metric_definition(metric_id),
    value REAL NOT NULL,
    provenance_type TEXT NOT NULL CHECK(provenance_type IN ('PROVIDER_REPORTED', 'CALCULATED', 'DERIVED', 'OBSERVED')),
    observation_date TEXT NULL,
    calculation_version TEXT NULL,
    source_file_id INTEGER NULL REFERENCES source_file(source_file_id),
    source_reference TEXT NOT NULL DEFAULT '',
    UNIQUE(portfolio_snapshot_id, metric_id, provenance_type, observation_date, source_reference)
);

CREATE TABLE shortlist_snapshot (
    shortlist_snapshot_id INTEGER PRIMARY KEY,
    snapshot_date TEXT NOT NULL CHECK(length(trim(snapshot_date)) > 0),
    source_sheet_id INTEGER NOT NULL REFERENCES source_sheet(source_sheet_id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_sheet_id)
);

CREATE TABLE shortlist_entry (
    shortlist_entry_id INTEGER PRIMARY KEY,
    shortlist_snapshot_id INTEGER NOT NULL REFERENCES shortlist_snapshot(shortlist_snapshot_id),
    instrument_id INTEGER NOT NULL REFERENCES instrument(instrument_id),
    source_row_number INTEGER NOT NULL CHECK(source_row_number > 0),
    status TEXT NOT NULL CHECK(length(trim(status)) > 0),
    UNIQUE(shortlist_snapshot_id, instrument_id),
    UNIQUE(shortlist_snapshot_id, source_row_number)
);

CREATE TABLE migration_build_manifest (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    schema_version INTEGER NOT NULL,
    build_version TEXT NOT NULL CHECK(length(trim(build_version)) > 0),
    source_fingerprints_json TEXT NOT NULL,
    ranking_policy_sha256 TEXT NOT NULL CHECK(length(ranking_policy_sha256) = 64),
    source_counts_json TEXT NOT NULL,
    target_counts_json TEXT NOT NULL,
    unresolved_semantic_count INTEGER NOT NULL CHECK(unresolved_semantic_count >= 0),
    equivalence_status TEXT NOT NULL CHECK(equivalence_status IN ('EXACT_PASS', 'FAILED')),
    dataset_fingerprint TEXT NOT NULL CHECK(length(dataset_fingerprint) = 64),
    database_fingerprint TEXT NULL,
    build_status TEXT NOT NULL CHECK(build_status IN ('PARALLEL_VALIDATED', 'FAILED_NO_PUBLICATION')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE instrument_nav_observation (
    instrument_nav_observation_id INTEGER PRIMARY KEY,
    instrument_id INTEGER NOT NULL REFERENCES instrument(instrument_id),
    observation_date TEXT NOT NULL,
    nav_value REAL NOT NULL CHECK(nav_value > 0 AND nav_value < 1.0e308),
    currency_code TEXT NOT NULL CHECK(length(currency_code) = 3),
    value_type TEXT NOT NULL CHECK(value_type = 'NAV'),
    source_provider TEXT NOT NULL,
    source_identifier TEXT NOT NULL,
    provenance_reference TEXT NOT NULL,
    quality_status TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL CHECK(length(source_fingerprint) = 64),
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(instrument_id, observation_date, source_provider, value_type, source_identifier)
);
"""
