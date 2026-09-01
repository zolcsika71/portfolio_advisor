"""Exact normalized allocation derived only from reviewed policy values."""

from __future__ import annotations

from portfolio_advisor.canonical import canonical_fingerprint
from portfolio_advisor.objectives import CapitalDefensiveConstructionPolicy

from .constraints import policy_decimal
from .models import ConstructedHolding, RankedConstructionInstrument


def allocate_holding(
    item: RankedConstructionInstrument,
    policy: CapitalDefensiveConstructionPolicy,
) -> ConstructedHolding:
    """Create one fixed-weight holding; quantities and private cash remain absent."""
    group = item.group
    if group is None:
        raise ValueError("conflict-free category evidence is required")
    evidence_fingerprint = canonical_fingerprint(item.fingerprint_payload())
    return ConstructedHolding(
        instrument_id=item.instrument_id,
        isin=item.isin,
        canonical_name=item.canonical_name,
        rank=item.rank,
        weight=policy_decimal(policy, "allocation", "weight_per_security"),
        currency=item.currency,
        group=group,
        shortlist_entry_id=item.shortlist_entry_id,
        source_occurrence_ids=item.source_occurrence_ids,
        constraint_evidence_fingerprint=evidence_fingerprint,
    )
