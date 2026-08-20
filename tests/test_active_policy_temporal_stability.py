"""Focused tests for active-policy temporal stability diagnostics."""

from __future__ import annotations

import json
from pathlib import Path

from portfolio_advisor.advisor.active_policy_validation import (
    build_active_policy_validation,
)
from portfolio_advisor.advisor.temporal_policy_validation import (
    _transitions,
    _winner_history,
    build_temporal_policy_validation,
)

ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "database/model_portfolio.sqlite"
RULES = ROOT / "data/knowledge/validated_rules/capital_preservation_ranking.yaml"
CONTRACT = ROOT / "data/audit/capital_preservation_ranking_policy_contract.json"
METHODOLOGY = ROOT / "data/audit/capital_preservation_metrics_ranking_validation.json"
STRICT = ROOT / "data/audit/strict_backtest_pipeline_validation.json"


def _summary(day: str, winner: str, rows: list[dict[str, object]]) -> dict[str, object]:
    return {
        "date": day,
        "selected_portfolio": winner,
        "total_candidates": len(rows),
        "ordered_eligible_ranking": [row["portfolio_name"] for row in rows if row["eligible"]],
        "ranking": rows,
    }


def _row(name: str, rank: int, score: float, *, eligible: bool = True) -> dict[str, object]:
    return {
        "portfolio_name": name,
        "eligible": eligible,
        "rank": rank if eligible else None,
        "total_score": score if eligible else None,
        "raw_feature_values": {"maximum_drawdown": -0.02},
        "normalized_feature_values": {"maximum_drawdown": 1.0},
    }


def test_winner_and_adjacent_turnover_diagnostics_are_deterministic() -> None:
    summaries = [
        _summary("2025-01-01", "A", [_row("A", 1, 1.0), _row("B", 2, 0.0)]),
        _summary("2025-02-01", "A", [_row("B", 1, 1.0), _row("A", 2, 0.0)]),
        _summary("2025-03-01", "B", [_row("B", 1, 1.0), _row("C", 2, 0.0)]),
    ]

    winners = _winner_history(summaries)
    transitions = _transitions(summaries)

    assert winners["winner_changes"] == 1
    assert winners["longest_winner_streak"] == 2
    assert transitions["rank_turnover"]["maximum_rank_change"] == 1
    assert transitions["top_k_stability"]["1"][0]["overlap"] == 0
    assert transitions["candidate_set_turnover"][1]["entries"] == ["C"]
    assert transitions["candidate_set_turnover"][1]["exits"] == ["A"]


def test_temporal_validation_reconciles_the_dynamic_current_universe(tmp_path: Path) -> None:
    """The latest temporal summary must reconcile to a freshly derived universe."""
    current_universe = build_active_policy_validation(
        database_path=DATABASE,
        rules_path=RULES,
        contract_path=CONTRACT,
        methodology_path=METHODOLOGY,
        strict_pipeline_path=STRICT,
    )
    current_path = tmp_path / "current_universe.json"
    current_path.write_text(json.dumps(current_universe), encoding="utf-8")
    result = build_temporal_policy_validation(
        database_path=DATABASE,
        rules_path=RULES,
        contract_path=CONTRACT,
        methodology_path=METHODOLOGY,
        strict_pipeline_path=STRICT,
        current_universe_path=current_path,
    )

    assert result["validation_status"] == "ACTIVE_POLICY_TEMPORAL_STABILITY_VALIDATED_WITH_CAVEATS"
    assert result["point_in_time_integrity"]["result"] == "NO_LOOKAHEAD"
    assert result["determinism"]["result"] == "PASS"
    assert result["latest_date_reconciliation"]["result"]
    assert result["critical_failures"] == []
    assert all(value is True for value in result["strict_pipeline_regressions"].values())
    assert result == build_temporal_policy_validation(
        database_path=DATABASE,
        rules_path=RULES,
        contract_path=CONTRACT,
        methodology_path=METHODOLOGY,
        strict_pipeline_path=STRICT,
        current_universe_path=current_path,
    )
