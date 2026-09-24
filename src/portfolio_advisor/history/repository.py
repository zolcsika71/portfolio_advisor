"""Read-only access to dated portfolio snapshots and optional NAV history."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import date
from math import isfinite
from pathlib import Path
from typing import Protocol, cast

from portfolio_advisor.database.repository import (
    FileBackedModelPortfolioReader,
    HoldingObservation,
    ModelPortfolioReader,
)

from .models import ForwardWindow, HistoricalDataError, NavObservation, NavSeries

NAV_HISTORY_TABLE = "portfolio_nav_history"
NAV_HISTORY_COLUMNS = frozenset({"Date", "Portfolio Name", "Net Asset Value"})


class PortfolioNavReader(Protocol):
    """Independent optional source of direct portfolio-NAV observations."""

    def available(self) -> bool: ...

    def nav_series(self, portfolio_name: str, window: ForwardWindow) -> NavSeries | None: ...


class SQLitePortfolioNavRepository:
    """Read the optional flat NAV table independently from model snapshots."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def available(self) -> bool:
        with self._connection() as connection:
            table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
                (NAV_HISTORY_TABLE,),
            ).fetchone()
            if table is None:
                return False
            columns = {
                row[1]
                for row in connection.execute(
                    f'PRAGMA table_info("{NAV_HISTORY_TABLE}")'
                ).fetchall()
            }
        missing = sorted(NAV_HISTORY_COLUMNS - columns)
        if missing:
            raise HistoricalDataError(
                f"Table {NAV_HISTORY_TABLE!r} is missing required columns: {', '.join(missing)}"
            )
        return True

    def nav_series(self, portfolio_name: str, window: ForwardWindow) -> NavSeries | None:
        if not self.available():
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
        if not self.database_path.is_file():
            raise HistoricalDataError(f"Database file does not exist: {self.database_path}")
        try:
            connection = sqlite3.connect(
                f"file:{self.database_path.resolve()}?mode=ro", uri=True
            )
        except sqlite3.Error as error:
            raise HistoricalDataError(
                f"Could not open database: {self.database_path}"
            ) from error
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection

    @staticmethod
    def _nav_observation(row: sqlite3.Row | Sequence[object]) -> NavObservation:
        raw_date = row["Date"] if isinstance(row, sqlite3.Row) else row[0]
        portfolio_name = (
            row["Portfolio Name"] if isinstance(row, sqlite3.Row) else row[1]
        )
        raw_nav = row["Net Asset Value"] if isinstance(row, sqlite3.Row) else row[2]
        try:
            observation_date = date.fromisoformat(str(raw_date).replace("/", "-"))
        except (TypeError, ValueError) as error:
            raise HistoricalDataError(
                f"Invalid NAV observation date: {raw_date!r}"
            ) from error
        try:
            nav = float(str(raw_nav))
        except (TypeError, ValueError) as error:
            raise HistoricalDataError(
                f"Invalid NAV for {portfolio_name!r} on {raw_date!r}"
            ) from error
        if not isfinite(nav) or nav <= 0.0:
            raise HistoricalDataError(
                f"NAV must be finite and positive for {portfolio_name!r} on {raw_date!r}"
            )
        return NavObservation(observation_date, str(portfolio_name), nav)


_USE_MODEL_DATABASE_NAV = object()


class HistoricalPortfolioRepository:
    """Expose point-in-time snapshots and a non-mutating optional NAV source.

    ``portfolio_nav_history`` is intentionally optional: Milestone 2's source
    database contains snapshot indicators only. Its absence is represented to
    callers as unavailable forward data rather than being synthesized.
    """

    def __init__(
        self,
        model_repository: ModelPortfolioReader,
        nav_repository: PortfolioNavReader | None | object = _USE_MODEL_DATABASE_NAV,
    ) -> None:
        self.model_repository = model_repository
        if nav_repository is _USE_MODEL_DATABASE_NAV:
            if not isinstance(model_repository, FileBackedModelPortfolioReader):
                raise HistoricalDataError(
                    "a non-file-backed model reader requires an explicit NAV reader or None"
                )
            nav_repository = SQLitePortfolioNavRepository(model_repository.database_path)
        self.nav_repository = cast(PortfolioNavReader | None, nav_repository)

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
        return self.nav_repository is not None and self.nav_repository.available()

    def nav_series(self, portfolio_name: str, window: ForwardWindow) -> NavSeries | None:
        """Return exact-boundary NAV data, or ``None`` when the window is incomplete.

        The evaluation-date NAV is an anchor only. Every derived return uses a
        later observation, and records after ``window.end_date`` are excluded.
        Missing endpoints are never replaced by an interpolated value.
        """
        if self.nav_repository is None:
            return None
        return self.nav_repository.nav_series(portfolio_name, window)
