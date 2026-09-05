"""Deterministic, synthetic-only Phase F3A wealth-foundation audit."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from decimal import Decimal

from portfolio_advisor.canonical import canonical_fingerprint, canonical_json
from portfolio_advisor.metrics.governed import PHASE_F2_IMPLEMENTATION_ID
from portfolio_advisor.metrics.policy_contract import PhaseF1PortfolioMetricsPolicy
from portfolio_advisor.metrics.portfolio_wealth import (
    PHASE_F3A_ACTIVATION_STATE,
    PHASE_F3A_IMPLEMENTATION_ID,
    PHASE_F3A_IMPLEMENTATION_VERSION,
    SyntheticPortfolioWealthRequest,
    build_synthetic_eur_portfolio_wealth,
    compute_phase_f3a_synthetic_metrics,
    create_synthetic_constituent_series,
)
from portfolio_advisor.objectives.construction_policy import (
    CapitalDefensiveConstructionPolicy,
)


def build_phase_f3a_wealth_foundation_audit(
    *,
    metrics_policy: PhaseF1PortfolioMetricsPolicy,
    construction_policy: CapitalDefensiveConstructionPolicy,
) -> dict[str, object]:
    """Build a timestamp-free audit from a deterministic 365-day fixture."""
    request = _reference_request()
    lineage = build_synthetic_eur_portfolio_wealth(
        request=request,
        metrics_policy=metrics_policy,
        construction_policy=construction_policy,
    )
    metric_run = compute_phase_f3a_synthetic_metrics(
        lineage=lineage,
        request=request,
        requested_metrics=(
            "TOTAL_RETURN",
            "ANNUALIZED_RETURN",
            "ANNUALIZED_VOLATILITY",
            "MAXIMUM_DRAWDOWN",
            "SHARPE_RATIO",
            "SORTINO_RATIO",
        ),
        metrics_policy=metrics_policy,
        construction_policy=construction_policy,
    )
    payload: dict[str, object] = {
        "activation_state": PHASE_F3A_ACTIVATION_STATE,
        "database_mutation": "NOT_PERFORMED",
        "f2_interface": {
            "execution_mode": "SYNTHETIC_FIXTURE",
            "implementation_id": PHASE_F2_IMPLEMENTATION_ID,
            "metric_run": metric_run.to_dict(),
        },
        "implementation_id": PHASE_F3A_IMPLEMENTATION_ID,
        "implementation_modules": [
            "portfolio_advisor.metrics.portfolio_wealth",
            "portfolio_advisor.metrics.wealth_foundation_audit",
        ],
        "implementation_version": PHASE_F3A_IMPLEMENTATION_VERSION,
        "numerical_conventions": {
            "canonical_output_quantum": "0.000000000000000001",
            "canonical_scale_is_economic_accuracy_claim": False,
            "decimal_context_precision": 50,
            "independently_serialized_nine_weight_tolerance": (
                "0.0000000000000000045"
            ),
            "internal_derived_weight_sum_tolerance": (
                "0.0000000000000000000000000000000000000001"
            ),
            "persisted_numeric_half_quantum_tolerance": "0.0000000000000000005",
            "rounding": "ROUND_HALF_EVEN",
            "source_decimal_resolution": "PRESERVED",
        },
        "lineage_reference_case": {
            "constituents": [item.to_dict() for item in lineage.constituents],
            "first_wealth_point": lineage.wealth_points[0].to_dict(),
            "last_wealth_point": lineage.wealth_points[-1].to_dict(),
            "lineage_fingerprint": lineage.lineage_fingerprint,
            "metrics_policy_fingerprint": lineage.metrics_policy_fingerprint,
            "construction_policy_fingerprint": lineage.construction_policy_fingerprint,
            "window_proof": lineage.window_proof.to_dict(),
        },
        "regression_boundaries": {
            "admitted_evidence_execution": "BLOCKED_NOT_AUTHORIZED",
            "database_persistence": "NOT_PERFORMED",
            "distribution_reinvestment": "NOT_IMPLEMENTED",
            "portfolio_nav_reconstruction_freeze_change": "NOT_PERFORMED",
            "portfolio_selection_activation": "NOT_ACTIVATED",
            "production_cutover": "NOT_AUTHORIZED",
            "provider_acquisition": "NOT_PERFORMED",
            "ranking_activation": "NOT_ACTIVATED",
            "real_candidate_construction": "NOT_PERFORMED",
            "rebalancing": "NOT_IMPLEMENTED",
            "supplementary_nav_admission": "NOT_PERFORMED",
            "trading": "NOT_AUTHORIZED",
        },
        "schema_version": 1,
        "supported_scope": {
            "currency": "EUR",
            "distribution_semantics": "SIMULATED_ACCUMULATING_SHARE_CLASS",
            "initial_cash_weight": "0.20",
            "initial_security_count": 8,
            "initial_security_weight": "0.10",
            "portfolio_dynamics": "BUY_AND_HOLD_WEIGHT_DRIFT",
            "valuation_dates": "STRICT_EIGHT_WAY_OBSERVED_INTERSECTION",
            "window": "LATEST_MINIMAL_COMMON_365D_252_WINDOW",
        },
    }
    payload["audit_fingerprint"] = canonical_fingerprint(payload)
    return payload


def render_phase_f3a_wealth_foundation_audit(payload: Mapping[str, object]) -> str:
    """Return byte-identical canonical audit JSON."""
    return canonical_json(dict(payload)) + "\n"


def _reference_request() -> SyntheticPortfolioWealthRequest:
    start = date(2025, 8, 28)
    dates = tuple(
        (start + timedelta(days=(index * 365) // 252)).isoformat() for index in range(253)
    )
    constituents = tuple(
        create_synthetic_constituent_series(
            constituent_identity=f"SYNTHETIC_AUDIT_FUND_{fund_index}",
            values=tuple(
                (
                    observation_date,
                    Decimal(fund_index + 2)
                    + Decimal(point_index) / Decimal(1000),
                )
                for point_index, observation_date in enumerate(dates)
            ),
            evidence_available_at_utc="2026-09-04T12:24:23.000000Z",
        )
        for fund_index in range(8)
    )
    return SyntheticPortfolioWealthRequest(
        portfolio_identity="SYNTHETIC_PHASE_F3A_AUDIT_PORTFOLIO",
        initial_capital=Decimal(100),
        decision_as_of_utc="2026-09-04T12:24:23.000000Z",
        nav_evidence_cutoff="2026-08-31",
        constituents=constituents,
    )
