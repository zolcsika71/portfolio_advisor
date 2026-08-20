from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from portfolio_advisor.database.repository import ModelPortfolioRepository
from portfolio_advisor.history.models import HistoricalDataError
from portfolio_advisor.history.repository import HistoricalPortfolioRepository


def _database(path: Path, *, malformed_nav_date: bool = False) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        '''CREATE TABLE model_portfolios (
            "Date" TEXT, "Portfolio Name" TEXT, "Product" TEXT, "ISIN" TEXT,
            "Allocation (%)" REAL, "Currency" TEXT, "Currency Risk" TEXT,
            "1 Year" REAL, "1Y Sharpe Ratio" REAL, "1Y Volatility" REAL,
            "Downside Risk" REAL, "Maximum Drawdown" REAL
        )'''
    )
    rows = [
        ("2025/01/01", "Alpha", "Fund", "A", 100.0, "HUF", "Hedged", 0.02, 0.5, 0.01, 0.01, -0.02),
        ("2025/01/01", "Beta", "Fund", "B", 100.0, "HUF", "Hedged", 0.02, 0.5, 0.08, 0.01, -0.10),
        ("2025/04/01", "Alpha", "Fund", "A", 100.0, "HUF", "Hedged", 0.02, 0.5, 0.09, 0.01, -0.10),
        ("2025/04/01", "Beta", "Fund", "B", 100.0, "HUF", "Hedged", 0.02, 0.5, 0.01, 0.01, -0.02),
    ]
    connection.executemany('INSERT INTO model_portfolios VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', rows)
    connection.execute(
        'CREATE TABLE portfolio_nav_history ("Date" TEXT, "Portfolio Name" TEXT, "Net Asset Value" REAL)'
    )
    nav_rows = [
        ("2025/01/01", "Alpha", 100.0),
        ("2025/01/31", "Alpha", 110.0),
        ("2025/03/02", "Alpha", 105.0),
        ("2025/04/01", "Alpha", 115.0),
        ("2025/01/01", "Beta", 100.0),
        ("2025/01/31", "Beta", 105.0),
        ("2025/03/02", "Beta", 110.0),
        ("2025/04/01", "Beta", 112.0),
    ]
    if malformed_nav_date:
        nav_rows.append(("not-a-date", "Alpha", 120.0))
    connection.executemany('INSERT INTO portfolio_nav_history VALUES (?, ?, ?)', nav_rows)
    connection.commit()
    connection.close()


def _history(path: Path) -> HistoricalPortfolioRepository:
    return HistoricalPortfolioRepository(ModelPortfolioRepository(path))


def test_historical_dates_are_chronological_and_holdings_are_point_in_time(tmp_path: Path) -> None:
    database = tmp_path / "history.sqlite"
    _database(database)
    history = _history(database)

    assert history.observation_dates() == (date(2025, 1, 1), date(2025, 4, 1))
    assert [item.volatility_1y for item in history.holdings_at(date(2025, 1, 1))] == [0.01, 0.08]


def test_forward_windows_use_fixed_supported_dates_without_interpolation(tmp_path: Path) -> None:
    database = tmp_path / "history.sqlite"
    _database(database)
    history = _history(database)

    window = history.forward_window(date(2025, 1, 1), 90)
    assert window.end_date == date(2025, 4, 1)
    assert history.nav_series("Alpha", window) is not None
    assert history.nav_series("Alpha", history.forward_window(date(2025, 1, 1), 180)) is None
    with pytest.raises(ValueError, match="horizon_days"):
        history.forward_window(date(2025, 1, 1), 91)


def test_malformed_nav_dates_fail_closed(tmp_path: Path) -> None:
    database = tmp_path / "history.sqlite"
    _database(database, malformed_nav_date=True)

    with pytest.raises(HistoricalDataError, match="Invalid NAV observation date"):
        _history(database).nav_series("Alpha", _history(database).forward_window(date(2025, 1, 1), 90))
