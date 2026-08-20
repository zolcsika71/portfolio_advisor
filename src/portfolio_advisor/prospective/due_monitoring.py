"""Offline monitoring of due prospective portfolio-outcome slots.

The monitor only reports temporal/source state.  It never fetches a provider,
constructs portfolio performance, or changes a pending slot.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Final, cast

from portfolio_advisor.history.official_portfolio_performance import (
    OfficialPortfolioPerformanceError,
    OfficialPortfolioPerformanceStore,
)

from .validation import (
    AVAILABLE_OFFICIAL,
    LIVE_RECORD,
    PENDING,
    RESEARCH_BACKFILL,
    UNAVAILABLE_OUTCOME_STATUSES,
    ProspectiveValidationError,
    ProspectiveValidationStore,
    _artifact_reference,
    _fingerprint,
    _load_object,
    _relative_repository_path,
)

MONITOR_SCHEMA_VERSION: Final = 1
MONITOR_VERSION: Final = "1.0.0"
NOT_YET_DUE: Final = "NOT_YET_DUE"
DUE_UNASSESSED: Final = "DUE_UNASSESSED"


def build_prospective_outcome_due_monitoring(
    *,
    store: ProspectiveValidationStore,
    repository_root: Path,
    freeze_path: Path,
    direct_performance_store_path: Path,
    as_of_date: date,
) -> dict[str, object]:
    """Classify only live slots at a supplied canonical date, offline."""
    freeze = _load_object(freeze_path, "portfolio-NAV reconstruction freeze")
    if freeze.get("validation_status") != "PORTFOLIO_NAV_RECONSTRUCTION_FROZEN_UNRESOLVED":
        raise ProspectiveValidationError("due monitoring requires the active reconstruction freeze")
    decisions = store.rows(
        "SELECT decision_id, decision_date, record_json FROM prospective_decisions "
        "WHERE record_type = ? AND lifecycle_status = ? ORDER BY decision_date, decision_id",
        (LIVE_RECORD, "FINALIZED"),
    )
    direct_store = OfficialPortfolioPerformanceStore(direct_performance_store_path)
    slots: list[dict[str, object]] = []
    for decision in decisions:
        record = _record_object(str(decision["record_json"]))
        selected_id = record.get("selected_portfolio_id")
        if not isinstance(selected_id, str) or not selected_id:
            raise ProspectiveValidationError("finalized live decision lacks its selected portfolio identity")
        rows = store.rows(
            "SELECT horizon_days, expected_start_date, expected_end_date, status, "
            "outcome_source_type, outcome_source_reference "
            "FROM prospective_outcome_slots WHERE decision_id = ? ORDER BY horizon_days",
            (str(decision["decision_id"]),),
        )
        for row in rows:
            expected_end = date.fromisoformat(str(row["expected_end_date"]))
            persisted_status = str(row["status"])
            temporal_status = _temporal_status(persisted_status, expected_end, as_of_date)
            source_state = _local_source_state(
                direct_store=direct_store,
                portfolio_id=selected_id,
                expected_start=date.fromisoformat(str(row["expected_start_date"])),
                expected_end=expected_end,
            ) if temporal_status == DUE_UNASSESSED else "NOT_APPLICABLE"
            slots.append(
                {
                    "decision_id": str(decision["decision_id"]),
                    "decision_date": str(decision["decision_date"]),
                    "portfolio_id": selected_id,
                    "horizon_days": int(row["horizon_days"]),
                    "expected_start_date": str(row["expected_start_date"]),
                    "expected_end_date": str(row["expected_end_date"]),
                    "persisted_status": persisted_status,
                    "temporal_status": temporal_status,
                    "local_direct_source_state": source_state,
                    "source_acquisition_required": temporal_status == DUE_UNASSESSED
                    and source_state == "NO_LOCAL_DIRECT_OFFICIAL_SOURCE",
                    "outcome_source_type": row["outcome_source_type"],
                    "outcome_source_reference": row["outcome_source_reference"],
                }
            )
    slots.sort(
        key=lambda item: (
            str(item["expected_end_date"]),
            str(item["decision_id"]),
            cast(int, item["horizon_days"]),
        )
    )
    temporal_counts = Counter(str(item["temporal_status"]) for item in slots)
    due_by_horizon = {
        str(horizon): sum(
            item["temporal_status"] == DUE_UNASSESSED and item["horizon_days"] == horizon for item in slots
        )
        for horizon in (90, 180, 365)
    }
    next_due = next((item for item in slots if item["temporal_status"] == NOT_YET_DUE), None)
    payload: dict[str, object] = {
        "schema_version": MONITOR_SCHEMA_VERSION,
        "monitor_version": MONITOR_VERSION,
        "monitoring_status": "PROSPECTIVE_OUTCOME_MONITORING_VALIDATED_WITH_CAVEATS",
        "as_of_date": as_of_date.isoformat(),
        "live_decision_count": len(decisions),
        "research_backfill_count": _research_backfill_count(store),
        "research_backfill_monitored": False,
        "slots": slots,
        "not_yet_due_count": temporal_counts[NOT_YET_DUE],
        "due_unassessed_count": temporal_counts[DUE_UNASSESSED],
        "available_official_count": temporal_counts[AVAILABLE_OFFICIAL],
        "unavailable_count": sum(temporal_counts[status] for status in UNAVAILABLE_OUTCOME_STATUSES),
        "due_by_horizon": due_by_horizon,
        "next_due_date": next_due["expected_end_date"] if next_due else None,
        "next_due_decision_id": next_due["decision_id"] if next_due else None,
        "next_due_horizon_days": next_due["horizon_days"] if next_due else None,
        "source_acquisition_required_count": sum(item["source_acquisition_required"] is True for item in slots),
        "direct_performance_store": {
            "path": _relative_repository_path(direct_performance_store_path, repository_root),
            "exists": direct_performance_store_path.is_file(),
            "network_access": "NOT_USED",
        },
        "freeze_reference": _artifact_reference(freeze_path, repository_root),
        "freeze_status": freeze["validation_status"],
        "admission_boundary": {
            "automatic_admission": False,
            "reason": "Monitoring does not calculate or synthesize portfolio performance; explicit direct-source admission remains required.",
            "approved_channels": [
                "DIRECT_OFFICIAL_PORTFOLIO_NAV",
                "DIRECT_OFFICIAL_PORTFOLIO_PERFORMANCE_EXPORT",
                "APPROVED_PROSPECTIVE_PORTFOLIO_OBSERVATION",
            ],
            "blocked_channels": [
                "CONSTITUENT_AGGREGATION",
                "SYNTHETIC_PORTFOLIO_NAV",
                "GRAPHIFY_INFERENCE",
                "UNPROVEN_MANUAL_RETURN",
            ],
        },
        "prospective_validation_readiness": "PROSPECTIVE_VALIDATION_NOT_READY",
        "optimization_readiness": "OPTIMIZATION_NOT_READY",
    }
    payload["audit_fingerprint"] = _fingerprint(payload)
    return payload


def write_prospective_outcome_due_monitoring(path: Path, payload: dict[str, object]) -> None:
    """Atomically persist a deterministic offline due-monitoring audit."""
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


def _temporal_status(persisted_status: str, expected_end: date, as_of_date: date) -> str:
    if persisted_status == AVAILABLE_OFFICIAL or persisted_status in UNAVAILABLE_OUTCOME_STATUSES:
        return persisted_status
    if persisted_status != PENDING:
        raise ProspectiveValidationError(f"unknown prospective outcome slot status: {persisted_status}")
    return DUE_UNASSESSED if as_of_date >= expected_end else NOT_YET_DUE


def _local_source_state(
    *,
    direct_store: OfficialPortfolioPerformanceStore,
    portfolio_id: str,
    expected_start: date,
    expected_end: date,
) -> str:
    if not direct_store.path.is_file():
        return "NO_LOCAL_DIRECT_OFFICIAL_SOURCE"
    try:
        observations = direct_store.observations(portfolio_id)
    except (OfficialPortfolioPerformanceError, OSError, ValueError):
        return "LOCAL_DIRECT_OFFICIAL_SOURCE_INVALID"
    start = [item for item in observations if item.observation_date == expected_start]
    end = [item for item in observations if item.observation_date == expected_end]
    if not start or not end:
        return "LOCAL_DIRECT_SOURCE_INTERVAL_INCOMPLETE"
    if any(item.quality_status != "VALIDATED" for item in start + end):
        return "LOCAL_DIRECT_SOURCE_VALIDATION_INSUFFICIENT"
    identities = {(item.source_provider, item.source_identifier, item.value_type, item.currency) for item in start + end}
    if len(identities) != 1:
        return "LOCAL_DIRECT_SOURCE_CONFLICT"
    return "LOCAL_DIRECT_SOURCE_EXACT_BOUNDARIES_FOUND_REQUIRES_EXPLICIT_ADMISSION"


def _research_backfill_count(store: ProspectiveValidationStore) -> int:
    rows = store.rows("SELECT COUNT(*) AS count FROM prospective_decisions WHERE record_type = ?", (RESEARCH_BACKFILL,))
    return int(rows[0]["count"]) if rows else 0


def _record_object(value: str) -> dict[str, object]:
    try:
        record = json.loads(value)
    except json.JSONDecodeError as error:
        raise ProspectiveValidationError("stored prospective decision JSON is malformed") from error
    if not isinstance(record, dict):
        raise ProspectiveValidationError("stored prospective decision JSON must be an object")
    return record
