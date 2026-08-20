from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

from portfolio_advisor.history.official_nav_store import (
    OfficialNavObservation,
    OfficialNavStore,
)

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "acquire_missing_historical_nav_series.py"
SPEC = importlib.util.spec_from_file_location("continuation_acquisition", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
acquisition = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = acquisition
SPEC.loader.exec_module(acquisition)


def _target(isin: str, impact: int, *, provider: str = "erste_market") -> dict[str, object]:
    return {
        "isin": isin,
        "recoverable_label_count": impact,
        "required_start_date": "2025-01-02",
        "required_end_date": "2025-01-03",
        "preferred_existing_provider": provider,
        "current_source_status": "STRICT_COMPLETE_SOURCE_COVERAGE_NOT_PERSISTED:erste_market",
    }


def _observation(isin: str, observed: date) -> OfficialNavObservation:
    return OfficialNavObservation(
        isin=isin,
        observation_date=observed,
        value=100.0,
        currency="EUR",
        value_type="NAV",
        source_provider="erste_market",
        source_identifier="source-id",
        provenance_reference="data/raw/official_nav/erste_market/evidence.json",
    )


def test_continuation_skips_exact_retained_targets_and_special_cases(tmp_path: Path) -> None:
    store = OfficialNavStore(tmp_path / "store.sqlite")
    retained = _target("LU0594300682", 999)
    store.persist(
        (
            _observation("LU0594300682", date(2025, 1, 2)),
            _observation("LU0594300682", date(2025, 1, 3)),
        )
    )
    targets = [
        retained,
        _target("HU0000554795", 800),
        _target("AT0000605324", 700),
        _target("HU0000708243", 300),
        _target("HU0000723572", 300),
        _target("LU0205352882", 250),
    ]

    selected, acquired = acquisition.select_continuation_targets(targets, store, 2)

    assert [item["isin"] for item in selected] == ["HU0000708243", "HU0000723572"]
    assert [item["isin"] for item in acquired] == ["LU0594300682"]
    assert acquired[0]["retained_status"]["status"] == "ACQUIRED_VALIDATED"


def test_continuation_requires_a_positive_bounded_batch(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="limit"):
        acquisition.select_continuation_targets([], OfficialNavStore(tmp_path / "store.sqlite"), 0)


def test_marginal_value_counts_only_new_validated_or_partial_evidence() -> None:
    value = acquisition.acquisition_marginal_value(
        [
            {
                "status": "VALIDATED",
                "new_observations_persisted": 10,
                "estimated_affected_strict_eligible_windows": 3,
            },
            {
                "status": "PARTIAL_HISTORY",
                "new_observations_persisted": 5,
                "estimated_affected_strict_eligible_windows": 2,
            },
            {"status": "SOURCE_UNAVAILABLE", "new_observations_persisted": 0},
        ],
        remaining_target_count=7,
        previous_payload={"new_observations_persisted": 12},
    )

    assert value["new_targets_acquired"] == 1
    assert value["new_partial_targets"] == 1
    assert value["new_observations"] == 15
    assert value["new_constituent_window_incidences_covered"] == 3
    assert value["partial_constituent_window_incidences"] == 2
    assert value["comparison_to_previous_batch"]["observation_delta"] == 3


def test_cumulative_coverage_keeps_partial_intervals_unresolved(tmp_path: Path) -> None:
    store = OfficialNavStore(tmp_path / "store.sqlite")
    store.persist(
        (
            _observation("LU0594300682", date(2025, 1, 2)),
            _observation("LU0594300682", date(2025, 1, 3)),
            _observation("HU0000708243", date(2025, 1, 2)),
        )
    )
    targets = [_target("LU0594300682", 10), _target("HU0000708243", 5)]

    coverage = acquisition.cumulative_constituent_coverage(targets, store)

    assert coverage["exact_constituent_window_incidences"] == 10
    assert coverage["partial_constituent_window_incidences"] == 5
    assert coverage["remaining_unresolved_targets"] == 1


def test_marginal_value_excludes_an_idempotent_reimport() -> None:
    value = acquisition.acquisition_marginal_value(
        [
            {
                "status": "PARTIAL_HISTORY",
                "new_observations_persisted": 0,
                "estimated_affected_strict_eligible_windows": 9,
            }
        ],
        remaining_target_count=1,
        previous_payload=None,
    )

    assert value["new_observations"] == 0
    assert value["partial_constituent_window_incidences"] == 0
