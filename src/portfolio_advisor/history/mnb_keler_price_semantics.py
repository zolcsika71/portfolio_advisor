"""Bounded, provenance-first research for MNB/KELER OTC price-field semantics.

The module is deliberately separate from absent-row semantics and from return
methodology.  Direct, locally retained, applicable primary evidence is the
only category allowed to validate a report-field property.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import cast

from .mnb_otc import MnbOtcObservation, decimal_text

REQUIRED_SOURCE_FAMILIES = frozenset(
    {
        "KELER_PRIMARY",
        "MNB_PRIMARY",
        "REGULATION_RULEBOOK",
        "BET_AKK",
        "OFFICIAL_ARCHIVE",
    }
)
REQUIRED_QUERY_FAMILIES = frozenset(
    {
        "quotation_basis",
        "clean_dirty_accrued_interest",
        "average_aggregation",
        "minimum_maximum",
        "tetelszam",
        "date_semantics",
        "fees",
        "weekly_report_methodology",
    }
)
MAX_QUERY_FAMILIES = 20
MAX_CANDIDATE_DOCUMENTS = 40
TARGET_START = date(2024, 7, 2)
TARGET_END = date(2025, 6, 4)
FIELD_PROPERTIES = frozenset(
    {
        "percentage_of_par",
        "clean_price",
        "accrued_interest_excluded",
        "dirty_price",
        "accrued_interest_included",
        "average_transaction_weighted",
        "average_nominal_volume_weighted",
        "average_arithmetic_mean",
        "minimum_semantics",
        "maximum_semantics",
        "tetelszam_trade_count",
        "tetelszam_settlement_count",
        "date_semantics",
        "fees_included",
    }
)
ESSENTIAL_PROPERTIES = frozenset(
    {
        "percentage_of_par",
        "clean_price",
        "dirty_price",
        "accrued_interest_excluded",
        "accrued_interest_included",
        "minimum_semantics",
        "maximum_semantics",
        "date_semantics",
    }
)
DIRECT = "AUTHORITATIVE_DIRECT"


class PriceSemanticsError(RuntimeError):
    """Price-semantics findings are malformed or cannot be safely promoted."""


def _date(value: object, label: str) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PriceSemanticsError(f"{label} must be an ISO date or null")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise PriceSemanticsError(f"{label} must be an ISO date") from exc


def _candidate(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise PriceSemanticsError("Price-semantics candidate must be an object")
    required = ("source_family", "authority", "host", "source_url", "title", "review_status", "evidence_category", "applicability_status")
    if any(not isinstance(value.get(field), str) or not value[field] for field in required):
        raise PriceSemanticsError("Price-semantics candidate provenance is incomplete")
    if value["source_family"] not in REQUIRED_SOURCE_FAMILIES:
        raise PriceSemanticsError("Price-semantics candidate has an unknown source family")
    if not value["source_url"].startswith("https://"):
        raise PriceSemanticsError("Price-semantics source URL must use HTTPS")
    if value["review_status"] not in {"ACCEPTED_EVIDENCE", "PARTIAL_EVIDENCE", "NOT_APPLICABLE", "INSUFFICIENT", "CONFLICTING", "DUPLICATE"}:
        raise PriceSemanticsError("Price-semantics candidate has an invalid review status")
    if value["evidence_category"] not in {DIRECT, "AUTHORITATIVE_INDIRECT", "INSTRUMENT_MARKET_CONVENTION", "DIAGNOSTIC_INFERENCE", "UNSUPPORTED", "CONFLICTING"}:
        raise PriceSemanticsError("Price-semantics candidate has an invalid evidence category")
    properties = value.get("supported_properties", [])
    if not isinstance(properties, list) or not all(isinstance(item, str) for item in properties):
        raise PriceSemanticsError("Price-semantics properties are malformed")
    if not set(properties).issubset(FIELD_PROPERTIES):
        raise PriceSemanticsError("Price-semantics candidate supports an unknown property")
    official = value.get("official")
    if not isinstance(official, bool):
        raise PriceSemanticsError("Price-semantics official flag is malformed")
    result = dict(value)
    result["publication_date"] = _date(value.get("publication_date"), "publication_date")
    result["effective_start"] = _date(value.get("effective_start"), "effective_start")
    result["effective_end"] = _date(value.get("effective_end"), "effective_end")
    if result["effective_start"] and result["effective_end"] and result["effective_start"] > result["effective_end"]:
        raise PriceSemanticsError("Price-semantics effective interval is reversed")
    direct = result["review_status"] == "ACCEPTED_EVIDENCE" and result["evidence_category"] == DIRECT
    if direct:
        for field in ("local_path", "sha256", "relevant_location"):
            if not isinstance(result.get(field), str) or not result[field]:
                raise PriceSemanticsError("Direct price evidence requires local provenance")
        if not official or result["applicability_status"] != "VALIDATED_2024_2025":
            raise PriceSemanticsError("Direct price evidence must be official and applicable")
        if result["effective_start"] is None or result["effective_end"] is None or result["effective_start"] > TARGET_START or result["effective_end"] < TARGET_END:
            raise PriceSemanticsError("Direct price evidence lacks a target-period effective interval")
        path = Path(result["local_path"])
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != result["sha256"]:
            raise PriceSemanticsError("Direct price evidence is missing or hash changed")
    return result


def build_price_semantics_ledger(findings: Mapping[str, object]) -> dict[str, object]:
    """Aggregate a finite reviewed search; no network I/O occurs here."""
    families = findings.get("source_families")
    queries = findings.get("query_families")
    raw_candidates = findings.get("candidate_documents")
    if not isinstance(families, list) or set(families) != REQUIRED_SOURCE_FAMILIES:
        raise PriceSemanticsError("Research did not cover every required source family")
    if not isinstance(queries, list) or not REQUIRED_QUERY_FAMILIES.issubset(set(queries)) or len(queries) > MAX_QUERY_FAMILIES:
        raise PriceSemanticsError("Research query-family coverage is incomplete or unbounded")
    if not isinstance(raw_candidates, list) or len(raw_candidates) > MAX_CANDIDATE_DOCUMENTS:
        raise PriceSemanticsError("Research candidate limit is invalid")
    candidates = [_candidate(item) for item in raw_candidates]
    urls = [str(item["source_url"]) for item in candidates]
    if len(urls) != len(set(urls)):
        raise PriceSemanticsError("Duplicate price-semantics candidate URL")
    direct = [item for item in candidates if item["review_status"] == "ACCEPTED_EVIDENCE" and item["evidence_category"] == DIRECT]
    conflicts = [item for item in candidates if item["review_status"] == "CONFLICTING" and item["evidence_category"] == "CONFLICTING"]
    supported = {
        property_name
        for item in direct
        for property_name in cast(list[str], item["supported_properties"])
    }
    if conflicts:
        status = "MNB_OTC_PRICE_SEMANTICS_CONFLICT"
    elif ESSENTIAL_PROPERTIES.issubset(supported):
        status = "MNB_OTC_PRICE_SEMANTICS_VALIDATED"
    elif direct:
        status = "MNB_OTC_PRICE_SEMANTICS_PARTIAL"
    else:
        status = "MNB_OTC_PRICE_SEMANTICS_NOT_FOUND"
    def document(item: Mapping[str, object]) -> dict[str, object]:
        converted = dict(item)
        for field in ("publication_date", "effective_start", "effective_end"):
            value = converted.get(field)
            if isinstance(value, date):
                converted[field] = value.isoformat()
        return converted
    return {
        "schema_version": 1,
        "target_report_family": "MNB-hosted KELER weekly OTC securities turnover report",
        "target_isin": "HU0000554795",
        "research_status": status,
        "stopping_rule_completed": True,
        "source_families": sorted(families),
        "query_families": sorted(queries),
        "candidate_document_count": len(candidates),
        "accepted_document_count": len(direct),
        "accepted_documents": [document(item) for item in direct],
        "candidate_documents": [document(item) for item in candidates],
        "questions": {
            property_name: {
                "answer": "YES" if property_name in supported else "UNKNOWN",
                "evidence_category": DIRECT if property_name in supported else "UNSUPPORTED",
                "candidate_urls": sorted(
                    str(item["source_url"])
                    for item in direct
                    if property_name in cast(list[str], item["supported_properties"])
                ),
            }
            for property_name in sorted(FIELD_PROPERTIES)
        },
        "remaining_unknowns": sorted(FIELD_PROPERTIES - supported),
        "conflicts": [document(item) for item in conflicts],
        "nav_equivalent": False,
        "backtest_return_series_approved": False,
        "usable_for_backtest": False,
    }


def diagnostic_price_analysis(observations: Sequence[MnbOtcObservation]) -> dict[str, object]:
    """Describe exact quotations only; this has no pricing or return meaning."""
    ordered = sorted(observations, key=lambda item: (item.period_start, item.period_end))
    if len(ordered) != 3:
        raise PriceSemanticsError("Expected exactly three retained HU0000554795 observations")
    rows: list[dict[str, object]] = []
    previous: MnbOtcObservation | None = None
    for observation in ordered:
        row: dict[str, object] = {
            "period_start": observation.period_start.isoformat(),
            "period_end": observation.period_end.isoformat(),
            "average_price": decimal_text(observation.average_price),
            "minimum_price": decimal_text(observation.minimum_price),
            "maximum_price": decimal_text(observation.maximum_price),
            "transaction_count": observation.transaction_count,
            "average_minus_100_diagnostic": decimal_text(
                observation.average_price - Decimal(100)
            ),
            "weekly_min_max_spread_diagnostic": decimal_text(observation.maximum_price - observation.minimum_price),
            "difference_from_prior_observed_average_diagnostic": None,
        }
        if previous is not None:
            row["difference_from_prior_observed_average_diagnostic"] = decimal_text(observation.average_price - previous.average_price)
            row["calendar_days_from_prior_period_end"] = (observation.period_start - previous.period_end).days
        rows.append(row)
        previous = observation
    return {
        "classification": "DIAGNOSTIC_INFERENCE_ONLY",
        "quoted_price_monotonic_non_decreasing": all(
            latter.average_price >= former.average_price
            for former, latter in pairwise(ordered)
        ),
        "observations": rows,
        "not_authoritative_evidence_for": [
            "percentage-of-par quotation",
            "clean or dirty quotation",
            "accrued-interest treatment",
            "average aggregation method",
            "investment return",
        ],
        "approved_as_return": False,
        "accrued_interest_synthesized": False,
        "clean_dirty_transformation_performed": False,
    }


def return_suitability() -> dict[str, object]:
    """Return the invariant non-approval independently from research status."""
    return {
        "status": "MNB_OTC_RETURN_SERIES_NOT_APPROVED",
        "reasons": [
            "weekly OTC aggregates are sparse and not NAV observations",
            "quotation, accrued-interest, and aggregation semantics are not validated",
            "cash-flow integration and exact-boundary methodology are not approved",
            "absence semantics remain frozen as unknown",
        ],
        "nav_equivalent": False,
        "backtest_return_series_approved": False,
        "usable_for_backtest": False,
    }
