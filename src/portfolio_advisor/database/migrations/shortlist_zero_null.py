"""Governed NULL corrections for source-reported shortlist numeric zeros."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from portfolio_advisor.canonical import canonical_fingerprint

from .shortlist_parallel import INTEGRATION_VERSION, METRICS

FEATURE_ID = "SHORTLIST_ZERO_NULL_CORRECTION"
FEATURE_REVISION = 1
CONTRACT_VERSION = 1
REPLACEMENT_SEMANTICS = "EXPLICIT_NULL"
TARGET_HEADERS = tuple(METRICS)
FEATURE_FINGERPRINT = canonical_fingerprint(
    {
        "contract_version": CONTRACT_VERSION,
        "feature_id": FEATURE_ID,
        "revision": FEATURE_REVISION,
        "replacement_semantics": REPLACEMENT_SEMANTICS,
        "target_headers": TARGET_HEADERS,
    }
)


class ShortlistCorrectionError(RuntimeError):
    """Raised when correction provenance or replay validation fails."""


@dataclass(frozen=True, slots=True)
class CorrectionRequest:
    correction_id: str
    dataset_fingerprint: str
    initial_target_sha256: str
    authorization_reference: str
    reason: str


@dataclass(frozen=True, slots=True)
class CorrectionResult:
    correction_id: str
    dataset_fingerprint: str
    correction_set_fingerprint: str
    item_count: int
    replayed: bool


_SCHEMA_SQL = """
CREATE TABLE shortlist_metric_correction_admission (
    correction_id TEXT PRIMARY KEY CHECK(length(trim(correction_id)) > 0),
    contract_version INTEGER NOT NULL CHECK(contract_version = 1),
    dataset_fingerprint TEXT NOT NULL CHECK(length(dataset_fingerprint) = 64),
    integration_version TEXT NOT NULL,
    initial_target_sha256 TEXT NOT NULL CHECK(length(initial_target_sha256) = 64),
    authorization_reference TEXT NOT NULL CHECK(length(trim(authorization_reference)) > 0),
    reason TEXT NOT NULL CHECK(length(trim(reason)) > 0),
    replacement_semantics TEXT NOT NULL CHECK(replacement_semantics = 'EXPLICIT_NULL'),
    correction_set_fingerprint TEXT NOT NULL UNIQUE CHECK(length(correction_set_fingerprint) = 64),
    item_count INTEGER NOT NULL CHECK(item_count > 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE shortlist_metric_correction (
    correction_id TEXT NOT NULL REFERENCES shortlist_metric_correction_admission(correction_id),
    source_reference TEXT NOT NULL,
    source_file_sha256 TEXT NOT NULL CHECK(length(source_file_sha256) = 64),
    source_sheet_name TEXT NOT NULL,
    source_row_number INTEGER NOT NULL CHECK(source_row_number > 0),
    source_header TEXT NOT NULL,
    isin TEXT NOT NULL,
    metric_code TEXT NOT NULL,
    observation_date TEXT NOT NULL,
    expected_original_value REAL NOT NULL CHECK(expected_original_value = 0.0),
    replacement_value REAL NULL CHECK(replacement_value IS NULL),
    PRIMARY KEY(correction_id, source_reference),
    UNIQUE(source_reference)
);
CREATE TRIGGER shortlist_metric_correction_admission_immutable_update
BEFORE UPDATE ON shortlist_metric_correction_admission
BEGIN SELECT RAISE(ABORT, 'shortlist correction admissions are immutable'); END;
CREATE TRIGGER shortlist_metric_correction_admission_immutable_delete
BEFORE DELETE ON shortlist_metric_correction_admission
BEGIN SELECT RAISE(ABORT, 'shortlist correction admissions are immutable'); END;
CREATE TRIGGER shortlist_metric_correction_immutable_update
BEFORE UPDATE ON shortlist_metric_correction
BEGIN SELECT RAISE(ABORT, 'shortlist corrections are immutable'); END;
CREATE TRIGGER shortlist_metric_correction_immutable_delete
BEFORE DELETE ON shortlist_metric_correction
BEGIN SELECT RAISE(ABORT, 'shortlist corrections are immutable'); END;
CREATE VIEW v_effective_shortlist_metric_observation AS
SELECT imo.instrument_metric_observation_id,
       imo.instrument_id,
       imo.metric_id,
       md.metric_code,
       imo.observation_date,
       imo.value AS original_value,
       CASE WHEN correction.source_reference IS NULL
            THEN imo.value ELSE correction.replacement_value END AS effective_value,
       imo.provenance_type,
       imo.source_file_id,
       imo.calculation_version,
       imo.source_reference,
       correction.correction_id
FROM instrument_metric_observation AS imo
JOIN metric_definition AS md ON md.metric_id = imo.metric_id
JOIN instrument AS instrument ON instrument.instrument_id = imo.instrument_id
LEFT JOIN shortlist_metric_correction AS correction
  ON correction.source_reference = imo.source_reference
 AND correction.isin = instrument.isin
 AND correction.metric_code = md.metric_code
 AND correction.observation_date = imo.observation_date
 AND correction.expected_original_value = imo.value
WHERE imo.source_reference LIKE 'SHORTLIST:%';
"""

_SCHEMA_OBJECT_NAMES = frozenset(
    {
        "shortlist_metric_correction_admission",
        "shortlist_metric_correction",
        "v_effective_shortlist_metric_observation",
        "shortlist_metric_correction_admission_immutable_update",
        "shortlist_metric_correction_admission_immutable_delete",
        "shortlist_metric_correction_immutable_update",
        "shortlist_metric_correction_immutable_delete",
    }
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def backup_database(source: Path, destination: Path) -> str:
    """Create a non-overwriting, consistent SQLite backup."""
    source = source.resolve()
    destination = destination.resolve()
    if not source.is_file() or source.is_symlink():
        raise ShortlistCorrectionError("backup source must be a regular SQLite file")
    if destination.exists() or destination.is_symlink():
        raise ShortlistCorrectionError("backup destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as source_connection:
            source_connection.execute("PRAGMA query_only=ON")
            with sqlite3.connect(destination) as destination_connection:
                source_connection.backup(destination_connection)
                destination_connection.execute("PRAGMA foreign_keys=ON")
                _validate_sqlite_health(destination_connection)
    except BaseException:
        if destination.exists():
            destination.unlink()
        raise
    return sha256_file(destination)


def admit_zero_null_corrections(
    path: Path, request: CorrectionRequest
) -> CorrectionResult:
    """Install and admit the exact governed correction set atomically."""
    path = path.resolve()
    _validate_request(request)
    if not path.is_file() or path.is_symlink():
        raise ShortlistCorrectionError(
            "correction target must be a regular SQLite file"
        )
    before_sha256 = sha256_file(path)
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        existing = _existing_admission(connection, request.correction_id)
        if existing is not None:
            result = _validate_replay(connection, request, existing)
            if sha256_file(path) != before_sha256:
                raise ShortlistCorrectionError(
                    "exact replay unexpectedly changed the database"
                )
            return result
        if before_sha256 != request.initial_target_sha256:
            raise ShortlistCorrectionError(
                "initial target SHA-256 does not match authorization"
            )
        try:
            connection.execute("BEGIN IMMEDIATE")
            _install_schema(connection)
            items = _discover_items(connection, request.dataset_fingerprint)
            correction_set_fingerprint = canonical_fingerprint(items)
            connection.execute(
                """INSERT INTO shortlist_metric_correction_admission(
                       correction_id, contract_version, dataset_fingerprint,
                       integration_version, initial_target_sha256, authorization_reference,
                       reason, replacement_semantics, correction_set_fingerprint, item_count
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    request.correction_id,
                    CONTRACT_VERSION,
                    request.dataset_fingerprint,
                    INTEGRATION_VERSION,
                    request.initial_target_sha256,
                    request.authorization_reference,
                    request.reason,
                    REPLACEMENT_SEMANTICS,
                    correction_set_fingerprint,
                    len(items),
                ),
            )
            connection.executemany(
                """INSERT INTO shortlist_metric_correction(
                       correction_id, source_reference, source_file_sha256,
                       source_sheet_name, source_row_number, source_header, isin,
                       metric_code, observation_date, expected_original_value,
                       replacement_value
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0, NULL)""",
                ((request.correction_id, *item) for item in items),
            )
            validate_corrections(connection)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    return CorrectionResult(
        correction_id=request.correction_id,
        dataset_fingerprint=request.dataset_fingerprint,
        correction_set_fingerprint=correction_set_fingerprint,
        item_count=len(items),
        replayed=False,
    )


def validate_corrections(connection: sqlite3.Connection) -> None:
    """Validate installed schema, immutable bindings, and effective projection."""
    _validate_sqlite_health(connection)
    names = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view', 'trigger')"
        )
    }
    present = _SCHEMA_OBJECT_NAMES & names
    marker = connection.execute(
        "SELECT revision, contract_fingerprint FROM schema_feature_contract WHERE feature_id=?",
        (FEATURE_ID,),
    ).fetchall()
    if not present:
        if marker:
            raise ShortlistCorrectionError(
                "shortlist correction feature marker exists without its schema"
            )
        return
    if present != _SCHEMA_OBJECT_NAMES:
        raise ShortlistCorrectionError(
            "shortlist correction feature is partially installed"
        )
    if _schema_objects(connection) != _expected_schema_objects():
        raise ShortlistCorrectionError(
            "shortlist correction schema is damaged or incompatible"
        )
    if [tuple(row) for row in marker] != [(FEATURE_REVISION, FEATURE_FINGERPRINT)]:
        raise ShortlistCorrectionError(
            "shortlist correction feature marker is missing or stale"
        )
    manifest = _manifest(connection)
    admissions = connection.execute(
        "SELECT * FROM shortlist_metric_correction_admission ORDER BY correction_id"
    ).fetchall()
    if len(admissions) != 1:
        raise ShortlistCorrectionError(
            "exactly one shortlist correction admission is required"
        )
    admission = admissions[0]
    if (
        int(admission["contract_version"]) != CONTRACT_VERSION
        or str(admission["dataset_fingerprint"]) != str(manifest["dataset_fingerprint"])
        or str(admission["integration_version"]) != INTEGRATION_VERSION
        or str(admission["replacement_semantics"]) != REPLACEMENT_SEMANTICS
    ):
        raise ShortlistCorrectionError(
            "shortlist correction admission binding is stale"
        )
    items = _discover_items(connection, str(manifest["dataset_fingerprint"]))
    stored = _stored_items(connection, str(admission["correction_id"]))
    if stored != items:
        raise ShortlistCorrectionError(
            "shortlist correction item bindings do not match evidence"
        )
    if int(admission["item_count"]) != len(items):
        raise ShortlistCorrectionError("shortlist correction item count mismatch")
    if str(admission["correction_set_fingerprint"]) != canonical_fingerprint(items):
        raise ShortlistCorrectionError("shortlist correction set fingerprint mismatch")
    effective_zero_count = int(
        connection.execute(
            """SELECT count(*) FROM v_effective_shortlist_metric_observation
               WHERE metric_code IN ({}) AND effective_value = 0.0""".format(
                ",".join("?" for _ in METRICS)
            ),
            tuple(METRICS.values()),
        ).fetchone()[0]
    )
    if effective_zero_count:
        raise ShortlistCorrectionError("targeted effective shortlist zeros remain")


def validate_corrections_if_present(connection: sqlite3.Connection) -> None:
    """Reject partial or stale correction state, while allowing an absent feature."""
    names = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE 'shortlist_metric_correction%' "
            "OR name='v_effective_shortlist_metric_observation'"
        )
    }
    marker = connection.execute(
        "SELECT 1 FROM schema_feature_contract WHERE feature_id=?", (FEATURE_ID,)
    ).fetchone()
    if names or marker is not None:
        validate_corrections(connection)


def _install_schema(connection: sqlite3.Connection) -> None:
    names = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if "schema_feature_contract" not in names:
        raise ShortlistCorrectionError("schema feature contract table is unavailable")
    existing = connection.execute(
        "SELECT revision, contract_fingerprint FROM schema_feature_contract WHERE feature_id=?",
        (FEATURE_ID,),
    ).fetchall()
    if existing:
        raise ShortlistCorrectionError(
            "correction feature exists without the requested admission"
        )
    _execute_schema(connection)
    connection.execute(
        "INSERT INTO schema_feature_contract(feature_id, revision, contract_fingerprint) VALUES(?,?,?)",
        (FEATURE_ID, FEATURE_REVISION, FEATURE_FINGERPRINT),
    )


def _discover_items(
    connection: sqlite3.Connection, expected_dataset_fingerprint: str
) -> tuple[tuple[object, ...], ...]:
    manifest = _manifest(connection)
    if str(manifest["dataset_fingerprint"]) != expected_dataset_fingerprint:
        raise ShortlistCorrectionError(
            "shortlist dataset fingerprint does not match authorization"
        )
    rows = connection.execute(
        """SELECT sf.sha256, sh.sheet_name, occurrence.source_row_number,
                  occurrence.source_payload_json, instrument.isin, snapshot.snapshot_date,
                  occurrence.instrument_id, sf.source_file_id
           FROM shortlist_entry_source_occurrence AS occurrence
           JOIN shortlist_snapshot AS snapshot
             ON snapshot.shortlist_snapshot_id = occurrence.shortlist_snapshot_id
           JOIN source_sheet AS sh ON sh.source_sheet_id = occurrence.source_sheet_id
           JOIN source_file AS sf ON sf.source_file_id = sh.source_file_id
           JOIN instrument AS instrument ON instrument.instrument_id = occurrence.instrument_id
           ORDER BY sf.sha256, sh.sheet_name, occurrence.source_row_number"""
    ).fetchall()
    items: list[tuple[object, ...]] = []
    for row in rows:
        try:
            payload = json.loads(str(row["source_payload_json"]))
        except json.JSONDecodeError as error:
            raise ShortlistCorrectionError(
                "shortlist source payload is malformed"
            ) from error
        if not isinstance(payload, dict):
            raise ShortlistCorrectionError("shortlist source payload is not an object")
        for header, metric_code in METRICS.items():
            raw_value = payload.get(header)
            if raw_value in (None, ""):
                continue
            source_reference = (
                f"SHORTLIST:{row['sha256']}:{row['sheet_name']}:"
                f"{row['source_row_number']}:{header}"
            )
            observations = connection.execute(
                """SELECT imo.value
                   FROM instrument_metric_observation AS imo
                   JOIN metric_definition AS md ON md.metric_id = imo.metric_id
                   WHERE imo.instrument_id=? AND imo.observation_date=?
                     AND imo.provenance_type='PROVIDER_REPORTED'
                     AND imo.source_file_id=? AND imo.source_reference=?
                     AND md.metric_code=?""",
                (
                    int(row["instrument_id"]),
                    str(row["snapshot_date"]),
                    int(row["source_file_id"]),
                    source_reference,
                    metric_code,
                ),
            ).fetchall()
            if len(observations) != 1:
                raise ShortlistCorrectionError(
                    "shortlist metric provenance is missing or ambiguous"
                )
            original_value = observations[0][0]
            if not isinstance(original_value, (int, float)):
                raise ShortlistCorrectionError("shortlist metric is not numeric")
            try:
                raw_numeric = float(str(raw_value))
            except ValueError as error:
                raise ShortlistCorrectionError(
                    "normalized shortlist metric has a non-numeric raw source value"
                ) from error
            if float(original_value) != raw_numeric:
                raise ShortlistCorrectionError(
                    "normalized shortlist metric does not match its raw source value"
                )
            if float(original_value) == 0.0:
                items.append(
                    (
                        source_reference,
                        str(row["sha256"]),
                        str(row["sheet_name"]),
                        int(row["source_row_number"]),
                        header,
                        str(row["isin"]),
                        metric_code,
                        str(row["snapshot_date"]),
                    )
                )
    if not items:
        raise ShortlistCorrectionError("authorized shortlist correction set is empty")
    return tuple(sorted(items))


def _stored_items(
    connection: sqlite3.Connection, correction_id: str
) -> tuple[tuple[object, ...], ...]:
    return tuple(
        tuple(row)
        for row in connection.execute(
            """SELECT source_reference, source_file_sha256, source_sheet_name,
                      source_row_number, source_header, isin, metric_code, observation_date
               FROM shortlist_metric_correction WHERE correction_id=?
               ORDER BY source_reference""",
            (correction_id,),
        )
    )


def _manifest(connection: sqlite3.Connection) -> sqlite3.Row:
    rows = connection.execute(
        "SELECT * FROM shortlist_stage_manifest WHERE singleton=1"
    ).fetchall()
    if len(rows) != 1:
        raise ShortlistCorrectionError("shortlist manifest is missing or ambiguous")
    row = rows[0]
    if (
        str(row["completion_status"]) != "COMPLETE"
        or str(row["integration_version"]) != INTEGRATION_VERSION
    ):
        raise ShortlistCorrectionError(
            "shortlist manifest is incomplete or incompatible"
        )
    return row


def _existing_admission(
    connection: sqlite3.Connection, correction_id: str
) -> sqlite3.Row | None:
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='shortlist_metric_correction_admission'"
    ).fetchone()
    if table is None:
        return None
    rows = connection.execute(
        "SELECT * FROM shortlist_metric_correction_admission WHERE correction_id=?",
        (correction_id,),
    ).fetchall()
    if len(rows) > 1:
        raise ShortlistCorrectionError("correction admission identity is ambiguous")
    return rows[0] if rows else None


def _validate_replay(
    connection: sqlite3.Connection, request: CorrectionRequest, existing: sqlite3.Row
) -> CorrectionResult:
    validate_corrections(connection)
    expected = {
        "contract_version": CONTRACT_VERSION,
        "dataset_fingerprint": request.dataset_fingerprint,
        "integration_version": INTEGRATION_VERSION,
        "initial_target_sha256": request.initial_target_sha256,
        "authorization_reference": request.authorization_reference,
        "reason": request.reason,
        "replacement_semantics": REPLACEMENT_SEMANTICS,
    }
    if any(str(existing[key]) != str(value) for key, value in expected.items()):
        raise ShortlistCorrectionError(
            "correction replay bindings do not exactly match"
        )
    return CorrectionResult(
        correction_id=request.correction_id,
        dataset_fingerprint=request.dataset_fingerprint,
        correction_set_fingerprint=str(existing["correction_set_fingerprint"]),
        item_count=int(existing["item_count"]),
        replayed=True,
    )


def _validate_request(request: CorrectionRequest) -> None:
    if not request.correction_id.strip():
        raise ShortlistCorrectionError("correction identity is required")
    for name, value in (
        ("dataset fingerprint", request.dataset_fingerprint),
        ("initial target SHA-256", request.initial_target_sha256),
    ):
        if len(value) != 64 or any(
            character not in "0123456789abcdef" for character in value
        ):
            raise ShortlistCorrectionError(
                f"{name} must be lowercase hexadecimal SHA-256"
            )
    if not request.authorization_reference.strip() or not request.reason.strip():
        raise ShortlistCorrectionError(
            "authorization reference and reason are required"
        )


def _validate_sqlite_health(connection: sqlite3.Connection) -> None:
    if tuple(str(row[0]) for row in connection.execute("PRAGMA integrity_check")) != (
        "ok",
    ):
        raise ShortlistCorrectionError("SQLite integrity_check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise ShortlistCorrectionError("SQLite foreign_key_check failed")


def _execute_schema(connection: sqlite3.Connection) -> None:
    statement = ""
    for line in _SCHEMA_SQL.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            connection.execute(statement)
            statement = ""
    if statement.strip():
        raise ShortlistCorrectionError("correction schema SQL is incomplete")


def _schema_objects(connection: sqlite3.Connection) -> tuple[tuple[str, ...], ...]:
    placeholders = ",".join("?" for _ in _SCHEMA_OBJECT_NAMES)
    rows = connection.execute(
        f"""SELECT type, name, tbl_name, sql FROM sqlite_master
            WHERE name IN ({placeholders}) ORDER BY type, name""",
        tuple(sorted(_SCHEMA_OBJECT_NAMES)),
    ).fetchall()
    return tuple(
        (str(row[0]), str(row[1]), str(row[2]), " ".join(str(row[3]).split()))
        for row in rows
    )


def _expected_schema_objects() -> tuple[tuple[str, ...], ...]:
    with sqlite3.connect(":memory:") as scratch:
        _execute_schema(scratch)
        return _schema_objects(scratch)
