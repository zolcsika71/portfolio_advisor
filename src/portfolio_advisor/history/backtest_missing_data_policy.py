"""Audit-only portfolio missing-data policy design and simulation.

This module deliberately operates between constituent evidence and a future
portfolio backtest policy.  It consumes read-only coverage/holding snapshots,
does not call the backtester, and never creates a portfolio return series.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from statistics import median
from typing import Any

RESOLUTION_STATUS = "BACKTEST_UNRESOLVABLE_WITH_CURRENT_PUBLIC_EVIDENCE"
CASE_STUDY_ISIN = "HU0000554795"
WEIGHT_TOTAL = Decimal(100)
WEIGHT_TOLERANCE = Decimal("0.000001")
THRESHOLDS = (Decimal(100), Decimal(99), Decimal(95), Decimal(90), Decimal(80))


class MissingDataPolicyError(RuntimeError):
    """Coverage or composition inputs cannot safely support an audit simulation."""


@dataclass(frozen=True, slots=True)
class PolicyDefinition:
    """A candidate policy only; none are consumed by production backtesting."""

    policy_id: str
    name: str
    description: str
    production_approved: bool
    return_calculation_allowed: bool
    constituent_exclusion_allowed: bool
    weight_renormalization_allowed: bool
    proxy_allowed: bool
    cash_assumption_allowed: bool
    simulation_only: bool
    metric_scope: str
    eligibility_rule: str
    methodological_risks: tuple[str, ...]
    diagnostics_allowed: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "policy_id": self.policy_id,
            "name": self.name,
            "description": self.description,
            "production_approved": self.production_approved,
            "return_calculation_allowed": self.return_calculation_allowed,
            "constituent_exclusion_allowed": self.constituent_exclusion_allowed,
            "weight_renormalization_allowed": self.weight_renormalization_allowed,
            "proxy_allowed": self.proxy_allowed,
            "cash_assumption_allowed": self.cash_assumption_allowed,
            "simulation_only": self.simulation_only,
            "metric_scope": self.metric_scope,
            "eligibility_rule": self.eligibility_rule,
            "methodological_risks": list(self.methodological_risks),
            "diagnostics_allowed": self.diagnostics_allowed,
        }


def policy_definitions() -> tuple[PolicyDefinition, ...]:
    """Return the required unapproved candidate policies in stable order."""
    return (
        PolicyDefinition(
            "STRICT_REJECT_WINDOW",
            "Strict reject window",
            "Reject a window when any required constituent lacks approved history.",
            False,
            False,
            False,
            False,
            False,
            False,
            True,
            "Full-portfolio historical metrics only after separate approval.",
            "resolvable_weight == 100%",
            ("reduced sample size", "data-availability selection bias"),
        ),
        PolicyDefinition(
            "PARTIAL_DIAGNOSTICS_ONLY",
            "Partial diagnostics only",
            "Retain composition and data-quality diagnostics but no official return metric.",
            False,
            False,
            False,
            False,
            False,
            False,
            True,
            "Composition, unresolved-weight, and data-quality diagnostics.",
            "always diagnostic; official returns prohibited when unresolved_weight > 0",
            ("can be mistaken for performance coverage if labels are ignored",),
            diagnostics_allowed=True,
        ),
        PolicyDefinition(
            "MINIMUM_RESOLVABLE_WEIGHT_THRESHOLD",
            "Minimum resolvable weight threshold",
            "Classify partial-methodology candidates at a stated resolvable-weight threshold.",
            False,
            False,
            False,
            False,
            False,
            False,
            True,
            "Threshold eligibility simulation only.",
            "resolvable_weight >= declared threshold",
            ("partial portfolio exposure", "threshold arbitrariness", "selection bias"),
        ),
        PolicyDefinition(
            "EXCLUDE_AND_RENORMALIZE",
            "Exclude and renormalize",
            "Drop unresolved holdings and scale the remaining holdings to 100% in simulation.",
            False,
            False,
            False,
            False,
            False,
            False,
            True,
            "Portfolio-definition distortion diagnostics only.",
            "resolvable_weight > 0; hypothetical weights sum to 100%",
            ("changes exposure", "concentration increase", "downside-risk understatement"),
        ),
        PolicyDefinition(
            "HOLD_UNRESOLVED_WEIGHT_AS_CASH",
            "Hold unresolved weight as cash",
            "Describe the cash-weight requirement without assuming a cash return.",
            False,
            False,
            False,
            False,
            False,
            False,
            True,
            "Structural cash-assumption diagnostics only.",
            "METHODOLOGY_NOT_SPECIFIED",
            ("cash return and reinvestment assumptions", "portfolio-definition change"),
        ),
        PolicyDefinition(
            "ZERO_RETURN_FOR_UNRESOLVED_WEIGHT",
            "Zero return for unresolved weight",
            "Counterfactual sensitivity only: assign a zero return to unresolved weight.",
            False,
            False,
            False,
            False,
            False,
            False,
            True,
            "Counterfactual structural sensitivity only; no returns are calculated.",
            "R = sum(resolved w_i*r_i) + unresolved_weight*0",
            ("zero is an economic assumption", "risk and return bias"),
        ),
        PolicyDefinition(
            "PROXY_RETURN",
            "Proxy return",
            "Conceptual only; no proxy mapping or proxy series is created.",
            False,
            False,
            False,
            False,
            False,
            False,
            True,
            "No metric scope until a separate proxy methodology is approved.",
            "NOT_APPROVED",
            ("basis risk", "duration/credit/currency/liquidity/issuer mismatch"),
        ),
    )


def _decimal(value: object, label: str) -> Decimal:
    if isinstance(value, bool):
        raise MissingDataPolicyError(f"{label} must be numeric")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise MissingDataPolicyError(f"{label} must be numeric") from exc
    if not result.is_finite():
        raise MissingDataPolicyError(f"{label} must be finite")
    return result


def _number(value: Decimal) -> float:
    """Serialize audit weights as numbers without using them for price calculations."""
    return float(value)


def _stat(values: Iterable[Decimal]) -> dict[str, float] | None:
    ordered = sorted(values)
    if not ordered:
        return None
    return {
        "minimum": _number(ordered[0]),
        "median": _number(median(ordered)),
        "maximum": _number(ordered[-1]),
    }


def _mean(values: Iterable[Decimal]) -> float | None:
    ordered = list(values)
    if not ordered:
        return None
    return _number(sum(ordered, Decimal()) / Decimal(len(ordered)))


def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise MissingDataPolicyError(f"{label} must be a list")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MissingDataPolicyError(f"{label} must be a non-empty string")
    return value.strip()


def aggregate_holdings(holdings: Iterable[Mapping[str, object]]) -> tuple[dict[str, object], ...]:
    """Validate and aggregate a source snapshot by exact ISIN without mutation."""
    weights: dict[str, Decimal] = defaultdict(Decimal)
    asset_classes: dict[str, str | None] = {}
    currencies: dict[str, str | None] = {}
    count = 0
    for holding in holdings:
        isin = _text(holding.get("isin"), "holding ISIN").upper()
        weight = _decimal(holding.get("allocation"), f"{isin} allocation")
        if weight < 0:
            raise MissingDataPolicyError(f"{isin} has a negative allocation")
        asset_class = holding.get("asset_class")
        currency = holding.get("currency")
        if asset_class is not None and not isinstance(asset_class, str):
            raise MissingDataPolicyError(f"{isin} asset class is malformed")
        if currency is not None and not isinstance(currency, str):
            raise MissingDataPolicyError(f"{isin} currency is malformed")
        if isin in asset_classes and asset_classes[isin] != asset_class:
            raise MissingDataPolicyError(f"{isin} has conflicting asset classes")
        if isin in currencies and currencies[isin] != currency:
            raise MissingDataPolicyError(f"{isin} has conflicting currencies")
        weights[isin] += weight
        asset_classes[isin] = asset_class
        currencies[isin] = currency
        count += 1
    if not count:
        raise MissingDataPolicyError("portfolio snapshot has no holdings")
    total = sum(weights.values(), Decimal())
    if abs(total - WEIGHT_TOTAL) > WEIGHT_TOLERANCE:
        raise MissingDataPolicyError(
            f"source portfolio weights total {_number(total)}, not {_number(WEIGHT_TOTAL)}"
        )
    return tuple(
        {
            "isin": isin,
            "weight": weight,
            "asset_class": asset_classes[isin],
            "currency": currencies[isin],
        }
        for isin, weight in sorted(weights.items())
    )


def _type_for_unresolved(
    isin: str, window_status: str, missing: set[str], terminal_isins: set[str]
) -> str:
    if isin in terminal_isins:
        return "TERMINAL_UNRESOLVABLE"
    if window_status == "RECONCILIATION_REQUIRED":
        return "RECONCILIATION_REQUIRED"
    if window_status == "WRONG_INSTRUMENT_TYPE":
        return "WRONG_INSTRUMENT_TYPE"
    if isin in missing:
        return "TEMPORARY_DATA_GAP"
    return "OTHER_UNUSABLE"


def _concentration(weights: Iterable[Decimal]) -> dict[str, float]:
    values = sorted(weights, reverse=True)
    shares = [value / WEIGHT_TOTAL for value in values]
    return {
        "largest_weight": _number(values[0]) if values else 0.0,
        "top_3_concentration": _number(sum(values[:3], Decimal())),
        "herfindahl_hirschman_index": _number(sum((share * share for share in shares), Decimal())),
    }


def _category_weights(
    holdings: Iterable[Mapping[str, object]], field: str
) -> dict[str, Decimal] | None:
    values: dict[str, Decimal] = defaultdict(Decimal)
    for holding in holdings:
        category = holding.get(field)
        if not isinstance(category, str) or not category.strip():
            return None
        values[category] += _decimal(holding.get("weight"), "holding weight")
    return values


def _category_shift(
    original: Iterable[Mapping[str, object]], hypothetical: Iterable[Mapping[str, object]], field: str
) -> Decimal | None:
    original_weights = _category_weights(original, field)
    hypothetical_weights = _category_weights(hypothetical, field)
    if original_weights is None or hypothetical_weights is None:
        return None
    keys = set(original_weights) | set(hypothetical_weights)
    result = sum(
        (
            abs(original_weights.get(key, Decimal()) - hypothetical_weights.get(key, Decimal()))
            for key in keys
        ),
        Decimal(),
    ) / Decimal(2)
    return Decimal() if abs(result) <= WEIGHT_TOLERANCE else result


def _renormalization(
    holdings: tuple[dict[str, object], ...], unresolved: set[str]
) -> dict[str, object]:
    retained = tuple(item for item in holdings if item["isin"] not in unresolved)
    dropped = tuple(item for item in holdings if item["isin"] in unresolved)
    resolvable_weight = sum((_decimal(item["weight"], "holding weight") for item in retained), Decimal())
    if not unresolved:
        return {
            "simulation_outcome": "NOT_APPLICABLE_FULLY_RESOLVABLE",
            "renormalization_factor": 1.0,
            "dropped_constituent_count": 0,
            "dropped_weight": 0.0,
            "original_concentration": _concentration(
                _decimal(item["weight"], "holding weight") for item in holdings
            ),
            "hypothetical_concentration": _concentration(
                _decimal(item["weight"], "holding weight") for item in holdings
            ),
            "hypothetical_renormalized_weights": [
                {"isin": item["isin"], "weight": _number(_decimal(item["weight"], "holding weight"))}
                for item in holdings
            ],
            "asset_class_allocation_shift_percentage_points": 0.0,
            "currency_allocation_shift_percentage_points": 0.0,
        }
    if resolvable_weight <= 0:
        return {
            "simulation_outcome": "REJECTED_ZERO_RESOLVABLE_WEIGHT",
            "renormalization_factor": None,
            "dropped_constituent_count": len(dropped),
            "dropped_weight": _number(WEIGHT_TOTAL),
            "original_concentration": _concentration(
                _decimal(item["weight"], "holding weight") for item in holdings
            ),
            "hypothetical_concentration": None,
            "hypothetical_renormalized_weights": [],
            "asset_class_allocation_shift_percentage_points": None,
            "currency_allocation_shift_percentage_points": None,
        }
    factor = WEIGHT_TOTAL / resolvable_weight
    renormalized = tuple(
        {**item, "weight": _decimal(item["weight"], "holding weight") * factor}
        for item in retained
    )
    asset_shift = _category_shift(holdings, renormalized, "asset_class")
    currency_shift = _category_shift(holdings, renormalized, "currency")
    return {
        "simulation_outcome": "SIMULATION_ONLY_RENORMALIZED",
        "renormalization_factor": _number(factor),
        "dropped_constituent_count": len(dropped),
        "dropped_weight": _number(WEIGHT_TOTAL - resolvable_weight),
        "original_concentration": _concentration(
            _decimal(item["weight"], "holding weight") for item in holdings
        ),
        "hypothetical_concentration": _concentration(
            _decimal(item["weight"], "holding weight") for item in renormalized
        ),
        "hypothetical_renormalized_weights": [
            {"isin": item["isin"], "weight": _number(_decimal(item["weight"], "holding weight"))}
            for item in renormalized
        ],
        "asset_class_allocation_shift_percentage_points": (
            _number(asset_shift) if asset_shift is not None else None
        ),
        "currency_allocation_shift_percentage_points": (
            _number(currency_shift) if currency_shift is not None else None
        ),
    }


def simulate_windows(
    coverage_windows: Iterable[Mapping[str, object]],
    holdings_by_snapshot: Mapping[tuple[str, str], Iterable[Mapping[str, object]]],
    terminal_isins: set[str],
    lifecycle_by_window: Mapping[tuple[str, str, int], Mapping[str, object]] | None = None,
) -> list[dict[str, Any]]:
    """Simulate policy eligibility from immutable coverage and holding snapshots."""
    records: list[dict[str, Any]] = []
    for raw in coverage_windows:
        observation_date = _text(raw.get("observation_date"), "window observation_date")
        portfolio_name = _text(raw.get("portfolio_name"), "window portfolio_name")
        status = _text(raw.get("status"), "window status")
        horizon = raw.get("horizon")
        if isinstance(horizon, bool) or not isinstance(horizon, int):
            raise MissingDataPolicyError("window horizon must be an integer")
        required = {str(value).strip().upper() for value in _list(raw.get("required_isins"), "required_isins")}
        missing = {str(value).strip().upper() for value in _list(raw.get("missing_isins"), "missing_isins")}
        unusable = {str(value).strip().upper() for value in _list(raw.get("unusable_isins"), "unusable_isins")}
        unresolved = missing | unusable
        if not unresolved.issubset(required):
            raise MissingDataPolicyError("coverage has unresolved ISIN outside required constituents")
        snapshot_key = (observation_date, portfolio_name)
        if snapshot_key not in holdings_by_snapshot:
            raise MissingDataPolicyError(f"holding snapshot missing for {snapshot_key!r}")
        holdings = aggregate_holdings(holdings_by_snapshot[snapshot_key])
        holding_isins = {str(item["isin"]) for item in holdings}
        if holding_isins != required:
            raise MissingDataPolicyError("coverage constituent set does not match source holdings")
        weight_by_isin = {str(item["isin"]): _decimal(item["weight"], "holding weight") for item in holdings}
        unresolved_weight = sum((weight_by_isin[isin] for isin in unresolved), Decimal())
        resolvable_weight = WEIGHT_TOTAL - unresolved_weight
        unresolved_types = {
            isin: _type_for_unresolved(isin, status, missing, terminal_isins)
            for isin in sorted(unresolved)
        }
        terminal = sorted(isin for isin in unresolved if isin in terminal_isins)
        lifecycle = (
            lifecycle_by_window.get((observation_date, portfolio_name, horizon))
            if lifecycle_by_window is not None
            else None
        )
        renormalization = _renormalization(holdings, unresolved)
        threshold_results = {
            str(int(threshold)): {
                "threshold": _number(threshold),
                "simulation_outcome": (
                    "FULL_PORTFOLIO_ELIGIBLE_IF_APPROVED"
                    if unresolved_weight == 0
                    else "PARTIAL_METHODOLOGY_CANDIDATE"
                    if resolvable_weight >= threshold
                    else "REJECTED_BY_THRESHOLD"
                ),
                "eligible": resolvable_weight >= threshold,
                "unresolved_weight_retained_in_definition": _number(unresolved_weight),
                "full_portfolio_fidelity": unresolved_weight == 0,
            }
            for threshold in THRESHOLDS
        }
        records.append(
            {
                "portfolio_name": portfolio_name,
                "observation_date": observation_date,
                "horizon": horizon,
                "required_start": _text(raw.get("required_start"), "required_start"),
                "required_end": _text(raw.get("required_end"), "required_end"),
                "current_coverage_status": status,
                "lifecycle_classification": (
                    lifecycle.get("lifecycle_classification") if lifecycle is not None else None
                ),
                "constituent_count": len(holdings),
                "source_weights": [
                    {
                        "isin": item["isin"],
                        "weight": _number(_decimal(item["weight"], "holding weight")),
                        "asset_class": item["asset_class"],
                        "currency": item["currency"],
                    }
                    for item in holdings
                ],
                "total_portfolio_weight": _number(WEIGHT_TOTAL),
                "resolvable_weight": _number(resolvable_weight),
                "unresolved_weight": _number(unresolved_weight),
                "unresolved_constituent_count": len(unresolved),
                "unresolved_isins": sorted(unresolved),
                "terminal_unresolved_isins": terminal,
                "terminal_constituent_weights": {
                    isin: _number(weight_by_isin[isin]) for isin in terminal
                },
                "unresolved_type_by_isin": unresolved_types,
                "strict_reject": {
                    "eligible": unresolved_weight == 0,
                    "simulation_outcome": (
                        "FULL_PORTFOLIO_ELIGIBLE_IF_APPROVED"
                        if unresolved_weight == 0
                        else "REJECTED_UNRESOLVED_CONSTITUENT"
                    ),
                },
                "partial_diagnostics": {
                    "simulation_outcome": (
                        "FULL_COVERAGE_DIAGNOSTICS"
                        if unresolved_weight == 0
                        else "DIAGNOSTICS_ONLY_UNRESOLVED_CONSTITUENT"
                    ),
                    "official_return_calculation_allowed": False,
                },
                "thresholds": threshold_results,
                "renormalization": renormalization,
                "cash": {
                    "simulation_outcome": "METHODOLOGY_NOT_SPECIFIED",
                    "hypothetical_cash_weight": _number(unresolved_weight),
                    "return_simulation_executed": False,
                },
                "zero_return": {
                    "simulation_outcome": "COUNTERFACTUAL_SENSITIVITY_ONLY",
                    "formula": "R = sum(resolved w_i*r_i) + unresolved_weight*0",
                    "return_simulation_executed": False,
                },
                "proxy": {
                    "simulation_outcome": "NOT_APPROVED",
                    "proxy_series_created": False,
                },
            }
        )
    return sorted(records, key=lambda item: (item["observation_date"], item["horizon"], item["portfolio_name"]))


def _horizon_counts(
    records: Iterable[Mapping[str, Any]], predicate: Callable[[Mapping[str, Any]], bool]
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for record in records:
        if predicate(record):
            counts[str(record["horizon"])] += 1
    return dict(sorted(counts.items(), key=lambda item: int(item[0])))


def _portfolio_counts(records: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts = Counter(str(record["portfolio_name"]) for record in records)
    return dict(sorted(counts.items()))


def _threshold_summary(records: list[dict[str, Any]], threshold: Decimal) -> dict[str, object]:
    key = str(int(threshold))
    accepted = [item for item in records if bool(item["thresholds"][key]["eligible"])]
    admitted_unresolved = [item for item in accepted if float(item["unresolved_weight"]) > 0]
    admitted_terminal = [item for item in admitted_unresolved if item["terminal_unresolved_isins"]]
    admitted_case_study = [
        item for item in admitted_unresolved if CASE_STUDY_ISIN in item["terminal_unresolved_isins"]
    ]
    return {
        "threshold": _number(threshold),
        "eligible_windows": len(accepted),
        "rejected_windows": len(records) - len(accepted),
        "horizon_counts": _horizon_counts(accepted, lambda _: True),
        "admitted_unresolved_windows": len(admitted_unresolved),
        "admitted_terminal_unresolved_windows": len(admitted_terminal),
        "admitted_hu0000554795_windows": len(admitted_case_study),
        "mean_unresolved_weight_for_accepted_windows": _mean(
            Decimal(str(item["unresolved_weight"])) for item in accepted
        ),
        "accepted_unresolved_weight": _stat(
            Decimal(str(item["unresolved_weight"])) for item in accepted
        ),
        "maximum_unresolved_weight_for_accepted_windows": (
            max((float(item["unresolved_weight"]) for item in accepted), default=0.0)
        ),
        "full_portfolio_fidelity_required": threshold == WEIGHT_TOTAL,
    }


def build_policy_analysis(
    *,
    coverage_payload: Mapping[str, object],
    holdings_by_snapshot: Mapping[tuple[str, str], Iterable[Mapping[str, object]]],
    terminal_resolutions: Mapping[str, Mapping[str, object]],
    artifact_references: Mapping[str, str],
    lifecycle_by_window: Mapping[tuple[str, str, int], Mapping[str, object]] | None = None,
) -> dict[str, Any]:
    """Build a deterministic policy-design artifact without calculating returns."""
    windows = _list(coverage_payload.get("windows"), "coverage windows")
    terminal_isins: set[str] = set()
    for isin, resolution in terminal_resolutions.items():
        if resolution.get("isin") != isin or resolution.get("resolution_status") != RESOLUTION_STATUS:
            raise MissingDataPolicyError("terminal resolution is invalid")
        admission = resolution.get("backtest_admission")
        if (
            resolution.get("research_closed") is not True
            or not isinstance(admission, Mapping)
            or any(admission.get(field) is not False for field in (
                "nav_equivalent", "backtest_return_series_approved", "usable_for_backtest"
            ))
        ):
            raise MissingDataPolicyError("terminal resolution has unsafe admission semantics")
        terminal_isins.add(isin)
    if not terminal_isins:
        raise MissingDataPolicyError("at least one terminal resolution is required")
    records = simulate_windows(
        (item for item in windows if isinstance(item, Mapping)),
        holdings_by_snapshot,
        terminal_isins,
        lifecycle_by_window,
    )
    if len(records) != len(windows):
        raise MissingDataPolicyError("coverage contains a malformed window")
    unresolved = [item for item in records if float(item["unresolved_weight"]) > 0]
    terminal = [item for item in records if item["terminal_unresolved_isins"]]
    distinct_unresolved = sorted(
        {isin for item in unresolved for isin in item["unresolved_isins"]}
    )
    unresolved_category_counts = Counter(
        category
        for item in unresolved
        for category in item["unresolved_type_by_isin"].values()
    )
    strict_eligible = [item for item in records if bool(item["strict_reject"]["eligible"])]
    hu = [item for item in records if "HU0000554795" in item["terminal_unresolved_isins"]]
    # The exact HU weight is derived from total unresolved detail when it is the
    # sole blocker; the source portfolio snapshot remains untouched.
    hu_exact_weights: list[Decimal] = []
    for record in hu:
        snapshot = aggregate_holdings(
            holdings_by_snapshot[(str(record["observation_date"]), str(record["portfolio_name"]))]
        )
        hu_exact_weights.append(
            next(_decimal(item["weight"], "HU allocation") for item in snapshot if item["isin"] == "HU0000554795")
        )
    return {
        "schema_version": 1,
        "scope": "audit-only portfolio missing-data policy design; no return calculation",
        "production_approved": False,
        "return_simulation_not_executed": True,
        "source_artifact_references": dict(sorted(artifact_references.items())),
        "terminal_resolution_isins": sorted(terminal_isins),
        "policy_definitions": [item.as_dict() for item in policy_definitions()],
        "current_dataset": {
            "total_actual_windows": len(records),
            "windows_with_any_unresolved_constituent": len(unresolved),
            "windows_with_terminal_unresolved_constituent": len(terminal),
            "distinct_unresolved_isins": len(distinct_unresolved),
            "unresolved_category_counts": dict(sorted(unresolved_category_counts.items())),
            "unresolved_horizon_counts": _horizon_counts(unresolved, lambda _: True),
            "terminal_unresolved_horizon_counts": _horizon_counts(terminal, lambda _: True),
            "unresolved_portfolio_counts": _portfolio_counts(unresolved),
            "terminal_unresolved_portfolio_counts": _portfolio_counts(terminal),
            "unresolved_weight": _stat(Decimal(str(item["unresolved_weight"])) for item in unresolved),
        },
        "hu0000554795_case_study": {
            "affected_windows": len(hu),
            "sole_unresolved_windows": sum(len(item["unresolved_isins"]) == 1 for item in hu),
            "multi_blocker_windows": sum(len(item["unresolved_isins"]) > 1 for item in hu),
            "horizon_counts": _horizon_counts(hu, lambda _: True),
            "lifecycle_classification_counts": dict(
                sorted(
                    Counter(
                        str(item["lifecycle_classification"])
                        for item in hu
                        if item["lifecycle_classification"] is not None
                    ).items()
                )
            ),
            "portfolio_counts": _portfolio_counts(hu),
            "hu0000554795_weight": _stat(hu_exact_weights),
            "total_unresolved_weight": _stat(Decimal(str(item["unresolved_weight"])) for item in hu),
        },
        "policy_simulation_summaries": {
            "STRICT_REJECT_WINDOW": {
                "eligible_windows": len(strict_eligible),
                "rejected_windows": len(records) - len(strict_eligible),
                "retention_percentage": _number(Decimal(len(strict_eligible)) / Decimal(len(records)) * WEIGHT_TOTAL),
                "eligible_horizon_counts": _horizon_counts(strict_eligible, lambda _: True),
                "eligible_portfolio_counts": _portfolio_counts(strict_eligible),
                "rejected_portfolio_counts": _portfolio_counts(unresolved),
                "rejected_due_solely_to_terminal_unresolved": sum(
                    bool(item["terminal_unresolved_isins"])
                    and len(item["unresolved_isins"]) == 1
                    for item in records
                ),
                "rejected_with_multiple_blockers": sum(
                    len(item["unresolved_isins"]) > 1 for item in records
                ),
                "rejected_by_unresolved_category": dict(sorted(unresolved_category_counts.items())),
                "hu0000554795_rejected_windows": len(hu),
            },
            "PARTIAL_DIAGNOSTICS_ONLY": {
                "full_return_eligible_windows": len(strict_eligible),
                "diagnostics_only_windows": len(unresolved),
                "completely_unusable_windows": sum(float(item["resolvable_weight"]) == 0 for item in records),
                "unresolved_weight": _stat(Decimal(str(item["unresolved_weight"])) for item in unresolved),
                "diagnostics_only_horizon_counts": _horizon_counts(unresolved, lambda _: True),
                "official_return_calculation_allowed": False,
            },
            "MINIMUM_RESOLVABLE_WEIGHT_THRESHOLD": {
                str(int(threshold)): _threshold_summary(records, threshold) for threshold in THRESHOLDS
            },
            "EXCLUDE_AND_RENORMALIZE": {
                "affected_windows": len(unresolved),
                "unresolved_weight": _stat(Decimal(str(item["unresolved_weight"])) for item in unresolved),
                "renormalization_factor": _stat(
                    Decimal(str(item["renormalization"]["renormalization_factor"])) for item in unresolved
                    if item["renormalization"]["renormalization_factor"] is not None
                ),
                "largest_weight_increase_percentage_points": _stat(
                    Decimal(str(item["renormalization"]["hypothetical_concentration"]["largest_weight"]))
                    - Decimal(str(item["renormalization"]["original_concentration"]["largest_weight"]))
                    for item in unresolved
                    if item["renormalization"]["hypothetical_concentration"] is not None
                ),
                "top_3_concentration_increase_percentage_points": _stat(
                    Decimal(str(item["renormalization"]["hypothetical_concentration"]["top_3_concentration"]))
                    - Decimal(str(item["renormalization"]["original_concentration"]["top_3_concentration"]))
                    for item in unresolved
                    if item["renormalization"]["hypothetical_concentration"] is not None
                ),
                "hhi_increase": _stat(
                    Decimal(str(item["renormalization"]["hypothetical_concentration"]["herfindahl_hirschman_index"]))
                    - Decimal(str(item["renormalization"]["original_concentration"]["herfindahl_hirschman_index"]))
                    for item in unresolved
                    if item["renormalization"]["hypothetical_concentration"] is not None
                ),
                "asset_class_allocation_shift_percentage_points": _stat(
                    Decimal(str(item["renormalization"]["asset_class_allocation_shift_percentage_points"]))
                    for item in unresolved
                    if item["renormalization"]["asset_class_allocation_shift_percentage_points"] is not None
                ),
                "asset_class_allocation_shift_status": (
                    "AVAILABLE"
                    if any(
                        item["renormalization"]["asset_class_allocation_shift_percentage_points"] is not None
                        for item in unresolved
                    )
                    else "NOT_AVAILABLE_FROM_CURRENT_READ_ONLY_HOLDING_INTERFACE"
                ),
                "currency_allocation_shift_percentage_points": _stat(
                    Decimal(str(item["renormalization"]["currency_allocation_shift_percentage_points"]))
                    for item in unresolved
                    if item["renormalization"]["currency_allocation_shift_percentage_points"] is not None
                ),
                "return_simulation_executed": False,
            },
            "HOLD_UNRESOLVED_WEIGHT_AS_CASH": {
                "status": "METHODOLOGY_NOT_SPECIFIED",
                "return_simulation_executed": False,
            },
            "ZERO_RETURN_FOR_UNRESOLVED_WEIGHT": {
                "status": "COUNTERFACTUAL_SENSITIVITY_ONLY",
                "return_simulation_executed": False,
            },
            "PROXY_RETURN": {"status": "NOT_APPROVED", "proxy_series_created": False},
        },
        "policy_matrix": [
            {
                "policy_id": "STRICT_REJECT_WINDOW",
                "full_portfolio_fidelity": "HIGH",
                "return_comparability": "HIGH_WHEN_ELIGIBLE",
                "sample_retention": "LOWER",
                "implementation_complexity": "LOW",
                "bias_risk": "LOWEST",
                "auditability": "HIGH",
                "reproducibility": "HIGH",
                "capital_preservation_ranking_suitability": "PREFERRED_FUTURE_CANDIDATE",
                "production_recommendation": "RECOMMENDED_FOR_FUTURE_APPROVAL",
            },
            {
                "policy_id": "PARTIAL_DIAGNOSTICS_ONLY",
                "full_portfolio_fidelity": "HIGH",
                "return_comparability": "NOT_ALLOWED",
                "sample_retention": "HIGH_FOR_DIAGNOSTICS",
                "implementation_complexity": "LOW",
                "bias_risk": "LOW_IF_NOT_MISLABELLED",
                "auditability": "HIGH",
                "reproducibility": "HIGH",
                "capital_preservation_ranking_suitability": "DIAGNOSTICS_ONLY",
                "production_recommendation": "OPTIONAL_AUDIT_MODE_FOR_FUTURE_APPROVAL",
            },
            {
                "policy_id": "MINIMUM_RESOLVABLE_WEIGHT_THRESHOLD",
                "full_portfolio_fidelity": "LOWER_THAN_FULL",
                "return_comparability": "NOT_ALLOWED",
                "sample_retention": "MEDIUM",
                "implementation_complexity": "MEDIUM",
                "bias_risk": "HIGH",
                "auditability": "MEDIUM",
                "reproducibility": "HIGH",
                "capital_preservation_ranking_suitability": "NOT_RECOMMENDED",
                "production_recommendation": "NOT_APPROVED",
            },
            {
                "policy_id": "EXCLUDE_AND_RENORMALIZE",
                "full_portfolio_fidelity": "LOW",
                "return_comparability": "NOT_ALLOWED",
                "sample_retention": "HIGH",
                "implementation_complexity": "MEDIUM",
                "bias_risk": "HIGH",
                "auditability": "MEDIUM",
                "reproducibility": "HIGH",
                "capital_preservation_ranking_suitability": "NOT_RECOMMENDED",
                "production_recommendation": "NOT_APPROVED",
            },
            {
                "policy_id": "HOLD_UNRESOLVED_WEIGHT_AS_CASH",
                "full_portfolio_fidelity": "LOW",
                "return_comparability": "NOT_ALLOWED",
                "sample_retention": "HIGH",
                "implementation_complexity": "HIGH",
                "bias_risk": "HIGH",
                "auditability": "LOW",
                "reproducibility": "LOW",
                "capital_preservation_ranking_suitability": "NOT_RECOMMENDED",
                "production_recommendation": "NOT_APPROVED",
            },
            {
                "policy_id": "ZERO_RETURN_FOR_UNRESOLVED_WEIGHT",
                "full_portfolio_fidelity": "LOW",
                "return_comparability": "NOT_ALLOWED",
                "sample_retention": "HIGH",
                "implementation_complexity": "LOW",
                "bias_risk": "HIGH",
                "auditability": "LOW",
                "reproducibility": "HIGH",
                "capital_preservation_ranking_suitability": "NOT_RECOMMENDED",
                "production_recommendation": "NOT_APPROVED",
            },
            {
                "policy_id": "PROXY_RETURN",
                "full_portfolio_fidelity": "LOW",
                "return_comparability": "NOT_ALLOWED",
                "sample_retention": "HIGH",
                "implementation_complexity": "HIGH",
                "bias_risk": "HIGH",
                "auditability": "LOW",
                "reproducibility": "LOW",
                "capital_preservation_ranking_suitability": "NOT_RECOMMENDED",
                "production_recommendation": "NOT_APPROVED",
            },
        ],
        "metric_specific_eligibility": {
            "composition_and_data_quality": {
                "status": "DIAGNOSTICS_PERMITTED",
                "requires_return_series": False,
                "partial_diagnostics_policy": "PARTIAL_DIAGNOSTICS_ONLY",
            },
            "historical_return": {
                "status": "NOT_APPROVED_WITH_UNRESOLVED_CONSTITUENTS",
                "requires_full_portfolio_history": True,
            },
            "volatility": {
                "status": "NOT_APPROVED_WITH_UNRESOLVED_CONSTITUENTS",
                "requires_dense_valid_return_series": True,
            },
            "sharpe_ratio": {
                "status": "NOT_APPROVED_WITH_UNRESOLVED_CONSTITUENTS",
                "requires_valid_return_series_and_risk_free_methodology": True,
            },
            "maximum_drawdown": {
                "status": "NOT_APPROVED_WITH_UNRESOLVED_CONSTITUENTS",
                "requires_path_complete_series": True,
            },
            "var_cvar": {
                "status": "NOT_APPROVED_WITH_UNRESOLVED_CONSTITUENTS",
                "requires_distribution_and_history_methodology": True,
            },
        },
        "bias_analysis": {
            "sample_size_bias": "Strict rejection reduces history and can favor portfolios with easier-to-source constituents.",
            "exclusion_bias": "Dropping an unresolved constituent changes the economic portfolio.",
            "renormalization_bias": "Scaling remaining holdings increases their implicit exposure and can understate omitted downside risk.",
            "zero_return_bias": "A zero return is an economic assumption, not a neutral missing-data treatment.",
            "proxy_basis_risk": "A proxy can differ in duration, credit, currency, liquidity, issuer, and path behavior.",
            "capital_preservation_risk": "Removing or neutralizing an unresolved risky holding can make volatility, drawdown, tail risk, and ranking appear safer than the actual portfolio.",
        },
        "recommendation": {
            "primary_policy_candidate": "STRICT_REJECT_WINDOW",
            "primary_status": "RECOMMENDED_FOR_FUTURE_APPROVAL",
            "optional_analytical_mode": "PARTIAL_DIAGNOSTICS_ONLY",
            "optional_status": "OPTIONAL_AUDIT_MODE_FOR_FUTURE_APPROVAL",
            "rationale": "Preserve the actual portfolio definition for comparable capital-preservation metrics; retain composition diagnostics without inventing returns.",
            "production_approved": False,
            "explicit_approval_required": True,
        },
        "next_task": "IMPLEMENT_STRICT_BACKTEST_WITH_PARTIAL_DIAGNOSTICS_MODE",
        "window_simulations": records,
    }
