"""Typed point-in-time and optional NAV-history data structures."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

SUPPORTED_HORIZON_DAYS = frozenset({90, 180, 365})


class HistoricalDataError(RuntimeError):
    """Raised when historical source data is malformed or unsafe to use."""


@dataclass(frozen=True, slots=True)
class ForwardWindow:
    """A non-interpolated forward interval anchored at an evaluation date."""

    evaluation_date: date
    horizon_days: int
    end_date: date

    @classmethod
    def build(cls, evaluation_date: date, horizon_days: int) -> ForwardWindow:
        """Create one of the supported fixed-day forward horizons."""
        if horizon_days not in SUPPORTED_HORIZON_DAYS:
            supported = ", ".join(str(value) for value in sorted(SUPPORTED_HORIZON_DAYS))
            raise ValueError(f"horizon_days must be one of: {supported}")
        return cls(evaluation_date, horizon_days, evaluation_date + timedelta(days=horizon_days))


@dataclass(frozen=True, slots=True)
class NavObservation:
    """One portfolio NAV checkpoint used to derive a return interval."""

    observation_date: date
    portfolio_name: str
    net_asset_value: float


@dataclass(frozen=True, slots=True)
class NavSeries:
    """Ordered exact-boundary NAV observations for a single forward window."""

    portfolio_name: str
    observations: tuple[NavObservation, ...]
