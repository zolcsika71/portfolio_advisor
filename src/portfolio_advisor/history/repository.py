"""Read-only access to dated portfolio snapshots and optional NAV history."""

from __future__ import annotations

import sqlite3
from datetime import date
from math import isfinite

from portfolio_advisor.database.repository import (
    HoldingObservation,
    ModelPortfolioRepository,
)

from .models import ForwardWindow, HistoricalDataError, NavObservation, NavSeries

NAV_HISTORY_TABLE = "portfolio_nav_history"
NAV_HISTORY_COLUMNS = frozenset({"Date", "Portfolio Name", "Net Asset Value"})


class HistoricalPortfolioRepository:
    """Expose point-in-time snapshots and a non-mutating optional NAV source.

    ``portfolio_nav_history`` is intentionally optional: Milestone 2's source
    database contains snapshot indicators only. Its absence is represented to
    callers as unavailable forward data rather than being synthesized.
    """

    def __init__(self, model_repository: ModelPortfolioRepository) -> None:
        self.model_repository = model_repository

    def observation_dates(self) -> tuple[date, ...]:
        """Return all model-portfolio observation dates in chronological order."""
        return self.model_repository.observation_dates()

    def holdings_at(self, observation_date: date) -> list[HoldingObservation]:
        """Return only holdings recorded at the requested point in time."""
        return self.model_repository.load_holdings(observation_date)

    def forward_window(self, evaluation_date: date, horizon_days: int) -> ForwardWindow:
        """Build a fixed forward window without choosing a nearby date."""
        return ForwardWindow.build(evaluation_date, horizon_days)

    def nav_history_available(self) -> bool:
        """Return whether the optional NAV-history schema is available and valid."""
        with self._connection() as connection:
            table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
                (NAV_HISTORY_TABLE,),
            ).fetchone()
            if table is None:
                return False
            columns = {
                row[1]
                for row in connection.execute(f'PRAGMA table_info("{NAV_HISTORY_TABLE}")').fetchall()
            }
        missing = sorted(NAV_HISTORY_COLUMNS - columns)
        if missing:
            raise HistoricalDataError(
                f"Table {NAV_HISTORY_TABLE!r} is missing required columns: {', '.join(missing)}"
            )
        return True

    def nav_series(self, portfolio_name: str, window: ForwardWindow) -> NavSeries | None:
        """Return exact-boundary NAV data, or ``None`` when the window is incomplete.

        The evaluation-date NAV is an anchor only. Every derived return uses a
        later observation, and records after ``window.end_date`` are excluded.
        Missing endpoints are never replaced by an interpolated value.
        """
        if not self.nav_history_available():
            return None
        with self._connection() as connection:
            rows = connection.execute(
                f'SELECT "Date", "Portfolio Name", "Net Asset Value" '
                f'FROM "{NAV_HISTORY_TABLE}" WHERE "Portfolio Name" = ?',
                (portfolio_name,),
            ).fetchall()
        observations = [self._nav_observation(row) for row in rows]
        dated = {item.observation_date: item for item in observations}
        if len(dated) != len(observations):
            raise HistoricalDataError(
                f"Duplicate NAV observations for portfolio {portfolio_name!r}"
            )
        if window.evaluation_date not in dated or window.end_date not in dated:
            return None
        selected = tuple(
            item
            for item in sorted(observations, key=lambda item: item.observation_date)
            if window.evaluation_date <= item.observation_date <= window.end_date
        )
        return NavSeries(portfolio_name, selected)

    def _connection(self) -> sqlite3.Connection:
        path = self.model_repository.database_path
        if not path.is_file():
            raise HistoricalDataError(f"Database file does not exist: {path}")
        try:
            connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
        except sqlite3.Error as error:
            raise HistoricalDataError(f"Could not open database: {path}") from error
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _nav_observation(row: sqlite3.Row) -> NavObservation:
        raw_date = row["Date"]
        try:
            observation_date = date.fromisoformat(str(raw_date).replace("/", "-"))
        except (TypeError, ValueError) as error:
            raise HistoricalDataError(f"Invalid NAV observation date: {raw_date!r}") from error
        try:
            nav = float(row["Net Asset Value"])
        except (TypeError, ValueError) as error:
            raise HistoricalDataError(
                f"Invalid NAV for {row['Portfolio Name']!r} on {raw_date!r}"
            ) from error
        if not isfinite(nav) or nav <= 0.0:
            raise HistoricalDataError(
                f"NAV must be finite and positive for {row['Portfolio Name']!r} on {raw_date!r}"
            )
        return NavObservation(observation_date, str(row["Portfolio Name"]), nav)
