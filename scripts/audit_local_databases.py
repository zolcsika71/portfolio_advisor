"""Read-only SQLite health audit scoped strictly to the project database/ directory."""

from __future__ import annotations

import argparse
from pathlib import Path

from portfolio_advisor.database.audit import (
    DatabaseAuditResult,
    audit_database_directory,
    audit_named_database,
)

DEFAULT_DATABASE_DIRECTORY = Path("database")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        help="Optional .sqlite or .db filename relative to database/; no paths outside that directory are accepted.",
    )
    args = parser.parse_args(argv)
    try:
        results = (
            (audit_named_database(DEFAULT_DATABASE_DIRECTORY, args.database),)
            if args.database
            else audit_database_directory(DEFAULT_DATABASE_DIRECTORY)
        )
    except ValueError as error:
        print(f"DATABASE_AUDIT_FAILED error={error}")
        return 2

    if not results:
        print("NO_DATABASE_FILES directory=database")
        return 0
    for result in results:
        _print_result(result)
    return 0 if all(result.healthy for result in results) else 2


def _print_result(result: DatabaseAuditResult) -> None:
    table_count = len(result.table_counts)
    row_count = sum(count for _, count in result.table_counts)
    object_summary = ",".join(
        f"{kind}:{sum(item.object_type == kind for item in result.schema_objects)}"
        for kind in ("table", "index", "view", "trigger")
    )
    print(
        "DATABASE_AUDIT "
        f"database={result.relative_path} healthy={result.healthy} "
        f"size_bytes={result.size_bytes} modified_at={result.modified_at.isoformat(timespec='seconds')} "
        f"integrity={','.join(result.integrity_check) or 'UNAVAILABLE'} "
        f"quick={','.join(result.quick_check) or 'UNAVAILABLE'} "
        f"foreign_key_violations={len(result.foreign_key_violations)} "
        f"journal_mode={result.journal_mode or 'UNAVAILABLE'} "
        f"schema_status={result.schema_status} "
        f"user_version={result.user_version if result.user_version is not None else 'UNAVAILABLE'} "
        f"application_id={result.application_id if result.application_id is not None else 'UNAVAILABLE'} "
        f"schema_version={result.schema_version if result.schema_version is not None else 'UNAVAILABLE'} "
        f"tables={table_count} rows={row_count} objects={object_summary}"
    )
    for table, count in result.table_counts:
        print(f"DATABASE_TABLE database={result.relative_path} table={table} rows={count}")
    for warning in result.warnings:
        print(f"DATABASE_WARNING database={result.relative_path} detail={warning}")
    for error in result.errors:
        print(f"DATABASE_ERROR database={result.relative_path} detail={error}")
    for violation in result.tbsz_invariant_violations:
        print(f"TBSZ_INVARIANT_ERROR database={result.relative_path} detail={violation}")


if __name__ == "__main__":
    raise SystemExit(main())
