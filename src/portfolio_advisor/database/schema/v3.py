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

from portfolio_advisor.canonical import canonical_fingerprint

SCHEMA_VERSION = 3
CONSTRUCTED_PORTFOLIO_FEATURE_ID = "MILESTONE_11B_CONSTRUCTED_PORTFOLIO"
CONSTRUCTED_PORTFOLIO_FEATURE_REVISION = 1
CONSTRUCTED_PORTFOLIO_FEATURE_FINGERPRINT = canonical_fingerprint(
    {
        "feature_id": CONSTRUCTED_PORTFOLIO_FEATURE_ID,
        "revision": CONSTRUCTED_PORTFOLIO_FEATURE_REVISION,
        "tables": (
            "constructed_portfolio_holding_lineage",
            "constructed_portfolio_metadata",
        ),
    }
)
REFERENCE_RATE_FEATURE_ID = "MILESTONE_11C_REFERENCE_RATE_EVIDENCE"
LEGACY_REFERENCE_RATE_FEATURE_REVISION = 1
LEGACY_REFERENCE_RATE_CONTRACT_SCHEMA_VERSION = 1
LEGACY_REFERENCE_RATE_FEATURE_FINGERPRINT = canonical_fingerprint(
    {
        "contract_schema_version": LEGACY_REFERENCE_RATE_CONTRACT_SCHEMA_VERSION,
        "feature_id": REFERENCE_RATE_FEATURE_ID,
        "revision": LEGACY_REFERENCE_RATE_FEATURE_REVISION,
        "tables": (
            "reference_rate_definition",
            "reference_rate_import_manifest",
            "reference_rate_observation",
            "reference_rate_source",
        ),
    }
)
REFERENCE_RATE_FEATURE_REVISION = 2
REFERENCE_RATE_CONTRACT_SCHEMA_VERSION = 2
REFERENCE_RATE_FEATURE_FINGERPRINT = canonical_fingerprint(
    {
        "contract_schema_version": REFERENCE_RATE_CONTRACT_SCHEMA_VERSION,
        "feature_id": REFERENCE_RATE_FEATURE_ID,
        "revision": REFERENCE_RATE_FEATURE_REVISION,
        "tables": (
            "reference_rate_definition",
            "reference_rate_import_manifest",
            "reference_rate_observation",
            "reference_rate_source",
        ),
    }
)
NAV_PROVENANCE_FEATURE_ID = "MILESTONE_11C_PHASE_E_NAV_PROVENANCE"
NAV_PROVENANCE_FEATURE_REVISION = 1
NAV_PROVENANCE_CONTRACT_VERSION = 1
NAV_PROVENANCE_TABLES = frozenset({
    "nav_evidence_source",
    "nav_import_manifest",
    "nav_observation_version",
})
NAV_PROVENANCE_FEATURE_FINGERPRINT = canonical_fingerprint(
    {
        "contract_version": NAV_PROVENANCE_CONTRACT_VERSION,
        "feature_id": NAV_PROVENANCE_FEATURE_ID,
        "revision": NAV_PROVENANCE_FEATURE_REVISION,
        "tables": tuple(sorted(NAV_PROVENANCE_TABLES)),
    }
)
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
        _insert_constructed_portfolio_feature_marker(connection)
        _insert_reference_rate_feature_marker(connection)
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


def upgrade_schema_v3_shortlist_extension(connection: sqlite3.Connection) -> None:
    """Explicit additive transition for immutable shortlist source rows."""
    if detect_schema_version(connection) != SCHEMA_VERSION:
        raise SchemaVersionError("shortlist extension requires recognized schema v3")
    names = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "shortlist_entry_source_occurrence" not in names:
        section = _SCHEMA_SQL.split("CREATE TABLE shortlist_entry_source_occurrence", 1)[1].split("CREATE TABLE migration_build_manifest", 1)[0]
        with transaction(connection):
            for statement in ("CREATE TABLE shortlist_entry_source_occurrence" + section).split(";"):
                if statement.strip(): connection.execute(statement)


def upgrade_schema_v3_constructed_portfolio_extension(
    connection: sqlite3.Connection,
) -> None:
    """Install the reviewed Milestone 11B additive schema feature atomically."""
    if detect_schema_version(connection) != SCHEMA_VERSION:
        raise SchemaVersionError("constructed-portfolio extension requires recognized schema v3")
    names = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    feature_tables = {
        "schema_feature_contract",
        "constructed_portfolio_metadata",
        "constructed_portfolio_holding_lineage",
    }
    present = feature_tables & names
    if present and present != feature_tables:
        raise SchemaVersionError("constructed-portfolio schema feature is partially installed")
    with transaction(connection):
        if not present:
            for statement in _CONSTRUCTED_PORTFOLIO_SCHEMA_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
        _insert_constructed_portfolio_feature_marker(connection)
    validate_schema(connection)


def upgrade_schema_v3_reference_rate_extension(connection: sqlite3.Connection) -> None:
    """Install the additive reference-rate evidence schema without ingesting data."""
    if detect_schema_version(connection) != SCHEMA_VERSION:
        raise SchemaVersionError("reference-rate extension requires recognized schema v3")
    names = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "schema_feature_contract" not in names:
        raise SchemaVersionError("reference-rate extension requires schema feature contracts")
    present = _REFERENCE_RATE_TABLES & names
    if present and present != _REFERENCE_RATE_TABLES:
        raise SchemaVersionError("reference-rate schema feature is partially installed")
    with transaction(connection):
        if not present:
            for statement in _REFERENCE_RATE_SCHEMA_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
        _insert_reference_rate_feature_marker(connection)
    validate_schema(connection)


def upgrade_schema_v3_nav_provenance_extension(connection: sqlite3.Connection) -> None:
    """Install the additive Phase E exact-Decimal NAV provenance contract."""
    validate_schema(connection)
    names = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    present = NAV_PROVENANCE_TABLES & names
    marker = connection.execute(
        "SELECT revision, contract_fingerprint FROM schema_feature_contract WHERE feature_id=?",
        (NAV_PROVENANCE_FEATURE_ID,),
    ).fetchall()
    if present and present != NAV_PROVENANCE_TABLES:
        raise SchemaVersionError("NAV provenance schema feature is partially installed")
    if not present and marker:
        raise SchemaVersionError("NAV provenance marker exists without its tables")
    if not present:
        with transaction(connection):
            _execute_complete_statements(connection, _NAV_PROVENANCE_SCHEMA_SQL)
            connection.execute(
                "INSERT INTO schema_feature_contract(feature_id, revision, contract_fingerprint) "
                "VALUES (?, ?, ?)",
                (
                    NAV_PROVENANCE_FEATURE_ID,
                    NAV_PROVENANCE_FEATURE_REVISION,
                    NAV_PROVENANCE_FEATURE_FINGERPRINT,
                ),
            )
    validate_nav_provenance_schema(connection)


def validate_nav_provenance_schema(connection: sqlite3.Connection) -> None:
    """Require the complete, exact Phase E table set and feature marker."""
    validate_schema(connection)
    names = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    present = NAV_PROVENANCE_TABLES & names
    if present != NAV_PROVENANCE_TABLES:
        raise SchemaVersionError("NAV provenance schema feature is absent or partial")
    marker = [
        tuple(row)
        for row in connection.execute(
            "SELECT revision, contract_fingerprint FROM schema_feature_contract WHERE feature_id=?",
            (NAV_PROVENANCE_FEATURE_ID,),
        ).fetchall()
    ]
    if marker != [
        (NAV_PROVENANCE_FEATURE_REVISION, NAV_PROVENANCE_FEATURE_FINGERPRINT)
    ]:
        raise SchemaVersionError("NAV provenance schema feature marker is missing or stale")
    if _nav_provenance_schema_objects(connection) != _nav_provenance_schema_objects_from_sql(
        _NAV_PROVENANCE_SCHEMA_SQL
    ):
        raise SchemaVersionError("NAV provenance schema objects are damaged or incompatible")


def _nav_provenance_schema_objects(
    connection: sqlite3.Connection,
) -> tuple[tuple[str, ...], ...]:
    rows = connection.execute(
        """SELECT type, name, tbl_name, sql
           FROM sqlite_master
           WHERE sql IS NOT NULL
             AND (name GLOB 'nav_*' OR tbl_name GLOB 'nav_*')
           ORDER BY type, name"""
    ).fetchall()
    return tuple(
        (str(row[0]), str(row[1]), str(row[2]), " ".join(str(row[3]).split()))
        for row in rows
    )


def _nav_provenance_schema_objects_from_sql(sql: str) -> tuple[tuple[str, ...], ...]:
    with sqlite3.connect(":memory:") as scratch:
        scratch.execute("PRAGMA foreign_keys=ON")
        scratch.execute("CREATE TABLE instrument(instrument_id INTEGER PRIMARY KEY)")
        _execute_complete_statements(scratch, sql)
        return _nav_provenance_schema_objects(scratch)


def _execute_complete_statements(connection: sqlite3.Connection, sql: str) -> None:
    statement = ""
    for line in sql.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            connection.execute(statement)
            statement = ""
    if statement.strip():
        raise SchemaVersionError("schema SQL ended with an incomplete statement")


def validate_schema(connection: sqlite3.Connection) -> None:
    """Fail closed on a missing table, integrity error, or FK violation."""
    _validate_schema(connection, allow_legacy_reference_rate=False)


def validate_schema_for_reference_rate_migration(connection: sqlite3.Connection) -> None:
    """Validate schema v3 while permitting only an exact legacy v1 feature."""
    _validate_schema(connection, allow_legacy_reference_rate=True)


def _validate_schema(
    connection: sqlite3.Connection, *, allow_legacy_reference_rate: bool
) -> None:
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
    marker = connection.execute(
        """SELECT revision, contract_fingerprint
           FROM schema_feature_contract WHERE feature_id=?""",
        (CONSTRUCTED_PORTFOLIO_FEATURE_ID,),
    ).fetchall()
    if [tuple(row) for row in marker] != [
        (CONSTRUCTED_PORTFOLIO_FEATURE_REVISION, CONSTRUCTED_PORTFOLIO_FEATURE_FINGERPRINT)
    ]:
        raise SchemaVersionError("constructed-portfolio schema feature marker is missing or stale")
    state = detect_reference_rate_feature_state(connection)
    if state == "V1" and not allow_legacy_reference_rate:
        raise SchemaVersionError("reference-rate provenance contract v1 requires explicit migration")
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


def _insert_constructed_portfolio_feature_marker(
    connection: sqlite3.Connection,
) -> None:
    existing = connection.execute(
        """SELECT revision, contract_fingerprint
           FROM schema_feature_contract WHERE feature_id=?""",
        (CONSTRUCTED_PORTFOLIO_FEATURE_ID,),
    ).fetchall()
    expected = (
        CONSTRUCTED_PORTFOLIO_FEATURE_REVISION,
        CONSTRUCTED_PORTFOLIO_FEATURE_FINGERPRINT,
    )
    if existing:
        if len(existing) != 1 or tuple(existing[0]) != expected:
            raise SchemaVersionError("conflicting constructed-portfolio schema feature marker")
        return
    connection.execute(
        """INSERT INTO schema_feature_contract(feature_id, revision, contract_fingerprint)
           VALUES (?, ?, ?)""",
        (CONSTRUCTED_PORTFOLIO_FEATURE_ID, *expected),
    )


def _insert_reference_rate_feature_marker(connection: sqlite3.Connection) -> None:
    existing = connection.execute(
        """SELECT revision, contract_fingerprint
           FROM schema_feature_contract WHERE feature_id=?""",
        (REFERENCE_RATE_FEATURE_ID,),
    ).fetchall()
    expected = (REFERENCE_RATE_FEATURE_REVISION, REFERENCE_RATE_FEATURE_FINGERPRINT)
    if existing:
        if len(existing) != 1 or tuple(existing[0]) != expected:
            raise SchemaVersionError("conflicting reference-rate schema feature marker")
        return
    connection.execute(
        """INSERT INTO schema_feature_contract(feature_id, revision, contract_fingerprint)
           VALUES (?, ?, ?)""",
        (REFERENCE_RATE_FEATURE_ID, *expected),
    )


def _validate_reference_rate_feature_if_present(
    connection: sqlite3.Connection,
    names: set[str],
) -> None:
    del names
    state = detect_reference_rate_feature_state(connection)
    if state == "V1":
        raise SchemaVersionError("reference-rate provenance contract v1 requires explicit migration")


def detect_reference_rate_feature_state(connection: sqlite3.Connection) -> str:
    """Classify absent, exact v1, or exact v2 reference-rate feature state."""
    marker = [
        tuple(row)
        for row in connection.execute(
            """SELECT revision, contract_fingerprint
               FROM schema_feature_contract WHERE feature_id=?""",
            (REFERENCE_RATE_FEATURE_ID,),
        ).fetchall()
    ]
    actual = _reference_rate_schema_objects(connection)
    if not actual and not marker:
        return "ABSENT"
    expected_v1 = _reference_rate_schema_objects_from_sql(_REFERENCE_RATE_SCHEMA_SQL_V1)
    expected_v2 = _reference_rate_schema_objects_from_sql(_REFERENCE_RATE_SCHEMA_SQL)
    if actual == expected_v1 and marker == [
        (LEGACY_REFERENCE_RATE_FEATURE_REVISION, LEGACY_REFERENCE_RATE_FEATURE_FINGERPRINT)
    ]:
        return "V1"
    if actual == expected_v2 and marker == [
        (REFERENCE_RATE_FEATURE_REVISION, REFERENCE_RATE_FEATURE_FINGERPRINT)
    ]:
        return "V2"
    raise SchemaVersionError(
        "reference-rate schema feature is partial, mixed, stale, or incompatible"
    )


def reference_rate_schema_objects(connection: sqlite3.Connection) -> tuple[tuple[str, ...], ...]:
    """Return the exact normalized v2 feature DDL inventory for audit tooling."""
    return _reference_rate_schema_objects(connection)


def _reference_rate_schema_objects(
    connection: sqlite3.Connection,
) -> tuple[tuple[str, ...], ...]:
    rows = connection.execute(
        """SELECT type, name, tbl_name, sql
           FROM sqlite_master
           WHERE sql IS NOT NULL
             AND (name GLOB 'reference_rate_*' OR tbl_name GLOB 'reference_rate_*')
           ORDER BY type, name"""
    ).fetchall()
    return tuple(
        (
            str(row[0]),
            str(row[1]),
            str(row[2]),
            " ".join(str(row[3]).split()),
        )
        for row in rows
    )


def _reference_rate_schema_objects_from_sql(sql: str) -> tuple[tuple[str, ...], ...]:
    with sqlite3.connect(":memory:") as scratch:
        for statement in sql.split(";"):
            if statement.strip():
                scratch.execute(statement)
        return _reference_rate_schema_objects(scratch)


_REQUIRED_TABLES = frozenset({
    "schema_version", "source_file", "source_sheet", "instrument", "instrument_alias",
    "portfolio", "portfolio_snapshot", "portfolio_holding_source_occurrence",
    "portfolio_holding", "portfolio_holding_lineage", "portfolio_cash", "metric_definition",
    "instrument_metric_observation", "portfolio_metric_observation", "shortlist_snapshot",
    "shortlist_entry", "shortlist_entry_source_occurrence", "shortlist_entry_lineage", "migration_build_manifest", "instrument_nav_observation",
    "schema_feature_contract", "constructed_portfolio_metadata",
    "constructed_portfolio_holding_lineage",
})

_REFERENCE_RATE_TABLES = frozenset({
    "reference_rate_definition",
    "reference_rate_import_manifest",
    "reference_rate_observation",
    "reference_rate_source",
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


_BASE_SCHEMA_SQL = """
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
CREATE TABLE shortlist_entry_source_occurrence (
    shortlist_entry_source_occurrence_id INTEGER PRIMARY KEY,
    shortlist_snapshot_id INTEGER NOT NULL REFERENCES shortlist_snapshot(shortlist_snapshot_id),
    instrument_id INTEGER NOT NULL REFERENCES instrument(instrument_id),
    source_sheet_id INTEGER NOT NULL REFERENCES source_sheet(source_sheet_id),
    source_row_number INTEGER NOT NULL CHECK(source_row_number > 0),
    observed_product_name TEXT NOT NULL,
    observed_currency_code TEXT NULL,
    observed_asset_class TEXT NULL,
    observed_sub_asset_class TEXT NULL,
    source_payload_json TEXT NOT NULL,
    conflict_status TEXT NOT NULL CHECK(conflict_status IN ('SOURCE_REPORTED','SOURCE_METADATA_CONFLICT')),
    UNIQUE(source_sheet_id, source_row_number)
);
CREATE TABLE shortlist_entry_lineage (
    shortlist_entry_id INTEGER NOT NULL REFERENCES shortlist_entry(shortlist_entry_id),
    source_occurrence_id INTEGER NOT NULL REFERENCES shortlist_entry_source_occurrence(shortlist_entry_source_occurrence_id),
    PRIMARY KEY(shortlist_entry_id, source_occurrence_id)
);
CREATE TABLE shortlist_stage_manifest (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    integration_version TEXT NOT NULL, workbook_fingerprints_json TEXT NOT NULL,
    header_signature TEXT NOT NULL, source_occurrence_count INTEGER NOT NULL,
    snapshot_count INTEGER NOT NULL, membership_count INTEGER NOT NULL,
    lineage_count INTEGER NOT NULL, instrument_count INTEGER NOT NULL,
    alias_count INTEGER NOT NULL, metric_observation_count INTEGER NOT NULL,
    multi_occurrence_count INTEGER NOT NULL, conflict_occurrence_count INTEGER NOT NULL,
    dataset_fingerprint TEXT NOT NULL, completion_status TEXT NOT NULL
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


_CONSTRUCTED_PORTFOLIO_SCHEMA_SQL = """
CREATE TABLE schema_feature_contract (
    feature_id TEXT PRIMARY KEY CHECK(length(trim(feature_id)) > 0),
    revision INTEGER NOT NULL CHECK(revision > 0),
    contract_fingerprint TEXT NOT NULL
        CHECK(length(contract_fingerprint) = 64
              AND contract_fingerprint NOT GLOB '*[^0-9a-f]*')
);

CREATE TABLE constructed_portfolio_metadata (
    portfolio_snapshot_id INTEGER PRIMARY KEY
        REFERENCES portfolio_snapshot(portfolio_snapshot_id),
    shortlist_snapshot_id INTEGER NOT NULL
        REFERENCES shortlist_snapshot(shortlist_snapshot_id),
    objective_code TEXT NOT NULL CHECK(objective_code = 'CAPITAL_CONSERVATION'),
    construction_policy_id TEXT NOT NULL CHECK(length(trim(construction_policy_id)) > 0),
    construction_policy_version TEXT NOT NULL
        CHECK(length(trim(construction_policy_version)) > 0),
    construction_policy_fingerprint TEXT NOT NULL
        CHECK(length(construction_policy_fingerprint) = 64
              AND construction_policy_fingerprint NOT GLOB '*[^0-9a-f]*'),
    construction_strategy TEXT NOT NULL CHECK(construction_strategy = 'CAPITAL_DEFENSIVE'),
    cash_currency TEXT NOT NULL CHECK(cash_currency IN ('EUR', 'USD', 'HUF')),
    portfolio_identity_fingerprint TEXT NOT NULL
        CHECK(length(portfolio_identity_fingerprint) = 64
              AND portfolio_identity_fingerprint NOT GLOB '*[^0-9a-f]*'),
    eligible_universe_fingerprint TEXT NOT NULL
        CHECK(length(eligible_universe_fingerprint) = 64
              AND eligible_universe_fingerprint NOT GLOB '*[^0-9a-f]*'),
    selected_universe_fingerprint TEXT NOT NULL
        CHECK(length(selected_universe_fingerprint) = 64
              AND selected_universe_fingerprint NOT GLOB '*[^0-9a-f]*'),
    candidate_fingerprint TEXT NOT NULL UNIQUE
        CHECK(length(candidate_fingerprint) = 64
              AND candidate_fingerprint NOT GLOB '*[^0-9a-f]*'),
    construction_status TEXT NOT NULL CHECK(construction_status = 'CONSTRUCTED_VALIDATED'),
    deterministic_provenance_json TEXT NOT NULL
        CHECK(length(trim(deterministic_provenance_json)) > 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(portfolio_identity_fingerprint, shortlist_snapshot_id)
);
CREATE INDEX constructed_portfolio_metadata_shortlist_snapshot
ON constructed_portfolio_metadata(shortlist_snapshot_id);

CREATE TABLE constructed_portfolio_holding_lineage (
    portfolio_holding_id INTEGER PRIMARY KEY
        REFERENCES portfolio_holding(portfolio_holding_id) ON DELETE CASCADE,
    shortlist_entry_id INTEGER NOT NULL
        REFERENCES shortlist_entry(shortlist_entry_id),
    selected_instrument_rank INTEGER NOT NULL CHECK(selected_instrument_rank > 0),
    allocation_basis TEXT NOT NULL
        CHECK(allocation_basis = 'FIXED_TOTAL_PORTFOLIO_WEIGHT'),
    allocation_weight_decimal TEXT NOT NULL CHECK(allocation_weight_decimal = '0.10'),
    constraint_evidence_fingerprint TEXT NOT NULL
        CHECK(length(constraint_evidence_fingerprint) = 64
              AND constraint_evidence_fingerprint NOT GLOB '*[^0-9a-f]*')
);
CREATE INDEX constructed_portfolio_holding_lineage_membership
ON constructed_portfolio_holding_lineage(shortlist_entry_id);
"""


_REFERENCE_RATE_SCHEMA_SQL_V1 = """
CREATE TABLE reference_rate_definition (
    reference_rate_definition_id INTEGER PRIMARY KEY,
    contract_schema_version INTEGER NOT NULL
        CHECK(contract_schema_version = 1),
    benchmark_id TEXT NOT NULL CHECK(length(trim(benchmark_id)) > 0),
    benchmark_name TEXT NOT NULL CHECK(length(trim(benchmark_name)) > 0),
    currency_code TEXT NOT NULL CHECK(currency_code IN ('EUR', 'USD', 'HUF')),
    administrator TEXT NOT NULL CHECK(length(trim(administrator)) > 0),
    series_identifier TEXT NOT NULL CHECK(length(trim(series_identifier)) > 0),
    rate_units TEXT NOT NULL CHECK(rate_units = 'PERCENT_PER_ANNUM'),
    day_count_convention TEXT NOT NULL CHECK(length(trim(day_count_convention)) > 0),
    compounding_convention TEXT NOT NULL CHECK(length(trim(compounding_convention)) > 0),
    definition_version TEXT NOT NULL CHECK(length(trim(definition_version)) > 0),
    definition_fingerprint TEXT NOT NULL UNIQUE
        CHECK(length(definition_fingerprint) = 64
              AND definition_fingerprint NOT GLOB '*[^0-9a-f]*'),
    UNIQUE(benchmark_id, definition_version)
);

CREATE TABLE reference_rate_source (
    reference_rate_source_id INTEGER PRIMARY KEY,
    reference_rate_definition_id INTEGER NOT NULL
        REFERENCES reference_rate_definition(reference_rate_definition_id) ON DELETE RESTRICT,
    source_code TEXT NOT NULL CHECK(length(trim(source_code)) > 0),
    source_organization TEXT NOT NULL CHECK(length(trim(source_organization)) > 0),
    official_page_url TEXT NOT NULL CHECK(length(trim(official_page_url)) > 0),
    machine_readable_url TEXT NOT NULL CHECK(length(trim(machine_readable_url)) > 0),
    response_format TEXT NOT NULL CHECK(length(trim(response_format)) > 0),
    source_role TEXT NOT NULL CHECK(source_role IN ('OFFICIAL_ADMINISTRATOR', 'OFFICIAL_PLATFORM')),
    authentication_requirement TEXT NOT NULL
        CHECK(authentication_requirement IN ('NONE', 'REQUIRED')),
    automated_use_status TEXT NOT NULL
        CHECK(automated_use_status IN ('NOT_REVIEWED', 'PERMITTED', 'PROHIBITED')),
    licensing_reference TEXT NOT NULL CHECK(length(trim(licensing_reference)) > 0),
    raw_retention_status TEXT NOT NULL
        CHECK(raw_retention_status IN ('NOT_REVIEWED', 'PERMITTED', 'PROHIBITED')),
    source_contract_fingerprint TEXT NOT NULL UNIQUE
        CHECK(length(source_contract_fingerprint) = 64
              AND source_contract_fingerprint NOT GLOB '*[^0-9a-f]*'),
    UNIQUE(reference_rate_source_id, reference_rate_definition_id),
    UNIQUE(reference_rate_definition_id, source_code)
);

CREATE TABLE reference_rate_import_manifest (
    reference_rate_import_manifest_id INTEGER PRIMARY KEY,
    reference_rate_source_id INTEGER NOT NULL,
    reference_rate_definition_id INTEGER NOT NULL,
    retrieval_timestamp TEXT NOT NULL CHECK(length(trim(retrieval_timestamp)) > 0),
    request_url TEXT NOT NULL CHECK(length(trim(request_url)) > 0),
    request_parameters_json TEXT NOT NULL CHECK(length(trim(request_parameters_json)) > 0),
    response_content_type TEXT NOT NULL CHECK(length(trim(response_content_type)) > 0),
    http_status INTEGER NOT NULL CHECK(http_status = 200),
    raw_artifact_reference TEXT NOT NULL CHECK(length(trim(raw_artifact_reference)) > 0),
    raw_artifact_sha256 TEXT NOT NULL
        CHECK(length(raw_artifact_sha256) = 64
              AND raw_artifact_sha256 NOT GLOB '*[^0-9a-f]*'),
    provider_dataset_version TEXT NOT NULL CHECK(length(trim(provider_dataset_version)) > 0),
    import_status TEXT NOT NULL
        CHECK(import_status IN ('VALIDATED_ADMITTED', 'VALIDATED_REJECTED')),
    dataset_fingerprint TEXT NOT NULL
        CHECK(length(dataset_fingerprint) = 64
              AND dataset_fingerprint NOT GLOB '*[^0-9a-f]*'),
    FOREIGN KEY(reference_rate_source_id, reference_rate_definition_id)
        REFERENCES reference_rate_source(
            reference_rate_source_id, reference_rate_definition_id
        ) ON DELETE RESTRICT,
    UNIQUE(reference_rate_import_manifest_id, reference_rate_source_id,
           reference_rate_definition_id),
    UNIQUE(reference_rate_source_id, raw_artifact_sha256, provider_dataset_version)
);

CREATE TABLE reference_rate_observation (
    reference_rate_observation_id INTEGER PRIMARY KEY,
    reference_rate_definition_id INTEGER NOT NULL,
    reference_rate_source_id INTEGER NOT NULL,
    reference_rate_import_manifest_id INTEGER NOT NULL,
    observation_date TEXT NOT NULL
        CHECK(length(observation_date) = 10),
    publication_date TEXT NOT NULL
        CHECK(length(publication_date) = 10 AND publication_date >= observation_date),
    rate_decimal TEXT NOT NULL
        CHECK(typeof(rate_decimal) = 'text'
              AND length(trim(rate_decimal)) > 0
              AND rate_decimal = trim(rate_decimal)),
    provider_revision_id TEXT NOT NULL CHECK(length(trim(provider_revision_id)) > 0),
    revision_sequence INTEGER NOT NULL CHECK(revision_sequence > 0),
    supersedes_observation_id INTEGER NULL
        REFERENCES reference_rate_observation(reference_rate_observation_id) ON DELETE RESTRICT,
    is_current INTEGER NOT NULL CHECK(is_current IN (0, 1)),
    quality_status TEXT NOT NULL CHECK(quality_status = 'ADMITTED_VALIDATED'),
    observation_fingerprint TEXT NOT NULL UNIQUE
        CHECK(length(observation_fingerprint) = 64
              AND observation_fingerprint NOT GLOB '*[^0-9a-f]*'),
    FOREIGN KEY(reference_rate_import_manifest_id, reference_rate_source_id,
                reference_rate_definition_id)
        REFERENCES reference_rate_import_manifest(
            reference_rate_import_manifest_id, reference_rate_source_id,
            reference_rate_definition_id
        ) ON DELETE RESTRICT,
    FOREIGN KEY(supersedes_observation_id, reference_rate_definition_id,
                observation_date)
        REFERENCES reference_rate_observation(
            reference_rate_observation_id, reference_rate_definition_id,
            observation_date
        ) ON DELETE RESTRICT,
    CHECK((revision_sequence = 1 AND supersedes_observation_id IS NULL)
          OR (revision_sequence > 1 AND supersedes_observation_id IS NOT NULL)),
    UNIQUE(reference_rate_observation_id, reference_rate_definition_id,
           observation_date),
    UNIQUE(reference_rate_definition_id, observation_date, revision_sequence),
    UNIQUE(reference_rate_source_id, observation_date, provider_revision_id)
);
CREATE UNIQUE INDEX reference_rate_observation_current
ON reference_rate_observation(reference_rate_definition_id, observation_date)
WHERE is_current = 1;
CREATE INDEX reference_rate_observation_date
ON reference_rate_observation(reference_rate_definition_id, observation_date);
"""


_REFERENCE_RATE_SCHEMA_SQL = """
CREATE TABLE reference_rate_definition (
    reference_rate_definition_id INTEGER PRIMARY KEY,
    contract_schema_version INTEGER NOT NULL
        CHECK(contract_schema_version = 2),
    benchmark_id TEXT NOT NULL CHECK(length(trim(benchmark_id)) > 0),
    benchmark_name TEXT NOT NULL CHECK(length(trim(benchmark_name)) > 0),
    currency_code TEXT NOT NULL CHECK(currency_code IN ('EUR', 'USD', 'HUF')),
    administrator TEXT NOT NULL CHECK(length(trim(administrator)) > 0),
    series_identifier TEXT NOT NULL CHECK(length(trim(series_identifier)) > 0),
    rate_units TEXT NOT NULL CHECK(rate_units = 'PERCENT_PER_ANNUM'),
    day_count_convention TEXT NOT NULL CHECK(length(trim(day_count_convention)) > 0),
    compounding_convention TEXT NOT NULL CHECK(length(trim(compounding_convention)) > 0),
    definition_version TEXT NOT NULL CHECK(length(trim(definition_version)) > 0),
    definition_fingerprint TEXT NOT NULL UNIQUE
        CHECK(length(definition_fingerprint) = 64
              AND definition_fingerprint NOT GLOB '*[^0-9a-f]*'),
    UNIQUE(benchmark_id, definition_version)
);

CREATE TABLE reference_rate_source (
    reference_rate_source_id INTEGER PRIMARY KEY,
    reference_rate_definition_id INTEGER NOT NULL
        REFERENCES reference_rate_definition(reference_rate_definition_id) ON DELETE RESTRICT,
    source_code TEXT NOT NULL CHECK(length(trim(source_code)) > 0),
    source_organization TEXT NOT NULL CHECK(length(trim(source_organization)) > 0),
    official_page_url TEXT NOT NULL CHECK(length(trim(official_page_url)) > 0),
    machine_readable_url TEXT NOT NULL CHECK(length(trim(machine_readable_url)) > 0),
    response_format TEXT NOT NULL CHECK(length(trim(response_format)) > 0),
    source_role TEXT NOT NULL CHECK(source_role IN ('OFFICIAL_ADMINISTRATOR', 'OFFICIAL_PLATFORM')),
    authentication_requirement TEXT NOT NULL
        CHECK(authentication_requirement IN ('NONE', 'REQUIRED')),
    automated_use_status TEXT NOT NULL
        CHECK(automated_use_status IN ('NOT_REVIEWED', 'PERMITTED', 'PROHIBITED')),
    licensing_reference TEXT NOT NULL CHECK(length(trim(licensing_reference)) > 0),
    raw_retention_status TEXT NOT NULL
        CHECK(raw_retention_status IN ('NOT_REVIEWED', 'PERMITTED', 'PROHIBITED')),
    source_contract_fingerprint TEXT NOT NULL UNIQUE
        CHECK(length(source_contract_fingerprint) = 64
              AND source_contract_fingerprint NOT GLOB '*[^0-9a-f]*'),
    UNIQUE(reference_rate_source_id, reference_rate_definition_id),
    UNIQUE(reference_rate_definition_id, source_code)
);

CREATE TABLE reference_rate_import_manifest (
    reference_rate_import_manifest_id INTEGER PRIMARY KEY,
    provenance_contract_version INTEGER NOT NULL CHECK(provenance_contract_version = 2),
    reference_rate_source_id INTEGER NOT NULL,
    reference_rate_definition_id INTEGER NOT NULL,
    retrieval_timestamp TEXT NOT NULL CHECK(length(trim(retrieval_timestamp)) > 0),
    request_url TEXT NOT NULL CHECK(length(trim(request_url)) > 0),
    request_parameters_json TEXT NOT NULL CHECK(length(trim(request_parameters_json)) > 0),
    response_content_type TEXT NOT NULL CHECK(length(trim(response_content_type)) > 0),
    http_status INTEGER NOT NULL CHECK(http_status = 200),
    raw_artifact_reference TEXT NOT NULL CHECK(length(trim(raw_artifact_reference)) > 0),
    raw_artifact_sha256 TEXT NOT NULL
        CHECK(length(raw_artifact_sha256) = 64
              AND raw_artifact_sha256 NOT GLOB '*[^0-9a-f]*'),
    provider_dataset_version TEXT NULL
        CHECK(provider_dataset_version IS NULL
              OR (length(provider_dataset_version) > 0
                  AND provider_dataset_version = trim(provider_dataset_version))),
    provider_dataset_version_source_field TEXT NULL
        CHECK(provider_dataset_version_source_field IS NULL
              OR length(trim(provider_dataset_version_source_field)) > 0),
    internal_evidence_identity_scheme TEXT NOT NULL
        CHECK(internal_evidence_identity_scheme = 'SYSTEM_CANONICAL_ARTIFACT_V1'),
    internal_evidence_identity TEXT NOT NULL
        CHECK(length(internal_evidence_identity) = 64
              AND internal_evidence_identity NOT GLOB '*[^0-9a-f]*'),
    import_status TEXT NOT NULL
        CHECK(import_status IN ('VALIDATED_ADMITTED', 'VALIDATED_REJECTED')),
    dataset_fingerprint TEXT NOT NULL
        CHECK(length(dataset_fingerprint) = 64
              AND dataset_fingerprint NOT GLOB '*[^0-9a-f]*'),
    manifest_fingerprint TEXT NOT NULL UNIQUE
        CHECK(length(manifest_fingerprint) = 64
              AND manifest_fingerprint NOT GLOB '*[^0-9a-f]*'),
    FOREIGN KEY(reference_rate_source_id, reference_rate_definition_id)
        REFERENCES reference_rate_source(
            reference_rate_source_id, reference_rate_definition_id
        ) ON DELETE RESTRICT,
    CHECK((provider_dataset_version IS NULL
           AND provider_dataset_version_source_field IS NULL)
          OR (provider_dataset_version IS NOT NULL
              AND provider_dataset_version_source_field IS NOT NULL)),
    UNIQUE(reference_rate_import_manifest_id, reference_rate_source_id,
           reference_rate_definition_id),
    UNIQUE(reference_rate_source_id, internal_evidence_identity_scheme,
           internal_evidence_identity)
);

CREATE TABLE reference_rate_observation (
    reference_rate_observation_id INTEGER PRIMARY KEY,
    provenance_contract_version INTEGER NOT NULL CHECK(provenance_contract_version = 2),
    reference_rate_definition_id INTEGER NOT NULL,
    reference_rate_source_id INTEGER NOT NULL,
    reference_rate_import_manifest_id INTEGER NOT NULL,
    observation_date TEXT NOT NULL CHECK(length(observation_date) = 10),
    provider_publication_date TEXT NULL
        CHECK(provider_publication_date IS NULL
              OR (length(provider_publication_date) = 10
                  AND provider_publication_date >= observation_date)),
    rate_decimal TEXT NOT NULL
        CHECK(typeof(rate_decimal) = 'text'
              AND length(trim(rate_decimal)) > 0
              AND rate_decimal = trim(rate_decimal)),
    provider_revision_id TEXT NULL
        CHECK(provider_revision_id IS NULL
              OR (length(provider_revision_id) > 0
                  AND provider_revision_id = trim(provider_revision_id))),
    provider_revision_id_source_field TEXT NULL
        CHECK(provider_revision_id_source_field IS NULL
              OR length(trim(provider_revision_id_source_field)) > 0),
    provider_revision_indicator TEXT NULL,
    provider_revision_indicator_source_field TEXT NULL
        CHECK(provider_revision_indicator_source_field IS NULL
              OR length(trim(provider_revision_indicator_source_field)) > 0),
    provider_revision_status TEXT NOT NULL CHECK(provider_revision_status IN (
        'PROVIDER_EXPLICIT_REVISION', 'PROVIDER_EXPLICIT_NO_REVISION',
        'PROVIDER_EMPTY_REVISION_INDICATOR', 'PROVIDER_REVISION_FIELD_NOT_SUPPLIED'
    )),
    provider_revision_contract_id TEXT NULL
        CHECK(provider_revision_contract_id IS NULL
              OR length(trim(provider_revision_contract_id)) > 0),
    provider_revision_contract_version TEXT NULL
        CHECK(provider_revision_contract_version IS NULL
              OR length(trim(provider_revision_contract_version)) > 0),
    provider_revision_contract_revision_indicator_value TEXT NULL
        CHECK(provider_revision_contract_revision_indicator_value IS NULL
              OR length(provider_revision_contract_revision_indicator_value) > 0),
    provider_revision_contract_authoritative_reference TEXT NULL
        CHECK(provider_revision_contract_authoritative_reference IS NULL
              OR length(trim(provider_revision_contract_authoritative_reference)) > 0),
    provider_revision_contract_fingerprint TEXT NULL
        CHECK(provider_revision_contract_fingerprint IS NULL
              OR (length(provider_revision_contract_fingerprint) = 64
                  AND provider_revision_contract_fingerprint NOT GLOB '*[^0-9a-f]*')),
    provider_publication_value TEXT NULL
        CHECK(provider_publication_value IS NULL OR length(provider_publication_value) > 0),
    provider_publication_value_kind TEXT NULL
        CHECK(provider_publication_value_kind IS NULL
              OR provider_publication_value_kind IN ('DATE', 'TIMESTAMP')),
    provider_publication_source_field TEXT NULL
        CHECK(provider_publication_source_field IS NULL
              OR length(trim(provider_publication_source_field)) > 0),
    availability_basis TEXT NOT NULL CHECK(availability_basis IN (
        'PROVIDER_REPORTED', 'OFFICIAL_SCHEDULE_DERIVED', 'RETRIEVAL_BOUND'
    )),
    availability_boundary_utc TEXT NOT NULL
        CHECK(length(availability_boundary_utc) = 27
              AND substr(availability_boundary_utc, 11, 1) = 'T'
              AND substr(availability_boundary_utc, 20, 1) = '.'
              AND substr(availability_boundary_utc, 27, 1) = 'Z'
              AND substr(availability_boundary_utc, 1, 10) >= observation_date),
    availability_derivation_rule_id TEXT NULL,
    availability_derivation_rule_version TEXT NULL,
    availability_policy_reference TEXT NULL,
    availability_calendar_id TEXT NULL,
    availability_calendar_version TEXT NULL,
    availability_calendar_fingerprint TEXT NULL
        CHECK(availability_calendar_fingerprint IS NULL
              OR (length(availability_calendar_fingerprint) = 64
                  AND availability_calendar_fingerprint NOT GLOB '*[^0-9a-f]*')),
    revision_sequence INTEGER NOT NULL CHECK(revision_sequence > 0),
    supersedes_observation_id INTEGER NULL
        REFERENCES reference_rate_observation(reference_rate_observation_id) ON DELETE RESTRICT,
    is_current INTEGER NOT NULL CHECK(is_current IN (0, 1)),
    quality_status TEXT NOT NULL CHECK(quality_status = 'ADMITTED_VALIDATED'),
    observation_fingerprint TEXT NOT NULL UNIQUE
        CHECK(length(observation_fingerprint) = 64
              AND observation_fingerprint NOT GLOB '*[^0-9a-f]*'),
    FOREIGN KEY(reference_rate_import_manifest_id, reference_rate_source_id,
                reference_rate_definition_id)
        REFERENCES reference_rate_import_manifest(
            reference_rate_import_manifest_id, reference_rate_source_id,
            reference_rate_definition_id
        ) ON DELETE RESTRICT,
    FOREIGN KEY(supersedes_observation_id, reference_rate_definition_id,
                reference_rate_source_id, observation_date)
        REFERENCES reference_rate_observation(
            reference_rate_observation_id, reference_rate_definition_id,
            reference_rate_source_id, observation_date
        ) ON DELETE RESTRICT,
    CHECK((revision_sequence = 1 AND supersedes_observation_id IS NULL)
          OR (revision_sequence > 1 AND supersedes_observation_id IS NOT NULL)),
    CHECK((provider_revision_id IS NULL AND provider_revision_id_source_field IS NULL)
          OR (provider_revision_id IS NOT NULL
              AND provider_revision_id_source_field IS NOT NULL)),
    CHECK(
        (provider_revision_status = 'PROVIDER_REVISION_FIELD_NOT_SUPPLIED'
         AND provider_revision_indicator IS NULL
         AND provider_revision_indicator_source_field IS NULL)
        OR (provider_revision_status = 'PROVIDER_EMPTY_REVISION_INDICATOR'
            AND provider_revision_indicator = ''
            AND provider_revision_indicator_source_field IS NOT NULL)
        OR (provider_revision_status IN (
                'PROVIDER_EXPLICIT_REVISION', 'PROVIDER_EXPLICIT_NO_REVISION'
            )
            AND provider_revision_indicator IS NOT NULL
            AND length(provider_revision_indicator) > 0
            AND provider_revision_indicator_source_field IS NOT NULL)
    ),
    CHECK(
        (provider_publication_value IS NULL
         AND provider_publication_value_kind IS NULL
         AND provider_publication_source_field IS NULL
         AND provider_publication_date IS NULL)
        OR (provider_publication_value IS NOT NULL
            AND provider_publication_value_kind IS NOT NULL
            AND provider_publication_source_field IS NOT NULL)
    ),
    CHECK(
        (revision_sequence = 1
         AND provider_revision_contract_id IS NULL
         AND provider_revision_contract_version IS NULL
         AND provider_revision_contract_revision_indicator_value IS NULL
         AND provider_revision_contract_authoritative_reference IS NULL
         AND provider_revision_contract_fingerprint IS NULL)
        OR (revision_sequence > 1
            AND provider_revision_status = 'PROVIDER_EXPLICIT_REVISION'
            AND provider_revision_contract_id IS NOT NULL
            AND provider_revision_contract_version IS NOT NULL
            AND provider_revision_contract_revision_indicator_value IS NOT NULL
            AND provider_revision_contract_revision_indicator_value
                = provider_revision_indicator
            AND provider_revision_contract_authoritative_reference IS NOT NULL
            AND provider_revision_contract_fingerprint IS NOT NULL)
    ),
    CHECK(
        (availability_basis = 'PROVIDER_REPORTED'
         AND provider_publication_value IS NOT NULL
         AND provider_publication_value_kind = 'TIMESTAMP'
         AND availability_derivation_rule_id IS NULL
         AND availability_derivation_rule_version IS NULL
         AND availability_policy_reference IS NULL
         AND availability_calendar_id IS NULL
         AND availability_calendar_version IS NULL
         AND availability_calendar_fingerprint IS NULL)
        OR (availability_basis = 'OFFICIAL_SCHEDULE_DERIVED'
            AND (provider_publication_value IS NULL
                 OR provider_publication_value_kind = 'DATE')
            AND availability_derivation_rule_id IS NOT NULL
            AND availability_derivation_rule_version IS NOT NULL
            AND availability_policy_reference IS NOT NULL
            AND availability_calendar_id IS NOT NULL
            AND availability_calendar_version IS NOT NULL
            AND availability_calendar_fingerprint IS NOT NULL)
        OR (availability_basis = 'RETRIEVAL_BOUND'
            AND availability_derivation_rule_id IS NULL
            AND availability_derivation_rule_version IS NULL
            AND availability_policy_reference IS NULL
            AND availability_calendar_id IS NULL
            AND availability_calendar_version IS NULL
            AND availability_calendar_fingerprint IS NULL)
    ),
    UNIQUE(reference_rate_observation_id, reference_rate_definition_id,
           reference_rate_source_id, observation_date),
    UNIQUE(reference_rate_definition_id, observation_date, revision_sequence)
);
CREATE UNIQUE INDEX reference_rate_observation_current
ON reference_rate_observation(reference_rate_definition_id, observation_date)
WHERE is_current = 1;
CREATE INDEX reference_rate_observation_date
ON reference_rate_observation(reference_rate_definition_id, observation_date);
CREATE UNIQUE INDEX reference_rate_observation_provider_revision
ON reference_rate_observation(reference_rate_source_id, observation_date,
                              provider_revision_id)
WHERE provider_revision_id IS NOT NULL;
CREATE INDEX reference_rate_observation_availability
ON reference_rate_observation(reference_rate_definition_id,
                              availability_boundary_utc,
                              observation_date, revision_sequence);
"""


_NAV_PROVENANCE_SCHEMA_SQL = """
CREATE TABLE nav_evidence_source (
    nav_evidence_source_id INTEGER PRIMARY KEY,
    contract_version INTEGER NOT NULL CHECK(contract_version = 1),
    source_code TEXT NOT NULL UNIQUE CHECK(length(trim(source_code)) > 0),
    source_organization TEXT NOT NULL CHECK(length(trim(source_organization)) > 0),
    source_governance TEXT NOT NULL
        CHECK(source_governance = 'APPROVED_DISTRIBUTOR_NON_AUTHORITATIVE'),
    identity_url_template TEXT NOT NULL CHECK(length(trim(identity_url_template)) > 0),
    series_url_template TEXT NOT NULL CHECK(length(trim(series_url_template)) > 0),
    source_role TEXT NOT NULL CHECK(source_role = 'APPROVED_DISTRIBUTOR'),
    approval_basis TEXT NOT NULL CHECK(length(trim(approval_basis)) > 0),
    licensing_reference TEXT NOT NULL CHECK(length(trim(licensing_reference)) > 0),
    automated_use_status TEXT NOT NULL CHECK(automated_use_status = 'PREVIOUSLY_APPROVED'),
    raw_retention_status TEXT NOT NULL CHECK(raw_retention_status = 'PREVIOUSLY_APPROVED'),
    source_fingerprint TEXT NOT NULL UNIQUE
        CHECK(length(source_fingerprint) = 64
              AND source_fingerprint NOT GLOB '*[^0-9a-f]*')
);

CREATE TABLE nav_import_manifest (
    nav_import_manifest_id INTEGER PRIMARY KEY,
    contract_version INTEGER NOT NULL CHECK(contract_version = 1),
    nav_evidence_source_id INTEGER NOT NULL
        REFERENCES nav_evidence_source(nav_evidence_source_id) ON DELETE RESTRICT,
    instrument_id INTEGER NOT NULL REFERENCES instrument(instrument_id) ON DELETE RESTRICT,
    exact_isin TEXT NOT NULL CHECK(length(exact_isin) = 12),
    share_class_name TEXT NOT NULL CHECK(length(trim(share_class_name)) > 0),
    nav_currency TEXT NOT NULL CHECK(nav_currency IN ('EUR', 'HUF')),
    provider_instrument_id TEXT NOT NULL CHECK(length(trim(provider_instrument_id)) > 0),
    identity_request_url TEXT NOT NULL CHECK(length(trim(identity_request_url)) > 0),
    identity_retrieval_timestamp TEXT NOT NULL CHECK(length(trim(identity_retrieval_timestamp)) > 0),
    identity_raw_artifact_reference TEXT NOT NULL CHECK(length(trim(identity_raw_artifact_reference)) > 0),
    identity_raw_artifact_sha256 TEXT NOT NULL
        CHECK(length(identity_raw_artifact_sha256) = 64
              AND identity_raw_artifact_sha256 NOT GLOB '*[^0-9a-f]*'),
    identity_receipt_reference TEXT NOT NULL CHECK(length(trim(identity_receipt_reference)) > 0),
    identity_receipt_sha256 TEXT NOT NULL
        CHECK(length(identity_receipt_sha256) = 64
              AND identity_receipt_sha256 NOT GLOB '*[^0-9a-f]*'),
    series_request_url TEXT NOT NULL CHECK(length(trim(series_request_url)) > 0),
    series_retrieval_timestamp TEXT NOT NULL CHECK(length(trim(series_retrieval_timestamp)) > 0),
    series_raw_artifact_reference TEXT NOT NULL CHECK(length(trim(series_raw_artifact_reference)) > 0),
    series_raw_artifact_sha256 TEXT NOT NULL
        CHECK(length(series_raw_artifact_sha256) = 64
              AND series_raw_artifact_sha256 NOT GLOB '*[^0-9a-f]*'),
    series_receipt_reference TEXT NOT NULL CHECK(length(trim(series_receipt_reference)) > 0),
    series_receipt_sha256 TEXT NOT NULL
        CHECK(length(series_receipt_sha256) = 64
              AND series_receipt_sha256 NOT GLOB '*[^0-9a-f]*'),
    currency_bundle_manifest_reference TEXT NOT NULL CHECK(length(trim(currency_bundle_manifest_reference)) > 0),
    currency_bundle_manifest_sha256 TEXT NOT NULL
        CHECK(length(currency_bundle_manifest_sha256) = 64
              AND currency_bundle_manifest_sha256 NOT GLOB '*[^0-9a-f]*'),
    combined_bundle_manifest_reference TEXT NOT NULL CHECK(length(trim(combined_bundle_manifest_reference)) > 0),
    combined_bundle_manifest_sha256 TEXT NOT NULL
        CHECK(length(combined_bundle_manifest_sha256) = 64
              AND combined_bundle_manifest_sha256 NOT GLOB '*[^0-9a-f]*'),
    acquisition_identity TEXT NOT NULL UNIQUE
        CHECK(length(acquisition_identity) = 64
              AND acquisition_identity NOT GLOB '*[^0-9a-f]*'),
    evidence_cutoff TEXT NOT NULL CHECK(length(evidence_cutoff) = 10),
    admitted_first_date TEXT NOT NULL CHECK(length(admitted_first_date) = 10),
    admitted_last_date TEXT NOT NULL CHECK(length(admitted_last_date) = 10),
    admitted_observation_count INTEGER NOT NULL CHECK(admitted_observation_count > 0),
    revision_semantics TEXT NOT NULL
        CHECK(revision_semantics IN ('PROVIDER_REVISION_FIELD_NOT_SUPPLIED', 'EXPLICIT_REPLACEMENT')),
    replaces_manifest_id INTEGER NULL
        REFERENCES nav_import_manifest(nav_import_manifest_id) ON DELETE RESTRICT,
    replacement_reason TEXT NULL,
    dataset_fingerprint TEXT NOT NULL
        CHECK(length(dataset_fingerprint) = 64
              AND dataset_fingerprint NOT GLOB '*[^0-9a-f]*'),
    manifest_fingerprint TEXT NOT NULL UNIQUE
        CHECK(length(manifest_fingerprint) = 64
              AND manifest_fingerprint NOT GLOB '*[^0-9a-f]*'),
    import_status TEXT NOT NULL CHECK(import_status = 'VALIDATED_ADMITTED'),
    CHECK((revision_semantics = 'PROVIDER_REVISION_FIELD_NOT_SUPPLIED'
           AND replaces_manifest_id IS NULL AND replacement_reason IS NULL)
          OR (revision_semantics = 'EXPLICIT_REPLACEMENT'
              AND replaces_manifest_id IS NOT NULL
              AND length(trim(replacement_reason)) > 0)),
    UNIQUE(nav_import_manifest_id, instrument_id, exact_isin),
    UNIQUE(nav_evidence_source_id, instrument_id, dataset_fingerprint)
);

CREATE TABLE nav_observation_version (
    nav_observation_version_id INTEGER PRIMARY KEY,
    nav_import_manifest_id INTEGER NOT NULL,
    instrument_id INTEGER NOT NULL,
    exact_isin TEXT NOT NULL CHECK(length(exact_isin) = 12),
    observation_date TEXT NOT NULL CHECK(length(observation_date) = 10),
    nav_decimal TEXT NOT NULL
        CHECK(typeof(nav_decimal) = 'text' AND length(trim(nav_decimal)) > 0
              AND nav_decimal = trim(nav_decimal)),
    currency_code TEXT NOT NULL CHECK(currency_code IN ('EUR', 'HUF')),
    provider_observation_identity TEXT NOT NULL
        CHECK(length(trim(provider_observation_identity)) > 0),
    provider_revision_id TEXT NULL,
    revision_sequence INTEGER NOT NULL CHECK(revision_sequence > 0),
    supersedes_observation_id INTEGER NULL
        REFERENCES nav_observation_version(nav_observation_version_id) ON DELETE RESTRICT,
    raw_artifact_sha256 TEXT NOT NULL
        CHECK(length(raw_artifact_sha256) = 64
              AND raw_artifact_sha256 NOT GLOB '*[^0-9a-f]*'),
    quality_status TEXT NOT NULL CHECK(quality_status = 'ADMITTED_VALIDATED'),
    observation_fingerprint TEXT NOT NULL UNIQUE
        CHECK(length(observation_fingerprint) = 64
              AND observation_fingerprint NOT GLOB '*[^0-9a-f]*'),
    FOREIGN KEY(nav_import_manifest_id, instrument_id, exact_isin)
        REFERENCES nav_import_manifest(nav_import_manifest_id, instrument_id, exact_isin)
        ON DELETE RESTRICT,
    FOREIGN KEY(supersedes_observation_id, instrument_id, observation_date)
        REFERENCES nav_observation_version(
            nav_observation_version_id, instrument_id, observation_date
        ) ON DELETE RESTRICT,
    CHECK((revision_sequence = 1 AND supersedes_observation_id IS NULL
           AND provider_revision_id IS NULL)
          OR (revision_sequence > 1 AND supersedes_observation_id IS NOT NULL
              AND length(trim(provider_revision_id)) > 0)),
    UNIQUE(nav_observation_version_id, instrument_id, observation_date),
    UNIQUE(nav_import_manifest_id, observation_date),
    UNIQUE(instrument_id, observation_date, revision_sequence),
    UNIQUE(nav_import_manifest_id, provider_observation_identity)
);
CREATE UNIQUE INDEX nav_observation_version_single_successor
ON nav_observation_version(supersedes_observation_id)
WHERE supersedes_observation_id IS NOT NULL;
CREATE INDEX nav_observation_version_date
ON nav_observation_version(instrument_id, observation_date);
CREATE TRIGGER nav_evidence_source_immutable_update
BEFORE UPDATE ON nav_evidence_source BEGIN
    SELECT RAISE(ABORT, 'NAV sources are immutable');
END;
CREATE TRIGGER nav_evidence_source_immutable_delete
BEFORE DELETE ON nav_evidence_source BEGIN
    SELECT RAISE(ABORT, 'NAV sources are immutable');
END;
CREATE TRIGGER nav_import_manifest_immutable_update
BEFORE UPDATE ON nav_import_manifest BEGIN
    SELECT RAISE(ABORT, 'NAV manifests are immutable');
END;
CREATE TRIGGER nav_import_manifest_immutable_delete
BEFORE DELETE ON nav_import_manifest BEGIN
    SELECT RAISE(ABORT, 'NAV manifests are immutable');
END;
CREATE TRIGGER nav_observation_version_immutable_update
BEFORE UPDATE ON nav_observation_version BEGIN
    SELECT RAISE(ABORT, 'NAV observations are immutable');
END;
CREATE TRIGGER nav_observation_version_immutable_delete
BEFORE DELETE ON nav_observation_version BEGIN
    SELECT RAISE(ABORT, 'NAV observations are immutable');
END;
"""


_SCHEMA_SQL = (
    _BASE_SCHEMA_SQL
    + _CONSTRUCTED_PORTFOLIO_SCHEMA_SQL
    + _REFERENCE_RATE_SCHEMA_SQL
)
