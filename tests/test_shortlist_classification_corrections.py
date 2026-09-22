"""Focused tests for governed shortlist classification corrections."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import date
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

from portfolio_advisor.construction import (
    ConstructionEvidenceReadiness,
    construct_capital_defensive_portfolio,
)
from portfolio_advisor.construction.evidence import (
    load_construction_instrument_evidence,
)
from portfolio_advisor.construction.models import (
    CapitalConservationShortlist,
    ConstructionProvenance,
    RankedInstrument,
    SourceLineage,
)
from portfolio_advisor.construction.persistence import (
    persist_constructed_candidate,
    validate_persisted_snapshot,
)
from portfolio_advisor.database.migrations import shortlist_parallel as stage
from portfolio_advisor.database.migrations.shortlist_classification import (
    EFFECTIVE_SUB_ASSET_CLASS,
    ORIGINAL_SUB_ASSET_CLASS,
    ClassificationCorrectionRequest,
    ShortlistClassificationCorrectionError,
    active_classification_correction,
    admit_classification_correction,
    load_entry_classifications,
    validate_classification_corrections,
)
from portfolio_advisor.database.migrations.shortlist_zero_null import (
    CorrectionRequest,
    admit_zero_null_corrections,
    validate_corrections,
)
from portfolio_advisor.database.schema.v3 import connect, initialize_schema
from portfolio_advisor.objectives import (
    CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT,
    load_capital_defensive_construction_policy,
)

from .constructed_portfolio_fixtures import build_fixture


def _row(
    source_row: int,
    isin: str,
    sub_asset_class: str,
    *,
    asset_class: str = "Equity",
    zero_metrics: bool = False,
) -> dict[str, object]:
    metrics = {header: "0" if zero_metrics else "0.25" for header in stage.METRICS}
    source_values: dict[str, object] = {
        **metrics,
        "Aleszközosztály": sub_asset_class,
        "Eszközosztály": asset_class,
        "ISIN": isin,
    }
    return {
        "isin": isin,
        "source_row": source_row,
        "product_name": f"Synthetic {isin}",
        "normalized_product_name": f"synthetic {isin}".casefold(),
        "currency": "EUR",
        "asset_class": asset_class,
        "sub_asset_class": sub_asset_class,
        "source_values": source_values,
    }


def _sheet(
    *,
    digest: str = "a" * 64,
    rows: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "source_type": "SHORTLIST_XLS",
        "status": "AUDITED",
        "header_signature": stage.SUPPORTED_SIGNATURE,
        "file": "fixture.xls",
        "file_sha256": digest,
        "sheet": "shortlist",
        "snapshot_date": "2026-09-22",
        "identity_records": rows
        or [
            _row(2, "US0378331005", ORIGINAL_SUB_ASSET_CLASS),
            _row(3, "US5949181045", EFFECTIVE_SUB_ASSET_CLASS),
            _row(4, "US0231351067", "Global"),
        ],
    }


def _target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    sheet: dict[str, object] | None = None,
) -> tuple[Path, dict[str, object], dict[str, list[dict[str, object]]]]:
    target = tmp_path / "target.sqlite"
    with connect(target) as connection:
        initialize_schema(connection)
    audit = {"files": [sheet or _sheet()]}
    monkeypatch.setattr(stage, "audit_workbooks", lambda _path: audit)
    result = stage.integrate_shortlist(
        workbook_directory=tmp_path, target=target, apply=True
    )
    return target, result, audit


def _request(target: Path, dataset_fingerprint: str) -> ClassificationCorrectionRequest:
    return ClassificationCorrectionRequest(
        correction_id="SHORTLIST_CLASSIFICATION_2026_09_22",
        dataset_fingerprint=dataset_fingerprint,
        initial_target_sha256=sha256(target.read_bytes()).hexdigest(),
        authorization_reference="USER_REQUEST_2026_09_22",
        reason=(
            "Correct the exact shortlist sub-asset label Fejl?d? piacok to "
            "Fejlődő piacok without changing source evidence."
        ),
    )


def _screening(target: Path, dataset_fingerprint: str) -> CapitalConservationShortlist:
    with sqlite3.connect(target) as connection:
        rows = connection.execute(
            """SELECT e.shortlist_entry_id, e.instrument_id, i.isin, i.canonical_name,
                      o.shortlist_entry_source_occurrence_id, o.source_row_number
               FROM shortlist_entry AS e
               JOIN instrument AS i ON i.instrument_id=e.instrument_id
               JOIN shortlist_entry_lineage AS l ON l.shortlist_entry_id=e.shortlist_entry_id
               JOIN shortlist_entry_source_occurrence AS o
                 ON o.shortlist_entry_source_occurrence_id=l.source_occurrence_id
               ORDER BY e.shortlist_entry_id, o.shortlist_entry_source_occurrence_id"""
        ).fetchall()
    by_entry: dict[int, list[tuple[object, ...]]] = {}
    for row in rows:
        by_entry.setdefault(int(row[0]), []).append(tuple(row))
    candidates = tuple(
        RankedInstrument(
            instrument_id=cast(int, values[0][1]),
            isin=str(values[0][2]),
            canonical_name=str(values[0][3]),
            eligible=True,
            rejection_reasons=(),
            rank=index,
            total_score=1.0,
            feature_values=(),
            weighted_contributions=(),
            lineage=SourceLineage(
                shortlist_entry_id=entry_id,
                source_occurrence_ids=tuple(cast(int, value[4]) for value in values),
                source_row_numbers=tuple(cast(int, value[5]) for value in values),
            ),
        )
        for index, (entry_id, values) in enumerate(by_entry.items(), start=1)
    )
    provenance = ConstructionProvenance(
        objective="capital_conservation",
        strategy="CAPITAL_DEFENSIVE",
        construction_capability="TEST",
        policy_id="CAPITAL_PRESERVATION_RANKING_POLICY",
        policy_version="1.0.1",
        policy_fingerprint="c" * 64,
        registry_fingerprint="d" * 64,
        capability_states=(),
        snapshot_id=1,
        snapshot_date=date(2026, 9, 22),
        source_file="fixture.xls",
        source_file_sha256="a" * 64,
        source_sheet_id=1,
        source_sheet_name="shortlist",
        shortlist_manifest_fingerprint=dataset_fingerprint,
        shortlist_integration_version=stage.INTEGRATION_VERSION,
    )
    return CapitalConservationShortlist(
        provenance=provenance,
        candidates=candidates,
        constructed=candidates,
        ranking_warnings=(),
    )


def test_admission_preserves_originals_and_exposes_effective_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, integration, _audit = _target(tmp_path, monkeypatch)
    request = _request(target, str(integration["dataset_fingerprint"]))

    result = admit_classification_correction(target, request)

    assert result.item_count == 1
    assert result.replayed is False
    with sqlite3.connect(target) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """SELECT original_sub_asset_class, effective_sub_asset_class,
                      correction_id
               FROM v_effective_shortlist_classification
               ORDER BY source_row_number"""
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            (
                ORIGINAL_SUB_ASSET_CLASS,
                EFFECTIVE_SUB_ASSET_CLASS,
                request.correction_id,
            ),
            (EFFECTIVE_SUB_ASSET_CLASS, EFFECTIVE_SUB_ASSET_CLASS, None),
            ("Global", "Global", None),
        ]
        assert (
            connection.execute(
                "SELECT count(*) FROM shortlist_entry_source_occurrence "
                "WHERE observed_sub_asset_class=?",
                (ORIGINAL_SUB_ASSET_CLASS,),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM shortlist_entry_source_occurrence "
                "WHERE json_extract(source_payload_json, '$.Aleszközosztály')=?",
                (ORIGINAL_SUB_ASSET_CLASS,),
            ).fetchone()[0]
            == 1
        )
        validate_classification_corrections(connection)


def test_mixed_spellings_share_effective_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, integration, _audit = _target(tmp_path, monkeypatch)
    admit_classification_correction(
        target, _request(target, str(integration["dataset_fingerprint"]))
    )
    screening = _screening(target, str(integration["dataset_fingerprint"]))

    evidence = load_construction_instrument_evidence(target, screening)

    assert (
        evidence[0].group
        == evidence[1].group
        == (
            "Equity",
            EFFECTIVE_SUB_ASSET_CLASS,
        )
    )
    assert evidence[0].original_sub_asset_class == ORIGINAL_SUB_ASSET_CLASS
    assert evidence[1].original_sub_asset_class == EFFECTIVE_SUB_ASSET_CLASS


def test_conflicting_occurrences_remain_conflicting_after_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    duplicate_isin = "US0378331005"
    target, integration, _audit = _target(
        tmp_path,
        monkeypatch,
        sheet=_sheet(
            rows=[
                _row(2, duplicate_isin, ORIGINAL_SUB_ASSET_CLASS),
                _row(3, duplicate_isin, EFFECTIVE_SUB_ASSET_CLASS),
            ]
        ),
    )
    admit_classification_correction(
        target, _request(target, str(integration["dataset_fingerprint"]))
    )
    screening = _screening(target, str(integration["dataset_fingerprint"]))

    evidence = load_construction_instrument_evidence(target, screening)

    assert len(evidence) == 1
    assert evidence[0].category_conflict is True
    assert evidence[0].group is None
    with sqlite3.connect(target) as connection:
        connection.row_factory = sqlite3.Row
        classifications = load_entry_classifications(
            connection,
            screening.candidates[0].lineage.shortlist_entry_id,
            apply_corrections=True,
        )
    assert {item.effective_sub_asset_class for item in classifications} == {
        EFFECTIVE_SUB_ASSET_CLASS
    }
    assert {item.conflict_status for item in classifications} == {
        "SOURCE_METADATA_CONFLICT"
    }


def test_exact_replay_and_changed_bindings_are_nonmutating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, integration, _audit = _target(tmp_path, monkeypatch)
    request = _request(target, str(integration["dataset_fingerprint"]))
    admit_classification_correction(target, request)
    admitted_sha256 = sha256(target.read_bytes()).hexdigest()

    replay = admit_classification_correction(target, request)
    assert replay.replayed is True
    assert sha256(target.read_bytes()).hexdigest() == admitted_sha256

    for changed in (
        replace(request, initial_target_sha256="b" * 64),
        replace(request, authorization_reference="OTHER_AUTHORIZATION"),
        replace(request, dataset_fingerprint="c" * 64),
        replace(request, reason="Different reason"),
    ):
        with pytest.raises(ShortlistClassificationCorrectionError, match="bindings"):
            admit_classification_correction(target, changed)
        assert sha256(target.read_bytes()).hexdigest() == admitted_sha256


def test_mismatched_raw_evidence_rejects_without_partial_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, integration, _audit = _target(tmp_path, monkeypatch)
    with sqlite3.connect(target) as connection:
        payload = json.loads(
            connection.execute(
                "SELECT source_payload_json FROM shortlist_entry_source_occurrence "
                "WHERE source_row_number=2"
            ).fetchone()[0]
        )
        payload["Aleszközosztály"] = "Changed source"
        connection.execute(
            "UPDATE shortlist_entry_source_occurrence SET source_payload_json=? "
            "WHERE source_row_number=2",
            (json.dumps(payload, ensure_ascii=False, sort_keys=True),),
        )
    request = _request(target, str(integration["dataset_fingerprint"]))
    before = sha256(target.read_bytes()).hexdigest()

    with pytest.raises(
        ShortlistClassificationCorrectionError, match="raw source evidence"
    ):
        admit_classification_correction(target, request)

    assert sha256(target.read_bytes()).hexdigest() == before
    with sqlite3.connect(target) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM sqlite_master "
                "WHERE name='shortlist_classification_correction_admission'"
            ).fetchone()[0]
            == 0
        )


def test_same_dataset_reimport_revalidates_and_changed_dataset_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, integration, _audit = _target(tmp_path, monkeypatch)
    admit_classification_correction(
        target, _request(target, str(integration["dataset_fingerprint"]))
    )

    same = stage.integrate_shortlist(
        workbook_directory=tmp_path, target=target, apply=True
    )
    assert same["dataset_fingerprint"] == integration["dataset_fingerprint"]
    with sqlite3.connect(target) as connection:
        connection.row_factory = sqlite3.Row
        validate_classification_corrections(connection)

    before = sha256(target.read_bytes()).hexdigest()
    monkeypatch.setattr(
        stage, "audit_workbooks", lambda _path: {"files": [_sheet(digest="b" * 64)]}
    )
    with pytest.raises(
        ShortlistClassificationCorrectionError, match="binding|dataset|evidence"
    ):
        stage.integrate_shortlist(
            workbook_directory=tmp_path, target=target, apply=True
        )
    assert sha256(target.read_bytes()).hexdigest() == before


def test_metric_corrections_remain_valid_when_classification_is_admitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sheet = _sheet(
        rows=[
            _row(
                2,
                "US0378331005",
                ORIGINAL_SUB_ASSET_CLASS,
                zero_metrics=True,
            )
        ]
    )
    target, integration, _audit = _target(tmp_path, monkeypatch, sheet=sheet)
    dataset_fingerprint = str(integration["dataset_fingerprint"])
    zero_request = CorrectionRequest(
        correction_id="SHORTLIST_ZERO_NULL_2026_09_22",
        dataset_fingerprint=dataset_fingerprint,
        initial_target_sha256=sha256(target.read_bytes()).hexdigest(),
        authorization_reference="USER_REQUEST_2026_09_22",
        reason="Synthetic zero correction.",
    )
    admit_zero_null_corrections(target, zero_request)
    classification_request = _request(target, dataset_fingerprint)

    admit_classification_correction(target, classification_request)

    with sqlite3.connect(target) as connection:
        connection.row_factory = sqlite3.Row
        validate_corrections(connection)
        validate_classification_corrections(connection)
        assert connection.execute(
            "SELECT count(*) FROM v_effective_shortlist_metric_observation "
            "WHERE effective_value IS NULL"
        ).fetchone()[0] == len(stage.METRICS)


def test_persistence_uses_effective_mapping_and_historical_provenance_stays_original(
    tmp_path: Path,
) -> None:
    groups = (
        ("Equity", ORIGINAL_SUB_ASSET_CLASS),
        ("Equity", EFFECTIVE_SUB_ASSET_CLASS),
        ("Bond", "Government"),
        ("Bond", "Government"),
        ("Bond", "Government"),
        ("Bond", "Corporate"),
        ("Bond", "Corporate"),
        ("Bond", "Corporate"),
        ("Cashlike", "Money"),
        ("Cashlike", "Money"),
    )
    historical_fixture = build_fixture(tmp_path / "historical", groups=groups)
    with sqlite3.connect(historical_fixture.database_path) as connection:
        payload = {
            "Aleszközosztály": ORIGINAL_SUB_ASSET_CLASS,
            "Eszközosztály": "Equity",
            "ISIN": historical_fixture.instruments[0].isin,
        }
        connection.execute(
            "UPDATE shortlist_entry_source_occurrence SET source_payload_json=? "
            "WHERE shortlist_entry_source_occurrence_id=1",
            (json.dumps(payload, ensure_ascii=False, sort_keys=True),),
        )
    policy = load_capital_defensive_construction_policy(
        Path(__file__).resolve().parents[1]
        / CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT
    )
    historical = construct_capital_defensive_portfolio(
        screening=historical_fixture.screening,
        cash_by_currency={"EUR": Decimal(1000)},
        policy=policy,
        instruments=historical_fixture.instruments,
        readiness=ConstructionEvidenceReadiness(True, True, True),
    )
    assert historical.candidate is not None
    historical_result = persist_constructed_candidate(
        database_path=historical_fixture.database_path,
        candidate=historical.candidate,
        policy=policy,
    )
    historical_request = _request(historical_fixture.database_path, "b" * 64)
    admit_classification_correction(
        historical_fixture.database_path, historical_request
    )
    with sqlite3.connect(historical_fixture.database_path) as connection:
        connection.row_factory = sqlite3.Row
        validate_persisted_snapshot(connection, historical_result.portfolio_snapshot_id)

    corrected_fixture = build_fixture(tmp_path / "corrected", groups=groups)
    with sqlite3.connect(corrected_fixture.database_path) as connection:
        payload = {
            "Aleszközosztály": ORIGINAL_SUB_ASSET_CLASS,
            "Eszközosztály": "Equity",
            "ISIN": corrected_fixture.instruments[0].isin,
        }
        connection.execute(
            "UPDATE shortlist_entry_source_occurrence SET source_payload_json=? "
            "WHERE shortlist_entry_source_occurrence_id=1",
            (json.dumps(payload, ensure_ascii=False, sort_keys=True),),
        )
    request = _request(corrected_fixture.database_path, "b" * 64)
    admitted = admit_classification_correction(corrected_fixture.database_path, request)
    with sqlite3.connect(corrected_fixture.database_path) as connection:
        connection.row_factory = sqlite3.Row
        binding = active_classification_correction(connection)
    assert binding is not None
    corrected_instruments = tuple(
        replace(
            item,
            sub_asset_class=(
                EFFECTIVE_SUB_ASSET_CLASS
                if item.sub_asset_class == ORIGINAL_SUB_ASSET_CLASS
                else item.sub_asset_class
            ),
            original_asset_class=item.asset_class,
            original_sub_asset_class=item.sub_asset_class,
            classification_correction_id=binding.correction_id,
            classification_correction_set_fingerprint=(
                binding.correction_set_fingerprint
            ),
        )
        for item in corrected_fixture.instruments
    )
    corrected = construct_capital_defensive_portfolio(
        screening=corrected_fixture.screening,
        cash_by_currency={"EUR": Decimal(1000)},
        policy=policy,
        instruments=corrected_instruments,
        readiness=ConstructionEvidenceReadiness(True, True, True),
    )
    assert corrected.candidate is not None

    corrected_result = persist_constructed_candidate(
        database_path=corrected_fixture.database_path,
        candidate=corrected.candidate,
        policy=policy,
    )

    with sqlite3.connect(corrected_fixture.database_path) as connection:
        connection.row_factory = sqlite3.Row
        validate_persisted_snapshot(connection, corrected_result.portfolio_snapshot_id)
        assert (
            connection.execute(
                "SELECT count(*) FROM shortlist_classification_correction"
            ).fetchone()[0]
            == admitted.item_count
        )
