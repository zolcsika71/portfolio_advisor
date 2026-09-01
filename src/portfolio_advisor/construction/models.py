"""Immutable governed result models for capital-conservation construction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from portfolio_advisor.canonical import canonical_fingerprint, canonical_json


class ConstructionBoundaryStatus(StrEnum):
    """Stable statement that a later financial action was not performed."""

    NOT_PERFORMED = "NOT_PERFORMED"


@dataclass(frozen=True, slots=True)
class SourceLineage:
    """Complete schema-v3 provenance supporting one shortlist membership."""

    shortlist_entry_id: int
    source_occurrence_ids: tuple[int, ...]
    source_row_numbers: tuple[int, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "shortlist_entry_id": self.shortlist_entry_id,
            "source_occurrence_ids": list(self.source_occurrence_ids),
            "source_row_numbers": list(self.source_row_numbers),
        }


@dataclass(frozen=True, slots=True)
class RankedInstrument:
    """One policy evaluation, including rejected evidence for auditability."""

    instrument_id: int
    isin: str
    canonical_name: str
    eligible: bool
    rejection_reasons: tuple[str, ...]
    rank: int | None
    total_score: float | None
    feature_values: tuple[tuple[str, float | None], ...]
    weighted_contributions: tuple[tuple[str, float, float, float], ...]
    lineage: SourceLineage

    def to_dict(self) -> dict[str, object]:
        return {
            "canonical_name": self.canonical_name,
            "eligible": self.eligible,
            "feature_values": {name: value for name, value in self.feature_values},
            "instrument_id": self.instrument_id,
            "isin": self.isin,
            "lineage": self.lineage.to_dict(),
            "rank": self.rank,
            "rejection_reasons": list(self.rejection_reasons),
            "total_score": self.total_score,
            "weighted_contributions": [
                {
                    "contribution": contribution,
                    "metric": metric,
                    "normalized_value": normalized,
                    "raw_value": raw,
                }
                for metric, raw, normalized, contribution in self.weighted_contributions
            ],
        }


@dataclass(frozen=True, slots=True)
class ConstructionProvenance:
    """Governed identities and source snapshot used for one construction."""

    objective: str
    strategy: str
    construction_capability: str
    policy_id: str
    policy_version: str
    policy_fingerprint: str
    registry_fingerprint: str
    capability_states: tuple[tuple[str, str], ...]
    snapshot_id: int
    snapshot_date: date
    source_file: str
    source_file_sha256: str
    source_sheet_id: int
    source_sheet_name: str
    shortlist_manifest_fingerprint: str
    shortlist_integration_version: str

    def to_dict(self) -> dict[str, object]:
        return {
            "construction_capability": self.construction_capability,
            "capability_states": dict(self.capability_states),
            "objective": self.objective,
            "policy_fingerprint": self.policy_fingerprint,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "registry_fingerprint": self.registry_fingerprint,
            "shortlist_integration_version": self.shortlist_integration_version,
            "shortlist_manifest_fingerprint": self.shortlist_manifest_fingerprint,
            "snapshot_date": self.snapshot_date.isoformat(),
            "snapshot_id": self.snapshot_id,
            "source_file": self.source_file,
            "source_file_sha256": self.source_file_sha256,
            "source_sheet_id": self.source_sheet_id,
            "source_sheet_name": self.source_sheet_name,
            "strategy": self.strategy,
        }


@dataclass(frozen=True, slots=True)
class CapitalConservationShortlist:
    """Deprecated ranked-instrument result; never a constructed portfolio."""

    provenance: ConstructionProvenance
    candidates: tuple[RankedInstrument, ...]
    constructed: tuple[RankedInstrument, ...]
    ranking_warnings: tuple[str, ...]
    allocation_status: ConstructionBoundaryStatus = ConstructionBoundaryStatus.NOT_PERFORMED
    cash_deployment_status: ConstructionBoundaryStatus = ConstructionBoundaryStatus.NOT_PERFORMED
    fx_conversion_status: ConstructionBoundaryStatus = ConstructionBoundaryStatus.NOT_PERFORMED

    def fingerprint_payload(self) -> dict[str, object]:
        return {
            "allocation_status": self.allocation_status.value,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "cash_deployment_status": self.cash_deployment_status.value,
            "constructed": [candidate.to_dict() for candidate in self.constructed],
            "fx_conversion_status": self.fx_conversion_status.value,
            "provenance": self.provenance.to_dict(),
            "ranking_warnings": list(self.ranking_warnings),
        }

    @property
    def result_fingerprint(self) -> str:
        return canonical_fingerprint(self.fingerprint_payload())

    def to_dict(self) -> dict[str, object]:
        return {**self.fingerprint_payload(), "result_fingerprint": self.result_fingerprint}

    def canonical_json(self) -> str:
        return canonical_json(self.to_dict())


class ConstructionRuntimeStatus(StrEnum):
    """Governed result states for the real construction foundation."""

    CONSTRUCTED_VALIDATED = "CONSTRUCTED_VALIDATED"
    IMPLEMENTED_BLOCKED_BY_DATA = "IMPLEMENTED_BLOCKED_BY_DATA"
    UNAVAILABLE = "UNAVAILABLE"
    REJECTED = "REJECTED"


class ConstructionReasonCode(StrEnum):
    """Stable fail-closed reason identities; no fallback is implied."""

    INSUFFICIENT_ADMITTED_NAV_COVERAGE = "INSUFFICIENT_ADMITTED_NAV_COVERAGE"
    STALE_NAV = "STALE_NAV"
    INSUFFICIENT_NAV_HISTORY = "INSUFFICIENT_NAV_HISTORY"
    INSUFFICIENT_ALIGNED_RETURN_INTERVALS = "INSUFFICIENT_ALIGNED_RETURN_INTERVALS"
    NO_COMMON_ALIGNED_RETURN_WINDOW = "NO_COMMON_ALIGNED_RETURN_WINDOW"
    MISSING_OFFICIAL_REFERENCE_RATE_EVIDENCE = (
        "MISSING_OFFICIAL_REFERENCE_RATE_EVIDENCE"
    )
    UNAVAILABLE_PORTFOLIO_RISK_METRICS = "UNAVAILABLE_PORTFOLIO_RISK_METRICS"
    INSUFFICIENT_SAME_CURRENCY_INSTRUMENTS = "INSUFFICIENT_SAME_CURRENCY_INSTRUMENTS"
    NO_FEASIBLE_DIVERSIFIED_SET = "NO_FEASIBLE_DIVERSIFIED_SET"
    INVALID_CATEGORY_EVIDENCE = "INVALID_CATEGORY_EVIDENCE"
    INVALID_SCREENING_OR_LINEAGE_EVIDENCE = "INVALID_SCREENING_OR_LINEAGE_EVIDENCE"


@dataclass(frozen=True, slots=True)
class NavReadinessEvidence:
    """Exact dates and admission semantics needed to prove NAV constraints."""

    observation_dates: tuple[date, ...]
    quality: str
    interpolation_used: bool = False
    nearest_date_substitution_used: bool = False
    proxy_instrument_used: bool = False

    def fingerprint_payload(self) -> dict[str, object]:
        return {
            "interpolation_used": self.interpolation_used,
            "nearest_date_substitution_used": self.nearest_date_substitution_used,
            "observation_dates": [value.isoformat() for value in self.observation_dates],
            "proxy_instrument_used": self.proxy_instrument_used,
            "quality": self.quality,
        }


@dataclass(frozen=True, slots=True)
class RankedConstructionInstrument:
    """One reviewed screening result enriched with exact construction evidence."""

    instrument_id: int
    isin: str
    canonical_name: str
    rank: int
    screening_eligible: bool
    currency: str
    asset_class: str | None
    sub_asset_class: str | None
    category_conflict: bool
    shortlist_snapshot_id: int
    shortlist_entry_id: int
    source_occurrence_ids: tuple[int, ...]
    nav: NavReadinessEvidence

    @property
    def group(self) -> tuple[str, str] | None:
        if (
            self.category_conflict
            or self.asset_class is None
            or self.sub_asset_class is None
            or not self.asset_class.strip()
            or not self.sub_asset_class.strip()
        ):
            return None
        return (self.asset_class.strip(), self.sub_asset_class.strip())

    def fingerprint_payload(self) -> dict[str, object]:
        return {
            "asset_class": self.asset_class,
            "currency": self.currency,
            "group": list(self.group) if self.group is not None else None,
            "isin": self.isin,
            "nav": self.nav.fingerprint_payload(),
            "rank": self.rank,
            "screening_eligible": self.screening_eligible,
            "sub_asset_class": self.sub_asset_class,
        }


@dataclass(frozen=True, slots=True)
class ConstructionEvidenceReadiness:
    """Non-fabricated global evidence states supplied to one run."""

    official_reference_rate_observations_validated: bool
    official_reference_rate_methodology_validated: bool
    portfolio_risk_metrics_available: bool


@dataclass(frozen=True, slots=True)
class ShortlistConstructionProvenance:
    """Local FK plus stable source identity for the exact shortlist snapshot."""

    shortlist_snapshot_id: int
    snapshot_date: date
    source_file_sha256: str
    source_sheet_name: str
    shortlist_manifest_fingerprint: str
    shortlist_integration_version: str

    def stable_payload(self) -> dict[str, object]:
        return {
            "shortlist_integration_version": self.shortlist_integration_version,
            "shortlist_manifest_fingerprint": self.shortlist_manifest_fingerprint,
            "snapshot_date": self.snapshot_date.isoformat(),
            "source_file_sha256": self.source_file_sha256,
            "source_sheet_name": self.source_sheet_name,
        }


@dataclass(frozen=True, slots=True)
class ConstructedHolding:
    """One normalized 10% holding with exact membership lineage."""

    instrument_id: int
    isin: str
    canonical_name: str
    rank: int
    weight: Decimal
    currency: str
    group: tuple[str, str]
    shortlist_entry_id: int
    source_occurrence_ids: tuple[int, ...]
    constraint_evidence_fingerprint: str

    def fingerprint_payload(self) -> dict[str, object]:
        return {
            "constraint_evidence_fingerprint": self.constraint_evidence_fingerprint,
            "currency": self.currency,
            "group": list(self.group),
            "isin": self.isin,
            "rank": self.rank,
            "weight": format(self.weight, "f"),
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self.fingerprint_payload(),
            "canonical_name": self.canonical_name,
            "instrument_id": self.instrument_id,
            "shortlist_entry_id": self.shortlist_entry_id,
            "source_occurrence_ids": list(self.source_occurrence_ids),
        }


@dataclass(frozen=True, slots=True)
class ConstructedPortfolioCandidate:
    """Immutable normalized candidate; the private cash amount is intentionally absent."""

    objective: str
    strategy: str
    currency: str
    policy_id: str
    policy_version: str
    policy_fingerprint: str
    provenance: ShortlistConstructionProvenance
    eligible_universe_fingerprint: str
    selected_universe_fingerprint: str
    holdings: tuple[ConstructedHolding, ...]
    cash_weight: Decimal
    status: ConstructionRuntimeStatus = ConstructionRuntimeStatus.CONSTRUCTED_VALIDATED

    @property
    def portfolio_identity_fingerprint(self) -> str:
        return canonical_fingerprint(
            {
                "currency": self.currency,
                "objective": self.objective,
                "policy_id": self.policy_id,
                "policy_version": self.policy_version,
                "strategy": self.strategy,
            }
        )

    def fingerprint_payload(self) -> dict[str, object]:
        return {
            "cash": {"currency": self.currency, "weight": format(self.cash_weight, "f")},
            "eligible_universe_fingerprint": self.eligible_universe_fingerprint,
            "holdings": [holding.fingerprint_payload() for holding in self.holdings],
            "objective": self.objective,
            "policy_fingerprint": self.policy_fingerprint,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "portfolio_identity_fingerprint": self.portfolio_identity_fingerprint,
            "provenance": self.provenance.stable_payload(),
            "selected_universe_fingerprint": self.selected_universe_fingerprint,
            "status": self.status.value,
            "strategy": self.strategy,
        }

    @property
    def candidate_fingerprint(self) -> str:
        return canonical_fingerprint(self.fingerprint_payload())

    def to_dict(self) -> dict[str, object]:
        return {
            **self.fingerprint_payload(),
            "candidate_fingerprint": self.candidate_fingerprint,
            "holdings": [holding.to_dict() for holding in self.holdings],
            "shortlist_snapshot_id": self.provenance.shortlist_snapshot_id,
        }


@dataclass(frozen=True, slots=True)
class ConstructionResult:
    """One candidate or an explicit unavailable/rejected state."""

    status: ConstructionRuntimeStatus
    reason_codes: tuple[ConstructionReasonCode, ...]
    candidate: ConstructedPortfolioCandidate | None
    screened_eligible_count: int
    admitted_nav_instrument_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "admitted_nav_instrument_count": self.admitted_nav_instrument_count,
            "candidate": self.candidate.to_dict() if self.candidate is not None else None,
            "reason_codes": [reason.value for reason in self.reason_codes],
            "screened_eligible_count": self.screened_eligible_count,
            "status": self.status.value,
        }
