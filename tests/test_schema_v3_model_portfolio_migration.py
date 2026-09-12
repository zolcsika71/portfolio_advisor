"""Temporary-database tests for the retained-data-safe model migration dry run."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from portfolio_advisor.database.migrations import model_portfolio_dry_run as migration
from portfolio_advisor.database.migrations.model_portfolio_dry_run import (
    CutoverNotAuthorized,
    ModelPortfolioMigrationError,
    SchemaV3ModelPortfolioRepository,
    dry_run_model_portfolio_to_v3,
    execute_model_portfolio_cutover,
)
from portfolio_advisor.database.migrations.model_portfolio_parallel import (
    ParallelBuildError,
    build_parallel_database,
    dry_run_parallel_build,
    validate_parallel_database,
)
from portfolio_advisor.database.repository import ModelPortfolioRepository
from scripts import audit_schema_v3_model_portfolio_migration_dry_run as dry_run_script


def _legacy_database(path: Path) -> None:
    columns = (
        '"Date" TEXT, "Portfolio Name" TEXT, "Product" TEXT, "ISIN" TEXT, '
        '"Allocation (%)" REAL, "Asset Class" TEXT, "Currency" TEXT, "Currency Risk" TEXT, '
        '"1 Year" REAL, "1Y Sharpe Ratio" REAL, "1Y Volatility" REAL, '
        '"Downside Risk" REAL, "Maximum Drawdown" REAL'
    )
    with sqlite3.connect(path) as connection:
        connection.execute(f"CREATE TABLE model_portfolios ({columns})")
        connection.executemany(
            "INSERT INTO model_portfolios VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("2024/09/17", "PB Konzervatív USD", "PIMCO GIS INCOME FUND E USD CAP", "IE00B7KFL990", 11.5, "Bond", "USD", "Unhedged", 0.10373, 0.09, 0.05831, None, -0.15057),
                ("2024/09/17", "PB Konzervatív USD", "PIMCO GIS INCOME FUND E USD CAP", "IE00B7KFL990", 6.0, "Bond", "USD", "Unhedged", 0.10373, 0.09, 0.05831, None, -0.15057),
            ],
        )


def _audit_payload() -> dict[str, Any]:
    rows = []
    for source_row, allocation in ((33, "11.5"), (35, "6")):
        rows.append({
            "source_type": "MODEL_XLS", "status": "AUDITED", "file": "PB_20240917.xls",
            "file_sha256": "a" * 64, "sheet": "modell portfóliók", "snapshot_date": "2024-09-17",
            "source_row": source_row, "isin": "IE00B7KFL990", "product_name": "PIMCO GIS INCOME FUND E USD CAP",
            "normalized_product_name": "pimco gis income fund e usd cap", "portfolio_name": "PB Konzervatív USD",
            "currency": "USD", "asset_class": "Bond", "sub_asset_class": "Global", "allocation": allocation,
            "source_values": {"Portfolio Name": "PB Konzervatív USD", "Product": "PIMCO GIS INCOME FUND E USD CAP", "ISIN": "IE00B7KFL990", "Allocation (%)": allocation},
        })
    return {"files": [{"source_type": "MODEL_XLS", "status": "AUDITED", "file": "PB_20240917.xls", "snapshot_date": "2024-09-17", "identity_records": rows}]}


def _rules_path() -> Path:
    return Path(__file__).parents[1] / "data/knowledge/validated_rules/capital_preservation_ranking.yaml"


def test_dry_run_preserves_duplicate_occurrences_and_matches_authoritative_ranking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "database"
    database.mkdir()
    legacy = database / "model_portfolio.sqlite"
    workbooks = tmp_path / "workbooks"
    workbooks.mkdir()
    _legacy_database(legacy)
    before = hashlib.sha256(legacy.read_bytes()).hexdigest()
    monkeypatch.setattr(migration, "audit_workbooks", lambda _path: _audit_payload())
    destination = database / "dry_runs" / "temporary-v3.sqlite"

    result = dry_run_model_portfolio_to_v3(
        legacy_path=legacy,
        workbook_directory=workbooks,
        destination_path=destination,
        rules_path=_rules_path(),
        required_destination_directory=database / "dry_runs",
    )

    assert hashlib.sha256(legacy.read_bytes()).hexdigest() == before
    assert result.counts["portfolio_holding_source_occurrence"] == 2
    assert result.counts["portfolio_holding"] == 0
    assert result.duplicate_occurrences["count"] == 2
    assert {row["semantic_status"] for row in result.duplicate_occurrences["rows"]} == {"UNRESOLVED_DUPLICATE_SEMANTICS"}
    assert result.equivalence_by_date["2024-09-17"]["exact"] is True
    assert SchemaV3ModelPortfolioRepository(destination).load_holdings(date(2024, 9, 17)) == ModelPortfolioRepository(legacy).load_holdings(date(2024, 9, 17))
    with sqlite3.connect(f"file:{destination.resolve()}?mode=ro", uri=True) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_legacy_repository_is_opened_read_only(tmp_path: Path) -> None:
    legacy = tmp_path / "model_portfolio.sqlite"
    _legacy_database(legacy)
    with ModelPortfolioRepository(legacy)._connection() as connection, pytest.raises(sqlite3.OperationalError):
        connection.execute("INSERT INTO model_portfolios VALUES (NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)")


def test_dry_run_rejects_retained_destination_and_cutover(tmp_path: Path) -> None:
    database = tmp_path / "database"
    database.mkdir()
    legacy = database / "model_portfolio.sqlite"
    _legacy_database(legacy)
    source_before = legacy.read_bytes()
    production = database / "portfolio_advisor.sqlite"
    production.write_bytes(b"preserved production database")
    backup = database / "backups" / "retained-snapshot.sqlite"
    backup.parent.mkdir()
    backup.write_bytes(b"preserved backup database")
    dry_runs = database / "dry_runs"
    protected_destinations = (legacy, production, backup)

    for destination in protected_destinations:
        with pytest.raises(ModelPortfolioMigrationError):
            migration.validate_dry_run_destination(
                legacy,
                destination,
                required_destination_directory=dry_runs,
            )
    with pytest.raises(ModelPortfolioMigrationError):
        dry_run_model_portfolio_to_v3(
            legacy_path=legacy,
            workbook_directory=tmp_path,
            destination_path=dry_runs / "portfolio_advisor.sqlite",
            rules_path=_rules_path(),
            required_destination_directory=dry_runs,
        )
    with pytest.raises(ModelPortfolioMigrationError, match="database/dry_runs"):
        migration.validate_dry_run_destination(legacy, database / "other" / "candidate.sqlite")
    assert legacy.read_bytes() == source_before
    assert production.read_bytes() == b"preserved production database"
    assert backup.read_bytes() == b"preserved backup database"
    assert not dry_runs.exists()
    assert not (database / "other").exists()
    with pytest.raises(CutoverNotAuthorized):
        execute_model_portfolio_cutover()


def test_dry_run_allows_distinct_database_dry_runs_subtree(tmp_path: Path) -> None:
    database = tmp_path / "database"
    database.mkdir()
    legacy = database / "model_portfolio.sqlite"
    _legacy_database(legacy)
    destination = database / "dry_runs" / "candidate.sqlite"

    validated = migration.validate_dry_run_destination(
        legacy,
        destination,
        required_destination_directory=database / "dry_runs",
    )

    assert validated == destination.resolve()
    assert not destination.parent.exists()
    assert not destination.exists()


def test_dry_run_cli_rejects_destination_outside_project_database_directory(
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit) as error:
        dry_run_script.main(["--destination", str(tmp_path / "outside.sqlite")])

    assert error.value.code == 2


def test_dry_run_cli_rejects_symlinked_dry_run_root_before_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    database = project / "database"
    database.mkdir(parents=True)
    source = database / "model_portfolio.sqlite"
    source.write_bytes(b"preserved source evidence")
    outside = tmp_path / "outside"
    outside.mkdir()
    (database / "dry_runs").symlink_to(outside, target_is_directory=True)
    destination = database / "dry_runs" / "escaped.sqlite"
    output = project / "audit.json"
    migration_called = False

    def fail_if_called(**_arguments: object) -> None:
        nonlocal migration_called
        migration_called = True
        pytest.fail("destination validation must run before migration")

    monkeypatch.setattr(dry_run_script, "PROJECT_ROOT", project, raising=False)
    monkeypatch.setattr(dry_run_script, "dry_run_model_portfolio_to_v3", fail_if_called)
    monkeypatch.chdir(project)

    with pytest.raises(SystemExit) as error:
        dry_run_script.main(["--destination", str(destination), "--output", str(output)])

    assert error.value.code == 2
    assert migration_called is False
    assert source.read_bytes() == b"preserved source evidence"
    assert not (outside / "escaped.sqlite").exists()
    assert not output.exists()


def test_dry_run_cli_rejects_symlinked_database_root_before_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside_database = tmp_path / "outside-database"
    dry_runs = outside_database / "dry_runs"
    dry_runs.mkdir(parents=True)
    (project / "database").symlink_to(outside_database, target_is_directory=True)
    destination = project / "database/dry_runs/escaped.sqlite"
    output = project / "audit.json"
    migration_called = False

    def fail_if_called(**_arguments: object) -> None:
        nonlocal migration_called
        migration_called = True
        pytest.fail("destination validation must run before migration")

    monkeypatch.setattr(dry_run_script, "PROJECT_ROOT", project)
    monkeypatch.setattr(dry_run_script, "dry_run_model_portfolio_to_v3", fail_if_called)
    monkeypatch.chdir(project)

    with pytest.raises(SystemExit) as error:
        dry_run_script.main(["--destination", str(destination), "--output", str(output)])

    assert error.value.code == 2
    assert migration_called is False
    assert not (dry_runs / "escaped.sqlite").exists()
    assert not output.exists()


def test_dry_run_rejects_traversal_and_nested_symlink_escape_without_writes(
    tmp_path: Path,
) -> None:
    database = tmp_path / "database"
    dry_runs = database / "dry_runs"
    dry_runs.mkdir(parents=True)
    source = database / "model_portfolio.sqlite"
    source.write_bytes(b"preserved source evidence")
    outside = tmp_path / "outside"
    outside.mkdir()
    (dry_runs / "escape").symlink_to(outside, target_is_directory=True)
    destinations = (
        dry_runs / ".." / "traversal.sqlite",
        dry_runs / "escape" / "symlink.sqlite",
    )

    for destination in destinations:
        with pytest.raises(ModelPortfolioMigrationError, match="database/dry_runs"):
            migration.validate_dry_run_destination(
                source,
                destination,
                required_destination_directory=dry_runs,
            )

    assert source.read_bytes() == b"preserved source evidence"
    assert not (database / "traversal.sqlite").exists()
    assert not (outside / "symlink.sqlite").exists()


def test_reconciliation_fails_closed_on_ambiguous_source_provenance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    legacy = tmp_path / "model_portfolio.sqlite"
    _legacy_database(legacy)
    payload = _audit_payload()
    payload["files"][0]["identity_records"].append(dict(payload["files"][0]["identity_records"][0], source_row=34))
    monkeypatch.setattr(migration, "audit_workbooks", lambda _path: payload)
    with pytest.raises(ModelPortfolioMigrationError, match="not unambiguously reconciled"):
        migration.reconcile_legacy_to_workbook(legacy, tmp_path)


def test_failed_migration_rolls_back_destination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    legacy = tmp_path / "model_portfolio.sqlite"
    _legacy_database(legacy)
    payload = _audit_payload()
    payload["files"][0]["identity_records"][0]["file_sha256"] = "not-a-sha"
    monkeypatch.setattr(migration, "audit_workbooks", lambda _path: payload)
    destination = tmp_path / "temporary-v3.sqlite"
    with pytest.raises(sqlite3.IntegrityError):
        dry_run_model_portfolio_to_v3(legacy_path=legacy, workbook_directory=tmp_path, destination_path=destination, rules_path=_rules_path())
    assert not destination.exists()


def test_repeat_runs_are_deterministic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    legacy = tmp_path / "model_portfolio.sqlite"
    workbooks = tmp_path / "workbooks"
    workbooks.mkdir()
    _legacy_database(legacy)
    monkeypatch.setattr(migration, "audit_workbooks", lambda _path: _audit_payload())
    first = dry_run_model_portfolio_to_v3(legacy_path=legacy, workbook_directory=workbooks, destination_path=tmp_path / "one.sqlite", rules_path=_rules_path())
    second = dry_run_model_portfolio_to_v3(legacy_path=legacy, workbook_directory=workbooks, destination_path=tmp_path / "two.sqlite", rules_path=_rules_path())
    assert first.counts == second.counts
    assert first.destination_fingerprint == second.destination_fingerprint
    assert first.equivalence_by_date == second.equivalence_by_date


def test_parallel_build_is_explicit_atomic_and_preserves_source_occurrences(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy = tmp_path / "model_portfolio.sqlite"
    workbooks = tmp_path / "workbooks"
    workbooks.mkdir()
    _legacy_database(legacy)
    before = hashlib.sha256(legacy.read_bytes()).hexdigest()
    monkeypatch.setattr(migration, "audit_workbooks", lambda _path: _audit_payload())
    dry = dry_run_parallel_build(legacy_path=legacy, workbook_directory=workbooks, rules_path=_rules_path())
    baseline = tmp_path / "dry-run.json"
    baseline.write_text(json.dumps({"source_fingerprints": dry.source_fingerprints, "migrated_counts": dry.counts, "duplicate_occurrence_results": dry.duplicate_occurrences}))
    target = tmp_path / "parallel.sqlite"
    assert not target.exists()
    result = build_parallel_database(legacy_path=legacy, workbook_directory=workbooks, target_path=target, rules_path=_rules_path(), audited_dry_run_path=baseline)
    assert result.published and target.exists() and result.counts["portfolio_holding"] == 0
    assert result.duplicate_occurrences["count"] == 2
    assert hashlib.sha256(legacy.read_bytes()).hexdigest() == before
    repeat = build_parallel_database(legacy_path=legacy, workbook_directory=workbooks, target_path=target, rules_path=_rules_path(), audited_dry_run_path=baseline)
    assert not repeat.published and repeat.build_status == "PARALLEL_VALIDATED"


def test_parallel_validation_rejects_stale_audit_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    legacy = tmp_path / "model_portfolio.sqlite"
    workbooks = tmp_path / "workbooks"
    workbooks.mkdir()
    _legacy_database(legacy)
    monkeypatch.setattr(migration, "audit_workbooks", lambda _path: _audit_payload())
    dry = dry_run_parallel_build(legacy_path=legacy, workbook_directory=workbooks, rules_path=_rules_path())
    baseline = tmp_path / "dry-run.json"
    baseline.write_text(json.dumps({"source_fingerprints": dry.source_fingerprints, "migrated_counts": dry.counts, "duplicate_occurrence_results": dry.duplicate_occurrences}))
    target = tmp_path / "parallel.sqlite"
    build_parallel_database(legacy_path=legacy, workbook_directory=workbooks, target_path=target, rules_path=_rules_path(), audited_dry_run_path=baseline)
    baseline.write_text(json.dumps({"source_fingerprints": {}, "migrated_counts": dry.counts, "duplicate_occurrence_results": dry.duplicate_occurrences}))
    with pytest.raises(ParallelBuildError, match="re-audit"):
        validate_parallel_database(legacy_path=legacy, workbook_directory=workbooks, target_path=target, rules_path=_rules_path(), audited_dry_run_path=baseline)
