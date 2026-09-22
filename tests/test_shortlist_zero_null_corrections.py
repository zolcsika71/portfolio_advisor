"""Focused tests for governed shortlist zero-to-NULL corrections."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from portfolio_advisor.construction.repository import SchemaV3ShortlistRepository
from portfolio_advisor.database.migrations import shortlist_parallel as stage
from portfolio_advisor.database.migrations.shortlist_zero_null import (
    FEATURE_FINGERPRINT,
    FEATURE_ID,
    FEATURE_REVISION,
    CorrectionRequest,
    ShortlistCorrectionError,
    admit_zero_null_corrections,
    validate_corrections,
)
from portfolio_advisor.database.schema.v3 import connect, initialize_schema


def _sheet(*, digest: str = "a" * 64, zero: bool = True) -> dict[str, object]:
    source_values = {header: "0" if zero else "0.25" for header in stage.METRICS}
    return {
        "source_type": "SHORTLIST_XLS",
        "status": "AUDITED",
        "header_signature": stage.SUPPORTED_SIGNATURE,
        "file": "fixture.xls",
        "file_sha256": digest,
        "sheet": "shortlist",
        "snapshot_date": "2026-09-22",
        "identity_records": [
            {
                "isin": "US0378331005",
                "source_row": 2,
                "product_name": "Synthetic shortlist item",
                "normalized_product_name": "synthetic shortlist item",
                "currency": "USD",
                "asset_class": "Equity",
                "sub_asset_class": "Global",
                "source_values": source_values,
            }
        ],
    }


def _target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, dict[str, object]]:
    target = tmp_path / "target.sqlite"
    with connect(target) as connection:
        initialize_schema(connection)
    audit = {"files": [_sheet()]}
    monkeypatch.setattr(stage, "audit_workbooks", lambda _path: audit)
    result = stage.integrate_shortlist(
        workbook_directory=tmp_path, target=target, apply=True
    )
    return target, result


def _request(target: Path, dataset_fingerprint: str) -> CorrectionRequest:
    return CorrectionRequest(
        correction_id="SHORTLIST_ZERO_NULL_2026_09_22",
        dataset_fingerprint=dataset_fingerprint,
        initial_target_sha256=sha256(target.read_bytes()).hexdigest(),
        authorization_reference="USER_REQUEST_2026_09_22",
        reason="Expose the verified current shortlist numeric zeros as missing values.",
    )


def test_admission_preserves_originals_and_projects_explicit_nulls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, integration = _target(tmp_path, monkeypatch)
    request = _request(target, str(integration["dataset_fingerprint"]))
    with sqlite3.connect(target) as connection:
        instrument_id, metric_id = connection.execute(
            "SELECT instrument_id, metric_id FROM instrument_metric_observation LIMIT 1"
        ).fetchone()
        connection.execute(
            """INSERT INTO instrument_metric_observation(
                   instrument_id, metric_id, observation_date, value,
                   provenance_type, source_reference
               ) VALUES (?, ?, '2026-09-23', 0.0, 'PROVIDER_REPORTED', 'MODEL:synthetic')""",
            (instrument_id, metric_id),
        )
    request = replace(
        request, initial_target_sha256=sha256(target.read_bytes()).hexdigest()
    )

    result = admit_zero_null_corrections(target, request)

    assert result.item_count == len(stage.METRICS)
    assert result.replayed is False
    with sqlite3.connect(target) as connection:
        connection.row_factory = sqlite3.Row
        assert (
            connection.execute(
                "SELECT count(*) FROM instrument_metric_observation WHERE value=0.0"
            ).fetchone()[0]
            == len(stage.METRICS) + 1
        )
        assert connection.execute(
            "SELECT count(*) FROM v_effective_shortlist_metric_observation "
            "WHERE effective_value IS NULL AND original_value=0.0"
        ).fetchone()[0] == len(stage.METRICS)
        assert (
            connection.execute(
                "SELECT value FROM instrument_metric_observation WHERE source_reference='MODEL:synthetic'"
            ).fetchone()[0]
            == 0.0
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM shortlist_entry_source_occurrence "
                "WHERE source_payload_json LIKE '%\"0\"%'"
            ).fetchone()[0]
            == 1
        )
        validate_corrections(connection)


def test_exact_replay_is_noop_and_changed_bindings_fail_without_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, integration = _target(tmp_path, monkeypatch)
    request = _request(target, str(integration["dataset_fingerprint"]))
    admit_zero_null_corrections(target, request)
    admitted_sha256 = sha256(target.read_bytes()).hexdigest()

    replay = admit_zero_null_corrections(target, request)
    assert replay.replayed is True
    assert sha256(target.read_bytes()).hexdigest() == admitted_sha256

    for changed in (
        replace(request, initial_target_sha256="b" * 64),
        replace(request, authorization_reference="OTHER_AUTHORIZATION"),
        replace(request, dataset_fingerprint="c" * 64),
        replace(request, reason="Different reason"),
    ):
        with pytest.raises(ShortlistCorrectionError, match="bindings"):
            admit_zero_null_corrections(target, changed)
        assert sha256(target.read_bytes()).hexdigest() == admitted_sha256


def test_scope_and_expected_zero_rejections_roll_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, integration = _target(tmp_path, monkeypatch)
    request = _request(target, str(integration["dataset_fingerprint"]))
    with sqlite3.connect(target) as connection:
        connection.execute(
            "UPDATE instrument_metric_observation SET value=1.0 "
            "WHERE source_reference LIKE 'SHORTLIST:%'"
        )
    request = replace(
        request, initial_target_sha256=sha256(target.read_bytes()).hexdigest()
    )
    before_rejection = sha256(target.read_bytes()).hexdigest()

    with pytest.raises(
        ShortlistCorrectionError, match="does not match its raw source value"
    ):
        admit_zero_null_corrections(target, request)
    with sqlite3.connect(target) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM sqlite_master "
                "WHERE name='shortlist_metric_correction_admission'"
            ).fetchone()[0]
            == 0
        )
        assert connection.execute(
            "SELECT count(*) FROM instrument_metric_observation WHERE value=1.0"
        ).fetchone()[0] == len(stage.METRICS)
    assert sha256(target.read_bytes()).hexdigest() == before_rejection


def test_reader_returns_none_without_coercion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, integration = _target(tmp_path, monkeypatch)
    request = _request(target, str(integration["dataset_fingerprint"]))
    admit_zero_null_corrections(target, request)
    repository = SchemaV3ShortlistRepository(target)
    fingerprints = {"fixture.xls": "a" * 64}
    snapshot = repository.select_snapshot(
        as_of=None,
        expected_workbook_fingerprints=fingerprints,
        expected_manifest_fingerprint=str(integration["dataset_fingerprint"]),
    )

    memberships = repository.load_memberships(snapshot)

    assert len(memberships) == 1
    assert dict(memberships[0].metrics) == {
        code: None for code in stage.METRICS.values()
    }


def test_same_dataset_reimport_revalidates_and_changed_dataset_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, integration = _target(tmp_path, monkeypatch)
    request = _request(target, str(integration["dataset_fingerprint"]))
    admit_zero_null_corrections(target, request)

    same = stage.integrate_shortlist(
        workbook_directory=tmp_path, target=target, apply=True
    )
    assert same["dataset_fingerprint"] == integration["dataset_fingerprint"]
    with sqlite3.connect(target) as connection:
        connection.row_factory = sqlite3.Row
        validate_corrections(connection)

    before_changed_attempt = sha256(target.read_bytes()).hexdigest()
    changed_audit = {"files": [_sheet(digest="b" * 64)]}
    monkeypatch.setattr(stage, "audit_workbooks", lambda _path: changed_audit)
    with pytest.raises(ShortlistCorrectionError, match="binding|dataset|evidence"):
        stage.integrate_shortlist(
            workbook_directory=tmp_path, target=target, apply=True
        )
    assert sha256(target.read_bytes()).hexdigest() == before_changed_attempt


def test_damaged_effective_view_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, integration = _target(tmp_path, monkeypatch)
    request = _request(target, str(integration["dataset_fingerprint"]))
    admit_zero_null_corrections(target, request)
    with sqlite3.connect(target) as connection:
        connection.execute("DROP VIEW v_effective_shortlist_metric_observation")
        connection.execute(
            """CREATE VIEW v_effective_shortlist_metric_observation AS
               SELECT instrument_metric_observation_id, instrument_id, metric_id,
                      '' AS metric_code, observation_date, value AS original_value,
                      value AS effective_value, provenance_type, source_file_id,
                      calculation_version, source_reference, NULL AS correction_id
               FROM instrument_metric_observation
               WHERE source_reference LIKE 'SHORTLIST:%'"""
        )

    with sqlite3.connect(target) as connection:
        connection.row_factory = sqlite3.Row
        with pytest.raises(ShortlistCorrectionError, match="damaged or incompatible"):
            validate_corrections(connection)


def test_orphan_feature_marker_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, _integration = _target(tmp_path, monkeypatch)
    with sqlite3.connect(target) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute(
            """INSERT INTO schema_feature_contract(
                   feature_id, revision, contract_fingerprint
               ) VALUES (?, ?, ?)""",
            (FEATURE_ID, FEATURE_REVISION, FEATURE_FINGERPRINT),
        )
        with pytest.raises(ShortlistCorrectionError, match="marker exists without"):
            validate_corrections(connection)
