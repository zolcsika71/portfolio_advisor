from __future__ import annotations

from pathlib import Path

import pytest

from portfolio_advisor.metrics.models import MetricValue, PortfolioMetrics
from portfolio_advisor.ranking.config import RuleConfigurationError, load_ranking_rules
from portfolio_advisor.ranking.normalization import normalize_metric
from portfolio_advisor.ranking.ranking import rank_portfolios

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "data/knowledge/validated_rules/capital_preservation_ranking.yaml"


def _metric(value: float | None, coverage: float = 1.0) -> MetricValue:
    return MetricValue(value, coverage, value is not None)


def _candidate(name: str, *, allocation: float = 100.0, volatility: float = 0.04, drawdown: float = -0.05, returns: float = 0.02) -> PortfolioMetrics:
    return PortfolioMetrics(
        portfolio_name=name,
        allocation_total=allocation,
        return_1y=_metric(returns),
        annualized_volatility=_metric(volatility),
        maximum_drawdown=_metric(drawdown),
        downside_deviation=_metric(None),
        sharpe_ratio=_metric(0.5),
        unhedged_allocation=_metric(0.0),
        currency_concentration=_metric(1.0),
    )


def _rules_file(tmp_path: Path, status: str = "approved") -> Path:
    path = tmp_path / "rules.yaml"
    path.write_text(RULES.read_text(encoding="utf-8").replace("status: approved", f"status: {status}", 1), encoding="utf-8")
    return path


def test_normalization_handles_identical_values_and_outlier() -> None:
    assert normalize_metric({"A": 1.0, "B": 1.0}, "LOWER_BETTER") == {"A": 1.0, "B": 1.0}
    result = normalize_metric({"A": 0.01, "B": 1.01}, "LOWER_BETTER")
    assert result == {"A": 1.0, "B": 0.0}


def test_ranking_is_stable_and_reports_eligibility_rejection(tmp_path: Path) -> None:
    rules = load_ranking_rules(_rules_file(tmp_path))
    ranking, warnings = rank_portfolios(
        [
            _candidate("Beta"),
            _candidate("Alpha"),
            _candidate("Invalid", allocation=99.0),
        ],
        rules,
    )
    assert warnings == ()
    assert [item.metrics.portfolio_name for item in ranking] == ["Alpha", "Beta", "Invalid"]
    assert [item.rank for item in ranking] == [1, 2, None]
    assert "allocation total" in ranking[-1].rejection_reasons[0]
    assert ranking[0].total_score == pytest.approx(1.0)
    assert len(ranking[0].contributions) == 5


def test_config_rejects_proposed_unless_explicitly_allowed(tmp_path: Path) -> None:
    path = _rules_file(tmp_path, status="proposed")
    with pytest.raises(RuleConfigurationError, match="proposed"):
        load_ranking_rules(path)
    rules = load_ranking_rules(path, allow_proposed=True)
    assert rules.status == "proposed"
    assert rules.version == "1.0.1"
