"""Immutable governed result models for capital-conservation construction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
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
    """Deterministic ranked universe; it is not a portfolio or recommendation."""

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
