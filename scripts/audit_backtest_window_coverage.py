"""Audit real walk-forward windows against existing historical NAV diagnostics.

This script is read-only with respect to provider and portfolio data.  It
derives the same evaluation dates and fixed horizons used by
``WalkForwardBacktester`` and evaluates every portfolio's actual holdings at
each date against the already-generated historical-data diagnostic artifact.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import TextIO, TypedDict

from portfolio_advisor.database.repository import (
    ModelPortfolioRepository,
    RepositoryError,
)
from portfolio_advisor.history.models import SUPPORTED_HORIZON_DAYS, ForwardWindow

COMPLETE = "COMPLETE"
MISSING_START = "MISSING_START"
MISSING_END = "MISSING_END"
MISSING_BOTH = "MISSING_BOTH"
UNUSABLE_SOURCE = "UNUSABLE_SOURCE"
RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
WRONG_INSTRUMENT_TYPE = "WRONG_INSTRUMENT_TYPE"
VALID_STATUSES = frozenset(
    {
        COMPLETE,
        MISSING_START,
        MISSING_END,
        MISSING_BOTH,
        UNUSABLE_SOURCE,
        RECONCILIATION_REQUIRED,
        WRONG_INSTRUMENT_TYPE,
    }
)
USABLE_ERSTE_STATUSES = frozenset({"PASS", "PASS_WITH_FILTERED_SENTINEL"})
USABLE_OEKB_STATUS = "VALIDATED_FALLBACK_HISTORY"
USABLE_MORNINGSTAR_STATUS = "VERIFIED_FOR_RANGE"
MORNINGSTAR_VERIFIED_EVIDENCE = {
    "LU2180923653": {
        "morningstar_id": "F0000159Z8",
        "fund_name": "Schroder International Selection Fund Emerging Markets Value",
        "currency": "USD",
        "first_date": date(2020, 9, 30),
        "last_date": date(2026, 3, 27),
        "observations": 1372,
    },
    "AT0000673314": {
        "morningstar_id": "F0GBR05S95",
        "fund_name": "ERSTE Stock Global EUR R01 VT",
        "currency": "EUR",
        "first_date": date(2024, 7, 2),
        "last_date": date(2026, 3, 18),
        "observations": 424,
    },
    "AT0000627484": {
        "morningstar_id": "F00000065K",
        "fund_name": "ERSTE Stock Global VT HUF",
        "currency": "HUF",
        "first_date": date(2024, 7, 2),
        "last_date": date(2026, 3, 18),
        "observations": 424,
    },
}


class CorporateActionLineageEvidence(TypedDict):
    predecessor_isin: str
    predecessor_fund_name: str
    predecessor_source: str
    predecessor_first_date: date
    predecessor_last_date: date
    predecessor_observations: int
    predecessor_final_nav: float
    successor_isin: str
    successor_fund_name: str
    successor_source: str
    successor_morningstar_id: str
    successor_first_date: date
    successor_last_date: date
    successor_observations: int
    successor_first_nav: float
    successor_last_nav: float
    currency: str
    transition_date: date


CORPORATE_ACTION_LINEAGE_EVIDENCE: CorporateActionLineageEvidence = {
    "predecessor_isin": "AT0000A2VH41",
    "predecessor_fund_name": "ERSTE STOCK GLOBAL USD R01 VTIA",
    "predecessor_source": "oekb",
    "predecessor_first_date": date(2024, 7, 2),
    "predecessor_last_date": date(2025, 10, 16),
    "predecessor_observations": 323,
    "predecessor_final_nav": 147.91,
    "successor_isin": "AT0000A3P9Z2",
    "successor_fund_name": "ERSTE STOCK WORLD USD R01",
    "successor_source": "morningstar",
    "successor_morningstar_id": "F000025UZR",
    "successor_first_date": date(2025, 10, 17),
    "successor_last_date": date(2026, 8, 14),
    "successor_observations": 203,
    "successor_first_nav": 116.77,
    "successor_last_nav": 130.16,
    "currency": "USD",
    "transition_date": date(2025, 10, 17),
}
MISSING_ISIN = "<MISSING_ISIN>"


class CoverageAuditError(RuntimeError):
    """Raised when required, existing audit inputs cannot be read safely."""


@dataclass(frozen=True, slots=True)
class SourceAvailability:
    """Validated date availability for one existing source artifact."""

    source_name: str
    source_status: str
    first_date: date | None
    last_date: date | None
    validated_usable: bool = False
    eligible_for_fallback_coverage: bool = False

    def covers_window(self, window: ForwardWindow) -> bool:
        return (
            self.first_date is not None
            and self.last_date is not None
            and self.first_date <= window.evaluation_date
            and self.last_date >= window.end_date
        )


@dataclass(frozen=True, slots=True)
class MnbOtcAvailability:
    """Weekly OTC evidence metadata; deliberately excluded from NAV fallback selection."""

    first_period_start: date
    last_period_end: date
    observation_count: int
    price_type: str
    frequency: str

    def as_audit_evidence(self) -> dict[str, object]:
        return {
            "source_evidence_available": True,
            "boundary_price_available": False,
            "frequency_sufficient": False,
            "backtest_return_series_approved": False,
            "nav_equivalent": False,
            "first_period_start": self.first_period_start.isoformat(),
            "last_period_end": self.last_period_end.isoformat(),
            "observation_count": self.observation_count,
            "price_type": self.price_type,
            "frequency": self.frequency,
        }


@dataclass(frozen=True, slots=True)
class CorporateActionLineage:
    """One explicitly reviewed, coverage-only corporate-action lineage."""

    predecessor_isin: str
    successor_isin: str
    predecessor: SourceAvailability
    successor: SourceAvailability
    transition_date: date
    currency: str

    def coverage_for(self, window: ForwardWindow) -> str | None:
        """Classify one window without joining or transforming NAV observations."""
        if window.end_date < self.transition_date:
            return "predecessor_only" if self.predecessor.covers_window(window) else None
        if window.evaluation_date >= self.transition_date:
            return "successor_only" if self.successor.covers_window(window) else None
        if (
            self.predecessor.first_date is not None
            and self.predecessor.last_date is not None
            and self.successor.first_date is not None
            and self.successor.last_date is not None
            and self.predecessor.first_date <= window.evaluation_date
            and self.predecessor.last_date >= self.transition_date - timedelta(days=1)
            and self.successor.first_date <= self.transition_date
            and self.successor.last_date >= window.end_date
        ):
            return "corporate_action_lineage"
        return None

    def missing_boundaries(self, window: ForwardWindow) -> tuple[bool, bool]:
        """Return exact audit-boundary failures for this enumerated lineage only."""
        start_missing = window.evaluation_date < self.predecessor.first_date
        end_missing = window.end_date > self.successor.last_date
        if window.end_date < self.transition_date:
            end_missing = window.end_date > self.predecessor.last_date
        elif window.evaluation_date >= self.transition_date:
            start_missing = window.evaluation_date < self.successor.first_date
        return start_missing, end_missing

    def provenance(self, coverage_type: str) -> dict[str, object]:
        """Emit audit provenance; never imply NAV or return continuity."""
        return {
            "coverage_type": coverage_type,
            "predecessor": {"isin": self.predecessor_isin, "source": self.predecessor.source_name},
            "successor": {"isin": self.successor_isin, "source": self.successor.source_name},
            "transition_date": self.transition_date.isoformat(),
            "coverage_only": True,
            "nav_continuity": False,
            "return_continuity": "not_validated",
            "return_calculation_approved": False,
        }


@dataclass(frozen=True, slots=True)
class HistoricalAvailability:
    """Primary Erste metadata plus explicit, audit-only fallback evidence."""

    isin: str
    erste: SourceAvailability | None
    oekb: SourceAvailability | None = None
    morningstar: SourceAvailability | None = None
    corporate_action_lineage: CorporateActionLineage | None = None
    mnb_otc: MnbOtcAvailability | None = None

    @property
    def is_reconciliation_required(self) -> bool:
        return self.erste is not None and self.erste.source_status in {
            "CONFLICTING_HISTORY",
            RECONCILIATION_REQUIRED,
        }

    @property
    def is_wrong_instrument_type(self) -> bool:
        return self.erste is not None and self.erste.source_status == WRONG_INSTRUMENT_TYPE

    def usable_source_for(self, window: ForwardWindow) -> SourceAvailability | None:
        """Return one complete existing source; never combine source ranges."""
        if self.is_reconciliation_required or self.is_wrong_instrument_type:
            return None
        if (
            self.erste is not None
            and self.erste.source_status in USABLE_ERSTE_STATUSES
            and self.erste.validated_usable
            and self.erste.covers_window(window)
        ):
            return self.erste
        for source in self.fallback_sources():
            if source.covers_window(window):
                return source
        return None

    def fallback_sources(self) -> tuple[SourceAvailability, ...]:
        """Return independent sources that are explicitly audit-eligible."""
        if not self.fallback_is_permitted:
            return ()
        sources: list[SourceAvailability] = []
        if (
            self.oekb is not None
            and self.oekb.source_status == USABLE_OEKB_STATUS
            and self.oekb.validated_usable
            and self.oekb.eligible_for_fallback_coverage
        ):
            sources.append(self.oekb)
        if (
            self.morningstar is not None
            and self.morningstar.source_status == USABLE_MORNINGSTAR_STATUS
            and self.morningstar.validated_usable
            and self.morningstar.eligible_for_fallback_coverage
        ):
            sources.append(self.morningstar)
        return tuple(sources)

    @property
    def fallback_is_permitted(self) -> bool:
        return self.erste is None or self.erste.source_status in {
            "NO_ERSTE_MAPPING",
            "SECONDARY_SOURCE_REQUIRED",
        }

    def is_unusable_without_fallback(self) -> bool:
        return (
            self.erste is None
            or self.erste.source_status not in USABLE_ERSTE_STATUSES
            or not self.erste.validated_usable
        )


@dataclass(frozen=True, slots=True)
class WindowCoverage:
    observation_date: date
    horizon: int
    required_start: date
    required_end: date
    portfolio_name: str
    required_isins: tuple[str, ...]
    covered_isins: tuple[str, ...]
    missing_isins: tuple[str, ...]
    unusable_isins: tuple[str, ...]
    source_used_by_isin: dict[str, str]
    coverage_via_corporate_action: bool
    corporate_action_lineage_by_isin: dict[str, dict[str, object]]
    mnb_otc_evidence_by_isin: dict[str, dict[str, object]]
    coverage_ratio: float
    status: str
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "observation_date": self.observation_date.isoformat(),
            "horizon": self.horizon,
            "required_start": self.required_start.isoformat(),
            "required_end": self.required_end.isoformat(),
            "portfolio_name": self.portfolio_name,
            "required_isins": list(self.required_isins),
            "covered_isins": list(self.covered_isins),
            "missing_isins": list(self.missing_isins),
            "unusable_isins": list(self.unusable_isins),
            "source_used_by_isin": dict(sorted(self.source_used_by_isin.items())),
            "coverage_via_corporate_action": self.coverage_via_corporate_action,
            "corporate_action_lineage_by_isin": dict(
                sorted(self.corporate_action_lineage_by_isin.items())
            ),
            "mnb_otc_evidence_by_isin": dict(sorted(self.mnb_otc_evidence_by_isin.items())),
            "coverage_ratio": self.coverage_ratio,
            "status": self.status,
            "reasons": list(self.reasons),
        }


def parse_date(value: object, field: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value).replace("/", "-"))
    except (TypeError, ValueError) as exc:
        raise CoverageAuditError(f"{field} is not an ISO date: {value!r}") from exc


def _load_results(path: Path, label: str) -> list[dict[str, object]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CoverageAuditError(f"Cannot read {label} audit {path}: {exc}") from exc
    if label == "Morningstar fallback":
        _reject_secrets(payload)
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        raise CoverageAuditError(f"{label} audit has no results list")
    typed_results: list[dict[str, object]] = []
    for record in results:
        if not isinstance(record, dict):
            raise CoverageAuditError(f"{label} audit contains a non-object result")
        typed_results.append(record)
    return typed_results


def _reject_secrets(value: object) -> None:
    """Ensure an audit-only manifest cannot retain provider credentials."""
    forbidden_keys = frozenset({"authorization", "bearer_token", "cookie", "cookies", "session_id", "token"})
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).strip().lower() in forbidden_keys:
                raise CoverageAuditError("Audit manifest contains a forbidden secret field")
            _reject_secrets(child)
    elif isinstance(value, list):
        for child in value:
            _reject_secrets(child)
    elif isinstance(value, str) and "bearer " in value.lower():
        raise CoverageAuditError("Audit manifest contains a bearer credential")


def load_erste_availability(path: Path) -> dict[str, HistoricalAvailability]:
    """Read existing Erste diagnostics, without making an acquisition request."""
    results = _load_results(path, "Erste historical-data")

    availability: dict[str, HistoricalAvailability] = {}
    for record in results:
        isin = str(record.get("isin", "")).strip().upper()
        status = str(record.get("status", "")).strip().upper()
        if not isin or not status:
            raise CoverageAuditError("Erste historical-data audit result is missing isin or status")
        if isin in availability:
            raise CoverageAuditError(f"Erste historical-data audit contains duplicate ISIN {isin}")
        date_range = record.get("normalized_date_range", record.get("date_range"))
        if not isinstance(date_range, dict):
            raise CoverageAuditError(f"Erste historical-data audit {isin} has no date_range object")
        availability[isin] = HistoricalAvailability(
            isin=isin,
            erste=SourceAvailability(
                source_name="erste_market",
                source_status=status,
                first_date=parse_date(date_range.get("first"), f"{isin} date_range.first"),
                last_date=parse_date(date_range.get("last"), f"{isin} date_range.last"),
                validated_usable=record.get("usable_for_backtest") is True,
            ),
        )
    return availability


def merge_oekb_fallback_availability(
    availability: dict[str, HistoricalAvailability], path: Path
) -> dict[str, HistoricalAvailability]:
    """Attach only explicitly eligible, already-validated OeKB coverage evidence."""
    merged = dict(availability)
    for record in _load_results(path, "OeKB fallback"):
        isin = str(record.get("isin", "")).strip().upper()
        status = str(record.get("status", "")).strip().upper()
        if not isin or not status:
            raise CoverageAuditError("OeKB fallback audit result is missing isin or status")
        if isin not in merged:
            raise CoverageAuditError(
                f"OeKB fallback audit ISIN {isin} is absent from existing Erste diagnostics"
            )
        date_range = record.get("date_range")
        if not isinstance(date_range, dict):
            raise CoverageAuditError(f"OeKB fallback audit {isin} has no date_range object")
        oekb = SourceAvailability(
            source_name="oekb",
            source_status=status,
            first_date=parse_date(date_range.get("first"), f"{isin} OeKB date_range.first"),
            last_date=parse_date(date_range.get("last"), f"{isin} OeKB date_range.last"),
            validated_usable=status == USABLE_OEKB_STATUS,
            eligible_for_fallback_coverage=record.get("eligible_for_fallback_coverage") is True,
        )
        existing = merged[isin]
        if existing.oekb is not None:
            raise CoverageAuditError(f"OeKB fallback audit contains duplicate ISIN {isin}")
        merged[isin] = HistoricalAvailability(isin=isin, erste=existing.erste, oekb=oekb)
    return merged


def merge_morningstar_fallback_availability(
    availability: dict[str, HistoricalAvailability], path: Path
) -> dict[str, HistoricalAvailability]:
    """Attach only the explicitly verified Morningstar NAV ranges as audit evidence."""
    merged = dict(availability)
    for record in _load_results(path, "Morningstar fallback"):
        isin = str(record.get("isin", "")).strip().upper()
        source = str(record.get("source", "")).strip().lower()
        status = str(record.get("historical_nav_status", "")).strip().upper()
        identity_status = str(record.get("identity_status", "")).strip().upper()
        expected = MORNINGSTAR_VERIFIED_EVIDENCE.get(isin)
        if expected is None:
            raise CoverageAuditError("Morningstar fallback manifest contains an unexpected ISIN")
        if (
            source != "morningstar"
            or status != USABLE_MORNINGSTAR_STATUS
            or record.get("status") != USABLE_MORNINGSTAR_STATUS
        ):
            raise CoverageAuditError("Morningstar fallback manifest has an invalid source or status")
        if identity_status != "VERIFIED" or record.get("metric") != "nav":
            raise CoverageAuditError("Morningstar fallback manifest identity or metric is not verified")
        if (
            record.get("morningstar_id") != expected["morningstar_id"]
            or record.get("fund_name") != expected["fund_name"]
            or record.get("currency") != expected["currency"]
        ):
            raise CoverageAuditError("Morningstar fallback manifest identity metadata does not match")
        if record.get("frequency") != "daily" or record.get("observations") != expected["observations"]:
            raise CoverageAuditError("Morningstar fallback manifest frequency or observation count does not match")
        if record.get("non_positive_nav_count") != 0 or record.get("duplicate_date_count") != 0:
            raise CoverageAuditError("Morningstar fallback manifest NAV quality metadata is invalid")
        if isin not in merged:
            raise CoverageAuditError(
                "Morningstar fallback ISIN is absent from existing Erste diagnostics"
            )
        date_range = record.get("date_range")
        if not isinstance(date_range, dict):
            raise CoverageAuditError("Morningstar fallback manifest has no date_range object")
        first_date = parse_date(date_range.get("first"), f"{isin} Morningstar date_range.first")
        last_date = parse_date(date_range.get("last"), f"{isin} Morningstar date_range.last")
        if record.get("first_date") != date_range.get("first") or record.get("last_date") != date_range.get("last"):
            raise CoverageAuditError("Morningstar fallback manifest date fields are inconsistent")
        if first_date != expected["first_date"] or last_date != expected["last_date"]:
            raise CoverageAuditError("Morningstar fallback manifest range does not match verified evidence")
        existing = merged[isin]
        if existing.morningstar is not None:
            raise CoverageAuditError("Morningstar fallback manifest contains duplicate ISIN evidence")
        merged[isin] = HistoricalAvailability(
            isin=isin,
            erste=existing.erste,
            oekb=existing.oekb,
            morningstar=SourceAvailability(
                source_name="morningstar",
                source_status=status,
                first_date=first_date,
                last_date=last_date,
                validated_usable=True,
                eligible_for_fallback_coverage=record.get("eligible_for_fallback_coverage") is True,
            ),
        )
    return merged


def merge_mnb_otc_evidence(
    availability: dict[str, HistoricalAvailability], path: Path
) -> dict[str, HistoricalAvailability]:
    """Attach MNB OTC audit evidence without admitting it as NAV coverage.

    The manifest is intentionally restricted to observations explicitly marked
    non-NAV and unapproved for return calculations. This metadata is emitted on
    affected audit windows but cannot reach ``fallback_sources``.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CoverageAuditError(f"Cannot read MNB OTC audit {path}: {exc}") from exc
    _reject_secrets(payload)
    if not isinstance(payload, dict):
        raise CoverageAuditError("MNB OTC audit manifest is not an object")
    if (
        payload.get("schema_version") != 1
        or payload.get("source") != "mnb_otc"
        or payload.get("nav_equivalent") is not False
        or payload.get("backtest_return_series_approved") is not False
    ):
        raise CoverageAuditError("MNB OTC audit manifest has invalid source semantics")
    results = payload.get("results")
    if not isinstance(results, list):
        raise CoverageAuditError("MNB OTC audit manifest has no results list")
    merged = dict(availability)
    for record in results:
        if not isinstance(record, dict):
            raise CoverageAuditError("MNB OTC audit manifest has a non-object record")
        isin = str(record.get("isin", "")).strip().upper()
        quality = record.get("quality")
        if (
            not isin
            or isin not in merged
            or record.get("source") != "mnb_otc"
            or record.get("status") != "VALIDATED_OTC_EVIDENCE_NOT_APPROVED"
            or record.get("currency") != "HUF"
            or record.get("price_type") != "OTC_WEEKLY_TRANSACTION_AVERAGE"
            or record.get("frequency") != "WEEKLY_OTC_AGGREGATE"
            or record.get("nav_equivalent") is not False
            or record.get("backtest_return_series_approved") is not False
            or record.get("eligible_for_fallback_coverage") is not False
            or not isinstance(quality, dict)
        ):
            raise CoverageAuditError("MNB OTC audit manifest record is invalid or unsafe")
        first_period = quality.get("first_period")
        last_period = quality.get("last_period")
        observations = quality.get("observation_count")
        if (
            not isinstance(first_period, dict)
            or not isinstance(last_period, dict)
            or isinstance(observations, bool)
            or not isinstance(observations, int)
            or observations <= 0
            or quality.get("duplicate_conflict_count") != 0
        ):
            raise CoverageAuditError("MNB OTC audit manifest has invalid quality statistics")
        first_period_start = parse_date(first_period.get("start"), f"{isin} MNB first period")
        last_period_end = parse_date(last_period.get("end"), f"{isin} MNB last period")
        if first_period_start is None or last_period_end is None:
            raise CoverageAuditError("MNB OTC audit manifest has missing period boundaries")
        mnb = MnbOtcAvailability(
            first_period_start=first_period_start,
            last_period_end=last_period_end,
            observation_count=observations,
            price_type=str(record["price_type"]),
            frequency=str(record["frequency"]),
        )
        existing = merged[isin]
        if existing.mnb_otc is not None:
            raise CoverageAuditError("MNB OTC audit manifest contains duplicate ISIN evidence")
        merged[isin] = HistoricalAvailability(
            isin=isin,
            erste=existing.erste,
            oekb=existing.oekb,
            morningstar=existing.morningstar,
            corporate_action_lineage=existing.corporate_action_lineage,
            mnb_otc=mnb,
        )
    return merged


def _require_exact_lineage_value(
    record: dict[str, object], field: str, expected: object
) -> None:
    if record.get(field) != expected:
        raise CoverageAuditError(
            f"Corporate-action lineage has invalid {field}: {record.get(field)!r}"
        )


def _require_lineage_record(
    record: object, field: str
) -> dict[str, object]:
    if not isinstance(record, dict):
        raise CoverageAuditError(f"Corporate-action lineage has no {field} object")
    return record


def merge_corporate_action_lineage_availability(
    availability: dict[str, HistoricalAvailability], path: Path
) -> dict[str, HistoricalAvailability]:
    """Attach the sole reviewed lineage, exclusively for coverage classification.

    This reader is deliberately an allowlist, not a relationship resolver: any
    second lineage or any value that differs from the reviewed evidence fails
    closed before the audit can classify a window.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CoverageAuditError(f"Cannot read corporate-action lineage audit {path}: {exc}") from exc
    _reject_secrets(payload)
    if not isinstance(payload, dict):
        raise CoverageAuditError("Corporate-action lineage manifest is not an object")
    if payload.get("schema_version") != 1 or payload.get("scope") != "actual-window coverage audit only":
        raise CoverageAuditError("Corporate-action lineage manifest has an unsupported schema or scope")
    lineages = payload.get("lineages")
    if not isinstance(lineages, list) or len(lineages) != 1:
        raise CoverageAuditError("Corporate-action lineage manifest must contain exactly one lineage")
    lineage_record = _require_lineage_record(lineages[0], "lineage")
    evidence = CORPORATE_ACTION_LINEAGE_EVIDENCE
    predecessor = _require_lineage_record(lineage_record.get("predecessor"), "predecessor")
    successor = _require_lineage_record(lineage_record.get("successor"), "successor")
    transition = _require_lineage_record(lineage_record.get("transition"), "transition")
    provenance = _require_lineage_record(
        lineage_record.get("documentary_provenance"), "documentary_provenance"
    )
    if not {
        "source",
        "relationship",
        "identity_evidence",
    }.issubset(provenance) or not all(
        isinstance(provenance.get(field), str) and str(provenance[field]).strip()
        for field in ("source", "relationship", "identity_evidence")
    ):
        raise CoverageAuditError("Corporate-action lineage documentary provenance is missing")

    for record, values in (
        (
            predecessor,
            {
                "isin": evidence["predecessor_isin"],
                "fund_name": evidence["predecessor_fund_name"],
                "source": evidence["predecessor_source"],
                "exact_returned_isin": evidence["predecessor_isin"],
                "currency": evidence["currency"],
                "observations": evidence["predecessor_observations"],
                "non_positive_nav_count": 0,
                "duplicate_date_count": 0,
            },
        ),
        (
            successor,
            {
                "isin": evidence["successor_isin"],
                "fund_name": evidence["successor_fund_name"],
                "source": evidence["successor_source"],
                "morningstar_id": evidence["successor_morningstar_id"],
                "currency": evidence["currency"],
                "observations": evidence["successor_observations"],
                "non_positive_nav_count": 0,
                "duplicate_date_count": 0,
            },
        ),
    ):
        for field, expected in values.items():
            _require_exact_lineage_value(record, field, expected)

    predecessor_range = _require_lineage_record(predecessor.get("validated_range"), "predecessor range")
    successor_range = _require_lineage_record(successor.get("validated_range"), "successor range")
    _require_exact_lineage_value(
        predecessor_range, "first", evidence["predecessor_first_date"].isoformat()
    )
    _require_exact_lineage_value(
        predecessor_range, "last", evidence["predecessor_last_date"].isoformat()
    )
    _require_exact_lineage_value(successor_range, "first", evidence["successor_first_date"].isoformat())
    _require_exact_lineage_value(successor_range, "last", evidence["successor_last_date"].isoformat())
    _require_exact_lineage_value(successor, "nav_observations", evidence["successor_observations"])
    predecessor_final = _require_lineage_record(
        predecessor.get("final_observation"), "predecessor final observation"
    )
    successor_first = _require_lineage_record(
        successor.get("first_observation"), "successor first observation"
    )
    successor_last = _require_lineage_record(
        successor.get("last_observation"), "successor last observation"
    )
    _require_exact_lineage_value(
        predecessor_final, "date", evidence["predecessor_last_date"].isoformat()
    )
    _require_exact_lineage_value(predecessor_final, "nav", evidence["predecessor_final_nav"])
    _require_exact_lineage_value(successor_first, "date", evidence["successor_first_date"].isoformat())
    _require_exact_lineage_value(successor_first, "nav", evidence["successor_first_nav"])
    _require_exact_lineage_value(successor_last, "date", evidence["successor_last_date"].isoformat())
    _require_exact_lineage_value(successor_last, "nav", evidence["successor_last_nav"])
    _require_exact_lineage_value(transition, "date", evidence["transition_date"].isoformat())
    _require_exact_lineage_value(
        transition, "successor_first_nav_date", evidence["transition_date"].isoformat()
    )
    _require_exact_lineage_value(lineage_record, "currency", evidence["currency"])
    _require_exact_lineage_value(lineage_record, "nav_continuity", False)
    _require_exact_lineage_value(lineage_record, "return_continuity", "not_validated")
    _require_exact_lineage_value(lineage_record, "coverage_only", True)

    predecessor_isin = str(evidence["predecessor_isin"])
    if predecessor_isin not in availability:
        raise CoverageAuditError(
            "Corporate-action predecessor is absent from existing Erste diagnostics"
        )
    existing = availability[predecessor_isin]
    if existing.corporate_action_lineage is not None:
        raise CoverageAuditError("Corporate-action lineage is already attached")
    merged = dict(availability)
    merged[predecessor_isin] = HistoricalAvailability(
        isin=existing.isin,
        erste=existing.erste,
        oekb=existing.oekb,
        morningstar=existing.morningstar,
        corporate_action_lineage=CorporateActionLineage(
            predecessor_isin=predecessor_isin,
            successor_isin=str(evidence["successor_isin"]),
            predecessor=SourceAvailability(
                source_name=str(evidence["predecessor_source"]),
                source_status=USABLE_OEKB_STATUS,
                first_date=evidence["predecessor_first_date"],
                last_date=evidence["predecessor_last_date"],
                validated_usable=True,
                eligible_for_fallback_coverage=True,
            ),
            successor=SourceAvailability(
                source_name=str(evidence["successor_source"]),
                source_status=USABLE_MORNINGSTAR_STATUS,
                first_date=evidence["successor_first_date"],
                last_date=evidence["successor_last_date"],
                validated_usable=True,
                eligible_for_fallback_coverage=True,
            ),
            transition_date=evidence["transition_date"],
            currency=str(evidence["currency"]),
        ),
    )
    return merged


def required_isins_by_portfolio(
    repository: ModelPortfolioRepository, observation_date: date
) -> dict[str, tuple[str, ...]]:
    """Use precisely the recorded constituents at one real source date."""
    grouped: dict[str, set[str]] = {}
    for holding in repository.load_holdings(observation_date):
        isin = str(holding.isin).strip().upper() if holding.isin is not None else MISSING_ISIN
        grouped.setdefault(holding.portfolio_name, set()).add(isin or MISSING_ISIN)
    return {name: tuple(sorted(isins)) for name, isins in sorted(grouped.items())}


def _status(
    *,
    wrong_instrument: bool,
    reconciliation_required: bool,
    unusable: bool,
    missing_start: bool,
    missing_end: bool,
) -> str:
    if wrong_instrument:
        return WRONG_INSTRUMENT_TYPE
    if reconciliation_required:
        return RECONCILIATION_REQUIRED
    if unusable:
        return UNUSABLE_SOURCE
    if missing_start and missing_end:
        return MISSING_BOTH
    if missing_start:
        return MISSING_START
    if missing_end:
        return MISSING_END
    return COMPLETE


def evaluate_window(
    *,
    observation_date: date,
    horizon: int,
    portfolio_name: str,
    required_isins: tuple[str, ...],
    availability: dict[str, HistoricalAvailability],
) -> WindowCoverage:
    """Apply exact-boundary, all-constituent coverage without substitutions."""
    window = ForwardWindow.build(observation_date, horizon)
    covered: list[str] = []
    missing: list[str] = []
    unusable: list[str] = []
    source_used_by_isin: dict[str, str] = {}
    corporate_action_lineage_by_isin: dict[str, dict[str, object]] = {}
    mnb_otc_evidence_by_isin: dict[str, dict[str, object]] = {}
    reasons: list[str] = []
    any_missing_start = False
    any_missing_end = False
    any_unusable = False
    any_reconciliation = False
    any_wrong_instrument = False

    for isin in required_isins:
        record = availability.get(isin)
        if isin == MISSING_ISIN:
            unusable.append(isin)
            reasons.append(f"{isin}: constituent has no ISIN")
            any_wrong_instrument = True
            continue
        if record is None:
            unusable.append(isin)
            reasons.append(f"{isin}: no validated historical-data metadata")
            any_unusable = True
            continue
        if record.mnb_otc is not None:
            mnb_otc_evidence_by_isin[isin] = record.mnb_otc.as_audit_evidence()
        if record.is_wrong_instrument_type:
            unusable.append(isin)
            source_status = record.erste.source_status if record.erste is not None else "NO_METADATA"
            reasons.append(f"{isin}: Erste source status {source_status}")
            any_wrong_instrument = True
            continue
        if record.is_reconciliation_required:
            unusable.append(isin)
            source_status = record.erste.source_status if record.erste is not None else "NO_METADATA"
            reasons.append(f"{isin}: Erste source status {source_status}")
            any_reconciliation = True
            continue
        if record.corporate_action_lineage is not None:
            lineage = record.corporate_action_lineage
            coverage_type = lineage.coverage_for(window)
            if coverage_type is not None:
                covered.append(isin)
                source_used_by_isin[isin] = (
                    "corporate_action_lineage"
                    if coverage_type == "corporate_action_lineage"
                    else (
                        lineage.predecessor.source_name
                        if coverage_type == "predecessor_only"
                        else lineage.successor.source_name
                    )
                )
                corporate_action_lineage_by_isin[isin] = lineage.provenance(coverage_type)
                continue
            missing_start, missing_end = lineage.missing_boundaries(window)
            missing.append(isin)
            if missing_start:
                any_missing_start = True
                reasons.append(
                    f"{isin}: corporate-action predecessor required start "
                    f"{window.evaluation_date.isoformat()} precedes available start "
                    f"{lineage.predecessor.first_date.isoformat() if lineage.predecessor.first_date else 'none'}"
                )
            if missing_end:
                any_missing_end = True
                reasons.append(
                    f"{isin}: corporate-action successor required end "
                    f"{window.end_date.isoformat()} exceeds available end "
                    f"{lineage.successor.last_date.isoformat() if lineage.successor.last_date else 'none'}"
                )
            if not missing_start and not missing_end:
                raise CoverageAuditError(
                    f"{isin}: validated corporate-action lineage does not cover the requested window"
                )
            continue
        if source := record.usable_source_for(window):
            covered.append(isin)
            source_used_by_isin[isin] = source.source_name
            continue

        fallback_sources = record.fallback_sources()
        if fallback_sources:
            fallback = fallback_sources[0]
            missing_start = fallback.first_date is None or fallback.first_date > window.evaluation_date
            missing_end = fallback.last_date is None or fallback.last_date < window.end_date
            missing.append(isin)
            if missing_start:
                any_missing_start = True
                reasons.append(
                    f"{isin}: {fallback.source_name} required start "
                    f"{window.evaluation_date.isoformat()} precedes "
                    f"available start {fallback.first_date.isoformat() if fallback.first_date else 'none'}"
                )
            if missing_end:
                any_missing_end = True
                reasons.append(
                    f"{isin}: {fallback.source_name} required end {window.end_date.isoformat()} exceeds "
                    f"available end {fallback.last_date.isoformat() if fallback.last_date else 'none'}"
                )
            continue

        erste = record.erste
        if record.is_unusable_without_fallback:
            unusable.append(isin)
            source_status = erste.source_status if erste is not None else "NO_METADATA"
            reasons.append(f"{isin}: Erste source status {source_status} is not usable")
            any_unusable = True
            continue

        if erste is None:
            raise AssertionError("Usable Erste availability unexpectedly absent")
        missing_start = erste.first_date is None or erste.first_date > window.evaluation_date
        missing_end = erste.last_date is None or erste.last_date < window.end_date
        if missing_start or missing_end:
            missing.append(isin)
            if missing_start:
                any_missing_start = True
                reasons.append(
                    f"{isin}: required start {window.evaluation_date.isoformat()} precedes "
                    f"Erste available start {erste.first_date.isoformat() if erste.first_date else 'none'}"
                )
            if missing_end:
                any_missing_end = True
                reasons.append(
                    f"{isin}: required end {window.end_date.isoformat()} exceeds "
                    f"Erste available end {erste.last_date.isoformat() if erste.last_date else 'none'}"
                )
            continue

    status = _status(
        wrong_instrument=any_wrong_instrument,
        reconciliation_required=any_reconciliation,
        unusable=any_unusable,
        missing_start=any_missing_start,
        missing_end=any_missing_end,
    )
    if status not in VALID_STATUSES:
        raise AssertionError(f"Unexpected coverage status {status}")
    return WindowCoverage(
        observation_date=window.evaluation_date,
        horizon=horizon,
        required_start=window.evaluation_date,
        required_end=window.end_date,
        portfolio_name=portfolio_name,
        required_isins=required_isins,
        covered_isins=tuple(covered),
        missing_isins=tuple(missing),
        unusable_isins=tuple(unusable),
        source_used_by_isin=source_used_by_isin,
        coverage_via_corporate_action=any(
            provenance["coverage_type"] == "corporate_action_lineage"
            for provenance in corporate_action_lineage_by_isin.values()
        ),
        corporate_action_lineage_by_isin=corporate_action_lineage_by_isin,
        mnb_otc_evidence_by_isin=mnb_otc_evidence_by_isin,
        coverage_ratio=len(covered) / len(required_isins) if required_isins else 0.0,
        status=status,
        reasons=tuple(reasons),
    )


def audit_windows(
    repository: ModelPortfolioRepository, availability: dict[str, HistoricalAvailability]
) -> list[WindowCoverage]:
    """Audit every actual date/portfolio/horizon the backtester can evaluate."""
    rows: list[WindowCoverage] = []
    for observation_date in repository.observation_dates():
        portfolios = required_isins_by_portfolio(repository, observation_date)
        for horizon in sorted(SUPPORTED_HORIZON_DAYS):
            for portfolio_name, isins in portfolios.items():
                rows.append(
                    evaluate_window(
                        observation_date=observation_date,
                        horizon=horizon,
                        portfolio_name=portfolio_name,
                        required_isins=isins,
                        availability=availability,
                    )
                )
    return rows


def build_payload(rows: list[WindowCoverage], *, inputs: dict[str, str]) -> dict[str, object]:
    status_counts = Counter(row.status for row in rows)
    failure_counts = Counter(row.status for row in rows if row.status != COMPLETE)
    complete_oekb_counts = Counter(
        isin
        for row in rows
        if row.status == COMPLETE
        for isin, source in row.source_used_by_isin.items()
        if source == "oekb"
    )
    complete_oekb_window_count = sum(
        row.status == COMPLETE and "oekb" in row.source_used_by_isin.values() for row in rows
    )
    complete_morningstar_counts = Counter(
        isin
        for row in rows
        if row.status == COMPLETE
        for isin, source in row.source_used_by_isin.items()
        if source == "morningstar"
    )
    complete_morningstar_window_count = sum(
        row.status == COMPLETE and "morningstar" in row.source_used_by_isin.values()
        for row in rows
    )
    lineage_coverage_counts = Counter(
        str(provenance["coverage_type"])
        for row in rows
        for isin, provenance in row.corporate_action_lineage_by_isin.items()
        if isin == CORPORATE_ACTION_LINEAGE_EVIDENCE["predecessor_isin"]
    )
    constituent_counts = Counter(
        isin
        for row in rows
        if row.status != COMPLETE
        for isin in (*row.missing_isins, *row.unusable_isins)
    )
    incomplete = len(rows) - status_counts[COMPLETE]
    return {
        "inputs": inputs,
        "period_definition": {
            "observation_dates": "SQLite model_portfolios source dates",
            "horizons": sorted(SUPPORTED_HORIZON_DAYS),
            "window_end_rule": "observation_date + fixed horizon days",
            "constituents": "holdings recorded for each portfolio at observation_date",
        },
        "summary": {
            "total_windows": len(rows),
            "complete_windows": status_counts[COMPLETE],
            "incomplete_windows": incomplete,
            "completion_percentage": (status_counts[COMPLETE] / len(rows) * 100.0) if rows else 0.0,
            "status_counts": dict(sorted(status_counts.items())),
            "failure_reason_counts": dict(sorted(failure_counts.items())),
            "complete_windows_using_oekb": {
                "total": complete_oekb_window_count,
                "by_isin": dict(sorted(complete_oekb_counts.items())),
            },
            "complete_windows_using_morningstar": {
                "total": complete_morningstar_window_count,
                "by_isin": dict(sorted(complete_morningstar_counts.items())),
            },
            "corporate_action_lineage_coverage": {
                "predecessor_isin": CORPORATE_ACTION_LINEAGE_EVIDENCE["predecessor_isin"],
                "successor_isin": CORPORATE_ACTION_LINEAGE_EVIDENCE["successor_isin"],
                "predecessor_only": lineage_coverage_counts["predecessor_only"],
                "successor_only": lineage_coverage_counts["successor_only"],
                "cross_lineage": lineage_coverage_counts["corporate_action_lineage"],
            },
            "most_frequently_missing_or_unusable_isins": [
                {"isin": isin, "window_count": count}
                for isin, count in sorted(
                    constituent_counts.items(), key=lambda item: (-item[1], item[0])
                )
            ],
        },
        "windows": [row.as_dict() for row in rows],
    }


CSV_FIELDS = (
    "observation_date",
    "horizon",
    "required_start",
    "required_end",
    "portfolio_name",
    "required_isins",
    "covered_isins",
    "missing_isins",
    "unusable_isins",
    "source_used_by_isin",
    "coverage_via_corporate_action",
    "corporate_action_lineage_by_isin",
    "mnb_otc_evidence_by_isin",
    "coverage_ratio",
    "status",
    "reasons",
)


def write_outputs(json_path: Path, csv_path: Path, payload: dict[str, object]) -> None:
    """Write deterministic JSON and CSV representations of the same rows."""
    json_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    windows = payload["windows"]
    if not isinstance(windows, list):
        raise CoverageAuditError("Coverage payload has no windows list")
    with csv_path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in windows:
            if not isinstance(row, dict):
                raise CoverageAuditError("Coverage payload contains a non-object window")
            csv_row: dict[str, object] = dict(row)
            for field in (
                "required_isins",
                "covered_isins",
                "missing_isins",
                "unusable_isins",
                "source_used_by_isin",
                "corporate_action_lineage_by_isin",
                "mnb_otc_evidence_by_isin",
                "reasons",
            ):
                csv_row[field] = json.dumps(csv_row[field], separators=(",", ":"))
            writer.writerow(csv_row)


def print_summary(payload: dict[str, object], stream: TextIO = sys.stdout) -> None:
    summary = payload["summary"]
    if not isinstance(summary, dict):
        raise CoverageAuditError("Coverage payload has no summary")
    stream.write("Backtest window coverage\n")
    stream.write(f"Total windows: {summary['total_windows']}\n")
    stream.write(f"Complete windows: {summary['complete_windows']}\n")
    stream.write(f"Incomplete windows: {summary['incomplete_windows']}\n")
    stream.write(f"Completion percentage: {summary['completion_percentage']:.2f}%\n")
    stream.write("Failure counts by status:\n")
    status_counts = summary["status_counts"]
    if isinstance(status_counts, dict):
        for status, count in status_counts.items():
            if status != COMPLETE:
                stream.write(f"  {status}: {count}\n")
    stream.write("Most frequent missing/unusable ISINs:\n")
    frequent = summary["most_frequently_missing_or_unusable_isins"]
    if isinstance(frequent, list):
        for item in frequent[:10]:
            if isinstance(item, dict):
                stream.write(f"  {item['isin']}: {item['window_count']}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("database/model_portfolio.sqlite"))
    parser.add_argument(
        "--historical-audit",
        type=Path,
        default=Path("data/audit/erste_nav_diagnostics.json"),
        help="Existing validated historical-data diagnostics JSON.",
    )
    parser.add_argument(
        "--oekb-fallback-audit",
        type=Path,
        default=Path("data/audit/oekb_fallback_coverage.json"),
        help="Existing validated OeKB fallback-coverage JSON.",
    )
    parser.add_argument(
        "--morningstar-fallback-audit",
        type=Path,
        default=Path("data/audit/morningstar_fallback_coverage.json"),
        help="Existing verified Morningstar fallback-coverage JSON.",
    )
    parser.add_argument(
        "--corporate-action-lineage-audit",
        type=Path,
        default=Path("data/audit/corporate_action_lineage.json"),
        help="Explicitly reviewed, coverage-only corporate-action lineage JSON.",
    )
    parser.add_argument(
        "--mnb-otc-audit",
        type=Path,
        default=Path("data/audit/mnb_otc_coverage.json"),
        help="MNB/KELER OTC evidence manifest; never used as NAV fallback coverage.",
    )
    parser.add_argument(
        "--json-output", type=Path, default=Path("data/audit/backtest_window_coverage.json")
    )
    parser.add_argument(
        "--csv-output", type=Path, default=Path("data/audit/backtest_window_coverage.csv")
    )
    args = parser.parse_args()
    try:
        repository = ModelPortfolioRepository(args.database)
        availability = merge_oekb_fallback_availability(
            load_erste_availability(args.historical_audit), args.oekb_fallback_audit
        )
        availability = merge_morningstar_fallback_availability(
            availability, args.morningstar_fallback_audit
        )
        availability = merge_corporate_action_lineage_availability(
            availability, args.corporate_action_lineage_audit
        )
        availability = merge_mnb_otc_evidence(availability, args.mnb_otc_audit)
        rows = audit_windows(repository, availability)
        payload = build_payload(
            rows,
            inputs={
                "database": str(args.database),
                "historical_audit": str(args.historical_audit),
                "oekb_fallback_audit": str(args.oekb_fallback_audit),
                "morningstar_fallback_audit": str(args.morningstar_fallback_audit),
                "corporate_action_lineage_audit": str(args.corporate_action_lineage_audit),
                "mnb_otc_audit": str(args.mnb_otc_audit),
            },
        )
        write_outputs(args.json_output, args.csv_output, payload)
        print_summary(payload)
        print(f"JSON output: {args.json_output}")
        print(f"CSV output: {args.csv_output}")
    except (CoverageAuditError, RepositoryError, OSError, ValueError) as exc:
        sys.stderr.write(f"Coverage audit failed: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
