"""Audit-only interpretation of sparse MNB/KELER OTC report observations.

The module deliberately measures what the retained reports say and preserves
unknowns.  It does not manufacture prices, returns, zero trades, or a daily
series from periods that lack an exact ISIN row.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from statistics import median

from .mnb_otc import MnbOtcObservation, decimal_text

OBSERVED_OTC_ACTIVITY = "OBSERVED_OTC_ACTIVITY"
NO_EXACT_ISIN_OBSERVATION = "NO_EXACT_ISIN_OBSERVATION"
NO_REPORTED_KELER_OTC_ACTIVITY = "NO_REPORTED_KELER_OTC_ACTIVITY"
REPORT_SCOPE_INSUFFICIENT = "REPORT_SCOPE_INSUFFICIENT"
STRUCTURAL_UNCERTAINTY = "STRUCTURAL_UNCERTAINTY"
REPORT_CONFLICT = "REPORT_CONFLICT"
TARGET_ISIN = "HU0000554795"


class SparseTradingSemanticsError(RuntimeError):
    """Sparse OTC report evidence is malformed or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class ReportPeriodEvidence:
    """One acquired official report, regardless of target-row presence."""

    filename: str
    sha256: str
    period_start: date
    period_end: date
    report_status: str
    parser_status: str
    contains_exact_isin: bool
    source_authority: str

    def __post_init__(self) -> None:
        if not self.filename or len(self.sha256) != 64:
            raise SparseTradingSemanticsError(
                "Report filename and SHA-256 are required"
            )
        if self.period_end < self.period_start:
            raise SparseTradingSemanticsError("Report period is reversed")
        if not self.source_authority:
            raise SparseTradingSemanticsError(
                "Authoritative source provenance is required"
            )


@dataclass(frozen=True, slots=True)
class ReportScopeEvidence:
    """What authoritative report wording does—and does not—establish."""

    authoritative: bool
    scope_statement: str
    transaction_count_column_present: bool
    complete_period_absence_policy_validated: bool
    absence_policy_reason: str

    def __post_init__(self) -> None:
        if not self.authoritative or not self.scope_statement:
            raise SparseTradingSemanticsError(
                "Authoritative report scope evidence is required"
            )


def classify_report(report: ReportPeriodEvidence, scope: ReportScopeEvidence) -> str:
    """Classify a report without treating an absent row as a zero-price row."""
    if report.report_status == "REPORT_PARSE_FAILED":
        return STRUCTURAL_UNCERTAINTY
    if report.report_status not in {
        "REPORT_ACQUIRED_ISIN_PRESENT",
        "REPORT_ACQUIRED_ISIN_ABSENT",
    }:
        return REPORT_SCOPE_INSUFFICIENT
    if report.contains_exact_isin:
        if report.report_status != "REPORT_ACQUIRED_ISIN_PRESENT":
            return REPORT_CONFLICT
        return OBSERVED_OTC_ACTIVITY
    if report.report_status != "REPORT_ACQUIRED_ISIN_ABSENT":
        return REPORT_CONFLICT
    if scope.complete_period_absence_policy_validated:
        return NO_REPORTED_KELER_OTC_ACTIVITY
    return NO_EXACT_ISIN_OBSERVATION


def _as_period(value: object, field: str) -> date:
    if not isinstance(value, str):
        raise SparseTradingSemanticsError(f"{field} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SparseTradingSemanticsError(f"{field} must be an ISO date") from exc


def report_from_manifest(
    record: object, *, source_authority: str
) -> ReportPeriodEvidence:
    """Convert one strictly validated local-inventory record into audit input."""
    if not isinstance(record, dict):
        raise SparseTradingSemanticsError("Inventory report record must be an object")
    period = record.get("reporting_period")
    if not isinstance(period, dict):
        raise SparseTradingSemanticsError("Inventory report has no reporting period")
    required = (
        "filename",
        "sha256",
        "report_status",
        "parser_status",
        "contains_exact_isin",
    )
    if any(field not in record for field in required):
        raise SparseTradingSemanticsError("Inventory report record is incomplete")
    if not isinstance(record["filename"], str) or not isinstance(record["sha256"], str):
        raise SparseTradingSemanticsError("Inventory report identity is malformed")
    if not isinstance(record["report_status"], str) or not isinstance(
        record["parser_status"], str
    ):
        raise SparseTradingSemanticsError("Inventory report status is malformed")
    if not isinstance(record["contains_exact_isin"], bool):
        raise SparseTradingSemanticsError("Inventory exact-ISIN flag is malformed")
    return ReportPeriodEvidence(
        filename=record["filename"],
        sha256=record["sha256"],
        period_start=_as_period(period.get("start"), "reporting_period.start"),
        period_end=_as_period(period.get("end"), "reporting_period.end"),
        report_status=record["report_status"],
        parser_status=record["parser_status"],
        contains_exact_isin=record["contains_exact_isin"],
        source_authority=source_authority,
    )


def _median_decimal(values: Sequence[Decimal]) -> str | None:
    return decimal_text(median(values)) if values else None


def _consecutive_absence_runs(statuses: Sequence[str]) -> tuple[list[int], int]:
    runs: list[int] = []
    current = 0
    for status in statuses:
        if status == OBSERVED_OTC_ACTIVITY:
            if current:
                runs.append(current)
                current = 0
        else:
            current += 1
    if current:
        runs.append(current)
    return runs, max(runs, default=0)


def build_sparse_trading_semantics(
    reports: Sequence[ReportPeriodEvidence],
    observations: Sequence[MnbOtcObservation],
    *,
    scope: ReportScopeEvidence,
    required_start: date,
    maturity_date: date,
) -> dict[str, object]:
    """Build deterministic audit data while keeping all price-method choices false."""
    if required_start > maturity_date:
        raise SparseTradingSemanticsError("Required start cannot follow maturity")
    ordered_reports = sorted(
        reports, key=lambda item: (item.period_start, item.period_end, item.filename)
    )
    if len(
        {
            (item.period_start, item.period_end, item.filename)
            for item in ordered_reports
        }
    ) != len(ordered_reports):
        raise SparseTradingSemanticsError(
            "Duplicate report identity in sparse-trading input"
        )
    ordered_observations = sorted(
        observations, key=lambda item: (item.period_start, item.period_end)
    )
    if any(item.isin != TARGET_ISIN for item in ordered_observations):
        raise SparseTradingSemanticsError(
            "Sparse-trading observations have an unexpected ISIN"
        )
    observation_by_period = {
        (item.period_start, item.period_end): item for item in ordered_observations
    }
    if len(observation_by_period) != len(ordered_observations):
        raise SparseTradingSemanticsError("Duplicate observation reporting period")

    rows: list[dict[str, object]] = []
    classifications: list[str] = []
    for report in ordered_reports:
        classification = classify_report(report, scope)
        classifications.append(classification)
        observation = observation_by_period.get(
            (report.period_start, report.period_end)
        )
        if classification == OBSERVED_OTC_ACTIVITY and observation is None:
            raise SparseTradingSemanticsError(
                "Positive report has no exact persisted observation"
            )
        if classification != OBSERVED_OTC_ACTIVITY and observation is not None:
            raise SparseTradingSemanticsError(
                "Non-positive report has a persisted target observation"
            )
        rows.append(
            {
                "filename": report.filename,
                "source_document_hash": report.sha256,
                "reporting_period": {
                    "start": report.period_start.isoformat(),
                    "end": report.period_end.isoformat(),
                },
                "semantic_status": classification,
                "trading_activity_observed": (
                    True if classification == OBSERVED_OTC_ACTIVITY else None
                ),
                "price_observation_available": classification == OBSERVED_OTC_ACTIVITY,
                "synthetic_price_created": False,
                "average_price": decimal_text(observation.average_price)
                if observation
                else None,
                "transaction_count": observation.transaction_count
                if observation
                else None,
            }
        )
    report_periods = {(item.period_start, item.period_end) for item in ordered_reports}
    if not set(observation_by_period).issubset(report_periods):
        raise SparseTradingSemanticsError(
            "Persisted observation has no acquired report provenance"
        )

    gaps: list[dict[str, object]] = []
    stale_diagnostics: list[dict[str, object]] = []
    for index, observation in enumerate(ordered_observations):
        next_observation = (
            ordered_observations[index + 1]
            if index + 1 < len(ordered_observations)
            else None
        )
        spread = observation.maximum_price - observation.minimum_price
        normalized_spread = spread / observation.average_price
        following_reports = [
            report
            for report in ordered_reports
            if report.period_start > observation.period_end
            and (
                next_observation is None
                or report.period_start < next_observation.period_start
            )
        ]
        stale_diagnostics.append(
            {
                "reporting_period": {
                    "start": observation.period_start.isoformat(),
                    "end": observation.period_end.isoformat(),
                },
                "transaction_count": observation.transaction_count,
                "minimum_price": decimal_text(observation.minimum_price),
                "average_price": decimal_text(observation.average_price),
                "maximum_price": decimal_text(observation.maximum_price),
                "average_price_spread": decimal_text(spread),
                "normalized_average_price_spread": decimal_text(normalized_spread),
                "days_until_next_positive_observation": (
                    (next_observation.period_start - observation.period_end).days
                    if next_observation
                    else None
                ),
                "acquired_reports_before_next_positive_observation": (
                    len(following_reports) if next_observation else None
                ),
                "acquired_reports_after_last_positive_observation": (
                    len(following_reports) if next_observation is None else None
                ),
            }
        )
        if next_observation:
            gaps.append(
                {
                    "from_period_end": observation.period_end.isoformat(),
                    "to_period_start": next_observation.period_start.isoformat(),
                    "calendar_days_between": (
                        next_observation.period_start - observation.period_end
                    ).days,
                    "unobserved_calendar_days": (
                        next_observation.period_start - observation.period_end
                    ).days
                    - 1,
                    "acquired_reports_before_next_positive_observation": len(
                        following_reports
                    ),
                }
            )
    absence_runs, maximum_absence_run = _consecutive_absence_runs(classifications)
    count_by_status = dict(sorted(Counter(classifications).items()))
    positive_count = len(ordered_observations)
    first_positive = ordered_observations[0] if ordered_observations else None
    last_positive = ordered_observations[-1] if ordered_observations else None
    return {
        "report_count": len(ordered_reports),
        "positive_observation_count": positive_count,
        "absent_report_count": sum(
            status in {NO_EXACT_ISIN_OBSERVATION, NO_REPORTED_KELER_OTC_ACTIVITY}
            for status in classifications
        ),
        "semantic_status_counts": count_by_status,
        "report_rows": rows,
        "trading_incidence": {
            "numerator_positive_reports": positive_count,
            "denominator_acquired_reports": len(ordered_reports),
            "ratio": decimal_text(
                Decimal(positive_count) / Decimal(len(ordered_reports))
            )
            if ordered_reports
            else None,
            "percentage": decimal_text(
                Decimal(100 * positive_count) / Decimal(len(ordered_reports))
            )
            if ordered_reports
            else None,
        },
        "first_positive_period": (
            {
                "start": first_positive.period_start.isoformat(),
                "end": first_positive.period_end.isoformat(),
            }
            if first_positive
            else None
        ),
        "last_positive_period": (
            {
                "start": last_positive.period_start.isoformat(),
                "end": last_positive.period_end.isoformat(),
            }
            if last_positive
            else None
        ),
        "days_from_required_start_to_first_positive": (
            (first_positive.period_start - required_start).days
            if first_positive
            else None
        ),
        "days_from_last_positive_to_maturity": (
            (maturity_date - last_positive.period_end).days if last_positive else None
        ),
        "positive_observation_gaps": gaps,
        "consecutive_absence_runs": absence_runs,
        "maximum_consecutive_absent_report_run": maximum_absence_run,
        "transaction_count_statistics": {
            "minimum": min(
                (item.transaction_count for item in ordered_observations), default=None
            ),
            "maximum": max(
                (item.transaction_count for item in ordered_observations), default=None
            ),
            "median": _median_decimal(
                [Decimal(item.transaction_count) for item in ordered_observations]
            ),
            "total": sum(item.transaction_count for item in ordered_observations),
        },
        "stale_price_diagnostics": stale_diagnostics,
    }


def methodology_assessment() -> dict[str, object]:
    """Explicitly preserve non-approval; these are research conclusions only."""
    return {
        "forward_fill_methodologically_approved": False,
        "forward_fill_rationale": (
            "No authoritative valuation/carry-forward rule is retained; coupon accrual, time to "
            "maturity, and clean/dirty quotation semantics remain unresolved."
        ),
        "interpolation_methodologically_approved": False,
        "interpolation_rationale": (
            "Irregular weekly transaction aggregates and unknown quotation convention do not establish "
            "a valid path between observations."
        ),
        "nearest_date_boundary_methodologically_approved": False,
        "nearest_date_boundary_rationale": (
            "An aggregate transaction price is not an exact start, end, or maturity boundary price."
        ),
        "point_to_point_descriptive_price_change_supported": True,
        "point_to_point_rationale": (
            "Actual reported average OTC prices can be compared descriptively only; this is not an "
            "approved economic return calculation."
        ),
        "daily_return_series_supported": False,
        "daily_volatility_supported": False,
        "sharpe_supported": False,
        "maximum_drawdown_supported": False,
        "var_cvar_supported": False,
        "no_daily_resampling": True,
        "no_return_metric_implementation": True,
    }
