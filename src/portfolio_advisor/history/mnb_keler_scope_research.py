"""Bounded, fail-closed evidence aggregation for MNB/KELER OTC scope research.

This module never performs network I/O.  A separately performed, bounded
official-source search supplies a discovery ledger; only locally retained,
applicable, authoritative documents can satisfy an evidence-chain link.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path

REQUIRED_SOURCE_FAMILIES = frozenset(
    {
        "KELER_PRIMARY",
        "MNB_PRIMARY",
        "REGULATION_RULEBOOK",
        "BET_OFFICIAL",
        "OFFICIAL_ARCHIVE",
    }
)
REQUIRED_QUERY_FAMILIES = frozenset(
    {
        "report_scope",
        "report_completeness",
        "row_inclusion",
        "zero_row_omission",
        "tetelszam",
        "transaction_universe",
        "otc_settlement_reporting",
        "weekly_report_methodology",
    }
)
MAX_QUERY_FAMILIES = 20
MAX_CANDIDATE_DOCUMENTS = 40
TARGET_START = date(2024, 7, 2)
TARGET_END = date(2025, 6, 4)
EVIDENCE_LINKS = frozenset(
    {
        "A_report_scope",
        "B_report_completeness",
        "C_row_inclusion",
        "D_zero_transaction_omission",
        "transaction_count_semantics",
    }
)


class ScopeResearchError(RuntimeError):
    """A research ledger is not reproducible or does not meet stopping rules."""


@dataclass(frozen=True, slots=True)
class ScopeResearchCandidate:
    """One discovered official-document candidate and its fail-closed review."""

    source_family: str
    authority: str
    host: str
    source_url: str
    title: str
    review_status: str
    official: bool
    applicability_status: str
    local_path: str | None = None
    sha256: str | None = None
    relevant_location: str | None = None
    supported_links: frozenset[str] = frozenset()
    publication_date: date | None = None
    effective_start: date | None = None
    effective_end: date | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if self.source_family not in REQUIRED_SOURCE_FAMILIES:
            raise ScopeResearchError("Candidate has an unknown source family")
        if not self.authority or not self.host or not self.source_url or not self.title:
            raise ScopeResearchError("Candidate provenance is incomplete")
        if not self.source_url.startswith("https://"):
            raise ScopeResearchError("Candidate source URL must use HTTPS")
        if self.review_status not in {
            "ACCEPTED_EVIDENCE",
            "PARTIAL_EVIDENCE",
            "NOT_APPLICABLE",
            "INSUFFICIENT",
            "CONFLICTING",
            "DUPLICATE",
        }:
            raise ScopeResearchError("Candidate has an unknown review status")
        if not self.supported_links.issubset(EVIDENCE_LINKS):
            raise ScopeResearchError("Candidate supports an unknown evidence link")
        if self.review_status in {"ACCEPTED_EVIDENCE", "CONFLICTING"}:
            if not self.official or not self.local_path or not self.sha256:
                raise ScopeResearchError(
                    "Promoting or conflicting evidence must be official and locally retained"
                )
            if not self.relevant_location:
                raise ScopeResearchError(
                    "Promoting or conflicting evidence requires an exact relevant location"
                )
            if self.applicability_status != "VALIDATED_2024_2025":
                raise ScopeResearchError(
                    "Promoting or conflicting evidence must apply to the target period"
                )
            if (
                self.effective_start is None
                or self.effective_end is None
                or self.effective_start > TARGET_START
                or self.effective_end < TARGET_END
            ):
                raise ScopeResearchError(
                    "Promoting or conflicting evidence has no applicable effective interval"
                )
        if (
            self.effective_start is not None
            and self.effective_end is not None
            and self.effective_start > self.effective_end
        ):
            raise ScopeResearchError("Candidate effective interval is reversed")

    @property
    def usable_for_promotion(self) -> bool:
        return self.review_status == "ACCEPTED_EVIDENCE"


def _date(value: object, field: str) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ScopeResearchError(f"{field} must be an ISO date or null")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ScopeResearchError(f"{field} must be an ISO date") from exc


def candidate_from_dict(value: object) -> ScopeResearchCandidate:
    """Parse one deterministic discovery result; it is not evidence by itself."""
    if not isinstance(value, dict):
        raise ScopeResearchError("Research candidate must be an object")
    required_strings = (
        "source_family",
        "authority",
        "host",
        "source_url",
        "title",
        "review_status",
        "applicability_status",
    )
    if any(not isinstance(value.get(field), str) for field in required_strings):
        raise ScopeResearchError("Research candidate has malformed text fields")
    official = value.get("official")
    if not isinstance(official, bool):
        raise ScopeResearchError("Research candidate official flag is malformed")
    raw_links = value.get("supported_links", [])
    if not isinstance(raw_links, list) or not all(
        isinstance(link, str) for link in raw_links
    ):
        raise ScopeResearchError("Research candidate evidence links are malformed")
    optional_strings = ("local_path", "sha256", "relevant_location", "notes")
    if any(
        value.get(field) is not None and not isinstance(value.get(field), str)
        for field in optional_strings
    ):
        raise ScopeResearchError("Research candidate optional provenance is malformed")
    return ScopeResearchCandidate(
        source_family=value["source_family"],
        authority=value["authority"],
        host=value["host"],
        source_url=value["source_url"],
        title=value["title"],
        review_status=value["review_status"],
        official=official,
        applicability_status=value["applicability_status"],
        local_path=value.get("local_path"),
        sha256=value.get("sha256"),
        relevant_location=value.get("relevant_location"),
        supported_links=frozenset(raw_links),
        publication_date=_date(value.get("publication_date"), "publication_date"),
        effective_start=_date(value.get("effective_start"), "effective_start"),
        effective_end=_date(value.get("effective_end"), "effective_end"),
        notes=value.get("notes", ""),
    )


def _validate_retained_candidate(candidate: ScopeResearchCandidate) -> None:
    if not candidate.usable_for_promotion:
        return
    assert candidate.local_path is not None
    assert candidate.sha256 is not None
    path = Path(candidate.local_path)
    if not path.is_file():
        raise ScopeResearchError(f"Accepted evidence source is missing: {path}")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != candidate.sha256:
        raise ScopeResearchError(f"Accepted evidence source hash changed: {path.name}")


def _link_status(
    link: str, candidates: Iterable[ScopeResearchCandidate], *, baseline: bool = False
) -> dict[str, object]:
    evidence = [
        candidate
        for candidate in candidates
        if candidate.usable_for_promotion and link in candidate.supported_links
    ]
    if baseline:
        return {"answer": "YES", "candidate_urls": [], "baseline_evidence": True}
    return {
        "answer": "YES" if evidence else "UNKNOWN",
        "candidate_urls": [candidate.source_url for candidate in evidence],
        "baseline_evidence": False,
    }


def _candidate_dict(candidate: ScopeResearchCandidate) -> dict[str, object]:
    return {
        "source_family": candidate.source_family,
        "authority": candidate.authority,
        "host": candidate.host,
        "source_url": candidate.source_url,
        "title": candidate.title,
        "status": candidate.review_status,
        "official": candidate.official,
        "applicability_status": candidate.applicability_status,
        "local_path": candidate.local_path,
        "sha256": candidate.sha256,
        "relevant_location": candidate.relevant_location,
        "supported_links": sorted(candidate.supported_links),
        "publication_date": candidate.publication_date.isoformat()
        if candidate.publication_date
        else None,
        "effective_start": candidate.effective_start.isoformat()
        if candidate.effective_start
        else None,
        "effective_end": candidate.effective_end.isoformat()
        if candidate.effective_end
        else None,
        "notes": candidate.notes,
    }


def build_research_ledger(findings: Mapping[str, object]) -> dict[str, object]:
    """Aggregate finite discovery findings without promoting unretained material."""
    source_families = findings.get("source_families")
    query_families = findings.get("query_families")
    raw_candidates = findings.get("candidate_documents")
    if not isinstance(source_families, list) or not all(
        isinstance(item, str) for item in source_families
    ):
        raise ScopeResearchError("Research source families are malformed")
    if set(source_families) != REQUIRED_SOURCE_FAMILIES:
        raise ScopeResearchError("Research did not cover every required source family")
    if not isinstance(query_families, list) or not all(
        isinstance(item, str) for item in query_families
    ):
        raise ScopeResearchError("Research query families are malformed")
    if len(query_families) > MAX_QUERY_FAMILIES or not REQUIRED_QUERY_FAMILIES.issubset(
        query_families
    ):
        raise ScopeResearchError("Research query coverage does not meet bounded rule")
    if not isinstance(raw_candidates, list) or len(raw_candidates) > MAX_CANDIDATE_DOCUMENTS:
        raise ScopeResearchError("Research candidate limit is invalid or exceeded")
    candidates = tuple(candidate_from_dict(item) for item in raw_candidates)
    urls = [candidate.source_url for candidate in candidates]
    if len(urls) != len(set(urls)):
        raise ScopeResearchError("Duplicate candidate URL must be recorded once")
    accepted_hashes = [
        candidate.sha256 for candidate in candidates if candidate.usable_for_promotion
    ]
    if len(accepted_hashes) != len(set(accepted_hashes)):
        raise ScopeResearchError("Duplicate accepted document hash must be recorded once")
    for candidate in candidates:
        _validate_retained_candidate(candidate)
    accepted = tuple(candidate for candidate in candidates if candidate.usable_for_promotion)
    conflicts = tuple(
        candidate for candidate in candidates if candidate.review_status == "CONFLICTING"
    )
    links = {
        "A_report_scope": _link_status(
            "A_report_scope", accepted, baseline=True
        ),
        "B_report_completeness": _link_status("B_report_completeness", accepted),
        "C_row_inclusion": _link_status("C_row_inclusion", accepted),
        "D_zero_transaction_omission": _link_status(
            "D_zero_transaction_omission", accepted
        ),
        "transaction_count_semantics": _link_status(
            "transaction_count_semantics", accepted
        ),
    }
    mandatory_answers = [
        links["A_report_scope"]["answer"],
        links["B_report_completeness"]["answer"],
        links["C_row_inclusion"]["answer"],
        links["D_zero_transaction_omission"]["answer"],
    ]
    if conflicts:
        status = "REPORT_SCOPE_SEMANTICS_CONFLICT"
    elif all(answer == "YES" for answer in mandatory_answers):
        status = "REPORT_SCOPE_SEMANTICS_VALIDATED"
    elif not accepted:
        status = "REPORT_SCOPE_SEMANTICS_NOT_FOUND"
    else:
        status = "REPORT_SCOPE_SEMANTICS_PARTIAL"
    remaining = [
        name for name, value in links.items() if value["answer"] != "YES"
    ]
    return {
        "schema_version": 1,
        "research_status": status,
        "stopping_rule_completed": True,
        "source_families_searched": sorted(source_families),
        "query_families_searched": sorted(query_families),
        "hard_limits": {
            "maximum_query_families": MAX_QUERY_FAMILIES,
            "maximum_candidate_documents": MAX_CANDIDATE_DOCUMENTS,
            "opaque_id_bruteforce_used": False,
            "unbounded_pagination_used": False,
        },
        "candidate_document_count": len(candidates),
        "candidate_documents": [_candidate_dict(candidate) for candidate in candidates],
        "accepted_document_count": len(accepted),
        "accepted_documents": [_candidate_dict(candidate) for candidate in accepted],
        "evidence_chain": links,
        "absence_semantics_validated": status
        == "REPORT_SCOPE_SEMANTICS_VALIDATED",
        "remaining_gaps": remaining,
        "conflicts": [_candidate_dict(candidate) for candidate in conflicts],
        "research_conclusion": (
            "No additional locally retained, applicable authoritative methodology "
            "document established a missing mandatory absence-semantics link."
            if status == "REPORT_SCOPE_SEMANTICS_NOT_FOUND"
            else "See evidence-chain links and candidate review records."
        ),
    }
