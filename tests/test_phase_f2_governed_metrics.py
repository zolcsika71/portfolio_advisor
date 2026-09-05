"""Milestone 11C Phase F2 governed metric-foundation tests."""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import replace
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

from portfolio_advisor.metrics.foundation_audit import (
    build_phase_f2_foundation_audit,
    render_phase_f2_foundation_audit,
)
from portfolio_advisor.metrics.governed import (
    PHASE_F2_ACTIVATION_STATE,
    GovernedMetricResult,
    GovernedMetricRun,
    GovernedMetricSeries,
    GovernedObservation,
    MetricSuitabilityState,
    ObservationFingerprintScheme,
    ObservationSemantics,
    PhaseF2ComputationStatus,
    PhaseF2ExecutionMode,
    SourceApprovalState,
    bind_series_provenance,
    build_observed_return_intervals,
    compute_governed_metrics,
    phase_f2_contract_inventory,
)
from portfolio_advisor.metrics.phase_e_adapter import (
    PhaseEReadError,
    load_admitted_phase_e_nav_series,
)
from portfolio_advisor.metrics.policy_contract import (
    PHASE_F1_POLICY_ARTIFACT,
    PHASE_F1_POLICY_FINGERPRINT,
    PhaseF1PortfolioMetricsPolicy,
    load_phase_f1_portfolio_metrics_policy,
)

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / PHASE_F1_POLICY_ARTIFACT
DATABASE = ROOT / "database/portfolio_advisor.sqlite"


@pytest.fixture(scope="module")
def policy() -> PhaseF1PortfolioMetricsPolicy:
    return load_phase_f1_portfolio_metrics_policy(POLICY_PATH)


def _series(
    values: tuple[str, ...],
    *,
    dates: tuple[str, ...] | None = None,
    identity: str = "F2_FIXTURE",
    currency: str = "EUR",
    mode: PhaseF2ExecutionMode = PhaseF2ExecutionMode.SYNTHETIC_FIXTURE,
    semantics: ObservationSemantics = ObservationSemantics.PORTFOLIO_WEALTH_TOTAL_RETURN,
    suitability: MetricSuitabilityState = MetricSuitabilityState.APPROVED_TOTAL_RETURN_SERIES,
    approval: SourceApprovalState | None = None,
) -> GovernedMetricSeries:
    if dates is None:
        start = date(2026, 1, 1)
        dates = tuple((start + timedelta(days=index)).isoformat() for index in range(len(values)))
    observations = tuple(
        GovernedObservation.create(
            observation_date=observation_date,
            value=Decimal(value),
            evidence_reference=f"SYNTHETIC_FIXTURE:{identity}:{index}",
        )
        for index, (observation_date, value) in enumerate(zip(dates, values, strict=True))
    )
    source_approval = approval or (
        SourceApprovalState.SYNTHETIC_FIXTURE_APPROVED
        if mode is PhaseF2ExecutionMode.SYNTHETIC_FIXTURE
        else SourceApprovalState.ADMITTED_VALIDATED
    )
    return bind_series_provenance(
        GovernedMetricSeries(
            series_identity=identity,
            subject_identity=identity,
            source_identity=(
                "PHASE_F2_SYNTHETIC_REFERENCE_FIXTURE"
                if mode is PhaseF2ExecutionMode.SYNTHETIC_FIXTURE
                else "ADMITTED_PORTFOLIO_WEALTH_SOURCE"
            ),
            source_governance=(
                "SYNTHETIC_FIXTURE_ONLY"
                if mode is PhaseF2ExecutionMode.SYNTHETIC_FIXTURE
                else "APPROVED_EVIDENCE_SOURCE"
            ),
            currency_code=currency,
            observation_semantics=semantics,
            source_approval_state=source_approval,
            metric_suitability_state=suitability,
            observation_fingerprint_scheme=(
                ObservationFingerprintScheme.PHASE_F2_SYNTHETIC_V1
                if mode is PhaseF2ExecutionMode.SYNTHETIC_FIXTURE
                else ObservationFingerprintScheme.PHASE_E_NAV_OBSERVATION_VERSION_V1
            ),
            execution_mode=mode,
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


def _result(run: GovernedMetricRun, metric_id: str) -> GovernedMetricResult:
    return next(item for item in run.results if item.metric_id == metric_id)


def _compute(
    policy: PhaseF1PortfolioMetricsPolicy,
    series: GovernedMetricSeries,
    *metric_ids: str,
) -> GovernedMetricRun:
    return compute_governed_metrics(
        series=series,
        requested_metrics=metric_ids,
        policy=policy,
    )


def test_exact_f1_policy_is_bound_to_every_result(policy: PhaseF1PortfolioMetricsPolicy) -> None:
    run = _compute(policy, _series(("100", "110")), "TOTAL_RETURN")
    result = _result(run, "TOTAL_RETURN")
    assert result.status is PhaseF2ComputationStatus.AVAILABLE
    assert (result.policy_id, result.policy_version, result.policy_fingerprint) == (
        "CAPITAL_DEFENSIVE_PORTFOLIO_METRICS_POLICY",
        "1.0.0",
        PHASE_F1_POLICY_FINGERPRINT,
    )
    assert run.activation_state == PHASE_F2_ACTIVATION_STATE
    assert run.ranking_activation == "NOT_ACTIVATED"
    assert run.portfolio_selection_activation == "NOT_ACTIVATED"
    decision_execution = phase_f2_contract_inventory()["decision_execution"]
    assert isinstance(decision_execution, dict)
    assert tuple(decision_execution) == tuple(f"F1-D{index:02d}" for index in range(1, 23))


def test_altered_or_missing_policy_fails_closed(policy: PhaseF1PortfolioMetricsPolicy) -> None:
    malformed = PhaseF1PortfolioMetricsPolicy(
        _canonical_payload="{}",
        artifact_reference=policy.artifact_reference,
    )
    run = _compute(malformed, _series(("100", "101")), "TOTAL_RETURN")
    assert _result(run, "TOTAL_RETURN").status is PhaseF2ComputationStatus.POLICY_BLOCKED
    assert _result(run, "TOTAL_RETURN").value is None
    with pytest.raises(ValueError, match="policy"):
        build_observed_return_intervals(_series(("100", "101")), policy=malformed)


def test_unsupported_and_underspecified_metrics_fail_closed(
    policy: PhaseF1PortfolioMetricsPolicy,
) -> None:
    run = _compute(
        policy,
        _series(("100", "101", "99")),
        "UNKNOWN_METRIC",
        "SHARPE_RATIO",
        "SORTINO_RATIO",
        "DOWNSIDE_DEVIATION",
        "HISTORICAL_VAR",
        "HISTORICAL_CVAR",
    )
    assert _result(run, "UNKNOWN_METRIC").status is PhaseF2ComputationStatus.UNSUPPORTED_METRIC
    for metric_id in ("SHARPE_RATIO", "SORTINO_RATIO", "DOWNSIDE_DEVIATION"):
        assert _result(run, metric_id).status is PhaseF2ComputationStatus.POLICY_BLOCKED
        assert _result(run, metric_id).value is None
    for metric_id in ("HISTORICAL_VAR", "HISTORICAL_CVAR"):
        assert _result(run, metric_id).status is PhaseF2ComputationStatus.UNSUPPORTED_METRIC


def test_forbidden_semantics_and_unapproved_source_are_rejected(
    policy: PhaseF1PortfolioMetricsPolicy,
) -> None:
    price_only = _series(
        ("100", "101"),
        semantics=ObservationSemantics.INSTRUMENT_NAV_PRICE_ONLY,
        suitability=MetricSuitabilityState.UNKNOWN_DISTRIBUTION_STATUS,
    )
    assert _result(_compute(policy, price_only, "TOTAL_RETURN"), "TOTAL_RETURN").status is (
        PhaseF2ComputationStatus.SEMANTICS_NOT_APPROVED
    )
    unapproved = _series(
        ("100", "101"),
        approval=SourceApprovalState.ADMITTED_VALIDATED,
    )
    assert _result(_compute(policy, unapproved, "TOTAL_RETURN"), "TOTAL_RETURN").status is (
        PhaseF2ComputationStatus.INPUT_REJECTED
    )


def test_chronological_observations_build_only_observed_intervals(
    policy: PhaseF1PortfolioMetricsPolicy,
) -> None:
    series = _series(
        ("100", "101", "103"),
        dates=("2026-01-01", "2026-01-02", "2026-01-05"),
    )
    intervals = build_observed_return_intervals(series, policy=policy)
    assert [(item.start_date, item.end_date, item.elapsed_calendar_days) for item in intervals] == [
        ("2026-01-01", "2026-01-02", 1),
        ("2026-01-02", "2026-01-05", 3),
    ]
    assert len(intervals) == len(series.observations) - 1


def test_unordered_duplicate_and_malformed_dates_fail_closed(
    policy: PhaseF1PortfolioMetricsPolicy,
) -> None:
    cases = (
        _series(("100", "101"), dates=("2026-01-02", "2026-01-01")),
        _series(("100", "101"), dates=("2026-01-01", "2026-01-01")),
        _series(("100", "100"), dates=("2026-01-01", "2026-01-01")),
        _series(("100", "101"), dates=("2026-01-01", "2026-1-2")),
    )
    for series in cases:
        result = _result(_compute(policy, series, "TOTAL_RETURN"), "TOTAL_RETURN")
        assert result.status is PhaseF2ComputationStatus.INPUT_REJECTED


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_nonfinite_observation_is_rejected(
    policy: PhaseF1PortfolioMetricsPolicy,
    value: Decimal,
) -> None:
    observation = GovernedObservation(
        observation_date="2026-01-01",
        value=value,
        evidence_reference="SYNTHETIC_FIXTURE:NONFINITE:0",
        observation_fingerprint="a" * 64,
    )
    series = replace(_series(("100", "101")), observations=(observation,), provenance_fingerprint="a" * 64)
    result = _result(_compute(policy, series, "TOTAL_RETURN"), "TOTAL_RETURN")
    assert result.status is PhaseF2ComputationStatus.INPUT_REJECTED
    assert result.value is None


@pytest.mark.parametrize("value", ["0", "-1"])
def test_nonpositive_endpoint_is_rejected(
    policy: PhaseF1PortfolioMetricsPolicy,
    value: str,
) -> None:
    result = _result(_compute(policy, _series(("100", value)), "TOTAL_RETURN"), "TOTAL_RETURN")
    assert result.status is PhaseF2ComputationStatus.INPUT_REJECTED


@pytest.mark.parametrize(
    ("end", "expected"),
    (("110", "0.100000000000000000"), ("90", "-0.100000000000000000"), ("100", "0.000000000000000000")),
)
def test_governed_total_return_sign_and_convention(
    policy: PhaseF1PortfolioMetricsPolicy,
    end: str,
    expected: str,
) -> None:
    result = _result(_compute(policy, _series(("100", end)), "TOTAL_RETURN"), "TOTAL_RETURN")
    assert result.status is PhaseF2ComputationStatus.AVAILABLE
    assert result.to_dict()["value"] == expected


def test_geometric_total_return_and_elapsed_annualization(
    policy: PhaseF1PortfolioMetricsPolicy,
) -> None:
    total = _result(
        _compute(policy, _series(("100", "110", "99")), "TOTAL_RETURN"),
        "TOTAL_RETURN",
    )
    annualized = _result(
        _compute(
            policy,
            _series(
                ("100", "121"),
                dates=("2024-01-01", "2025-12-31"),
                identity="ANNUALIZED",
            ),
            "ANNUALIZED_RETURN",
        ),
        "ANNUALIZED_RETURN",
    )
    assert total.to_dict()["value"] == "-0.010000000000000000"
    assert annualized.to_dict()["value"] == "0.100000000000000000"


def test_insufficient_observations_never_become_zero_risk(
    policy: PhaseF1PortfolioMetricsPolicy,
) -> None:
    series = _series(("100",))
    run = _compute(policy, series, "TOTAL_RETURN", "ANNUALIZED_VOLATILITY", "MAXIMUM_DRAWDOWN")
    assert {item.status for item in run.results} == {PhaseF2ComputationStatus.INSUFFICIENT_DATA}
    assert all(item.value is None for item in run.results)


def test_d11_regular_reference_and_equal_gap_equivalence(
    policy: PhaseF1PortfolioMetricsPolicy,
) -> None:
    series = _series(
        ("100", "110", "99"),
        dates=("2021-01-01", "2022-01-01", "2023-01-01"),
        identity="D11_REGULAR",
    )
    result = _result(_compute(policy, series, "ANNUALIZED_VOLATILITY"), "ANNUALIZED_VOLATILITY")
    assert result.to_dict()["value"] == "0.141895609546707636"
    equal_gap = _series(
        ("100", "110", "99"),
        dates=("2026-01-01", "2026-01-08", "2026-01-15"),
        identity="D11_EQUAL_SEVEN_DAY_GAPS",
    )
    equal_gap_result = _result(
        _compute(policy, equal_gap, "ANNUALIZED_VOLATILITY"),
        "ANNUALIZED_VOLATILITY",
    )
    intervals = build_observed_return_intervals(equal_gap, policy=policy)
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        mean = sum((item.log_return for item in intervals), Decimal(0)) / Decimal(2)
        sample_variance = sum((item.log_return - mean) ** 2 for item in intervals)
        gap_years = Decimal(7) / Decimal(365)
        assert equal_gap_result.value is not None
        assert abs(equal_gap_result.value**2 - sample_variance / gap_years) < Decimal("1E-47")


def test_d11_irregular_reference_constant_series_and_no_resampling(
    policy: PhaseF1PortfolioMetricsPolicy,
) -> None:
    dates = ("2026-01-01", "2026-01-02", "2026-01-05")
    irregular = _series(("100", "101", "103"), dates=dates, identity="D11_IRREGULAR")
    flat = _series(("100", "100", "100"), dates=dates, identity="D11_FLAT")
    irregular_result = _result(
        _compute(policy, irregular, "ANNUALIZED_VOLATILITY"),
        "ANNUALIZED_VOLATILITY",
    )
    flat_result = _result(
        _compute(policy, flat, "ANNUALIZED_VOLATILITY"),
        "ANNUALIZED_VOLATILITY",
    )
    assert irregular_result.to_dict()["value"] == "0.056488842982830394"
    assert flat_result.value == Decimal(0)
    assert flat_result.to_dict()["value"] == "0.000000000000000000"
    assert irregular_result.interval_count == 2


@pytest.mark.parametrize(
    ("values", "expected"),
    (
        (("100", "110", "121"), "0.000000000000000000"),
        (("100", "90", "80"), "-0.200000000000000000"),
        (("100", "80", "120"), "-0.200000000000000000"),
        (("100", "80", "120", "90", "100"), "-0.250000000000000000"),
        (("100", "100", "100"), "0.000000000000000000"),
    ),
)
def test_maximum_drawdown_observed_wealth_path_and_sign(
    policy: PhaseF1PortfolioMetricsPolicy,
    values: tuple[str, ...],
    expected: str,
) -> None:
    result = _result(_compute(policy, _series(values), "MAXIMUM_DRAWDOWN"), "MAXIMUM_DRAWDOWN")
    assert result.to_dict()["value"] == expected
    assert result.value is not None and result.value <= 0


def test_wrong_contract_metadata_and_provenance_mismatch_fail_closed(
    policy: PhaseF1PortfolioMetricsPolicy,
) -> None:
    original = _series(("100", "101"))
    wrong_contract = bind_series_provenance(replace(original, alignment_method="UNION_WITH_FILL"))
    mismatch = replace(original, source_identity="DIFFERENT_SOURCE")
    assert _result(_compute(policy, wrong_contract, "TOTAL_RETURN"), "TOTAL_RETURN").status is (
        PhaseF2ComputationStatus.SEMANTICS_NOT_APPROVED
    )
    assert _result(_compute(policy, mismatch, "TOTAL_RETURN"), "TOTAL_RETURN").status is (
        PhaseF2ComputationStatus.INPUT_REJECTED
    )


def test_observation_fingerprint_and_evidence_inventory_fail_closed(
    policy: PhaseF1PortfolioMetricsPolicy,
) -> None:
    original = _series(("100", "101"), identity="PROVENANCE_BOUNDARY")
    altered_observation = replace(original.observations[0], value=Decimal(999))
    stale_fingerprint = bind_series_provenance(
        replace(original, observations=(altered_observation, original.observations[1]))
    )
    orphan_observation = GovernedObservation.create(
        observation_date="2026-01-01",
        value=Decimal(100),
        evidence_reference="SYNTHETIC_FIXTURE:ORPHAN:0",
    )
    orphan = bind_series_provenance(
        replace(original, observations=(orphan_observation, original.observations[1]))
    )
    empty_inventory = bind_series_provenance(replace(original, evidence_references=()))
    wrong_scheme = bind_series_provenance(
        replace(
            original,
            observation_fingerprint_scheme=(
                ObservationFingerprintScheme.PHASE_E_NAV_OBSERVATION_VERSION_V1
            ),
        )
    )
    for series in (stale_fingerprint, orphan, empty_inventory, wrong_scheme):
        result = _result(_compute(policy, series, "TOTAL_RETURN"), "TOTAL_RETURN")
        assert result.status is PhaseF2ComputationStatus.INPUT_REJECTED
        assert result.value is None


def test_different_provenance_changes_result_and_run_fingerprints(
    policy: PhaseF1PortfolioMetricsPolicy,
) -> None:
    first = _compute(policy, _series(("100", "101"), identity="A"), "TOTAL_RETURN")
    second = _compute(policy, _series(("100", "101"), identity="B"), "TOTAL_RETURN")
    assert first.run_fingerprint != second.run_fingerprint
    assert first.results[0].result_fingerprint != second.results[0].result_fingerprint


def test_repeated_execution_and_audit_are_byte_identical(
    policy: PhaseF1PortfolioMetricsPolicy,
) -> None:
    series = _series(("100", "101", "103"), identity="DETERMINISM")
    first = _compute(policy, series, "MAXIMUM_DRAWDOWN", "TOTAL_RETURN", "ANNUALIZED_VOLATILITY")
    second = _compute(policy, series, "ANNUALIZED_VOLATILITY", "TOTAL_RETURN", "MAXIMUM_DRAWDOWN")
    assert first.render_audit() == second.render_audit()
    phase_e = {
        "constructed_portfolio_row_counts": {
            "constructed_portfolio_holding_lineage": 0,
            "constructed_portfolio_metadata": 0,
        },
        "currency_ranges": {
            "EUR": {"isin_count": 8, "observation_count": 1959},
            "HUF": {"isin_count": 8, "observation_count": 2025},
        },
        "database_sha256": "f" * 64,
        "legacy_nav": {
            "dataset_fingerprint": (
                "b2e6e4b8c2066c932d6933dbb07d8f22ab1fa9e2cd04c88eae7283334829f99a"
            ),
            "isin_count": 19,
            "observation_count": 8770,
        },
        "manifest_count": 16,
        "observation_count": 3984,
        "foreign_key_violations": 0,
        "integrity_check": "ok",
        "phase_e_dataset_fingerprint": "c" * 64,
    }
    audit_a = build_phase_f2_foundation_audit(policy=policy, phase_e_validation=phase_e)
    audit_b = build_phase_f2_foundation_audit(policy=policy, phase_e_validation=phase_e)
    assert render_phase_f2_foundation_audit(audit_a) == render_phase_f2_foundation_audit(audit_b)
    with pytest.raises(ValueError, match="constructed rows"):
        build_phase_f2_foundation_audit(
            policy=policy,
            phase_e_validation={
                **phase_e,
                "constructed_portfolio_row_counts": {
                    "constructed_portfolio_holding_lineage": 0,
                    "constructed_portfolio_metadata": 1,
                },
            },
        )


def test_admitted_window_enforces_365_days_and_252_intervals(
    policy: PhaseF1PortfolioMetricsPolicy,
) -> None:
    start = date(2025, 8, 31)
    dates = tuple(
        (start + timedelta(days=(index * 365) // 252)).isoformat()
        for index in range(253)
    )
    eligible = _series(
        tuple("100" for _ in dates),
        dates=dates,
        identity="ADMITTED_ELIGIBLE",
        mode=PhaseF2ExecutionMode.ADMITTED_EVIDENCE,
    )
    eligible_result = _result(_compute(policy, eligible, "TOTAL_RETURN"), "TOTAL_RETURN")
    assert eligible_result.status is PhaseF2ComputationStatus.POLICY_BLOCKED
    assert eligible_result.value is None
    too_short_span = _series(
        tuple("100" for _ in dates),
        dates=tuple(
            (start + timedelta(days=(index * 364) // 252)).isoformat()
            for index in range(253)
        ),
        identity="ADMITTED_SHORT_SPAN",
        mode=PhaseF2ExecutionMode.ADMITTED_EVIDENCE,
    )
    too_few_intervals = _series(
        tuple("100" for _ in range(252)),
        dates=tuple(
            (start + timedelta(days=(index * 365) // 251)).isoformat()
            for index in range(252)
        ),
        identity="ADMITTED_FEW_INTERVALS",
        mode=PhaseF2ExecutionMode.ADMITTED_EVIDENCE,
    )
    for series in (too_short_span, too_few_intervals):
        assert _result(_compute(policy, series, "TOTAL_RETURN"), "TOTAL_RETURN").status is (
            PhaseF2ComputationStatus.INSUFFICIENT_DATA
        )


def test_admitted_temporal_currency_and_window_contracts_fail_closed(
    policy: PhaseF1PortfolioMetricsPolicy,
) -> None:
    start = date(2025, 8, 31)
    dates = tuple(
        (start + timedelta(days=(index * 365) // 252)).isoformat()
        for index in range(253)
    )
    eligible = _series(
        tuple("100" for _ in dates),
        dates=dates,
        identity="ADMITTED_BOUNDARIES",
        mode=PhaseF2ExecutionMode.ADMITTED_EVIDENCE,
    )
    cases = (
        (replace(eligible, currency_code="HUF"), PhaseF2ComputationStatus.POLICY_BLOCKED),
        (
            replace(eligible, decision_as_of_utc="2026-09-04T12:24:24.000000Z"),
            PhaseF2ComputationStatus.POLICY_BLOCKED,
        ),
        (
            replace(eligible, nav_evidence_cutoff="2026-08-30"),
            PhaseF2ComputationStatus.POLICY_BLOCKED,
        ),
        (
            replace(eligible, window_selection_method="EARLIEST_AVAILABLE_WINDOW"),
            PhaseF2ComputationStatus.SEMANTICS_NOT_APPROVED,
        ),
        (
            replace(eligible, evidence_available_at_utc="2026-09-04T12:24:24.000000Z"),
            PhaseF2ComputationStatus.INPUT_REJECTED,
        ),
    )
    for item, expected_status in cases:
        series = bind_series_provenance(item)
        result = _result(_compute(policy, series, "TOTAL_RETURN"), "TOTAL_RETURN")
        assert result.status is expected_status

    future_observation = replace(
        eligible.observations[-1],
        observation_date="2026-09-01",
    )
    beyond_cutoff = bind_series_provenance(
        replace(eligible, observations=(*eligible.observations[:-1], future_observation))
    )
    result = _result(_compute(policy, beyond_cutoff, "TOTAL_RETURN"), "TOTAL_RETURN")
    assert result.status is PhaseF2ComputationStatus.INPUT_REJECTED

    stale_dates = tuple(
        (start - timedelta(days=31) + timedelta(days=(index * 365) // 252)).isoformat()
        for index in range(253)
    )
    stale = _series(
        tuple("100" for _ in stale_dates),
        dates=stale_dates,
        identity="ADMITTED_STALE",
        mode=PhaseF2ExecutionMode.ADMITTED_EVIDENCE,
    )
    result = _result(_compute(policy, stale, "TOTAL_RETURN"), "TOTAL_RETURN")
    assert result.status is PhaseF2ComputationStatus.INSUFFICIENT_DATA


@pytest.mark.skipif(not DATABASE.exists(), reason="installed Phase E database is local evidence")
def test_phase_e_adapter_is_read_only_and_price_nav_remains_blocked(
    policy: PhaseF1PortfolioMetricsPolicy,
) -> None:
    before = sha256(DATABASE.read_bytes()).hexdigest()
    series = load_admitted_phase_e_nav_series(
        DATABASE,
        exact_isin="AT0000673322",
        repository_root=ROOT,
        phase_e_index_path=ROOT / "data/raw/nav/erste_market/phase-e-index.json",
    )
    run = _compute(policy, series, "TOTAL_RETURN", "ANNUALIZED_VOLATILITY")
    assert len(series.observations) == 249
    assert series.source_governance == "APPROVED_DISTRIBUTOR_NON_AUTHORITATIVE"
    assert {item.status for item in run.results} == {
        PhaseF2ComputationStatus.SEMANTICS_NOT_APPROVED
    }
    assert all(item.value is None for item in run.results)
    assert sha256(DATABASE.read_bytes()).hexdigest() == before


@pytest.mark.skipif(not DATABASE.exists(), reason="installed Phase E database is local evidence")
def test_phase_e_adapter_rejects_partial_schema(tmp_path: Path) -> None:
    damaged = tmp_path / "damaged-phase-e.sqlite"
    shutil.copyfile(DATABASE, damaged)
    with sqlite3.connect(damaged) as connection:
        connection.execute("DROP TABLE nav_import_manifest")
    with pytest.raises(PhaseEReadError, match="validation failed"):
        load_admitted_phase_e_nav_series(
            damaged,
            exact_isin="AT0000673322",
            repository_root=ROOT,
            phase_e_index_path=ROOT / "data/raw/nav/erste_market/phase-e-index.json",
        )


def test_float_input_cannot_cross_decimal_boundary(policy: PhaseF1PortfolioMetricsPolicy) -> None:
    observation = GovernedObservation(
        observation_date="2026-01-01",
        value=cast(Decimal, 100.0),
        evidence_reference="SYNTHETIC_FIXTURE:FLOAT:0",
        observation_fingerprint="a" * 64,
    )
    series = replace(_series(("100", "101")), observations=(observation,), provenance_fingerprint="a" * 64)
    result = _result(_compute(policy, series, "TOTAL_RETURN"), "TOTAL_RETURN")
    assert result.status is PhaseF2ComputationStatus.INPUT_REJECTED
