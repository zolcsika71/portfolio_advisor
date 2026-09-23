"""Synthetic-only tests for model-portfolio consolidation Phase 1."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import asdict, replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from portfolio_advisor.database.migrations.model_portfolio_dry_run import (
    equivalence_report,
)
from portfolio_advisor.database.model_portfolio_phase1 import (
    AnalyticalMnbOtcRepository,
    AnalyticalModelPortfolioRepository,
    MnbEvidenceBinding,
    ModelOccurrenceBinding,
    ModelPortfolioPhase1Error,
    admit_mnb_otc_evidence,
    admit_workbook_normalization,
    install_phase1_schema,
    normalize_parsed_fields,
    source_dataset_fingerprint,
    validate_phase1_contracts,
    validate_phase1_schema,
)
from portfolio_advisor.database.repository import (
    ModelPortfolioReader,
    ModelPortfolioRepository,
)
from portfolio_advisor.database.schema.v3 import connect, initialize_schema
from portfolio_advisor.history.mnb_otc import MnbOtcObservation
from portfolio_advisor.metrics.portfolio import calculate_all_portfolio_metrics
from portfolio_advisor.ranking.config import load_ranking_rules
from portfolio_advisor.ranking.ranking import rank_portfolios
from scripts.admit_model_portfolio_phase1 import _temporary_database, main
from tests.fixtures.model_portfolio_phase1_fixture import (
    FIXTURE_DATE,
    create_phase1_databases,
)


def _rules_path() -> Path:
    return (
        Path(__file__).parents[1]
        / "data/knowledge/validated_rules/capital_preservation_ranking.yaml"
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mnb_observation() -> MnbOtcObservation:
    return MnbOtcObservation(
        isin="HU0000554795",
        instrument_name="Synthetic one-year government security",
        currency="HUF",
        period_start=date(2024, 11, 25),
        period_end=date(2024, 12, 1),
        nominal_value_huf_thousand=Decimal("1000.0"),
        purchase_value_huf_thousand=Decimal("1020.0"),
        average_price=Decimal("102.000000"),
        minimum_price=Decimal("101.900000"),
        maximum_price=Decimal("102.100000"),
        transaction_count=2,
        source_document="evidence/mnb/synthetic.pdf",
        source_document_hash="d" * 64,
    )


def test_parser_normalizes_only_numeric_zero_and_rejects_raw_text_zero() -> None:
    values: dict[str, object] = {
        "Sustainability": "1: ESG-Minimum Standard",
        "YTD": 0,
        "3 Years": -0.0,
        "5 Years": None,
        "3Y Sharpe Ratio": Decimal("0.8"),
        "5Y Sharpe Ratio": 1.1,
        "3Y Volatility": 0.06,
        "Information Ratio": -0.2,
    }

    parsed = normalize_parsed_fields(values)

    assert parsed.ytd is None
    assert parsed.return_3y is None
    assert parsed.return_5y is None
    assert parsed.sharpe_ratio_3y == 0.8
    with pytest.raises(ModelPortfolioPhase1Error, match="raw strings"):
        normalize_parsed_fields({**values, "YTD": "0"})
    with pytest.raises(ModelPortfolioPhase1Error, match="differ"):
        normalize_parsed_fields(
            {key: value for key, value in values.items() if key != "YTD"}
        )


def test_additive_schema_is_idempotent_preserves_existing_contracts_and_fails_closed(
    tmp_path: Path,
) -> None:
    database = tmp_path / "schema.sqlite"
    with connect(database) as connection:
        initialize_schema(connection)
        connection.execute(
            "INSERT INTO schema_feature_contract VALUES('SYNTHETIC_EXISTING_FEATURE',1,?)",
            ("e" * 64,),
        )
        connection.execute(
            """INSERT INTO migration_build_manifest(
                   singleton, schema_version, build_version,
                   source_fingerprints_json, ranking_policy_sha256,
                   source_counts_json, target_counts_json,
                   unresolved_semantic_count, equivalence_status,
                   dataset_fingerprint, database_fingerprint, build_status
               ) VALUES(1,3,'synthetic','{}',?,'{}','{}',0,'EXACT_PASS',?,NULL,
                        'PARALLEL_VALIDATED')""",
            ("f" * 64, "1" * 64),
        )
        manifest_before = tuple(
            connection.execute("SELECT * FROM migration_build_manifest").fetchone()
        )
        install_phase1_schema(connection)
        first_objects = tuple(
            connection.execute(
                "SELECT type,name,sql FROM sqlite_master WHERE name LIKE 'model_%' ORDER BY type,name"
            )
        )
        install_phase1_schema(connection)
        assert first_objects == tuple(
            connection.execute(
                "SELECT type,name,sql FROM sqlite_master WHERE name LIKE 'model_%' ORDER BY type,name"
            )
        )
        assert (
            tuple(
                connection.execute("SELECT * FROM migration_build_manifest").fetchone()
            )
            == manifest_before
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM schema_feature_contract WHERE feature_id='SYNTHETIC_EXISTING_FEATURE'"
            ).fetchone()[0]
            == 1
        )

    partial = tmp_path / "partial.sqlite"
    with connect(partial) as connection:
        initialize_schema(connection)
        connection.execute("CREATE TABLE model_import_batch(unexpected INTEGER)")
        before = tuple(
            connection.execute("SELECT name FROM sqlite_master ORDER BY name")
        )
        with pytest.raises(ModelPortfolioPhase1Error, match="partially"):
            install_phase1_schema(connection)
        assert (
            tuple(connection.execute("SELECT name FROM sqlite_master ORDER BY name"))
            == before
        )

    with connect(database) as connection:
        connection.execute("DROP TRIGGER model_import_batch_immutable_update")
        with pytest.raises(ModelPortfolioPhase1Error, match="absent or partial"):
            validate_phase1_schema(connection)


def test_admission_preserves_occurrences_and_legacy_ranking_with_exact_replay(
    tmp_path: Path,
) -> None:
    legacy, analytical, request = create_phase1_databases(tmp_path)
    with sqlite3.connect(analytical) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        original_payloads = tuple(
            connection.execute(
                "SELECT source_payload_json FROM portfolio_holding_source_occurrence ORDER BY source_row_number"
            )
        )
        install_phase1_schema(connection)
        admission_result = admit_workbook_normalization(connection, request)
        assert admission_result.replayed is False
        assert admission_result.item_count == 4
        assert (
            tuple(
                connection.execute(
                    "SELECT source_payload_json FROM portfolio_holding_source_occurrence ORDER BY source_row_number"
                )
            )
            == original_payloads
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM portfolio_holding_source_occurrence"
            ).fetchone()[0]
            == 5
        )
        assert (
            connection.execute("SELECT count(*) FROM portfolio_holding").fetchone()[0]
            == 0
        )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                """UPDATE instrument_metric_observation SET value=value + 1
                   WHERE source_reference LIKE 'MODEL_PHASE1:%'"""
            )
        assert (
            connection.execute(
                """SELECT count(*) FROM portfolio_holding_source_occurrence
               WHERE source_semantics_status='UNRESOLVED_DUPLICATE_SEMANTICS'"""
            ).fetchone()[0]
            == 4
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM model_source_occurrence_typed_extension"
            ).fetchone()[0]
            == 4
        )
        assert (
            connection.execute(
                """SELECT count(*) FROM instrument_metric_observation AS observation
               JOIN metric_definition AS definition
                 ON definition.metric_id=observation.metric_id
               WHERE definition.metric_code='YTD'
                 AND observation.source_reference LIKE 'MODEL_PHASE1:%'"""
            ).fetchone()[0]
            == 0
        )

    before_replay = _sha256(analytical)
    with sqlite3.connect(analytical) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        replay = admit_workbook_normalization(connection, request)
    assert replay.replayed is True
    assert _sha256(analytical) == before_replay

    legacy_reader = ModelPortfolioRepository(legacy)
    analytical_reader = AnalyticalModelPortfolioRepository(
        analytical, authority_epoch_id=request.authority.epoch_id
    )
    assert isinstance(legacy_reader, ModelPortfolioReader)
    assert isinstance(analytical_reader, ModelPortfolioReader)
    assert analytical_reader.observation_dates() == (FIXTURE_DATE,)
    assert analytical_reader.latest_observation_date() == FIXTURE_DATE
    assert analytical_reader.load_holdings(FIXTURE_DATE) == legacy_reader.load_holdings(
        FIXTURE_DATE
    )
    missing_date = date(2000, 1, 1)
    assert (
        analytical_reader.load_holdings(missing_date)
        == legacy_reader.load_holdings(missing_date)
        == []
    )
    typed = analytical_reader.load_typed_occurrences(FIXTURE_DATE)
    assert len(typed) == 4
    assert {row.source_semantics_status for row in typed} == {
        "UNRESOLVED_DUPLICATE_SEMANTICS"
    }
    report = equivalence_report(legacy_reader, analytical_reader, _rules_path())
    comparison = report[FIXTURE_DATE.isoformat()]
    assert comparison["exact"] is True
    assert comparison["maximum_numeric_delta"] == 0.0
    assert comparison["source_occurrence_count"] == {"legacy": 4, "schema_v3": 4}
    assert comparison["rank_order"] == [
        "PB Kiegyensúlyozott USD",
        "PB Konzervatív USD",
    ]
    ranking, _warnings = rank_portfolios(
        calculate_all_portfolio_metrics(analytical_reader.load_holdings(FIXTURE_DATE)),
        load_ranking_rules(_rules_path()),
    )
    assert [item.rank for item in ranking] == [1, 2]
    assert ranking[0].total_score == ranking[1].total_score


def test_mismatched_replays_and_changed_evidence_roll_back_without_mutation(
    tmp_path: Path,
) -> None:
    _legacy, analytical, request = create_phase1_databases(tmp_path)
    with sqlite3.connect(analytical) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        install_phase1_schema(connection)
        admit_workbook_normalization(connection, request)

    cases = (
        replace(request, filename="changed.xls"),
        replace(
            request,
            items=(
                replace(request.items[0], portfolio_name="Wrong portfolio"),
                *request.items[1:],
            ),
        ),
        replace(
            request,
            admission_id="CHANGED_HASH_FOR_SAME_DATE",
            batch_id="CHANGED_HASH_BATCH",
            source_file_sha256="9" * 64,
            items=tuple(
                replace(item, source_file_sha256="9" * 64) for item in request.items
            ),
        ),
    )
    for changed in cases:
        before = _sha256(analytical)
        with sqlite3.connect(analytical) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            with pytest.raises(ModelPortfolioPhase1Error):
                admit_workbook_normalization(connection, changed)
        assert _sha256(analytical) == before

    with sqlite3.connect(analytical) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM model_workbook_admission"
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute("SELECT count(*) FROM model_import_batch").fetchone()[0]
            == 1
        )


def test_ordered_multiple_admissions_validate_each_projection_prefix(
    tmp_path: Path,
) -> None:
    _legacy, analytical, initial_request = create_phase1_databases(tmp_path)
    with sqlite3.connect(analytical) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        file_id = int(
            connection.execute(
                """INSERT INTO source_file(filename,sha256,source_type,source_date)
                   VALUES('synthetic_model_20240918.xls',?,'MODEL_XLS','2024-09-18')
                   RETURNING source_file_id""",
                ("2" * 64,),
            ).fetchone()[0]
        )
        sheet_id = int(
            connection.execute(
                """INSERT INTO source_sheet(source_file_id,sheet_name)
                   VALUES(?,?) RETURNING source_sheet_id""",
                (file_id, initial_request.source_sheet_name),
            ).fetchone()[0]
        )
        portfolio_id = int(
            connection.execute(
                "SELECT portfolio_id FROM portfolio WHERE portfolio_name=?",
                ("PB Kiegyensúlyozott USD",),
            ).fetchone()[0]
        )
        instrument_id = int(
            connection.execute("SELECT min(instrument_id) FROM instrument").fetchone()[
                0
            ]
        )
        snapshot_id = int(
            connection.execute(
                """INSERT INTO portfolio_snapshot(portfolio_id,snapshot_date,source_sheet_id)
                   VALUES(?,'2024-09-18',?) RETURNING portfolio_snapshot_id""",
                (portfolio_id, sheet_id),
            ).fetchone()[0]
        )
        payload_sha = "3" * 64
        occurrence_id = int(
            connection.execute(
                """INSERT INTO portfolio_holding_source_occurrence(
                       portfolio_snapshot_id,instrument_id,source_sheet_id,
                       source_row_number,reported_weight,observed_product_name,
                       observed_currency_code,observed_currency_risk,
                       observed_asset_class,observed_sub_asset_class,
                       source_payload_json,source_payload_sha256,source_semantics_status
                   ) VALUES(?,?,?,2,100.0,'Synthetic later row','USD','Unhedged',
                            'Bond','Global','{}',?,'SOURCE_REPORTED')
                   RETURNING portfolio_holding_source_occurrence_id""",
                (snapshot_id, instrument_id, sheet_id, payload_sha),
            ).fetchone()[0]
        )
        dataset_fingerprint = source_dataset_fingerprint(connection)
        authority = replace(
            initial_request.authority,
            baseline_dataset_fingerprint=dataset_fingerprint,
        )
        first = replace(
            initial_request,
            authority=authority,
            expected_source_dataset_fingerprint=dataset_fingerprint,
        )
        fields = normalize_parsed_fields(
            {
                "Sustainability": None,
                "YTD": 0.02,
                "3 Years": None,
                "5 Years": None,
                "3Y Sharpe Ratio": None,
                "5Y Sharpe Ratio": None,
                "3Y Volatility": None,
                "Information Ratio": None,
            }
        )
        second_item = ModelOccurrenceBinding(
            source_occurrence_id=occurrence_id,
            source_file_sha256="2" * 64,
            source_sheet_name=initial_request.source_sheet_name,
            source_row_number=2,
            snapshot_date="2024-09-18",
            portfolio_name="PB Kiegyensúlyozott USD",
            isin="IE00B7KFL990",
            source_payload_sha256=payload_sha,
            parsed_fields=fields,
        )
        second = replace(
            first,
            admission_id="SYNTHETIC_MODEL_WORKBOOK_2024_09_18",
            batch_id="SYNTHETIC_MODEL_BATCH_2",
            filename="synthetic_model_20240918.xls",
            source_file_sha256="2" * 64,
            snapshot_date="2024-09-18",
            items=(second_item,),
        )
        install_phase1_schema(connection)
        assert admit_workbook_normalization(connection, first).replayed is False
        assert admit_workbook_normalization(connection, second).replayed is False
        validate_phase1_contracts(connection, expected_epoch_id=authority.epoch_id)
        assert [
            int(row[0])
            for row in connection.execute(
                "SELECT batch_sequence FROM model_import_batch ORDER BY batch_sequence"
            )
        ] == [1, 2]
    before = _sha256(analytical)
    with sqlite3.connect(analytical) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        assert admit_workbook_normalization(connection, second).replayed is True
    assert _sha256(analytical) == before


def test_stale_source_binding_is_rejected_by_adapter(tmp_path: Path) -> None:
    _legacy, analytical, request = create_phase1_databases(tmp_path)
    with sqlite3.connect(analytical) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        install_phase1_schema(connection)
        admit_workbook_normalization(connection, request)
        connection.execute("UPDATE source_file SET sha256=?", ("9" * 64,))
    reader = AnalyticalModelPortfolioRepository(
        analytical, authority_epoch_id=request.authority.epoch_id
    )
    with pytest.raises(ModelPortfolioPhase1Error, match="binding"):
        reader.load_holdings(FIXTURE_DATE)


def test_dedicated_mnb_contract_preserves_non_nav_semantics_and_replay(
    tmp_path: Path,
) -> None:
    database = tmp_path / "mnb.sqlite"
    with connect(database) as connection:
        initialize_schema(connection)
        install_phase1_schema(connection)
        observation = _mnb_observation()
        binding = MnbEvidenceBinding(
            portable_evidence_role="evidence/mnb/synthetic.pdf",
            authorization_reference="synthetic-MNB-authorization",
        )
        assert admit_mnb_otc_evidence(connection, observation, binding) is True
        assert admit_mnb_otc_evidence(connection, observation, binding) is False
        before = tuple(
            connection.execute("SELECT * FROM model_mnb_otc_evidence_observation")
        )
        with pytest.raises(ModelPortfolioPhase1Error, match="conflicting"):
            admit_mnb_otc_evidence(
                connection,
                replace(observation, average_price=Decimal("102.050000")),
                binding,
            )
        assert (
            tuple(
                connection.execute("SELECT * FROM model_mnb_otc_evidence_observation")
            )
            == before
        )
        validate_phase1_contracts(connection)
    stored = AnalyticalMnbOtcRepository(database).observations("HU0000554795")
    assert stored == (_mnb_observation(),)
    assert stored[0].as_dict()["nav_equivalent"] is False
    assert stored[0].as_dict()["backtest_return_series_approved"] is False


def test_phase1_command_rejects_path_aliases_and_non_temporary_databases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allowed_root = tmp_path / "allowed"
    retained_root = tmp_path / "retained"
    allowed_root.mkdir()
    retained_root.mkdir()
    retained = retained_root / "retained.sqlite"
    retained.write_bytes(b"synthetic retained database")
    monkeypatch.setattr(
        "scripts.admit_model_portfolio_phase1.tempfile.gettempdir",
        lambda: str(allowed_root),
    )

    with pytest.raises(ModelPortfolioPhase1Error, match="below"):
        _temporary_database(retained)

    direct_symlink = allowed_root / "direct.sqlite"
    direct_symlink.symlink_to(retained)
    with pytest.raises(ModelPortfolioPhase1Error, match="regular"):
        _temporary_database(direct_symlink)

    escaped_parent = allowed_root / "escaped"
    escaped_parent.symlink_to(retained_root, target_is_directory=True)
    with pytest.raises(ModelPortfolioPhase1Error, match="below"):
        _temporary_database(escaped_parent / retained.name)

    hard_link = allowed_root / "hard-link.sqlite"
    os.link(retained, hard_link)
    with pytest.raises(ModelPortfolioPhase1Error, match="hard-linked"):
        _temporary_database(hard_link)

    temporary = allowed_root / "candidate.sqlite"
    temporary.write_bytes(b"synthetic")
    assert _temporary_database(temporary) == temporary.resolve()


def test_phase1_command_admits_an_exact_synthetic_json_request(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _legacy, database, request = create_phase1_databases(tmp_path)
    payload = asdict(request)
    for item in payload["items"]:
        fields = item["parsed_fields"]
        item["parsed_fields"] = {
            "Sustainability": fields["sustainability"],
            "YTD": fields["ytd"],
            "3 Years": fields["return_3y"],
            "5 Years": fields["return_5y"],
            "3Y Sharpe Ratio": fields["sharpe_ratio_3y"],
            "5Y Sharpe Ratio": fields["sharpe_ratio_5y"],
            "3Y Volatility": fields["volatility_3y"],
            "Information Ratio": fields["information_ratio"],
        }
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(payload), encoding="utf-8")

    assert main(["--database", str(database), "--request", str(request_path)]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["admission_id"] == request.admission_id
    assert output["item_count"] == 4
    assert output["replayed"] is False
    with sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM model_workbook_admission_item"
            ).fetchone()[0]
            == 4
        )
