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
from portfolio_advisor.database.migrations import (
    shortlist_classification_composition as composition,
)
from portfolio_advisor.database.migrations import (
    shortlist_classification_pair_mapping as pair_mapping,
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
from portfolio_advisor.database.migrations.shortlist_classification_composition import (
    ClassificationCompositionRequest,
    admit_composed_classification_correction,
)
from portfolio_advisor.database.migrations.shortlist_classification_pair_mapping import (
    ClassificationPairMappingRequest,
    PairMappingEntry,
    PairMappingManifest,
    admit_pair_mapping_correction,
    load_pair_mapping_manifest,
    validate_pair_mapping_corrections,
)
from portfolio_advisor.database.migrations.shortlist_zero_null import (
    CorrectionRequest,
    admit_zero_null_corrections,
    validate_corrections,
)
from portfolio_advisor.database.schema.v3 import connect, initialize_schema
from portfolio_advisor.history import nav_provenance as nav
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


def _composition_request(
    target: Path,
    dataset_fingerprint: str,
    *label_counts: tuple[str, int],
) -> ClassificationCompositionRequest:
    return ClassificationCompositionRequest(
        correction_id="SHORTLIST_CLASSIFICATION_QUESTION_MARK_2026_09_23",
        dataset_fingerprint=dataset_fingerprint,
        initial_target_sha256=sha256(target.read_bytes()).hexdigest(),
        authorization_reference="USER_REQUEST_2026_09_23",
        reason=(
            "Replace each literal question mark with ő in the authorized current "
            "effective shortlist sub-asset labels."
        ),
        expected_prior_label_counts=label_counts,
    )


def _pair_manifest(
    dataset_fingerprint: str,
    *mappings: tuple[str, str, str, str, int],
    mapping_id: str = "SYNTHETIC_ENGLISH_PAIR_MAPPING_V1",
    expected_occurrence_count: int | None = None,
    snapshot_date: str = "2026-09-22",
) -> PairMappingManifest:
    entries = tuple(
        PairMappingEntry(
            prior_asset_class=prior_asset,
            prior_sub_asset_class=prior_sub_asset,
            effective_asset_class=effective_asset,
            effective_sub_asset_class=effective_sub_asset,
            row_count=count,
            snapshot_count=1,
            first_snapshot_date=snapshot_date,
            last_snapshot_date=snapshot_date,
            reference_status="SYNTHETIC_TEST_MAPPING",
        )
        for prior_asset, prior_sub_asset, effective_asset, effective_sub_asset, count in mappings
    )
    output_pairs = {
        (entry.effective_asset_class, entry.effective_sub_asset_class)
        for entry in entries
    }
    prior_pairs = {
        (entry.prior_asset_class, entry.prior_sub_asset_class) for entry in entries
    }
    return PairMappingManifest(
        schema_version=1,
        mapping_id=mapping_id,
        authorization_reference="USER_APPROVED_SYNTHETIC_ENGLISH_MAPPING",
        dataset_fingerprint=dataset_fingerprint,
        expected_occurrence_count=(
            expected_occurrence_count
            if expected_occurrence_count is not None
            else sum(entry.row_count for entry in entries)
        ),
        expected_mapped_occurrence_count=sum(entry.row_count for entry in entries),
        expected_prior_asset_class_count=len({asset for asset, _sub in prior_pairs}),
        expected_prior_sub_asset_class_count=len({sub for _asset, sub in prior_pairs}),
        expected_prior_pair_count=len(prior_pairs),
        expected_result_asset_class_count=len({asset for asset, _sub in output_pairs}),
        expected_result_sub_asset_class_count=len(
            {sub for _asset, sub in output_pairs}
        ),
        expected_result_pair_count=len(output_pairs),
        expected_snapshot_count=1,
        expected_unchanged_snapshot_count=1,
        expected_changed_transitions=(),
        entries=entries,
        manifest_sha256="e" * 64,
    )


def _pair_request(
    target: Path,
    dataset_fingerprint: str,
    manifest: PairMappingManifest,
    *,
    correction_id: str = "SHORTLIST_CLASSIFICATION_ENGLISH_TEST",
) -> ClassificationPairMappingRequest:
    return ClassificationPairMappingRequest(
        correction_id=correction_id,
        dataset_fingerprint=dataset_fingerprint,
        initial_target_sha256=sha256(target.read_bytes()).hexdigest(),
        authorization_reference=manifest.authorization_reference,
        reason="Apply the explicitly reviewed synthetic English pair mapping.",
        manifest=manifest,
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


def test_additional_admission_composes_effective_labels_and_groups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    affected = "Fejl?d? piacok-Vállalatok"
    corrected = "Fejlődő piacok-Vállalatok"
    target, integration, _audit = _target(
        tmp_path,
        monkeypatch,
        sheet=_sheet(
            rows=[
                _row(2, "US0378331005", ORIGINAL_SUB_ASSET_CLASS),
                _row(3, "US5949181045", affected),
                _row(4, "US0231351067", corrected),
            ]
        ),
    )
    dataset = str(integration["dataset_fingerprint"])
    admit_classification_correction(target, _request(target, dataset))
    request = _composition_request(target, dataset, (affected, 1))

    result = admit_composed_classification_correction(target, request)

    assert result.item_count == 1
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
                "SHORTLIST_CLASSIFICATION_2026_09_22",
            ),
            (affected, corrected, request.correction_id),
            (corrected, corrected, None),
        ]
        assert (
            connection.execute(
                "SELECT count(*) FROM shortlist_entry_source_occurrence "
                "WHERE observed_sub_asset_class=?",
                (affected,),
            ).fetchone()[0]
            == 1
        )
        validate_classification_corrections(connection)

    evidence = load_construction_instrument_evidence(
        target, _screening(target, dataset)
    )
    assert evidence[1].group == evidence[2].group == ("Equity", corrected)


def test_construction_batch_validates_composed_contract_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    affected = "Fejl?d? piacok-Vállalatok"
    corrected = "Fejlődő piacok-Vállalatok"
    target, integration, _audit = _target(
        tmp_path,
        monkeypatch,
        sheet=_sheet(
            rows=[
                _row(2, "US0378331005", ORIGINAL_SUB_ASSET_CLASS),
                _row(3, "US5949181045", affected),
                _row(4, "US0231351067", corrected),
            ]
        ),
    )
    dataset = str(integration["dataset_fingerprint"])
    admit_classification_correction(target, _request(target, dataset))
    admit_composed_classification_correction(
        target, _composition_request(target, dataset, (affected, 1))
    )
    validation_calls = 0
    validate = composition.validate_composed_classification_corrections

    def counted_validate(connection: sqlite3.Connection) -> None:
        nonlocal validation_calls
        validation_calls += 1
        validate(connection)

    monkeypatch.setattr(
        composition, "validate_composed_classification_corrections", counted_validate
    )

    evidence = load_construction_instrument_evidence(
        target, _screening(target, dataset)
    )

    assert validation_calls == 1
    assert len(evidence) == 3
    assert evidence[1].group == evidence[2].group == ("Equity", corrected)


def test_validated_batch_binding_rejects_changed_database_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    affected = "Fejl?d? piacok-Vállalatok"
    target, integration, _audit = _target(
        tmp_path,
        monkeypatch,
        sheet=_sheet(
            rows=[
                _row(2, "US0378331005", ORIGINAL_SUB_ASSET_CLASS),
                _row(3, "US5949181045", affected),
            ]
        ),
    )
    dataset = str(integration["dataset_fingerprint"])
    admit_classification_correction(target, _request(target, dataset))
    admit_composed_classification_correction(
        target, _composition_request(target, dataset, (affected, 1))
    )

    with sqlite3.connect(target) as reader:
        reader.row_factory = sqlite3.Row
        binding = active_classification_correction(reader)
        assert binding is not None
        entry_id = int(
            reader.execute(
                "SELECT min(shortlist_entry_id) FROM shortlist_entry"
            ).fetchone()[0]
        )
        with sqlite3.connect(target) as writer:
            writer.execute(
                "UPDATE source_file SET filename=filename || '.changed' "
                "WHERE source_file_id=(SELECT min(source_file_id) FROM source_file)"
            )

        with pytest.raises(
            ShortlistClassificationCorrectionError, match="became stale"
        ):
            load_entry_classifications(
                reader,
                entry_id,
                apply_corrections=True,
                correction_binding=binding,
            )


def test_composed_replay_and_mismatch_rejection_are_nonmutating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    affected = "Fejl?d? piaci vállalatok"
    target, integration, _audit = _target(
        tmp_path,
        monkeypatch,
        sheet=_sheet(
            rows=[
                _row(2, "US0378331005", ORIGINAL_SUB_ASSET_CLASS),
                _row(3, "US5949181045", affected),
            ]
        ),
    )
    dataset = str(integration["dataset_fingerprint"])
    admit_classification_correction(target, _request(target, dataset))
    request = _composition_request(target, dataset, (affected, 1))
    admit_composed_classification_correction(target, request)
    admitted_sha256 = sha256(target.read_bytes()).hexdigest()

    replay = admit_composed_classification_correction(target, request)
    assert replay.replayed is True
    assert sha256(target.read_bytes()).hexdigest() == admitted_sha256

    for changed in (
        replace(request, initial_target_sha256="b" * 64),
        replace(request, authorization_reference="OTHER_AUTHORIZATION"),
        replace(request, expected_prior_label_counts=((affected, 2),)),
        replace(request, reason="Different reason"),
    ):
        with pytest.raises(ShortlistClassificationCorrectionError, match="bindings"):
            admit_composed_classification_correction(target, changed)
        assert sha256(target.read_bytes()).hexdigest() == admitted_sha256


def test_composed_admission_rejects_changed_evidence_without_partial_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    affected = "Fejl?d? piaci állampapír"
    target, integration, _audit = _target(
        tmp_path,
        monkeypatch,
        sheet=_sheet(
            rows=[
                _row(2, "US0378331005", ORIGINAL_SUB_ASSET_CLASS),
                _row(3, "US5949181045", affected),
            ]
        ),
    )
    dataset = str(integration["dataset_fingerprint"])
    admit_classification_correction(target, _request(target, dataset))
    with sqlite3.connect(target) as connection:
        payload = json.loads(
            connection.execute(
                "SELECT source_payload_json FROM shortlist_entry_source_occurrence "
                "WHERE source_row_number=3"
            ).fetchone()[0]
        )
        payload["Aleszközosztály"] = "Changed source"
        connection.execute(
            "UPDATE shortlist_entry_source_occurrence SET source_payload_json=? "
            "WHERE source_row_number=3",
            (json.dumps(payload, ensure_ascii=False, sort_keys=True),),
        )
    request = _composition_request(target, dataset, (affected, 1))
    before = sha256(target.read_bytes()).hexdigest()

    with pytest.raises(
        ShortlistClassificationCorrectionError, match="raw source evidence"
    ):
        admit_composed_classification_correction(target, request)

    assert sha256(target.read_bytes()).hexdigest() == before
    with sqlite3.connect(target) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM sqlite_master "
                "WHERE name='shortlist_classification_composition_admission'"
            ).fetchone()[0]
            == 0
        )


def test_composed_same_dataset_reimport_preserves_and_changed_dataset_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    affected = "Fejl?d? piacok-Vállalatok"
    sheet = _sheet(
        rows=[
            _row(2, "US0378331005", ORIGINAL_SUB_ASSET_CLASS),
            _row(3, "US5949181045", affected),
        ]
    )
    target, integration, _audit = _target(tmp_path, monkeypatch, sheet=sheet)
    dataset = str(integration["dataset_fingerprint"])
    admit_classification_correction(target, _request(target, dataset))
    admit_composed_classification_correction(
        target, _composition_request(target, dataset, (affected, 1))
    )

    same = stage.integrate_shortlist(
        workbook_directory=tmp_path, target=target, apply=True
    )
    assert same["dataset_fingerprint"] == dataset
    with sqlite3.connect(target) as connection:
        connection.row_factory = sqlite3.Row
        validate_classification_corrections(connection)
        assert (
            connection.execute(
                "SELECT count(*) FROM v_effective_shortlist_classification "
                "WHERE instr(effective_sub_asset_class, '?') > 0"
            ).fetchone()[0]
            == 0
        )

    before = sha256(target.read_bytes()).hexdigest()
    monkeypatch.setattr(
        stage,
        "audit_workbooks",
        lambda _path: {
            "files": [
                _sheet(
                    digest="b" * 64,
                    rows=cast(list[dict[str, object]], sheet["identity_records"]),
                )
            ]
        },
    )
    with pytest.raises(
        ShortlistClassificationCorrectionError, match="binding|dataset|evidence"
    ):
        stage.integrate_shortlist(
            workbook_directory=tmp_path, target=target, apply=True
        )
    assert sha256(target.read_bytes()).hexdigest() == before


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
    later_corrected_label = "Fejl?d? piacok-Vállalatok"
    groups = (
        ("Equity", ORIGINAL_SUB_ASSET_CLASS),
        ("Equity", later_corrected_label),
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
        later_payload = {
            "Aleszközosztály": later_corrected_label,
            "Eszközosztály": "Equity",
            "ISIN": corrected_fixture.instruments[1].isin,
        }
        connection.execute(
            "UPDATE shortlist_entry_source_occurrence SET source_payload_json=? "
            "WHERE shortlist_entry_source_occurrence_id=2",
            (json.dumps(later_payload, ensure_ascii=False, sort_keys=True),),
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

    composition_request = _composition_request(
        corrected_fixture.database_path,
        "b" * 64,
        (later_corrected_label, 1),
    )
    admit_composed_classification_correction(
        corrected_fixture.database_path, composition_request
    )
    with sqlite3.connect(corrected_fixture.database_path) as connection:
        connection.row_factory = sqlite3.Row
        validate_persisted_snapshot(connection, corrected_result.portfolio_snapshot_id)
        rows = connection.execute(
            """SELECT o.shortlist_entry_source_occurrence_id, i.isin,
                      o.observed_asset_class, o.observed_sub_asset_class
               FROM shortlist_entry_source_occurrence AS o
               JOIN instrument AS i ON i.instrument_id=o.instrument_id"""
        ).fetchall()
        connection.executemany(
            "UPDATE shortlist_entry_source_occurrence SET source_payload_json=? "
            "WHERE shortlist_entry_source_occurrence_id=?",
            (
                (
                    json.dumps(
                        {
                            "Aleszközosztály": row[3],
                            "Eszközosztály": row[2],
                            "ISIN": row[1],
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    row[0],
                )
                for row in rows
            ),
        )
    pair_manifest = _pair_manifest(
        "b" * 64,
        ("Equity", EFFECTIVE_SUB_ASSET_CLASS, "Public Equity", "Emerging Markets", 1),
        (
            "Equity",
            "Fejlődő piacok-Vállalatok",
            "Public Equity",
            "Emerging Markets-Corporates",
            1,
        ),
        ("Bond", "Government", "Bond", "Government", 3),
        ("Bond", "Corporate", "Bond", "Corporate", 3),
        ("Cashlike", "Money", "Money Market", "Money", 2),
        snapshot_date="2026-01-01",
    )
    admit_pair_mapping_correction(
        corrected_fixture.database_path,
        _pair_request(corrected_fixture.database_path, "b" * 64, pair_manifest),
    )
    with sqlite3.connect(corrected_fixture.database_path) as connection:
        connection.row_factory = sqlite3.Row
        validate_persisted_snapshot(connection, corrected_result.portfolio_snapshot_id)


def _prepared_pair_mapping_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, str, PairMappingManifest]:
    affected = "Fejl?d? piacok-Vállalatok"
    corrected = "Fejlődő piacok-Vállalatok"
    target, integration, _audit = _target(
        tmp_path,
        monkeypatch,
        sheet=_sheet(
            rows=[
                _row(
                    2, "US0378331005", ORIGINAL_SUB_ASSET_CLASS, asset_class="Részvény"
                ),
                _row(3, "US5949181045", affected, asset_class="Részvény"),
                _row(4, "US0231351067", "Globál", asset_class="Kötvény"),
            ]
        ),
    )
    dataset = str(integration["dataset_fingerprint"])
    admit_classification_correction(target, _request(target, dataset))
    admit_composed_classification_correction(
        target, _composition_request(target, dataset, (affected, 1))
    )
    manifest = _pair_manifest(
        dataset,
        ("Részvény", EFFECTIVE_SUB_ASSET_CLASS, "Equity", "Emerging Markets", 1),
        ("Részvény", corrected, "Equity", "Emerging Markets-Corporates", 1),
        ("Kötvény", "Globál", "Investment Grade Bond", "Global", 1),
    )
    return target, dataset, manifest


def test_pair_mapping_preserves_evidence_and_drives_shared_consumers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, dataset, manifest = _prepared_pair_mapping_target(tmp_path, monkeypatch)
    request = _pair_request(target, dataset, manifest)

    result = admit_pair_mapping_correction(target, request)

    assert result.item_count == 3
    assert result.replayed is False
    with sqlite3.connect(target) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """SELECT original_asset_class, original_sub_asset_class,
                      effective_asset_class, effective_sub_asset_class,
                      correction_id, conflict_status
               FROM v_effective_shortlist_classification
               ORDER BY source_row_number"""
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            (
                "Részvény",
                ORIGINAL_SUB_ASSET_CLASS,
                "Equity",
                "Emerging Markets",
                request.correction_id,
                "SOURCE_REPORTED",
            ),
            (
                "Részvény",
                "Fejl?d? piacok-Vállalatok",
                "Equity",
                "Emerging Markets-Corporates",
                request.correction_id,
                "SOURCE_REPORTED",
            ),
            (
                "Kötvény",
                "Globál",
                "Investment Grade Bond",
                "Global",
                request.correction_id,
                "SOURCE_REPORTED",
            ),
        ]
        assert tuple(
            connection.execute(
                "SELECT json_extract(source_payload_json, '$.Eszközosztály'), "
                "json_extract(source_payload_json, '$.Aleszközosztály') "
                "FROM shortlist_entry_source_occurrence WHERE source_row_number=2"
            ).fetchone()
        ) == ("Részvény", ORIGINAL_SUB_ASSET_CLASS)
        validate_pair_mapping_corrections(connection)

    evidence = load_construction_instrument_evidence(
        target, _screening(target, dataset)
    )
    assert [item.group for item in evidence] == [
        ("Equity", "Emerging Markets"),
        ("Equity", "Emerging Markets-Corporates"),
        ("Investment Grade Bond", "Global"),
    ]
    assert all(item.classification_correction_id for item in evidence)


def test_pair_mapping_batch_validates_contract_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, dataset, manifest = _prepared_pair_mapping_target(tmp_path, monkeypatch)
    admit_pair_mapping_correction(target, _pair_request(target, dataset, manifest))
    validation_calls = 0
    validate = pair_mapping.validate_pair_mapping_corrections

    def counted_validate(connection: sqlite3.Connection) -> None:
        nonlocal validation_calls
        validation_calls += 1
        validate(connection)

    monkeypatch.setattr(
        pair_mapping, "validate_pair_mapping_corrections", counted_validate
    )

    evidence = load_construction_instrument_evidence(
        target, _screening(target, dataset)
    )

    assert validation_calls == 1
    assert len(evidence) == 3


def test_phase_e_cohort_selection_uses_effective_pair_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, dataset, manifest = _prepared_pair_mapping_target(tmp_path, monkeypatch)
    admit_pair_mapping_correction(target, _pair_request(target, dataset, manifest))
    expected_isins = {"US0378331005", "US5949181045", "US0231351067"}
    monkeypatch.setattr(nav, "PHASE_E_CURRENCIES", ("EUR",))
    monkeypatch.setattr(nav, "PHASE_E_COHORT_ISINS", {"EUR": expected_isins})
    monkeypatch.setattr(nav, "PHASE_E_SECURITY_COUNT", 3)
    monkeypatch.setattr(nav, "PHASE_E_CUTOFF", date(2026, 9, 22))

    members = nav.select_phase_e_cohorts(target)["EUR"]

    assert {member.isin for member in members} == expected_isins
    assert {member.group for member in members} == {
        ("Equity", "Emerging Markets"),
        ("Equity", "Emerging Markets-Corporates"),
        ("Investment Grade Bond", "Global"),
    }


def test_pair_mapping_replay_and_mismatched_bindings_are_nonmutating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, dataset, manifest = _prepared_pair_mapping_target(tmp_path, monkeypatch)
    request = _pair_request(target, dataset, manifest)
    admit_pair_mapping_correction(target, request)
    admitted_sha256 = sha256(target.read_bytes()).hexdigest()

    replay = admit_pair_mapping_correction(target, request)
    assert replay.replayed is True
    assert sha256(target.read_bytes()).hexdigest() == admitted_sha256

    for changed in (
        replace(request, initial_target_sha256="b" * 64),
        replace(request, reason="Different reason"),
        replace(request, correction_id="DIFFERENT_CORRECTION_ID"),
    ):
        with pytest.raises(ShortlistClassificationCorrectionError):
            admit_pair_mapping_correction(target, changed)
        assert sha256(target.read_bytes()).hexdigest() == admitted_sha256


def test_pair_mapping_rejects_changed_evidence_without_partial_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, dataset, manifest = _prepared_pair_mapping_target(tmp_path, monkeypatch)
    with sqlite3.connect(target) as connection:
        payload = json.loads(
            connection.execute(
                "SELECT source_payload_json FROM shortlist_entry_source_occurrence "
                "WHERE source_row_number=4"
            ).fetchone()[0]
        )
        payload["Eszközosztály"] = "Changed source"
        connection.execute(
            "UPDATE shortlist_entry_source_occurrence SET source_payload_json=? "
            "WHERE source_row_number=4",
            (json.dumps(payload, ensure_ascii=False, sort_keys=True),),
        )
    request = _pair_request(target, dataset, manifest)
    before = sha256(target.read_bytes()).hexdigest()

    with pytest.raises(
        ShortlistClassificationCorrectionError, match="raw source evidence"
    ):
        admit_pair_mapping_correction(target, request)

    assert sha256(target.read_bytes()).hexdigest() == before
    with sqlite3.connect(target) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM sqlite_master "
                "WHERE name='shortlist_classification_pair_mapping_admission'"
            ).fetchone()[0]
            == 0
        )


def test_pair_mapping_same_dataset_reimport_and_changed_dataset_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, dataset, manifest = _prepared_pair_mapping_target(tmp_path, monkeypatch)
    admit_pair_mapping_correction(target, _pair_request(target, dataset, manifest))

    same = stage.integrate_shortlist(
        workbook_directory=tmp_path, target=target, apply=True
    )
    assert same["dataset_fingerprint"] == dataset
    with sqlite3.connect(target) as connection:
        connection.row_factory = sqlite3.Row
        validate_pair_mapping_corrections(connection)

    before = sha256(target.read_bytes()).hexdigest()
    monkeypatch.setattr(
        stage,
        "audit_workbooks",
        lambda _path: {
            "files": [
                _sheet(
                    digest="b" * 64,
                    rows=[
                        _row(
                            2,
                            "US0378331005",
                            ORIGINAL_SUB_ASSET_CLASS,
                            asset_class="Részvény",
                        ),
                        _row(
                            3,
                            "US5949181045",
                            "Fejl?d? piacok-Vállalatok",
                            asset_class="Részvény",
                        ),
                        _row(4, "US0231351067", "Globál", asset_class="Kötvény"),
                    ],
                )
            ]
        },
    )
    with pytest.raises(
        ShortlistClassificationCorrectionError, match="binding|dataset|evidence"
    ):
        stage.integrate_shortlist(
            workbook_directory=tmp_path, target=target, apply=True
        )
    assert sha256(target.read_bytes()).hexdigest() == before


def test_pair_mapping_composes_additional_admission_and_retains_prior_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, dataset, manifest = _prepared_pair_mapping_target(tmp_path, monkeypatch)
    first = _pair_request(target, dataset, manifest)
    admit_pair_mapping_correction(target, first)
    second_manifest = _pair_manifest(
        dataset,
        ("Equity", "Emerging Markets", "Public Equity", "Emerging Markets", 1),
        (
            "Equity",
            "Emerging Markets-Corporates",
            "Public Equity",
            "Emerging Markets-Corporates",
            1,
        ),
        ("Investment Grade Bond", "Global", "Bond", "Global", 1),
        mapping_id="SYNTHETIC_ENGLISH_PAIR_MAPPING_V2",
    )
    second = _pair_request(
        target,
        dataset,
        second_manifest,
        correction_id="SHORTLIST_CLASSIFICATION_ENGLISH_TEST_2",
    )

    result = admit_pair_mapping_correction(target, second)

    assert result.item_count == 3
    with sqlite3.connect(target) as connection:
        connection.row_factory = sqlite3.Row
        stage_three = connection.execute(
            "SELECT effective_asset_class, effective_sub_asset_class "
            "FROM v_shortlist_classification_correction_stage "
            "WHERE application_order=3 ORDER BY source_row_number"
        ).fetchall()
        effective = connection.execute(
            "SELECT effective_asset_class, effective_sub_asset_class "
            "FROM v_effective_shortlist_classification ORDER BY source_row_number"
        ).fetchall()
        assert [tuple(row) for row in stage_three] == [
            ("Equity", "Emerging Markets"),
            ("Equity", "Emerging Markets-Corporates"),
            ("Investment Grade Bond", "Global"),
        ]
        assert [tuple(row) for row in effective] == [
            ("Public Equity", "Emerging Markets"),
            ("Public Equity", "Emerging Markets-Corporates"),
            ("Bond", "Global"),
        ]
        validate_pair_mapping_corrections(connection)


def test_pair_mapping_preserves_null_classifications(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    null_row = _row(5, "US02079K3059", "placeholder", asset_class="Részvény")
    null_row["sub_asset_class"] = None
    cast(dict[str, object], null_row["source_values"])["Aleszközosztály"] = None
    target, integration, _audit = _target(
        tmp_path,
        monkeypatch,
        sheet=_sheet(
            rows=[
                _row(
                    2, "US0378331005", ORIGINAL_SUB_ASSET_CLASS, asset_class="Részvény"
                ),
                _row(
                    3,
                    "US5949181045",
                    "Fejl?d? piacok-Vállalatok",
                    asset_class="Részvény",
                ),
                _row(4, "US0231351067", "Globál", asset_class="Kötvény"),
                null_row,
            ]
        ),
    )
    dataset = str(integration["dataset_fingerprint"])
    admit_classification_correction(target, _request(target, dataset))
    admit_composed_classification_correction(
        target,
        _composition_request(target, dataset, ("Fejl?d? piacok-Vállalatok", 1)),
    )
    manifest = _pair_manifest(
        dataset,
        ("Részvény", EFFECTIVE_SUB_ASSET_CLASS, "Equity", "Emerging Markets", 1),
        (
            "Részvény",
            "Fejlődő piacok-Vállalatok",
            "Equity",
            "Emerging Markets-Corporates",
            1,
        ),
        ("Kötvény", "Globál", "Investment Grade Bond", "Global", 1),
        expected_occurrence_count=4,
    )

    admit_pair_mapping_correction(target, _pair_request(target, dataset, manifest))

    with sqlite3.connect(target) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT original_sub_asset_class, effective_sub_asset_class "
            "FROM v_effective_shortlist_classification WHERE source_row_number=5"
        ).fetchone()
        assert tuple(row) == (None, None)
        validate_pair_mapping_corrections(connection)


def test_reviewed_english_mapping_manifest_is_complete() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    manifest = load_pair_mapping_manifest(
        repository_root
        / "data/knowledge/validated_rules/shortlist_classification_english_mapping_v1.json"
    )

    assert manifest.dataset_fingerprint == (
        "32216038d2f69dbf4c6436e91782025ae117dc59fe466e8a31ebc3719f93e8b2"
    )
    assert len(manifest.entries) == manifest.expected_prior_pair_count == 77
    assert manifest.expected_occurrence_count == 10_833
    assert manifest.expected_result_asset_class_count == 7
    assert manifest.expected_result_sub_asset_class_count == 38
    assert manifest.expected_result_pair_count == 51
    assert manifest.expected_changed_transitions == ((48, 46, 8), (48, 47, 8))
    assert manifest.expected_unchanged_snapshot_count == 17
    assert (
        "Equity",
        "Global - Industrials and Raw Materials",
    ) in {
        (entry.effective_asset_class, entry.effective_sub_asset_class)
        for entry in manifest.entries
    }
