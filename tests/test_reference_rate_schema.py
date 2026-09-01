"""Additive reference-rate schema and disposable migration contract tests."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from portfolio_advisor.database.migrations.reference_rate import (
    REFERENCE_RATE_MIGRATION_REVISION,
    ReferenceRateMigrationError,
    build_reference_rate_schema_candidate,
    reference_rate_schema_contract,
    validate_reference_rate_schema_foundation,
)
from portfolio_advisor.database.schema.v3 import (
    REFERENCE_RATE_FEATURE_FINGERPRINT,
    REFERENCE_RATE_FEATURE_ID,
    REFERENCE_RATE_FEATURE_REVISION,
    SchemaVersionError,
    connect,
    initialize_schema,
    validate_schema,
)

_TABLES = (
    "reference_rate_observation",
    "reference_rate_import_manifest",
    "reference_rate_source",
    "reference_rate_definition",
)


def _pre_reference_rate_v3(path: Path) -> None:
    with connect(path) as connection:
        initialize_schema(connection)
        for table in _TABLES:
            connection.execute(f'DROP TABLE "{table}"')
        connection.execute(
            "DELETE FROM schema_feature_contract WHERE feature_id=?",
            (REFERENCE_RATE_FEATURE_ID,),
        )
        connection.commit()
        validate_schema(connection)


def test_from_scratch_schema_has_complete_reference_rate_contract(tmp_path: Path) -> None:
    target = tmp_path / "from-scratch.sqlite"
    with connect(target) as connection:
        initialize_schema(connection)
        validate_schema(connection)
        names = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert set(_TABLES) <= names
        definition_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(reference_rate_definition)")
        }
        assert {
            "benchmark_id",
            "currency_code",
            "administrator",
            "series_identifier",
            "rate_units",
            "day_count_convention",
            "compounding_convention",
            "definition_version",
            "definition_fingerprint",
        } <= definition_columns
        observation_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(reference_rate_observation)")
        }
        assert {
            "observation_date",
            "publication_date",
            "rate_decimal",
            "provider_revision_id",
            "revision_sequence",
            "supersedes_observation_id",
            "is_current",
            "quality_status",
            "observation_fingerprint",
        } <= observation_columns
        marker = connection.execute(
            "SELECT * FROM schema_feature_contract WHERE feature_id=?",
            (REFERENCE_RATE_FEATURE_ID,),
        ).fetchone()
        assert tuple(marker) == (
            REFERENCE_RATE_FEATURE_ID,
            REFERENCE_RATE_FEATURE_REVISION,
            REFERENCE_RATE_FEATURE_FINGERPRINT,
        )
        assert sum(
            int(connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0])
            for table in _TABLES
        ) == 0


def test_absent_feature_remains_backward_compatible_but_partial_feature_fails(
    tmp_path: Path,
) -> None:
    target = tmp_path / "pre-feature.sqlite"
    _pre_reference_rate_v3(target)
    with connect(target) as connection:
        validate_schema(connection)
        connection.execute(
            "CREATE TABLE reference_rate_definition(reference_rate_definition_id INTEGER PRIMARY KEY)"
        )
        connection.commit()
        with pytest.raises(SchemaVersionError, match="partially installed"):
            validate_schema(connection)


def test_reference_rate_foreign_keys_and_current_revision_uniqueness(tmp_path: Path) -> None:
    target = tmp_path / "constraints.sqlite"
    with connect(target) as connection:
        initialize_schema(connection)
        connection.execute(
            """INSERT INTO reference_rate_definition VALUES
               (1, 1, 'ESTR', '€STR', 'EUR', 'European Central Bank',
                'EST.B.EU000A2X2A25.WT', 'PERCENT_PER_ANNUM', 'ACT_360',
                'OFFICIAL_OVERNIGHT_DAILY_COMPOUNDING', '1.0.0', ?)""",
            ("a" * 64,),
        )
        connection.execute(
            """INSERT INTO reference_rate_definition VALUES
               (2, 1, 'ESTR', '€STR', 'EUR', 'European Central Bank',
                'EST.B.EU000A2X2A25.WT', 'PERCENT_PER_ANNUM', 'ACT_360',
                'OFFICIAL_OVERNIGHT_DAILY_COMPOUNDING', '2.0.0', ?)""",
            ("9" * 64,),
        )
        connection.execute(
            """INSERT INTO reference_rate_source VALUES
               (1, 1, 'ECB_DATA_API_ESTR', 'European Central Bank',
                'https://www.ecb.europa.eu/estr', 'https://data-api.ecb.europa.eu/estr',
                'CSV', 'OFFICIAL_ADMINISTRATOR', 'NONE', 'PERMITTED', 'ECB reuse',
                'PERMITTED', ?)""",
            ("b" * 64,),
        )
        connection.execute(
            """INSERT INTO reference_rate_import_manifest VALUES
               (1, 1, 1, '2026-09-01T12:00:00Z', 'https://data-api.ecb.europa.eu/estr',
                '{}', 'text/csv', 200, 'data/raw/reference_rates/estr.csv', ?,
                '2026-09-01', 'VALIDATED_ADMITTED', ?)""",
            ("c" * 64, "d" * 64),
        )
        connection.execute(
            """INSERT INTO reference_rate_observation VALUES
               (1, 1, 1, 1, '2026-08-31', '2026-09-01', '2.188', 'STANDARD',
                1, NULL, 1, 'ADMITTED_VALIDATED', ?)""",
            ("e" * 64,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO reference_rate_observation VALUES
                   (2, 1, 1, 1, '2026-08-31', '2026-09-01', '2.189', 'REVISED',
                    2, 1, 1, 'ADMITTED_VALIDATED', ?)""",
                ("f" * 64,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "DELETE FROM reference_rate_import_manifest WHERE reference_rate_import_manifest_id=1"
            )


def test_disposable_migration_preserves_all_preexisting_content(tmp_path: Path) -> None:
    source = tmp_path / "pre-reference.sqlite"
    candidate = tmp_path / "candidate.sqlite"
    _pre_reference_rate_v3(source)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    result = build_reference_rate_schema_candidate(source=source, candidate=candidate)
    assert result.migration_revision == REFERENCE_RATE_MIGRATION_REVISION
    assert result.source_sha256 == before
    assert result.source_logical_fingerprint == result.candidate_base_logical_fingerprint
    assert result.feature_contract_fingerprint == REFERENCE_RATE_FEATURE_FINGERPRINT
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    with connect(candidate) as migrated, sqlite3.connect(":memory:") as scratch:
        scratch.row_factory = sqlite3.Row
        initialize_schema(scratch)
        assert reference_rate_schema_contract(migrated) == reference_rate_schema_contract(scratch)
        assert migrated.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert migrated.execute("PRAGMA foreign_key_check").fetchall() == []


def test_read_only_foundation_audit_is_deterministic_and_requires_empty_rows(
    tmp_path: Path,
) -> None:
    target = tmp_path / "foundation.sqlite"
    with connect(target) as connection:
        initialize_schema(connection)
    first = validate_reference_rate_schema_foundation(target)
    second = validate_reference_rate_schema_foundation(target)
    assert first == second
    assert first["status"] == "PASS"
    assert first["reference_rate_row_counts"] == {
        table: 0 for table in sorted(_TABLES)
    }

    with connect(target) as connection:
        connection.execute(
            """INSERT INTO reference_rate_definition VALUES
               (1, 1, 'ESTR', '€STR', 'EUR', 'European Central Bank',
                'EST.B.EU000A2X2A25.WT', 'PERCENT_PER_ANNUM', 'ACT_360',
                'OFFICIAL_OVERNIGHT_DAILY_COMPOUNDING', '1.0.0', ?)""",
            ("a" * 64,),
        )
        connection.commit()
    with pytest.raises(ReferenceRateMigrationError, match="zero evidence rows"):
        validate_reference_rate_schema_foundation(target)


def test_candidate_builder_rejects_installed_or_existing_target(tmp_path: Path) -> None:
    installed = tmp_path / "installed.sqlite"
    with connect(installed) as connection:
        initialize_schema(connection)
    with pytest.raises(ReferenceRateMigrationError, match="already installed"):
        build_reference_rate_schema_candidate(
            source=installed,
            candidate=tmp_path / "candidate.sqlite",
        )
    source = tmp_path / "source.sqlite"
    _pre_reference_rate_v3(source)
    existing = tmp_path / "existing.sqlite"
    existing.write_bytes(b"occupied")
    with pytest.raises(ReferenceRateMigrationError, match="already exists"):
        build_reference_rate_schema_candidate(source=source, candidate=existing)
