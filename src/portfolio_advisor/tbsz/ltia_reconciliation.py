"""Read-only LTIA identity reconciliation over legacy-named TBSZ evidence."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

from portfolio_advisor.audit.milestone_4 import is_valid_isin
from portfolio_advisor.tbsz.repository import TbszPortfolioRepository


class LtiaIdentityStatus(StrEnum):
    CONFIRMED_EXPLICIT_ISIN = "CONFIRMED_EXPLICIT_ISIN"
    CONFIRMED_MANUAL_ALIAS = "CONFIRMED_MANUAL_ALIAS"
    CONFIRMED_UNIQUE_EXACT_NAME = "CONFIRMED_UNIQUE_EXACT_NAME"
    IDENTITY_CANDIDATE = "IDENTITY_CANDIDATE"
    AMBIGUOUS_IDENTITY = "AMBIGUOUS_IDENTITY"
    UNRESOLVED_IDENTITY = "UNRESOLVED_IDENTITY"
    CONFLICTING_IDENTITY = "CONFLICTING_IDENTITY"


@dataclass(frozen=True, slots=True)
class IdentityResolution:
    source_name: str
    normalized_name: str
    isin: str | None
    status: LtiaIdentityStatus
    rule: str
    candidates: tuple[str, ...]
    provenance: str


def normalize_name(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())


class IdentityResolver:
    """Deterministic exact-only resolver; fuzzy candidates never confirm identity."""

    def __init__(self, canonical: dict[str, set[str]], manual: dict[str, str] | None = None) -> None:
        self.canonical = {normalize_name(name): tuple(sorted(values)) for name, values in canonical.items()}
        self.manual = {normalize_name(name): isin for name, isin in (manual or {}).items()}

    def resolve(self, source_name: str, explicit_isin: str | None = None, fuzzy_candidates: tuple[str, ...] = ()) -> IdentityResolution:
        normalized = normalize_name(source_name)
        if explicit_isin is not None:
            isin = explicit_isin.strip().upper()
            if not is_valid_isin(isin):
                return IdentityResolution(source_name, normalized, None, LtiaIdentityStatus.CONFLICTING_IDENTITY, "INVALID_EXPLICIT_ISIN", (), "source")
            return IdentityResolution(source_name, normalized, isin, LtiaIdentityStatus.CONFIRMED_EXPLICIT_ISIN, "EXPLICIT_ISIN", (isin,), "source")
        if normalized in self.manual:
            isin = self.manual[normalized]
            return IdentityResolution(source_name, normalized, isin, LtiaIdentityStatus.CONFIRMED_MANUAL_ALIAS, "MANUAL_ALIAS", (isin,), "manual_confirmation")
        candidates = self.canonical.get(normalized, ())
        if len(candidates) == 1:
            return IdentityResolution(source_name, normalized, candidates[0], LtiaIdentityStatus.CONFIRMED_UNIQUE_EXACT_NAME, "UNIQUE_EXACT_NAME", candidates, "canonical_registry")
        if len(candidates) > 1:
            return IdentityResolution(source_name, normalized, None, LtiaIdentityStatus.AMBIGUOUS_IDENTITY, "MULTIPLE_EXACT_CANDIDATES", candidates, "canonical_registry")
        if fuzzy_candidates:
            return IdentityResolution(source_name, normalized, None, LtiaIdentityStatus.IDENTITY_CANDIDATE, "FUZZY_REVIEW_ONLY", tuple(sorted(fuzzy_candidates)), "review_only")
        return IdentityResolution(source_name, normalized, None, LtiaIdentityStatus.UNRESOLVED_IDENTITY, "NO_EXACT_EVIDENCE", (), "source")


def validate_manual_confirmation(
    *, source_name: str, selected_isin: str, canonical_isins: set[str], existing: dict[str, str], apply: bool = False,
) -> dict[str, Any]:
    """Validate an idempotent future confirmation without writing by default."""
    normalized = normalize_name(source_name)
    isin = selected_isin.strip().upper()
    if not is_valid_isin(isin) or isin not in canonical_isins:
        raise ValueError("selected ISIN is not in the canonical registry")
    prior = existing.get(normalized)
    if prior is not None and prior != isin:
        raise ValueError("contradictory confirmation")
    return {"status": "DRY_RUN_VALID" if not apply else "APPLY_NOT_AUTHORIZED", "normalized_source_name": normalized, "isin": isin, "idempotent": prior == isin}


def apply_confirmation_store(
    path: Path,
    confirmations: dict[str, str],
    *,
    apply: bool,
    provenance: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Atomically write a separate local identity store only with explicit apply."""
    canonical = {normalize_name(name): isin for name, isin in confirmations.items()}
    existing: dict[str, Any] = {}
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
    existing_records = existing.get("confirmation_records", {})
    records: dict[str, dict[str, Any]] = {}
    for name, isin in canonical.items():
        prior = existing_records.get(name)
        if prior is not None and prior.get("isin") == isin:
            records[name] = prior
        else:
            records[name] = {
                "isin": isin,
                "confirmed_by": "USER_APPROVED_MILESTONE_6_GATE",
                "confirmed_at": datetime.now(UTC).isoformat(),
                **(provenance or {}).get(name, {}),
            }
    payload = {"schema_version": 1, "mappings": canonical, "confirmation_records": records}
    encoded = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    if not apply:
        return {"status": "DRY_RUN", "mapping_count": len(canonical), "fingerprint": hashlib.sha256(encoded.encode()).hexdigest()}
    if path.exists():
        if existing == payload:
            return {"status": "IDEMPOTENT_NO_CHANGE", "mapping_count": len(canonical), "fingerprint": hashlib.sha256(encoded.encode()).hexdigest()}
        backup = path.with_name(path.name + ".backup")
        if backup.exists():
            raise ValueError("refusing to replace an existing confirmation backup")
        backup.write_bytes(path.read_bytes())
        if hashlib.sha256(backup.read_bytes()).hexdigest() != hashlib.sha256(path.read_bytes()).hexdigest():
            raise RuntimeError("confirmation backup verification failed")
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(encoded, encoding="utf-8")
    temporary.replace(path)
    return {"status": "APPLIED", "mapping_count": len(canonical), "fingerprint": hashlib.sha256(encoded.encode()).hexdigest()}


@dataclass(frozen=True, slots=True)
class LtiaPosition:
    account: str
    source_snapshot_ids: tuple[int, ...]
    source_date: date | None
    source_name: str
    isin: str | None
    status: LtiaIdentityStatus
    quantity: Decimal | None
    unit_price: Decimal | None
    market_value: Decimal | None
    currency: str | None
    observed_roi: Decimal | None


def classify_equivalence(left: dict[str, Any], right: dict[str, Any]) -> str:
    if left["content_sha256"] == right["content_sha256"]:
        return "BYTE_IDENTICAL_SOURCE"
    keys = ("account", "view_type", "source_date", "evidence_fingerprint", "positions", "cash")
    return "SEMANTICALLY_EQUIVALENT_SOURCE" if all(left.get(key) == right.get(key) for key in keys) else "CONFLICTING_SOURCE_SNAPSHOT"


def project_current_positions(
    positions: tuple[LtiaPosition, ...], *, precedence_proven: bool,
) -> tuple[tuple[LtiaPosition, ...], tuple[dict[str, Any], ...]]:
    """Return account rows and ISIN-only consolidated rows without FX conversion."""
    if not precedence_proven:
        return (), ({"status": "UNRESOLVED_CURRENT_STATE_PRECEDENCE"},)
    grouped: dict[str, list[LtiaPosition]] = defaultdict(list)
    unresolved: list[dict[str, Any]] = []
    for item in positions:
        if item.isin is None:
            unresolved.append({"status": "UNRESOLVED_IDENTITY_NOT_AGGREGATED", "account": item.account, "source_snapshot_ids": item.source_snapshot_ids})
        else:
            grouped[item.isin].append(item)
    consolidated: list[dict[str, Any]] = []
    for isin, items in sorted(grouped.items()):
        currencies = {item.currency for item in items}
        values = [item.market_value for item in items]
        aggregate = sum((value for value in values if value is not None), Decimal()) if len(currencies) == 1 and None not in currencies and all(value is not None for value in values) else None
        consolidated.append({"isin": isin, "market_value": aggregate, "currency": next(iter(currencies)) if len(currencies) == 1 else None, "source_snapshot_ids": tuple(sorted({sid for item in items for sid in item.source_snapshot_ids})), "contributing_accounts": tuple(sorted({item.account for item in items}))})
    return positions, (*consolidated, *unresolved)


def audit_ltia_read_only(path: Path) -> dict[str, Any]:
    """Aggregate-only audit of legacy local evidence; never opens it for write."""
    repository = TbszPortfolioRepository(path)
    snapshots = repository.source_snapshots()
    positions = [position for snapshot in snapshots for position in repository.positions_for_snapshot(snapshot.snapshot_id)]
    cash = [item for snapshot in snapshots for item in repository.cash_for_snapshot(snapshot.snapshot_id)]
    statuses: dict[str, int] = defaultdict(int)
    for position in positions:
        statuses[position.instrument.identity_status.value] += 1
    equivalent: list[dict[str, Any]] = []
    groups: dict[tuple[int, str, str | None, str], list[Any]] = defaultdict(list)
    for snapshot in snapshots:
        groups[(snapshot.account_id, snapshot.view_type, snapshot.source_date.isoformat() if snapshot.source_date else None, snapshot.evidence_fingerprint)].append(snapshot)
    for group in groups.values():
        if len(group) > 1:
            equivalent.append({"snapshot_ids": [item.snapshot_id for item in group], "classification": "SEMANTICALLY_EQUIVALENT_SOURCE", "undated": group[0].source_date is None})
    summary = {"accounts": len(repository.accounts()), "source_snapshots": len(snapshots), "positions": len(positions), "cash": len(cash), "transactions": len(repository.transactions()), "identity_status_counts": dict(sorted(statuses.items())), "equivalent_groups": equivalent}
    summary["fingerprint"] = hashlib.sha256(json.dumps(summary, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    return summary
