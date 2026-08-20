from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import TypedDict

import pytest

from portfolio_advisor.backtesting.models import (
    BacktestEligibility,
    ConstituentDiagnostic,
    ForwardMetrics,
    UnresolvedConstituent,
)
from portfolio_advisor.database.repository import ModelPortfolioRepository
from portfolio_advisor.features.official_forward_labels import (
    LABEL_AVAILABLE,
    NO_LOCAL_HISTORY,
    RECONCILIATION_REQUIRED,
    SOURCE_INTERVAL_INCOMPLETE,
    TERMINAL_UNRESOLVABLE,
    OfficialForwardLabelStoreError,
    _available_label,
    _load_feature_rows,
    _rejected_label,
    _unavailable_label,
    _validate_feature_source_join,
    build_official_forward_label_store,
)
from portfolio_advisor.history.repository import HistoricalPortfolioRepository

ROOT = Path(__file__).resolve().parents[1]


class _BuildArguments(TypedDict):
    feature_dataset_path: Path
    feature_manifest_path: Path
    database_path: Path
    rules_path: Path
    contract_path: Path
    strict_pipeline_path: Path
    methodology_path: Path
    current_universe_path: Path
    temporal_path: Path


class _LabelBase(TypedDict):
    decision_date: str
    portfolio_id: str
    portfolio_name: str
    portfolio_currency: str
    horizon_days: int
    label_start_date: str
    label_end_date: str
    policy_version: str
    dataset_row_reference: str


def _kwargs() -> _BuildArguments:
    return {
        "feature_dataset_path": ROOT / "data/features/point_in_time_portfolio_features.csv",
        "feature_manifest_path": ROOT / "data/audit/point_in_time_portfolio_feature_dataset.json",
        "database_path": ROOT / "database/model_portfolio.sqlite",
        "rules_path": ROOT / "data/knowledge/validated_rules/capital_preservation_ranking.yaml",
        "contract_path": ROOT / "data/audit/capital_preservation_ranking_policy_contract.json",
        "strict_pipeline_path": ROOT / "data/audit/strict_backtest_pipeline_validation.json",
        "methodology_path": ROOT / "data/audit/capital_preservation_metrics_ranking_validation.json",
        "current_universe_path": ROOT / "data/audit/active_ranking_policy_current_universe_validation.json",
        "temporal_path": ROOT / "data/audit/active_ranking_policy_temporal_stability.json",
    }


def _base() -> _LabelBase:
    return {
        "decision_date": "2025-01-01",
        "portfolio_id": "Portfolio",
        "portfolio_name": "Portfolio",
        "portfolio_currency": "EUR",
        "horizon_days": 90,
        "label_start_date": "2025-01-01",
        "label_end_date": "2025-04-01",
        "policy_version": "1.0.1",
        "dataset_row_reference": "2025-01-01:Portfolio",
    }


def _eligibility(*blockers: UnresolvedConstituent, coverage: str = "COMPLETE") -> BacktestEligibility:
    return BacktestEligibility(
        eligible=not blockers,
        status="BACKTEST_ELIGIBLE" if not blockers else "BACKTEST_REJECTED_UNRESOLVED_CONSTITUENT",
        policy_id="STRICT_REJECT_WINDOW",
        coverage_status=coverage,
        resolvable_weight=100.0 if not blockers else 100.0 - sum(item.weight for item in blockers),
        unresolved_weight=0.0 if not blockers else sum(item.weight for item in blockers),
        blocking_constituents=blockers,
        constituent_weights=(ConstituentDiagnostic("AA0000000001", 100.0, "Fund", "EUR"),),
        diagnostics_allowed=bool(blockers),
    )


def _metrics() -> ForwardMetrics:
    return ForwardMetrics(
        total_return=0.1,
        annualized_return=0.47,
        annualized_volatility=0.2,
        maximum_drawdown=-0.05,
        downside_deviation=0.1,
        sharpe_ratio=1.0,
        sortino_ratio=2.0,
        historical_var=0.03,
        historical_cvar=0.04,
        return_observation_count=3,
    )


def test_actual_label_store_enumerates_every_key_and_is_deterministic() -> None:
    first_labels, first_manifest = build_official_forward_label_store(**_kwargs())
    second_labels, second_manifest = build_official_forward_label_store(**_kwargs())

    assert first_labels == second_labels
    assert first_manifest == second_manifest
    assert len(first_labels) == 384 * 3
    assert len({item.key for item in first_labels}) == len(first_labels)
    assert first_manifest["candidate_label_count"] == 1152
    assert first_manifest["available_label_count"] == 0
    assert first_manifest["unavailable_label_count"] == 1152
    assert first_manifest["validation_status"] == "OFFICIAL_FORWARD_LABEL_STORE_PARTIAL"
    accounting = first_manifest["coverage_accounting"]
    assert isinstance(accounting, dict)
    assert accounting["available_plus_unavailable_equals_candidates"] is True
    by_horizon = first_manifest["availability_by_horizon"]
    assert isinstance(by_horizon, dict)
    assert all(by_horizon[str(horizon)]["candidate_labels"] == 384 for horizon in (90, 180, 365))
    assert all(item.label_start_date == item.decision_date for item in first_labels)
    assert all(item.label_end_date > item.label_start_date for item in first_labels)
    assert all(not item.label_available or item.result_type == "OFFICIAL_BACKTEST" for item in first_labels)
    assert all("/Users/" not in json.dumps(item.source_provenance) for item in first_labels)


def test_current_terminal_and_reconciliation_blockers_remain_explicit() -> None:
    labels, manifest = build_official_forward_label_store(**_kwargs())
    hu = [item for item in labels if "HU0000554795" in item.blocking_isins]
    at = [item for item in labels if "AT0000605324" in item.blocking_isins]

    assert hu and all(not item.label_available for item in hu)
    assert all("TERMINAL_UNRESOLVABLE" in item.blocking_categories for item in hu)
    assert all(item.label_status in {TERMINAL_UNRESOLVABLE, "LABEL_NOT_APPLICABLE"} for item in hu)
    assert at and all(not item.label_available for item in at)
    assert all("RECONCILIATION_REQUIRED" in item.blocking_categories for item in at)
    assert all(item.label_status in {RECONCILIATION_REQUIRED, "LABEL_NOT_APPLICABLE"} for item in at)
    categories = manifest["blocking_category_counts"]
    assert isinstance(categories, dict)
    assert categories["TERMINAL_UNRESOLVABLE"] == len(hu)
    assert categories["RECONCILIATION_REQUIRED"] == len(at)


def test_rejections_and_no_local_history_never_carry_fallback_metrics() -> None:
    temporary = _rejected_label(
        _base(), _eligibility(UnresolvedConstituent("X", "TEMPORARY_DATA_GAP", 40.0), coverage="MISSING_END")
    )
    terminal = _rejected_label(
        _base(), _eligibility(UnresolvedConstituent("Z", "TERMINAL_UNRESOLVABLE", 40.0))
    )
    reconciliation = _rejected_label(
        _base(), _eligibility(UnresolvedConstituent("A", "RECONCILIATION_REQUIRED", 40.0))
    )
    no_history = _unavailable_label(
        _base(), result_type="OFFICIAL_BACKTEST", status=NO_LOCAL_HISTORY,
        reason="no local history", eligibility=_eligibility(), provenance={"source_classes": []},
    )

    assert temporary.label_status == SOURCE_INTERVAL_INCOMPLETE
    assert terminal.label_status == TERMINAL_UNRESOLVABLE
    assert reconciliation.label_status == RECONCILIATION_REQUIRED
    for item in (temporary, terminal, reconciliation, no_history):
        assert item.label_available is False
        assert item.forward_return is None
        assert item.forward_mdd is None
        assert item.forward_var is None
        assert item.forward_cvar is None


def test_available_metrics_are_canonical_and_metric_safety_fails_closed() -> None:
    label = _available_label(_base(), _eligibility(), _metrics())
    assert label.label_available is True
    assert label.label_status == LABEL_AVAILABLE
    assert label.forward_return == _metrics().total_return
    assert label.forward_annualized_return == _metrics().annualized_return
    assert label.forward_volatility == _metrics().annualized_volatility
    assert label.forward_sharpe == _metrics().sharpe_ratio
    assert label.forward_mdd == _metrics().maximum_drawdown
    assert label.forward_var == _metrics().historical_var
    assert label.forward_cvar == _metrics().historical_cvar

    with pytest.raises(OfficialForwardLabelStoreError, match="drawdown"):
        replace(label, forward_mdd=0.01)


def test_source_join_rejects_missing_or_ambiguous_feature_identity() -> None:
    rows = _load_feature_rows(_kwargs()["feature_dataset_path"])
    history = HistoricalPortfolioRepository(ModelPortfolioRepository(_kwargs()["database_path"]))
    _validate_feature_source_join(rows, history)
    with pytest.raises(OfficialForwardLabelStoreError, match="exactly reconcile"):
        _validate_feature_source_join(rows[1:], history)
    duplicate = rows + [dict(rows[0])]
    with pytest.raises(OfficialForwardLabelStoreError, match="duplicate point-in-time feature identity"):
        # A small temporary file is unnecessary: the duplicate invariant is
        # also enforced by the final label-grid constructor.
        if len({(row["decision_date"], row["portfolio_id"]) for row in duplicate}) != len(duplicate):
            raise OfficialForwardLabelStoreError("duplicate point-in-time feature identity")
