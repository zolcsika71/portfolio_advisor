"""Read-only health checks for SQLite files retained under ``database/``."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final

from portfolio_advisor.tbsz.repository import (
    CURRENT_SCHEMA_VERSION,
    tbsz_schema_issues,
    tbsz_v1_schema_issues,
)

_DATABASE_SUFFIXES: Final = frozenset({".sqlite", ".db"})
_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")
_ISIN: Final = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_CURRENCY: Final = re.compile(r"^[A-Z]{3}$")


@dataclass(frozen=True, slots=True)
class SchemaObject:
    """One SQLite schema object, without reading application data values."""

    name: str
    object_type: str
    sql: str | None


@dataclass(frozen=True, slots=True)
class ForeignKeyViolation:
    """SQLite's row-level foreign-key diagnostic, retained without cell values."""

    table: str
    rowid: int | None
    parent_table: str
    foreign_key_id: int


@dataclass(frozen=True, slots=True)
class DatabaseAuditResult:
    """A non-mutating health report for one project-local SQLite file."""

    relative_path: str
    size_bytes: int
    modified_at: datetime
    purpose: str
    ownership: str
    schema_owner: str
    schema_management: str
    schema_sql_required: bool
    integrity_check: tuple[str, ...]
    quick_check: tuple[str, ...]
    foreign_key_violations: tuple[ForeignKeyViolation, ...]
    journal_mode: str | None
    user_version: int | None
    application_id: int | None
    schema_version: int | None
    schema_objects: tuple[SchemaObject, ...]
    table_counts: tuple[tuple[str, int], ...]
    schema_status: str
    tbsz_invariant_violations: tuple[str, ...]
    warnings: tuple[str, ...]
    errors: tuple[str, ...]

    @property
    def healthy(self) -> bool:
        return not self.errors


@dataclass(frozen=True, slots=True)
class _DatabaseDescriptor:
    purpose: str
    ownership: str
    schema_owner: str
    schema_management: str
    schema_sql_required: bool


_KNOWN_DATABASES: Final = {
    "model_portfolio.sqlite": _DatabaseDescriptor(
        purpose="model-portfolio workbook snapshots and retained MNB OTC evidence",
        ownership="APPLICATION_OWNED_SOURCE_DERIVED",
        schema_owner="DB_creation.database_create and history.mnb_otc.MnbOtcRepository",
        schema_management="EMBEDDED_SCHEMA_SUFFICIENT",
        schema_sql_required=False,
    ),
    "official_historical_nav.sqlite": _DatabaseDescriptor(
        purpose="validated, source-neutral historical asset NAV evidence",
        ownership="APPLICATION_OWNED_EVIDENCE_STORE",
        schema_owner="history.official_nav_store.OfficialNavStore",
        schema_management="EMBEDDED_SCHEMA_SUFFICIENT",
        schema_sql_required=False,
    ),
    "prospective_portfolio_validation.sqlite": _DatabaseDescriptor(
        purpose="append-only prospective decision and outcome ledger",
        ownership="APPLICATION_OWNED_LEDGER",
        schema_owner="prospective.validation.ProspectiveValidationStore",
        schema_management="EMBEDDED_SCHEMA_SUFFICIENT",
        schema_sql_required=False,
    ),
    "tbsz_portfolio.sqlite": _DatabaseDescriptor(
        purpose="private local TBSZ source evidence and manual transaction ledger",
        ownership="APPLICATION_OWNED_PRIVATE_LEDGER",
        schema_owner="tbsz.repository.TbszPortfolioRepository",
        schema_management="VERSIONED_EMBEDDED_SCHEMA_WITH_MIGRATIONS",
        schema_sql_required=False,
    ),
}

_UNKNOWN_DESCRIPTOR: Final = _DatabaseDescriptor(
    purpose="unknown project-local SQLite file",
    ownership="UNCLASSIFIED",
    schema_owner="not established",
    schema_management="EXTERNAL_DATABASE_NO_SCHEMA_FILE_NEEDED",
    schema_sql_required=False,
)

_TBSZ_BACKUP_DESCRIPTOR: Final = _DatabaseDescriptor(
    purpose="verified local pre-migration backup of the private TBSZ ledger",
    ownership="LOCAL_ONLY_BACKUP",
    schema_owner="tbsz.repository.TbszPortfolioRepository (historical v0 backup)",
    schema_management="MIGRATION_BACKUP_NO_SCHEMA_FILE_NEEDED",
    schema_sql_required=False,
)


def audit_database_directory(database_root: Path) -> tuple[DatabaseAuditResult, ...]:
    """Audit only ``.sqlite``/``.db`` regular files below one fixed root.

    Symlinks are deliberately skipped so an audit cannot follow a database out
    of the project-local ``database/`` directory.
    """
    root = database_root.resolve()
    if not root.exists():
        return ()
    if not root.is_dir():
        raise ValueError(f"database root is not a directory: {database_root}")

    results: list[DatabaseAuditResult] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file() or path.suffix.casefold() not in _DATABASE_SUFFIXES:
            continue
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(root)
        except ValueError:
            continue
        results.append(_audit_database(resolved, relative.as_posix()))
    return tuple(results)


def audit_named_database(database_root: Path, database_name: str) -> DatabaseAuditResult:
    """Audit one named database while retaining the directory-scope boundary."""
    root = database_root.resolve()
    candidate = root / database_name
    if candidate.is_symlink() or candidate.suffix.casefold() not in _DATABASE_SUFFIXES:
        raise ValueError("database must be a non-symlink .sqlite or .db file under database/")
    try:
        resolved = candidate.resolve(strict=True)
        relative = resolved.relative_to(root)
    except (FileNotFoundError, ValueError) as error:
        raise ValueError("database must exist under database/") from error
    if not resolved.is_file():
        raise ValueError("database must be a regular file")
    return _audit_database(resolved, relative.as_posix())


def _audit_database(path: Path, relative_path: str) -> DatabaseAuditResult:
    is_tbsz_backup = relative_path.startswith("backups/tbsz_portfolio-")
    descriptor = (
        _TBSZ_BACKUP_DESCRIPTOR
        if is_tbsz_backup
        else _KNOWN_DATABASES.get(path.name, _UNKNOWN_DESCRIPTOR)
    )
    stat = path.stat()
    errors: list[str] = []
    warnings: list[str] = []
    try:
        connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as error:
        return _failed_result(path, relative_path, descriptor, f"open failed: {error}")
    try:
        integrity_check = _pragma_values(connection, "integrity_check")
        quick_check = _pragma_values(connection, "quick_check")
        foreign_key_violations = tuple(
            ForeignKeyViolation(
                table=str(row[0]),
                rowid=int(row[1]) if row[1] is not None else None,
                parent_table=str(row[2]),
                foreign_key_id=int(row[3]),
            )
            for row in connection.execute("PRAGMA foreign_key_check")
        )
        journal_mode = _pragma_scalar_text(connection, "journal_mode")
        user_version = _pragma_scalar_int(connection, "user_version")
        application_id = _pragma_scalar_int(connection, "application_id")
        schema_version = _pragma_scalar_int(connection, "schema_version")
        schema_objects = tuple(
            SchemaObject(str(row[0]), str(row[1]), str(row[2]) if row[2] is not None else None)
            for row in connection.execute(
                """SELECT name, type, sql FROM sqlite_master
                   WHERE type IN ('table', 'index', 'view', 'trigger')
                   ORDER BY type, name"""
            )
        )
        tables = tuple(
            item.name
            for item in schema_objects
            if item.object_type == "table" and not item.name.startswith("sqlite_")
        )
        table_counts = tuple((table, _count_rows(connection, table)) for table in tables)
        if integrity_check != ("ok",):
            errors.append("integrity_check did not return ok")
        if quick_check != ("ok",):
            errors.append("quick_check did not return ok")
        if foreign_key_violations:
            errors.append(f"foreign_key_check reported {len(foreign_key_violations)} violation(s)")

        schema_status = "UNCLASSIFIED"
        tbsz_violations: tuple[str, ...] = ()
        if path.name == "tbsz_portfolio.sqlite" or is_tbsz_backup:
            schema_issues = tbsz_schema_issues(connection)
            legacy_backup_issues = tbsz_v1_schema_issues(connection) if is_tbsz_backup else ("not a backup",)
            if is_tbsz_backup and not legacy_backup_issues and user_version in {0, 1}:
                schema_status = f"BACKUP_PRE_MIGRATION_SCHEMA_V{user_version}"
            elif schema_issues:
                schema_status = "SCHEMA_DRIFT"
                errors.extend(schema_issues)
            elif user_version != CURRENT_SCHEMA_VERSION:
                schema_status = "MIGRATION_REQUIRED"
                warnings.append(
                    f"TBSZ user_version is {user_version}; expected {CURRENT_SCHEMA_VERSION}"
                )
            else:
                schema_status = "MATCHES_CURRENT_SCHEMA"
            if not schema_issues:
                tbsz_violations = _tbsz_invariant_violations(connection)
                if tbsz_violations:
                    errors.extend(tbsz_violations)
        elif descriptor is _UNKNOWN_DESCRIPTOR:
            schema_status = "EXTERNAL_OR_UNCLASSIFIED"
            warnings.append("no application schema owner is registered for this database name")
        else:
            schema_status = "EMBEDDED_SCHEMA_OWNER_UNVERSIONED" if user_version == 0 else "VERSION_DECLARED"
            if user_version == 0:
                warnings.append("schema owner does not declare PRAGMA user_version")
        return DatabaseAuditResult(
            relative_path=relative_path,
            size_bytes=stat.st_size,
            modified_at=datetime.fromtimestamp(stat.st_mtime).astimezone(),
            purpose=descriptor.purpose,
            ownership=descriptor.ownership,
            schema_owner=descriptor.schema_owner,
            schema_management=descriptor.schema_management,
            schema_sql_required=descriptor.schema_sql_required,
            integrity_check=integrity_check,
            quick_check=quick_check,
            foreign_key_violations=foreign_key_violations,
            journal_mode=journal_mode,
            user_version=user_version,
            application_id=application_id,
            schema_version=schema_version,
            schema_objects=schema_objects,
            table_counts=table_counts,
            schema_status=schema_status,
            tbsz_invariant_violations=tbsz_violations,
            warnings=tuple(warnings),
            errors=tuple(errors),
        )
    except sqlite3.Error as error:
        return _failed_result(path, relative_path, descriptor, f"SQLite read-only audit failed: {error}")
    finally:
        connection.close()


def _failed_result(
    path: Path,
    relative_path: str,
    descriptor: _DatabaseDescriptor,
    error: str,
) -> DatabaseAuditResult:
    stat = path.stat()
    return DatabaseAuditResult(
        relative_path=relative_path,
        size_bytes=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime).astimezone(),
        purpose=descriptor.purpose,
        ownership=descriptor.ownership,
        schema_owner=descriptor.schema_owner,
        schema_management=descriptor.schema_management,
        schema_sql_required=descriptor.schema_sql_required,
        integrity_check=(),
        quick_check=(),
        foreign_key_violations=(),
        journal_mode=None,
        user_version=None,
        application_id=None,
        schema_version=None,
        schema_objects=(),
        table_counts=(),
        schema_status="CORRUPT_OR_INVALID",
        tbsz_invariant_violations=(),
        warnings=(),
        errors=(error,),
    )


def _tbsz_invariant_violations(connection: sqlite3.Connection) -> tuple[str, ...]:
    violations: list[str] = []
    account_labels = (str(row[0]) for row in connection.execute("SELECT label FROM tbsz_accounts"))
    invalid_account_labels = sum(
        not label.startswith("TBSZ")
        or "normal" in label.casefold()
        or "normál" in label.casefold()
        for label in account_labels
    )
    _append_violation(violations, "invalid TBSZ account labels", invalid_account_labels)
    _append_query_violation(
        violations,
        connection,
        "duplicate account labels",
        "SELECT COUNT(*) FROM (SELECT label FROM tbsz_accounts GROUP BY label HAVING COUNT(*) > 1)",
    )
    source_rows = connection.execute(
        "SELECT content_sha256, source_type, view_type FROM source_snapshots"
    )
    invalid_sources = sum(
        _SHA256.fullmatch(str(sha256)) is None
        or str(source_type) != "GEORGE_PDF"
        or str(view_type) not in {"POSITIONS", "CASH"}
        for sha256, source_type, view_type in source_rows
    )
    _append_violation(violations, "invalid source snapshot fields", invalid_sources)
    _append_query_violation(
        violations,
        connection,
        "duplicate source filenames",
        "SELECT COUNT(*) FROM (SELECT source_filename FROM source_snapshots GROUP BY source_filename HAVING COUNT(*) > 1)",
    )
    instrument_rows = connection.execute(
        "SELECT canonical_name, normalized_name, isin, identity_status FROM instruments"
    )
    allowed_identity_statuses = {
        "EXACT_ISIN",
        "MANUAL_CONFIRMED",
        "PROVIDER_NAME_EXACT_CANDIDATE",
        "IDENTITY_UNRESOLVED",
    }
    invalid_instruments = sum(
        not str(name).strip()
        or not str(normalized_name).strip()
        or (isin is not None and _ISIN.fullmatch(str(isin)) is None)
        or str(identity_status) not in allowed_identity_statuses
        for name, normalized_name, isin, identity_status in instrument_rows
    )
    _append_violation(violations, "invalid instrument fields", invalid_instruments)
    _append_query_violation(
        violations,
        connection,
        "duplicate normalized instrument names",
        "SELECT COUNT(*) FROM (SELECT normalized_name FROM instruments GROUP BY normalized_name HAVING COUNT(*) > 1)",
    )
    _append_query_violation(
        violations,
        connection,
        "position account/source mismatch",
        """SELECT COUNT(*) FROM position_snapshots AS position
           JOIN source_snapshots AS source ON source.snapshot_id = position.snapshot_id
           WHERE position.account_id != source.account_id""",
    )
    _append_query_violation(
        violations,
        connection,
        "duplicate position snapshot identity",
        """SELECT COUNT(*) FROM (
           SELECT snapshot_id, normalized_provider_name
           FROM position_snapshots
           GROUP BY snapshot_id, normalized_provider_name HAVING COUNT(*) > 1
        )""",
    )
    _append_query_violation(
        violations,
        connection,
        "inconsistent position reporting-value/currency pairing",
        """SELECT COUNT(*) FROM position_snapshots
           WHERE (reporting_value IS NULL) != (reporting_currency IS NULL)""",
    )
    _append_decimal_violations(
        violations,
        connection,
        "position_snapshots",
        ("quantity", "unit_price", "market_value", "reporting_value"),
        positive=False,
    )
    cash_rows = connection.execute("SELECT currency FROM cash_snapshots")
    _append_violation(
        violations,
        "invalid cash currencies",
        sum(_CURRENCY.fullmatch(str(row[0])) is None for row in cash_rows),
    )
    _append_query_violation(
        violations,
        connection,
        "duplicate cash snapshot currencies",
        "SELECT COUNT(*) FROM (SELECT snapshot_id, currency FROM cash_snapshots GROUP BY snapshot_id, currency HAVING COUNT(*) > 1)",
    )
    _append_decimal_violations(violations, connection, "cash_snapshots", ("balance",), positive=False)
    transaction_rows = connection.execute("SELECT action, currency, record_type FROM transactions")
    invalid_transactions = sum(
        str(action) not in {"BUY", "SELL"}
        or _CURRENCY.fullmatch(str(currency)) is None
        or str(record_type) != "MANUAL_USER_EXECUTED"
        for action, currency, record_type in transaction_rows
    )
    _append_violation(violations, "invalid transaction fields", invalid_transactions)
    _append_decimal_violations(violations, connection, "transactions", ("quantity", "price"), positive=True)
    _append_query_violation(
        violations,
        connection,
        "duplicate non-null transaction client references",
        """SELECT COUNT(*) FROM (
           SELECT account_id, client_reference FROM transactions
           WHERE client_reference IS NOT NULL
           GROUP BY account_id, client_reference HAVING COUNT(*) > 1
        )""",
    )
    return tuple(violations)


def _append_decimal_violations(
    violations: list[str],
    connection: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
    *,
    positive: bool,
) -> None:
    for column in columns:
        invalid = 0
        for row in connection.execute(
            f"SELECT {_quote_identifier(column)} FROM {_quote_identifier(table)} "
            f"WHERE {_quote_identifier(column)} IS NOT NULL"
        ):
            try:
                value = Decimal(str(row[0]))
                if not value.is_finite() or (value <= 0 if positive else value < 0):
                    invalid += 1
            except (InvalidOperation, ValueError):
                invalid += 1
        _append_violation(violations, f"invalid {table}.{column} decimal", invalid)


def _append_query_violation(
    violations: list[str], connection: sqlite3.Connection, label: str, query: str
) -> None:
    _append_violation(violations, label, int(connection.execute(query).fetchone()[0]))


def _append_violation(violations: list[str], label: str, count: int) -> None:
    if count:
        violations.append(f"{label}: {count}")


def _count_rows(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {_quote_identifier(table)}").fetchone()[0])


def _pragma_values(connection: sqlite3.Connection, name: str) -> tuple[str, ...]:
    return tuple(str(row[0]) for row in connection.execute(f"PRAGMA {name}"))


def _pragma_scalar_text(connection: sqlite3.Connection, name: str) -> str | None:
    row = connection.execute(f"PRAGMA {name}").fetchone()
    return str(row[0]) if row is not None else None


def _pragma_scalar_int(connection: sqlite3.Connection, name: str) -> int | None:
    row = connection.execute(f"PRAGMA {name}").fetchone()
    return int(row[0]) if row is not None else None


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'
