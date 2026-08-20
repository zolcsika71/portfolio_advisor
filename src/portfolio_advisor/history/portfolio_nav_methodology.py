"""Offline, fail-closed audit of portfolio-NAV construction methodology.

This module deliberately evaluates evidence and does not construct a portfolio
NAV, modify the model database, or admit forward labels.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
from collections import Counter
from datetime import date
from itertools import pairwise
from pathlib import Path

from portfolio_advisor.database.repository import (
    HoldingObservation,
    ModelPortfolioRepository,
)
from portfolio_advisor.history.official_nav_store import OfficialNavStore

METHODOLOGY_STATUS = "PORTFOLIO_NAV_METHODOLOGY_BLOCKED"
ACTIVATION_STATE = "NOT_ACTIVATED"
THEORETICAL_CATEGORIES = (
    "THEORETICALLY_CONSTRUCTIBLE",
    "BLOCKED_MISSING_CONSTITUENT_HISTORY",
    "BLOCKED_STRICT_ELIGIBILITY",
    "BLOCKED_FX",
    "BLOCKED_SEMANTICS",
)


class PortfolioNavMethodologyError(RuntimeError):
    """Methodology evidence cannot be audited deterministically."""


def build_portfolio_nav_methodology_audit(
    *,
    database_path: Path,
    nav_store_path: Path,
    strict_validation_path: Path,
    label_store_path: Path,
) -> dict[str, object]:
    """Audit local evidence without deriving a portfolio performance series."""
    repository = ModelPortfolioRepository(database_path)
    dates = repository.observation_dates()
    snapshots = _snapshot_diagnostics(repository, dates)
    strict = _load_object(strict_validation_path, "strict pipeline validation")
    labels = _load_label_rows(label_store_path)
    strict_counts = _strict_counts(strict, labels)
    nav_summary = OfficialNavStore(nav_store_path).summary()
    evidence = {
        "model_snapshot_dates": [item.isoformat() for item in dates],
        "snapshot_diagnostics": snapshots,
        "constituent_nav_store": {
            "acquired_isin_count": nav_summary.acquired_isin_count,
            "observation_count": nav_summary.observation_count,
            "provider_observation_counts": dict(nav_summary.provider_observation_counts),
        },
        "strict_pipeline": {
            "official_eligible_windows": strict_counts["official_eligible_windows"],
            "rejected_windows": strict_counts["rejected_windows"],
            "candidate_windows": strict_counts["candidate_windows"],
        },
    }
    blockers = [
        "SNAPSHOT_WEIGHT_SEMANTICS_UNRESOLVED",
        "REBALANCE_EFFECTIVE_TIMESTAMP_UNRESOLVED",
        "PORTFOLIO_REPORTING_CURRENCY_UNRESOLVED",
        "FX_METHODOLOGY_REQUIRED",
        "DISTRIBUTION_AND_CORPORATE_ACTION_TOTAL_RETURN_SEMANTICS_UNRESOLVED",
        "PORTFOLIO_CASHFLOW_AND_FEE_TREATMENT_UNRESOLVED",
        "STRICT_PORTFOLIO_NAV_SOURCE_NOT_MATERIALIZED",
    ]
    if snapshots["duplicate_constituent_rows"] != 0:
        blockers.append("DUPLICATE_CONSTITUENT_ROWS_REQUIRE_RESOLUTION")
    payload: dict[str, object] = {
        "schema_version": 1,
        "methodology_version": "0.1.0",
        "validation_status": METHODOLOGY_STATUS,
        "activation_state": ACTIVATION_STATE,
        "evidence": evidence,
        "evaluated_alternatives": _alternatives(),
        "selected_candidate": None,
        "proven_semantics": {
            "snapshot_rows_are_dated": True,
            "allocation_field_is_present": True,
            "allocation_totals_are_audited_not_interpreted": True,
            "constituent_nav_provenance_is_retained": True,
            "strict_rejected_windows_remain_inadmissible": True,
        },
        "unresolved_semantics": blockers,
        "date_rules": {
            "calendar_boundary": "No alternative NAV date is selected when a required boundary is absent.",
            "valid_nav_observation": "Only exact retained source observations are admissible.",
            "rebalance_rule": "UNKNOWN",
            "rebalance_effective_date": "UNRESOLVED; a snapshot date is not assumed to be effective at open, close, or next NAV date.",
            "rebalance_effective_time": "UNKNOWN",
            "non_trading_day_rule": "UNKNOWN",
            "prohibited": ["nearest_date", "forward_fill", "backward_fill", "interpolation", "weekend_synthesis"],
        },
        "currency_rules": {
            "constituent_snapshot_currencies": snapshots["currency_summary"],
            "portfolio_reporting_currency": "UNRESOLVED",
            "fx_conversion": "FX_METHODOLOGY_REQUIRED",
            "admission": "Mixed nominal currencies cannot be aggregated without approved FX evidence and methodology.",
        },
        "missing_data_rules": {
            "rule": "ANY_REQUIRED_CONSTITUENT_HISTORY_MISSING => PORTFOLIO_NAV_UNAVAILABLE",
            "prohibited": ["drop_constituent", "renormalize", "zero_return", "cash_return", "proxy", "source_stitching"],
        },
        "corporate_action_rules": {
            "status": "UNRESOLVED_FOR_PORTFOLIO_TOTAL_RETURN",
            "prohibited": ["synthetic_coupon", "synthetic_redemption", "synthetic_distribution_cashflow"],
        },
        "numerical_formulas": {
            "status": "NOT_APPROVED_FOR_ACTIVATION",
            "buy_and_hold_example": "units_i = initial_portfolio_value * weight_i / nav_i(t0); portfolio_value(t) = sum(units_i * nav_i(t))",
            "precision": "Decimal precision and deterministic rounding are not specified because the required economic semantics are unresolved.",
        },
        "lookahead_safeguards": {
            "result": "NO_POINT_IN_TIME_LEAKAGE_REQUIRED",
            "requirements": [
                "A future snapshot cannot affect the portfolio before its documented effective timestamp.",
                "A future constituent cannot affect prior portfolio values.",
                "Later NAV observations cannot alter prior portfolio values.",
                "Horizon-specific future information cannot cross-contaminate other horizons.",
            ],
        },
        "metric_suitability": {
            "status": "NOT_ADMITTED",
            "reason": "No economically identified, total-return-compatible portfolio NAV path exists under retained evidence.",
            "metrics": ["return", "annualized_return", "volatility", "sharpe", "mdd", "var", "cvar"],
        },
        "historical_feasibility": {
            "THEORETICALLY_CONSTRUCTIBLE": 0,
            "BLOCKED_MISSING_CONSTITUENT_HISTORY": 0,
            "BLOCKED_STRICT_ELIGIBILITY": strict_counts["rejected_windows"],
            "BLOCKED_FX": 0,
            "BLOCKED_SEMANTICS": strict_counts["official_eligible_windows"],
            "accounting_reconciles": sum(
                [
                    strict_counts["rejected_windows"],
                    strict_counts["official_eligible_windows"],
                ]
            )
            == strict_counts["candidate_windows"],
            "classification_note": "Strict-eligible windows are classified as semantics-blocked before constituent availability or FX can safely be evaluated.",
        },
        "approval_blockers": blockers,
        "source_artifact_references": {
            "point_in_time_holdings": "database/model_portfolio.sqlite:model_portfolios",
            "constituent_nav_store": "database/official_historical_nav.sqlite:asset_nav_observations",
            "strict_pipeline": "data/audit/strict_backtest_pipeline_validation.json",
            "label_store": "data/features/official_forward_labels.csv",
        },
        "safety": {
            "portfolio_nav_constructed": False,
            "official_labels_created": False,
            "network_access": False,
            "graphify_used_as_portfolio_evidence": False,
        },
    }
    payload["evidence_fingerprint"] = _fingerprint(payload)
    return payload


def write_portfolio_nav_methodology_audit(path: Path, payload: dict[str, object]) -> None:
    """Atomically write a canonical, deterministic methodology audit."""
    _write_json_atomic(path, payload)


def _snapshot_diagnostics(
    repository: ModelPortfolioRepository, dates: tuple[date, ...]
) -> dict[str, object]:
    per_date: dict[str, dict[str, object]] = {}
    portfolio_snapshots: dict[str, list[tuple[date, tuple[tuple[str, float | None], ...]]]] = {}
    malformed_weights: list[str] = []
    currency_counter: Counter[str] = Counter()
    duplicate_constituents = 0
    for observation_date in dates:
        by_portfolio: dict[str, list[HoldingObservation]] = {}
        for holding in repository.load_holdings(observation_date):
            by_portfolio.setdefault(holding.portfolio_name, []).append(holding)
            if holding.currency:
                currency_counter[holding.currency] += 1
        per_date[observation_date.isoformat()] = {}
        for portfolio, holdings in sorted(by_portfolio.items()):
            isins = [item.isin for item in holdings]
            duplicate_constituents += len(isins) - len(set(isins))
            weights = [item.allocation for item in holdings]
            if any(item is None or item < 0 for item in weights):
                malformed_weights.append(f"{observation_date.isoformat()}:{portfolio}")
            total = sum(item for item in weights if item is not None)
            currencies = sorted({item.currency for item in holdings if item.currency})
            per_date[observation_date.isoformat()][portfolio] = {
                "constituent_count": len(holdings),
                "weight_total": total,
                "weight_total_is_approximately_100": abs(total - 100.0) <= 1e-6,
                "currencies": currencies,
            }
            portfolio_snapshots.setdefault(portfolio, []).append(
                (observation_date, tuple(sorted((str(item.isin), item.allocation) for item in holdings)))
            )
    transitions: list[dict[str, object]] = []
    composition_changed_count = 0
    weight_changed_transition_count = 0
    for portfolio, snapshots in sorted(portfolio_snapshots.items()):
        for (prior_date, prior), (current_date, current) in pairwise(snapshots):
            prior_map, current_map = dict(prior), dict(current)
            added = sorted(set(current_map) - set(prior_map))
            removed = sorted(set(prior_map) - set(current_map))
            changed_count = sum(
                prior_map.get(isin) != current_map.get(isin)
                for isin in set(prior_map) | set(current_map)
            )
            composition_changed_count += bool(added or removed)
            weight_changed_transition_count += changed_count > 0
            transitions.append(
                {
                    "portfolio": portfolio,
                    "from": prior_date.isoformat(),
                    "to": current_date.isoformat(),
                    "added_constituents": added,
                    "removed_constituents": removed,
                    "weight_changed_constituent_count": changed_count,
                }
            )
    gaps = [(later - earlier).days for earlier, later in pairwise(dates)]
    return {
        "decision_date_count": len(dates),
        "portfolio_count": len(portfolio_snapshots),
        "snapshot_frequency_days": {
            "minimum": min(gaps) if gaps else 0,
            "maximum": max(gaps) if gaps else 0,
            "distinct": sorted(set(gaps)),
        },
        "duplicate_constituent_rows": duplicate_constituents,
        "malformed_weight_snapshots": malformed_weights,
        "currency_summary": dict(sorted(currency_counter.items())),
        "per_date": per_date,
        "transition_summary": {
            "transition_count": len(transitions),
            "composition_changed_count": composition_changed_count,
            "weight_changed_transition_count": weight_changed_transition_count,
            "transitions": transitions,
        },
    }


def _alternatives() -> list[dict[str, object]]:
    return [
        {
            "id": "BUY_AND_HOLD_FROM_DECISION_DATE",
            "economic_interpretation": "Snapshot allocations would define units at the decision date and weights would drift with NAV.",
            "required_assumptions": ["weight_semantics", "initial_nav_boundary", "currency_and_fx", "total_return_semantics"],
            "assumption_statuses": {
                "weight_semantics": "UNKNOWN",
                "initial_nav_boundary": "UNKNOWN",
                "currency_and_fx": "UNKNOWN",
                "total_return_semantics": "UNKNOWN",
                "rebalancing": "NOT_REQUIRED",
            },
            "approval": "BLOCKED",
            "reason": "Snapshot allocations are not evidenced as executable holdings or effective at a documented timestamp.",
        },
        {
            "id": "REBALANCE_ON_EVERY_RETAINED_SNAPSHOT",
            "economic_interpretation": "Each later snapshot would reset target units at an effective rebalance time.",
            "required_assumptions": ["target_weight_semantics", "rebalance_effective_timestamp", "currency_and_fx", "transaction_cost_and_cashflow_treatment"],
            "assumption_statuses": {
                "target_weight_semantics": "UNKNOWN",
                "rebalance_effective_timestamp": "UNKNOWN",
                "currency_and_fx": "UNKNOWN",
                "transaction_cost_and_cashflow_treatment": "UNKNOWN",
                "corporate_action_and_total_return_semantics": "UNKNOWN",
            },
            "approval": "BLOCKED",
            "reason": "No retained evidence establishes that every snapshot is an investable rebalance instruction or when it takes effect.",
        },
        {
            "id": "FIXED_WEIGHT_PERIODIC_AGGREGATION",
            "economic_interpretation": "Snapshot weights would be mathematically fixed over the window.",
            "required_assumptions": ["fixed_weight_economic_identity", "currency_and_fx", "total_return_semantics"],
            "assumption_statuses": {
                "fixed_weight_economic_identity": "UNKNOWN",
                "currency_and_fx": "UNKNOWN",
                "total_return_semantics": "UNKNOWN",
                "rebalancing": "NOT_REQUIRED",
            },
            "approval": "BLOCKED",
            "reason": "This is a mathematical index unless a portfolio-specific fixed-weight mandate is evidenced.",
        },
    ]


def _strict_counts(strict: dict[str, object], labels: list[dict[str, str]]) -> dict[str, int]:
    dataset = strict.get("dataset")
    if not isinstance(dataset, dict):
        raise PortfolioNavMethodologyError("strict validation dataset is malformed")
    eligible = _integer(dataset.get("official_eligible_windows"), "official_eligible_windows")
    rejected = _integer(dataset.get("rejected_windows"), "rejected_windows")
    candidate = len(labels)
    if eligible + rejected != candidate:
        raise PortfolioNavMethodologyError("strict window accounting does not reconcile to label candidates")
    return {
        "official_eligible_windows": eligible,
        "rejected_windows": rejected,
        "candidate_windows": candidate,
    }


def _load_label_rows(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except OSError as exc:
        raise PortfolioNavMethodologyError(f"cannot read label store: {path}") from exc
    if not rows:
        raise PortfolioNavMethodologyError("label store is empty")
    return [row for row in rows if row is not None]


def _load_object(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PortfolioNavMethodologyError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise PortfolioNavMethodologyError(f"{label} must be an object")
    return value


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PortfolioNavMethodologyError(f"{label} must be a non-negative integer")
    return value


def _fingerprint(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
