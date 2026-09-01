"""Behavioral tests for the governed capital-conservation reference workflow."""

from __future__ import annotations

import json
import runpy
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

from portfolio_advisor.database.schema.v3 import connect
from portfolio_advisor.objectives import (
    PolicyCapabilityStatus,
    PolicyRegistry,
    PortfolioObjective,
    build_default_policy_registry,
)
from portfolio_advisor.workflows import (
    CapitalConservationWorkflowError,
    RecommendationStatus,
    UserChoiceOption,
    UserChoiceState,
    build_capital_conservation_reference_workflow,
    build_finalist_comparison_policy,
    compare_finalists,
    record_capital_conservation_user_choice,
)
from portfolio_advisor.workflows.models import (
    FinalistKind,
    FinalistProvenance,
    WorkflowFinalist,
)

ROOT = Path(__file__).resolve().parents[1]
_SHORTLIST_TEST = runpy.run_path(str(ROOT / "tests/test_capital_conservation_shortlist.py"))
DATASET_FINGERPRINT = cast(str, _SHORTLIST_TEST["DATASET_FINGERPRINT"])
_build_target = cast(Callable[[Path], tuple[Path, dict[str, str]]], _SHORTLIST_TEST["_build_target"])
POLICY_FINGERPRINT = "d3cc192857459963eab539d93457396756b341ad8941e6c0832cedf7450091ba"


def _build_reference_target(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    target, fingerprints = _build_target(tmp_path)
    retained = tmp_path / "retained-model-source.bin"
    retained.write_bytes(b"synthetic retained model evidence")
    retained_hash = sha256(retained.read_bytes()).hexdigest()
    metric_values = {
        1: {
            "RETURN_1Y": 0.08,
            "SHARPE_RATIO_1Y": 0.9,
            "VOLATILITY_1Y": 0.03,
            "DOWNSIDE_RISK": 0.02,
            "MAXIMUM_DRAWDOWN": -0.05,
        },
        2: {
            "RETURN_1Y": 0.10,
            "SHARPE_RATIO_1Y": 1.0,
            "VOLATILITY_1Y": 0.05,
            "DOWNSIDE_RISK": 0.03,
            "MAXIMUM_DRAWDOWN": -0.10,
        },
    }
    with connect(target) as connection:
        connection.executemany(
            "INSERT INTO portfolio(portfolio_id, portfolio_name, portfolio_type) VALUES (?, ?, 'MODEL')",
            ((1, "Synthetic Model A"), (2, "Synthetic Model B")),
        )
        connection.executemany(
            "INSERT INTO source_sheet(source_sheet_id, source_file_id, sheet_name) VALUES (?, ?, ?)",
            ((3, 1, "modell portfóliók"), (4, 2, "modell portfóliók")),
        )
        metric_ids = {
            str(row["metric_code"]): int(row["metric_id"])
            for row in connection.execute("SELECT metric_id, metric_code FROM metric_definition")
        }
        occurrence_id = 0
        snapshot_id = 0
        for source_sheet_id, snapshot_date in ((3, "2026-01-01"), (4, "2026-02-01")):
            for portfolio_id, instrument_id in ((1, 1), (2, 2)):
                snapshot_id += 1
                occurrence_id += 1
                connection.execute(
                    """INSERT INTO portfolio_snapshot(
                           portfolio_snapshot_id, portfolio_id, snapshot_date, source_sheet_id
                       ) VALUES (?, ?, ?, ?)""",
                    (snapshot_id, portfolio_id, snapshot_date, source_sheet_id),
                )
                connection.execute(
                    """INSERT INTO portfolio_holding_source_occurrence(
                           portfolio_holding_source_occurrence_id, portfolio_snapshot_id,
                           instrument_id, source_sheet_id, source_row_number, reported_weight,
                           observed_product_name, observed_currency_code, observed_currency_risk,
                           source_payload_sha256, source_semantics_status
                       ) VALUES (?, ?, ?, ?, ?, 100.0, ?, 'EUR', 'Fedezve', ?, 'SOURCE_REPORTED')""",
                    (
                        occurrence_id,
                        snapshot_id,
                        instrument_id,
                        source_sheet_id,
                        portfolio_id + 1,
                        f"Synthetic constituent {instrument_id}",
                        sha256(b"{}").hexdigest(),
                    ),
                )
                for code, value in metric_values[instrument_id].items():
                    connection.execute(
                        """INSERT INTO instrument_metric_observation(
                               instrument_id, metric_id, observation_date, value,
                               provenance_type, source_file_id, source_reference
                           ) VALUES (?, ?, ?, ?, 'PROVIDER_REPORTED', ?, ?)""",
                        (
                            instrument_id,
                            metric_ids[code],
                            snapshot_date,
                            value,
                            source_sheet_id - 2,
                            f"OCCURRENCE:{occurrence_id}:{code}",
                        ),
                    )
        connection.execute(
            """INSERT INTO migration_build_manifest(
                   singleton, schema_version, build_version, source_fingerprints_json,
                   ranking_policy_sha256, source_counts_json, target_counts_json,
                   unresolved_semantic_count, equivalence_status, dataset_fingerprint,
                   build_status
               ) VALUES (1, 3, 'MILESTONE_7_MODEL_PORTFOLIO_PARALLEL_V1', ?, ?,
                         '{"source_occurrences":4}',
                         '{"portfolio_holding_source_occurrence":4,"portfolio_snapshot":4}',
                         0, 'EXACT_PASS', ?,
                         'PARALLEL_VALIDATED')""",
            (
                json.dumps({str(retained): retained_hash}, sort_keys=True),
                POLICY_FINGERPRINT,
                "m" * 64,
            ),
        )
        connection.commit()
    return target, fingerprints


def _workflow(
    target: Path,
    fingerprints: dict[str, str],
    *,
    objective: PortfolioObjective | str = PortfolioObjective.CAPITAL_CONSERVATION,
    as_of: date | None = None,
    registry: PolicyRegistry | None = None,
):
    return build_capital_conservation_reference_workflow(
        database_path=target,
        repository_root=ROOT,
        expected_workbook_fingerprints=fingerprints,
        expected_shortlist_manifest_fingerprint=DATASET_FINGERPRINT,
        objective=objective,
        as_of=as_of,
        registry=registry,
    )


def _hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def test_complete_workflow_is_deterministic_read_only_and_awaits_choice(tmp_path: Path) -> None:
    target, fingerprints = _build_reference_target(tmp_path)
    before = _hash(target)
    first = _workflow(target, fingerprints)
    second = _workflow(target, dict(reversed(tuple(fingerprints.items()))))

    assert first.canonical_json() == second.canonical_json()
    assert first.workflow_fingerprint == second.workflow_fingerprint
    assert first.common_as_of_date == date(2026, 2, 1)
    assert first.model_finalist.display_name == "Synthetic Model A"
    assert first.shortlist_finalist.stable_id == "US0378331005"
    assert first.recommendation.status is RecommendationStatus.NO_CLEAR_RECOMMENDATION
    assert first.user_choice_state is UserChoiceState.AWAITING_USER_CHOICE
    assert first.fingerprint_payload()["user_choice"] is None
    assert set(first.valid_choice_options) == set(UserChoiceOption)
    assert first.policy_fingerprint == POLICY_FINGERPRINT
    assert _hash(target) == before


def test_explicit_as_of_uses_latest_common_date_without_future_leakage(tmp_path: Path) -> None:
    target, fingerprints = _build_reference_target(tmp_path)
    result = _workflow(target, fingerprints, as_of=date(2026, 1, 15))
    assert result.common_as_of_date == date(2026, 1, 1)
    assert result.model_finalist.provenance.snapshot_date == date(2026, 1, 1)
    assert result.shortlist_finalist.provenance.snapshot_date == date(2026, 1, 1)
    with pytest.raises(CapitalConservationWorkflowError, match="no common complete"):
        _workflow(target, fingerprints, as_of=date(2025, 12, 31))


def test_policy_registry_does_not_advertise_exploratory_comparison_as_finalist_runtime() -> None:
    policy = build_default_policy_registry(ROOT).resolve_active_policy(
        PortfolioObjective.CAPITAL_CONSERVATION
    )
    assert policy.capabilities.finalist_comparison is PolicyCapabilityStatus.NOT_IMPLEMENTED
    assert policy.capabilities.outcome_success_criteria is PolicyCapabilityStatus.NOT_IMPLEMENTED
    assert policy.fingerprint == POLICY_FINGERPRINT


def test_explicit_user_choice_can_match_disagree_defer_or_decline(tmp_path: Path) -> None:
    target, fingerprints = _build_reference_target(tmp_path)
    workflow = _workflow(target, fingerprints)
    governed_recommendation = compare_finalists(
        replace(
            workflow.model_finalist,
            feature_values=_finalist(
                FinalistKind.MODEL_PORTFOLIO, (0.02, -0.02, 0.1, 0.5, 0.1)
            ).feature_values,
        ),
        replace(
            workflow.shortlist_finalist,
            feature_values=_finalist(
                FinalistKind.SHORTLIST_INSTRUMENT, (0.01, -0.01, 0.2, 1.0, 0.0)
            ).feature_values,
        ),
        build_finalist_comparison_policy(),
    )
    workflow = replace(workflow, recommendation=governed_recommendation)
    matching = record_capital_conservation_user_choice(
        workflow,
        UserChoiceOption.SELECT_SHORTLIST_CANDIDATE,
        expected_workflow_fingerprint=workflow.workflow_fingerprint,
        expected_recommendation_fingerprint=workflow.recommendation.fingerprint,
    )
    disagreement = record_capital_conservation_user_choice(
        workflow,
        UserChoiceOption.SELECT_MODEL_PORTFOLIO,
        expected_workflow_fingerprint=workflow.workflow_fingerprint,
        expected_recommendation_fingerprint=workflow.recommendation.fingerprint,
    )
    deferred = record_capital_conservation_user_choice(
        workflow,
        UserChoiceOption.DEFER,
        expected_workflow_fingerprint=workflow.workflow_fingerprint,
        expected_recommendation_fingerprint=workflow.recommendation.fingerprint,
    )
    declined = record_capital_conservation_user_choice(
        workflow,
        UserChoiceOption.DECLINE,
        expected_workflow_fingerprint=workflow.workflow_fingerprint,
        expected_recommendation_fingerprint=workflow.recommendation.fingerprint,
    )
    assert matching.selected_finalist_id == workflow.shortlist_finalist.stable_id
    assert matching.disagrees_with_recommendation is False
    assert disagreement.selected_finalist_id == workflow.model_finalist.stable_id
    assert disagreement.disagrees_with_recommendation is True
    assert deferred.selected_finalist_id is declined.selected_finalist_id is None
    assert matching.choice_fingerprint == replace(matching).choice_fingerprint
    assert all(
        item.persistence_status.value == "NOT_PERFORMED"
        for item in (matching, disagreement, deferred)
    )


def test_choice_rejects_invalid_cross_workflow_and_tampered_fingerprints(tmp_path: Path) -> None:
    target, fingerprints = _build_reference_target(tmp_path)
    workflow = _workflow(target, fingerprints)
    with pytest.raises(CapitalConservationWorkflowError, match="invalid user choice"):
        record_capital_conservation_user_choice(
            workflow,
            "SELECT_UNKNOWN",
            expected_workflow_fingerprint=workflow.workflow_fingerprint,
            expected_recommendation_fingerprint=workflow.recommendation.fingerprint,
        )
    with pytest.raises(CapitalConservationWorkflowError, match="workflow fingerprint"):
        record_capital_conservation_user_choice(
            workflow,
            UserChoiceOption.DEFER,
            expected_workflow_fingerprint="a" * 64,
            expected_recommendation_fingerprint=workflow.recommendation.fingerprint,
        )
    with pytest.raises(CapitalConservationWorkflowError, match="recommendation fingerprint"):
        record_capital_conservation_user_choice(
            workflow,
            UserChoiceOption.DEFER,
            expected_workflow_fingerprint=workflow.workflow_fingerprint,
            expected_recommendation_fingerprint="b" * 64,
        )


def _finalist(kind: FinalistKind, values: tuple[float | None, ...]) -> WorkflowFinalist:
    names = (
        "annualized_volatility",
        "maximum_drawdown",
        "return_1y",
        "sharpe_ratio",
        "unhedged_allocation",
    )
    return WorkflowFinalist(
        kind=kind,
        stable_id=kind.value,
        database_local_id=1 if kind is FinalistKind.MODEL_PORTFOLIO else 2,
        display_name=kind.value,
        rank=1,
        eligible=True,
        total_score=0.5,
        feature_values=tuple(zip(names, values, strict=True)),
        provenance=FinalistProvenance(
            1, date(2026, 1, 1), "source.xls", "a" * 64, 1, "sheet", (1,), (2,), "b" * 64
        ),
    )


@pytest.mark.parametrize(
    ("model_values", "shortlist_values", "status"),
    [
        ((0.01, -0.01, 0.2, 1.0, 0.0), (0.02, -0.02, 0.1, 0.5, 0.1), RecommendationStatus.RECOMMEND_MODEL_PORTFOLIO),
        ((0.02, -0.02, 0.1, 0.5, 0.1), (0.01, -0.01, 0.2, 1.0, 0.0), RecommendationStatus.RECOMMEND_SHORTLIST_CANDIDATE),
        ((0.01, -0.01, 0.2, 1.0, 0.0), (0.01, -0.01, 0.2, 1.0, 0.0), RecommendationStatus.NO_CLEAR_RECOMMENDATION),
        ((0.01, -0.02, 0.2, 0.5, 0.0), (0.02, -0.01, 0.1, 1.0, 0.1), RecommendationStatus.NO_CLEAR_RECOMMENDATION),
        ((None, -0.01, 0.2, 1.0, 0.0), (0.02, -0.02, 0.1, 0.5, 0.1), RecommendationStatus.INSUFFICIENT_COMPARABLE_EVIDENCE),
    ],
)
def test_comparison_policy_recommendations_ties_and_insufficient_evidence(
    model_values: tuple[float | None, ...],
    shortlist_values: tuple[float | None, ...],
    status: RecommendationStatus,
) -> None:
    result = compare_finalists(
        _finalist(FinalistKind.MODEL_PORTFOLIO, model_values),
        _finalist(FinalistKind.SHORTLIST_INSTRUMENT, shortlist_values),
        build_finalist_comparison_policy(),
    )
    assert result.status is status
    assert "cross_universe_total_score" in result.unavailable_dimensions


def test_comparison_policy_tampering_fails_closed() -> None:
    policy = build_finalist_comparison_policy()
    with pytest.raises(RuntimeError, match="identity or fingerprint mismatch"):
        compare_finalists(
            _finalist(FinalistKind.MODEL_PORTFOLIO, (0.01, -0.01, 0.2, 1.0, 0.0)),
            _finalist(FinalistKind.SHORTLIST_INSTRUMENT, (0.02, -0.02, 0.1, 0.5, 0.1)),
            replace(policy, version="1.0.1"),
        )


def test_dividend_unknown_and_capability_or_policy_tampering_fail_closed(tmp_path: Path) -> None:
    target, fingerprints = _build_reference_target(tmp_path)
    with pytest.raises(CapitalConservationWorkflowError, match="NO_VALIDATED_ACTIVE_POLICY"):
        _workflow(target, fingerprints, objective=PortfolioObjective.DIVIDEND_PORTFOLIO)
    with pytest.raises(CapitalConservationWorkflowError, match="Unknown portfolio objective"):
        _workflow(target, fingerprints, objective="unknown")
    registry = build_default_policy_registry(ROOT)
    capital = registry.resolve_active_policy(PortfolioObjective.CAPITAL_CONSERVATION)
    unavailable = PolicyRegistry(
        policies=(
            replace(
                capital,
                capabilities=replace(
                    capital.capabilities,
                    instrument_screening_ranking=PolicyCapabilityStatus.NOT_IMPLEMENTED,
                ),
            ),
        )
    )
    with pytest.raises(CapitalConservationWorkflowError, match="instrument screening capability"):
        _workflow(target, fingerprints, registry=unavailable)
    stale = PolicyRegistry(policies=(replace(capital, mandate="Stale reviewed metadata"),))
    with pytest.raises(CapitalConservationWorkflowError, match="stale or mismatched"):
        _workflow(target, fingerprints, registry=stale)


@pytest.mark.parametrize(
    ("statement", "message"),
    [
        (
            "UPDATE migration_build_manifest SET build_status='FAILED_NO_PUBLICATION'",
            "manifest is incomplete",
        ),
        (
            "UPDATE migration_build_manifest SET build_version='MILESTONE_7_STALE'",
            "version is stale",
        ),
        ("UPDATE migration_build_manifest SET ranking_policy_sha256='a' || substr(ranking_policy_sha256,2)", "ranking-policy fingerprint"),
        (
            "DELETE FROM shortlist_entry_lineage WHERE rowid=(SELECT min(rowid) FROM shortlist_entry_lineage)",
            "lineage_count mismatch",
        ),
        ("UPDATE shortlist_stage_manifest SET completion_status='FAILED'", "manifest is incomplete"),
    ],
)
def test_manifest_policy_and_lineage_corruption_fail_closed(
    tmp_path: Path, statement: str, message: str
) -> None:
    target, fingerprints = _build_reference_target(tmp_path)
    with sqlite3.connect(target) as connection:
        connection.executescript(statement)
        connection.commit()
    with pytest.raises(CapitalConservationWorkflowError, match=message):
        _workflow(target, fingerprints)


def test_database_integrity_foreign_keys_and_schema_fail_closed(tmp_path: Path) -> None:
    target, fingerprints = _build_reference_target(tmp_path)
    with sqlite3.connect(target) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("UPDATE portfolio_snapshot SET portfolio_id=9999 WHERE portfolio_snapshot_id=1")
        connection.commit()
    with pytest.raises(CapitalConservationWorkflowError, match="foreign_key_check"):
        _workflow(target, fingerprints)

    second = tmp_path / "second"
    second.mkdir()
    target2, fingerprints2 = _build_reference_target(second)
    with sqlite3.connect(target2) as connection:
        connection.execute("ALTER TABLE migration_build_manifest RENAME TO missing_manifest")
        connection.commit()
    with pytest.raises(CapitalConservationWorkflowError, match="incompatible schema"):
        _workflow(target2, fingerprints2)


def test_stale_model_source_and_shortlist_source_fail_closed(tmp_path: Path) -> None:
    target, fingerprints = _build_reference_target(tmp_path)
    with sqlite3.connect(target) as connection:
        source = next(iter(json.loads(connection.execute(
            "SELECT source_fingerprints_json FROM migration_build_manifest"
        ).fetchone()[0])))
    Path(source).write_bytes(b"changed")
    with pytest.raises(CapitalConservationWorkflowError, match="stale model source"):
        _workflow(target, fingerprints)

    second = tmp_path / "second"
    second.mkdir()
    target2, fingerprints2 = _build_reference_target(second)
    fingerprints2["synthetic_20260201.xls"] = "f" * 64
    with pytest.raises(CapitalConservationWorkflowError, match="stale source workbook"):
        _workflow(target2, fingerprints2)
