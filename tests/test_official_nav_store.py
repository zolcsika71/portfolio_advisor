from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from portfolio_advisor.history.official_nav_store import (
    OfficialNavObservation,
    OfficialNavStore,
    OfficialNavStoreError,
)


def _observation(value: float = 100.0) -> OfficialNavObservation:
    return OfficialNavObservation(
        isin="LU0594300682",
        observation_date=date(2025, 1, 2),
        value=value,
        currency="EUR",
        value_type="NAV",
        source_provider="erste_market",
        source_identifier="123",
        provenance_reference="data/raw/official_nav/erste_market/LU0594300682.json",
    )


def test_persistence_is_idempotent_and_conflicts_fail_closed(tmp_path: Path) -> None:
    store = OfficialNavStore(tmp_path / "official.sqlite")
    assert store.persist((_observation(),)) == 1
    assert store.persist((_observation(),)) == 0
    assert store.observations("LU0594300682") == (_observation(),)
    with pytest.raises(OfficialNavStoreError, match="conflicting"):
        store.persist((_observation(101.0),))


def test_coverage_and_summary_are_local_and_deterministic(tmp_path: Path) -> None:
    store = OfficialNavStore(tmp_path / "official.sqlite")
    store.persist((_observation(),))

    coverage = store.coverage("LU0594300682", "erste_market")

    assert coverage is not None
    assert coverage.observation_count == 1
    assert coverage.first_observation == date(2025, 1, 2)
    assert coverage.last_observation == date(2025, 1, 2)
    assert store.summary().provider_observation_counts == (("erste_market", 1),)
    assert store.identities() == (("LU0594300682", "erste_market", "EUR", "NAV"),)


@pytest.mark.parametrize("value", (0.0, -1.0, float("inf"), float("nan")))
def test_invalid_values_are_never_admitted(value: float) -> None:
    with pytest.raises(OfficialNavStoreError, match="finite and positive"):
        _observation(value)
