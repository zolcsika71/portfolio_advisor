"""Copy-on-write Milestone 11B schema-v3 feature migration."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from portfolio_advisor.canonical import canonical_fingerprint, canonical_json
from portfolio_advisor.database.schema.v3 import (
    CONSTRUCTED_PORTFOLIO_FEATURE_FINGERPRINT,
    CONSTRUCTED_PORTFOLIO_FEATURE_ID,
    CONSTRUCTED_PORTFOLIO_FEATURE_REVISION,
    connect,
    detect_schema_version,
    initialize_schema,
    upgrade_schema_v3_constructed_portfolio_extension,
    validate_schema,
)

MIGRATION_REVISION = "MILESTONE_11B_CONSTRUCTED_PORTFOLIO_SCHEMA_V1"
_FEATURE_TABLES = frozenset(
    {
        "constructed_portfolio_holding_lineage",
        "constructed_portfolio_metadata",
        "schema_feature_contract",
    }
)


class ConstructedPortfolioMigrationError(RuntimeError):
    """The additive candidate migration did not pass every safety gate."""


@dataclass(frozen=True, slots=True)
class ConstructedPortfolioMigrationResult:
    """Deterministic evidence from a successful disposable candidate build."""

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
            "feature_id": CONSTRUCTED_PORTFOLIO_FEATURE_ID,
            "feature_revision": self.feature_revision,
            "migration_revision": self.migration_revision,
            "schema_contract_fingerprint": self.schema_contract_fingerprint,
            "source_logical_fingerprint": self.source_logical_fingerprint,
            "source_sha256": self.source_sha256,
        }


def build_constructed_portfolio_schema_candidate(
    *, source: Path, candidate: Path
) -> ConstructedPortfolioMigrationResult:
    """Copy a recognized v3 source, add only 11B schema, and validate it fully."""
    source = source.resolve()
    candidate = candidate.resolve()
    if not source.is_file() or source.is_symlink():
        raise ConstructedPortfolioMigrationError("migration source must be a regular SQLite file")
    if candidate == source:
        raise ConstructedPortfolioMigrationError("candidate must differ from the installed source")
    if candidate.exists():
        raise ConstructedPortfolioMigrationError("candidate target already exists")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    source_sha256 = _sha256(source)
    source_logical, counts = base_logical_fingerprint(source)
    try:
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as source_connection:
            source_connection.execute("PRAGMA foreign_keys=ON")
            source_connection.execute("PRAGMA query_only=ON")
            if detect_schema_version(source_connection) != 3:
                raise ConstructedPortfolioMigrationError("migration source is not recognized schema v3")
            if tuple(str(row[0]) for row in source_connection.execute("PRAGMA integrity_check")) != (
                "ok",
            ):
                raise ConstructedPortfolioMigrationError("source integrity_check failed")
            if source_connection.execute("PRAGMA foreign_key_check").fetchall():
                raise ConstructedPortfolioMigrationError("source foreign_key_check failed")
            with sqlite3.connect(candidate) as candidate_connection:
                candidate_connection.execute("PRAGMA foreign_keys=ON")
                source_connection.backup(candidate_connection)
        with connect(candidate) as connection:
            upgrade_schema_v3_constructed_portfolio_extension(connection)
            validate_schema(connection)
            if _constructed_row_count(connection) != 0:
                raise ConstructedPortfolioMigrationError(
                    "schema candidate unexpectedly contains constructed portfolio rows"
                )
            schema_contract = constructed_schema_contract(connection)
        candidate_logical, candidate_counts = base_logical_fingerprint(candidate)
        if source_logical != candidate_logical or counts != candidate_counts:
            raise ConstructedPortfolioMigrationError(
                "candidate changed pre-existing schema-v3 content"
            )
        with sqlite3.connect(":memory:") as scratch:
            scratch.row_factory = sqlite3.Row
            initialize_schema(scratch)
            from_scratch_contract = constructed_schema_contract(scratch)
        if schema_contract != from_scratch_contract:
            raise ConstructedPortfolioMigrationError(
                "migrated and from-scratch constructed schema contracts differ"
            )
        if _sha256(source) != source_sha256:
            raise ConstructedPortfolioMigrationError("source database changed during migration")
        return ConstructedPortfolioMigrationResult(
            migration_revision=MIGRATION_REVISION,
            source_sha256=source_sha256,
            candidate_sha256=_sha256(candidate),
            source_logical_fingerprint=source_logical,
            candidate_base_logical_fingerprint=candidate_logical,
            schema_contract_fingerprint=canonical_fingerprint(schema_contract),
            feature_contract_fingerprint=CONSTRUCTED_PORTFOLIO_FEATURE_FINGERPRINT,
            feature_revision=CONSTRUCTED_PORTFOLIO_FEATURE_REVISION,
            base_table_counts=counts,
        )
    except BaseException:
        if candidate.exists():
            candidate.unlink()
        raise


def base_logical_fingerprint(path: Path) -> tuple[str, tuple[tuple[str, int], ...]]:
    """Fingerprint every pre-11B table row and column value deterministically."""
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
            if str(row[0]) not in _FEATURE_TABLES
        )
        for table in tables:
            columns = tuple(
                str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')
            )
            order = ", ".join(f'"{column}"' for column in columns)
            rows = connection.execute(f'SELECT * FROM "{table}" ORDER BY {order}')
            count = 0
            digest.update(canonical_json({"columns": columns, "table": table}).encode())
            for row in rows:
                digest.update(canonical_json(list(row)).encode())
                count += 1
            counts.append((table, count))
    return digest.hexdigest(), tuple(counts)


def constructed_schema_contract(connection: sqlite3.Connection) -> dict[str, object]:
    """Return a normalized feature-only schema contract for path-independent checks."""
    objects = []
    for row in connection.execute(
        """SELECT type, name, tbl_name, sql FROM sqlite_master
           WHERE name IN (
               'schema_feature_contract',
               'constructed_portfolio_metadata',
               'constructed_portfolio_holding_lineage',
               'constructed_portfolio_metadata_shortlist_snapshot',
               'constructed_portfolio_holding_lineage_membership'
           ) ORDER BY type, name"""
    ):
        objects.append(
            {
                "name": str(row[1]),
                "sql": " ".join(str(row[3]).split()),
                "table": str(row[2]),
                "type": str(row[0]),
            }
        )
    marker = connection.execute(
        """SELECT feature_id, revision, contract_fingerprint
           FROM schema_feature_contract WHERE feature_id=?""",
        (CONSTRUCTED_PORTFOLIO_FEATURE_ID,),
    ).fetchall()
    return {"marker": [list(row) for row in marker], "objects": objects}


def _constructed_row_count(connection: sqlite3.Connection) -> int:
    return sum(
        int(connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0])
        for table in (
            "constructed_portfolio_holding_lineage",
            "constructed_portfolio_metadata",
        )
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
