from __future__ import annotations

import importlib.util
import json
import socket
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "analyze_at0000605324_conflict_patterns.py"
SPEC = importlib.util.spec_from_file_location("at0000605324_conflict_patterns", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
patterns = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = patterns
SPEC.loader.exec_module(patterns)

DIAGNOSTICS = ROOT / "data" / "audit" / "erste_nav_diagnostics.json"
RECONCILIATION = ROOT / "data" / "audit" / "at0000605324_morningstar_reconciliation.json"


def _write_json(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _diagnostics_payload() -> dict[str, Any]:
    return json.loads(DIAGNOSTICS.read_text(encoding="utf-8"))


def _reconciliation_payload() -> dict[str, Any]:
    return json.loads(RECONCILIATION.read_text(encoding="utf-8"))


def _structural_row(
    *,
    index_a: int,
    index_b: int,
    previous: str,
    next_value: str,
) -> dict[str, object]:
    return {
        "occurrence_index_a": index_a,
        "occurrence_index_b": index_b,
        "a_closer_to_previous": previous == "A",
        "b_closer_to_previous": previous == "B",
        "previous_tie": previous == "TIE",
        "a_closer_to_next": next_value == "A",
        "b_closer_to_next": next_value == "B",
        "next_tie": next_value == "TIE",
        "same_occurrence_order": index_a < index_b,
    }


def test_exactly_28_conflicts_load_and_occurrence_indexes_are_extracted() -> None:
    conflicts = patterns.read_conflicts(DIAGNOSTICS)

    assert len(conflicts) == 28
    assert conflicts[0].calendar_date == date(2005, 3, 2)
    assert (conflicts[0].occurrence_index_a, conflicts[0].occurrence_index_b) == (2, 3)


def test_exact_date_join_and_decimal_calculations() -> None:
    conflicts = patterns.read_conflicts(DIAGNOSTICS)
    matches = patterns.read_reconciliation_matches(RECONCILIATION)
    rows = patterns.analyze(conflicts, matches)
    first = rows[0]

    assert first["date"] == "2005-03-02"
    assert first["classification"] == patterns.CLASSIFICATION_A
    assert first["morningstar_nav"] == Decimal("93.01")
    assert isinstance(first["difference_a_b"], Decimal)
    assert first["difference_a_b"] == Decimal("0.09")
    assert first["difference_ms_a"] == Decimal("0.00")
    assert first["difference_ms_b"] == Decimal("0.09")


def test_previous_observation_ignores_same_day_duplicate() -> None:
    conflict_date = date(2005, 3, 2)
    observations = (
        patterns.Observation(date(2005, 3, 1), Decimal(10)),
        patterns.Observation(conflict_date, Decimal(999)),
        patterns.Observation(date(2005, 2, 28), Decimal(8)),
    )

    assert patterns.representative_previous(conflict_date, observations) == Decimal(10)


def test_next_observation_ignores_same_day_duplicate() -> None:
    conflict_date = date(2005, 3, 2)
    observations = (
        patterns.Observation(conflict_date, Decimal(999)),
        patterns.Observation(date(2005, 3, 4), Decimal(12)),
        patterns.Observation(date(2005, 3, 3), Decimal(11)),
    )

    assert patterns.representative_next(conflict_date, observations) == Decimal(11)


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        (Decimal(10), {"a_closer_to_previous": True, "b_closer_to_previous": False, "previous_tie": False}),
        (Decimal(20), {"a_closer_to_previous": False, "b_closer_to_previous": True, "previous_tie": False}),
        (Decimal(15), {"a_closer_to_previous": False, "b_closer_to_previous": False, "previous_tie": True}),
    ],
)
def test_previous_closeness_flags(reference: Decimal, expected: dict[str, bool]) -> None:
    assert patterns.closeness_flags(Decimal(12), Decimal(18), reference, "previous") == expected


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        (Decimal(10), {"a_closer_to_next": True, "b_closer_to_next": False, "next_tie": False}),
        (Decimal(20), {"a_closer_to_next": False, "b_closer_to_next": True, "next_tie": False}),
        (Decimal(15), {"a_closer_to_next": False, "b_closer_to_next": False, "next_tie": True}),
    ],
)
def test_next_closeness_flags(reference: Decimal, expected: dict[str, bool]) -> None:
    assert patterns.closeness_flags(Decimal(12), Decimal(18), reference, "next") == expected


def test_classifications_and_reconciliation_safety_are_preserved() -> None:
    report = patterns.run(DIAGNOSTICS, RECONCILIATION)

    assert report["classification_counts"] == {
        "MATCH_ERSTE_VALUE_A": 24,
        "MATCH_ERSTE_VALUE_B": 4,
    }
    assert len(report["a_cases"]) == 24
    assert len(report["b_cases"]) == 4
    assert report["reconciliation_status"] == "RECONCILIATION_REQUIRED"
    assert report["usable_for_backtest"] is False
    assert report["deterministic_reconciliation_rule_accepted"] is False


def test_candidate_pattern_summary_for_actual_inputs_is_diagnostic_only() -> None:
    report = patterns.run(DIAGNOSTICS, RECONCILIATION)
    summary = report["candidate_pattern_summary"]

    assert summary["unique_b_discriminator_found"] is False
    assert summary["a_cases_same_pattern_count"] == 24
    assert "No unique observed structural discriminator" in str(summary["statement"])


def test_candidate_pattern_summary_can_identify_a_b_only_structural_property() -> None:
    b_cases = [
        _structural_row(index_a=10, index_b=11, previous="B", next_value="B")
        for _ in range(4)
    ]
    a_cases = [
        _structural_row(index_a=20, index_b=21, previous="A", next_value="A")
        for _ in range(24)
    ]

    summary = patterns.candidate_pattern_summary(a_cases, b_cases)

    assert summary["unique_b_discriminator_found"] is True
    assert "occurrence_index_pair" in summary["unique_b_discriminators"]


def test_candidate_pattern_summary_is_false_when_an_a_case_shares_every_b_property() -> None:
    b_cases = [
        _structural_row(index_a=10, index_b=11, previous="A", next_value="B"),
        _structural_row(index_a=10, index_b=11, previous="B", next_value="A"),
        _structural_row(index_a=10, index_b=11, previous="A", next_value="B"),
        _structural_row(index_a=10, index_b=11, previous="B", next_value="A"),
    ]
    a_cases = [
        _structural_row(index_a=10, index_b=11, previous="A", next_value="B"),
        _structural_row(index_a=20, index_b=21, previous="B", next_value="A"),
    ]

    summary = patterns.candidate_pattern_summary(a_cases, b_cases)

    assert summary["unique_b_discriminator_found"] is False
    assert summary["all_b_cases_share_occurrence_pair"] is True


def test_malformed_conflict_fails_closed(tmp_path: Path) -> None:
    payload = _diagnostics_payload()
    target = next(
        row for row in payload["results"] if row["isin"] == patterns.TARGET_ISIN
    )
    target["anomaly_details"][0]["values"] = ["93.01", "93.1", "93.2"]

    with pytest.raises(patterns.ConflictPatternError, match="exactly two"):
        patterns.read_conflicts(_write_json(tmp_path / "diagnostics.json", payload))


def test_missing_reconciliation_date_fails_closed(tmp_path: Path) -> None:
    reconciliation = _reconciliation_payload()
    reconciliation["comparisons"] = reconciliation["comparisons"][1:]
    matches = patterns.read_reconciliation_matches(
        _write_json(tmp_path / "reconciliation.json", reconciliation)
    )

    with pytest.raises(patterns.ConflictPatternError, match="Missing exact-date"):
        patterns.analyze(patterns.read_conflicts(DIAGNOSTICS), matches)


def test_duplicate_reconciliation_date_fails_closed(tmp_path: Path) -> None:
    reconciliation = _reconciliation_payload()
    reconciliation["comparisons"].append(reconciliation["comparisons"][0])

    with pytest.raises(patterns.ConflictPatternError, match="duplicate date"):
        patterns.read_reconciliation_matches(
            _write_json(tmp_path / "reconciliation.json", reconciliation)
        )


def test_analysis_makes_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_network(*args: object, **kwargs: object) -> object:
        raise AssertionError("Network access is prohibited")

    monkeypatch.setattr(socket, "create_connection", no_network)
    report = patterns.run(DIAGNOSTICS, RECONCILIATION)

    assert report["total_conflicts"] == 28
