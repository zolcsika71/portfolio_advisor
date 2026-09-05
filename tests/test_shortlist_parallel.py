"""Synthetic, retained-data-free checks for the shortlist occurrence stage."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from portfolio_advisor.database.migrations import shortlist_parallel as stage
from portfolio_advisor.database.schema.v3 import (
    connect,
    initialize_schema,
    upgrade_schema_v3_nav_extension,
)
from scripts.validate_schema_v3_shortlist import validate_shortlist_stage

type AuditValue = str | int | list[dict[str, AuditValue]] | dict[str, AuditValue]


def _row(row: int, isin: str, name: str) -> dict[str, AuditValue]:
    return {"isin": isin, "source_row": row, "product_name": name, "normalized_product_name": name.casefold(), "currency": "USD", "asset_class": "Equity", "sub_asset_class": "Global", "source_values": {"1yr": "0.1", "1Y Sharpe": "0.2"}}


def _sheet(
    rows: list[dict[str, AuditValue]],
    signature: str = stage.SUPPORTED_SIGNATURE,
) -> dict[str, AuditValue]:
    return {"source_type":"SHORTLIST_XLS","status":"AUDITED","header_signature":signature,"file":"fixture.xls","file_sha256":"a" * 64,"sheet":"shortlist","snapshot_date":"2024-01-01","identity_records":rows}


def _target(path: Path) -> None:
    with connect(path) as connection:
        initialize_schema(connection)
        upgrade_schema_v3_nav_extension(connection)


def test_one_and_conflicting_two_occurrences_preserve_lineage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "target.sqlite"; _target(target)
    rows = [_row(2,"US0378331005","One"), _row(3,"US5949181045","Alpha"), _row(4,"US5949181045","Beta")]
    monkeypatch.setattr(stage, "audit_workbooks", lambda _path: {"files":[_sheet(rows)]})
    result = stage.integrate_shortlist(workbook_directory=tmp_path, target=target, apply=True)
    assert result["source_occurrences"] == 3 and result["shortlist_entries"] == 2
    with sqlite3.connect(target) as connection:
        assert connection.execute("SELECT count(*) FROM shortlist_entry_lineage").fetchone()[0] == 3
        assert connection.execute("SELECT count(*) FROM shortlist_entry_source_occurrence WHERE conflict_status='SOURCE_METADATA_CONFLICT'").fetchone()[0] == 2
        assert {row[0] for row in connection.execute("SELECT observed_product_name FROM shortlist_entry_source_occurrence")} == {"One","Alpha","Beta"}


def test_unknown_signature_and_invalid_isin_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "target.sqlite"; _target(target)
    monkeypatch.setattr(stage, "audit_workbooks", lambda _path: {"files":[_sheet([_row(2,"US0378331005","One")], "unknown")]})
    with pytest.raises(stage.ShortlistIntegrationError): stage.integrate_shortlist(workbook_directory=tmp_path,target=target,apply=False)
    monkeypatch.setattr(stage, "audit_workbooks", lambda _path: {"files":[_sheet([_row(2,"INVALID","One")])]})
    with pytest.raises(stage.ShortlistIntegrationError): stage.integrate_shortlist(workbook_directory=tmp_path,target=target,apply=False)


def test_dry_run_does_not_mutate_and_replay_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target=tmp_path/"target.sqlite"; _target(target); rows=[_row(2,"US0378331005","One")]
    monkeypatch.setattr(stage,"audit_workbooks",lambda _path:{"files":[_sheet(rows)]})
    stage.integrate_shortlist(workbook_directory=tmp_path,target=target,apply=False)
    with sqlite3.connect(target) as connection: assert connection.execute("SELECT count(*) FROM shortlist_snapshot").fetchone()[0] == 0
    stage.integrate_shortlist(workbook_directory=tmp_path,target=target,apply=True); stage.integrate_shortlist(workbook_directory=tmp_path,target=target,apply=True)
    with sqlite3.connect(target) as connection: assert connection.execute("SELECT count(*) FROM shortlist_entry_source_occurrence").fetchone()[0] == 1


def test_multiple_names_and_equal_metrics_keep_separate_provenance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "target.sqlite"; _target(target)
    rows = [_row(2, "US0378331005", "First name"), _row(3, "US0378331005", "Second name")]
    monkeypatch.setattr(stage, "audit_workbooks", lambda _path: {"files":[_sheet(rows)]})
    stage.integrate_shortlist(workbook_directory=tmp_path, target=target, apply=True)
    with sqlite3.connect(target) as connection:
        assert connection.execute("SELECT count(*) FROM shortlist_entry_source_occurrence").fetchone()[0] == 2
        assert connection.execute("SELECT count(DISTINCT source_reference) FROM instrument_metric_observation WHERE source_reference LIKE 'SHORTLIST:%'").fetchone()[0] == 4


def test_failed_import_rolls_back_and_never_publishes_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "target.sqlite"; _target(target)
    before = target.read_bytes()
    rows = [_row(2, "US0378331005", "One"), _row(2, "US5949181045", "Two")]
    monkeypatch.setattr(stage, "audit_workbooks", lambda _path: {"files":[_sheet(rows)]})
    with pytest.raises(sqlite3.IntegrityError):
        stage.integrate_shortlist(workbook_directory=tmp_path, target=target, apply=True)
    assert target.read_bytes() == before


def test_no_portfolio_or_holding_is_constructed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "target.sqlite"; _target(target)
    monkeypatch.setattr(stage, "audit_workbooks", lambda _path: {"files":[_sheet([_row(2, "US0378331005", "One")])]})
    stage.integrate_shortlist(workbook_directory=tmp_path, target=target, apply=True)
    with sqlite3.connect(target) as connection:
        assert connection.execute("SELECT count(*) FROM portfolio").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM portfolio_holding").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM portfolio_cash").fetchone()[0] == 0


def _validated_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, dict[str, AuditValue]]:
    target = tmp_path / "validated.sqlite"; _target(target)
    audit: dict[str, AuditValue] = {"files":[_sheet([_row(2,"US0378331005","One"), _row(3,"US5949181045","Alpha"), _row(4,"US5949181045","Beta")])]}
    monkeypatch.setattr(stage, "audit_workbooks", lambda _path: audit)
    stage.integrate_shortlist(workbook_directory=tmp_path, target=target, apply=True)
    return target, audit


def test_validator_passes_untouched_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target, audit = _validated_target(tmp_path, monkeypatch)
    assert validate_shortlist_stage(target, audit)["occurrences"] == 3


def test_validator_rejects_same_count_payload_tampering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target, audit = _validated_target(tmp_path, monkeypatch); before = target.read_bytes()
    with sqlite3.connect(target) as connection: connection.execute("UPDATE shortlist_entry_source_occurrence SET source_payload_json='{}' WHERE source_row_number=2")
    with pytest.raises(RuntimeError, match="payload"): validate_shortlist_stage(target, audit)
    assert target.read_bytes() != before


def test_validator_rejects_source_row_tampering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target, audit = _validated_target(tmp_path, monkeypatch)
    with sqlite3.connect(target) as connection: connection.execute("UPDATE shortlist_entry_source_occurrence SET source_row_number=20 WHERE source_row_number=2")
    with pytest.raises(RuntimeError, match="payload"): validate_shortlist_stage(target, audit)


def test_validator_rejects_workbook_sha_tampering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target, audit = _validated_target(tmp_path, monkeypatch)
    files = audit["files"]
    assert isinstance(files, list)
    sheet = files[0]
    assert isinstance(sheet, dict)
    sheet["file_sha256"] = "b" * 64
    with pytest.raises(RuntimeError, match="stale manifest"): validate_shortlist_stage(target, audit)


@pytest.mark.parametrize(("sql", "message"), [
    ("UPDATE instrument_metric_observation SET value=9 WHERE source_reference LIKE 'SHORTLIST:%' LIMIT 1", "metric"),
    ("UPDATE instrument_metric_observation SET source_reference='SHORTLIST:a:shortlist:2:1yrX' WHERE source_reference LIKE 'SHORTLIST:%' LIMIT 1", "metric"),
    ("UPDATE shortlist_entry_source_occurrence SET conflict_status='SOURCE_REPORTED' WHERE source_row_number=3", "conflict"),
])
def test_validator_rejects_same_count_semantic_tampering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sql: str, message: str) -> None:
    target, audit = _validated_target(tmp_path, monkeypatch)
    with sqlite3.connect(target) as connection: connection.execute(sql)
    with pytest.raises(RuntimeError, match=message): validate_shortlist_stage(target, audit)


def test_validator_rejects_same_count_lineage_redirection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target, audit = _validated_target(tmp_path, monkeypatch)
    assert validate_shortlist_stage(target, audit)["lineage"] == 3
    with sqlite3.connect(target) as connection:
        source_id, old_membership = connection.execute(
            "SELECT source_occurrence_id, shortlist_entry_id FROM shortlist_entry_lineage ORDER BY source_occurrence_id LIMIT 1"
        ).fetchone()
        wrong_membership = connection.execute(
            "SELECT shortlist_entry_id FROM shortlist_entry WHERE shortlist_entry_id != ? LIMIT 1", (old_membership,)
        ).fetchone()[0]
        connection.execute("DELETE FROM shortlist_entry_lineage WHERE source_occurrence_id = ?", (source_id,))
        connection.execute(
            "INSERT INTO shortlist_entry_lineage(shortlist_entry_id, source_occurrence_id) VALUES (?, ?)",
            (wrong_membership, source_id),
        )
        assert connection.execute("SELECT count(*) FROM shortlist_entry_lineage").fetchone()[0] == 3
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    with pytest.raises(RuntimeError, match="lineage tampering"):
        validate_shortlist_stage(target, audit)


@pytest.mark.parametrize(("sql", "message"), [
    ("UPDATE shortlist_stage_manifest SET integration_version='MILESTONE_9_SHORTLIST_V2'", "stale manifest"),
    ("UPDATE shortlist_stage_manifest SET header_signature='product|isin|currency'", "stale manifest"),
    ("UPDATE shortlist_stage_manifest SET workbook_fingerprints_json='{\"fixture.xls\":\"b" + "b" * 63 + "\"}'", "stale manifest"),
    ("UPDATE shortlist_stage_manifest SET snapshot_count=2", "manifest count"),
    ("UPDATE shortlist_stage_manifest SET dataset_fingerprint='c" + "c" * 63 + "'", "stale manifest"),
    ("UPDATE shortlist_stage_manifest SET completion_status='INCOMPLETE'", "stale manifest"),
])
def test_validator_rejects_manifest_tampering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sql: str, message: str) -> None:
    target, audit = _validated_target(tmp_path, monkeypatch)
    with sqlite3.connect(target) as connection:
        before = connection.execute("SELECT count(*) FROM shortlist_entry_source_occurrence").fetchone()[0]
        connection.execute(sql)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    with pytest.raises(RuntimeError, match=message): validate_shortlist_stage(target, audit)
    with sqlite3.connect(target) as connection:
        assert connection.execute("SELECT count(*) FROM shortlist_entry_source_occurrence").fetchone()[0] == before


@pytest.mark.parametrize(("delete_sql", "message"), [
    ("DELETE FROM shortlist_entry_lineage WHERE source_occurrence_id=1", "incomplete"),
    (
        (
            "DELETE FROM shortlist_entry_lineage WHERE source_occurrence_id=1;"
            "DELETE FROM shortlist_entry_source_occurrence "
            "WHERE shortlist_entry_source_occurrence_id=1"
        ),
        "source occurrence",
    ),
    (
        (
            "DELETE FROM shortlist_entry_lineage WHERE shortlist_entry_id=1;"
            "DELETE FROM shortlist_entry WHERE shortlist_entry_id=1"
        ),
        "incomplete",
    ),
])
def test_validator_rejects_missing_structural_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, delete_sql: str, message: str) -> None:
    target, audit = _validated_target(tmp_path, monkeypatch)
    with sqlite3.connect(target) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(delete_sql)
    with pytest.raises(RuntimeError, match=message): validate_shortlist_stage(target, audit)


def test_source_row_uniqueness_constraint_rejects_duplicate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target, _audit = _validated_target(tmp_path, monkeypatch)
    with sqlite3.connect(target) as connection:
        row = connection.execute("SELECT shortlist_snapshot_id,instrument_id,source_sheet_id,source_row_number,observed_product_name,source_payload_json FROM shortlist_entry_source_occurrence LIMIT 1").fetchone()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO shortlist_entry_source_occurrence(shortlist_snapshot_id,instrument_id,source_sheet_id,source_row_number,observed_product_name,source_payload_json,conflict_status) VALUES(?,?,?,?,?,?, 'SOURCE_REPORTED')", row)


def _assert_database_healthy(target: Path) -> None:
    with sqlite3.connect(target) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def _assert_read_only_rejection(
    target: Path,
    audit: dict[str, AuditValue],
    message: str,
) -> None:
    before = target.read_bytes()
    with pytest.raises(RuntimeError, match=message):
        validate_shortlist_stage(target, audit)
    assert target.read_bytes() == before


def test_validator_rejects_extra_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target, audit = _validated_target(tmp_path, monkeypatch)
    assert validate_shortlist_stage(target, audit)["snapshots"] == 1
    with sqlite3.connect(target) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        source_file_id = connection.execute(
            "INSERT INTO source_file(filename,sha256,source_type,source_date) VALUES(?,?,?,?)",
            ("unreported.xls", "b" * 64, "SHORTLIST_XLS", "2024-02-01"),
        ).lastrowid
        source_sheet_id = connection.execute(
            "INSERT INTO source_sheet(source_file_id,sheet_name) VALUES(?,?)",
            (source_file_id, "shortlist"),
        ).lastrowid
        connection.execute(
            "INSERT INTO shortlist_snapshot(snapshot_date,source_sheet_id) VALUES(?,?)",
            ("2024-02-01", source_sheet_id),
        )
    _assert_database_healthy(target)
    _assert_read_only_rejection(target, audit, "incomplete or stale shortlist stage")


def test_validator_rejects_extra_source_occurrence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target, audit = _validated_target(tmp_path, monkeypatch)
    with sqlite3.connect(target) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        instrument_id = connection.execute(
            "INSERT INTO instrument(isin,canonical_name) VALUES(?,?)",
            ("US0231351067", "Unreported instrument"),
        ).lastrowid
        snapshot_id, source_sheet_id = connection.execute(
            "SELECT shortlist_snapshot_id,source_sheet_id FROM shortlist_snapshot"
        ).fetchone()
        connection.execute(
            """INSERT INTO shortlist_entry_source_occurrence(
                   shortlist_snapshot_id,instrument_id,source_sheet_id,source_row_number,
                   observed_product_name,observed_currency_code,observed_asset_class,
                   observed_sub_asset_class,source_payload_json,conflict_status
               ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                snapshot_id,
                instrument_id,
                source_sheet_id,
                99,
                "Unreported instrument",
                "USD",
                "Equity",
                "Global",
                "{}",
                "SOURCE_REPORTED",
            ),
        )
    _assert_database_healthy(target)
    _assert_read_only_rejection(target, audit, "source occurrence payload tampering")


def test_validator_rejects_extra_membership(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target, audit = _validated_target(tmp_path, monkeypatch)
    with sqlite3.connect(target) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        instrument_id = connection.execute(
            "INSERT INTO instrument(isin,canonical_name) VALUES(?,?)",
            ("US0231351067", "Unsupported membership"),
        ).lastrowid
        snapshot_id = connection.execute(
            "SELECT shortlist_snapshot_id FROM shortlist_snapshot"
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO shortlist_entry(
                   shortlist_snapshot_id,instrument_id,source_row_number,status
               ) VALUES(?,?,?,?)""",
            (snapshot_id, instrument_id, 99, "SOURCE_REPORTED"),
        )
    _assert_database_healthy(target)
    _assert_read_only_rejection(target, audit, "incomplete or stale shortlist stage")


def test_validator_rejects_extra_lineage_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target, audit = _validated_target(tmp_path, monkeypatch)
    with sqlite3.connect(target) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        source_occurrence_id, correct_membership_id = connection.execute(
            """SELECT l.source_occurrence_id,l.shortlist_entry_id
               FROM shortlist_entry_lineage l
               ORDER BY l.source_occurrence_id LIMIT 1"""
        ).fetchone()
        wrong_membership_id = connection.execute(
            "SELECT shortlist_entry_id FROM shortlist_entry WHERE shortlist_entry_id != ?",
            (correct_membership_id,),
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO shortlist_entry_lineage(shortlist_entry_id,source_occurrence_id) VALUES(?,?)",
            (wrong_membership_id, source_occurrence_id),
        )
    _assert_database_healthy(target)
    _assert_read_only_rejection(target, audit, "lineage tampering")


def test_validator_rejects_unexpected_second_multi_occurrence_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target, audit = _validated_target(tmp_path, monkeypatch)
    with sqlite3.connect(target) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        snapshot_id, instrument_id, source_sheet_id, membership_id = connection.execute(
            """SELECT o.shortlist_snapshot_id,o.instrument_id,o.source_sheet_id,l.shortlist_entry_id
               FROM shortlist_entry_source_occurrence o
               JOIN shortlist_entry_lineage l
                 ON l.source_occurrence_id=o.shortlist_entry_source_occurrence_id
               WHERE o.source_row_number=2"""
        ).fetchone()
        occurrence_id = connection.execute(
            """INSERT INTO shortlist_entry_source_occurrence(
                   shortlist_snapshot_id,instrument_id,source_sheet_id,source_row_number,
                   observed_product_name,observed_currency_code,observed_asset_class,
                   observed_sub_asset_class,source_payload_json,conflict_status
               ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                snapshot_id,
                instrument_id,
                source_sheet_id,
                99,
                "Unreported second occurrence",
                "USD",
                "Equity",
                "Global",
                "{}",
                "SOURCE_REPORTED",
            ),
        ).lastrowid
        connection.execute(
            "INSERT INTO shortlist_entry_lineage(shortlist_entry_id,source_occurrence_id) VALUES(?,?)",
            (membership_id, occurrence_id),
        )
        assert connection.execute(
            """SELECT count(*) FROM (
                   SELECT shortlist_snapshot_id,instrument_id
                   FROM shortlist_entry_source_occurrence
                   GROUP BY shortlist_snapshot_id,instrument_id
                   HAVING count(*) > 1
               )"""
        ).fetchone()[0] == 2
    _assert_database_healthy(target)
    _assert_read_only_rejection(target, audit, "unexpected multi-occurrence group")


def test_validator_rejects_dangling_foreign_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target, audit = _validated_target(tmp_path, monkeypatch)
    assert validate_shortlist_stage(target, audit)["lineage"] == 3
    with sqlite3.connect(target) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "UPDATE shortlist_entry_lineage SET source_occurrence_id=999999 "
            "WHERE source_occurrence_id=(SELECT min(source_occurrence_id) "
            "FROM shortlist_entry_lineage)"
        )
    with sqlite3.connect(target) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        foreign_key_violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        assert foreign_key_violations
    _assert_read_only_rejection(target, audit, "foreign-key integrity failure")
    with sqlite3.connect(target) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == foreign_key_violations


def test_validator_rejects_missing_required_schema_object(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target, audit = _validated_target(tmp_path, monkeypatch)
    assert validate_shortlist_stage(target, audit)["snapshots"] == 1
    with sqlite3.connect(target) as connection:
        connection.execute(
            "ALTER TABLE shortlist_stage_manifest RENAME TO removed_shortlist_stage_manifest"
        )
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    _assert_read_only_rejection(target, audit, "missing or incomplete shortlist schema")


def test_validator_rejects_incompatible_required_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target, audit = _validated_target(tmp_path, monkeypatch)
    assert validate_shortlist_stage(target, audit)["lineage"] == 3
    with sqlite3.connect(target) as connection:
        connection.execute(
            "ALTER TABLE shortlist_entry_lineage RENAME TO displaced_shortlist_entry_lineage"
        )
        connection.execute(
            """CREATE TABLE shortlist_entry_lineage (
                   shortlist_entry_id INTEGER NOT NULL
                       REFERENCES shortlist_entry(shortlist_entry_id),
                   occurrence_reference INTEGER NOT NULL
                       REFERENCES shortlist_entry_source_occurrence(
                           shortlist_entry_source_occurrence_id
                       ),
                   PRIMARY KEY(shortlist_entry_id, occurrence_reference)
               )"""
        )
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    _assert_read_only_rejection(target, audit, "incompatible shortlist schema")
