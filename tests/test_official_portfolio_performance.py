from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from portfolio_advisor.history.official_portfolio_performance import (
    OfficialPortfolioPerformanceError,
    OfficialPortfolioPerformanceObservation,
    OfficialPortfolioPerformanceStore,
)


def _observation(value: float = 100.0) -> OfficialPortfolioPerformanceObservation:
    return OfficialPortfolioPerformanceObservation(
        portfolio_id="PB Conservative EUR",
        observation_date=date(2025, 1, 2),
        value=value,
        currency="EUR",
        value_type="PORTFOLIO_NAV",
        source_provider="official_provider",
        source_identifier="official-id",
        provenance_reference="data/portfolio_performance/raw/provider/export.csv",
    )


def test_direct_portfolio_store_is_idempotent_and_conflicts_fail_closed(tmp_path: Path) -> None:
    store = OfficialPortfolioPerformanceStore(tmp_path / "official.sqlite")

    assert store.persist((_observation(),)) == 1
    assert store.persist((_observation(),)) == 0
    assert store.observations("PB Conservative EUR") == (_observation(),)
    with pytest.raises(OfficialPortfolioPerformanceError, match="conflicting"):
        store.persist((_observation(101.0),))


@pytest.mark.parametrize("value", (0.0, -1.0, float("inf"), float("nan")))
def test_direct_portfolio_values_must_be_finite_positive_and_semantically_typed(value: float) -> None:
    with pytest.raises(OfficialPortfolioPerformanceError, match="finite and positive"):
        _observation(value)
