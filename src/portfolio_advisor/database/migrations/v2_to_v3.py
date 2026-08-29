"""Dry-run-only v2-to-v3 migration planning with an unconditional cutover guard."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


class CutoverNotAuthorized(RuntimeError):
    """Raised for every attempted v2-to-v3 data migration in this milestone."""


class UnsupportedMigrationSource(RuntimeError):
    """The source is not an explicitly recognized v2 migration source."""


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    """A non-mutating assessment of a future migration source."""

    source_path: Path
    source_schema_version: int
    action: str
    cutover_authorized: bool


def dry_run_v2_to_v3(source_path: Path) -> MigrationPlan:
    """Inspect a recognized v2 source read-only; never create a destination."""
    if not source_path.is_file() or source_path.is_symlink():
        raise ValueError("migration source must be a regular SQLite file")
    with sqlite3.connect(f"file:{source_path.resolve()}?mode=ro", uri=True) as connection:
        version = _version(connection)
    if version != 2:
        raise UnsupportedMigrationSource(f"unsupported migration source schema version {version}; expected 2")
    return MigrationPlan(source_path.resolve(), version, "DRY_RUN_ONLY_NO_DATA_MIGRATED", False)


def execute_v2_to_v3(*_args: object, **_kwargs: object) -> None:
    """Reject migration and cutover until a later milestone grants authority."""
    raise CutoverNotAuthorized(
        "Milestone 5 provides schema scaffolding only; retained-data migration and cutover are not authorized"
    )


def _version(connection: sqlite3.Connection) -> int:
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if "schema_version" not in tables:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])
    rows = connection.execute("SELECT version FROM schema_version WHERE singleton = 1").fetchall()
    if len(rows) != 1:
        raise UnsupportedMigrationSource("schema_version must contain exactly one singleton row")
    return int(rows[0][0])
