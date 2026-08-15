from __future__ import annotations

import socket
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from portfolio_advisor.history import mnb_otc
from portfolio_advisor.history.sparse_trading_semantics import (
    NO_EXACT_ISIN_OBSERVATION,
    NO_REPORTED_KELER_OTC_ACTIVITY,
    OBSERVED_OTC_ACTIVITY,
    STRUCTURAL_UNCERTAINTY,
    ReportPeriodEvidence,
    ReportScopeEvidence,
    SparseTradingSemanticsError,
    build_sparse_trading_semantics,
    classify_report,
    methodology_assessment,
)

FIXTURE = Path(__file__).parent / "fixtures" / "mnb_otc_20241129_extract.txt"


def observation() -> mnb_otc.MnbOtcObservation:
    return mnb_otc.parse_mnb_otc_report_text(
        FIXTURE.read_text(encoding="utf-8"), "report.pdf", "a" * 64
    )


def report(
    start: date,
    end: date,
    *,
    present: bool,
    status: str | None = None,
) -> ReportPeriodEvidence:
    return ReportPeriodEvidence(
        filename=f"OTC_{start.isoformat()}.pdf",
        sha256=f"{start.day:02x}" * 32,
        period_start=start,
        period_end=end,
        report_status=status
        or (
            "REPORT_ACQUIRED_ISIN_PRESENT" if present else "REPORT_ACQUIRED_ISIN_ABSENT"
        ),
        parser_status="PARSED" if present else "NOT_APPLICABLE_EXACT_ISIN_ABSENT",
        contains_exact_isin=present,
        source_authority="MNB public publication infrastructure / KELER publication",
    )


def incomplete_scope() -> ReportScopeEvidence:
    return ReportScopeEvidence(
        authoritative=True,
        scope_statement="KELER OTC turnover summary",
        transaction_count_column_present=True,
        complete_period_absence_policy_validated=False,
        absence_policy_reason="No retained completeness rule.",
    )


def test_report_semantic_classification_fails_closed_for_absence() -> None:
    absent = report(date(2024, 9, 2), date(2024, 9, 8), present=False)

    assert (
        classify_report(
            report(date(2024, 8, 26), date(2024, 9, 1), present=True),
            incomplete_scope(),
        )
        == OBSERVED_OTC_ACTIVITY
    )
    assert classify_report(absent, incomplete_scope()) == NO_EXACT_ISIN_OBSERVATION
    assert (
        classify_report(
            absent,
            replace(incomplete_scope(), complete_period_absence_policy_validated=True),
        )
        == NO_REPORTED_KELER_OTC_ACTIVITY
    )
    assert (
        classify_report(
            replace(absent, report_status="REPORT_PARSE_FAILED"), incomplete_scope()
        )
        == STRUCTURAL_UNCERTAINTY
    )


def test_sparse_metrics_are_decimal_safe_and_never_create_prices() -> None:
    first = replace(
        observation(), period_start=date(2024, 8, 26), period_end=date(2024, 9, 1)
    )
    second = replace(
        first,
        period_start=date(2024, 10, 28),
        period_end=date(2024, 11, 3),
        minimum_price=Decimal("102.400000"),
        average_price=Decimal("102.400000"),
        maximum_price=Decimal("102.400000"),
        transaction_count=4,
    )
    reports = (
        report(first.period_start, first.period_end, present=True),
        report(date(2024, 9, 2), date(2024, 9, 8), present=False),
        report(date(2024, 9, 9), date(2024, 9, 15), present=False),
        report(second.period_start, second.period_end, present=True),
        report(date(2024, 11, 4), date(2024, 11, 10), present=False),
    )

    result = build_sparse_trading_semantics(
        reports,
        (first, second),
        scope=incomplete_scope(),
        required_start=date(2024, 7, 2),
        maturity_date=date(2025, 6, 4),
    )

    assert result["semantic_status_counts"] == {
        "NO_EXACT_ISIN_OBSERVATION": 3,
        "OBSERVED_OTC_ACTIVITY": 2,
    }
    assert result["consecutive_absence_runs"] == [2, 1]
    assert result["maximum_consecutive_absent_report_run"] == 2
    assert result["positive_observation_gaps"] == [
        {
            "from_period_end": "2024-09-01",
            "to_period_start": "2024-10-28",
            "calendar_days_between": 57,
            "unobserved_calendar_days": 56,
            "acquired_reports_before_next_positive_observation": 2,
        }
    ]
    diagnostics = result["stale_price_diagnostics"]
    assert isinstance(diagnostics, list)
    assert diagnostics[0]["average_price_spread"] == "0.000003"
    assert diagnostics[0]["normalized_average_price_spread"] != "0"
    assert diagnostics[1]["acquired_reports_before_next_positive_observation"] is None
    assert result["transaction_count_statistics"] == {
        "minimum": 3,
        "maximum": 4,
        "median": "3.5",
        "total": 7,
    }
    report_rows = result["report_rows"]
    assert isinstance(report_rows, list)
    assert all(
        isinstance(row, dict) and row["synthetic_price_created"] is False
        for row in report_rows
    )


def test_source_provenance_and_period_alignment_are_required() -> None:
    with pytest.raises(SparseTradingSemanticsError, match="Authoritative"):
        ReportScopeEvidence(
            authoritative=False,
            scope_statement="fixture wording",
            transaction_count_column_present=True,
            complete_period_absence_policy_validated=False,
            absence_policy_reason="A fixture cannot establish production semantics.",
        )
    with pytest.raises(SparseTradingSemanticsError, match="provenance"):
        ReportPeriodEvidence(
            filename="report.pdf",
            sha256="a" * 64,
            period_start=date(2024, 1, 1),
            period_end=date(2024, 1, 1),
            report_status="REPORT_ACQUIRED_ISIN_ABSENT",
            parser_status="PARSED",
            contains_exact_isin=False,
            source_authority="",
        )
    with pytest.raises(
        SparseTradingSemanticsError, match="no acquired report provenance"
    ):
        build_sparse_trading_semantics(
            (report(date(2024, 8, 26), date(2024, 9, 1), present=False),),
            (observation(),),
            scope=incomplete_scope(),
            required_start=date(2024, 7, 2),
            maturity_date=date(2025, 6, 4),
        )


def test_methodology_guardrails_are_unapproved_and_network_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network")),
    )

    assessment = methodology_assessment()

    assert assessment["forward_fill_methodologically_approved"] is False
    assert assessment["interpolation_methodologically_approved"] is False
    assert assessment["nearest_date_boundary_methodologically_approved"] is False
    assert assessment["daily_return_series_supported"] is False
    assert assessment["daily_volatility_supported"] is False
    assert assessment["sharpe_supported"] is False
    assert assessment["maximum_drawdown_supported"] is False
    assert assessment["var_cvar_supported"] is False
    assert assessment["point_to_point_descriptive_price_change_supported"] is True
