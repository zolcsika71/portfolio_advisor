from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from portfolio_advisor.history.backtest_missing_data_policy import (
    MissingDataPolicyError,
    aggregate_holdings,
    build_policy_analysis,
    policy_definitions,
    simulate_windows,
)


def holdings(*items: tuple[str, float]) -> list[dict[str, object]]:
    return [
        {"isin": isin, "allocation": allocation, "asset_class": "Bond", "currency": "HUF"}
        for isin, allocation in items
    ]


def window(
    *,
    status: str = "UNUSABLE_SOURCE",
    missing: list[str] | None = None,
    unusable: list[str] | None = None,
) -> dict[str, object]:
    return {
        "observation_date": "2024-07-02",
        "portfolio_name": "Test portfolio",
        "horizon": 90,
        "required_start": "2024-07-02",
        "required_end": "2024-09-30",
        "required_isins": ["A", "B"],
        "missing_isins": [] if missing is None else missing,
        "unusable_isins": ["B"] if unusable is None else unusable,
        "status": status,
    }


def terminal() -> dict[str, object]:
    return {
        "isin": "B",
        "resolution_status": "BACKTEST_UNRESOLVABLE_WITH_CURRENT_PUBLIC_EVIDENCE",
        "research_closed": True,
        "backtest_admission": {
            "nav_equivalent": False,
            "backtest_return_series_approved": False,
            "usable_for_backtest": False,
        },
    }


def test_required_policies_are_unapproved_and_simulation_only() -> None:
    definitions = {item.policy_id: item for item in policy_definitions()}
    assert set(definitions) == {
        "STRICT_REJECT_WINDOW",
        "PARTIAL_DIAGNOSTICS_ONLY",
        "MINIMUM_RESOLVABLE_WEIGHT_THRESHOLD",
        "EXCLUDE_AND_RENORMALIZE",
        "HOLD_UNRESOLVED_WEIGHT_AS_CASH",
        "ZERO_RETURN_FOR_UNRESOLVED_WEIGHT",
        "PROXY_RETURN",
    }
    assert all(item.production_approved is False and item.simulation_only for item in definitions.values())
    assert definitions["PARTIAL_DIAGNOSTICS_ONLY"].return_calculation_allowed is False


def test_window_simulation_preserves_source_weights_and_rejects_unresolved() -> None:
    source = {("2024-07-02", "Test portfolio"): holdings(("A", 90.0), ("B", 10.0))}
    records: list[dict[str, Any]] = simulate_windows([window()], source, {"B"})
    record = records[0]
    assert record["resolvable_weight"] == 90.0
    assert record["unresolved_weight"] == 10.0
    assert record["terminal_unresolved_isins"] == ["B"]
    assert record["strict_reject"]["eligible"] is False
    assert record["partial_diagnostics"]["official_return_calculation_allowed"] is False
    assert record["thresholds"]["90"]["eligible"] is True
    assert record["thresholds"]["95"]["eligible"] is False
    assert record["renormalization"]["renormalization_factor"] == pytest.approx(100 / 90)
    assert sum(item["weight"] for item in record["renormalization"]["hypothetical_renormalized_weights"]) == pytest.approx(100)
    assert record["renormalization"]["hypothetical_concentration"]["herfindahl_hirschman_index"] > record["renormalization"]["original_concentration"]["herfindahl_hirschman_index"]
    assert source[("2024-07-02", "Test portfolio")][1]["allocation"] == 10.0
    assert record["cash"]["return_simulation_executed"] is False
    assert record["zero_return"]["return_simulation_executed"] is False
    assert record["proxy"]["proxy_series_created"] is False


@pytest.mark.parametrize(
    ("unresolved_weight", "threshold", "expected"),
    [(0.0, "100", True), (1.0, "99", True), (5.0, "95", True), (10.0, "90", True), (20.0, "80", True), (5.1, "95", False)],
)
def test_threshold_boundaries_are_exact(
    unresolved_weight: float, threshold: str, expected: bool
) -> None:
    source = {
        ("2024-07-02", "Test portfolio"): holdings(
            ("A", 100.0 - unresolved_weight), ("B", unresolved_weight)
        )
    }
    unresolved = [] if unresolved_weight == 0 else ["B"]
    record = simulate_windows([window(status="UNUSABLE_SOURCE", unusable=unresolved)], source, {"B"})[0]
    assert record["thresholds"][threshold]["eligible"] is expected
    assert record["thresholds"][threshold]["unresolved_weight_retained_in_definition"] == unresolved_weight


def test_zero_resolvable_weight_is_rejected_without_synthetic_weights() -> None:
    source = {("2024-07-02", "Test portfolio"): holdings(("A", 0.0), ("B", 100.0))}
    record = simulate_windows([window()], source, {"B"})[0]
    renormalization = record["renormalization"]
    assert renormalization["simulation_outcome"] == "REJECTED_ZERO_RESOLVABLE_WEIGHT"
    assert renormalization["renormalization_factor"] is None
    assert renormalization["hypothetical_renormalized_weights"] == []


def test_fully_resolvable_window_is_strictly_eligible() -> None:
    source = {("2024-07-02", "Test portfolio"): holdings(("A", 90.0), ("B", 10.0))}
    complete = window(status="COMPLETE", missing=[], unusable=[])
    record: dict[str, Any] = simulate_windows([complete], source, {"B"})[0]
    assert record["strict_reject"]["eligible"] is True
    assert record["thresholds"]["100"]["eligible"] is True
    assert record["renormalization"]["simulation_outcome"] == "NOT_APPLICABLE_FULLY_RESOLVABLE"


def test_weight_validation_and_duplicate_handling_fail_closed() -> None:
    aggregated = aggregate_holdings(holdings(("A", 40.0), ("A", 10.0), ("B", 50.0)))
    assert [(item["isin"], item["weight"]) for item in aggregated] == [
        ("A", pytest.approx(50)),
        ("B", pytest.approx(50)),
    ]
    with pytest.raises(MissingDataPolicyError):
        aggregate_holdings(holdings(("A", 99.0)))
    with pytest.raises(MissingDataPolicyError):
        aggregate_holdings(holdings(("A", 110.0), ("B", -10.0)))


def test_analysis_does_not_turn_terminal_evidence_into_backtest_admission() -> None:
    source = {("2024-07-02", "Test portfolio"): holdings(("A", 90.0), ("B", 10.0))}
    analysis: dict[str, Any] = build_policy_analysis(
        coverage_payload={"windows": [window()]},
        holdings_by_snapshot=source,
        terminal_resolutions={"B": terminal()},
        artifact_references={"coverage": "data/audit/backtest_window_coverage.json"},
    )
    assert analysis["production_approved"] is False
    assert analysis["return_simulation_not_executed"] is True
    assert analysis["policy_simulation_summaries"]["STRICT_REJECT_WINDOW"]["eligible_windows"] == 0
    assert analysis["recommendation"]["primary_policy_candidate"] == "STRICT_REJECT_WINDOW"


def test_actual_audit_counts_reconcile_without_changing_coverage() -> None:
    artifact: dict[str, Any] = json.loads(
        Path("data/audit/backtest_missing_data_policy_analysis.json").read_text(encoding="utf-8")
    )
    dataset = artifact["current_dataset"]
    strict = artifact["policy_simulation_summaries"]["STRICT_REJECT_WINDOW"]
    hu = artifact["hu0000554795_case_study"]
    assert dataset["total_actual_windows"] == 1152
    assert strict["eligible_windows"] + strict["rejected_windows"] == 1152
    assert hu["affected_windows"] == 132
    assert hu["horizon_counts"] == {"90": 44, "180": 44, "365": 44}
    assert hu["lifecycle_classification_counts"] == {"CROSSES_MATURITY": 52, "PRE_MATURITY": 80}
    assert artifact["current_dataset"]["unresolved_category_counts"]["TERMINAL_UNRESOLVABLE"] == 132
    assert artifact["next_task"] == "IMPLEMENT_STRICT_BACKTEST_WITH_PARTIAL_DIAGNOSTICS_MODE"
    assert artifact["recommendation"]["production_approved"] is False
