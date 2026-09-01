"""Additive schema and disposable migration contract tests for Milestone 11B."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from portfolio_advisor.database.migrations.constructed_portfolio import (
    MIGRATION_REVISION,
    ConstructedPortfolioMigrationError,
    build_constructed_portfolio_schema_candidate,
    constructed_schema_contract,
)
from portfolio_advisor.database.schema.v3 import (
    CONSTRUCTED_PORTFOLIO_FEATURE_FINGERPRINT,
    CONSTRUCTED_PORTFOLIO_FEATURE_ID,
    CONSTRUCTED_PORTFOLIO_FEATURE_REVISION,
    SchemaVersionError,
    connect,
    initialize_schema,
    validate_schema,
)


def _pre_11b_v3(path: Path) -> None:
    with connect(path) as connection:
        initialize_schema(connection)
        connection.execute("DROP TABLE reference_rate_observation")
        connection.execute("DROP TABLE reference_rate_import_manifest")
        connection.execute("DROP TABLE reference_rate_source")
        connection.execute("DROP TABLE reference_rate_definition")
        connection.execute("DROP TABLE constructed_portfolio_holding_lineage")
        connection.execute("DROP TABLE constructed_portfolio_metadata")
        connection.execute("DROP TABLE schema_feature_contract")
        connection.commit()


def test_from_scratch_schema_has_required_tables_columns_checks_and_foreign_keys(
    tmp_path: Path,
) -> None:
    target = tmp_path / "from-scratch.sqlite"
    with connect(target) as connection:
        initialize_schema(connection)
        validate_schema(connection)
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(constructed_portfolio_metadata)")
        }
        assert {
            "portfolio_snapshot_id",
            "shortlist_snapshot_id",
            "objective_code",
            "construction_policy_id",
            "construction_policy_version",
            "construction_policy_fingerprint",
            "construction_strategy",
            "cash_currency",
            "portfolio_identity_fingerprint",
            "eligible_universe_fingerprint",
            "selected_universe_fingerprint",
            "candidate_fingerprint",
            "construction_status",
            "deterministic_provenance_json",
        } <= columns
        lineage_columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(constructed_portfolio_holding_lineage)"
            )
        }
        assert lineage_columns == {
            "portfolio_holding_id",
            "shortlist_entry_id",
            "selected_instrument_rank",
            "allocation_basis",
            "allocation_weight_decimal",
            "constraint_evidence_fingerprint",
        }
        foreign_keys = {
            (str(row[2]), str(row[3]), str(row[4]), str(row[6]))
            for table in (
                "constructed_portfolio_metadata",
                "constructed_portfolio_holding_lineage",
            )
            for row in connection.execute(f"PRAGMA foreign_key_list({table})")
        }
        assert (
            "portfolio_snapshot",
            "portfolio_snapshot_id",
            "portfolio_snapshot_id",
            "NO ACTION",
        ) in foreign_keys
        assert (
            "portfolio_holding",
            "portfolio_holding_id",
            "portfolio_holding_id",
            "CASCADE",
        ) in foreign_keys
        marker = connection.execute(
            "SELECT * FROM schema_feature_contract WHERE feature_id=?",
            (CONSTRUCTED_PORTFOLIO_FEATURE_ID,),
        ).fetchone()
        assert tuple(marker) == (
            CONSTRUCTED_PORTFOLIO_FEATURE_ID,
            CONSTRUCTED_PORTFOLIO_FEATURE_REVISION,
            CONSTRUCTED_PORTFOLIO_FEATURE_FINGERPRINT,
        )


def test_missing_or_incompatible_constructed_schema_fails_closed(tmp_path: Path) -> None:
    target = tmp_path / "invalid.sqlite"
    with connect(target) as connection:
        initialize_schema(connection)
        connection.execute(
            "UPDATE schema_feature_contract SET revision=2 WHERE feature_id=?",
            (CONSTRUCTED_PORTFOLIO_FEATURE_ID,),
        )
        connection.commit()
        with pytest.raises(SchemaVersionError, match="marker"):
            validate_schema(connection)
    second = tmp_path / "missing.sqlite"
    with connect(second) as connection:
        initialize_schema(connection)
        connection.execute("DROP TABLE constructed_portfolio_holding_lineage")
        connection.commit()
        with pytest.raises(SchemaVersionError, match="missing tables"):
            validate_schema(connection)


def test_disposable_candidate_preserves_base_content_and_matches_from_scratch_schema(
    tmp_path: Path,
) -> None:
    source = tmp_path / "pre-11b.sqlite"
    candidate = tmp_path / "candidate.sqlite"
    _pre_11b_v3(source)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    result = build_constructed_portfolio_schema_candidate(source=source, candidate=candidate)
    assert result.migration_revision == MIGRATION_REVISION
    assert result.source_sha256 == before
    assert result.source_logical_fingerprint == result.candidate_base_logical_fingerprint
    assert result.feature_contract_fingerprint == CONSTRUCTED_PORTFOLIO_FEATURE_FINGERPRINT
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    with connect(candidate) as migrated, sqlite3.connect(":memory:") as scratch:
        scratch.row_factory = sqlite3.Row
        initialize_schema(scratch)
        assert constructed_schema_contract(migrated) == constructed_schema_contract(scratch)
        assert migrated.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert migrated.execute("PRAGMA foreign_key_check").fetchall() == []
        assert migrated.execute("SELECT count(*) FROM constructed_portfolio_metadata").fetchone()[
            0
        ] == 0


def test_candidate_builder_rejects_existing_or_same_target(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite"
    _pre_11b_v3(source)
    with pytest.raises(ConstructedPortfolioMigrationError, match="differ"):
        build_constructed_portfolio_schema_candidate(source=source, candidate=source)
    existing = tmp_path / "existing.sqlite"
    existing.write_bytes(b"occupied")
    with pytest.raises(ConstructedPortfolioMigrationError, match="already exists"):
        build_constructed_portfolio_schema_candidate(source=source, candidate=existing)
