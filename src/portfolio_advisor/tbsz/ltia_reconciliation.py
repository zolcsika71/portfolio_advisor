"""Read-only LTIA identity reconciliation over legacy-named TBSZ evidence."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Mapping
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


@dataclass(frozen=True, slots=True)
class ValidatedIdentityConfirmation:
    """One approved exact-name mapping with validated retained provenance."""

    normalized_source_name: str
    isin: str
    approved_currency: str
    confirmed_by: str
    confirmed_at: str
    rule: str
    source_support: str
    store_fingerprint: str
    registry_audit_fingerprint: str


@dataclass(frozen=True, slots=True)
class ValidatedIdentityConfirmationStore:
    """Usable records plus aggregate fail-closed validation blockers."""

    confirmations: Mapping[str, ValidatedIdentityConfirmation]
    store_fingerprint: str | None
    registry_audit_fingerprint: str | None
    validation_blockers: tuple[str, ...]


def normalize_name(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())


def load_validated_identity_confirmations(
    store_path: Path | None,
    registry_audit_path: Path | None,
) -> ValidatedIdentityConfirmationStore:
    """Load only directly validated approvals; never alter either source.

    Every admitted mapping must retain the Milestone 6 approval fields and
    still resolve to one exact ISIN and currency in the canonical registry
    audit used by the existing confirmation workflow. Invalid records are
    rejected individually; invalid top-level contracts reject the whole store.
    """
    if store_path is None:
        return ValidatedIdentityConfirmationStore({}, None, None, ())
    if not store_path.is_file():
        return ValidatedIdentityConfirmationStore(
            {}, None, None, ("IDENTITY_CONFIRMATION_STORE_UNAVAILABLE",)
        )
    try:
        raw_store = store_path.read_bytes()
    except OSError:
        return ValidatedIdentityConfirmationStore(
            {}, None, None, ("IDENTITY_CONFIRMATION_STORE_UNREADABLE",)
        )
    fingerprint = hashlib.sha256(raw_store).hexdigest()
    try:
        payload = json.loads(raw_store)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ValidatedIdentityConfirmationStore(
            {}, fingerprint, None, ("IDENTITY_CONFIRMATION_STORE_INVALID_JSON",)
        )
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return ValidatedIdentityConfirmationStore(
            {}, fingerprint, None, ("IDENTITY_CONFIRMATION_STORE_SCHEMA_INVALID",)
        )
    mappings = payload.get("mappings")
    records = payload.get("confirmation_records")
    if not isinstance(mappings, dict) or not isinstance(records, dict):
        return ValidatedIdentityConfirmationStore(
            {}, fingerprint, None, ("IDENTITY_CONFIRMATION_STORE_SHAPE_INVALID",)
        )
    if set(mappings) != set(records):
        return ValidatedIdentityConfirmationStore(
            {}, fingerprint, None, ("IDENTITY_CONFIRMATION_RECORD_SET_MISMATCH",)
        )
    registry, registry_fingerprint, registry_blocker = _confirmation_registry(
        registry_audit_path
    )
    if registry_blocker is not None:
        return ValidatedIdentityConfirmationStore(
            {}, fingerprint, registry_fingerprint, (registry_blocker,)
        )
    assert registry_fingerprint is not None

    confirmations: dict[str, ValidatedIdentityConfirmation] = {}
    blockers: list[str] = []
    normalized_keys: set[str] = set()
    for key, raw_isin in mappings.items():
        if not isinstance(key, str) or normalize_name(key) != key or key in normalized_keys:
            blockers.append("IDENTITY_CONFIRMATION_KEY_INVALID")
            continue
        normalized_keys.add(key)
        if not isinstance(raw_isin, str) or not is_valid_isin(raw_isin):
            blockers.append("IDENTITY_CONFIRMATION_ISIN_INVALID")
            continue
        isin = raw_isin.strip().upper()
        record = records.get(key)
        if not isinstance(record, dict):
            blockers.append("IDENTITY_CONFIRMATION_RECORD_INVALID")
            continue
        record_blockers = _confirmation_record_blockers(key, isin, record)
        if record_blockers:
            blockers.extend(record_blockers)
            continue
        candidates = registry.get(key, ())
        candidate_isins = {candidate_isin for candidate_isin, _ in candidates}
        candidate_currencies = {
            currency for _, currency in candidates if currency is not None
        }
        if candidate_isins != {isin}:
            blockers.append("IDENTITY_CONFIRMATION_CANONICAL_IDENTITY_MISMATCH")
            continue
        if len(candidate_currencies) != 1:
            blockers.append("IDENTITY_CONFIRMATION_CANONICAL_CURRENCY_NOT_UNIQUE")
            continue
        confirmations[key] = ValidatedIdentityConfirmation(
            normalized_source_name=key,
            isin=isin,
            approved_currency=next(iter(candidate_currencies)),
            confirmed_by=str(record["confirmed_by"]),
            confirmed_at=str(record["confirmed_at"]),
            rule=str(record["rule"]),
            source_support=str(record["source_support"]),
            store_fingerprint=fingerprint,
            registry_audit_fingerprint=registry_fingerprint,
        )
    return ValidatedIdentityConfirmationStore(
        confirmations,
        fingerprint,
        registry_fingerprint,
        tuple(sorted(set(blockers))),
    )


def _confirmation_registry(
    path: Path | None,
) -> tuple[
    dict[str, tuple[tuple[str, str | None], ...]],
    str | None,
    str | None,
]:
    if path is None or not path.is_file():
        return {}, None, "IDENTITY_CONFIRMATION_REGISTRY_AUDIT_UNAVAILABLE"
    try:
        raw_registry = path.read_bytes()
        fingerprint = hashlib.sha256(raw_registry).hexdigest()
        payload = json.loads(raw_registry)
        files = payload["xls_inventory"]["files"]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
        return {}, None, "IDENTITY_CONFIRMATION_REGISTRY_AUDIT_INVALID"
    if not isinstance(files, list):
        return {}, fingerprint, "IDENTITY_CONFIRMATION_REGISTRY_AUDIT_INVALID"
    registry: dict[str, list[tuple[str, str | None]]] = defaultdict(list)
    try:
        for sheet in files:
            if not isinstance(sheet, dict) or not isinstance(sheet["identity_records"], list):
                return {}, fingerprint, "IDENTITY_CONFIRMATION_REGISTRY_AUDIT_INVALID"
            for record in sheet["identity_records"]:
                if not isinstance(record, dict):
                    return {}, fingerprint, "IDENTITY_CONFIRMATION_REGISTRY_AUDIT_INVALID"
                product = record.get("product_name")
                isin = record.get("isin")
                currency = record.get("currency")
                if product and isin:
                    registry[normalize_name(str(product))].append(
                        (str(isin).strip().upper(), str(currency) if currency else None)
                    )
    except (KeyError, TypeError):
        return {}, fingerprint, "IDENTITY_CONFIRMATION_REGISTRY_AUDIT_INVALID"
    return {key: tuple(values) for key, values in registry.items()}, fingerprint, None


def _confirmation_record_blockers(
    key: str, isin: str, record: dict[str, Any]
) -> tuple[str, ...]:
    blockers: list[str] = []
    if record.get("isin") != isin:
        blockers.append("IDENTITY_CONFIRMATION_RECORD_ISIN_CONFLICT")
    if record.get("confirmed_by") != "USER_APPROVED_MILESTONE_6_GATE":
        blockers.append("IDENTITY_CONFIRMATION_APPROVAL_INVALID")
    if record.get("rule") != "UNIQUE_EXACT_NORMALIZED_PROVIDER_NAME":
        blockers.append("IDENTITY_CONFIRMATION_RULE_INVALID")
    if record.get("candidate_count") != 1:
        blockers.append("IDENTITY_CONFIRMATION_CANDIDATE_COUNT_INVALID")
    if record.get("source_support") != "canonical_model_or_shortlist_registry":
        blockers.append("IDENTITY_CONFIRMATION_PROVENANCE_INVALID")
    if record.get("currency_checked") is not True:
        blockers.append("IDENTITY_CONFIRMATION_CURRENCY_APPROVAL_MISSING")
    if record.get("share_class_checked") is not True:
        blockers.append("IDENTITY_CONFIRMATION_SHARE_CLASS_APPROVAL_MISSING")
    if record.get("contradictory_product_evidence") is not False:
        blockers.append("IDENTITY_CONFIRMATION_CONTRADICTION_NOT_CLEARED")
    source_identity = record.get("source_identity")
    if not isinstance(source_identity, str) or normalize_name(source_identity) != key:
        blockers.append("IDENTITY_CONFIRMATION_SOURCE_PROVENANCE_MISMATCH")
    confirmed_at = record.get("confirmed_at")
    try:
        timestamp = (
            datetime.fromisoformat(confirmed_at) if isinstance(confirmed_at, str) else None
        )
    except ValueError:
        timestamp = None
    if timestamp is None or timestamp.tzinfo is None:
        blockers.append("IDENTITY_CONFIRMATION_TIMESTAMP_INVALID")
    return tuple(blockers)


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
