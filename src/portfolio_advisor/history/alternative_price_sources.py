"""Fail-closed alternative-price-source research for an exact security ISIN.

Candidates remain isolated audit evidence.  This module does not select a
source, fill a missing date, or expose a series to NAV/backtest code.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from pathlib import Path
from statistics import median

from .mnb_otc import TARGET_HU_ISIN, MnbOtcObservation, decimal_text

TARGET_START = date(2024, 7, 2)
TARGET_END = date(2025, 6, 4)
TARGET_CURRENCY = "HUF"
SOURCE_TIERS = frozenset(
    {
        "AKK_ALLAMPAPIR",
        "MAGYAR_ALLAMKINCSTAR",
        "MNB",
        "BET",
        "KELER",
        "OFFICIAL_DISTRIBUTOR",
    }
)
SOURCE_TYPES = frozenset(
    {
        "AUTHORITATIVE_ISSUER",
        "AUTHORITATIVE_TREASURY",
        "AUTHORITATIVE_REGULATOR",
        "AUTHORITATIVE_EXCHANGE",
        "AUTHORITATIVE_CSD",
        "AUTHORITATIVE_DISTRIBUTOR",
        "COMMERCIAL_CORROBORATING",
        "UNUSABLE",
    }
)
ADMISSION_STATUSES = frozenset(
    {
        "AUDIT_CANDIDATE_VALIDATED",
        "AUDIT_CANDIDATE_PARTIAL",
        "AUDIT_CANDIDATE_NOT_FOUND",
        "AUDIT_CANDIDATE_REJECTED",
        "AUDIT_CANDIDATE_CONFLICT",
    }
)
MAX_QUERY_FAMILIES = 20
MAX_CANDIDATES = 40


class AlternativePriceSourceError(RuntimeError):
    """Alternative-source research is incomplete, ambiguous, or unsafe."""


@dataclass(frozen=True, slots=True)
class AlternativePriceObservation:
    """An exact-date observation retained from one isolated candidate source."""

    isin: str
    currency: str
    observation_date: date
    value: Decimal

    def __post_init__(self) -> None:
        if self.isin != TARGET_HU_ISIN:
            raise AlternativePriceSourceError("Alternative price observation has wrong ISIN")
        if self.currency != TARGET_CURRENCY:
            raise AlternativePriceSourceError("Alternative price observation has wrong currency")
        if not self.value.is_finite() or self.value <= 0:
            raise AlternativePriceSourceError("Alternative price observation must be finite and positive")


def _date(value: object, label: str) -> date:
    if not isinstance(value, str):
        raise AlternativePriceSourceError(f"{label} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise AlternativePriceSourceError(f"{label} must be an ISO date") from exc


def _decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, str):
        raise AlternativePriceSourceError(f"{label} must be a Decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise AlternativePriceSourceError(f"{label} is not a Decimal") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise AlternativePriceSourceError(f"{label} must be finite and positive")
    return parsed


def validate_observations(records: Sequence[Mapping[str, object]]) -> tuple[AlternativePriceObservation, ...]:
    """Validate one source's isolated exact-date observations and duplicates."""
    deduplicated: dict[date, AlternativePriceObservation] = {}
    for item in records:
        observation = AlternativePriceObservation(
            isin=str(item.get("isin", "")),
            currency=str(item.get("currency", "")),
            observation_date=_date(item.get("date"), "alternative observation date"),
            value=_decimal(item.get("value"), "alternative observation value"),
        )
        existing = deduplicated.get(observation.observation_date)
        if existing is not None and existing.value != observation.value:
            raise AlternativePriceSourceError("Alternative source has conflicting duplicate dates")
        deduplicated[observation.observation_date] = observation
    return tuple(sorted(deduplicated.values(), key=lambda item: item.observation_date))


def series_statistics(observations: Sequence[AlternativePriceObservation]) -> dict[str, object]:
    if not observations:
        return {
            "first_date": None,
            "last_date": None,
            "observation_count": 0,
            "distinct_dates": 0,
            "maximum_gap_days": None,
            "median_gap_days": None,
            "exact_start_boundary": False,
            "exact_maturity_boundary": False,
            "days_from_required_start_to_first": None,
            "days_from_last_to_maturity": None,
        }
    gaps = [
        (later.observation_date - earlier.observation_date).days
        for earlier, later in pairwise(observations)
    ]
    first = observations[0].observation_date
    last = observations[-1].observation_date
    return {
        "first_date": first.isoformat(),
        "last_date": last.isoformat(),
        "observation_count": len(observations),
        "distinct_dates": len(observations),
        "maximum_gap_days": max(gaps) if gaps else 0,
        "median_gap_days": median(gaps) if gaps else 0,
        "exact_start_boundary": any(item.observation_date == TARGET_START for item in observations),
        "exact_maturity_boundary": any(item.observation_date == TARGET_END for item in observations),
        "days_from_required_start_to_first": (first - TARGET_START).days,
        "days_from_last_to_maturity": (TARGET_END - last).days,
    }


def compare_keler_observations(
    alternative: Sequence[AlternativePriceObservation],
    keler: Sequence[MnbOtcObservation],
    *,
    semantically_comparable: bool,
) -> list[dict[str, object]]:
    """Compare only exact period-boundary dates; no tolerance or matching rule."""
    results: list[dict[str, object]] = []
    for source_observation in alternative:
        matching = [
            observation
            for observation in keler
            if source_observation.observation_date
            in {observation.period_start, observation.period_end}
        ]
        if not matching:
            results.append(
                {
                    "candidate_date": source_observation.observation_date.isoformat(),
                    "candidate_value": decimal_text(source_observation.value),
                    "comparison_status": "NO_EXACT_DATE",
                }
            )
            continue
        for keler_observation in matching:
            row: dict[str, object] = {
                "candidate_date": source_observation.observation_date.isoformat(),
                "candidate_value": decimal_text(source_observation.value),
                "keler_reporting_period": {
                    "start": keler_observation.period_start.isoformat(),
                    "end": keler_observation.period_end.isoformat(),
                },
                "keler_average": decimal_text(keler_observation.average_price),
                "keler_minimum": decimal_text(keler_observation.minimum_price),
                "keler_maximum": decimal_text(keler_observation.maximum_price),
            }
            if not semantically_comparable:
                row["comparison_status"] = "NOT_COMPARABLE_SEMANTICS"
            else:
                difference = source_observation.value - keler_observation.average_price
                row.update(
                    {
                        "difference": decimal_text(difference),
                        "relative_difference": decimal_text(
                            difference / keler_observation.average_price
                        ),
                        "comparison_status": "COMPARABLE_MATCH"
                        if difference == 0
                        else "COMPARABLE_DIFFERENT",
                    }
                )
            results.append(row)
    return results


def hypothetical_window_coverage(
    windows: Sequence[Mapping[str, object]],
    observations: Sequence[AlternativePriceObservation],
    *,
    maturity: date,
) -> dict[str, int]:
    """Describe one source alone; it cannot change primary coverage outcomes."""
    dates = {item.observation_date for item in observations}
    exact = range_only = partial = uncovered = crossing = 0
    for window in windows:
        start = _date(window.get("required_start"), "window start")
        end = _date(window.get("required_end"), "window end")
        inside = [item for item in observations if start <= item.observation_date <= end]
        crossing += start < maturity < end
        if start in dates and end in dates:
            exact += 1
        elif inside and min(item.observation_date for item in inside) <= start and max(
            item.observation_date for item in inside
        ) >= end:
            range_only += 1
        elif inside:
            partial += 1
        else:
            uncovered += 1
    return {
        "exact_boundary_coverable_windows": exact,
        "range_covered_not_exact_boundary_windows": range_only,
        "partially_covered_windows": partial,
        "uncovered_windows": uncovered,
        "crossing_maturity_windows": crossing,
        "actual_complete_windows_created": 0,
    }


def _validate_retained_source(candidate: Mapping[str, object]) -> None:
    path_value = candidate.get("local_evidence_path")
    digest = candidate.get("sha256")
    if not isinstance(path_value, str) or not isinstance(digest, str):
        raise AlternativePriceSourceError("Validated alternative source needs local evidence and SHA-256")
    path = Path(path_value)
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise AlternativePriceSourceError("Alternative-source retained evidence is missing or hash changed")


def build_alternative_source_research(findings: Mapping[str, object]) -> dict[str, object]:
    """Build a finite research result; URL-only candidates cannot be validated."""
    tiers = findings.get("source_tiers")
    queries = findings.get("query_families")
    raw_candidates = findings.get("candidates")
    if not isinstance(tiers, list) or set(tiers) != SOURCE_TIERS:
        raise AlternativePriceSourceError("Every alternative-source tier must be covered")
    if not isinstance(queries, list) or not queries or len(queries) > MAX_QUERY_FAMILIES:
        raise AlternativePriceSourceError("Alternative-source query coverage is invalid")
    if not isinstance(raw_candidates, list) or len(raw_candidates) > MAX_CANDIDATES:
        raise AlternativePriceSourceError("Alternative-source candidate limit is invalid")
    candidates: list[dict[str, object]] = []
    seen_urls: set[str] = set()
    for raw in raw_candidates:
        if not isinstance(raw, dict):
            raise AlternativePriceSourceError("Alternative-source candidate is malformed")
        candidate = dict(raw)
        required_text = ("source_id", "authority", "host", "source_type", "source_url", "admission_status")
        if any(not isinstance(candidate.get(field), str) or not candidate[field] for field in required_text):
            raise AlternativePriceSourceError("Alternative-source candidate provenance is incomplete")
        if candidate["source_type"] not in SOURCE_TYPES or candidate["admission_status"] not in ADMISSION_STATUSES:
            raise AlternativePriceSourceError("Alternative-source candidate classification is invalid")
        if not str(candidate["source_url"]).startswith("https://"):
            raise AlternativePriceSourceError("Alternative-source candidate URL must use HTTPS")
        if candidate["source_url"] in seen_urls:
            raise AlternativePriceSourceError("Duplicate alternative-source candidate URL")
        seen_urls.add(str(candidate["source_url"]))
        local_path = candidate.get("local_evidence_path")
        if isinstance(local_path, str) and local_path:
            path = Path(local_path)
            if path.is_file():
                candidate["source_hash"] = hashlib.sha256(path.read_bytes()).hexdigest()
            else:
                candidate["local_evidence_warning"] = "LOCAL_EVIDENCE_PATH_MISSING"
        if candidate["admission_status"] == "AUDIT_CANDIDATE_VALIDATED":
            if candidate.get("exact_isin_supported") is not True or candidate.get("currency") != TARGET_CURRENCY:
                raise AlternativePriceSourceError("Validated alternative source lacks exact identity")
            if candidate.get("price_semantics_status") != "VALIDATED" or candidate.get("date_semantics") in {None, "UNKNOWN"}:
                raise AlternativePriceSourceError("Validated alternative source lacks field/date semantics")
            _validate_retained_source(candidate)
            records = candidate.get("observations")
            if not isinstance(records, list):
                raise AlternativePriceSourceError("Validated alternative source lacks observations")
            validated_observations = validate_observations(records)
            candidate["series_statistics"] = series_statistics(validated_observations)
        candidates.append(candidate)
    validated_candidates = [
        item
        for item in candidates
        if item["admission_status"] == "AUDIT_CANDIDATE_VALIDATED"
    ]
    partial = [item for item in candidates if item["admission_status"] == "AUDIT_CANDIDATE_PARTIAL"]
    conflicts = [item for item in candidates if item["admission_status"] == "AUDIT_CANDIDATE_CONFLICT"]
    outcome = (
        "ALTERNATIVE_PRICE_SOURCE_CONFLICT"
        if conflicts
        else "ALTERNATIVE_PRICE_SOURCE_FOUND"
        if validated_candidates
        else "ALTERNATIVE_PRICE_SOURCE_PARTIAL"
        if partial
        else "ALTERNATIVE_PRICE_SOURCE_NOT_FOUND"
    )
    return {
        "schema_version": 1,
        "isin": TARGET_HU_ISIN,
        "research_interval": {"start": TARGET_START.isoformat(), "end": TARGET_END.isoformat()},
        "source_tiers": sorted(tiers),
        "query_families": sorted(queries),
        "candidate_count": len(candidates),
        "candidates": candidates,
        "retained_evidence": [item for item in validated_candidates],
        "research_outcome": outcome,
        "stopping_rule_completed": True,
        "no_brute_force_discovery": True,
        "no_unbounded_pagination": True,
        "preferred_audit_candidate": None,
        "nav_equivalent": False,
        "backtest_return_series_approved": False,
        "usable_for_backtest": False,
    }
