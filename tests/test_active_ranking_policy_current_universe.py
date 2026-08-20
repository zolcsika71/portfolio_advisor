"""Regression coverage for active-policy validation on the checked-in universe."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from portfolio_advisor.advisor.active_policy_validation import (
    ActivePolicyValidationError,
    build_active_policy_validation,
)

ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "database/model_portfolio.sqlite"
RULES = ROOT / "data/knowledge/validated_rules/capital_preservation_ranking.yaml"
CONTRACT = ROOT / "data/audit/capital_preservation_ranking_policy_contract.json"
METHODOLOGY = ROOT / "data/audit/capital_preservation_metrics_ranking_validation.json"
STRICT = ROOT / "data/audit/strict_backtest_pipeline_validation.json"


def test_active_policy_current_universe_is_deterministic_and_read_only() -> None:
    result = build_active_policy_validation(
        database_path=DATABASE,
        rules_path=RULES,
        contract_path=CONTRACT,
        methodology_path=METHODOLOGY,
        strict_pipeline_path=STRICT,
    )

    assert result["validation_status"] == "ACTIVE_RANKING_POLICY_CURRENT_UNIVERSE_VALIDATED"
    assert result["policy_identity"]["activation_state"] == "ACTIVE"
    assert result["contract_consistency"]["result"] == "PASS"
    assert result["determinism"]["result"] == "PASS"
    assert result["capital_preservation_alignment"]["result"] == "PASS"
    assert result["current_universe"]["selected_portfolio"] == "PB Konzervatív MultiCCY"
    assert result["failures"] == []


def test_active_policy_validation_rejects_nonactive_contract(tmp_path: Path) -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    contract["final_policy_status"] = "RANKING_POLICY_APPROVAL_READY"
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(contract), encoding="utf-8")

    with pytest.raises(ActivePolicyValidationError, match="not active"):
        build_active_policy_validation(
            database_path=DATABASE,
            rules_path=RULES,
            contract_path=path,
            methodology_path=METHODOLOGY,
            strict_pipeline_path=STRICT,
        )
