"""Read-only, schema-validated access to the SQLite portfolio database."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final

TABLE_NAME: Final = "model_portfolios"
REQUIRED_COLUMNS: Final = frozenset(
    {
        "Date",
        "Portfolio Name",
        "Product",
        "ISIN",
        "Allocation (%)",
        "Currency",
        "Currency Risk",
        "1 Year",
        "1Y Sharpe Ratio",
        "1Y Volatility",
        "Downside Risk",
        "Maximum Drawdown",
    }
)
SELECT_COLUMNS: Final = (
    "Portfolio Name",
    "Product",
    "ISIN",
    "Allocation (%)",
    "Currency",
    "Currency Risk",
    "1 Year",
    "1Y Sharpe Ratio",
    "1Y Volatility",
    "Downside Risk",
    "Maximum Drawdown",
)


class RepositoryError(RuntimeError):
    """Raised when the source database cannot be read safely."""


@dataclass(frozen=True, slots=True)
class HoldingObservation:
    """One constituent of a model portfolio at an observation date."""

    portfolio_name: str
    product: str | None
    isin: str | None
    allocation: float | None
    currency: str | None
    currency_risk: str | None
    return_1y: float | None
    sharpe_ratio_1y: float | None
    volatility_1y: float | None
    downside_risk: float | None
    maximum_drawdown: float | None


class ModelPortfolioRepository:
    """Expose only parameterized, non-mutating queries against SQLite."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def _connection(self) -> sqlite3.Connection:
        if not self.database_path.is_file():
            raise RepositoryError(f"Database file does not exist: {self.database_path}")
        try:
            connection = sqlite3.connect(
                f"file:{self.database_path.resolve()}?mode=ro", uri=True
            )
        except sqlite3.Error as error:
            raise RepositoryError(f"Could not open database: {self.database_path}") from error
        connection.row_factory = sqlite3.Row
        return connection

    def _validate_schema(self, connection: sqlite3.Connection) -> None:
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            (TABLE_NAME,),
        ).fetchone()
        if table is None:
            raise RepositoryError(f"Required table is missing: {TABLE_NAME}")
        columns = {
            row[1]
            for row in connection.execute(f'PRAGMA table_info("{TABLE_NAME}")').fetchall()
        }
        missing = sorted(REQUIRED_COLUMNS - columns)
        if missing:
            raise RepositoryError(
                f"Table {TABLE_NAME!r} is missing required columns: {', '.join(missing)}"
            )

    def observation_dates(self) -> tuple[date, ...]:
        """Return every valid source date in ascending chronological order."""
        with self._connection() as connection:
            self._validate_schema(connection)
            values = connection.execute(
                'SELECT DISTINCT "Date" FROM "model_portfolios" WHERE "Date" IS NOT NULL'
            ).fetchall()
        parsed: list[date] = []
        for row in values:
            try:
                parsed.append(date.fromisoformat(str(row[0]).replace("/", "-")))
            except (TypeError, ValueError) as error:
                raise RepositoryError(f"Invalid observation date in database: {row[0]!r}") from error
        if not parsed:
            raise RepositoryError("No portfolio observation dates are available")
        return tuple(sorted(set(parsed)))

    def latest_observation_date(self) -> date:
        """Return the latest valid source date without relying on text sorting."""
        return self.observation_dates()[-1]

    def load_holdings(self, observation_date: date) -> list[HoldingObservation]:
        """Load all rows for one date, preserving the original observation date."""
        date_value = observation_date.strftime("%Y/%m/%d")
        quoted = ", ".join(f'"{column}"' for column in SELECT_COLUMNS)
        with self._connection() as connection:
            self._validate_schema(connection)
            rows = connection.execute(
                f'SELECT {quoted} FROM "{TABLE_NAME}" WHERE "Date" = ? '
                'ORDER BY "Portfolio Name", "ISIN", "Product"',
                (date_value,),
            ).fetchall()
        return [
            HoldingObservation(
                portfolio_name=row["Portfolio Name"],
                product=row["Product"],
                isin=row["ISIN"],
                allocation=row["Allocation (%)"],
                currency=row["Currency"],
                currency_risk=row["Currency Risk"],
                return_1y=row["1 Year"],
                sharpe_ratio_1y=row["1Y Sharpe Ratio"],
                volatility_1y=row["1Y Volatility"],
                downside_risk=row["Downside Risk"],
                maximum_drawdown=row["Maximum Drawdown"],
            )
            for row in rows
        ]
