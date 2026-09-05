"""Focused tests for the synthetic-only Phase F3A wealth foundation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal, localcontext
from pathlib import Path
from typing import cast

import pytest

from portfolio_advisor.metrics.governed import (
    MetricSuitabilityState,
    ObservationFingerprintScheme,
    ObservationSemantics,
    PhaseF2ComputationStatus,
    PhaseF2ExecutionMode,
    SourceApprovalState,
    bind_series_provenance,
    compute_governed_metrics,
)
from portfolio_advisor.metrics.policy_contract import (
    PHASE_F1_POLICY_ARTIFACT,
    PhaseF1PortfolioMetricsPolicy,
    load_phase_f1_portfolio_metrics_policy,
)
from portfolio_advisor.metrics.portfolio_wealth import (
    PHASE_F3A_ACTIVATION_STATE,
    PhaseF3AValidationError,
    SyntheticConstituentSeries,
    SyntheticDistributionState,
    SyntheticPortfolioWealthLineage,
    SyntheticPortfolioWealthRequest,
    adapt_validated_synthetic_wealth_to_f2,
    bind_synthetic_constituent_provenance,
    build_synthetic_eur_portfolio_wealth,
    compute_phase_f3a_synthetic_metrics,
    create_synthetic_constituent_series,
    validate_synthetic_eur_portfolio_wealth,
)
from portfolio_advisor.metrics.wealth_foundation_audit import (
    build_phase_f3a_wealth_foundation_audit,
    render_phase_f3a_wealth_foundation_audit,
)
from portfolio_advisor.objectives.construction_policy import (
    CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT,
    CapitalDefensiveConstructionPolicy,
    load_capital_defensive_construction_policy,
)

ROOT = Path(__file__).resolve().parents[1]
DECISION_AS_OF = "2026-09-04T12:24:23.000000Z"
CUTOFF = "2026-08-31"


@pytest.fixture(scope="module")
def metrics_policy() -> PhaseF1PortfolioMetricsPolicy:
    return load_phase_f1_portfolio_metrics_policy(ROOT / PHASE_F1_POLICY_ARTIFACT)


@pytest.fixture(scope="module")
def construction_policy() -> CapitalDefensiveConstructionPolicy:
    return load_capital_defensive_construction_policy(
        ROOT / CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT
    )


def _spaced_dates(*, start: date, span_days: int, count: int) -> tuple[str, ...]:
    assert count >= 2
    return tuple(
        (start + timedelta(days=(index * span_days) // (count - 1))).isoformat()
        for index in range(count)
    )


def _request(
    dates: tuple[str, ...],
    *,
    first_final_nav: Decimal = Decimal(6),
    identities: tuple[str, ...] | None = None,
    distribution_state: SyntheticDistributionState = (
        SyntheticDistributionState.SIMULATED_ACCUMULATING_SHARE_CLASS
    ),
) -> SyntheticPortfolioWealthRequest:
    names = identities or tuple(f"SYNTHETIC_FUND_{index}" for index in range(8))
    constituents = []
    for index, identity in enumerate(names):
        initial_nav = Decimal(3) if index == 0 else Decimal(10)
        values = tuple(
            (
                observation_date,
                first_final_nav
                if index == 0 and point_index == len(dates) - 1
                else initial_nav,
            )
            for point_index, observation_date in enumerate(dates)
        )
        constituents.append(
            create_synthetic_constituent_series(
                constituent_identity=identity,
                values=values,
                evidence_available_at_utc=DECISION_AS_OF,
                distribution_state=distribution_state,
            )
        )
    return SyntheticPortfolioWealthRequest(
        portfolio_identity="SYNTHETIC_PHASE_F3A_PORTFOLIO",
        initial_capital=Decimal(100),
        decision_as_of_utc=DECISION_AS_OF,
        nav_evidence_cutoff=CUTOFF,
        constituents=tuple(constituents),
    )


def _qualifying_dates() -> tuple[str, ...]:
    return _spaced_dates(start=date(2025, 8, 28), span_days=365, count=253)


def _build(
    request: SyntheticPortfolioWealthRequest,
    metrics_policy: PhaseF1PortfolioMetricsPolicy,
    construction_policy: CapitalDefensiveConstructionPolicy,
) -> SyntheticPortfolioWealthLineage:
    return build_synthetic_eur_portfolio_wealth(
        request=request,
        metrics_policy=metrics_policy,
        construction_policy=construction_policy,
    )


def _assert_close(actual: Decimal, expected: Decimal, tolerance: Decimal = Decimal("1E-40")) -> None:
    assert abs(actual - expected) / abs(expected) <= tolerance


def test_hand_calculated_units_cash_wealth_and_weight_drift(
    metrics_policy: PhaseF1PortfolioMetricsPolicy,
    construction_policy: CapitalDefensiveConstructionPolicy,
) -> None:
    lineage = _build(_request(_qualifying_dates()), metrics_policy, construction_policy)

    assert lineage.activation_state == PHASE_F3A_ACTIVATION_STATE
    assert lineage.initial_capital == Decimal(100)
    assert lineage.initial_security_allocation == Decimal("10.00")
    assert lineage.nominal_cash == Decimal("20.00")
    assert lineage.constituents[0].constituent_identity == "SYNTHETIC_FUND_0"
    with localcontext() as context:
        context.prec = 50
        expected_units = Decimal(10) / Decimal(3)
    _assert_close(lineage.constituents[0].fixed_mathematical_units, expected_units)
    assert lineage.constituents[0].initial_reconciliation_relative_error <= Decimal("1E-40")

    first = lineage.wealth_points[0]
    final = lineage.wealth_points[-1]
    _assert_close(first.total_wealth, Decimal(100))
    _assert_close(final.total_wealth, Decimal(110))
    _assert_close(final.components[0].component_value, Decimal(20))
    with localcontext() as context:
        context.prec = 50
        expected_doubled_weight = Decimal(2) / Decimal(11)
        expected_unchanged_weight = Decimal(1) / Decimal(11)
    _assert_close(final.components[0].derived_weight, expected_doubled_weight)
    _assert_close(final.cash_weight, expected_doubled_weight)
    for component in final.components[1:]:
        _assert_close(component.derived_weight, expected_unchanged_weight)
    assert all(point.nominal_cash == Decimal("20.00") for point in lineage.wealth_points)
    assert final.components[0].derived_weight != first.components[0].derived_weight


def test_nonterminating_units_use_f1_reconciliation_tolerance(
    metrics_policy: PhaseF1PortfolioMetricsPolicy,
    construction_policy: CapitalDefensiveConstructionPolicy,
) -> None:
    lineage = _build(_request(_qualifying_dates()), metrics_policy, construction_policy)
    component = lineage.constituents[0]

    with localcontext() as context:
        context.prec = 50
        reconstructed = component.fixed_mathematical_units * component.initial_nav
        error = abs(reconstructed - component.initial_allocation) / component.initial_allocation
    assert error == component.initial_reconciliation_relative_error
    assert error <= Decimal("1E-40")

    serialized_constituent = component.to_dict()
    units_text = cast(str, serialized_constituent["fixed_mathematical_units"])
    assert units_text == "3.333333333333333333"
    assert len(units_text.partition(".")[2]) == 18
    assert abs(component.fixed_mathematical_units - Decimal(units_text)) <= Decimal("5E-19")

    final = lineage.wealth_points[-1]
    serialized_point = final.to_dict()
    serialized_components = cast(
        list[dict[str, object]], serialized_point["components"]
    )
    serialized_weights = [
        Decimal(cast(str, item["derived_weight"])) for item in serialized_components
    ]
    serialized_weights.append(Decimal(cast(str, serialized_point["cash_weight"])))
    assert all(
        len(cast(str, item["derived_weight"]).partition(".")[2]) == 18
        for item in serialized_components
    )
    assert len(cast(str, serialized_point["cash_weight"]).partition(".")[2]) == 18
    serialized_weight_error = abs(sum(serialized_weights, Decimal(0)) - Decimal(1))
    assert serialized_weight_error == final.serialized_weight_sum_absolute_error
    assert serialized_weight_error <= Decimal("4.5E-18")
    assert all(
        abs(component_value.derived_weight - serialized_weight) <= Decimal("5E-19")
        for component_value, serialized_weight in zip(
            final.components, serialized_weights[:8], strict=True
        )
    )
    assert abs(final.cash_weight - serialized_weights[-1]) <= Decimal("5E-19")


def test_strict_intersection_and_latest_minimal_window_are_proven(
    metrics_policy: PhaseF1PortfolioMetricsPolicy,
    construction_policy: CapitalDefensiveConstructionPolicy,
) -> None:
    qualifying = _qualifying_dates()
    common_leading = "2025-08-01"
    request = _request((common_leading, *qualifying))
    first = request.constituents[0]
    unique_extra = create_synthetic_constituent_series(
        constituent_identity=first.constituent_identity,
        values=(
            ("2025-07-31", Decimal(3)),
            *((item.observation_date, item.nav) for item in first.observations),
        ),
        evidence_available_at_utc=DECISION_AS_OF,
    )
    request = replace(request, constituents=(unique_extra, *request.constituents[1:]))

    lineage = _build(request, metrics_policy, construction_policy)
    proof = lineage.window_proof
    assert proof.complete_common_date_count == 254
    assert proof.selected_start_date == qualifying[0]
    assert proof.selected_end_date == qualifying[-1]
    assert proof.selected_observation_count == 253
    assert proof.selected_return_interval_count == 252
    assert proof.selected_calendar_span_days == 365
    assert proof.excluded_leading_common_dates == 1
    assert proof.next_later_start_date == qualifying[1]
    assert "MINIMUM_252_RETURN_INTERVALS" in proof.next_later_start_failed_minima
    assert all(point.valuation_date != "2025-07-31" for point in lineage.wealth_points)
    assert all(point.valuation_date != common_leading for point in lineage.wealth_points)


def test_252_interval_minimum_fails_independently(
    metrics_policy: PhaseF1PortfolioMetricsPolicy,
    construction_policy: CapitalDefensiveConstructionPolicy,
) -> None:
    dates = _spaced_dates(start=date(2025, 8, 28), span_days=365, count=252)
    with pytest.raises(PhaseF3AValidationError, match="fewer than 252 return intervals"):
        _build(_request(dates), metrics_policy, construction_policy)


def test_365_day_minimum_fails_independently(
    metrics_policy: PhaseF1PortfolioMetricsPolicy,
    construction_policy: CapitalDefensiveConstructionPolicy,
) -> None:
    dates = _spaced_dates(start=date(2025, 8, 29), span_days=364, count=253)
    with pytest.raises(PhaseF3AValidationError, match="fewer than 365 calendar days"):
        _build(_request(dates), metrics_policy, construction_policy)


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (lambda item: replace(item, constituent_identity="REALISH"), "explicit synthetic text"),
        (lambda item: replace(item, currency_code="HUF"), "EUR synthetic constituents only"),
        (lambda item: replace(item, source_identity="UNAPPROVED"), "source identity"),
        (lambda item: replace(item, source_governance="ADMITTED"), "source governance"),
        (
            lambda item: replace(
                item, distribution_state=SyntheticDistributionState.UNKNOWN
            ),
            "simulated accumulating",
        ),
        (
            lambda item: replace(
                item,
                distribution_state=(
                    SyntheticDistributionState.SIMULATED_DISTRIBUTING_SHARE_CLASS
                ),
            ),
            "simulated accumulating",
        ),
        (lambda item: replace(item, provenance_fingerprint="0" * 64), "fingerprint mismatch"),
    ),
)
def test_invalid_constituent_identity_currency_provenance_and_distribution_fail_closed(
    mutation: Callable[[SyntheticConstituentSeries], SyntheticConstituentSeries],
    message: str,
    metrics_policy: PhaseF1PortfolioMetricsPolicy,
    construction_policy: CapitalDefensiveConstructionPolicy,
) -> None:
    request = _request(_qualifying_dates())
    changed = mutation(request.constituents[0])
    request = replace(request, constituents=(changed, *request.constituents[1:]))
    with pytest.raises(PhaseF3AValidationError, match=message):
        _build(request, metrics_policy, construction_policy)


@pytest.mark.parametrize(
    ("values", "message"),
    (
        (
            (("not-a-date", Decimal(3)), ("2026-08-28", Decimal(3))),
            "canonical ISO date",
        ),
        (
            (("2025-08-28", Decimal(3)), ("2025-08-27", Decimal(3))),
            "strictly chronological",
        ),
        (
            (("2025-08-28", Decimal(3)), ("2025-08-28", Decimal(3))),
            "strictly chronological",
        ),
        (
            (("2025-08-28", Decimal(3)), ("2026-08-28", Decimal("NaN"))),
            "finite Decimal",
        ),
        (
            (("2025-08-28", Decimal(3)), ("2026-08-28", Decimal(0))),
            "positive",
        ),
    ),
)
def test_invalid_dates_and_values_fail_before_alignment(
    values: tuple[tuple[str, Decimal], ...],
    message: str,
    metrics_policy: PhaseF1PortfolioMetricsPolicy,
    construction_policy: CapitalDefensiveConstructionPolicy,
) -> None:
    request = _request(_qualifying_dates())
    with pytest.raises(PhaseF3AValidationError, match=message):
        invalid = create_synthetic_constituent_series(
            constituent_identity=request.constituents[0].constituent_identity,
            values=values,
            evidence_available_at_utc=DECISION_AS_OF,
        )
        request = replace(request, constituents=(invalid, *request.constituents[1:]))
        _build(request, metrics_policy, construction_policy)


def test_duplicate_identity_and_wrong_constituent_count_fail_closed(
    metrics_policy: PhaseF1PortfolioMetricsPolicy,
    construction_policy: CapitalDefensiveConstructionPolicy,
) -> None:
    request = _request(_qualifying_dates())
    duplicate = replace(
        request,
        constituents=(*request.constituents[:-1], request.constituents[0]),
    )
    with pytest.raises(PhaseF3AValidationError, match="identities must be unique"):
        _build(duplicate, metrics_policy, construction_policy)
    with pytest.raises(PhaseF3AValidationError, match="exactly eight"):
        _build(
            replace(request, constituents=request.constituents[:-1]),
            metrics_policy,
            construction_policy,
        )


def test_binary_float_cannot_cross_synthetic_decimal_boundary(
    metrics_policy: PhaseF1PortfolioMetricsPolicy,
    construction_policy: CapitalDefensiveConstructionPolicy,
) -> None:
    request = replace(
        _request(_qualifying_dates()),
        initial_capital=cast(Decimal, 100.0),
    )
    with pytest.raises(PhaseF3AValidationError, match="finite Decimal"):
        _build(request, metrics_policy, construction_policy)


def test_malformed_synthetic_reference_fails_closed(
    metrics_policy: PhaseF1PortfolioMetricsPolicy,
    construction_policy: CapitalDefensiveConstructionPolicy,
) -> None:
    request = _request(_qualifying_dates())
    invalid = bind_synthetic_constituent_provenance(
        replace(request.constituents[0], series_evidence_reference="REAL_EVIDENCE:NOT_ALLOWED")
    )
    with pytest.raises(PhaseF3AValidationError, match="explicit synthetic provenance"):
        _build(
            replace(request, constituents=(invalid, *request.constituents[1:])),
            metrics_policy,
            construction_policy,
        )


def test_stale_fixture_and_future_evidence_fail_closed(
    metrics_policy: PhaseF1PortfolioMetricsPolicy,
    construction_policy: CapitalDefensiveConstructionPolicy,
) -> None:
    stale_dates = _spaced_dates(start=date(2025, 7, 26), span_days=365, count=253)
    with pytest.raises(PhaseF3AValidationError, match="staleness"):
        _build(_request(stale_dates), metrics_policy, construction_policy)

    request = _request(_qualifying_dates())
    future = replace(
        request.constituents[0], evidence_available_at_utc="2026-09-04T12:24:24.000000Z"
    )
    future = bind_synthetic_constituent_provenance(future)
    with pytest.raises(PhaseF3AValidationError, match="unavailable at decision"):
        _build(
            replace(request, constituents=(future, *request.constituents[1:])),
            metrics_policy,
            construction_policy,
        )


def test_tampered_and_rehashed_lineage_is_rejected_by_recomputation(
    metrics_policy: PhaseF1PortfolioMetricsPolicy,
    construction_policy: CapitalDefensiveConstructionPolicy,
) -> None:
    request = _request(_qualifying_dates())
    lineage = _build(request, metrics_policy, construction_policy)
    tampered = replace(lineage, nominal_cash=Decimal(21))
    assert tampered.lineage_fingerprint != lineage.lineage_fingerprint
    with pytest.raises(PhaseF3AValidationError, match="recomputed derivation"):
        validate_synthetic_eur_portfolio_wealth(
            lineage=tampered,
            request=request,
            metrics_policy=metrics_policy,
            construction_policy=construction_policy,
        )

    first_point = lineage.wealth_points[0]
    rehashed_point = replace(first_point, total_wealth=first_point.total_wealth + Decimal(1))
    rehashed = replace(lineage, wealth_points=(rehashed_point, *lineage.wealth_points[1:]))
    assert rehashed_point.point_fingerprint != first_point.point_fingerprint
    with pytest.raises(PhaseF3AValidationError, match="recomputed derivation"):
        validate_synthetic_eur_portfolio_wealth(
            lineage=rehashed,
            request=request,
            metrics_policy=metrics_policy,
            construction_policy=construction_policy,
        )

    sub_q18_point = replace(
        first_point,
        total_wealth=first_point.total_wealth + Decimal("1E-25"),
    )
    assert sub_q18_point.payload_without_fingerprint()["total_wealth"] == (
        first_point.payload_without_fingerprint()["total_wealth"]
    )
    assert sub_q18_point.point_fingerprint != first_point.point_fingerprint
    sub_q18_lineage = replace(
        lineage,
        wealth_points=(sub_q18_point, *lineage.wealth_points[1:]),
    )
    with pytest.raises(PhaseF3AValidationError, match="recomputed derivation"):
        validate_synthetic_eur_portfolio_wealth(
            lineage=sub_q18_lineage,
            request=request,
            metrics_policy=metrics_policy,
            construction_policy=construction_policy,
        )


def test_unreviewed_construction_policy_mutation_fails_closed(
    metrics_policy: PhaseF1PortfolioMetricsPolicy,
    construction_policy: CapitalDefensiveConstructionPolicy,
) -> None:
    altered = replace(construction_policy, diversification=())
    with pytest.raises(PhaseF3AValidationError, match="construction policy differs"):
        _build(_request(_qualifying_dates()), metrics_policy, altered)


def test_input_correspondence_and_deterministic_serialization(
    metrics_policy: PhaseF1PortfolioMetricsPolicy,
    construction_policy: CapitalDefensiveConstructionPolicy,
) -> None:
    request = _request(_qualifying_dates())
    first = _build(request, metrics_policy, construction_policy)
    second = _build(
        replace(request, constituents=tuple(reversed(request.constituents))),
        metrics_policy,
        construction_policy,
    )
    with localcontext() as context:
        context.prec = 6
        low_ambient_precision = _build(request, metrics_policy, construction_policy)
    assert first.render_audit().encode("utf-8") == second.render_audit().encode("utf-8")
    assert first.render_audit().encode("utf-8") == low_ambient_precision.render_audit().encode(
        "utf-8"
    )
    assert first.lineage_fingerprint == second.lineage_fingerprint

    changed_series = create_synthetic_constituent_series(
        constituent_identity=request.constituents[0].constituent_identity,
        values=tuple(
            (item.observation_date, item.nav + (Decimal(1) if index == 0 else Decimal(0)))
            for index, item in enumerate(request.constituents[0].observations)
        ),
        evidence_available_at_utc=DECISION_AS_OF,
    )
    changed_request = replace(
        request, constituents=(changed_series, *request.constituents[1:])
    )
    with pytest.raises(PhaseF3AValidationError, match="recomputed derivation"):
        validate_synthetic_eur_portfolio_wealth(
            lineage=first,
            request=changed_request,
            metrics_policy=metrics_policy,
            construction_policy=construction_policy,
        )


def test_validated_synthetic_output_integrates_with_unchanged_f2_interface(
    metrics_policy: PhaseF1PortfolioMetricsPolicy,
    construction_policy: CapitalDefensiveConstructionPolicy,
) -> None:
    request = _request(_qualifying_dates())
    lineage = _build(request, metrics_policy, construction_policy)
    series = adapt_validated_synthetic_wealth_to_f2(
        lineage=lineage,
        request=request,
        metrics_policy=metrics_policy,
        construction_policy=construction_policy,
    )
    assert series.execution_mode is PhaseF2ExecutionMode.SYNTHETIC_FIXTURE
    assert series.observation_semantics is ObservationSemantics.PORTFOLIO_WEALTH_TOTAL_RETURN
    assert series.metric_suitability_state is MetricSuitabilityState.APPROVED_TOTAL_RETURN_SERIES
    assert lineage.lineage_fingerprint in series.series_identity

    run = compute_phase_f3a_synthetic_metrics(
        lineage=lineage,
        request=request,
        requested_metrics=(
            "TOTAL_RETURN",
            "ANNUALIZED_RETURN",
            "ANNUALIZED_VOLATILITY",
            "MAXIMUM_DRAWDOWN",
        ),
        metrics_policy=metrics_policy,
        construction_policy=construction_policy,
    )
    assert all(item.status is PhaseF2ComputationStatus.AVAILABLE for item in run.results)
    total_return = next(item for item in run.results if item.metric_id == "TOTAL_RETURN")
    _assert_close(total_return.value or Decimal(0), Decimal("0.10"))
    assert run.ranking_activation == "NOT_ACTIVATED"
    assert run.portfolio_selection_activation == "NOT_ACTIVATED"


def test_admitted_evidence_path_remains_blocked(
    metrics_policy: PhaseF1PortfolioMetricsPolicy,
    construction_policy: CapitalDefensiveConstructionPolicy,
) -> None:
    request = _request(_qualifying_dates())
    lineage = _build(request, metrics_policy, construction_policy)
    synthetic = adapt_validated_synthetic_wealth_to_f2(
        lineage=lineage,
        request=request,
        metrics_policy=metrics_policy,
        construction_policy=construction_policy,
    )
    relabelled = bind_series_provenance(
        replace(
            synthetic,
            execution_mode=PhaseF2ExecutionMode.ADMITTED_EVIDENCE,
            source_approval_state=SourceApprovalState.ADMITTED_VALIDATED,
            observation_fingerprint_scheme=(
                ObservationFingerprintScheme.PHASE_E_NAV_OBSERVATION_VERSION_V1
            ),
        )
    )
    run = compute_governed_metrics(
        series=relabelled,
        requested_metrics=("TOTAL_RETURN",),
        policy=metrics_policy,
    )
    assert run.results[0].status is PhaseF2ComputationStatus.POLICY_BLOCKED
    assert run.results[0].value is None
    assert "no trusted admitted" in (run.results[0].reason or "")


def test_unapproved_distribution_cannot_reach_f2(
    metrics_policy: PhaseF1PortfolioMetricsPolicy,
    construction_policy: CapitalDefensiveConstructionPolicy,
) -> None:
    request = _request(
        _qualifying_dates(),
        distribution_state=SyntheticDistributionState.SIMULATED_DISTRIBUTING_SHARE_CLASS,
    )
    with pytest.raises(PhaseF3AValidationError, match="simulated accumulating"):
        _build(request, metrics_policy, construction_policy)


def test_synthetic_series_factory_rejects_rebound_inconsistent_provenance(
    metrics_policy: PhaseF1PortfolioMetricsPolicy,
    construction_policy: CapitalDefensiveConstructionPolicy,
) -> None:
    request = _request(_qualifying_dates())
    series: SyntheticConstituentSeries = request.constituents[0]
    changed_observation = replace(series.observations[0], nav=Decimal(4))
    rebound = bind_synthetic_constituent_provenance(
        replace(series, observations=(changed_observation, *series.observations[1:]))
    )
    with pytest.raises(PhaseF3AValidationError, match="observation fingerprint mismatch"):
        _build(
            replace(request, constituents=(rebound, *request.constituents[1:])),
            metrics_policy,
            construction_policy,
        )


def test_f3a_audit_is_deterministic_and_never_claims_runtime_activation(
    metrics_policy: PhaseF1PortfolioMetricsPolicy,
    construction_policy: CapitalDefensiveConstructionPolicy,
) -> None:
    first = build_phase_f3a_wealth_foundation_audit(
        metrics_policy=metrics_policy,
        construction_policy=construction_policy,
    )
    second = build_phase_f3a_wealth_foundation_audit(
        metrics_policy=metrics_policy,
        construction_policy=construction_policy,
    )
    assert render_phase_f3a_wealth_foundation_audit(first).encode("utf-8") == (
        render_phase_f3a_wealth_foundation_audit(second).encode("utf-8")
    )
    assert first["audit_fingerprint"] == second["audit_fingerprint"]
    boundaries = first["regression_boundaries"]
    assert isinstance(boundaries, dict)
    assert boundaries["admitted_evidence_execution"] == "BLOCKED_NOT_AUTHORIZED"
    assert boundaries["portfolio_nav_reconstruction_freeze_change"] == "NOT_PERFORMED"
    assert boundaries["ranking_activation"] == "NOT_ACTIVATED"
    assert boundaries["production_cutover"] == "NOT_AUTHORIZED"
