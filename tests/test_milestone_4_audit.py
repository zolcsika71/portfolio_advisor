"""Focused tests for the read-only Milestone 4 inventory."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from portfolio_advisor.audit.milestone_4 import (
    _adjudicate_duplicate_group,
    _audit_sheet,
    _metadata_conflicts,
    _numeric_comparison,
    audit_databases,
    audit_ltia_reconciliation,
    is_valid_isin,
)


def test_isin_validation_accepts_only_structurally_valid_luhn_isins() -> None:
    assert is_valid_isin("US0378331005") is True
    assert is_valid_isin("US0378331006") is False
    assert is_valid_isin("CASH") is False


def test_sheet_audit_reports_duplicate_and_unresolved_identity_rows() -> None:
    frame = pd.DataFrame([
        ["ISIN", "Product"],
        ["US0378331005", "Apple Inc."],
        ["US0378331005", "Apple Inc."],
        ["INVALID", "Unknown"],
        [None, "No ISIN"],
    ])

    report = _audit_sheet(
        frame, file="PB_20240101.xls", file_sha256="a" * 64, snapshot_date="2024-01-01",
        sheet="shortlist", source_type="SHORTLIST_XLS",
    )

    assert report["valid_explicit_isin_rows"] == 2
    assert report["unresolved_identity_rows"] == 2
    assert report["duplicate_rows"] == 2
    assert report["malformed_rows"] == 2
    assert report["identity_records"][2]["identity_reason"] == "INVALID_EXPLICIT_ISIN"
    assert report["identity_records"][3]["identity_reason"] == "MISSING_EXPLICIT_ISIN"


def test_database_inventory_reports_constraints_and_roles_read_only(tmp_path: Path) -> None:
    database_directory = tmp_path / "database"
    database_directory.mkdir()
    model = database_directory / "model_portfolio.sqlite"
    with sqlite3.connect(model) as connection:
        connection.executescript(
            "CREATE TABLE parent (id INTEGER PRIMARY KEY);"
            "CREATE TABLE child (id INTEGER PRIMARY KEY, parent_id INTEGER NOT NULL REFERENCES parent(id));"
            "CREATE INDEX child_parent_index ON child(parent_id);"
        )

    report = {item["database"]: item for item in audit_databases(database_directory)}
    model_report = report["model_portfolio.sqlite"]

    assert model_report["status"] == "AUDITED"
    assert model_report["provenance_role"] == "AUTHORITATIVE_LEGACY_MODEL_PORTFOLIO_COMPATIBILITY_SOURCE"
    assert {item["name"] for item in model_report["schema_objects"]} >= {
        "parent", "child", "child_parent_index"
    }
    child = next(item for item in model_report["tables"] if item["name"] == "child")
    assert child["row_count"] == 0
    assert child["foreign_keys"][0]["parent_table"] == "parent"
    assert any(index["name"] == "child_parent_index" for index in child["indexes"])
    assert report["official_historical_nav.sqlite"]["status"] == "MISSING_OR_NOT_REGULAR_FILE"


def test_database_inventory_includes_discovered_backup_without_treating_it_as_authoritative(tmp_path: Path) -> None:
    database_directory = tmp_path / "database"
    backup_directory = database_directory / "backups"
    backup_directory.mkdir(parents=True)
    backup = backup_directory / "tbsz_portfolio-v1-before-v2.sqlite"
    with sqlite3.connect(backup) as connection:
        connection.execute("CREATE TABLE retained_evidence (id INTEGER PRIMARY KEY)")

    report = {item["database"]: item for item in audit_databases(database_directory)}

    assert report["backups/tbsz_portfolio-v1-before-v2.sqlite"]["status"] == "AUDITED"
    assert report["backups/tbsz_portfolio-v1-before-v2.sqlite"]["ownership"] == "LOCAL_ONLY_MIGRATION_BACKUP"


def test_metadata_conflicts_remain_explicit_without_resolving_them() -> None:
    report = _metadata_conflicts([
        {"isin": "US0378331005", "currency": "USD", "asset_class": "Equity", "sub_asset_class": "US"},
        {"isin": "US0378331005", "currency": "EUR", "asset_class": "Equity", "sub_asset_class": "US"},
    ])

    assert report["currency"] == [{"isin": "US0378331005", "values": ["EUR", "USD"]}]
    assert report["asset_class"] == []


def test_duplicate_adjudication_preserves_distinct_allocations_and_requires_approval() -> None:
    records = [
        {
            "file": "PB_20240917.xls", "sheet": "modell portfóliók", "snapshot_date": "2024-09-17",
            "portfolio_name": "Portfolio", "isin": "US0378331005", "product_name": "Apple",
            "source_row": 10, "currency": "USD", "asset_class": "Equity", "sub_asset_class": "US",
            "allocation": "7", "source_values": {"ISIN": "US0378331005", "Hányad (%)": "7", "1yr": "0.1"},
        },
        {
            "file": "PB_20240917.xls", "sheet": "modell portfóliók", "snapshot_date": "2024-09-17",
            "portfolio_name": "Portfolio", "isin": "US0378331005", "product_name": "Apple",
            "source_row": 12, "currency": "USD", "asset_class": "Equity", "sub_asset_class": "US",
            "allocation": "6", "source_values": {"ISIN": "US0378331005", "Hányad (%)": "6", "1yr": "0.1"},
        },
    ]

    report = _adjudicate_duplicate_group(records)

    assert report["classification"] == "DISTINCT_SOURCE_ROWS_FOR_SAME_INSTRUMENT"
    assert report["semantic_status"] == "UNRESOLVED_DUPLICATE_SEMANTICS"
    assert report["field_for_field_identical"] is False
    assert report["non_empty_source_field_differences"] == {"Hányad (%)": ["6", "7"]}
    assert report["requires_human_approval"] is True


def test_duplicate_adjudication_marks_field_for_field_equal_rows_as_exact() -> None:
    source = {"ISIN": "US0378331005", "Hányad (%)": "5", "1yr": "0.1"}
    records = [
        {
            "file": "PB_20240917.xls", "sheet": "modell portfóliók", "snapshot_date": "2024-09-17",
            "portfolio_name": "Portfolio", "isin": "US0378331005", "product_name": "Apple",
            "source_row": row, "currency": "USD", "asset_class": "Equity", "sub_asset_class": "US",
            "allocation": "5", "source_values": source,
        }
        for row in (10, 12)
    ]

    report = _adjudicate_duplicate_group(records)

    assert report["classification"] == "EXACT_DUPLICATE_SOURCE_ROWS"
    assert report["field_for_field_identical"] is True


def test_numeric_comparison_exposes_tiny_float_drift_without_calling_it_exact() -> None:
    report = _numeric_comparison({"metric": 0.1 + 0.2}, {"metric": 0.3})

    assert report["exact"] is False
    assert report["within_tolerance"] is True
    difference = report["max_absolute_difference"]
    assert isinstance(difference, float)
    assert difference > 0.0


def test_ltia_audit_blocks_reconciliation_when_legacy_evidence_has_no_isin(tmp_path: Path) -> None:
    source = tmp_path / "tbsz_portfolio.sqlite"
    current = tmp_path / "tbsz_current_portfolio.sqlite"
    with sqlite3.connect(source) as connection:
        connection.executescript(
            "CREATE TABLE instruments (instrument_id INTEGER PRIMARY KEY, isin TEXT, identity_status TEXT);"
            "CREATE TABLE position_snapshots (position_id INTEGER PRIMARY KEY, instrument_id INTEGER);"
            "CREATE TABLE cash_snapshots (cash_id INTEGER PRIMARY KEY, currency TEXT);"
            "CREATE TABLE source_snapshots (snapshot_id INTEGER PRIMARY KEY, account_id INTEGER, view_type TEXT, source_date TEXT, evidence_fingerprint TEXT);"
            "INSERT INTO instruments VALUES (1, NULL, 'IDENTITY_UNRESOLVED');"
            "INSERT INTO position_snapshots VALUES (1, 1);"
            "INSERT INTO source_snapshots VALUES (1, 1, 'POSITIONS', NULL, 'same');"
            "INSERT INTO source_snapshots VALUES (2, 1, 'POSITIONS', NULL, 'same');"
        )
    with sqlite3.connect(current) as connection:
        connection.executescript(
            "CREATE TABLE instruments (instrument_id INTEGER PRIMARY KEY, isin TEXT);"
            "CREATE TABLE position_snapshots (position_id INTEGER PRIMARY KEY, instrument_id INTEGER);"
            "CREATE TABLE cash_snapshots (cash_id INTEGER PRIMARY KEY, currency TEXT);"
            "INSERT INTO instruments VALUES (1, NULL);"
            "INSERT INTO position_snapshots VALUES (1, 1);"
        )

    report = audit_ltia_reconciliation(source, current)

    assert report["automatic_cross_database_reconciliation"] == "BLOCKED"
    assert report["source_evidence"]["equivalent_source_snapshot_groups"][0]["source_count"] == 2
    assert report["current_projection"]["projection_status"] == "IDENTITY_BLOCKED"
