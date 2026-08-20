"""Minimal SQLite snapshots for immutable ranking and label-store tests."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

HISTORICAL_RANKING_DATE = date(2026, 7, 6)
LATER_PRODUCTION_LIKE_DATE = date(2026, 8, 18)
HISTORICAL_RANKING_ORDER = (
    "PB Konzervatív MultiCCY",
    "PB Konzervatív HUF",
    "PB Konzervatív EUR",
    "PB Kiegyensúlyozott MultiCCY",
    "PB Kiegyensúlyozott HUF",
    "PB Konzervatív USD",
    "PB Dinamikus MultiCCY",
    "PB Dinamikus HUF",
    "PB Kiegyensúlyozott EUR",
    "PB Kiegyensúlyozott USD",
    "PB Dinamikus EUR",
    "PB Dinamikus USD",
)


def create_historical_ranking_database(path: Path) -> None:
    """Create the exact compact D1 ranking fixture without production data."""
    _create_schema(path)
    _insert_ranked_snapshot(path, HISTORICAL_RANKING_DATE)


def append_later_production_like_snapshot(path: Path) -> None:
    """Append a valid D2 snapshot used to prove fixture isolation."""
    _insert_ranked_snapshot(path, LATER_PRODUCTION_LIKE_DATE)


def create_label_store_database(path: Path) -> date:
    """Create two exact source identities for terminal/reconciliation labels."""
    fixture_date = HISTORICAL_RANKING_DATE
    _create_schema(path)
    rows = (
        (fixture_date.strftime("%Y/%m/%d"), "HU fixture", "HU fund", "HU0000554795", "HUF"),
        (fixture_date.strftime("%Y/%m/%d"), "AT fixture", "AT fund", "AT0000605324", "EUR"),
    )
    with sqlite3.connect(path) as connection:
        connection.executemany(
            'INSERT INTO model_portfolios VALUES (?, ?, ?, ?, 100.0, "Fund", ?, "Hedged", '
            '0.05, 1.0, 0.05, 0.04, -0.05)',
            rows,
        )
    return fixture_date


def _create_schema(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            'CREATE TABLE model_portfolios ('
            '"Date" TEXT, "Portfolio Name" TEXT, "Product" TEXT, "ISIN" TEXT, '
            '"Allocation (%)" REAL, "Asset Class" TEXT, "Currency" TEXT, "Currency Risk" TEXT, '
            '"1 Year" REAL, "1Y Sharpe Ratio" REAL, "1Y Volatility" REAL, '
            '"Downside Risk" REAL, "Maximum Drawdown" REAL)'
        )


def _insert_ranked_snapshot(path: Path, observation_date: date) -> None:
    rows = []
    for index, portfolio_name in enumerate(HISTORICAL_RANKING_ORDER):
        rejected = index == len(HISTORICAL_RANKING_ORDER) - 1
        currency = "HUF" if "HUF" in portfolio_name else "USD" if "USD" in portfolio_name else "EUR"
        rows.append(
            (
                observation_date.strftime("%Y/%m/%d"),
                portfolio_name,
                f"Fixture fund {index + 1}",
                f"FI{index + 1:010d}",
                100.0,
                "Fund",
                currency,
                "Hedged",
                0.12 - index * 0.01,
                12.0 - index,
                0.01 + index * 0.01,
                0.01 + index * 0.01,
                None if rejected else -(0.01 + index * 0.01),
            )
        )
    with sqlite3.connect(path) as connection:
        connection.executemany('INSERT INTO model_portfolios VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', rows)
