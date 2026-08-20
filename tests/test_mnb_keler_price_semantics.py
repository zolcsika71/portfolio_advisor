from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from portfolio_advisor.history.mnb_keler_price_semantics import (
    REQUIRED_QUERY_FAMILIES,
    REQUIRED_SOURCE_FAMILIES,
    PriceSemanticsError,
    build_price_semantics_ledger,
    diagnostic_price_analysis,
    return_suitability,
)
from portfolio_advisor.history.mnb_otc import MnbOtcObservation


def findings(candidates: list[dict[str, object]]) -> dict[str, object]:
    return {
        "source_families": sorted(REQUIRED_SOURCE_FAMILIES),
        "query_families": sorted(REQUIRED_QUERY_FAMILIES),
        "candidate_documents": candidates,
    }


def candidate(
    source: Path,
    *,
    category: str = "AUTHORITATIVE_DIRECT",
    status: str = "ACCEPTED_EVIDENCE",
    properties: list[str] | None = None,
) -> dict[str, object]:
    return {
        "source_family": "KELER_PRIMARY",
        "authority": "KELER",
        "host": "www.keler.hu",
        "source_url": "https://www.keler.hu/methodology.pdf",
        "title": "Methodology",
        "review_status": status,
        "evidence_category": category,
        "official": True,
        "applicability_status": "VALIDATED_2024_2025",
        "local_path": str(source),
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "relevant_location": "section 2",
        "effective_start": "2024-01-01",
        "effective_end": "2025-12-31",
        "supported_properties": properties or ["percentage_of_par"],
    }


def observation(start: date, end: date, average: str) -> MnbOtcObservation:
    return MnbOtcObservation(
        isin="HU0000554795",
        instrument_name="K250604 Egyéves Magyar Állampapír",
        currency="HUF",
        period_start=start,
        period_end=end,
        nominal_value_huf_thousand=Decimal("51100.0"),
        purchase_value_huf_thousand=Decimal("52586.805"),
        average_price=Decimal(average),
        minimum_price=Decimal(average),
        maximum_price=Decimal(average),
        transaction_count=3,
        source_document="report.pdf",
        source_document_hash="a" * 64,
    )


def answers(result: dict[str, object]) -> dict[str, dict[str, object]]:
    return cast(dict[str, dict[str, object]], result["questions"])


def test_no_direct_evidence_is_not_found() -> None:
    result = build_price_semantics_ledger(findings([]))
    assert result["research_status"] == "MNB_OTC_PRICE_SEMANTICS_NOT_FOUND"
    assert answers(result)["clean_price"]["answer"] == "UNKNOWN"


@pytest.mark.parametrize(
    "category",
    ["AUTHORITATIVE_INDIRECT", "INSTRUMENT_MARKET_CONVENTION", "DIAGNOSTIC_INFERENCE"],
)
def test_non_direct_evidence_cannot_validate_report_field(
    tmp_path: Path, category: str
) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"source")
    item = candidate(source, category=category, status="PARTIAL_EVIDENCE")
    result = build_price_semantics_ledger(findings([item]))
    assert answers(result)["percentage_of_par"]["answer"] == "UNKNOWN"


def test_direct_retained_applicable_evidence_can_validate_a_property(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"source")
    result = build_price_semantics_ledger(findings([candidate(source)]))
    assert result["research_status"] == "MNB_OTC_PRICE_SEMANTICS_PARTIAL"
    assert answers(result)["percentage_of_par"]["answer"] == "YES"


def test_invalid_direct_provenance_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"source")
    item = candidate(source)
    item["sha256"] = "b" * 64
    with pytest.raises(PriceSemanticsError):
        build_price_semantics_ledger(findings([item]))


def test_conflicting_direct_evidence_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"source")
    item = candidate(source, category="CONFLICTING", status="CONFLICTING")
    result = build_price_semantics_ledger(findings([item]))
    assert result["research_status"] == "MNB_OTC_PRICE_SEMANTICS_CONFLICT"


def test_decimal_diagnostics_are_not_returns() -> None:
    result = diagnostic_price_analysis(
        (
            observation(date(2024, 8, 26), date(2024, 9, 1), "101.364406"),
            observation(date(2024, 10, 28), date(2024, 11, 3), "102.400000"),
            observation(date(2024, 11, 25), date(2024, 12, 1), "102.909598"),
        )
    )
    assert result["classification"] == "DIAGNOSTIC_INFERENCE_ONLY"
    assert result["approved_as_return"] is False
    rows = cast(list[dict[str, object]], result["observations"])
    assert rows[1]["difference_from_prior_observed_average_diagnostic"] == "1.035594"
    assert result["accrued_interest_synthesized"] is False


def test_return_suitability_remains_unapproved() -> None:
    result = return_suitability()
    assert result["status"] == "MNB_OTC_RETURN_SERIES_NOT_APPROVED"
    assert result["backtest_return_series_approved"] is False
