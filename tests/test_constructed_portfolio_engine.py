"""Focused construction, identity, and deterministic feasibility tests for 11B."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from portfolio_advisor.construction import (
    ConstructionEvidenceReadiness,
    ConstructionReasonCode,
    ConstructionRuntimeStatus,
    construct_capital_defensive_portfolio,
)
from portfolio_advisor.objectives import (
    CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT,
    ConstructionPolicyValidationError,
    load_capital_defensive_construction_policy,
)

from .constructed_portfolio_fixtures import ISINS, SNAPSHOT_DATE, build_fixture

ROOT = Path(__file__).resolve().parents[1]
POLICY = load_capital_defensive_construction_policy(
    ROOT / CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT
)
READY = ConstructionEvidenceReadiness(True, True, True)


def _construct(fixture, *, amount: Decimal = Decimal("1000.01"), readiness=READY):  # type: ignore[no-untyped-def]
    return construct_capital_defensive_portfolio(
        screening=fixture.screening,
        cash_by_currency={"EUR": amount},
        policy=POLICY,
        instruments=fixture.instruments,
        readiness=readiness,
    )


def test_valid_synthetic_construction_reconciles_exact_80_20_and_is_stable(
    tmp_path: Path,
) -> None:
    fixture = build_fixture(tmp_path)
    first = _construct(fixture)
    second = _construct(fixture, amount=Decimal("999999999.99"))
    assert first.status is ConstructionRuntimeStatus.CONSTRUCTED_VALIDATED
    assert first.reason_codes == ()
    assert first.candidate is not None and second.candidate is not None
    assert len(first.candidate.holdings) == 8
    assert {holding.weight for holding in first.candidate.holdings} == {Decimal("0.10")}
    assert sum((holding.weight for holding in first.candidate.holdings), Decimal(0)) == Decimal(
        "0.80"
    )
    assert first.candidate.cash_weight == Decimal("0.20")
    assert first.candidate.candidate_fingerprint == second.candidate.candidate_fingerprint
    assert "1000.01" not in str(first.candidate.to_dict())
    assert "999999999.99" not in str(second.candidate.to_dict())


def test_same_currency_is_enforced_without_fx_or_substitution(tmp_path: Path) -> None:
    fixture = build_fixture(
        tmp_path,
        currencies=tuple("EUR" if index < 8 else "USD" for index in range(10)),
    )
    result = _construct(fixture)
    assert result.candidate is not None
    assert [holding.isin for holding in result.candidate.holdings] == list(ISINS[:8])
    assert {holding.currency for holding in result.candidate.holdings} == {"EUR"}


def test_insufficient_same_currency_and_unsupported_cash_are_unavailable_or_rejected(
    tmp_path: Path,
) -> None:
    fixture = build_fixture(tmp_path, count=7)
    result = _construct(fixture)
    assert result.status is ConstructionRuntimeStatus.UNAVAILABLE
    assert ConstructionReasonCode.INSUFFICIENT_SAME_CURRENCY_INSTRUMENTS in result.reason_codes
    with pytest.raises(ConstructionPolicyValidationError, match="unsupported"):
        construct_capital_defensive_portfolio(
            screening=fixture.screening,
            cash_by_currency={"GBP": Decimal(1)},
            policy=POLICY,
            instruments=fixture.instruments,
            readiness=READY,
        )


@pytest.mark.parametrize("conflicting", [False, True])
def test_missing_or_conflicting_category_evidence_is_rejected(
    tmp_path: Path, conflicting: bool
) -> None:
    groups: list[tuple[str | None, str | None]] = [
        (("BOND", "GOV"), ("BOND", "CORP"), ("CASHLIKE", "MONEY"))[index % 3]
        for index in range(10)
    ]
    if not conflicting:
        groups[0] = (None, "GOV")
    conflicts = tuple(index == 0 and conflicting for index in range(10))
    fixture = build_fixture(
        tmp_path,
        groups=tuple(groups),
        category_conflicts=conflicts,
    )
    result = _construct(fixture)
    assert result.status is ConstructionRuntimeStatus.REJECTED
    assert result.reason_codes == (ConstructionReasonCode.INVALID_CATEGORY_EVIDENCE,)


def test_fewer_than_three_groups_or_unavoidable_group_cap_has_no_feasible_set(
    tmp_path: Path,
) -> None:
    two_groups = tuple(("BOND", "A" if index < 5 else "B") for index in range(10))
    result = _construct(build_fixture(tmp_path, groups=two_groups))
    assert result.status is ConstructionRuntimeStatus.UNAVAILABLE
    assert result.reason_codes == (ConstructionReasonCode.NO_FEASIBLE_DIVERSIFIED_SET,)


def test_top_eight_infeasible_selects_lexicographically_earliest_rank_vector(
    tmp_path: Path,
) -> None:
    groups = (
        ("BOND", "A"),
        ("BOND", "A"),
        ("BOND", "A"),
        ("BOND", "A"),
        ("BOND", "A"),
        ("BOND", "B"),
        ("BOND", "B"),
        ("BOND", "C"),
        ("BOND", "C"),
        ("BOND", "B"),
    )
    result = _construct(build_fixture(tmp_path, groups=groups))
    assert result.candidate is not None
    assert [holding.rank for holding in result.candidate.holdings] == [1, 2, 3, 4, 6, 7, 8, 9]


def test_exact_rank_tie_uses_lexicographic_isin_tuple(tmp_path: Path) -> None:
    fixture = build_fixture(tmp_path, ranks=tuple(1 for _ in range(10)))
    reversed_fixture = replace(
        fixture,
        instruments=tuple(reversed(fixture.instruments)),
        screening=replace(
            fixture.screening,
            candidates=tuple(reversed(fixture.screening.candidates)),
            constructed=tuple(reversed(fixture.screening.constructed)),
        ),
    )
    result = _construct(reversed_fixture)
    assert result.candidate is not None
    assert [holding.isin for holding in result.candidate.holdings] == sorted(ISINS[:8])


def test_stale_history_interval_proxy_and_common_window_fail_closed(tmp_path: Path) -> None:
    full = tuple(
        SNAPSHOT_DATE - timedelta(days=365 - index) for index in range(366)
    )
    stale = tuple(value - timedelta(days=31) for value in full)
    stale_result = _construct(build_fixture(tmp_path / "stale", nav_dates=tuple(stale for _ in range(10))))
    assert ConstructionReasonCode.STALE_NAV in stale_result.reason_codes

    history = full[1:]
    history_result = _construct(
        build_fixture(tmp_path / "history", nav_dates=tuple(history for _ in range(10)))
    )
    assert ConstructionReasonCode.INSUFFICIENT_NAV_HISTORY in history_result.reason_codes

    intervals = tuple(full[round(index * 365 / 251)] for index in range(252))
    interval_result = _construct(
        build_fixture(tmp_path / "intervals", nav_dates=tuple(intervals for _ in range(10)))
    )
    assert ConstructionReasonCode.INSUFFICIENT_ALIGNED_RETURN_INTERVALS in (
        interval_result.reason_codes
    )

    proxy_fixture = build_fixture(
        tmp_path / "proxy",
        proxy_flags=(True,) + tuple(False for _ in range(9)),
    )
    proxy_result = _construct(proxy_fixture)
    assert proxy_result.candidate is not None
    assert ISINS[0] not in {holding.isin for holding in proxy_result.candidate.holdings}

    first_dates = tuple(value for index, value in enumerate(full) if index % 5 != 1)
    second_dates = tuple(value for index, value in enumerate(full) if index % 5 != 2)
    common_fixture = build_fixture(
        tmp_path / "common",
        nav_dates=tuple(first_dates if index < 5 else second_dates for index in range(10)),
    )
    common_result = _construct(common_fixture)
    assert common_result.status is ConstructionRuntimeStatus.UNAVAILABLE
    assert common_result.reason_codes == (
        ConstructionReasonCode.NO_COMMON_ALIGNED_RETURN_WINDOW,
    )


def test_missing_benchmark_and_metrics_return_implemented_blocked_by_data(
    tmp_path: Path,
) -> None:
    fixture = build_fixture(tmp_path)
    result = _construct(fixture, readiness=ConstructionEvidenceReadiness(False, False, False))
    assert result.status is ConstructionRuntimeStatus.IMPLEMENTED_BLOCKED_BY_DATA
    assert ConstructionReasonCode.MISSING_OFFICIAL_REFERENCE_RATE_EVIDENCE in result.reason_codes
    assert ConstructionReasonCode.UNAVAILABLE_PORTFOLIO_RISK_METRICS in result.reason_codes
    assert result.candidate is None
