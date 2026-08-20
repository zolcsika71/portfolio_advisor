from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from portfolio_advisor.history.alternative_price_sources import (
    SOURCE_TIERS,
    AlternativePriceObservation,
    AlternativePriceSourceError,
    build_alternative_source_research,
    compare_keler_observations,
    hypothetical_window_coverage,
    series_statistics,
    validate_observations,
)
from portfolio_advisor.history.mnb_otc import MnbOtcObservation


def findings(candidates: list[dict[str, object]]) -> dict[str, object]:
    return {
        "source_tiers": sorted(SOURCE_TIERS),
        "query_families": ["exact_isin_and_series"],
        "candidates": candidates,
    }


def candidate(source: Path, *, status: str = "AUDIT_CANDIDATE_VALIDATED") -> dict[str, object]:
    return {
        "source_id": "issuer",
        "authority": "ÁKK",
        "host": "www.allampapir.hu",
        "source_type": "AUTHORITATIVE_ISSUER",
        "source_url": "https://www.allampapir.hu/history.csv",
        "admission_status": status,
        "exact_isin_supported": True,
        "currency": "HUF",
        "price_semantics_status": "VALIDATED",
        "date_semantics": "valuation date",
        "local_evidence_path": str(source),
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "observations": [
            {"isin": "HU0000554795", "currency": "HUF", "date": "2024-07-02", "value": "100.1"},
            {"isin": "HU0000554795", "currency": "HUF", "date": "2025-06-04", "value": "101.2"},
        ],
    }


def test_exact_issuer_source_can_be_a_validated_audit_candidate(tmp_path: Path) -> None:
    source = tmp_path / "history.csv"
    source.write_bytes(b"date,value")
    result = build_alternative_source_research(findings([candidate(source)]))

    assert result["research_outcome"] == "ALTERNATIVE_PRICE_SOURCE_FOUND"
    candidates = cast(list[dict[str, object]], result["candidates"])
    statistics = cast(dict[str, object], candidates[0]["series_statistics"])
    assert statistics["observation_count"] == 2


@pytest.mark.parametrize(
    "source_type",
    ["AUTHORITATIVE_REGULATOR", "AUTHORITATIVE_EXCHANGE", "AUTHORITATIVE_DISTRIBUTOR", "COMMERCIAL_CORROBORATING"],
)
def test_source_categories_remain_explicit(tmp_path: Path, source_type: str) -> None:
    source = tmp_path / "history.csv"
    source.write_bytes(b"history")
    item = candidate(source, status="AUDIT_CANDIDATE_REJECTED")
    item["source_type"] = source_type
    result = build_alternative_source_research(findings([item]))
    candidates = cast(list[dict[str, object]], result["candidates"])
    assert candidates[0]["source_type"] == source_type
    assert result["research_outcome"] == "ALTERNATIVE_PRICE_SOURCE_NOT_FOUND"


@pytest.mark.parametrize(
    "field,value",
    [("isin", "HU0000554794"), ("currency", "EUR"), ("value", "0")],
)
def test_wrong_identity_currency_or_nonpositive_value_fails_closed(
    field: str, value: str
) -> None:
    record = {"isin": "HU0000554795", "currency": "HUF", "date": "2024-07-02", "value": "100"}
    record[field] = value
    with pytest.raises(AlternativePriceSourceError):
        validate_observations([record])


def test_identical_duplicates_are_deduplicated_but_conflicts_fail_closed() -> None:
    row = {"isin": "HU0000554795", "currency": "HUF", "date": "2024-07-02", "value": "100"}
    assert len(validate_observations([row, row])) == 1
    conflicting = dict(row)
    conflicting["value"] = "101"
    with pytest.raises(AlternativePriceSourceError):
        validate_observations([row, conflicting])


def test_series_statistics_use_exact_dates_without_nearest_substitution() -> None:
    observations = (
        AlternativePriceObservation("HU0000554795", "HUF", date(2024, 7, 3), Decimal(100)),
        AlternativePriceObservation("HU0000554795", "HUF", date(2024, 7, 10), Decimal(101)),
    )
    result = series_statistics(observations)
    assert result["exact_start_boundary"] is False
    assert result["maximum_gap_days"] == 7


def test_comparison_requires_semantic_compatibility_and_uses_decimal() -> None:
    source = AlternativePriceObservation("HU0000554795", "HUF", date(2024, 9, 1), Decimal("101.5"))
    keler = MnbOtcObservation(
        isin="HU0000554795", instrument_name="K250604 Egyéves Magyar Állampapír", currency="HUF",
        period_start=date(2024, 8, 26), period_end=date(2024, 9, 1),
        nominal_value_huf_thousand=Decimal(1), purchase_value_huf_thousand=Decimal(1),
        average_price=Decimal("101.4"), minimum_price=Decimal("101.4"), maximum_price=Decimal("101.4"),
        transaction_count=1, source_document="report.pdf", source_document_hash="a" * 64,
    )
    not_comparable = compare_keler_observations((source,), (keler,), semantically_comparable=False)
    comparable = compare_keler_observations((source,), (keler,), semantically_comparable=True)
    assert not_comparable[0]["comparison_status"] == "NOT_COMPARABLE_SEMANTICS"
    assert comparable[0]["difference"] == "0.1"


def test_hypothetical_windows_stay_hypothetical() -> None:
    observations = (
        AlternativePriceObservation("HU0000554795", "HUF", date(2024, 7, 2), Decimal(100)),
        AlternativePriceObservation("HU0000554795", "HUF", date(2024, 7, 3), Decimal(101)),
    )
    result = hypothetical_window_coverage(
        [{"required_start": "2024-07-02", "required_end": "2024-07-03"}], observations,
        maturity=date(2025, 6, 4),
    )
    assert result["exact_boundary_coverable_windows"] == 1
    assert result["actual_complete_windows_created"] == 0


def test_url_only_cannot_be_validated(tmp_path: Path) -> None:
    source = tmp_path / "history.csv"
    source.write_bytes(b"history")
    item = candidate(source)
    item["local_evidence_path"] = None
    with pytest.raises(AlternativePriceSourceError):
        build_alternative_source_research(findings([item]))
