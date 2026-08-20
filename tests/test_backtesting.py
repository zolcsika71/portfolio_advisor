from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from portfolio_advisor.backtesting.models import BacktestEligibility
from portfolio_advisor.backtesting.service import WalkForwardBacktester
from portfolio_advisor.database.repository import ModelPortfolioRepository
from portfolio_advisor.history.repository import HistoricalPortfolioRepository


class _FixtureEligibleGate:
    """Controlled fixture evidence: all fixture holdings are explicitly usable."""

    def evaluate(self, *, history, portfolio_name, window) -> BacktestEligibility:  # type: ignore[no-untyped-def]
        return BacktestEligibility(
            eligible=True,
            status="BACKTEST_ELIGIBLE",
            policy_id="FIXTURE_FULLY_RESOLVABLE",
            coverage_status="COMPLETE",
            resolvable_weight=100.0,
            unresolved_weight=0.0,
            blocking_constituents=(),
            constituent_weights=(),
            diagnostics_allowed=False,
        )


def _rules(path: Path) -> Path:
    rules = path / "rules.yaml"
    rules.write_text(
        (Path(__file__).resolve().parents[1] / "data/knowledge/validated_rules/capital_preservation_ranking.yaml")
        .read_text(encoding="utf-8")
        .replace('version: "1.0.1"', 'version: "backtest-test-1"', 1)
        .replace("status: proposed", "status: approved", 1)
        .replace("minimum_metric_coverage: 0.70", "minimum_metric_coverage: 1", 1),
        encoding="utf-8",
    )
    return rules


def _database(path: Path, *, include_nav: bool = True, tied: bool = False) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        '''CREATE TABLE model_portfolios (
            "Date" TEXT, "Portfolio Name" TEXT, "Product" TEXT, "ISIN" TEXT,
            "Allocation (%)" REAL, "Currency" TEXT, "Currency Risk" TEXT,
            "1 Year" REAL, "1Y Sharpe Ratio" REAL, "1Y Volatility" REAL,
            "Downside Risk" REAL, "Maximum Drawdown" REAL
        )'''
    )
    beta_volatility = 0.01 if tied else 0.08
    rows = [
        ("2025/01/01", "Alpha", "Fund", "A", 100.0, "HUF", "Hedged", 0.02, 0.5, 0.01, 0.01, -0.02),
        ("2025/01/01", "Beta", "Fund", "B", 100.0, "HUF", "Hedged", 0.02, 0.5, beta_volatility, 0.01, -0.10),
        # These future snapshot values reverse the ranking and must not affect 2025-01-01.
        ("2025/04/01", "Alpha", "Fund", "A", 100.0, "HUF", "Hedged", 0.02, 0.5, 0.09, 0.01, -0.10),
        ("2025/04/01", "Beta", "Fund", "B", 100.0, "HUF", "Hedged", 0.02, 0.5, 0.01, 0.01, -0.02),
    ]
    connection.executemany('INSERT INTO model_portfolios VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', rows)
    if include_nav:
        connection.execute(
            'CREATE TABLE portfolio_nav_history ("Date" TEXT, "Portfolio Name" TEXT, "Net Asset Value" REAL)'
        )
        connection.executemany(
            'INSERT INTO portfolio_nav_history VALUES (?, ?, ?)',
            [
                ("2025/01/01", "Alpha", 100.0),
                ("2025/01/31", "Alpha", 110.0),
                ("2025/03/02", "Alpha", 105.0),
                ("2025/04/01", "Alpha", 115.0),
                ("2025/05/01", "Alpha", 500.0),
                ("2025/01/01", "Beta", 100.0),
                ("2025/01/31", "Beta", 105.0),
                ("2025/03/02", "Beta", 110.0),
                ("2025/04/01", "Beta", 112.0),
            ],
        )
    connection.commit()
    connection.close()


def _backtester(database: Path, rules: Path) -> WalkForwardBacktester:
    history = HistoricalPortfolioRepository(ModelPortfolioRepository(database))
    return WalkForwardBacktester(history, rules, eligibility_gate=_FixtureEligibleGate())


def test_walk_forward_prevents_future_snapshot_leakage_and_computes_metrics(tmp_path: Path) -> None:
    database = tmp_path / "history.sqlite"
    _database(database)
    result = _backtester(database, _rules(tmp_path)).run(
        horizon_days=90, evaluation_dates=[date(2025, 1, 1)]
    )

    period = result.periods[0]
    assert period.selected_portfolio == "Alpha"
    assert period.selected_rank == 1
    assert period.selected_score == pytest.approx(1.0)
    assert period.rule_set_version == "backtest-test-1"
    assert not period.proposed_rules_explicitly_enabled
    assert period.realized_forward_metrics is not None
    assert period.realized_forward_metrics.total_return == pytest.approx(0.15)
    assert period.realized_forward_metrics.annualized_return == pytest.approx(
        (1.15 ** (365 / 90)) - 1.0
    )
    assert period.realized_forward_metrics.return_observation_count == 3
    assert period.realized_forward_metrics.maximum_drawdown == pytest.approx(-(0.05 / 1.10))
    assert period.realized_forward_metrics.annualized_volatility is not None
    assert period.realized_forward_metrics.historical_var is not None
    assert period.realized_forward_metrics.historical_cvar is not None


def test_baselines_and_aggregation_are_deterministic(tmp_path: Path) -> None:
    database = tmp_path / "history.sqlite"
    _database(database)
    backtester = _backtester(database, _rules(tmp_path))
    first = backtester.run(horizon_days=90, evaluation_dates=[date(2025, 1, 1)])
    second = backtester.run(horizon_days=90, evaluation_dates=[date(2025, 1, 1)])

    assert first == second
    baselines = {item.strategy: item for item in first.periods[0].baseline_results}
    assert baselines["equal_weight_eligible"].portfolio_names == ("Alpha", "Beta")
    assert baselines["lowest_volatility"].portfolio_names == ("Alpha",)
    assert baselines["lowest_drawdown"].portfolio_names == ("Alpha",)
    assert first.aggregate.complete_period_count == 1
    assert first.aggregate.incomplete_period_count == 0
    assert first.aggregate.average_realized_return == pytest.approx(0.15)
    assert first.aggregate.median_realized_return == pytest.approx(0.15)
    assert first.aggregate.selection_frequency == {"Alpha": 1}
    assert first.aggregate.hit_rate_vs_baselines["lowest_volatility"] == 1.0


def test_tied_candidates_use_existing_stable_ranking_tie_break(tmp_path: Path) -> None:
    database = tmp_path / "history.sqlite"
    _database(database, tied=True)

    result = _backtester(database, _rules(tmp_path)).run(
        horizon_days=90, evaluation_dates=[date(2025, 1, 1)]
    )

    assert result.periods[0].selected_portfolio == "Alpha"


def test_missing_nav_data_and_incomplete_horizons_are_explicit(tmp_path: Path) -> None:
    no_nav_database = tmp_path / "no-nav.sqlite"
    _database(no_nav_database, include_nav=False)
    no_nav = _backtester(no_nav_database, _rules(tmp_path)).run(
        horizon_days=90, evaluation_dates=[date(2025, 1, 1)]
    )
    assert no_nav.periods[0].realized_forward_metrics is None
    assert no_nav.periods[0].incomplete_period_reason == (
        "NAV history is not stored; recomputed forward metrics are unavailable"
    )
    assert no_nav.aggregate.incomplete_period_count == 1

    database = tmp_path / "history.sqlite"
    _database(database)
    incomplete = _backtester(database, _rules(tmp_path)).run(
        horizon_days=180, evaluation_dates=[date(2025, 1, 1)]
    )
    assert incomplete.periods[0].realized_forward_metrics is None
    assert "exact evaluation-date or horizon-end" in (incomplete.periods[0].incomplete_period_reason or "")


def test_backtest_rejects_unsupported_horizon(tmp_path: Path) -> None:
    database = tmp_path / "history.sqlite"
    _database(database)

    with pytest.raises(ValueError, match="horizon_days"):
        _backtester(database, _rules(tmp_path)).run(horizon_days=91)
