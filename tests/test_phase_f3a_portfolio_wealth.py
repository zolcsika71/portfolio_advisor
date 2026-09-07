"""Focused tests for the synthetic-only Phase F3A wealth foundation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import date, timedelta
from decimal import (
    ROUND_DOWN,
    Clamped,
    Context,
    Decimal,
    Inexact,
    Overflow,
    Rounded,
    localcontext,
)
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

from portfolio_advisor.canonical import canonical_fingerprint
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
    SyntheticNavObservation,
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
PLAIN_THOUSAND = Decimal("1000")  # noqa: FURB157 - representation is under test.


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


def _decimal_context_state(context: Context) -> tuple[object, ...]:
    return (
        context.prec,
        context.rounding,
        context.Emin,
        context.Emax,
        context.capitals,
        context.clamp,
        tuple(sorted((signal.__name__, enabled) for signal, enabled in context.flags.items())),
        tuple(sorted((signal.__name__, enabled) for signal, enabled in context.traps.items())),
    )


def _request_with_first_nav_representation(
    nav: Decimal,
) -> SyntheticPortfolioWealthRequest:
    request = _request(_qualifying_dates())
    first = create_synthetic_constituent_series(
        constituent_identity=request.constituents[0].constituent_identity,
        values=tuple((observation_date, nav) for observation_date in _qualifying_dates()),
        evidence_available_at_utc=DECISION_AS_OF,
    )
    return replace(request, constituents=(first, *request.constituents[1:]))


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


def test_valid_construction_ignores_ambient_inexact_and_rounded_traps(
    metrics_policy: PhaseF1PortfolioMetricsPolicy,
    construction_policy: CapitalDefensiveConstructionPolicy,
) -> None:
    request = _request(_qualifying_dates())
    reference = _build(request, metrics_policy, construction_policy)

    with localcontext() as ambient:
        ambient.traps[Inexact] = True
        ambient.traps[Rounded] = True
        ambient.flags[Inexact] = True
        before = _decimal_context_state(ambient)
        actual = _build(request, metrics_policy, construction_policy)
        after = _decimal_context_state(ambient)

    assert actual.render_audit() == reference.render_audit()
    assert after == before


def test_valid_construction_ignores_restrictive_ambient_exponent_bounds(
    metrics_policy: PhaseF1PortfolioMetricsPolicy,
    construction_policy: CapitalDefensiveConstructionPolicy,
) -> None:
    request = _request(_qualifying_dates())
    reference = _build(request, metrics_policy, construction_policy)

    with localcontext() as ambient:
        ambient.Emin = -1
        ambient.Emax = 1
        ambient.traps[Overflow] = True
        before = _decimal_context_state(ambient)
        actual = _build(request, metrics_policy, construction_policy)
        after = _decimal_context_state(ambient)

    assert actual.render_audit() == reference.render_audit()
    assert after == before


def test_construction_validation_adaptation_metrics_and_audit_ignore_ambient_context(
    metrics_policy: PhaseF1PortfolioMetricsPolicy,
    construction_policy: CapitalDefensiveConstructionPolicy,
) -> None:
    request = replace(
        _request_with_first_nav_representation(Decimal("1E+3")),
        initial_capital=Decimal("1E+30"),
    )
    reference_lineage = _build(request, metrics_policy, construction_policy)
    reference_lineage_payload = reference_lineage.to_dict()
    reference_lineage_audit = reference_lineage.render_audit()
    reference_series = adapt_validated_synthetic_wealth_to_f2(
        lineage=reference_lineage,
        request=request,
        metrics_policy=metrics_policy,
        construction_policy=construction_policy,
    )
    reference_metrics = compute_phase_f3a_synthetic_metrics(
        lineage=reference_lineage,
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
    reference_audit = render_phase_f3a_wealth_foundation_audit(
        build_phase_f3a_wealth_foundation_audit(
            metrics_policy=metrics_policy,
            construction_policy=construction_policy,
        )
    )

    with localcontext() as ambient:
        ambient.prec = 2
        ambient.rounding = ROUND_DOWN
        ambient.Emin = -1
        ambient.Emax = 1
        ambient.capitals = 0
        ambient.clamp = 1
        ambient.traps[Clamped] = True
        ambient.traps[Inexact] = True
        ambient.traps[Overflow] = True
        ambient.traps[Rounded] = True
        ambient.clear_flags()
        ambient.flags[Rounded] = True
        before = _decimal_context_state(ambient)
        actual_request = replace(
            _request_with_first_nav_representation(Decimal("1E+3")),
            initial_capital=Decimal("1E+30"),
        )
        actual_lineage = _build(actual_request, metrics_policy, construction_policy)
        actual_lineage_payload = actual_lineage.to_dict()
        actual_lineage_audit = actual_lineage.render_audit()
        validate_synthetic_eur_portfolio_wealth(
            lineage=actual_lineage,
            request=actual_request,
            metrics_policy=metrics_policy,
            construction_policy=construction_policy,
        )
        actual_series = adapt_validated_synthetic_wealth_to_f2(
            lineage=actual_lineage,
            request=actual_request,
            metrics_policy=metrics_policy,
            construction_policy=construction_policy,
        )
        actual_metrics = compute_phase_f3a_synthetic_metrics(
            lineage=actual_lineage,
            request=actual_request,
            requested_metrics=(
                "TOTAL_RETURN",
                "ANNUALIZED_RETURN",
                "ANNUALIZED_VOLATILITY",
                "MAXIMUM_DRAWDOWN",
            ),
            metrics_policy=metrics_policy,
            construction_policy=construction_policy,
        )
        actual_audit = render_phase_f3a_wealth_foundation_audit(
            build_phase_f3a_wealth_foundation_audit(
                metrics_policy=metrics_policy,
                construction_policy=construction_policy,
            )
        )
        after = _decimal_context_state(ambient)

    assert actual_request == request
    assert actual_lineage_payload == reference_lineage_payload
    assert actual_lineage_audit == reference_lineage_audit
    assert actual_series == reference_series
    assert actual_metrics.to_dict() == reference_metrics.to_dict()
    assert actual_audit == reference_audit
    assert after == before


def test_caller_decimal_context_is_preserved_when_validation_raises(
    metrics_policy: PhaseF1PortfolioMetricsPolicy,
    construction_policy: CapitalDefensiveConstructionPolicy,
) -> None:
    request = replace(_request(_qualifying_dates()), initial_capital=Decimal(0))
    with localcontext() as ambient:
        ambient.prec = 7
        ambient.rounding = ROUND_DOWN
        ambient.Emin = -7
        ambient.Emax = 7
        ambient.flags[Rounded] = True
        before = _decimal_context_state(ambient)
        with pytest.raises(PhaseF3AValidationError, match="initial capital must be positive"):
            _build(request, metrics_policy, construction_policy)
        after = _decimal_context_state(ambient)
    assert after == before


@pytest.mark.parametrize(
    ("left", "right"),
    (
        (Decimal("1E+3"), PLAIN_THOUSAND),
        (Decimal("1.0"), Decimal("1.00")),
        (Decimal("-0"), Decimal(0)),
    ),
)
def test_source_decimal_fingerprint_preserves_sign_digits_and_exponent(
    left: Decimal,
    right: Decimal,
) -> None:
    assert left == right
    assert left.as_tuple() != right.as_tuple()
    left_observation = SyntheticNavObservation.create(
        constituent_identity="SYNTHETIC_DECIMAL_REPRESENTATION",
        observation_date="2025-08-28",
        nav=left,
        evidence_reference="SYNTHETIC_FIXTURE:PHASE_F3A:DECIMAL:OBSERVATION",
    )
    right_observation = SyntheticNavObservation.create(
        constituent_identity="SYNTHETIC_DECIMAL_REPRESENTATION",
        observation_date="2025-08-28",
        nav=right,
        evidence_reference="SYNTHETIC_FIXTURE:PHASE_F3A:DECIMAL:OBSERVATION",
    )
    assert left_observation.payload("SYNTHETIC_DECIMAL_REPRESENTATION")["nav"] != (
        right_observation.payload("SYNTHETIC_DECIMAL_REPRESENTATION")["nav"]
    )
    assert left_observation.observation_fingerprint != (
        right_observation.observation_fingerprint
    )


@pytest.mark.parametrize(
    ("left", "right"),
    (
        (Decimal("1E+3"), PLAIN_THOUSAND),
        (Decimal("1.0"), Decimal("1.00")),
    ),
)
def test_valid_source_decimal_representations_produce_distinct_lineage(
    left: Decimal,
    right: Decimal,
    metrics_policy: PhaseF1PortfolioMetricsPolicy,
    construction_policy: CapitalDefensiveConstructionPolicy,
) -> None:
    left_request = _request_with_first_nav_representation(left)
    right_request = _request_with_first_nav_representation(right)
    left_lineage = _build(left_request, metrics_policy, construction_policy)
    right_lineage = _build(right_request, metrics_policy, construction_policy)

    assert left_request.constituents[0].provenance_fingerprint != (
        right_request.constituents[0].provenance_fingerprint
    )
    assert left_lineage.lineage_fingerprint != right_lineage.lineage_fingerprint


def test_source_decimal_representation_change_requires_rebuilt_fingerprints_and_lineage(
    metrics_policy: PhaseF1PortfolioMetricsPolicy,
    construction_policy: CapitalDefensiveConstructionPolicy,
) -> None:
    original_request = _request_with_first_nav_representation(Decimal("1E+3"))
    original_lineage = _build(original_request, metrics_policy, construction_policy)
    original_series = original_request.constituents[0]
    changed_observations = tuple(
        replace(observation, nav=PLAIN_THOUSAND)
        for observation in original_series.observations
    )
    unrefingerprinted_tamper = replace(original_series, observations=changed_observations)

    with pytest.raises(
        PhaseF3AValidationError, match="constituent provenance fingerprint mismatch"
    ):
        _build(
            replace(
                original_request,
                constituents=(
                    unrefingerprinted_tamper,
                    *original_request.constituents[1:],
                ),
            ),
            metrics_policy,
            construction_policy,
        )

    rebuilt_request = _request_with_first_nav_representation(PLAIN_THOUSAND)
    with pytest.raises(PhaseF3AValidationError, match="recomputed derivation"):
        validate_synthetic_eur_portfolio_wealth(
            lineage=original_lineage,
            request=rebuilt_request,
            metrics_policy=metrics_policy,
            construction_policy=construction_policy,
        )


def test_obsolete_v1_source_fingerprint_is_not_accepted(
    metrics_policy: PhaseF1PortfolioMetricsPolicy,
    construction_policy: CapitalDefensiveConstructionPolicy,
) -> None:
    request = _request_with_first_nav_representation(Decimal("1E+3"))
    series = request.constituents[0]
    observation = series.observations[0]
    obsolete_payload = observation.payload(series.constituent_identity)
    obsolete_payload.update(
        {
            "fingerprint_scheme": "PHASE_F3A_SYNTHETIC_NAV_V1",
            "nav": format(observation.nav, "f"),
        }
    )
    obsolete_observation = replace(
        observation,
        observation_fingerprint=canonical_fingerprint(obsolete_payload),
    )
    obsolete_series = bind_synthetic_constituent_provenance(
        replace(series, observations=(obsolete_observation, *series.observations[1:]))
    )

    with pytest.raises(PhaseF3AValidationError, match="observation fingerprint mismatch"):
        _build(
            replace(request, constituents=(obsolete_series, *request.constituents[1:])),
            metrics_policy,
            construction_policy,
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
    rendered = render_phase_f3a_wealth_foundation_audit(first).encode("utf-8")
    assert rendered == render_phase_f3a_wealth_foundation_audit(second).encode("utf-8")
    assert sha256(rendered).hexdigest() == (
        "af00676072ae8ba5f6a5e22aba0794059fbaf30c7d6b54b0eccedc8f57e08c0c"
    )
    assert first["audit_fingerprint"] == second["audit_fingerprint"]
    assert first["audit_fingerprint"] == (
        "b06f3517ec2edeaa88514d7744ddae0e1aff85a4bf5609f025b9981d8daedfeb"
    )
    numerical = first["numerical_conventions"]
    assert isinstance(numerical, dict)
    assert numerical["source_decimal_encoding"] == "DECIMAL_STR_SIGN_DIGITS_EXPONENT_V1"
    assert numerical["synthetic_nav_fingerprint_scheme"] == (
        "PHASE_F3A_SYNTHETIC_NAV_V2"
    )
    boundaries = first["regression_boundaries"]
    assert isinstance(boundaries, dict)
    assert boundaries["admitted_evidence_execution"] == "BLOCKED_NOT_AUTHORIZED"
    assert boundaries["portfolio_nav_reconstruction_freeze_change"] == "NOT_PERFORMED"
    assert boundaries["ranking_activation"] == "NOT_ACTIVATED"
    assert boundaries["production_cutover"] == "NOT_AUTHORIZED"
