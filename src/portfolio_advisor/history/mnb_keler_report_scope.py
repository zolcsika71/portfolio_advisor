"""Fail-closed evidence chain for MNB/KELER weekly OTC report scope semantics."""

from __future__ import annotations

from dataclasses import dataclass

from .sparse_trading_semantics import (
    NO_EXACT_ISIN_OBSERVATION,
    NO_REPORTED_KELER_OTC_ACTIVITY,
    OBSERVED_OTC_ACTIVITY,
    REPORT_CONFLICT,
    STRUCTURAL_UNCERTAINTY,
)


class ReportScopeSemanticsError(RuntimeError):
    """Report-scope evidence is insufficient, inconsistent, or untrustworthy."""


@dataclass(frozen=True, slots=True)
class ScopeEvidenceDocument:
    """Locally retained authoritative evidence, with host and issuer separated."""

    filename: str
    sha256: str
    authority: str
    host: str
    locally_retained: bool
    official: bool

    def __post_init__(self) -> None:
        if not self.filename or len(self.sha256) != 64 or not self.authority or not self.host:
            raise ReportScopeSemanticsError("Scope evidence provenance is incomplete")
        if not self.locally_retained or not self.official:
            raise ReportScopeSemanticsError(
                "Only locally retained authoritative evidence can promote report semantics"
            )


@dataclass(frozen=True, slots=True)
class ReportScopeEvidenceChain:
    """Each link required before absence can receive a no-reported-activity meaning."""

    report_scope_validated: bool
    report_completeness_validated: bool
    row_inclusion_rule_validated: bool
    zero_transaction_omission_validated: bool
    transaction_count_semantics_validated: bool
    conflicting_authoritative_evidence: bool = False
    documents: tuple[ScopeEvidenceDocument, ...] = ()
    bounded_research_completed: bool = False
    no_additional_adequate_evidence_found: bool = False

    @property
    def absence_semantics_validated(self) -> bool:
        return (
            not self.conflicting_authoritative_evidence
            and self.report_scope_validated
            and self.report_completeness_validated
            and self.row_inclusion_rule_validated
            and self.zero_transaction_omission_validated
            and self.transaction_count_semantics_validated
            and bool(self.documents)
        )

    @property
    def semantic_status(self) -> str:
        if self.conflicting_authoritative_evidence:
            return "REPORT_SCOPE_SEMANTICS_CONFLICT"
        if self.absence_semantics_validated:
            return "REPORT_SCOPE_SEMANTICS_VALIDATED"
        if (
            self.bounded_research_completed
            and self.no_additional_adequate_evidence_found
        ):
            return "REPORT_SCOPE_SEMANTICS_NOT_FOUND"
        if any(
            (
                self.report_scope_validated,
                self.report_completeness_validated,
                self.row_inclusion_rule_validated,
                self.zero_transaction_omission_validated,
                self.transaction_count_semantics_validated,
            )
        ):
            return "REPORT_SCOPE_SEMANTICS_PARTIAL"
        return "REPORT_SCOPE_SEMANTICS_UNKNOWN"


def classify_report_row(
    *,
    structure_valid: bool,
    exact_isin_present: bool,
    chain: ReportScopeEvidenceChain,
) -> str:
    """Classify a report without constructing an absent-security observation."""
    if chain.conflicting_authoritative_evidence:
        return REPORT_CONFLICT
    if not structure_valid:
        return STRUCTURAL_UNCERTAINTY
    if exact_isin_present:
        return OBSERVED_OTC_ACTIVITY
    if chain.absence_semantics_validated:
        return NO_REPORTED_KELER_OTC_ACTIVITY
    return NO_EXACT_ISIN_OBSERVATION
