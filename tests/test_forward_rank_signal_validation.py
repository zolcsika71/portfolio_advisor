from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import cast

import pytest

from portfolio_advisor.advisor.forward_rank_signal_validation import (
    STATUS_INSUFFICIENT,
    ForwardRankSignalValidationError,
    admit_official_label,
    build_forward_rank_signal_validation,
    classify_signal,
    intervals_overlap,
    label_availability,
    pairwise_outcome,
    rank_bucket,
)

ROOT = Path(__file__).resolve().parents[1]


def _label_row(*, available: str = "True", result_type: str = "OFFICIAL_BACKTEST") -> dict[str, str]:
    row = {
        "decision_date": "2025-01-01", "portfolio_id": "P", "portfolio_name": "P",
        "portfolio_currency": "EUR", "ranking_eligible": "True", "result_type": result_type,
        "source_or_backtest_reference": "strict/backtest/1", "horizon": "90",
        "label_90d_available": available, "label_90d_status": result_type,
        "label_90d_start_date": "2025-01-01", "label_90d_end_date": "2025-04-01",
    }
    for metric in (
        "forward_return", "forward_annualized_return", "forward_volatility", "forward_sharpe",
        "forward_mdd", "forward_var", "forward_cvar",
    ):
        row[f"{metric}_90d"] = "" if metric != "forward_return" else "0.03"
    return row


def test_label_admission_is_official_exact_and_never_zero_filled() -> None:
    admitted = admit_official_label(_label_row(), 90)
    assert admitted is not None
    with pytest.raises(ForwardRankSignalValidationError, match="OFFICIAL_BACKTEST"):
        admit_official_label(_label_row(result_type="DIAGNOSTICS_ONLY"), 90)
    with pytest.raises(ForwardRankSignalValidationError, match="OFFICIAL_BACKTEST"):
        admit_official_label(_label_row(result_type="BACKTEST_REJECTED"), 90)

    missing = _label_row(available="False")
    for key in tuple(missing):
        if key.startswith("forward_"):
            missing[key] = ""
    assert admit_official_label(missing, 90) is None
    missing["forward_return_90d"] = "0"
    with pytest.raises(ForwardRankSignalValidationError, match="unavailable label"):
        admit_official_label(missing, 90)


def test_label_join_timing_and_horizon_fail_closed() -> None:
    wrong_horizon = _label_row()
    wrong_horizon["horizon"] = "180"
    with pytest.raises(ForwardRankSignalValidationError, match="horizon"):
        admit_official_label(wrong_horizon, 90)
    wrong_date = _label_row()
    wrong_date["label_90d_start_date"] = "2024-12-31"
    with pytest.raises(ForwardRankSignalValidationError, match="invalid forward interval"):
        admit_official_label(wrong_date, 90)


def test_buckets_pairwise_directions_signal_and_overlap_are_deterministic() -> None:
    assert [rank_bucket(rank, 5) for rank in range(1, 6)] == ["TOP", "TOP", "MIDDLE", "BOTTOM", "BOTTOM"]
    assert rank_bucket(1, 2) == "TOP"
    assert rank_bucket(2, 2) == "BOTTOM"
    assert pairwise_outcome(-0.02, -0.10, "HIGHER_BETTER") == "HIGHER_RANK_WINS"
    assert pairwise_outcome(0.03, 0.05, "LOWER_BETTER") == "HIGHER_RANK_WINS"
    assert pairwise_outcome(0.02, 0.01, "HIGHER_BETTER") == "HIGHER_RANK_WINS"
    assert classify_signal(higher_rank_wins=11, lower_rank_wins=1, ties=0) == "POSITIVE_SIGNAL"
    assert classify_signal(higher_rank_wins=7, lower_rank_wins=6, ties=0) == "WEAK_POSITIVE_SIGNAL"
    assert classify_signal(higher_rank_wins=6, lower_rank_wins=7, ties=0) == "NO_CLEAR_SIGNAL"
    assert classify_signal(higher_rank_wins=3, lower_rank_wins=9, ties=0) == "INVERSE_SIGNAL"
    assert classify_signal(higher_rank_wins=2, lower_rank_wins=1, ties=0) == "INSUFFICIENT_SAMPLE"
    assert intervals_overlap(date(2025, 1, 1), date(2025, 4, 1), date(2025, 2, 1), date(2025, 5, 1))
    assert not intervals_overlap(date(2025, 1, 1), date(2025, 4, 1), date(2025, 4, 1), date(2025, 7, 1))


def test_label_availability_retains_missing_denominator() -> None:
    missing = _label_row(available="False")
    for key in tuple(missing):
        if key.startswith("forward_"):
            missing[key] = ""
    for horizon in (180, 365):
        missing.update({
            f"label_{horizon}d_available": "False",
            f"label_{horizon}d_status": "OFFICIAL_BACKTEST_INCOMPLETE_NAV",
            f"label_{horizon}d_start_date": "2025-01-01",
            f"label_{horizon}d_end_date": "2025-01-01",
        })
        for metric in (
            "forward_return", "forward_annualized_return", "forward_volatility", "forward_sharpe",
            "forward_mdd", "forward_var", "forward_cvar",
        ):
            missing[f"{metric}_{horizon}d"] = ""
    unavailable = {**missing, "decision_date": "2025-02-01", "portfolio_id": "Q"}
    ineligible = {**missing, "ranking_eligible": "False", "portfolio_id": "R"}
    availability = label_availability([missing, unavailable, ineligible])
    assert availability["90"]["possible_eligible_observations"] == 2
    assert availability["90"]["admitted_official_labels"] == 0
    assert availability["90"]["unavailable_or_rejected_labels"] == 2


def test_actual_dataset_reports_deterministic_insufficient_official_evidence() -> None:
    kwargs = {
        "dataset_path": ROOT / "data/features/point_in_time_portfolio_features.csv",
        "dataset_manifest_path": ROOT / "data/audit/point_in_time_portfolio_feature_dataset.json",
        "database_path": ROOT / "database/model_portfolio.sqlite",
        "rules_path": ROOT / "data/knowledge/validated_rules/capital_preservation_ranking.yaml",
        "contract_path": ROOT / "data/audit/capital_preservation_ranking_policy_contract.json",
        "strict_pipeline_path": ROOT / "data/audit/strict_backtest_pipeline_validation.json",
        "methodology_path": ROOT / "data/audit/capital_preservation_metrics_ranking_validation.json",
        "current_universe_path": ROOT / "data/audit/active_ranking_policy_current_universe_validation.json",
        "temporal_path": ROOT / "data/audit/active_ranking_policy_temporal_stability.json",
    }
    first = build_forward_rank_signal_validation(**kwargs)
    second = build_forward_rank_signal_validation(**kwargs)
    assert first == second
    assert first["validation_status"] == STATUS_INSUFFICIENT
    assert first["point_in_time_integrity"] == "NO_LOOKAHEAD"
    availability = cast(dict[str, dict[str, object]], first["label_availability"])
    assert availability["90"]["admitted_official_labels"] == 0
