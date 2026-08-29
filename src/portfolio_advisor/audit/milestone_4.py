"""Deterministic, read-only inventory for Milestone 4 source evidence.

The module deliberately inventories evidence; it does not create identities,
resolve aliases, alter SQLite files, or import workbook rows.
"""

from __future__ import annotations

import hashlib
import math
import re
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path
from typing import Any, Final

import pandas as pd
from python_calamine import load_workbook

from portfolio_advisor.database.repository import (
    HoldingObservation,
    ModelPortfolioRepository,
)
from portfolio_advisor.DB_creation.excel_processing import (
    HEADER_TRANSLATIONS,
    fill_merged_cells,
    is_visible_sheet,
)
from portfolio_advisor.DB_creation.text_normalization import normalized_key
from portfolio_advisor.metrics.portfolio import calculate_all_portfolio_metrics
from portfolio_advisor.ranking.config import load_ranking_rules
from portfolio_advisor.ranking.ranking import rank_portfolios

REPORT_SCHEMA_VERSION: Final = 2
DATABASE_FILENAMES: Final = (
    "model_portfolio.sqlite",
    "official_historical_nav.sqlite",
    "prospective_portfolio_validation.sqlite",
    "tbsz_portfolio.sqlite",
    "tbsz_current_portfolio.sqlite",
)
_WORKBOOK_SUFFIXES: Final = frozenset({".xls", ".xlsx", ".xlsm", ".xlsb"})
_DATE_FROM_FILENAME: Final = re.compile(r"(?<!\d)(\d{8})(?!\d)")
_ISIN: Final = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_PRODUCT_HEADERS: Final = frozenset({"product", "termék", "security name", "instrument name"})
_ISIN_HEADERS: Final = frozenset({"isin"})
_PORTFOLIO_HEADERS: Final = frozenset({"portfólió neve", "portfolio name"})
_CURRENCY_HEADERS: Final = frozenset({"deviza", "currency"})
_ASSET_CLASS_HEADERS: Final = frozenset({"eszközosztály", "asset class"})
_SUB_ASSET_CLASS_HEADERS: Final = frozenset({"aleszközosztály", "sub-asset class"})
_ALLOCATION_HEADERS: Final = frozenset({"hányad (%)", "allocation (%)"})

_DATABASE_ROLES: Final = {
    "model_portfolio.sqlite": {
        "ownership": "APPLICATION_OWNED_SOURCE_DERIVED",
        "provenance_role": "AUTHORITATIVE_LEGACY_MODEL_PORTFOLIO_COMPATIBILITY_SOURCE",
        "schema_owner": "portfolio_advisor.DB_creation.database_create and portfolio_advisor.history.mnb_otc",
    },
    "official_historical_nav.sqlite": {
        "ownership": "APPLICATION_OWNED_EVIDENCE_STORE",
        "provenance_role": "OFFICIAL_HISTORICAL_NAV_EVIDENCE",
        "schema_owner": "portfolio_advisor.history.official_nav_store.OfficialNavStore",
    },
    "prospective_portfolio_validation.sqlite": {
        "ownership": "APPLICATION_OWNED_APPEND_ONLY_LEDGER",
        "provenance_role": "PROSPECTIVE_DECISION_AND_OUTCOME_EVIDENCE",
        "schema_owner": "portfolio_advisor.prospective.validation.ProspectiveValidationStore",
    },
    "tbsz_portfolio.sqlite": {
        "ownership": "APPLICATION_OWNED_PRIVATE_LEDGER",
        "provenance_role": "PRIVATE_LTIA_SOURCE_EVIDENCE_LEGACY_NAME",
        "schema_owner": "portfolio_advisor.tbsz.repository.TbszPortfolioRepository",
    },
    "tbsz_current_portfolio.sqlite": {
        "ownership": "APPLICATION_OWNED_PRIVATE_READ_MODEL",
        "provenance_role": "DERIVED_CURRENT_LTIA_PROJECTION_LEGACY_NAME",
        "schema_owner": "portfolio_advisor.tbsz.current_standings",
    },
}


def is_valid_isin(value: object) -> bool:
    """Validate the ISO 6166 structure and Luhn check digit without lookup."""
    isin = str(value).strip().upper()
    if _ISIN.fullmatch(isin) is None:
        return False
    expanded = "".join(str(ord(character) - 55) if character.isalpha() else character for character in isin)
    total = 0
    for offset, character in enumerate(reversed(expanded)):
        digit = int(character)
        if offset % 2 == 1:
            digit *= 2
            digit = digit - 9 if digit > 9 else digit
        total += digit
    return total % 10 == 0


def normalize_isin(value: object) -> str | None:
    """Return a valid explicit ISIN, otherwise preserve unresolved status as ``None``."""
    candidate = str(value).strip().upper()
    return candidate if is_valid_isin(candidate) else None


def audit_milestone_4(
    *, database_directory: Path, workbook_directory: Path
) -> dict[str, Any]:
    """Audit configured sources only; returned data contains no execution timestamp."""
    workbook_audit = audit_workbooks(workbook_directory)
    project_sqlite = _project_sqlite_inventory(database_directory.parent, database_directory)
    duplicate_adjudication = audit_duplicate_holdings(
        workbook_audit,
        database_path=database_directory / "model_portfolio.sqlite",
        rules_path=database_directory.parent / "data/knowledge/validated_rules/capital_preservation_ranking.yaml",
    )
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "read_only_contract": {
            "existing_databases_opened_mode": "ro",
            "workbooks_modified": False,
            "identity_auto_resolution": "PROHIBITED",
            "cash_identity": "CURRENCY_PLUS_AMOUNT_NOT_INSTRUMENT",
        },
        "database_inventory": audit_databases(database_directory),
        "additional_project_sqlite_inventory": project_sqlite,
        "xls_inventory": workbook_audit,
        "canonical_instrument_registry_seed": {
            "source_rule": "UNION_OF_VALID_EXPLICIT_ISINS_IN_MODEL_XLS_AND_SHORTLIST_XLS",
            "valid_isin_count": len(workbook_audit["valid_isins"]),
            "valid_isins": workbook_audit["valid_isins"],
            "unresolved_source_rows": workbook_audit["summary"]["unresolved_identity_rows"],
            "identity_conflicts": workbook_audit["identity_conflicts"],
        },
        "duplicate_holding_adjudication": duplicate_adjudication,
        "ltia_reconciliation": audit_ltia_reconciliation(
            database_directory / "tbsz_portfolio.sqlite",
            database_directory / "tbsz_current_portfolio.sqlite",
        ),
    }


def audit_databases(database_directory: Path) -> list[dict[str, Any]]:
    """Inventory required databases plus every discovered SQLite file under ``database/``."""
    required = [database_directory / name for name in DATABASE_FILENAMES]
    discovered = sorted(
        path for path in database_directory.rglob("*")
        if path.is_file() and not path.is_symlink() and path.suffix.casefold() in {".sqlite", ".db"}
    ) if database_directory.is_dir() else []
    paths = [*required, *(path for path in discovered if path not in required)]
    return [_audit_database(path, database_directory) for path in paths]


def _audit_database(path: Path, database_root: Path | None = None) -> dict[str, Any]:
    database = _relative_database_name(path, database_root)
    role = _database_role(path, database)
    base: dict[str, Any] = {"database": database, **role}
    if not path.is_file() or path.is_symlink():
        return {**base, "status": "MISSING_OR_NOT_REGULAR_FILE"}
    base["sha256"] = _sha256(path)
    try:
        connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    except sqlite3.Error as error:
        return {**base, "status": "OPEN_FAILED", "error": str(error)}
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {
            **base,
            "status": "AUDITED",
            "pragma": {
                "user_version": _pragma_scalar(connection, "user_version"),
                "application_id": _pragma_scalar(connection, "application_id"),
                "schema_version": _pragma_scalar(connection, "schema_version"),
                "integrity_check": _pragma_values(connection, "integrity_check"),
                "quick_check": _pragma_values(connection, "quick_check"),
                "foreign_key_check": [
                    {"table": str(row[0]), "rowid": row[1], "parent_table": str(row[2]), "foreign_key_id": int(row[3])}
                    for row in connection.execute("PRAGMA foreign_key_check")
                ],
            },
            "schema_objects": [
                {"name": str(row[0]), "type": str(row[1]), "sql": row[2]}
                for row in connection.execute(
                    "SELECT name, type, sql FROM sqlite_master "
                    "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
                )
            ],
            "tables": [_table_inventory(connection, table) for table in tables],
        }
    except sqlite3.Error as error:
        return {**base, "status": "AUDIT_FAILED", "error": str(error)}
    finally:
        connection.close()


def _project_sqlite_inventory(project_root: Path, database_root: Path) -> list[dict[str, Any]]:
    """Record top-level project SQLite artifacts outside ``database/`` explicitly."""
    if not project_root.is_dir():
        return []
    return [
        _audit_database(path, project_root)
        for path in sorted(project_root.iterdir())
        if path.is_file()
        and not path.is_symlink()
        and path.suffix.casefold() in {".sqlite", ".db"}
        and path.resolve().parent != database_root.resolve()
    ]


def _relative_database_name(path: Path, root: Path | None) -> str:
    if root is None:
        return path.name
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def _database_role(path: Path, database: str) -> dict[str, str]:
    if database.startswith("backups/tbsz_portfolio-"):
        return {
            "ownership": "LOCAL_ONLY_MIGRATION_BACKUP",
            "provenance_role": "HISTORICAL_PRIVATE_LTIA_EVIDENCE_BACKUP_LEGACY_NAME",
            "schema_owner": "portfolio_advisor.tbsz.repository.TbszPortfolioRepository",
        }
    return _DATABASE_ROLES.get(path.name, {
        "ownership": "UNCLASSIFIED_LOCAL_SQLITE",
        "provenance_role": "UNCLASSIFIED_NOT_A_MILESTONE_5_MIGRATION_SOURCE",
        "schema_owner": "NOT_ESTABLISHED",
    })


def _table_inventory(connection: sqlite3.Connection, table: str) -> dict[str, Any]:
    quoted = _quote_identifier(table)
    columns = [
        {
            "position": int(row[0]), "name": str(row[1]), "declared_type": str(row[2]),
            "not_null": bool(row[3]), "default": row[4], "primary_key_position": int(row[5]),
        }
        for row in connection.execute(f"PRAGMA table_info({quoted})")
    ]
    foreign_keys = [
        {
            "id": int(row[0]), "sequence": int(row[1]), "parent_table": str(row[2]),
            "from_column": str(row[3]), "to_column": str(row[4]),
            "on_update": str(row[5]), "on_delete": str(row[6]), "match": str(row[7]),
        }
        for row in connection.execute(f"PRAGMA foreign_key_list({quoted})")
    ]
    indexes = []
    for index in connection.execute(f"PRAGMA index_list({quoted})"):
        name = str(index[1])
        indexes.append({
            "name": name, "unique": bool(index[2]), "origin": str(index[3]), "partial": bool(index[4]),
            "columns": [
                {"sequence": int(row[0]), "column": row[2], "key": bool(row[5])}
                for row in connection.execute(f"PRAGMA index_xinfo({_quote_identifier(name)})")
            ],
        })
    schema_sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()[0]
    row_count = int(connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0])
    return {
        "name": table, "row_count": row_count, "columns": columns,
        "foreign_keys": foreign_keys, "indexes": indexes, "schema_sql": schema_sql,
    }


def audit_workbooks(workbook_directory: Path) -> dict[str, Any]:
    """Inspect both targeted workbook sheets without invoking the importer."""
    files = _workbook_files(workbook_directory)
    sheets: list[dict[str, Any]] = []
    workbooks: list[dict[str, Any]] = []
    file_failures: list[dict[str, str]] = []
    for path in files:
        try:
            workbook_inventory, target_sheets = _audit_workbook(path)
            workbooks.append(workbook_inventory)
            sheets.extend(target_sheets)
        except (OSError, ValueError, RuntimeError) as error:
            file_failures.append({"file": path.name, "status": "WORKBOOK_UNREADABLE", "detail": str(error)})

    targeted = [item for item in sheets if item["source_type"] in {"MODEL_XLS", "SHORTLIST_XLS"}]
    records = [record for item in targeted for record in item["identity_records"]]
    valid_isins = sorted({str(record["isin"]) for record in records if record["isin"] is not None})
    schema_groups = _schema_groups(targeted)
    identity_conflicts = _identity_conflicts(records)
    metadata_conflicts = _metadata_conflicts(records)
    summary = {
        "workbook_count": len(files), "target_sheet_count": len(targeted),
        "model_sheet_count": sum(item["source_type"] == "MODEL_XLS" for item in targeted),
        "shortlist_sheet_count": sum(item["source_type"] == "SHORTLIST_XLS" for item in targeted),
        "data_rows": sum(item["data_row_count"] for item in targeted),
        "valid_explicit_isin_rows": sum(item["valid_explicit_isin_rows"] for item in targeted),
        "product_name_rows": sum(item["product_name_rows"] for item in targeted),
        "currency_rows": sum(item["currency"] is not None for item in records),
        "currencies": sorted({str(item["currency"]) for item in records if item["currency"] is not None}),
        "distinct_normalized_product_names": len({
            str(record["normalized_product_name"])
            for record in records if record["normalized_product_name"] is not None
        }),
        "unresolved_identity_rows": sum(item["unresolved_identity_rows"] for item in targeted),
        "malformed_rows": sum(item["malformed_rows"] for item in targeted),
        "duplicate_rows": sum(item["duplicate_rows"] for item in targeted),
    }
    return {
        "root": str(workbook_directory),
        "workbooks": sorted(workbooks, key=lambda item: item["file"]),
        "files": sorted(sheets, key=lambda item: (item["file"], item["sheet"])),
        "file_failures": file_failures, "summary": summary, "valid_isins": valid_isins,
        "source_schema_changes": schema_groups, "identity_conflicts": identity_conflicts,
        "metadata_conflicts": metadata_conflicts,
    }


def _workbook_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    if not root.is_dir():
        raise ValueError(f"workbook root is not a directory: {root}")
    return sorted(path for path in root.rglob("*") if path.is_file() and not path.is_symlink() and path.suffix.casefold() in _WORKBOOK_SUFFIXES)


def _audit_workbook(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    workbook = load_workbook(path)
    file_sha256 = _sha256(path)
    snapshot_date = _snapshot_date(path.name)
    results: list[dict[str, Any]] = []
    sheet_inventory = [
        {"name": str(metadata.name), "visible": is_visible_sheet(metadata)}
        for metadata in workbook.sheets_metadata
    ]
    with pd.ExcelFile(path, engine="calamine") as excel_file:
        for index, metadata in enumerate(workbook.sheets_metadata):
            normalized_sheet = normalized_key(metadata.name)
            source_type = {"modell portfóliók": "MODEL_XLS", "shortlist": "SHORTLIST_XLS"}.get(normalized_sheet)
            if source_type is None or not is_visible_sheet(metadata):
                continue
            sheet = workbook.get_sheet_by_index(index)
            frame = pd.read_excel(excel_file, sheet_name=metadata.name, dtype=object, header=None)
            frame = fill_merged_cells(frame, sheet.merged_cell_ranges)
            frame = frame.dropna(axis=1, how="all").dropna(how="all").reset_index(drop=True)
            results.append(_audit_sheet(
                frame, file=path.name, file_sha256=file_sha256, snapshot_date=snapshot_date,
                sheet=str(metadata.name), source_type=source_type,
            ))
    return ({
        "file": path.name, "file_sha256": file_sha256, "snapshot_date": snapshot_date,
        "sheets": sheet_inventory,
    }, results)


def _audit_sheet(
    frame: pd.DataFrame, *, file: str, file_sha256: str, snapshot_date: str | None,
    sheet: str, source_type: str,
) -> dict[str, Any]:
    header_index = _find_header_row(frame)
    if header_index is None:
        return {
            "file": file, "file_sha256": file_sha256, "snapshot_date": snapshot_date, "sheet": sheet,
            "source_type": source_type, "status": "HEADER_UNRESOLVED", "headers": [],
            "data_row_count": 0, "valid_explicit_isin_rows": 0, "unresolved_identity_rows": 0,
            "malformed_rows": 0, "duplicate_rows": 0, "identity_records": [],
        }
    headers = [_cell_text(value) for value in frame.iloc[header_index].tolist()]
    normalized_headers = [normalized_key(value) if value else "" for value in headers]
    isin_column = _first_column(normalized_headers, _ISIN_HEADERS)
    product_column = _first_column(normalized_headers, _PRODUCT_HEADERS)
    portfolio_column = _first_column(normalized_headers, _PORTFOLIO_HEADERS)
    currency_column = _first_column(normalized_headers, _CURRENCY_HEADERS)
    asset_class_column = _first_column(normalized_headers, _ASSET_CLASS_HEADERS)
    sub_asset_class_column = _first_column(normalized_headers, _SUB_ASSET_CLASS_HEADERS)
    allocation_column = _first_column(normalized_headers, _ALLOCATION_HEADERS)
    records: list[dict[str, Any]] = []
    malformed = 0
    for row_number, values in enumerate(frame.iloc[header_index + 1:].itertuples(index=False, name=None), start=header_index + 2):
        if not any(_cell_text(value) for value in values):
            continue
        raw_isin = _cell_text(values[isin_column]) if isin_column is not None and isin_column < len(values) else ""
        product = _cell_text(values[product_column]) if product_column is not None and product_column < len(values) else ""
        portfolio_name = _cell_text(values[portfolio_column]) if portfolio_column is not None and portfolio_column < len(values) else ""
        currency = _cell_text(values[currency_column]) if currency_column is not None and currency_column < len(values) else ""
        asset_class = _cell_text(values[asset_class_column]) if asset_class_column is not None and asset_class_column < len(values) else ""
        sub_asset_class = _cell_text(values[sub_asset_class_column]) if sub_asset_class_column is not None and sub_asset_class_column < len(values) else ""
        allocation = _cell_text(values[allocation_column]) if allocation_column is not None and allocation_column < len(values) else ""
        isin = normalize_isin(raw_isin) if raw_isin else None
        reason = None
        if not raw_isin:
            reason = "MISSING_EXPLICIT_ISIN"
        elif isin is None:
            reason = "INVALID_EXPLICIT_ISIN"
        elif not product:
            reason = "MISSING_PRODUCT_NAME"
        if reason is not None:
            malformed += 1
        records.append({
            "source_type": source_type, "file": file, "sheet": sheet, "snapshot_date": snapshot_date,
            "source_row": row_number, "isin": isin, "raw_isin": raw_isin or None,
            "product_name": product or None, "normalized_product_name": normalized_key(product) if product else None,
            "portfolio_name": portfolio_name or None,
            "currency": currency or None, "asset_class": asset_class or None,
            "sub_asset_class": sub_asset_class or None,
            "allocation": allocation or None,
            "source_values": _source_values(headers, values),
            "identity_status": "EXPLICIT_ISIN_VALID" if isin else "IDENTITY_UNRESOLVED",
            "identity_reason": reason,
        })
    duplicate_rows = _mark_duplicates(records)
    return {
        "file": file, "file_sha256": file_sha256, "snapshot_date": snapshot_date, "sheet": sheet,
        "source_type": source_type, "status": "AUDITED", "header_row": header_index + 1,
        "headers": headers, "normalized_headers": normalized_headers,
        "header_signature": "|".join(normalized_headers),
        "data_row_count": len(records), "valid_explicit_isin_rows": sum(item["isin"] is not None for item in records),
        "product_name_rows": sum(item["product_name"] is not None for item in records),
        "unresolved_identity_rows": sum(item["isin"] is None for item in records),
        "malformed_rows": malformed, "duplicate_rows": duplicate_rows, "identity_records": records,
    }


def _find_header_row(frame: pd.DataFrame) -> int | None:
    best: tuple[int, int] | None = None
    known = set(HEADER_TRANSLATIONS) | _PRODUCT_HEADERS | _ISIN_HEADERS
    for index, values in enumerate(frame.itertuples(index=False, name=None)):
        matches = sum(normalized_key(value) in known for value in values if _cell_text(value))
        if best is None or matches > best[1]:
            best = (index, matches)
    return best[0] if best is not None and best[1] >= 2 else None


def _first_column(headers: list[str], accepted: frozenset[str]) -> int | None:
    return next((index for index, header in enumerate(headers) if header in accepted), None)


def _mark_duplicates(records: list[dict[str, Any]]) -> int:
    keys: Counter[tuple[str, str, str]] = Counter()
    for record in records:
        key = _duplicate_key(record)
        if key != ("", "", ""):
            keys[key] += 1
    duplicate_keys = {key for key, count in keys.items() if count > 1}
    for record in records:
        key = _duplicate_key(record)
        record["duplicate_within_source_sheet"] = key in duplicate_keys
    return sum(record["duplicate_within_source_sheet"] for record in records)


def _duplicate_key(record: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(record["portfolio_name"] or ""), str(record["isin"] or ""),
        str(record["normalized_product_name"] or ""),
    )


def _source_values(headers: list[str], values: tuple[object, ...]) -> dict[str, str | None]:
    """Capture every source cell using displayed headers and no inferred values."""
    observed: dict[str, str | None] = {}
    for position, value in enumerate(values):
        header = headers[position] if position < len(headers) and headers[position] else f"COLUMN_{position + 1}"
        key = header if header not in observed else f"{header}__{position + 1}"
        text = _cell_text(value)
        observed[key] = text or None
    return observed


def _schema_groups(sheets: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in sheets:
        groups[(str(item["source_type"]), str(item.get("header_signature", "")))].append(item)
    return [
        {
            "source_type": source_type, "header_signature": signature,
            "headers": items[0].get("headers", []), "file_count": len(items),
            "files": sorted(item["file"] for item in items),
            "snapshot_dates": sorted({item["snapshot_date"] for item in items if item["snapshot_date"] is not None}),
        }
        for (source_type, signature), items in sorted(groups.items())
    ]


def _identity_conflicts(records: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    names: dict[str, set[str]] = defaultdict(set)
    isins: dict[str, set[str]] = defaultdict(set)
    for record in records:
        isin = record["isin"]
        name = record["normalized_product_name"]
        if isin and name:
            names[str(name)].add(str(isin))
            isins[str(isin)].add(str(record["product_name"]))
    return {
        "same_normalized_name_multiple_isins": [
            {"normalized_product_name": name, "isins": sorted(values)}
            for name, values in sorted(names.items()) if len(values) > 1
        ],
        "same_isin_multiple_product_names": [
            {"isin": isin, "product_names": sorted(values)}
            for isin, values in sorted(isins.items()) if len(values) > 1
        ],
    }


def _metadata_conflicts(records: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Retain conflicting source metadata as warnings, never select a winner."""
    fields = ("currency", "asset_class", "sub_asset_class")
    values: dict[str, dict[str, set[str]]] = {
        field: defaultdict(set) for field in fields
    }
    for record in records:
        isin = record["isin"]
        if isin is None:
            continue
        for field in fields:
            value = record[field]
            if value is not None:
                values[field][str(isin)].add(str(value))
    return {
        field: [
            {"isin": isin, "values": sorted(observed_values)}
            for isin, observed_values in sorted(by_isin.items()) if len(observed_values) > 1
        ]
        for field, by_isin in values.items()
    }


def audit_duplicate_holdings(
    workbook_audit: dict[str, Any], *, database_path: Path, rules_path: Path
) -> dict[str, Any]:
    """Adjudicate duplicate model source rows and calculate diagnostics only.

    The result preserves every source occurrence. It does not decide whether a
    source occurrence is an intended sleeve, a duplicate, or an import error.
    """
    records = [
        record
        for sheet in workbook_audit["files"]
        if sheet["source_type"] == "MODEL_XLS"
        for record in sheet["identity_records"]
        if record.get("duplicate_within_source_sheet")
    ]
    groups: dict[tuple[str, str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[(
            str(record["file"]), str(record["sheet"]), str(record["snapshot_date"]),
            str(record["portfolio_name"]), str(record["isin"]), str(record["normalized_product_name"]),
        )].append(record)
    adjudications = [
        _adjudicate_duplicate_group(group)
        for _, group in sorted(groups.items())
    ]
    return {
        "status": "AUDITED",
        "source_evidence_rule": "EVERY_SOURCE_OCCURRENCE_IS_RETAINED",
        "groups": adjudications,
        "financial_impact_diagnostics": _duplicate_financial_impact(
            adjudications, database_path=database_path, rules_path=rules_path,
        ),
    }


def _adjudicate_duplicate_group(records: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(records, key=lambda item: int(item["source_row"]))
    source_values = [item["source_values"] for item in ordered]
    differing = _non_empty_field_differences(source_values)
    field_identical = all(item == source_values[0] for item in source_values[1:])
    if field_identical:
        classification = "EXACT_DUPLICATE_SOURCE_ROWS"
        semantics = "SOURCE_OCCURRENCES_EQUAL_BUT_BUSINESS_SEMANTICS_STILL_REQUIRE_REVIEW"
    elif set(differing) == {"Hányad (%)"} or set(differing) == {"Allocation (%)"}:
        classification = "DISTINCT_SOURCE_ROWS_FOR_SAME_INSTRUMENT"
        semantics = "UNRESOLVED_DUPLICATE_SEMANTICS"
    else:
        classification = "CONFLICTING_DUPLICATE_ROWS"
        semantics = "UNRESOLVED_DUPLICATE_SEMANTICS"
    first = ordered[0]
    return {
        "classification": classification,
        "semantic_status": semantics,
        "requires_human_approval": True,
        "source": {
            "workbook": first["file"], "sheet": first["sheet"],
            "snapshot_date": first["snapshot_date"], "portfolio_name": first["portfolio_name"],
            "isin": first["isin"], "displayed_product_name": first["product_name"],
        },
        "source_rows": [
            {
                "source_row": item["source_row"], "currency": item["currency"],
                "asset_class": item["asset_class"], "sub_asset_class": item["sub_asset_class"],
                "allocation": item["allocation"], "all_source_fields": item["source_values"],
            }
            for item in ordered
        ],
        "field_for_field_identical": field_identical,
        "non_empty_source_field_differences": differing,
    }


def _non_empty_field_differences(source_values: list[dict[str, str | None]]) -> dict[str, list[str]]:
    fields = sorted({field for values in source_values for field in values})
    differences: dict[str, list[str]] = {}
    for field in fields:
        values = sorted({str(value) for source in source_values if (value := source.get(field)) is not None})
        if len(values) > 1:
            differences[field] = values
    return differences


def _duplicate_financial_impact(
    adjudications: list[dict[str, Any]], *, database_path: Path, rules_path: Path
) -> dict[str, Any]:
    """Run scenario comparisons in memory against the unchanged active policy."""
    if not database_path.is_file() or not rules_path.is_file():
        return {"status": "UNAVAILABLE_REQUIRED_DIAGNOSTIC_INPUT_MISSING"}
    snapshot_dates = {str(group["source"]["snapshot_date"]) for group in adjudications}
    if len(snapshot_dates) != 1:
        return {"status": "UNAVAILABLE_DUPLICATE_GROUPS_DO_NOT_SHARE_ONE_SNAPSHOT"}
    try:
        snapshot_date = date.fromisoformat(next(iter(snapshot_dates)))
    except ValueError:
        return {"status": "UNAVAILABLE_INVALID_SNAPSHOT_DATE"}
    holdings = ModelPortfolioRepository(database_path).load_holdings(snapshot_date)
    rules = load_ranking_rules(rules_path)
    scenarios = {
        "LEGACY_BEHAVIOR": holdings,
        "RETAIN_EACH_SOURCE_OCCURRENCE": list(holdings),
        "DEDUPLICATE_EXACT_ROWS_ONLY": _deduplicate_exact_rows_only(holdings, adjudications),
        "AGGREGATE_BY_PORTFOLIO_SNAPSHOT_AND_ISIN": _aggregate_duplicate_rows(holdings, adjudications),
    }
    results = {name: _scenario_result(candidate_holdings, rules) for name, candidate_holdings in scenarios.items()}
    legacy = results["LEGACY_BEHAVIOR"]
    return {
        "status": "DIAGNOSTIC_ONLY",
        "policy": {"name": rules.policy_name, "version": rules.version},
        "snapshot_date": snapshot_date.isoformat(),
        "scenarios": results,
        "comparison_to_legacy": {
            name: _compare_scenario_to_legacy(legacy, result)
            for name, result in results.items() if name != "LEGACY_BEHAVIOR"
        },
    }


def _deduplicate_exact_rows_only(
    holdings: list[HoldingObservation], adjudications: list[dict[str, Any]]
) -> list[HoldingObservation]:
    """No row is removed unless the source group is field-for-field identical."""
    exact = [item for item in adjudications if item["classification"] == "EXACT_DUPLICATE_SOURCE_ROWS"]
    if not exact:
        return list(holdings)
    # This audit has no exact groups. A future exact-group implementation must
    # use source-occurrence IDs; matching only a business key would be unsafe.
    raise ValueError("exact-row source-occurrence de-duplication requires a source-row-to-holding mapping")


def _aggregate_duplicate_rows(
    holdings: list[HoldingObservation], adjudications: list[dict[str, Any]]
) -> list[HoldingObservation]:
    """Aggregate only diagnostic groups whose non-allocation fields agree."""
    result = list(holdings)
    for group in adjudications:
        if group["classification"] != "DISTINCT_SOURCE_ROWS_FOR_SAME_INSTRUMENT":
            raise ValueError("aggregation is unavailable for exact or conflicting duplicate source rows")
        source = group["source"]
        matches = [
            item for item in result
            if item.portfolio_name == source["portfolio_name"]
            and item.isin == source["isin"]
            and item.product == source["displayed_product_name"]
        ]
        if len(matches) != len(group["source_rows"]):
            raise ValueError("source duplicate group cannot be matched exactly to legacy holdings")
        if any(item.allocation is None for item in matches):
            raise ValueError("duplicate aggregation requires source-supported allocations")
        aggregate = replace(matches[0], allocation=sum(item.allocation or 0.0 for item in matches))
        match_ids = {id(item) for item in matches}
        result = [item for item in result if id(item) not in match_ids]
        result.append(aggregate)
    return result


def _scenario_result(holdings: list[HoldingObservation], rules: Any) -> dict[str, Any]:
    metrics = calculate_all_portfolio_metrics(holdings)
    ranking, warnings = rank_portfolios(metrics, rules)
    return {
        "candidate_count": len(metrics),
        "metrics": [asdict(item) for item in metrics],
        "ranking": [asdict(item) for item in ranking],
        "rank_order": [item.metrics.portfolio_name for item in ranking if item.rank is not None],
        "selected_winner": next((item.metrics.portfolio_name for item in ranking if item.rank == 1), None),
        "warnings": list(warnings),
    }


def _compare_scenario_to_legacy(legacy: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
    feature_comparison = _numeric_comparison(legacy["metrics"], scenario["metrics"])
    ranking_comparison = _numeric_comparison(legacy["ranking"], scenario["ranking"])
    legacy_eligibility = [
        (item["metrics"]["portfolio_name"], item["eligible"], item["rejection_reasons"])
        for item in legacy["ranking"]
    ]
    scenario_eligibility = [
        (item["metrics"]["portfolio_name"], item["eligible"], item["rejection_reasons"])
        for item in scenario["ranking"]
    ]
    return {
        "allocation_totals_and_eligibility_identical": legacy_eligibility == scenario_eligibility,
        "portfolio_feature_values_exact_serialization": feature_comparison["exact"],
        "portfolio_feature_values_within_1e_12": feature_comparison["within_tolerance"],
        "portfolio_feature_values_max_absolute_difference": feature_comparison["max_absolute_difference"],
        "normalized_values_contributions_scores_exact_serialization": ranking_comparison["exact"],
        "normalized_values_contributions_scores_within_1e_12": ranking_comparison["within_tolerance"],
        "normalized_values_contributions_scores_max_absolute_difference": ranking_comparison["max_absolute_difference"],
        "rank_order_identical": legacy["rank_order"] == scenario["rank_order"],
        "selected_winner_identical": legacy["selected_winner"] == scenario["selected_winner"],
    }


def _numeric_comparison(left: object, right: object, tolerance: float = 1e-12) -> dict[str, object]:
    """Compare nested audit payloads while exposing, rather than hiding, float drift."""
    differences: list[float] = []
    structures_match = _collect_numeric_differences(left, right, differences)
    maximum = max(differences, default=0.0)
    return {
        "exact": left == right,
        "within_tolerance": structures_match and maximum <= tolerance,
        "max_absolute_difference": maximum,
    }


def _collect_numeric_differences(left: object, right: object, differences: list[float]) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        differences.append(abs(float(left) - float(right)))
        return True
    if isinstance(left, dict) and isinstance(right, dict):
        if set(left) != set(right):
            return False
        return all(_collect_numeric_differences(left[key], right[key], differences) for key in left)
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(
            _collect_numeric_differences(first, second, differences)
            for first, second in zip(left, right, strict=True)
        )
    return left == right


def audit_ltia_reconciliation(tbsz_path: Path, current_path: Path) -> dict[str, Any]:
    """Report LTIA identity and source-equivalence blockers without reading values."""
    source = _audit_tbsz_evidence(tbsz_path)
    projection = _audit_current_projection(current_path)
    blockers = []
    if source.get("unresolved_position_count", 0):
        blockers.append("LTIA_SOURCE_POSITION_IDENTITIES_UNRESOLVED")
    if projection.get("unresolved_position_count", 0):
        blockers.append("CURRENT_LTIA_PROJECTION_IDENTITIES_UNRESOLVED")
    return {
        "terminology": "LTIA (legacy TBSZ database names retained)",
        "source_evidence": source, "current_projection": projection,
        "automatic_cross_database_reconciliation": "BLOCKED" if blockers else "ELIGIBLE_FOR_EXACT_ISIN_ONLY",
        "blockers": blockers,
        "cash_rule": "CASH_REMAINS_SEPARATE_BY_ACCOUNT_AND_CURRENCY",
    }


def _audit_tbsz_evidence(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"status": "MISSING"}
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as connection:
        identity_statuses = [
            {"identity_status": str(row[0]), "instrument_count": int(row[1])}
            for row in connection.execute("SELECT identity_status, COUNT(*) FROM instruments GROUP BY identity_status ORDER BY identity_status")
        ]
        unresolved = int(connection.execute(
            "SELECT COUNT(*) FROM position_snapshots AS p JOIN instruments AS i ON i.instrument_id = p.instrument_id WHERE i.isin IS NULL"
        ).fetchone()[0])
        position_count = int(connection.execute("SELECT COUNT(*) FROM position_snapshots").fetchone()[0])
        cash_by_currency = [
            {"currency": str(row[0]), "snapshot_count": int(row[1])}
            for row in connection.execute("SELECT currency, COUNT(*) FROM cash_snapshots GROUP BY currency ORDER BY currency")
        ]
        equivalents = [
            {"account_id": int(row[0]), "view_type": str(row[1]), "source_date": row[2], "evidence_fingerprint": str(row[3]), "source_count": int(row[4])}
            for row in connection.execute(
                "SELECT account_id, view_type, source_date, evidence_fingerprint, COUNT(*) FROM source_snapshots "
                "GROUP BY account_id, view_type, source_date, evidence_fingerprint HAVING COUNT(*) > 1 "
                "ORDER BY account_id, view_type, source_date, evidence_fingerprint"
            )
        ]
    return {
        "status": "AUDITED", "position_count": position_count, "unresolved_position_count": unresolved,
        "identity_statuses": identity_statuses, "cash_snapshots_by_currency": cash_by_currency,
        "equivalent_source_snapshot_groups": equivalents,
    }


def _audit_current_projection(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"status": "MISSING"}
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as connection:
        position_count = int(connection.execute("SELECT COUNT(*) FROM position_snapshots").fetchone()[0])
        unresolved = int(connection.execute(
            "SELECT COUNT(*) FROM position_snapshots AS p JOIN instruments AS i ON i.instrument_id = p.instrument_id WHERE i.isin IS NULL"
        ).fetchone()[0])
        cash_by_currency = [
            {"currency": str(row[0]), "current_balance_records": int(row[1])}
            for row in connection.execute("SELECT currency, COUNT(*) FROM cash_snapshots GROUP BY currency ORDER BY currency")
        ]
    return {
        "status": "AUDITED", "position_count": position_count, "unresolved_position_count": unresolved,
        "cash_by_currency": cash_by_currency,
        "projection_status": "IDENTITY_BLOCKED" if unresolved else "EXACT_ISIN_RECONCILABLE",
    }


def _snapshot_date(filename: str) -> str | None:
    match = _DATE_FROM_FILENAME.search(filename)
    if match is None:
        return None
    value = match.group(1)
    return f"{value[:4]}-{value[4:6]}-{value[6:]}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _pragma_scalar(connection: sqlite3.Connection, name: str) -> int:
    return int(connection.execute(f"PRAGMA {name}").fetchone()[0])


def _pragma_values(connection: sqlite3.Connection, name: str) -> list[str]:
    return [str(row[0]) for row in connection.execute(f"PRAGMA {name}")]


def _cell_text(value: object) -> str:
    if value is None or value is pd.NA or value is pd.NaT:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()
