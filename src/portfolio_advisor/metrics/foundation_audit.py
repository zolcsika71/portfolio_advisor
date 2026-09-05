"""Deterministic audit representation for the Phase F2 metric foundation."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from portfolio_advisor.canonical import canonical_fingerprint, canonical_json
from portfolio_advisor.metrics.governed import (
    PHASE_F2_ACTIVATION_STATE,
    PHASE_F2_IMPLEMENTATION_ID,
    PHASE_F2_IMPLEMENTATION_VERSION,
    GovernedMetricSeries,
    GovernedObservation,
    MetricSuitabilityState,
    ObservationFingerprintScheme,
    ObservationSemantics,
    PhaseF2ExecutionMode,
    SourceApprovalState,
    bind_series_provenance,
    compute_governed_metrics,
    phase_f2_contract_inventory,
)
from portfolio_advisor.metrics.policy_contract import (
    PHASE_F1_DECISION_TOKENS,
    PHASE_F1_POLICY_FINGERPRINT,
    PHASE_F1_POLICY_ID,
    PHASE_F1_POLICY_VERSION,
    PhaseF1PortfolioMetricsPolicy,
)


def build_phase_f2_foundation_audit(
    *,
    policy: PhaseF1PortfolioMetricsPolicy,
    phase_e_validation: Mapping[str, object],
) -> dict[str, object]:
    """Build a timestamp-free audit tied to F1 and the read-only Phase E validation."""
    if (
        policy.policy_id != PHASE_F1_POLICY_ID
        or policy.version != PHASE_F1_POLICY_VERSION
        or policy.fingerprint != PHASE_F1_POLICY_FINGERPRINT
        or dict(policy.decision_tokens) != PHASE_F1_DECISION_TOKENS
    ):
        raise ValueError("Phase F1 policy differs from the exact approved contract")
    phase_e_references = _validated_phase_e_references(phase_e_validation)
    regular = _fixture_series(
        "D11_REGULAR_REFERENCE",
        (("2021-01-01", "100"), ("2022-01-01", "110"), ("2023-01-01", "99")),
    )
    irregular = _fixture_series(
        "D11_IRREGULAR_REFERENCE",
        (("2026-01-01", "100"), ("2026-01-02", "101"), ("2026-01-05", "103")),
    )
    flat = _fixture_series(
        "D11_ZERO_REFERENCE",
        (("2026-01-01", "100"), ("2026-01-02", "100"), ("2026-01-05", "100")),
    )
    requested = (
        "TOTAL_RETURN",
        "ANNUALIZED_RETURN",
        "ANNUALIZED_VOLATILITY",
        "MAXIMUM_DRAWDOWN",
        "SHARPE_RATIO",
        "SORTINO_RATIO",
        "HISTORICAL_VAR",
        "HISTORICAL_CVAR",
    )
    runs = {
        "regular": compute_governed_metrics(
            series=regular,
            requested_metrics=requested,
            policy=policy,
        ),
        "irregular": compute_governed_metrics(
            series=irregular,
            requested_metrics=requested,
            policy=policy,
        ),
        "flat": compute_governed_metrics(
            series=flat,
            requested_metrics=("ANNUALIZED_VOLATILITY", "MAXIMUM_DRAWDOWN"),
            policy=policy,
        ),
    }
    payload: dict[str, object] = {
        "activation_state": PHASE_F2_ACTIVATION_STATE,
        "contract_inventory": dict(phase_f2_contract_inventory()),
        "evidence_database_references": phase_e_references,
        "failure_state_semantics": [
            "AVAILABLE",
            "INSUFFICIENT_DATA",
            "INPUT_REJECTED",
            "SEMANTICS_NOT_APPROVED",
            "POLICY_BLOCKED",
            "UNSUPPORTED_METRIC",
        ],
        "implementation_id": PHASE_F2_IMPLEMENTATION_ID,
        "implementation_modules": [
            "portfolio_advisor.metrics.governed",
            "portfolio_advisor.metrics.phase_e_adapter",
        ],
        "implementation_version": PHASE_F2_IMPLEMENTATION_VERSION,
        "minimum_sample_rules": {
            "annualized_volatility_formula_intervals": 2,
            "governed_admitted_calendar_days": 365,
            "governed_admitted_intervals": 252,
            "maximum_drawdown_intervals": 1,
            "return_intervals": 1,
        },
        "numerical_conventions": {
            "binary_float": "PROHIBITED",
            "canonical_output_scale": 18,
            "context_precision_significant_digits": 50,
            "intermediate_quantization": "PROHIBITED",
            "rounding": "ROUND_HALF_EVEN",
        },
        "phase_f1_policy": {
            "decision_tokens": dict(policy.decision_tokens),
            "fingerprint": policy.fingerprint,
            "policy_id": policy.policy_id,
            "version": policy.version,
        },
        "reference_cases": {name: run.to_dict() for name, run in sorted(runs.items())},
        "regression_gate_results": {
            "constructed_portfolio_rows": phase_e_references[
                "constructed_portfolio_row_counts"
            ],
            "foreign_key_violations": 0,
            "integrity_check": "ok",
            "phase_e_manifest_count": 16,
            "phase_e_observation_count": 3984,
            "phase_e_read_only_validator": "PASS",
            "strict_phase_f1_policy_loader": "PASS",
        },
        "regression_boundaries": {
            "database_mutation": "NOT_PERFORMED",
            "new_source_admission": "NOT_PERFORMED",
            "portfolio_reconstruction": "NOT_PERFORMED",
            "portfolio_selection_activation": "NOT_ACTIVATED",
            "production_cutover": "NOT_AUTHORIZED",
            "ranking_activation": "NOT_ACTIVATED",
            "scoring_activation": "NOT_ACTIVATED",
            "supplementary_nav_admission": "NOT_PERFORMED",
        },
        "regular_irregular_interval_rules": {
            "d11_model": "CONSTANT_DRIFT_CONSTANT_DIFFUSION",
            "elapsed_time": "ACTUAL_CALENDAR_DAYS_DIVIDED_BY_365",
            "equal_gap_equivalence": "SAMPLE_STDEV_LOG_RETURNS_DIVIDED_BY_SQRT_GAP_YEARS",
            "interpolation_or_resampling": "PROHIBITED",
            "observed_endpoints_only": True,
        },
        "schema_version": 1,
    }
    payload["audit_fingerprint"] = canonical_fingerprint(payload)
    return payload


def render_phase_f2_foundation_audit(payload: Mapping[str, object]) -> str:
    """Return byte-stable canonical audit JSON."""
    return canonical_json(dict(payload)) + "\n"


def _validated_phase_e_references(validation: Mapping[str, object]) -> dict[str, object]:
    constructed = validation.get("constructed_portfolio_row_counts")
    legacy = validation.get("legacy_nav")
    currency_ranges = validation.get("currency_ranges")
    expected_constructed = {
        "constructed_portfolio_holding_lineage": 0,
        "constructed_portfolio_metadata": 0,
    }
    if constructed != expected_constructed:
        raise ValueError("Phase E validation has nonzero or malformed constructed rows")
    if validation.get("integrity_check") != "ok" or validation.get("foreign_key_violations") != 0:
        raise ValueError("Phase E validation did not pass database integrity gates")
    if validation.get("manifest_count") != 16 or validation.get("observation_count") != 3984:
        raise ValueError("Phase E validation counts differ from the installed evidence contract")
    if not isinstance(legacy, dict) or (
        legacy.get("dataset_fingerprint")
        != "b2e6e4b8c2066c932d6933dbb07d8f22ab1fa9e2cd04c88eae7283334829f99a"
        or legacy.get("isin_count") != 19
        or legacy.get("observation_count") != 8770
    ):
        raise ValueError("legacy NAV validation reference differs from the preserved contract")
    if not isinstance(currency_ranges, dict) or {
        currency: (
            value.get("observation_count") if isinstance(value, dict) else None,
            value.get("isin_count") if isinstance(value, dict) else None,
        )
        for currency, value in currency_ranges.items()
    } != {"EUR": (1959, 8), "HUF": (2025, 8)}:
        raise ValueError("Phase E currency counts differ from the installed evidence contract")
    database_sha256 = validation.get("database_sha256")
    phase_e_fingerprint = validation.get("phase_e_dataset_fingerprint")
    if not _is_sha256(database_sha256) or not _is_sha256(phase_e_fingerprint):
        raise ValueError("Phase E validation fingerprints are malformed")
    return {
        "constructed_portfolio_row_counts": expected_constructed,
        "currency_ranges": currency_ranges,
        "database_sha256": database_sha256,
        "legacy_nav": legacy,
        "manifest_count": 16,
        "observation_count": 3984,
        "phase_e_dataset_fingerprint": phase_e_fingerprint,
    }


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _fixture_series(
    identity: str,
    values: tuple[tuple[str, str], ...],
) -> GovernedMetricSeries:
    observations = tuple(
        GovernedObservation.create(
            observation_date=observation_date,
            value=Decimal(value),
            evidence_reference=f"SYNTHETIC_FIXTURE:{identity}:{index}",
        )
        for index, (observation_date, value) in enumerate(values)
    )
    return bind_series_provenance(
        GovernedMetricSeries(
            series_identity=identity,
            subject_identity=identity,
            source_identity="PHASE_F2_SYNTHETIC_REFERENCE_FIXTURE",
            source_governance="SYNTHETIC_FIXTURE_ONLY",
            currency_code="EUR",
            observation_semantics=ObservationSemantics.PORTFOLIO_WEALTH_TOTAL_RETURN,
            source_approval_state=SourceApprovalState.SYNTHETIC_FIXTURE_APPROVED,
            metric_suitability_state=MetricSuitabilityState.APPROVED_TOTAL_RETURN_SERIES,
            observation_fingerprint_scheme=ObservationFingerprintScheme.PHASE_F2_SYNTHETIC_V1,
            execution_mode=PhaseF2ExecutionMode.SYNTHETIC_FIXTURE,
            observations=observations,
            evidence_references=(
                f"SYNTHETIC_FIXTURE:{identity}",
                *(item.evidence_reference for item in observations),
            ),
            decision_as_of_utc="2026-09-04T12:24:23.000000Z",
            evidence_available_at_utc="2026-09-04T12:24:23.000000Z",
            nav_evidence_cutoff="2026-08-31",
            alignment_method="STRICT_EIGHT_WAY_INTERSECTION",
            window_selection_method="LATEST_MINIMAL_COMMON_365D_252_WINDOW",
            endpoint_method="OBSERVED_ENDPOINTS_EXACT_ELAPSED_DAYS",
            portfolio_dynamics="BUY_AND_HOLD_WEIGHT_DRIFT",
            cash_return_treatment="UNREMUNERATED_NOMINAL_CASH",
        )
    )
