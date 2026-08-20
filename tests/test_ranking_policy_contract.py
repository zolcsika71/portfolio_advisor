"""Focused contract, fail-closed, and policy-readiness regression tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from portfolio_advisor.metrics.models import MetricValue, PortfolioMetrics
from portfolio_advisor.ranking.config import RuleConfigurationError, load_ranking_rules
from portfolio_advisor.ranking.normalization import normalize_metric
from portfolio_advisor.ranking.policy_contract import build_policy_contract
from portfolio_advisor.ranking.ranking import rank_portfolios

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "data/knowledge/validated_rules/capital_preservation_ranking.yaml"
METHODOLOGY = ROOT / "data/audit/capital_preservation_metrics_ranking_validation.json"
STRICT = ROOT / "data/audit/strict_backtest_pipeline_validation.json"


def _write_policy(tmp_path: Path, mutate) -> Path:  # type: ignore[no-untyped-def]
    policy = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    policy["status"] = "approved"
    mutate(policy)
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(policy, sort_keys=False), encoding="utf-8")
    return path


def _metric(value: float | None) -> MetricValue:
    return MetricValue(value, 1.0, value is not None)


def _candidate(
    name: str, *, returns: float = 0.02, volatility: float = 0.04, drawdown: float = -0.05
) -> PortfolioMetrics:
    return PortfolioMetrics(
        portfolio_name=name,
        allocation_total=100.0,
        return_1y=_metric(returns),
        annualized_volatility=_metric(volatility),
        maximum_drawdown=_metric(drawdown),
        downside_deviation=_metric(0.01),
        sharpe_ratio=_metric(0.5),
        unhedged_allocation=_metric(0.0),
        currency_concentration=_metric(1.0),
    )


def test_schema_accepts_current_policy_and_rejects_missing_or_unknown_feature(tmp_path: Path) -> None:
    assert load_ranking_rules(RULES, allow_proposed=True).schema_version == 2
    missing = _write_policy(tmp_path, lambda value: value["feature_definitions"][0].pop("source"))
    with pytest.raises(RuleConfigurationError, match="feature definition"):
        load_ranking_rules(missing)
    unknown = _write_policy(
        tmp_path, lambda value: value["scoring"]["metrics"].update({"future_return": {"weight": 0, "direction": "HIGHER_BETTER"}})
    )
    with pytest.raises(RuleConfigurationError, match="unknown or missing scoring features"):
        load_ranking_rules(unknown)


def test_schema_rejects_duplicate_direction_weight_and_threshold_errors(tmp_path: Path) -> None:
    duplicate = _write_policy(
        tmp_path, lambda value: value["feature_definitions"].append(value["feature_definitions"][0].copy())
    )
    with pytest.raises(RuleConfigurationError, match="duplicate feature"):
        load_ranking_rules(duplicate)
    direction = _write_policy(tmp_path, lambda value: value["scoring"]["metrics"]["maximum_drawdown"].update({"direction": "lower"}))
    with pytest.raises(RuleConfigurationError, match="invalid direction"):
        load_ranking_rules(direction)
    weight = _write_policy(tmp_path, lambda value: value["scoring"]["metrics"]["return_1y"].update({"weight": float("nan")}))
    with pytest.raises(RuleConfigurationError, match="finite"):
        load_ranking_rules(weight)
    threshold = _write_policy(tmp_path, lambda value: value["thresholds"][0].pop("operator"))
    with pytest.raises(RuleConfigurationError, match="threshold"):
        load_ranking_rules(threshold)


@pytest.mark.parametrize("weight", [0.09, 0.11, float("inf")])
def test_weight_total_and_nonfinite_weights_fail_closed(tmp_path: Path, weight: float) -> None:
    path = _write_policy(tmp_path, lambda value: value["scoring"]["metrics"]["return_1y"].update({"weight": weight}))
    with pytest.raises(RuleConfigurationError):
        load_ranking_rules(path)


def test_mdd_direction_dominance_and_catastrophic_regression() -> None:
    rules = load_ranking_rules(RULES, allow_proposed=True)
    ranking, _ = rank_portfolios(
        [
            _candidate("Better MDD", drawdown=-0.02),
            _candidate("Worse MDD", drawdown=-0.10),
            _candidate("Crash", returns=0.10, volatility=0.50, drawdown=-0.50),
        ],
        rules,
    )
    assert [item.metrics.portfolio_name for item in ranking if item.rank] == [
        "Better MDD", "Worse MDD", "Crash"
    ]
    assert normalize_metric({"better": -0.02, "worse": -0.10}, "HIGHER_BETTER")["better"] == 1.0


def test_missing_required_risk_is_rejected_and_never_treated_as_zero() -> None:
    rules = load_ranking_rules(RULES, allow_proposed=True)
    missing = _candidate("Missing risk")
    object.__setattr__(missing, "maximum_drawdown", MetricValue(None, 0.0, False))
    ranking, _ = rank_portfolios([_candidate("Complete"), missing], rules)
    rejected = next(item for item in ranking if item.metrics.portfolio_name == "Missing risk")
    assert not rejected.eligible
    assert rejected.total_score is None
    assert "unavailable" in rejected.rejection_reasons[0]


def test_normalization_tie_single_candidate_and_outlier_preserve_direction() -> None:
    assert normalize_metric({"only": 0.1}, "LOWER_BETTER") == {"only": 1.0}
    lower = normalize_metric({"safe": 0.01, "middle": 0.02, "outlier": 2.0}, "LOWER_BETTER")
    assert lower["safe"] > lower["middle"] > lower["outlier"]
    higher = normalize_metric({"safe": -0.02, "worse": -0.10}, "HIGHER_BETTER")
    assert higher["safe"] > higher["worse"]


def test_tie_is_deterministic_and_empty_eligible_set_has_no_fallback() -> None:
    rules = load_ranking_rules(RULES, allow_proposed=True)
    tied, _ = rank_portfolios([_candidate("Beta"), _candidate("Alpha")], rules)
    assert [item.metrics.portfolio_name for item in tied] == ["Alpha", "Beta"]
    incomplete = _candidate("Incomplete")
    object.__setattr__(incomplete, "annualized_volatility", MetricValue(None, 0.0, False))
    rejected, _ = rank_portfolios([incomplete], rules)
    assert rejected[0].rank is None
    assert not rejected[0].eligible


def test_contract_activates_approved_policy_and_caveats_do_not_block(tmp_path: Path) -> None:
    active = build_policy_contract(rules_path=RULES, methodology_path=METHODOLOGY, strict_pipeline_path=STRICT)
    assert active["final_policy_status"] == "RANKING_POLICY_ACTIVE"
    assert active["policy_identity"]["activation_state"] == "ACTIVE"
    proposed = _write_policy(tmp_path, lambda value: value.update({"status": "proposed"}))
    ready = build_policy_contract(rules_path=proposed, methodology_path=METHODOLOGY, strict_pipeline_path=STRICT)
    assert ready["final_policy_status"] == "RANKING_POLICY_APPROVAL_READY"
    methodology = json.loads(METHODOLOGY.read_text(encoding="utf-8"))
    methodology["caveats"].append("A non-blocking caveat")
    caveat_path = tmp_path / "caveat.json"
    caveat_path.write_text(json.dumps(methodology), encoding="utf-8")
    caveat_only = build_policy_contract(rules_path=RULES, methodology_path=caveat_path, strict_pipeline_path=STRICT)
    assert caveat_only["final_policy_status"] == "RANKING_POLICY_ACTIVE"
    methodology["look_ahead_validation"]["result"] = "FAIL"
    blocked_path = tmp_path / "blocked.json"
    blocked_path.write_text(json.dumps(methodology), encoding="utf-8")
    blocked = build_policy_contract(rules_path=RULES, methodology_path=blocked_path, strict_pipeline_path=STRICT)
    assert blocked["final_policy_status"] == "RANKING_POLICY_BLOCKED"
    assert any(item["code"] == "NO_LOOK_AHEAD" for item in blocked["approval_blockers"])
