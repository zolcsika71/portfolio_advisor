from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import pytest

from portfolio_advisor.history.mnb_keler_scope_research import (
    MAX_CANDIDATE_DOCUMENTS,
    REQUIRED_QUERY_FAMILIES,
    REQUIRED_SOURCE_FAMILIES,
    ScopeResearchError,
    build_research_ledger,
)


def findings(candidates: list[dict[str, object]]) -> dict[str, object]:
    return {
        "source_families": sorted(REQUIRED_SOURCE_FAMILIES),
        "query_families": sorted(REQUIRED_QUERY_FAMILIES),
        "candidate_documents": candidates,
    }


def candidate(
    path: Path,
    *,
    links: list[str],
    status: str = "ACCEPTED_EVIDENCE",
    url_suffix: str = "document.pdf",
) -> dict[str, object]:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "source_family": "KELER_PRIMARY",
        "authority": "KELER",
        "host": "www.keler.hu",
        "source_url": f"https://www.keler.hu/{url_suffix}",
        "title": "Official methodology",
        "review_status": status,
        "official": True,
        "applicability_status": "VALIDATED_2024_2025",
        "local_path": str(path),
        "sha256": digest,
        "relevant_location": "section 2.1",
        "supported_links": links,
        "effective_start": "2024-01-01",
        "effective_end": "2025-12-31",
    }


def test_complete_applicable_chain_validates_and_keeps_narrow_scope(
    tmp_path: Path,
) -> None:
    source = tmp_path / "methodology.pdf"
    source.write_bytes(b"official scope methodology")
    result = build_research_ledger(
        findings(
            [
                candidate(
                    source,
                    links=[
                        "B_report_completeness",
                        "C_row_inclusion",
                        "D_zero_transaction_omission",
                        "transaction_count_semantics",
                    ],
                )
            ]
        )
    )

    assert result["research_status"] == "REPORT_SCOPE_SEMANTICS_VALIDATED"
    assert result["absence_semantics_validated"] is True
    assert result["accepted_document_count"] == 1


def test_a_only_or_a_b_c_without_d_remain_partial(tmp_path: Path) -> None:
    source = tmp_path / "methodology.pdf"
    source.write_bytes(b"official scope methodology")

    a_only = build_research_ledger(
        findings([candidate(source, links=["A_report_scope"])])
    )
    missing_d = build_research_ledger(
        findings(
            [
                candidate(
                    source,
                    links=["B_report_completeness", "C_row_inclusion"],
                    url_suffix="other.pdf",
                )
            ]
        )
    )

    assert a_only["research_status"] == "REPORT_SCOPE_SEMANTICS_PARTIAL"
    assert missing_d["research_status"] == "REPORT_SCOPE_SEMANTICS_PARTIAL"
    assert missing_d["absence_semantics_validated"] is False


def test_completed_bounded_search_with_no_accepted_evidence_is_not_found() -> None:
    result = build_research_ledger(findings([]))

    assert result["research_status"] == "REPORT_SCOPE_SEMANTICS_NOT_FOUND"
    assert result["stopping_rule_completed"] is True
    assert result["absence_semantics_validated"] is False


def test_conflicting_applicable_authoritative_evidence_fails_closed(
    tmp_path: Path,
) -> None:
    source = tmp_path / "conflict.pdf"
    source.write_bytes(b"conflicting source")
    result = build_research_ledger(
        findings(
            [
                candidate(
                    source,
                    links=["B_report_completeness"],
                    status="CONFLICTING",
                )
            ]
        )
    )

    assert result["research_status"] == "REPORT_SCOPE_SEMANTICS_CONFLICT"
    assert result["absence_semantics_validated"] is False


def test_non_applicable_or_unofficial_source_cannot_satisfy_a_link(
    tmp_path: Path,
) -> None:
    source = tmp_path / "unofficial.pdf"
    source.write_bytes(b"unofficial source")
    item = candidate(source, links=["B_report_completeness"])
    item["review_status"] = "INSUFFICIENT"
    item["official"] = False
    item["applicability_status"] = "APPLICABILITY_NOT_VALIDATED"
    item["local_path"] = None
    item["sha256"] = None
    item["relevant_location"] = None
    item["effective_start"] = None
    item["effective_end"] = None

    result = build_research_ledger(findings([item]))

    assert result["research_status"] == "REPORT_SCOPE_SEMANTICS_NOT_FOUND"
    remaining = result["remaining_gaps"]
    assert isinstance(remaining, list)
    assert "B_report_completeness" in remaining


@pytest.mark.parametrize(
    "mutation",
    ["missing_file", "changed_hash", "missing_location", "outside_interval"],
)
def test_promoting_source_requires_retention_hash_location_and_applicability(
    tmp_path: Path, mutation: str
) -> None:
    source = tmp_path / "methodology.pdf"
    source.write_bytes(b"official source")
    item = candidate(source, links=["B_report_completeness"])
    if mutation == "missing_file":
        item["local_path"] = str(tmp_path / "missing.pdf")
    elif mutation == "changed_hash":
        item["sha256"] = "a" * 64
    elif mutation == "missing_location":
        item["relevant_location"] = None
    else:
        item["effective_start"] = "2025-01-01"

    with pytest.raises(ScopeResearchError):
        build_research_ledger(findings([item]))


def test_stopping_rules_reject_incomplete_coverage_duplicate_and_unbounded_input() -> None:
    missing_family = findings([])
    missing_family["source_families"] = ["KELER_PRIMARY"]
    with pytest.raises(ScopeResearchError, match="source family"):
        build_research_ledger(missing_family)

    duplicate = findings([])
    duplicate["candidate_documents"] = [
        {
            "source_family": "KELER_PRIMARY",
            "authority": "KELER",
            "host": "www.keler.hu",
            "source_url": "https://www.keler.hu/one",
            "title": "One",
            "review_status": "INSUFFICIENT",
            "official": True,
            "applicability_status": "APPLICABILITY_NOT_VALIDATED",
            "supported_links": [],
        }
    ] * 2
    with pytest.raises(ScopeResearchError, match="Duplicate candidate URL"):
        build_research_ledger(duplicate)

    oversized = findings([])
    oversized["candidate_documents"] = [{}] * (MAX_CANDIDATE_DOCUMENTS + 1)
    with pytest.raises(ScopeResearchError, match="candidate limit"):
        build_research_ledger(oversized)


def test_accepted_evidence_period_covers_target_interval(tmp_path: Path) -> None:
    source = tmp_path / "methodology.pdf"
    source.write_bytes(b"official source")
    item = candidate(source, links=["B_report_completeness"])
    item["effective_start"] = date(2024, 7, 3).isoformat()

    with pytest.raises(ScopeResearchError, match="effective interval"):
        build_research_ledger(findings([item]))
