from __future__ import annotations

import sqlite3
from pathlib import Path

from portfolio_advisor.advisor.service import CapitalPreservationAdvisor
from portfolio_advisor.database.repository import ModelPortfolioRepository


def _create_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        '''CREATE TABLE model_portfolios (
            "Date" TEXT, "Portfolio Name" TEXT, "Product" TEXT, "ISIN" TEXT,
            "Allocation (%)" REAL, "Currency" TEXT, "Currency Risk" TEXT,
            "1 Year" REAL, "1Y Sharpe Ratio" REAL, "1Y Volatility" REAL,
            "Downside Risk" REAL, "Maximum Drawdown" REAL
        )'''
    )
    connection.executemany(
        'INSERT INTO model_portfolios VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        [
            ("2025/01/01", "Old", "Fund", "O", 100.0, "HUF", "Hedged", 0.01, 0.1, 0.1, 0.1, -0.2),
            ("2025/02/01", "Best", "Fund", "B", 100.0, "HUF", "Hedged", 0.02, 0.5, 0.02, 0.01, -0.02),
            ("2025/02/01", "Other", "Fund", "X", 100.0, "HUF", "Unhedged", 0.04, 0.5, 0.08, 0.01, -0.10),
        ],
    )
    connection.commit()
    connection.close()


def _rules(path: Path) -> Path:
    rules = path / "rules.yaml"
    rules.write_text(
        '''version: "test-1"
status: approved
purpose: test
assumptions: [test assumption]
source_references: [test source]
eligibility:
  target_allocation: 100
  allocation_tolerance: 0.01
  minimum_metric_coverage: 1
  required_metrics: [annualized_volatility, maximum_drawdown]
scoring:
  metrics:
    maximum_drawdown: {weight: 0.5, direction: lower}
    annualized_volatility: {weight: 0.5, direction: lower}
''',
        encoding="utf-8",
    )
    return rules


def test_advisor_selects_latest_date_candidate_without_mutating_database(tmp_path: Path) -> None:
    database = tmp_path / "portfolio.sqlite"
    _create_database(database)
    advisor = CapitalPreservationAdvisor(ModelPortfolioRepository(database), _rules(tmp_path))
    result = advisor.evaluate(alternative_count=1)
    assert result.observation_date is not None
    assert result.observation_date.isoformat() == "2025-02-01"
    assert [item.portfolio_name for item in result.calculated_metrics] == ["Best", "Other"]
    assert result.selected_portfolio is not None
    assert result.selected_portfolio.metrics.portfolio_name == "Best"
    assert result.selected_portfolio.rank == 1
    assert [item.metrics.portfolio_name for item in result.alternative_top_ranked] == ["Other"]
    assert result.rule_set_version == "test-1"
    assert not result.proposed_rules_explicitly_enabled
    assert sqlite3.connect(database).execute("SELECT count(*) FROM model_portfolios").fetchone()[0] == 3


def test_advisor_reports_unreviewed_rules_without_selecting(tmp_path: Path) -> None:
    database = tmp_path / "portfolio.sqlite"
    _create_database(database)
    rules = _rules(tmp_path)
    rules.write_text(rules.read_text(encoding="utf-8").replace("approved", "proposed", 1), encoding="utf-8")
    result = CapitalPreservationAdvisor(ModelPortfolioRepository(database), rules).evaluate()
    assert result.selected_portfolio is None
    assert len(result.calculated_metrics) == 2
    assert result.rules_status == "unavailable"
    assert result.rule_set_version == "unavailable"
    assert not result.proposed_rules_explicitly_enabled
    assert "proposed" in result.warnings[0]


def test_advisor_fails_closed_when_validated_rules_are_unavailable(tmp_path: Path) -> None:
    database = tmp_path / "portfolio.sqlite"
    _create_database(database)
    missing_rules = tmp_path / "missing-rules.yaml"

    result = CapitalPreservationAdvisor(ModelPortfolioRepository(database), missing_rules).evaluate()

    assert result.selected_portfolio is None
    assert result.ranking == ()
    assert result.rules_status == "unavailable"
    assert result.rule_set_version == "unavailable"
    assert "Could not read rules" in result.warnings[0]
