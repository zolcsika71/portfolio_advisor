"""Governed effective classifications for immutable shortlist source evidence."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from portfolio_advisor.canonical import canonical_fingerprint

from .shortlist_parallel import INTEGRATION_VERSION
from .shortlist_zero_null import sha256_file

FEATURE_ID = "SHORTLIST_CLASSIFICATION_CORRECTION"
FEATURE_REVISION = 1
CONTRACT_VERSION = 1
SOURCE_HEADER = "Aleszközosztály"
ASSET_CLASS_HEADER = "Eszközosztály"
ORIGINAL_SUB_ASSET_CLASS = "Fejl?d? piacok"
EFFECTIVE_SUB_ASSET_CLASS = "Fejlődő piacok"
FEATURE_FINGERPRINT = canonical_fingerprint(
    {
        "contract_version": CONTRACT_VERSION,
        "effective_sub_asset_class": EFFECTIVE_SUB_ASSET_CLASS,
        "feature_id": FEATURE_ID,
        "original_sub_asset_class": ORIGINAL_SUB_ASSET_CLASS,
        "revision": FEATURE_REVISION,
        "scope": "SHORTLIST_SUB_ASSET_CLASS",
        "source_header": SOURCE_HEADER,
    }
)


class ShortlistClassificationCorrectionError(RuntimeError):
    """Classification correction provenance or replay validation failed."""


@dataclass(frozen=True, slots=True)
class ClassificationCorrectionRequest:
    correction_id: str
    dataset_fingerprint: str
    initial_target_sha256: str
    authorization_reference: str
    reason: str


@dataclass(frozen=True, slots=True)
class ClassificationCorrectionResult:
    correction_id: str
    dataset_fingerprint: str
    correction_set_fingerprint: str
    item_count: int
    replayed: bool


@dataclass(frozen=True, slots=True)
class ClassificationCorrectionBinding:
    correction_id: str
    correction_set_fingerprint: str
    application_order: int = 1
    validated_data_version: int | None = None

    def to_dict(self) -> dict[str, str]:
        return {
            "correction_id": self.correction_id,
            "correction_set_fingerprint": self.correction_set_fingerprint,
        }


def _composition_extension_present(connection: sqlite3.Connection) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='shortlist_classification_composition_admission'"
        ).fetchone()
        is not None
    )


def ensure_classification_binding_current(
    connection: sqlite3.Connection,
    binding: ClassificationCorrectionBinding,
) -> None:
    """Reject use of a validated binding after another connection commits."""
    if binding.validated_data_version is None:
        raise ShortlistClassificationCorrectionError(
            "classification correction binding lacks a validation version"
        )
    current = int(connection.execute("PRAGMA data_version").fetchone()[0])
    if current != binding.validated_data_version:
        raise ShortlistClassificationCorrectionError(
            "classification correction binding became stale"
        )


@dataclass(frozen=True, slots=True)
class SourceClassification:
    source_occurrence_id: int
    currency: str | None
    original_asset_class: str | None
    original_sub_asset_class: str | None
    effective_asset_class: str | None
    effective_sub_asset_class: str | None
    conflict_status: str
    correction_id: str | None


_SCHEMA_SQL = """
CREATE TABLE shortlist_classification_correction_admission (
    correction_id TEXT PRIMARY KEY CHECK(length(trim(correction_id)) > 0),
    contract_version INTEGER NOT NULL CHECK(contract_version = 1),
    dataset_fingerprint TEXT NOT NULL CHECK(length(dataset_fingerprint) = 64),
    integration_version TEXT NOT NULL,
    initial_target_sha256 TEXT NOT NULL CHECK(length(initial_target_sha256) = 64),
    authorization_reference TEXT NOT NULL CHECK(length(trim(authorization_reference)) > 0),
    reason TEXT NOT NULL CHECK(length(trim(reason)) > 0),
    source_header TEXT NOT NULL CHECK(source_header = 'Aleszközosztály'),
    original_sub_asset_class TEXT NOT NULL CHECK(original_sub_asset_class = 'Fejl?d? piacok'),
    effective_sub_asset_class TEXT NOT NULL CHECK(effective_sub_asset_class = 'Fejlődő piacok'),
    correction_set_fingerprint TEXT NOT NULL UNIQUE CHECK(length(correction_set_fingerprint) = 64),
    item_count INTEGER NOT NULL CHECK(item_count > 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE shortlist_classification_correction (
    correction_id TEXT NOT NULL REFERENCES shortlist_classification_correction_admission(correction_id),
    source_key TEXT NOT NULL,
    source_file_sha256 TEXT NOT NULL CHECK(length(source_file_sha256) = 64),
    source_sheet_name TEXT NOT NULL,
    source_row_number INTEGER NOT NULL CHECK(source_row_number > 0),
    source_header TEXT NOT NULL CHECK(source_header = 'Aleszközosztály'),
    snapshot_date TEXT NOT NULL,
    isin TEXT NOT NULL,
    asset_class TEXT NOT NULL CHECK(length(trim(asset_class)) > 0),
    expected_original_sub_asset_class TEXT NOT NULL CHECK(expected_original_sub_asset_class = 'Fejl?d? piacok'),
    effective_sub_asset_class TEXT NOT NULL CHECK(effective_sub_asset_class = 'Fejlődő piacok'),
    PRIMARY KEY(correction_id, source_key),
    UNIQUE(source_key)
);
CREATE TRIGGER shortlist_classification_correction_admission_immutable_update
BEFORE UPDATE ON shortlist_classification_correction_admission
BEGIN SELECT RAISE(ABORT, 'shortlist classification correction admissions are immutable'); END;
CREATE TRIGGER shortlist_classification_correction_admission_immutable_delete
BEFORE DELETE ON shortlist_classification_correction_admission
BEGIN SELECT RAISE(ABORT, 'shortlist classification correction admissions are immutable'); END;
CREATE TRIGGER shortlist_classification_correction_immutable_update
BEFORE UPDATE ON shortlist_classification_correction
BEGIN SELECT RAISE(ABORT, 'shortlist classification corrections are immutable'); END;
CREATE TRIGGER shortlist_classification_correction_immutable_delete
BEFORE DELETE ON shortlist_classification_correction
BEGIN SELECT RAISE(ABORT, 'shortlist classification corrections are immutable'); END;
CREATE VIEW v_effective_shortlist_classification AS
SELECT occurrence.shortlist_entry_source_occurrence_id,
       occurrence.shortlist_snapshot_id,
       occurrence.instrument_id,
       source_file.sha256 AS source_file_sha256,
       source_sheet.sheet_name AS source_sheet_name,
       occurrence.source_row_number,
       instrument.isin,
       occurrence.observed_currency_code,
       occurrence.observed_asset_class AS original_asset_class,
       occurrence.observed_asset_class AS effective_asset_class,
       occurrence.observed_sub_asset_class AS original_sub_asset_class,
       CASE WHEN correction.source_key IS NULL
            THEN occurrence.observed_sub_asset_class
            ELSE correction.effective_sub_asset_class END AS effective_sub_asset_class,
       occurrence.conflict_status,
       correction.correction_id,
       admission.correction_set_fingerprint
FROM shortlist_entry_source_occurrence AS occurrence
JOIN shortlist_snapshot AS snapshot
  ON snapshot.shortlist_snapshot_id = occurrence.shortlist_snapshot_id
JOIN source_sheet AS source_sheet
  ON source_sheet.source_sheet_id = occurrence.source_sheet_id
JOIN source_file AS source_file
  ON source_file.source_file_id = source_sheet.source_file_id
JOIN instrument AS instrument
  ON instrument.instrument_id = occurrence.instrument_id
LEFT JOIN shortlist_classification_correction AS correction
  ON correction.source_file_sha256 = source_file.sha256
 AND correction.source_sheet_name = source_sheet.sheet_name
 AND correction.source_row_number = occurrence.source_row_number
 AND correction.snapshot_date = snapshot.snapshot_date
 AND correction.isin = instrument.isin
 AND correction.asset_class = occurrence.observed_asset_class
 AND correction.expected_original_sub_asset_class = occurrence.observed_sub_asset_class
LEFT JOIN shortlist_classification_correction_admission AS admission
  ON admission.correction_id = correction.correction_id;
"""

_SCHEMA_OBJECT_NAMES = frozenset(
    {
        "shortlist_classification_correction_admission",
        "shortlist_classification_correction",
        "v_effective_shortlist_classification",
        "shortlist_classification_correction_admission_immutable_update",
        "shortlist_classification_correction_admission_immutable_delete",
        "shortlist_classification_correction_immutable_update",
        "shortlist_classification_correction_immutable_delete",
    }
)


def admit_classification_correction(
    path: Path, request: ClassificationCorrectionRequest
) -> ClassificationCorrectionResult:
    """Install and admit the exact source-bound mapping atomically."""
    path = path.resolve()
    _validate_request(request)
    if not path.is_file() or path.is_symlink():
        raise ShortlistClassificationCorrectionError(
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
                raise ShortlistClassificationCorrectionError(
                    "exact replay unexpectedly changed the database"
                )
            return result
        if before_sha256 != request.initial_target_sha256:
            raise ShortlistClassificationCorrectionError(
                "initial target SHA-256 does not match authorization"
            )
        try:
            connection.execute("BEGIN IMMEDIATE")
            _install_schema(connection)
            items = _discover_items(connection, request.dataset_fingerprint)
            correction_set_fingerprint = canonical_fingerprint(items)
            connection.execute(
                """INSERT INTO shortlist_classification_correction_admission(
                       correction_id, contract_version, dataset_fingerprint,
                       integration_version, initial_target_sha256,
                       authorization_reference, reason, source_header,
                       original_sub_asset_class, effective_sub_asset_class,
                       correction_set_fingerprint, item_count
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    request.correction_id,
                    CONTRACT_VERSION,
                    request.dataset_fingerprint,
                    INTEGRATION_VERSION,
                    request.initial_target_sha256,
                    request.authorization_reference,
                    request.reason,
                    SOURCE_HEADER,
                    ORIGINAL_SUB_ASSET_CLASS,
                    EFFECTIVE_SUB_ASSET_CLASS,
                    correction_set_fingerprint,
                    len(items),
                ),
            )
            connection.executemany(
                """INSERT INTO shortlist_classification_correction(
                       correction_id, source_key, source_file_sha256,
                       source_sheet_name, source_row_number, source_header,
                       snapshot_date, isin, asset_class,
                       expected_original_sub_asset_class,
                       effective_sub_asset_class
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ((request.correction_id, *item) for item in items),
            )
            validate_classification_corrections(connection)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    return ClassificationCorrectionResult(
        correction_id=request.correction_id,
        dataset_fingerprint=request.dataset_fingerprint,
        correction_set_fingerprint=correction_set_fingerprint,
        item_count=len(items),
        replayed=False,
    )


def validate_classification_corrections(connection: sqlite3.Connection) -> None:
    """Validate installed DDL, immutable evidence bindings, and projection."""
    if _composition_extension_present(connection):
        from .shortlist_classification_composition import (
            validate_composed_classification_corrections,
        )

        validate_composed_classification_corrections(connection)
        return
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
            raise ShortlistClassificationCorrectionError(
                "classification correction marker exists without its schema"
            )
        return
    if present != _SCHEMA_OBJECT_NAMES:
        raise ShortlistClassificationCorrectionError(
            "classification correction feature is partially installed"
        )
    if _schema_objects(connection) != _expected_schema_objects():
        raise ShortlistClassificationCorrectionError(
            "classification correction schema is damaged or incompatible"
        )
    if [tuple(row) for row in marker] != [(FEATURE_REVISION, FEATURE_FINGERPRINT)]:
        raise ShortlistClassificationCorrectionError(
            "classification correction marker is missing or stale"
        )
    manifest = _manifest(connection)
    admissions = connection.execute(
        "SELECT * FROM shortlist_classification_correction_admission ORDER BY correction_id"
    ).fetchall()
    if len(admissions) != 1:
        raise ShortlistClassificationCorrectionError(
            "exactly one classification correction admission is required"
        )
    admission = admissions[0]
    if (
        int(admission["contract_version"]) != CONTRACT_VERSION
        or str(admission["dataset_fingerprint"]) != str(manifest["dataset_fingerprint"])
        or str(admission["integration_version"]) != INTEGRATION_VERSION
        or str(admission["source_header"]) != SOURCE_HEADER
        or str(admission["original_sub_asset_class"]) != ORIGINAL_SUB_ASSET_CLASS
        or str(admission["effective_sub_asset_class"]) != EFFECTIVE_SUB_ASSET_CLASS
    ):
        raise ShortlistClassificationCorrectionError(
            "classification correction admission binding is stale"
        )
    items = _discover_items(connection, str(manifest["dataset_fingerprint"]))
    stored = _stored_items(connection, str(admission["correction_id"]))
    if stored != items:
        raise ShortlistClassificationCorrectionError(
            "classification correction item bindings do not match evidence"
        )
    if int(admission["item_count"]) != len(items):
        raise ShortlistClassificationCorrectionError(
            "classification correction item count mismatch"
        )
    if str(admission["correction_set_fingerprint"]) != canonical_fingerprint(items):
        raise ShortlistClassificationCorrectionError(
            "classification correction set fingerprint mismatch"
        )
    projected = connection.execute(
        """SELECT count(*),
                  sum(CASE WHEN effective_sub_asset_class=? THEN 1 ELSE 0 END),
                  sum(CASE WHEN original_sub_asset_class=?
                                AND correction_id IS NULL THEN 1 ELSE 0 END)
           FROM v_effective_shortlist_classification
           WHERE original_sub_asset_class=?""",
        (
            EFFECTIVE_SUB_ASSET_CLASS,
            ORIGINAL_SUB_ASSET_CLASS,
            ORIGINAL_SUB_ASSET_CLASS,
        ),
    ).fetchone()
    if projected is None or tuple(int(value or 0) for value in projected) != (
        len(items),
        len(items),
        0,
    ):
        raise ShortlistClassificationCorrectionError(
            "effective classification projection is incomplete"
        )


def validate_classification_corrections_if_present(
    connection: sqlite3.Connection,
) -> None:
    """Reject partial or stale state while allowing an absent feature."""
    names = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE name LIKE 'shortlist_classification_correction%' "
            "OR name='v_effective_shortlist_classification'"
        )
    }
    marker = connection.execute(
        "SELECT 1 FROM schema_feature_contract WHERE feature_id=?", (FEATURE_ID,)
    ).fetchone()
    if names or marker is not None:
        validate_classification_corrections(connection)


def active_classification_correction(
    connection: sqlite3.Connection,
) -> ClassificationCorrectionBinding | None:
    """Return the validated active mapping identity, if installed."""
    before_version = int(connection.execute("PRAGMA data_version").fetchone()[0])
    validate_classification_corrections_if_present(connection)
    if _composition_extension_present(connection):
        from .shortlist_classification_composition import (
            active_composed_classification_correction,
        )

        binding = active_composed_classification_correction(connection)
    else:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='shortlist_classification_correction_admission'"
        ).fetchone()
        if table is None:
            return None
        row = connection.execute(
            "SELECT correction_id, correction_set_fingerprint "
            "FROM shortlist_classification_correction_admission"
        ).fetchone()
        if row is None:
            raise ShortlistClassificationCorrectionError(
                "classification correction admission is missing"
            )
        binding = ClassificationCorrectionBinding(str(row[0]), str(row[1]))
    after_version = int(connection.execute("PRAGMA data_version").fetchone()[0])
    if before_version != after_version:
        raise ShortlistClassificationCorrectionError(
            "classification correction state changed during validation"
        )
    return ClassificationCorrectionBinding(
        correction_id=binding.correction_id,
        correction_set_fingerprint=binding.correction_set_fingerprint,
        application_order=binding.application_order,
        validated_data_version=after_version,
    )


def load_entry_classifications(
    connection: sqlite3.Connection,
    shortlist_entry_id: int,
    *,
    apply_corrections: bool,
    correction_binding: ClassificationCorrectionBinding | None = None,
) -> tuple[SourceClassification, ...]:
    """Load original and explicitly selected classifications for one membership."""
    if correction_binding is not None:
        ensure_classification_binding_current(connection, correction_binding)
    if apply_corrections:
        parameters: tuple[object, ...]
        if _composition_extension_present(connection):
            if correction_binding is None:
                raise ShortlistClassificationCorrectionError(
                    "composed classification reads require a validated binding"
                )
            application_order = correction_binding.application_order
            maximum_order = int(
                connection.execute(
                    "SELECT max(application_order) "
                    "FROM shortlist_classification_composition_admission"
                ).fetchone()[0]
            )
            if application_order == maximum_order:
                source = "v_effective_shortlist_classification"
                parameters = (shortlist_entry_id,)
            else:
                source = """(
                    SELECT stage.*
                    FROM v_shortlist_classification_correction_stage AS stage
                    WHERE stage.application_order=(
                        SELECT max(candidate.application_order)
                        FROM v_shortlist_classification_correction_stage AS candidate
                        WHERE candidate.shortlist_entry_source_occurrence_id=
                              stage.shortlist_entry_source_occurrence_id
                          AND candidate.application_order<=?
                    )
                )"""
                parameters = (application_order, shortlist_entry_id)
        else:
            source = "v_effective_shortlist_classification"
            parameters = (shortlist_entry_id,)
        asset = "classification.effective_asset_class"
        sub_asset = "classification.effective_sub_asset_class"
        correction = "classification.correction_id"
    else:
        source = "shortlist_entry_source_occurrence"
        asset = "classification.observed_asset_class"
        sub_asset = "classification.observed_sub_asset_class"
        correction = "NULL"
        parameters = (shortlist_entry_id,)
    rows = connection.execute(
        f"""SELECT classification.shortlist_entry_source_occurrence_id,
                   classification.observed_currency_code,
                   classification.{("original_asset_class" if apply_corrections else "observed_asset_class")},
                   classification.{("original_sub_asset_class" if apply_corrections else "observed_sub_asset_class")},
                   {asset}, {sub_asset}, classification.conflict_status, {correction}
            FROM shortlist_entry_lineage AS lineage
            JOIN {source} AS classification
              ON classification.shortlist_entry_source_occurrence_id=lineage.source_occurrence_id
            WHERE lineage.shortlist_entry_id=?
            ORDER BY classification.shortlist_entry_source_occurrence_id""",
        parameters,
    ).fetchall()
    result = tuple(
        SourceClassification(
            source_occurrence_id=int(row[0]),
            currency=_optional_text(row[1]),
            original_asset_class=_optional_text(row[2]),
            original_sub_asset_class=_optional_text(row[3]),
            effective_asset_class=_optional_text(row[4]),
            effective_sub_asset_class=_optional_text(row[5]),
            conflict_status=str(row[6]),
            correction_id=None if row[7] is None else str(row[7]),
        )
        for row in rows
    )
    if correction_binding is not None:
        ensure_classification_binding_current(connection, correction_binding)
    return result


def _discover_items(
    connection: sqlite3.Connection, expected_dataset_fingerprint: str
) -> tuple[tuple[object, ...], ...]:
    manifest = _manifest(connection)
    if str(manifest["dataset_fingerprint"]) != expected_dataset_fingerprint:
        raise ShortlistClassificationCorrectionError(
            "shortlist dataset fingerprint does not match authorization"
        )
    rows = connection.execute(
        """SELECT source_file.sha256, source_sheet.sheet_name,
                  occurrence.source_row_number, occurrence.source_payload_json,
                  snapshot.snapshot_date, instrument.isin,
                  occurrence.observed_asset_class,
                  occurrence.observed_sub_asset_class
           FROM shortlist_entry_source_occurrence AS occurrence
           JOIN shortlist_snapshot AS snapshot
             ON snapshot.shortlist_snapshot_id=occurrence.shortlist_snapshot_id
           JOIN source_sheet AS source_sheet
             ON source_sheet.source_sheet_id=occurrence.source_sheet_id
           JOIN source_file AS source_file
             ON source_file.source_file_id=source_sheet.source_file_id
           JOIN instrument AS instrument
             ON instrument.instrument_id=occurrence.instrument_id
           WHERE occurrence.observed_sub_asset_class=?
           ORDER BY source_file.sha256, source_sheet.sheet_name,
                    occurrence.source_row_number, instrument.isin""",
        (ORIGINAL_SUB_ASSET_CLASS,),
    ).fetchall()
    items: list[tuple[object, ...]] = []
    for row in rows:
        try:
            payload = json.loads(str(row["source_payload_json"]))
        except json.JSONDecodeError as error:
            raise ShortlistClassificationCorrectionError(
                "shortlist source payload is malformed"
            ) from error
        if not isinstance(payload, dict):
            raise ShortlistClassificationCorrectionError(
                "shortlist source payload is not an object"
            )
        if (
            payload.get(SOURCE_HEADER) != ORIGINAL_SUB_ASSET_CLASS
            or payload.get(ASSET_CLASS_HEADER) != row["observed_asset_class"]
            or payload.get("ISIN") != row["isin"]
        ):
            raise ShortlistClassificationCorrectionError(
                "normalized shortlist classification does not match raw source evidence"
            )
        source_key = (
            f"SHORTLIST_CLASSIFICATION:{row['sha256']}:{row['sheet_name']}:"
            f"{row['source_row_number']}:{SOURCE_HEADER}"
        )
        items.append(
            (
                source_key,
                str(row["sha256"]),
                str(row["sheet_name"]),
                int(row["source_row_number"]),
                SOURCE_HEADER,
                str(row["snapshot_date"]),
                str(row["isin"]),
                str(row["observed_asset_class"]),
                ORIGINAL_SUB_ASSET_CLASS,
                EFFECTIVE_SUB_ASSET_CLASS,
            )
        )
    if not items:
        raise ShortlistClassificationCorrectionError(
            "authorized classification correction set is empty"
        )
    return tuple(sorted(items))


def _stored_items(
    connection: sqlite3.Connection, correction_id: str
) -> tuple[tuple[object, ...], ...]:
    return tuple(
        tuple(row)
        for row in connection.execute(
            """SELECT source_key, source_file_sha256, source_sheet_name,
                      source_row_number, source_header, snapshot_date, isin,
                      asset_class, expected_original_sub_asset_class,
                      effective_sub_asset_class
               FROM shortlist_classification_correction
               WHERE correction_id=? ORDER BY source_key""",
            (correction_id,),
        )
    )


def _manifest(connection: sqlite3.Connection) -> sqlite3.Row:
    rows = connection.execute(
        "SELECT * FROM shortlist_stage_manifest WHERE singleton=1"
    ).fetchall()
    if len(rows) != 1:
        raise ShortlistClassificationCorrectionError(
            "shortlist manifest is missing or ambiguous"
        )
    row = rows[0]
    if (
        str(row["completion_status"]) != "COMPLETE"
        or str(row["integration_version"]) != INTEGRATION_VERSION
    ):
        raise ShortlistClassificationCorrectionError(
            "shortlist manifest is incomplete or incompatible"
        )
    return row


def _existing_admission(
    connection: sqlite3.Connection, correction_id: str
) -> sqlite3.Row | None:
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='shortlist_classification_correction_admission'"
    ).fetchone()
    if table is None:
        return None
    rows = connection.execute(
        "SELECT * FROM shortlist_classification_correction_admission "
        "WHERE correction_id=?",
        (correction_id,),
    ).fetchall()
    if len(rows) > 1:
        raise ShortlistClassificationCorrectionError(
            "classification correction identity is ambiguous"
        )
    return rows[0] if rows else None


def _validate_replay(
    connection: sqlite3.Connection,
    request: ClassificationCorrectionRequest,
    existing: sqlite3.Row,
) -> ClassificationCorrectionResult:
    validate_classification_corrections(connection)
    expected = {
        "contract_version": CONTRACT_VERSION,
        "dataset_fingerprint": request.dataset_fingerprint,
        "integration_version": INTEGRATION_VERSION,
        "initial_target_sha256": request.initial_target_sha256,
        "authorization_reference": request.authorization_reference,
        "reason": request.reason,
        "source_header": SOURCE_HEADER,
        "original_sub_asset_class": ORIGINAL_SUB_ASSET_CLASS,
        "effective_sub_asset_class": EFFECTIVE_SUB_ASSET_CLASS,
    }
    if any(str(existing[key]) != str(value) for key, value in expected.items()):
        raise ShortlistClassificationCorrectionError(
            "classification correction replay bindings do not exactly match"
        )
    return ClassificationCorrectionResult(
        correction_id=request.correction_id,
        dataset_fingerprint=request.dataset_fingerprint,
        correction_set_fingerprint=str(existing["correction_set_fingerprint"]),
        item_count=int(existing["item_count"]),
        replayed=True,
    )


def _validate_request(request: ClassificationCorrectionRequest) -> None:
    if not request.correction_id.strip():
        raise ShortlistClassificationCorrectionError(
            "classification correction identity is required"
        )
    for name, value in (
        ("dataset fingerprint", request.dataset_fingerprint),
        ("initial target SHA-256", request.initial_target_sha256),
    ):
        if len(value) != 64 or any(
            character not in "0123456789abcdef" for character in value
        ):
            raise ShortlistClassificationCorrectionError(
                f"{name} must be lowercase hexadecimal SHA-256"
            )
    if not request.authorization_reference.strip() or not request.reason.strip():
        raise ShortlistClassificationCorrectionError(
            "authorization reference and reason are required"
        )


def _install_schema(connection: sqlite3.Connection) -> None:
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='schema_feature_contract'"
    ).fetchone()
    if table is None:
        raise ShortlistClassificationCorrectionError(
            "schema feature contract table is unavailable"
        )
    existing = connection.execute(
        "SELECT revision, contract_fingerprint FROM schema_feature_contract "
        "WHERE feature_id=?",
        (FEATURE_ID,),
    ).fetchall()
    if existing:
        raise ShortlistClassificationCorrectionError(
            "classification correction feature exists without the requested admission"
        )
    _execute_schema(connection)
    connection.execute(
        "INSERT INTO schema_feature_contract(feature_id, revision, contract_fingerprint) "
        "VALUES (?, ?, ?)",
        (FEATURE_ID, FEATURE_REVISION, FEATURE_FINGERPRINT),
    )


def _validate_sqlite_health(connection: sqlite3.Connection) -> None:
    if tuple(str(row[0]) for row in connection.execute("PRAGMA integrity_check")) != (
        "ok",
    ):
        raise ShortlistClassificationCorrectionError("SQLite integrity_check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise ShortlistClassificationCorrectionError("SQLite foreign_key_check failed")


def _execute_schema(connection: sqlite3.Connection) -> None:
    statement = ""
    for line in _SCHEMA_SQL.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            connection.execute(statement)
            statement = ""
    if statement.strip():
        raise ShortlistClassificationCorrectionError(
            "classification correction schema SQL is incomplete"
        )


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


def _optional_text(value: object) -> str | None:
    result = str(value).strip() if value is not None else ""
    return result or None
