"""Synthetic-only tests for the atomic Phase 3B.1 writer contract."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

import portfolio_advisor.database.model_portfolio_phase3b as phase3b
from portfolio_advisor.database.model_portfolio_phase3b import (
    ModelPortfolioPhase3BError,
    admit_synthetic_workbook,
    initialize_synthetic_writer_database,
    install_phase3b1_schema,
    validate_phase3b1_contracts,
)
from portfolio_advisor.database.schema.v3 import initialize_schema
from tests.fixtures.model_portfolio_phase3b_fixture import (
    request_for,
    write_synthetic_workbook,
)


def test_atomic_dual_sheet_admission_receipt_and_outbox(tmp_path: Path) -> None:
    database = _database(tmp_path)
    workbook = write_synthetic_workbook(tmp_path, snapshot_date="2026-01-15")
    result = admit_synthetic_workbook(
        database, request_for(database, workbook, admission_id="SYNTHETIC_2026_01_15")
    )

    assert result.replayed is False
    assert (
        result.model_item_count,
        result.shortlist_item_count,
        result.outbox_item_count,
    ) == (2, 2, 2)
    retained = (
        tmp_path
        / "phase3b-retained-evidence"
        / "workbooks"
        / f"{hashlib.sha256(workbook.read_bytes()).hexdigest()}.json"
    )
    assert retained.read_bytes() == workbook.read_bytes()
    with sqlite3.connect(database) as connection:
        assert _counts(connection) == (1, 2, 2, 2, 2, 2)
        assert connection.execute(
            "SELECT DISTINCT source_semantics_status FROM portfolio_holding_source_occurrence"
        ).fetchall() == [("UNRESOLVED_DUPLICATE_SEMANTICS",)]
        assert (
            connection.execute("SELECT count(*) FROM portfolio_holding").fetchone()[0]
            == 0
        )
        assert connection.execute(
            "SELECT work_type,state FROM model_workbook_writer_outbox ORDER BY work_type"
        ).fetchall() == [
            ("ARTIFACT_REFRESH", "PENDING"),
            ("FILE_FINALIZATION", "PENDING"),
        ]
        assert (
            connection.execute(
                "SELECT count(*) FROM portfolio_holding_source_occurrence "
                "WHERE json_extract(source_payload_json,'$.YTD')='0'"
            ).fetchone()[0]
            == 2
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM instrument_metric_observation "
                "WHERE source_reference LIKE 'MODEL_PHASE3B1:%:YTD'"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM instrument_metric_observation "
                "WHERE source_reference LIKE 'SHORTLIST:%:YTD' AND value=0.0"
            ).fetchone()[0]
            == 2
        )
        validate_phase3b1_contracts(connection)


def test_exact_replay_is_byte_stable_and_changed_binding_rejects(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    workbook = write_synthetic_workbook(tmp_path, snapshot_date="2026-01-15")
    request = request_for(database, workbook, admission_id="SYNTHETIC_2026_01_15")
    admit_synthetic_workbook(database, request)
    before = _sha256(database)

    replay = admit_synthetic_workbook(database, request)
    assert replay.replayed is True
    assert _sha256(database) == before

    mismatched = replace(request, authorization_reference="different-authorization")
    with pytest.raises(ModelPortfolioPhase3BError, match="replay bindings"):
        admit_synthetic_workbook(database, mismatched)
    assert _sha256(database) == before


def test_changed_same_date_rejects_without_mutation(tmp_path: Path) -> None:
    database = _database(tmp_path)
    original = write_synthetic_workbook(tmp_path, snapshot_date="2026-01-15")
    admit_synthetic_workbook(
        database, request_for(database, original, admission_id="SYNTHETIC_2026_01_15")
    )
    changed = write_synthetic_workbook(
        tmp_path,
        snapshot_date="2026-01-15",
        suffix="-changed",
        changed_product=True,
    )
    changed_request = request_for(database, changed, admission_id="CHANGED_2026_01_15")
    before = _sha256(database)
    with pytest.raises(ModelPortfolioPhase3BError, match="snapshot date"):
        admit_synthetic_workbook(database, changed_request)
    assert _sha256(database) == before


def test_either_sheet_failure_and_injected_failures_roll_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = _database(tmp_path)
    invalid = write_synthetic_workbook(
        tmp_path, snapshot_date="2026-01-15", invalid_shortlist=True
    )
    before = _sha256(database)
    with pytest.raises(ModelPortfolioPhase3BError, match="invalid ISIN"):
        admit_synthetic_workbook(
            database, request_for(database, invalid, admission_id="INVALID_SHORTLIST")
        )
    assert _sha256(database) == before

    workbook = write_synthetic_workbook(tmp_path, snapshot_date="2026-01-16")
    request = request_for(database, workbook, admission_id="INJECTED_FAILURE")

    def interrupt(point: str, _connection: sqlite3.Connection) -> None:
        if point == "after_model_sheet":
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        admit_synthetic_workbook(database, request, _failure_hook=interrupt)
    assert _sha256(database) == before

    original_validator = phase3b._validate_phase3b1_records
    validation_calls = 0

    def fail_validation(_connection: sqlite3.Connection) -> None:
        nonlocal validation_calls
        validation_calls += 1
        if validation_calls == 2:
            raise ModelPortfolioPhase3BError("injected final validation failure")
        original_validator(_connection)

    monkeypatch.setattr(phase3b, "_validate_phase3b1_records", fail_validation)
    with pytest.raises(ModelPortfolioPhase3BError, match="injected final"):
        admit_synthetic_workbook(database, request)
    monkeypatch.setattr(phase3b, "_validate_phase3b1_records", original_validator)
    assert validation_calls == 2
    assert _sha256(database) == before
    with sqlite3.connect(database) as connection:
        assert _counts(connection) == (0, 0, 0, 0, 0, 0)


def test_uncommitted_rows_are_invisible_to_other_connections(tmp_path: Path) -> None:
    database = _database(tmp_path)
    workbook = write_synthetic_workbook(tmp_path, snapshot_date="2026-01-15")
    observations: list[tuple[int, int]] = []

    def observe(point: str, _connection: sqlite3.Connection) -> None:
        if point == "after_shortlist_sheet":
            with sqlite3.connect(
                f"file:{database.resolve()}?mode=ro", uri=True
            ) as reader:
                observations.append(
                    (
                        int(
                            reader.execute(
                                "SELECT count(*) FROM source_file"
                            ).fetchone()[0]
                        ),
                        int(
                            reader.execute(
                                "SELECT count(*) FROM model_workbook_writer_admission"
                            ).fetchone()[0]
                        ),
                    )
                )

    admit_synthetic_workbook(
        database,
        request_for(database, workbook, admission_id="VISIBILITY_TEST"),
        _failure_hook=observe,
    )
    assert observations == [(0, 0)]


def test_successive_dates_preserve_history_and_corrections_fail_closed(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    first = write_synthetic_workbook(tmp_path, snapshot_date="2026-01-15")
    admit_synthetic_workbook(
        database, request_for(database, first, admission_id="FIRST")
    )
    with sqlite3.connect(database) as connection:
        first_payloads = connection.execute(
            "SELECT source_payload_json FROM portfolio_holding_source_occurrence ORDER BY 1"
        ).fetchall()

    second = write_synthetic_workbook(tmp_path, snapshot_date="2026-02-15")
    admit_synthetic_workbook(
        database, request_for(database, second, admission_id="SECOND")
    )
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT admission_id,predecessor_admission_id FROM model_workbook_writer_admission ORDER BY admission_sequence"
        ).fetchall() == [("FIRST", None), ("SECOND", "FIRST")]
        assert (
            connection.execute(
                "SELECT source_payload_json FROM portfolio_holding_source_occurrence ORDER BY portfolio_holding_source_occurrence_id LIMIT 2"
            ).fetchall()
            == first_payloads
        )
        connection.execute(
            "CREATE TABLE shortlist_synthetic_correction_guard(value TEXT)"
        )
        connection.execute(
            "INSERT INTO shortlist_synthetic_correction_guard VALUES('unchanged')"
        )

    third = write_synthetic_workbook(tmp_path, snapshot_date="2026-03-15")
    before = _sha256(database)
    with pytest.raises(ModelPortfolioPhase3BError, match="cannot inherit"):
        admit_synthetic_workbook(
            database, request_for(database, third, admission_id="THIRD")
        )
    assert _sha256(database) == before
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT * FROM shortlist_synthetic_correction_guard"
        ).fetchall() == [("unchanged",)]
        assert (
            connection.execute(
                "SELECT count(*) FROM model_workbook_writer_admission"
            ).fetchone()[0]
            == 2
        )


def test_concurrent_exact_submissions_do_not_duplicate(tmp_path: Path) -> None:
    database = _database(tmp_path)
    workbook = write_synthetic_workbook(tmp_path, snapshot_date="2026-01-15")
    request = request_for(database, workbook, admission_id="CONCURRENT")
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _unused: admit_synthetic_workbook(database, request), range(2)
            )
        )
    assert sorted(result.replayed for result in results) == [False, True]
    with sqlite3.connect(database) as connection:
        assert _counts(connection) == (1, 2, 2, 2, 2, 2)


def test_historical_manifest_blocks_new_append_without_mutation(tmp_path: Path) -> None:
    database = _database(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """INSERT INTO migration_build_manifest(
                   singleton,schema_version,build_version,source_fingerprints_json,
                   ranking_policy_sha256,source_counts_json,target_counts_json,
                   unresolved_semantic_count,equivalence_status,dataset_fingerprint,
                   database_fingerprint,build_status)
               VALUES(1,3,'SYNTHETIC_BASELINE','{}',?,'{}','{}',0,
                      'EXACT_PASS',?,NULL,'PARALLEL_VALIDATED')""",
            ("a" * 64, "b" * 64),
        )
    workbook = write_synthetic_workbook(tmp_path, snapshot_date="2026-01-15")
    before = _sha256(database)
    with pytest.raises(ModelPortfolioPhase3BError, match="cannot inherit"):
        admit_synthetic_workbook(
            database,
            request_for(database, workbook, admission_id="BLOCKED_BY_MANIFEST"),
        )
    assert _sha256(database) == before
    with sqlite3.connect(database) as connection:
        assert _counts(connection) == (0, 0, 0, 0, 0, 0)


def test_response_lost_after_commit_retries_as_exact_replay(tmp_path: Path) -> None:
    database = _database(tmp_path)
    workbook = write_synthetic_workbook(tmp_path, snapshot_date="2026-01-15")
    request = request_for(database, workbook, admission_id="LOST_RESPONSE")

    def lose_response() -> None:
        raise ConnectionError("synthetic response lost")

    with pytest.raises(ConnectionError, match="response lost"):
        admit_synthetic_workbook(database, request, _after_commit_hook=lose_response)
    assert admit_synthetic_workbook(database, request).replayed is True


def test_validator_rejects_changed_metric_binding(tmp_path: Path) -> None:
    database = _database(tmp_path)
    workbook = write_synthetic_workbook(tmp_path, snapshot_date="2026-01-15")
    admit_synthetic_workbook(
        database, request_for(database, workbook, admission_id="METRIC_BINDING")
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "DELETE FROM instrument_metric_observation "
            "WHERE instrument_metric_observation_id=("
            "SELECT instrument_metric_observation_id "
            "FROM instrument_metric_observation "
            "WHERE source_reference LIKE 'MODEL_PHASE3B1:%' LIMIT 1)"
        )
        connection.commit()
        with pytest.raises(ModelPortfolioPhase3BError, match="metric binding"):
            validate_phase3b1_contracts(connection)


def test_paths_reject_symlink_parent_escape_and_hard_link(tmp_path: Path) -> None:
    database = _database(tmp_path)
    workbook = write_synthetic_workbook(tmp_path, snapshot_date="2026-01-15")
    request = request_for(database, workbook, admission_id="PATH_TEST")

    database_symlink = tmp_path / "database-link.sqlite"
    database_symlink.symlink_to(database)
    with pytest.raises(ModelPortfolioPhase3BError, match="ordinary file"):
        admit_synthetic_workbook(database_symlink, request)

    hard_link = tmp_path / "database-hard-link.sqlite"
    os.link(database, hard_link)
    with pytest.raises(ModelPortfolioPhase3BError, match="hard links"):
        admit_synthetic_workbook(database, request)
    hard_link.unlink()

    outside = Path.home() / f".phase3b-target-{os.getpid()}-{tmp_path.name}.sqlite"
    with pytest.raises(ModelPortfolioPhase3BError, match="system temporary"):
        initialize_synthetic_writer_database(outside)

    escaped_parent = tmp_path / "escaped-parent"
    escaped_parent.symlink_to(Path.home(), target_is_directory=True)
    with pytest.raises(ModelPortfolioPhase3BError, match="system temporary"):
        initialize_synthetic_writer_database(escaped_parent / "database.sqlite")

    retention_link = tmp_path / "retention-link"
    (tmp_path / "retention-target").mkdir()
    retention_link.symlink_to(tmp_path / "retention-target", target_is_directory=True)
    with pytest.raises(ModelPortfolioPhase3BError, match="retention root"):
        admit_synthetic_workbook(
            database, replace(request, retention_root=retention_link)
        )

    with sqlite3.connect(":memory:") as memory:
        initialize_schema(memory)
        with pytest.raises(ModelPortfolioPhase3BError, match="temporary file database"):
            install_phase3b1_schema(memory)


def _database(tmp_path: Path) -> Path:
    database = tmp_path / "phase3b.sqlite"
    initialize_synthetic_writer_database(database)
    return database


def _counts(connection: sqlite3.Connection) -> tuple[int, int, int, int, int, int]:
    return tuple(
        int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
        for table in (
            "model_workbook_writer_admission",
            "model_workbook_writer_sheet",
            "model_workbook_writer_model_item",
            "model_workbook_writer_shortlist_item",
            "model_workbook_writer_outbox",
            "source_sheet",
        )
    )  # type: ignore[return-value]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
