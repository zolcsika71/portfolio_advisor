"""Read-only validation helpers shared by future transactional migrations."""

from __future__ import annotations

import sqlite3


class MigrationValidationError(RuntimeError):
    """A migration validation failed and must prevent commit/cutover."""


def validate_integrity(connection: sqlite3.Connection) -> None:
    """Require SQLite integrity and foreign-key checks to pass."""
    integrity = tuple(str(row[0]) for row in connection.execute("PRAGMA integrity_check"))
    if integrity != ("ok",):
        raise MigrationValidationError("integrity_check did not return ok")
    violations = tuple(connection.execute("PRAGMA foreign_key_check"))
    if violations:
        raise MigrationValidationError(f"foreign_key_check reported {len(violations)} violation(s)")
