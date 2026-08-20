from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from portfolio_advisor.advisor.forward_validation_strategy import (
    OPTIMIZATION_NOT_READY,
    StrategyEvidence,
    build_forward_validation_strategy_reassessment,
    classify_validation_strategy,
    optimization_readiness,
)


def _write(path: Path, value: dict[str, object]) -> Path:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    return path


def _inputs(root: Path) -> dict[str, Path]:
    return {
        "freeze": _write(root / "freeze.json", {"validation_status": "PORTFOLIO_NAV_RECONSTRUCTION_FROZEN_UNRESOLVED"}),
        "methodology": _write(root / "methodology.json", {"validation_status": "PORTFOLIO_NAV_METHODOLOGY_BLOCKED"}),
        "labels": _write(root / "labels.json", {"available_label_count": 0, "candidate_label_count": 2}),
        "strict": _write(root / "strict.json", {"dataset": {"official_eligible_windows": 1, "rejected_windows": 1}}),
        "features": _write(root / "features.json", {"dataset_status": "POINT_IN_TIME_FEATURE_DATASET_VALIDATED_WITH_CAVEATS"}),
        "temporal": _write(root / "temporal.json", {"validation_status": "ACTIVE_POLICY_TEMPORAL_STABILITY_VALIDATED_WITH_CAVEATS"}),
        "current": _write(root / "current.json", {"validation_status": "ACTIVE_RANKING_POLICY_CURRENT_UNIVERSE_VALIDATED"}),
    }


def test_strategy_classifier_covers_every_fail_closed_status() -> None:
    assert classify_validation_strategy(StrategyEvidence(False, False, False, False, False)) == "NOT_APPLICABLE"
    assert classify_validation_strategy(StrategyEvidence(True, True, False, False, False)) == "BLOCKED"
    assert classify_validation_strategy(StrategyEvidence(True, False, False, True, False)) == "DIAGNOSTIC_ONLY"
    assert classify_validation_strategy(StrategyEvidence(True, False, False, False, True)) == "APPROVED_WITH_CAVEATS"
    assert classify_validation_strategy(StrategyEvidence(True, False, False, False, False)) == "APPROVED_VALIDATION_PATH"


def test_reassessment_preserves_performance_boundary_and_optimization_block(tmp_path: Path) -> None:
    paths = _inputs(tmp_path)
    payload = build_forward_validation_strategy_reassessment(
        repository_root=tmp_path,
        freeze_path=paths["freeze"],
        methodology_path=paths["methodology"],
        label_store_path=paths["labels"],
        strict_validation_path=paths["strict"],
        feature_dataset_path=paths["features"],
        temporal_policy_path=paths["temporal"],
        current_policy_path=paths["current"],
    )

    matrix = cast(list[dict[str, object]], payload["strategy_matrix"])
    strategies = {item["strategy"]: item for item in matrix}
    assert strategies["PORTFOLIO_RETURN_BACKTEST_VALIDATION"]["status"] == "BLOCKED"
    assert strategies["CONSTITUENT_LEVEL_FORWARD_SIGNAL_VALIDATION"]["status"] == "DIAGNOSTIC_ONLY"
    assert strategies["CONSTITUENT_LEVEL_FORWARD_SIGNAL_VALIDATION"]["portfolio_performance_claim_allowed"] is False
    assert payload["optimization_readiness"] == OPTIMIZATION_NOT_READY
    assert payload["is_policy_optimization_currently_approved"] is False


def test_diagnostic_evidence_cannot_enable_portfolio_performance_optimization() -> None:
    assert optimization_readiness(official_portfolio_label_count=0, diagnostic_evidence_only=False) == OPTIMIZATION_NOT_READY
    assert optimization_readiness(official_portfolio_label_count=10, diagnostic_evidence_only=True) == OPTIMIZATION_NOT_READY
