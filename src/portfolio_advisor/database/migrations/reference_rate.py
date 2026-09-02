"""Copy-on-write schema-v3 reference-rate evidence migration."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from portfolio_advisor.canonical import canonical_fingerprint, canonical_json
from portfolio_advisor.database.schema.v3 import (
    REFERENCE_RATE_FEATURE_FINGERPRINT,
    REFERENCE_RATE_FEATURE_ID,
    REFERENCE_RATE_FEATURE_REVISION,
    connect,
    detect_schema_version,
    initialize_schema,
    reference_rate_schema_objects,
    upgrade_schema_v3_reference_rate_extension,
    validate_schema,
)

LEGACY_REFERENCE_RATE_MIGRATION_REVISION = "MILESTONE_11C_REFERENCE_RATE_SCHEMA_V1"
REFERENCE_RATE_MIGRATION_REVISION = (
    "MILESTONE_11C_PHASE_C0_REFERENCE_RATE_PROVENANCE_V2"
)
_REFERENCE_TABLES = frozenset(
    {
        "reference_rate_definition",
        "reference_rate_import_manifest",
        "reference_rate_observation",
        "reference_rate_source",
    }
)


class ReferenceRateMigrationError(RuntimeError):
    """The additive reference-rate candidate failed a safety contract."""


@dataclass(frozen=True, slots=True)
class ReferenceRateMigrationResult:
    """Deterministic evidence returned for a validated disposable candidate."""

    migration_revision: str
    source_sha256: str
    candidate_sha256: str
    source_logical_fingerprint: str
    candidate_base_logical_fingerprint: str
    schema_contract_fingerprint: str
    feature_contract_fingerprint: str
    feature_revision: int
    base_table_counts: tuple[tuple[str, int], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "base_table_counts": dict(self.base_table_counts),
            "candidate_base_logical_fingerprint": self.candidate_base_logical_fingerprint,
            "candidate_sha256": self.candidate_sha256,
            "feature_contract_fingerprint": self.feature_contract_fingerprint,
            "feature_id": REFERENCE_RATE_FEATURE_ID,
            "feature_revision": self.feature_revision,
            "migration_revision": self.migration_revision,
            "schema_contract_fingerprint": self.schema_contract_fingerprint,
            "source_logical_fingerprint": self.source_logical_fingerprint,
            "source_sha256": self.source_sha256,
        }


def build_reference_rate_schema_candidate(
    *, source: Path, candidate: Path
) -> ReferenceRateMigrationResult:
    """Copy recognized schema v3 and add only empty reference-rate structures."""
    source = source.resolve()
    candidate = candidate.resolve()
    if not source.is_file() or source.is_symlink():
        raise ReferenceRateMigrationError("migration source must be a regular SQLite file")
    if candidate == source:
        raise ReferenceRateMigrationError("candidate must differ from the installed source")
    if candidate.exists():
        raise ReferenceRateMigrationError("candidate target already exists")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    source_sha256 = _sha256(source)
    source_logical, counts = pre_reference_rate_logical_fingerprint(source)
    try:
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as source_connection:
            source_connection.execute("PRAGMA foreign_keys=ON")
            source_connection.execute("PRAGMA query_only=ON")
            if detect_schema_version(source_connection) != 3:
                raise ReferenceRateMigrationError("migration source is not recognized schema v3")
            validate_schema(source_connection)
            source_names = {
                str(row[0])
                for row in source_connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            source_marker = source_connection.execute(
                "SELECT 1 FROM schema_feature_contract WHERE feature_id=?",
                (REFERENCE_RATE_FEATURE_ID,),
            ).fetchone()
            if (_REFERENCE_TABLES & source_names) or source_marker is not None:
                raise ReferenceRateMigrationError("reference-rate feature is already installed")
            if tuple(
                str(row[0]) for row in source_connection.execute("PRAGMA integrity_check")
            ) != ("ok",):
                raise ReferenceRateMigrationError("source integrity_check failed")
            if source_connection.execute("PRAGMA foreign_key_check").fetchall():
                raise ReferenceRateMigrationError("source foreign_key_check failed")
            with sqlite3.connect(candidate) as candidate_connection:
                candidate_connection.execute("PRAGMA foreign_keys=ON")
                source_connection.backup(candidate_connection)

        with connect(candidate) as connection:
            upgrade_schema_v3_reference_rate_extension(connection)
            validate_schema(connection)
            if _reference_row_count(connection) != 0:
                raise ReferenceRateMigrationError(
                    "schema candidate unexpectedly contains reference-rate evidence"
                )
            schema_contract = reference_rate_schema_contract(connection)

        candidate_logical, candidate_counts = pre_reference_rate_logical_fingerprint(candidate)
        if source_logical != candidate_logical or counts != candidate_counts:
            raise ReferenceRateMigrationError(
                "candidate changed pre-existing schema-v3 content"
            )

        with sqlite3.connect(":memory:") as scratch:
            scratch.row_factory = sqlite3.Row
            initialize_schema(scratch)
            from_scratch_contract = reference_rate_schema_contract(scratch)
        if schema_contract != from_scratch_contract:
            raise ReferenceRateMigrationError(
                "migrated and from-scratch reference-rate schema contracts differ"
            )
        if _sha256(source) != source_sha256:
            raise ReferenceRateMigrationError("source database changed during migration")
        return ReferenceRateMigrationResult(
            migration_revision=REFERENCE_RATE_MIGRATION_REVISION,
            source_sha256=source_sha256,
            candidate_sha256=_sha256(candidate),
            source_logical_fingerprint=source_logical,
            candidate_base_logical_fingerprint=candidate_logical,
            schema_contract_fingerprint=canonical_fingerprint(schema_contract),
            feature_contract_fingerprint=REFERENCE_RATE_FEATURE_FINGERPRINT,
            feature_revision=REFERENCE_RATE_FEATURE_REVISION,
            base_table_counts=counts,
        )
    except BaseException:
        if candidate.exists():
            candidate.unlink()
        raise


def pre_reference_rate_logical_fingerprint(
    path: Path,
) -> tuple[str, tuple[tuple[str, int], ...]]:
    """Fingerprint all pre-feature values, including Milestone 11B rows and marker."""
    digest = hashlib.sha256()
    counts: list[tuple[str, int]] = []
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA query_only=ON")
        tables = sorted(
            str(row[0])
            for row in connection.execute(
                """SELECT name FROM sqlite_master
                   WHERE type='table' AND name NOT LIKE 'sqlite_%'
                   ORDER BY name"""
            )
            if str(row[0]) not in _REFERENCE_TABLES
        )
        for table in tables:
            columns = tuple(
                str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')
            )
            order = ", ".join(f'"{column}"' for column in columns)
            sql = f'SELECT * FROM "{table}"'
            parameters: tuple[object, ...] = ()
            if table == "schema_feature_contract":
                sql += " WHERE feature_id <> ?"
                parameters = (REFERENCE_RATE_FEATURE_ID,)
            sql += f" ORDER BY {order}"
            rows = connection.execute(sql, parameters)
            count = 0
            digest.update(canonical_json({"columns": columns, "table": table}).encode())
            for row in rows:
                digest.update(canonical_json(list(row)).encode())
                count += 1
            counts.append((table, count))
    return digest.hexdigest(), tuple(counts)


def reference_rate_schema_contract(connection: sqlite3.Connection) -> dict[str, object]:
    """Return a path-independent reference-rate feature schema contract."""
    objects = [
        {"type": row[0], "name": row[1], "table": row[2], "sql": row[3]}
        for row in reference_rate_schema_objects(connection)
    ]
    marker = connection.execute(
        """SELECT feature_id, revision, contract_fingerprint
           FROM schema_feature_contract WHERE feature_id=?""",
        (REFERENCE_RATE_FEATURE_ID,),
    ).fetchall()
    return {"marker": [list(row) for row in marker], "objects": objects}


def validate_reference_rate_schema_foundation(target: Path) -> dict[str, object]:
    """Validate the current reference-rate structure read-only, populated or empty."""
    target = target.resolve()
    if not target.is_file() or target.is_symlink():
        raise ReferenceRateMigrationError(
            "reference-rate schema target must be a regular SQLite file"
        )
    before = _sha256(target)
    with sqlite3.connect(f"file:{target}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA query_only=ON")
        if detect_schema_version(connection) != 3:
            raise ReferenceRateMigrationError("reference-rate target is not schema v3")
        validate_schema(connection)
        integrity = tuple(
            str(row[0]) for row in connection.execute("PRAGMA integrity_check")
        )
        if integrity != ("ok",):
            raise ReferenceRateMigrationError("target integrity_check failed")
        foreign_key_violations = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
        if foreign_key_violations:
            raise ReferenceRateMigrationError("target foreign_key_check failed")
        installed_contract = reference_rate_schema_contract(connection)
        row_counts = {
            table: int(
                connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
            )
            for table in sorted(_REFERENCE_TABLES)
        }

    with sqlite3.connect(":memory:") as scratch:
        scratch.row_factory = sqlite3.Row
        initialize_schema(scratch)
        expected_contract = reference_rate_schema_contract(scratch)
    if installed_contract != expected_contract:
        raise ReferenceRateMigrationError(
            "installed reference-rate schema differs from the reviewed contract"
        )
    after = _sha256(target)
    if before != after:
        raise ReferenceRateMigrationError("read-only schema validation changed database bytes")
    return {
        "audit_schema_version": 2,
        "database_sha256": before,
        "evidence_status": "PRESENT" if any(row_counts.values()) else "NOT_INGESTED",
        "feature_contract_fingerprint": REFERENCE_RATE_FEATURE_FINGERPRINT,
        "feature_id": REFERENCE_RATE_FEATURE_ID,
        "feature_revision": REFERENCE_RATE_FEATURE_REVISION,
        "foreign_key_violations": 0,
        "integrity_check": "ok",
        "migration_revision": REFERENCE_RATE_MIGRATION_REVISION,
        "reference_rate_row_counts": row_counts,
        "runtime_admission": "NO_GO",
        "schema_contract_fingerprint": canonical_fingerprint(installed_contract),
        "status": "PASS",
    }


def _reference_row_count(connection: sqlite3.Connection) -> int:
    return sum(
        int(connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0])
        for table in _REFERENCE_TABLES
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
