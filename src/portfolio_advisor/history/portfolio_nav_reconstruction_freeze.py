"""Deterministic governance freeze for unapproved portfolio-NAV reconstruction."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

FREEZE_STATUS: Final = "PORTFOLIO_NAV_RECONSTRUCTION_FROZEN_UNRESOLVED"
RECONSTRUCTION_NOT_APPROVED: Final = "PORTFOLIO_NAV_RECONSTRUCTION_NOT_APPROVED"
ALLOWED_REOPEN_TRIGGERS: Final = frozenset(
    {
        "AUTHORITATIVE_ALLOCATION_SEMANTICS",
        "AUTHORITATIVE_REBALANCE_EFFECTIVE_DATE_RULE",
        "AUTHORITATIVE_PORTFOLIO_REPORTING_CURRENCY_RULE",
        "APPROVED_POINT_IN_TIME_FX_METHODOLOGY",
        "AUTHORITATIVE_TOTAL_RETURN_TREATMENT",
        "APPROVED_DUPLICATE_ROW_INTERPRETATION",
        "OFFICIAL_PORTFOLIO_NAV_HISTORY",
        "AUTHENTICATED_PROVIDER_PORTFOLIO_PERFORMANCE_EXPORT",
    }
)
REJECTED_REOPEN_TRIGGERS: Final = (
    "REPEATED_WEB_SEARCH",
    "GENERAL_FINANCE_CONVENTION",
    "GRAPHIFY_INFERENCE_WITHOUT_PORTFOLIO_SOURCE",
    "MORE_CONSTITUENT_NAV_OBSERVATIONS_ALONE",
    "MANUALLY_ENTERED_ASSUMPTION",
    "SYNTHETIC_PORTFOLIO_RETURN",
    "DUPLICATE_EXISTING_DOCUMENT",
)


class PortfolioNavReconstructionFrozenError(RuntimeError):
    """A caller attempted an unapproved synthetic portfolio-NAV path."""


@dataclass(frozen=True, slots=True)
class ReopenEvidence:
    """Evidence that can qualify a future governance-review request."""

    trigger: str
    authoritative: bool
    locally_retained: bool
    provenance_backed: bool
    applicable_historical_period: bool
    fingerprint_verified: bool
    fingerprint: str


def build_portfolio_nav_reconstruction_freeze(
    *,
    repository_root: Path,
    methodology_path: Path,
    blocker_resolution_path: Path,
    duplicate_resolution_path: Path,
    label_store_path: Path,
    feature_dataset_path: Path,
) -> dict[str, object]:
    """Build a freeze from exact retained evidence without altering any data source."""
    evidence_paths = (
        methodology_path,
        blocker_resolution_path,
        duplicate_resolution_path,
        label_store_path,
        feature_dataset_path,
    )
    evidence = [_evidence_reference(path, repository_root) for path in evidence_paths]
    by_path = {item["path"]: item for item in evidence}
    methodology = _load_object(methodology_path)
    resolution = _load_object(blocker_resolution_path)
    labels = _load_object(label_store_path)
    features = _load_object(feature_dataset_path)
    if methodology.get("validation_status") != "PORTFOLIO_NAV_METHODOLOGY_BLOCKED":
        raise PortfolioNavReconstructionFrozenError("portfolio-NAV methodology is not blocked")
    if methodology.get("activation_state") != "NOT_ACTIVATED":
        raise PortfolioNavReconstructionFrozenError("portfolio-NAV methodology activation is not disabled")
    if resolution.get("validation_status") not in {
        "PORTFOLIO_NAV_METHODOLOGY_BLOCKERS_PARTIALLY_RESOLVED",
        "PORTFOLIO_NAV_METHODOLOGY_BLOCKERS_UNRESOLVED",
    }:
        raise PortfolioNavReconstructionFrozenError("blocker resolution has an incompatible status")
    available_labels = _integer(labels.get("available_label_count"), "available_label_count")
    blockers = methodology.get("approval_blockers")
    if not isinstance(blockers, list) or not all(isinstance(item, str) for item in blockers):
        raise PortfolioNavReconstructionFrozenError("methodology approval blockers are malformed")
    payload: dict[str, object] = {
        "schema_version": 1,
        "status": "PORTFOLIO_NAV_RECONSTRUCTION_UNRESOLVED",
        "validation_status": FREEZE_STATUS,
        "research_closed": True,
        "reopen_allowed": True,
        "production_activation": False,
        "portfolio_nav_generation_allowed": False,
        "portfolio_forward_label_generation_allowed": False,
        "runtime_guard_status": RECONSTRUCTION_NOT_APPROVED,
        "evidence": evidence,
        "evidence_summary": {
            "methodology_status": methodology["validation_status"],
            "methodology_activation_state": methodology["activation_state"],
            "blocker_resolution_status": resolution["validation_status"],
            "official_portfolio_label_count": available_labels,
            "point_in_time_dataset_fingerprint": features.get("dataset_fingerprint"),
            "supporting_artifact_sha256": {
                "methodology": by_path[_relative(methodology_path, repository_root)]["sha256"],
                "blocker_resolution": by_path[_relative(blocker_resolution_path, repository_root)]["sha256"],
                "duplicate_resolution": by_path[_relative(duplicate_resolution_path, repository_root)]["sha256"],
                "label_store": by_path[_relative(label_store_path, repository_root)]["sha256"],
                "feature_dataset": by_path[_relative(feature_dataset_path, repository_root)]["sha256"],
            },
        },
        "blockers": sorted(blockers),
        "freeze_reason": "Retained evidence does not establish the economic assumptions required to reconstruct a portfolio NAV or portfolio forward return.",
        "valid_reopen_triggers": sorted(ALLOWED_REOPEN_TRIGGERS),
        "reopen_evidence_requirements": [
            "authoritative",
            "locally_retained",
            "provenance_backed",
            "applicable_historical_period",
            "fingerprint_verified",
            "not_a_duplicate_of_existing_evidence",
        ],
        "invalid_reopen_triggers": list(REJECTED_REOPEN_TRIGGERS),
        "special_case_references": {
            "HU0000554795": {
                "status": "BACKTEST_UNRESOLVABLE_WITH_CURRENT_PUBLIC_EVIDENCE",
                "prohibited": "MNB/KELER OTC values must not be converted to NAV.",
            },
            "AT0000605324": {
                "status": "RECONCILIATION_REQUIRED",
                "prohibited": "No conflicting source value may be selected automatically.",
            },
        },
        "scope": {
            "direct_official_portfolio_nav_reading": "NOT_BLOCKED",
            "constituent_nav_reading": "NOT_BLOCKED",
            "synthetic_constituent_to_portfolio_aggregation": "BLOCKED",
            "synthetic_portfolio_forward_label_generation": "BLOCKED",
        },
    }
    payload["freeze_fingerprint"] = _fingerprint(payload)
    return payload


def qualifies_as_reopen_evidence(
    evidence: ReopenEvidence,
    *,
    existing_fingerprints: frozenset[str],
) -> bool:
    """Require all governance conditions before evidence can qualify for review."""
    return (
        evidence.trigger in ALLOWED_REOPEN_TRIGGERS
        and evidence.authoritative
        and evidence.locally_retained
        and evidence.provenance_backed
        and evidence.applicable_historical_period
        and evidence.fingerprint_verified
        and bool(evidence.fingerprint)
        and evidence.fingerprint not in existing_fingerprints
    )


def assert_reconstruction_allowed(
    freeze: dict[str, object],
    *,
    reconstruction_requested: bool,
    direct_official_portfolio_source: bool = False,
) -> None:
    """Reject only synthetic reconstruction; preserve direct official NAV readers."""
    if not reconstruction_requested or direct_official_portfolio_source:
        return
    if (
        freeze.get("status") == "PORTFOLIO_NAV_RECONSTRUCTION_UNRESOLVED"
        and freeze.get("portfolio_nav_generation_allowed") is False
    ):
        raise PortfolioNavReconstructionFrozenError(RECONSTRUCTION_NOT_APPROVED)


def assert_portfolio_forward_label_generation_allowed(
    freeze: dict[str, object],
    *,
    reconstructed_portfolio_source: bool,
    direct_official_portfolio_source: bool = False,
) -> None:
    """Reject labels sourced from a frozen synthetic portfolio-NAV path."""
    if not reconstructed_portfolio_source or direct_official_portfolio_source:
        return
    if freeze.get("portfolio_forward_label_generation_allowed") is False:
        raise PortfolioNavReconstructionFrozenError(RECONSTRUCTION_NOT_APPROVED)


def write_portfolio_nav_reconstruction_freeze(path: Path, payload: dict[str, object]) -> None:
    """Atomically write the deterministic governance artifact."""
    _write_json_atomic(path, payload)


def _evidence_reference(path: Path, root: Path) -> dict[str, object]:
    value = _load_object(path)
    fingerprints = {
        key: value[key]
        for key in (
            "evidence_fingerprint",
            "resolution_fingerprint",
            "label_store_fingerprint",
            "dataset_fingerprint",
        )
        if isinstance(value.get(key), str)
    }
    return {"path": _relative(path, root), "sha256": _sha256(path), "embedded_fingerprints": fingerprints}


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise PortfolioNavReconstructionFrozenError("evidence path must be repository-relative") from error


def _load_object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PortfolioNavReconstructionFrozenError(f"cannot read freeze evidence: {path}") from error
    if not isinstance(value, dict):
        raise PortfolioNavReconstructionFrozenError("freeze evidence must be a JSON object")
    return value


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PortfolioNavReconstructionFrozenError(f"{name} must be a non-negative integer")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fingerprint(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
