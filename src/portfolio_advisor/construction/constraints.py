"""Strict per-instrument construction constraints from the reviewed policy."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from portfolio_advisor.audit.milestone_4 import is_valid_isin
from portfolio_advisor.objectives import CapitalDefensiveConstructionPolicy

from .models import (
    ConstructionReasonCode,
    RankedConstructionInstrument,
    ShortlistConstructionProvenance,
)


class ConstructionValidationError(RuntimeError):
    """Construction evidence is internally invalid or conflicts with policy."""


def policy_decimal(
    policy: CapitalDefensiveConstructionPolicy, section: str, field: str
) -> Decimal:
    """Read one exact governed decimal; no application fallback exists."""
    values = dict(getattr(policy, section))
    value = values[field]
    if not isinstance(value, str):
        raise ConstructionValidationError(f"policy {section}.{field} is not an exact decimal")
    return Decimal(value)


def policy_integer(
    policy: CapitalDefensiveConstructionPolicy, section: str, field: str
) -> int:
    """Read one governed integer with bool and coercion rejection."""
    value = dict(getattr(policy, section))[field]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConstructionValidationError(f"policy {section}.{field} is not an integer")
    return value


def validate_instrument_identity(
    item: RankedConstructionInstrument,
    provenance: ShortlistConstructionProvenance,
) -> None:
    """Require reviewed rank, canonical ISIN, and exact shortlist membership."""
    if not item.screening_eligible:
        raise ConstructionValidationError("construction input contains an ineligible instrument")
    if isinstance(item.rank, bool) or item.rank < 1:
        raise ConstructionValidationError("selected instrument rank must be positive")
    if not is_valid_isin(item.isin):
        raise ConstructionValidationError("construction input contains an invalid canonical ISIN")
    if item.instrument_id < 1 or item.shortlist_entry_id < 1:
        raise ConstructionValidationError("construction input lacks canonical database identity")
    if item.shortlist_snapshot_id != provenance.shortlist_snapshot_id:
        raise ConstructionValidationError("instrument is not from the exact source shortlist snapshot")
    if not item.source_occurrence_ids or len(set(item.source_occurrence_ids)) != len(
        item.source_occurrence_ids
    ):
        raise ConstructionValidationError("shortlist membership occurrence lineage is incomplete")
    if not item.currency or item.currency != item.currency.strip():
        raise ConstructionValidationError("instrument currency evidence is missing or invalid")


def nav_failure_codes(
    item: RankedConstructionInstrument,
    construction_date: date,
    policy: CapitalDefensiveConstructionPolicy,
) -> tuple[ConstructionReasonCode, ...]:
    """Evaluate exact NAV dates without interpolation, proxying, or substitution."""
    dates = item.nav.observation_dates
    failures: list[ConstructionReasonCode] = []
    if (
        item.nav.quality != "ADMITTED_AND_VALIDATED"
        or item.nav.interpolation_used
        or item.nav.nearest_date_substitution_used
        or item.nav.proxy_instrument_used
        or not dates
        or tuple(sorted(set(dates))) != dates
        or dates[-1] > construction_date
    ):
        return (ConstructionReasonCode.INSUFFICIENT_ADMITTED_NAV_COVERAGE,)
    history_days = (dates[-1] - dates[0]).days
    if history_days < policy_integer(
        policy, "historical_nav", "minimum_history_span_calendar_days"
    ):
        failures.append(ConstructionReasonCode.INSUFFICIENT_NAV_HISTORY)
    if len(dates) - 1 < policy_integer(
        policy, "historical_nav", "minimum_aligned_return_intervals"
    ):
        failures.append(ConstructionReasonCode.INSUFFICIENT_ALIGNED_RETURN_INTERVALS)
    if (construction_date - dates[-1]).days > policy_integer(
        policy, "historical_nav", "maximum_observation_staleness_calendar_days"
    ):
        failures.append(ConstructionReasonCode.STALE_NAV)
    return tuple(failures)
