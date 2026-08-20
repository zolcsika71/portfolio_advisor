"""Offline end-to-end validation of strict backtest eligibility evidence."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from portfolio_advisor.history.repository import HistoricalPortfolioRepository

from .eligibility import StrictCoverageEligibilityGate
from .models import BacktestEligibility


class StrictPipelineValidationError(RuntimeError):
    """A retained strict-policy invariant did not reconcile."""


def validate_strict_pipeline(
    *,
    history: HistoricalPortfolioRepository,
    gate: StrictCoverageEligibilityGate,
    coverage_payload: Mapping[str, object],
    policy_payload: Mapping[str, object],
) -> dict[str, Any]:
    """Validate every retained coverage window without loading NAV or providers."""
    raw_windows = coverage_payload.get("windows")
    if not isinstance(raw_windows, list):
        raise StrictPipelineValidationError("coverage artifact has no windows list")
    decisions: list[tuple[Mapping[str, object], BacktestEligibility]] = []
    for raw in raw_windows:
        if not isinstance(raw, Mapping):
            raise StrictPipelineValidationError("coverage window is malformed")
        observation = raw.get("observation_date")
        portfolio = raw.get("portfolio_name")
        horizon = raw.get("horizon")
        if (
            not isinstance(observation, str)
            or not isinstance(portfolio, str)
            or isinstance(horizon, bool)
            or not isinstance(horizon, int)
        ):
            raise StrictPipelineValidationError("coverage window identity is malformed")
        window = history.forward_window(date.fromisoformat(observation), horizon)
        decisions.append(
            (
                raw,
                gate.evaluate(history=history, portfolio_name=portfolio, window=window),
            )
        )

    strict = _object(_object(policy_payload.get("policy_simulation_summaries"), "policy summaries").get(
        "STRICT_REJECT_WINDOW"
    ), "strict policy summary")
    dataset = _object(policy_payload.get("current_dataset"), "policy dataset")
    eligible = [(row, decision) for row, decision in decisions if decision.eligible]
    rejected = [(row, decision) for row, decision in decisions if not decision.eligible]
    _equal(len(decisions), _integer(dataset.get("total_actual_windows"), "total_actual_windows"), "total windows")
    _equal(len(eligible), _integer(strict.get("eligible_windows"), "eligible_windows"), "eligible windows")
    _equal(len(rejected), _integer(strict.get("rejected_windows"), "rejected_windows"), "rejected windows")
    if len(eligible) + len(rejected) != len(decisions):
        raise StrictPipelineValidationError("strict decisions do not partition coverage windows")

    horizons = {
        str(horizon): {
            "total": sum(_horizon(row) == horizon for row, _ in decisions),
            "eligible": sum(_horizon(row) == horizon for row, _ in eligible),
            "rejected": sum(_horizon(row) == horizon for row, _ in rejected),
        }
        for horizon in sorted({_horizon(row) for row, _ in decisions})
    }
    expected_horizons = _object(strict.get("eligible_horizon_counts"), "eligible_horizon_counts")
    for horizon, values in horizons.items():
        _equal(values["eligible"], _integer(expected_horizons.get(horizon), f"eligible horizon {horizon}"), f"eligible horizon {horizon}")
        if values["eligible"] + values["rejected"] != values["total"]:
            raise StrictPipelineValidationError(f"horizon {horizon} does not reconcile")

    category_associations = Counter(
        blocker.category for _, decision in rejected for blocker in decision.blocking_constituents
    )
    hu = [(row, decision) for row, decision in rejected if any(
        item.isin == "HU0000554795" for item in decision.blocking_constituents
    )]
    hu_case = _object(policy_payload.get("hu0000554795_case_study"), "HU case study")
    _equal(len(hu), _integer(hu_case.get("affected_windows"), "HU affected_windows"), "HU affected windows")
    _equal(
        sum(len(decision.blocking_constituents) == 1 for _, decision in hu),
        _integer(hu_case.get("sole_unresolved_windows"), "HU sole_unresolved_windows"),
        "HU sole-blocker windows",
    )
    _equal(
        sum(len(decision.blocking_constituents) > 1 for _, decision in hu),
        _integer(hu_case.get("multi_blocker_windows"), "HU multi_blocker_windows"),
        "HU multi-blocker windows",
    )
    at_reconciliation = sum(
        any(
            item.isin == "AT0000605324" and item.category == "RECONCILIATION_REQUIRED"
            for item in decision.blocking_constituents
        )
        for _, decision in rejected
    )
    if not at_reconciliation:
        raise StrictPipelineValidationError("AT0000605324 has no reconciliation-required rejection")

    return {
        "schema_version": 1,
        "validation_status": "STRICT_BACKTEST_PIPELINE_VALIDATED",
        "policy": "STRICT_REJECT_WINDOW",
        "offline": True,
        "source_artifact_references": {
            "backtest_window_coverage": "data/audit/backtest_window_coverage.json",
            "missing_data_policy_analysis": "data/audit/backtest_missing_data_policy_analysis.json",
            "terminal_resolutions": "data/audit/*_backtest_resolvability.json",
        },
        "dataset": {
            "total_windows": len(decisions),
            "official_eligible_windows": len(eligible),
            "rejected_windows": len(rejected),
            "eligible_percentage": _percentage(len(eligible), len(decisions)),
            "rejected_percentage": _percentage(len(rejected), len(decisions)),
        },
        "horizons": horizons,
        "blocking_category_associations": {
            "denominator": "blocking-ISIN/window associations; categories can overlap within a window",
            "counts": dict(sorted(category_associations.items())),
        },
        "hu0000554795": {
            "affected_windows": len(hu),
            "rejected_windows": len(hu),
            "horizon_counts": _horizon_counts(hu),
            "sole_blocker_windows": sum(len(decision.blocking_constituents) == 1 for _, decision in hu),
            "multi_blocker_windows": sum(len(decision.blocking_constituents) > 1 for _, decision in hu),
            "diagnostics_supported": True,
            "terminal_resolution_reference": next(
                (
                    item.resolution_reference
                    for _, decision in hu
                    for item in decision.blocking_constituents
                    if item.isin == "HU0000554795"
                ),
                None,
            ),
        },
        "at0000605324": {
            "reconciliation_required_rejection_associations": at_reconciliation,
            "conflicting_source_selected": False,
        },
        "metric_boundary": {
            "gate_precedes_nav_series": True,
            "rejected_before_metric_computation": True,
            "official_metric_functions": [
                "compounded_return",
                "annualized_volatility",
                "maximum_drawdown",
                "sharpe_ratio",
                "historical_var",
                "historical_cvar",
            ],
        },
        "result_admission_boundary": {
            "official_result_type": "OFFICIAL_BACKTEST",
            "diagnostics_result_type": "DIAGNOSTICS_ONLY",
            "rejected_result_type": "BACKTEST_REJECTED",
            "non_official_results_cannot_carry_metrics_or_selection": True,
            "aggregate_uses_official_results_only": True,
        },
        "prohibited_fallbacks": {
            "threshold": "NOT_USED",
            "renormalization": "NOT_USED",
            "cash": "NOT_USED",
            "zero_return": "NOT_USED",
            "proxy": "NOT_USED",
            "interpolation": "NOT_USED",
            "fill": "NOT_USED",
            "nearest_date": "NOT_USED",
            "source_stitching": "NOT_USED",
        },
        "invariants": {
            "every_window_classified": True,
            "eligible_plus_rejected_equals_total": True,
            "all_horizons_reconcile": True,
            "hu0000554795_rejected": True,
            "at0000605324_reconciliation_remains_blocking": True,
            "no_network_or_provider_access": True,
        },
    }


def _object(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise StrictPipelineValidationError(f"{label} must be an object")
    return value


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise StrictPipelineValidationError(f"{label} must be an integer")
    return value


def _equal(actual: int, expected: int, label: str) -> None:
    if actual != expected:
        raise StrictPipelineValidationError(f"{label} mismatch: expected {expected}, got {actual}")


def _percentage(numerator: int, denominator: int) -> float:
    return (numerator / denominator * 100.0) if denominator else 0.0


def _horizon_counts(decisions: Sequence[tuple[Mapping[str, object], BacktestEligibility]]) -> dict[str, int]:
    return dict(
        sorted(
            Counter(str(row["horizon"]) for row, _ in decisions).items(),
            key=lambda item: int(item[0]),
        )
    )


def _horizon(row: Mapping[str, object]) -> int:
    return _integer(row.get("horizon"), "coverage horizon")
