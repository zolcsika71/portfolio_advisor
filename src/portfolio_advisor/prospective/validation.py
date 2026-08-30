"""Fail-closed prospective evidence ledger for portfolio validation.

This module deliberately records decisions and later direct observations as
separate immutable layers.  It does not construct a portfolio NAV, aggregate
constituent histories, call providers, or alter the ranking policy.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Final, cast

from portfolio_advisor.advisor.models import AdvisorResult
from portfolio_advisor.canonical import canonical_fingerprint, canonical_json
from portfolio_advisor.database.repository import (
    HoldingObservation,
    ModelPortfolioRepository,
)
from portfolio_advisor.features.dataset import (
    KnowledgeItem,
    knowledge_available_at,
    load_graphify_knowledge,
    portfolio_structure,
)
from portfolio_advisor.history.models import ForwardWindow
from portfolio_advisor.ranking.config import load_ranking_rules
from portfolio_advisor.ranking.models import CandidateEvaluation

PIPELINE_SCHEMA_VERSION: Final = 1
PIPELINE_VERSION: Final = "1.0.0"
OUTCOME_HORIZONS: Final = (90, 180, 365)
LIVE_RECORD: Final = "PROSPECTIVE_LIVE_RECORD"
RESEARCH_BACKFILL: Final = "RESEARCH_BACKFILL"
FINALIZED: Final = "FINALIZED"
DRAFT: Final = "DRAFT"
PENDING: Final = "PENDING"
AVAILABLE_OFFICIAL: Final = "AVAILABLE_OFFICIAL"
UNAVAILABLE_OUTCOME_STATUSES: Final = frozenset(
    {
        "UNAVAILABLE_NO_SOURCE",
        "UNAVAILABLE_STRICT_REJECTION",
        "UNAVAILABLE_SEMANTICS_BLOCKED",
        "UNAVAILABLE_SOURCE_CONFLICT",
        "UNAVAILABLE_PROVENANCE_INSUFFICIENT",
    }
)
ALLOWED_OUTCOME_SOURCE_TYPES: Final = frozenset(
    {
        "DIRECT_OFFICIAL_PORTFOLIO_NAV",
        "DIRECT_OFFICIAL_PORTFOLIO_PERFORMANCE_EXPORT",
        "APPROVED_PROSPECTIVE_PORTFOLIO_OBSERVATION",
    }
)
ALLOWED_OUTCOME_VALUE_SEMANTICS: Final = frozenset(
    {
        "PORTFOLIO_NAV",
        "PORTFOLIO_INDEX_VALUE",
        "OFFICIAL_PORTFOLIO_PRICE",
        "OFFICIAL_TOTAL_RETURN_INDEX",
        "PROVIDER_REPORTED_PORTFOLIO_RETURN",
    }
)
BLOCKED_OUTCOME_SOURCE_TYPES: Final = frozenset(
    {
        "CONSTITUENT_AGGREGATION",
        "SYNTHETIC_PORTFOLIO_NAV",
        "GRAPHIFY_INFERENCE",
        "MANUAL_RETURN_ENTRY_WITHOUT_PROVENANCE",
    }
)


class ProspectiveValidationError(RuntimeError):
    """A prospective record or outcome does not meet the evidence contract."""


@dataclass(frozen=True, slots=True)
class ProspectiveOutcome:
    """A later, direct official portfolio observation admitted to one slot."""

    decision_id: str
    horizon_days: int
    portfolio_id: str
    observation_information_date: date
    source_type: str
    source_provider: str
    source_identifier: str
    source_reference: str
    local_artifact: str
    sha256_or_fingerprint: str
    currency: str
    value_semantics: str
    metrics: Mapping[str, float]
    validation_status: str = "VALIDATED"

    def __post_init__(self) -> None:
        if self.horizon_days not in OUTCOME_HORIZONS:
            raise ProspectiveValidationError("unsupported prospective outcome horizon")
        if self.source_type not in ALLOWED_OUTCOME_SOURCE_TYPES:
            raise ProspectiveValidationError("outcome source type is not an approved direct portfolio channel")
        if self.value_semantics not in ALLOWED_OUTCOME_VALUE_SEMANTICS:
            raise ProspectiveValidationError("outcome value semantics are not approved for a direct portfolio outcome")
        required = (
            self.portfolio_id,
            self.source_provider,
            self.source_identifier,
            self.source_reference,
            self.local_artifact,
            self.sha256_or_fingerprint,
            self.currency,
            self.value_semantics,
        )
        if any(not value.strip() for value in required):
            raise ProspectiveValidationError("official outcome provenance is incomplete")
        if self.validation_status != "VALIDATED":
            raise ProspectiveValidationError("official outcome evidence must have validated source status")
        if not _repository_relative(self.local_artifact):
            raise ProspectiveValidationError("outcome local artifact must be a repository-relative path")
        if not self.metrics:
            raise ProspectiveValidationError("an available official outcome requires at least one supported metric")
        _validate_outcome_metrics(self.metrics)


class ProspectiveValidationStore:
    """SQLite-backed append-only ledger for finalized prospective evidence."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def finalize(self, draft: Mapping[str, object]) -> bool:
        """Finalize one decision; identical retries are idempotent, changes fail closed."""
        record = _finalize_record(draft)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            self._ensure_schema(connection)
            existing = connection.execute(
                "SELECT record_fingerprint, record_json FROM prospective_decisions WHERE decision_id = ?",
                (record["decision_id"],),
            ).fetchone()
            canonical = _canonical_json(record)
            if existing is not None:
                if tuple(existing) == (record["record_fingerprint"], canonical):
                    return False
                raise ProspectiveValidationError("finalized decision identity conflicts with different content")
            connection.execute(
                """INSERT INTO prospective_decisions (
                    decision_id, decision_date, information_date, record_type, lifecycle_status,
                    policy_id, policy_version, policy_fingerprint, portfolio_universe_fingerprint,
                    record_fingerprint, record_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["decision_id"],
                    record["decision_date"],
                    record["information_date"],
                    record["record_type"],
                    record["lifecycle_status"],
                    record["policy_id"],
                    record["policy_version"],
                    record["policy_fingerprint"],
                    record["portfolio_universe_fingerprint"],
                    record["record_fingerprint"],
                    canonical,
                ),
            )
            candidate_rows = record["full_candidate_ranking"]
            if not isinstance(candidate_rows, list):
                raise ProspectiveValidationError("candidate record is malformed")
            for candidate in candidate_rows:
                if not isinstance(candidate, dict):
                    raise ProspectiveValidationError("candidate record is malformed")
                connection.execute(
                    "INSERT INTO prospective_candidates VALUES (?, ?, ?, ?)",
                    (
                        record["decision_id"],
                        candidate["portfolio_id"],
                        candidate.get("rank"),
                        _canonical_json(candidate),
                    ),
                )
            for horizon in OUTCOME_HORIZONS:
                window = ForwardWindow.build(date.fromisoformat(str(record["decision_date"])), horizon)
                connection.execute(
                    "INSERT INTO prospective_outcome_slots VALUES (?, ?, ?, ?, ?, NULL, NULL)",
                    (
                        record["decision_id"],
                        horizon,
                        window.evaluation_date.isoformat(),
                        window.end_date.isoformat(),
                        PENDING,
                    ),
                )
        return True

    def admit_outcome(self, outcome: ProspectiveOutcome, *, current_date: date) -> bool:
        """Append one due direct official outcome without ever changing a decision."""
        with sqlite3.connect(self.path) as connection:
            self._ensure_schema(connection)
            decision = connection.execute(
                "SELECT decision_date, lifecycle_status, record_type, record_json "
                "FROM prospective_decisions WHERE decision_id = ?",
                (outcome.decision_id,),
            ).fetchone()
            if decision is None or decision[1] != FINALIZED:
                raise ProspectiveValidationError("outcomes require an existing finalized decision")
            if decision[2] != LIVE_RECORD:
                raise ProspectiveValidationError("research backfills cannot receive prospective outcomes")
            decision_record = _stored_record(str(decision[3]))
            expected_portfolio = decision_record.get("selected_portfolio_id")
            if outcome.portfolio_id != expected_portfolio:
                raise ProspectiveValidationError("outcome portfolio identity differs from the finalized selected portfolio")
            currencies = decision_record.get("portfolio_currency")
            expected_currency = currencies.get(outcome.portfolio_id) if isinstance(currencies, dict) else None
            if expected_currency in {"HUF", "EUR", "USD"} and outcome.currency != expected_currency:
                raise ProspectiveValidationError("outcome currency differs from the finalized portfolio currency")
            decision_date = date.fromisoformat(str(decision[0]))
            if outcome.observation_information_date <= decision_date:
                raise ProspectiveValidationError("outcome observation information must be after the decision date")
            slot = connection.execute(
                "SELECT expected_start_date, expected_end_date, status FROM prospective_outcome_slots "
                "WHERE decision_id = ? AND horizon_days = ?",
                (outcome.decision_id, outcome.horizon_days),
            ).fetchone()
            if slot is None:
                raise ProspectiveValidationError("outcome slot does not exist")
            if current_date < date.fromisoformat(str(slot[1])):
                raise ProspectiveValidationError("outcome horizon is not yet due")
            payload = _outcome_payload(
                outcome,
                observation_start=str(slot[0]),
                observation_end=str(slot[1]),
            )
            existing = connection.execute(
                "SELECT outcome_fingerprint, outcome_json FROM prospective_outcomes "
                "WHERE decision_id = ? AND horizon_days = ?",
                (outcome.decision_id, outcome.horizon_days),
            ).fetchone()
            current_status = str(slot[2])
            if current_status == PENDING:
                if existing is not None:
                    raise ProspectiveValidationError("pending outcome slot already has immutable evidence")
                connection.execute(
                    "INSERT INTO prospective_outcomes VALUES (?, ?, ?, ?)",
                    (
                        outcome.decision_id,
                        outcome.horizon_days,
                        payload["outcome_fingerprint"],
                        _canonical_json(payload),
                    ),
                )
            elif current_status == AVAILABLE_OFFICIAL:
                if _official_outcome_evidence_exists(
                    connection,
                    decision_id=outcome.decision_id,
                    horizon_days=outcome.horizon_days,
                    evidence=payload,
                ):
                    return False
                raise ProspectiveValidationError("outcome slot conflicts with different official evidence")
            elif current_status in UNAVAILABLE_OUTCOME_STATUSES:
                if existing is None:
                    raise ProspectiveValidationError("unavailable outcome slot lacks immutable assessment evidence")
                if _official_outcome_evidence_exists(
                    connection,
                    decision_id=outcome.decision_id,
                    horizon_days=outcome.horizon_days,
                    evidence=payload,
                ):
                    return False
            else:
                raise ProspectiveValidationError("outcome slot has an unsupported lifecycle status")
            _append_outcome_event(
                connection,
                decision_id=outcome.decision_id,
                horizon_days=outcome.horizon_days,
                previous_status=current_status,
                new_status=AVAILABLE_OFFICIAL,
                evidence=payload,
            )
            connection.execute(
                """UPDATE prospective_outcome_slots
                   SET status = ?, outcome_source_type = ?, outcome_source_reference = ?
                   WHERE decision_id = ? AND horizon_days = ?""",
                (
                    AVAILABLE_OFFICIAL,
                    outcome.source_type,
                    outcome.source_reference,
                    outcome.decision_id,
                    outcome.horizon_days,
                ),
            )
        return True

    def append_amendment(
        self,
        *,
        original_decision_id: str,
        amendment_id: str,
        reason: str,
        evidence: Mapping[str, object],
        affected_fields: Sequence[str],
        effective_date: date,
    ) -> bool:
        """Append a correction reference; it never rewrites the original record."""
        if not amendment_id or not reason or not affected_fields:
            raise ProspectiveValidationError("amendment identity, reason, and affected fields are required")
        payload = {
            "original_decision_id": original_decision_id,
            "amendment_id": amendment_id,
            "reason": reason,
            "evidence": _json_value(evidence),
            "affected_fields": sorted(set(affected_fields)),
            "effective_date": effective_date.isoformat(),
        }
        fingerprint = _fingerprint(payload)
        with sqlite3.connect(self.path) as connection:
            self._ensure_schema(connection)
            if connection.execute(
                "SELECT 1 FROM prospective_decisions WHERE decision_id = ?", (original_decision_id,)
            ).fetchone() is None:
                raise ProspectiveValidationError("amendment references an unknown decision")
            existing = connection.execute(
                "SELECT amendment_fingerprint, amendment_json FROM prospective_decision_amendments "
                "WHERE amendment_id = ?",
                (amendment_id,),
            ).fetchone()
            if existing is not None:
                if tuple(existing) == (fingerprint, _canonical_json(payload)):
                    return False
                raise ProspectiveValidationError("amendment identity conflicts with different content")
            connection.execute(
                "INSERT INTO prospective_decision_amendments VALUES (?, ?, ?, ?)",
                (amendment_id, original_decision_id, fingerprint, _canonical_json(payload)),
            )
        return True

    def mark_outcome_unavailable(
        self,
        *,
        decision_id: str,
        horizon_days: int,
        status: str,
        source_reference: str,
        current_date: date,
        reason: str = "No admissible official portfolio-level outcome is available under the retained evidence.",
    ) -> bool:
        """Close a due slot explicitly without fabricating an outcome value."""
        if horizon_days not in OUTCOME_HORIZONS or status not in UNAVAILABLE_OUTCOME_STATUSES:
            raise ProspectiveValidationError("unsupported unavailable-outcome status or horizon")
        if not source_reference.strip() or not reason.strip():
            raise ProspectiveValidationError("unavailable outcome requires a rejection/source reference")
        payload = {
            "decision_id": decision_id,
            "horizon_days": horizon_days,
            "status": status,
            "previous_status": PENDING,
            "reason": reason,
            "assessed_at": current_date.isoformat(),
            "source_reference": source_reference,
            "metrics": {},
            "numeric_label_present": False,
        }
        payload["outcome_fingerprint"] = _fingerprint(payload)
        with sqlite3.connect(self.path) as connection:
            self._ensure_schema(connection)
            decision = connection.execute(
                "SELECT lifecycle_status, record_type FROM prospective_decisions WHERE decision_id = ?", (decision_id,)
            ).fetchone()
            if decision is None or decision[0] != FINALIZED:
                raise ProspectiveValidationError("outcomes require an existing finalized decision")
            if decision[1] != LIVE_RECORD:
                raise ProspectiveValidationError("research backfills cannot receive prospective outcomes")
            slot = connection.execute(
                "SELECT expected_start_date, expected_end_date, status FROM prospective_outcome_slots "
                "WHERE decision_id = ? AND horizon_days = ?",
                (decision_id, horizon_days),
            ).fetchone()
            if slot is None:
                raise ProspectiveValidationError("outcome slot does not exist")
            if current_date < date.fromisoformat(str(slot[1])):
                raise ProspectiveValidationError("outcome horizon is not yet due")
            existing = connection.execute(
                "SELECT outcome_fingerprint, outcome_json FROM prospective_outcomes "
                "WHERE decision_id = ? AND horizon_days = ?",
                (decision_id, horizon_days),
            ).fetchone()
            if existing is not None:
                if tuple(existing) == (payload["outcome_fingerprint"], _canonical_json(payload)):
                    return False
                raise ProspectiveValidationError("outcome slot conflicts with different evidence")
            if slot[2] != PENDING:
                raise ProspectiveValidationError("only pending outcome slots may be closed")
            connection.execute(
                "INSERT INTO prospective_outcomes VALUES (?, ?, ?, ?)",
                (decision_id, horizon_days, payload["outcome_fingerprint"], _canonical_json(payload)),
            )
            _append_outcome_event(
                connection,
                decision_id=decision_id,
                horizon_days=horizon_days,
                previous_status=PENDING,
                new_status=status,
                evidence=payload,
            )
            connection.execute(
                """UPDATE prospective_outcome_slots
                   SET status = ?, outcome_source_type = ?, outcome_source_reference = ?
                   WHERE decision_id = ? AND horizon_days = ?""",
                (status, "NO_ADMITTED_OFFICIAL_OUTCOME", source_reference, decision_id, horizon_days),
            )
        return True

    def rows(self, query: str, parameters: tuple[object, ...] = ()) -> list[sqlite3.Row]:
        """Read ledger evidence for deterministic audit generation only."""
        if not self.path.is_file():
            return []
        with sqlite3.connect(f"file:{self.path.resolve()}?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            return list(connection.execute(query, parameters).fetchall())

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS prospective_decisions (
                decision_id TEXT PRIMARY KEY, decision_date TEXT NOT NULL, information_date TEXT NOT NULL,
                record_type TEXT NOT NULL, lifecycle_status TEXT NOT NULL,
                policy_id TEXT NOT NULL, policy_version TEXT NOT NULL, policy_fingerprint TEXT NOT NULL,
                portfolio_universe_fingerprint TEXT NOT NULL, record_fingerprint TEXT NOT NULL,
                record_json TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS prospective_candidates (
                decision_id TEXT NOT NULL, portfolio_id TEXT NOT NULL, rank INTEGER,
                candidate_json TEXT NOT NULL,
                PRIMARY KEY (decision_id, portfolio_id),
                FOREIGN KEY (decision_id) REFERENCES prospective_decisions(decision_id)
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS prospective_outcome_slots (
                decision_id TEXT NOT NULL, horizon_days INTEGER NOT NULL,
                expected_start_date TEXT NOT NULL, expected_end_date TEXT NOT NULL,
                status TEXT NOT NULL, outcome_source_type TEXT, outcome_source_reference TEXT,
                PRIMARY KEY (decision_id, horizon_days),
                FOREIGN KEY (decision_id) REFERENCES prospective_decisions(decision_id)
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS prospective_outcome_events (
                event_id TEXT PRIMARY KEY, decision_id TEXT NOT NULL, horizon_days INTEGER NOT NULL,
                previous_status TEXT NOT NULL, new_status TEXT NOT NULL,
                event_fingerprint TEXT NOT NULL, event_json TEXT NOT NULL,
                FOREIGN KEY (decision_id, horizon_days)
                    REFERENCES prospective_outcome_slots(decision_id, horizon_days)
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS prospective_outcomes (
                decision_id TEXT NOT NULL, horizon_days INTEGER NOT NULL,
                outcome_fingerprint TEXT NOT NULL, outcome_json TEXT NOT NULL,
                PRIMARY KEY (decision_id, horizon_days),
                FOREIGN KEY (decision_id, horizon_days)
                    REFERENCES prospective_outcome_slots(decision_id, horizon_days)
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS prospective_decision_amendments (
                amendment_id TEXT PRIMARY KEY, original_decision_id TEXT NOT NULL,
                amendment_fingerprint TEXT NOT NULL, amendment_json TEXT NOT NULL,
                FOREIGN KEY (original_decision_id) REFERENCES prospective_decisions(decision_id)
            )"""
        )


def _append_outcome_event(
    connection: sqlite3.Connection,
    *,
    decision_id: str,
    horizon_days: int,
    previous_status: str,
    new_status: str,
    evidence: Mapping[str, object],
) -> bool:
    """Append one status transition; outcomes and their assessments are immutable."""
    payload: dict[str, object] = {
        "decision_id": decision_id,
        "horizon_days": horizon_days,
        "previous_status": previous_status,
        "new_status": new_status,
        "evidence": _json_value(evidence),
    }
    fingerprint = _fingerprint(payload)
    payload["event_fingerprint"] = fingerprint
    event_id = fingerprint
    existing = connection.execute(
        "SELECT event_fingerprint, event_json FROM prospective_outcome_events WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    canonical = _canonical_json(payload)
    if existing is not None:
        if tuple(existing) == (fingerprint, canonical):
            return False
        raise ProspectiveValidationError("outcome event identity conflicts with different evidence")
    connection.execute(
        "INSERT INTO prospective_outcome_events VALUES (?, ?, ?, ?, ?, ?, ?)",
        (event_id, decision_id, horizon_days, previous_status, new_status, fingerprint, canonical),
    )
    return True


def _official_outcome_evidence_exists(
    connection: sqlite3.Connection,
    *,
    decision_id: str,
    horizon_days: int,
    evidence: Mapping[str, object],
) -> bool:
    """Recognize an identical admission retry irrespective of its prior state."""
    rows = connection.execute(
        "SELECT event_json FROM prospective_outcome_events "
        "WHERE decision_id = ? AND horizon_days = ? AND new_status = ?",
        (decision_id, horizon_days, AVAILABLE_OFFICIAL),
    ).fetchall()
    expected = _canonical_json(_json_value(evidence))
    for row in rows:
        event = _stored_record(str(row[0]))
        if _canonical_json(event.get("evidence")) == expected:
            return True
    return False


def build_prospective_decision(
    *,
    advisor_result: AdvisorResult,
    repository: ModelPortfolioRepository,
    rules_path: Path,
    graph_path: Path,
    repository_root: Path,
    record_type: str = LIVE_RECORD,
    freeze_path: Path | None = None,
) -> dict[str, object]:
    """Capture only the state supplied to the deterministic advisor at one date.

    The input snapshot comes directly from ``load_holdings(decision_date)``;
    no global feature-dataset fingerprint is reused because one covering later
    dates would not be point-in-time safe for a live record.
    """
    if record_type not in {LIVE_RECORD, RESEARCH_BACKFILL}:
        raise ProspectiveValidationError("record type must be prospective live or research backfill")
    if advisor_result.observation_date is None:
        raise ProspectiveValidationError("decision record requires a concrete observation date")
    if advisor_result.selected_portfolio is None:
        raise ProspectiveValidationError("only a completed ranking with a selected portfolio can be finalized")
    if advisor_result.rules_status != "approved":
        raise ProspectiveValidationError("prospective decision requires the active approved ranking policy")
    decision_date = advisor_result.observation_date
    if record_type == LIVE_RECORD and decision_date != repository.latest_observation_date():
        raise ProspectiveValidationError(
            "live prospective decision must use the repository's latest canonical portfolio observation date"
        )
    holdings = repository.load_holdings(decision_date)
    if not holdings:
        raise ProspectiveValidationError("decision record has no point-in-time holdings")
    grouped = _group_holdings(holdings)
    candidates = _candidate_payloads(advisor_result.ranking, grouped)
    candidate_ids = tuple(str(item["portfolio_id"]) for item in candidates)
    if len(set(candidate_ids)) != len(candidate_ids):
        raise ProspectiveValidationError("candidate universe has ambiguous portfolio identifiers")
    selected = advisor_result.selected_portfolio
    selected_id = selected.metrics.portfolio_name
    selected_payload = next((item for item in candidates if item["portfolio_id"] == selected_id), None)
    if selected_payload is None:
        raise ProspectiveValidationError("selected portfolio is absent from candidate universe")
    if set(candidate_ids) != set(grouped):
        raise ProspectiveValidationError("ranking candidate universe does not reconcile with source holdings")
    rules = load_ranking_rules(rules_path)
    if rules.policy_name == "" or rules.version != advisor_result.rule_set_version:
        raise ProspectiveValidationError("advisor policy identity does not match active policy source")
    knowledge = knowledge_available_at(load_graphify_knowledge(graph_path), decision_date)
    knowledge_payload = [_knowledge_payload(item) for item in knowledge]
    policy_fingerprint = _sha256(rules_path)
    universe_fingerprint = _fingerprint({"portfolio_ids": sorted(candidate_ids)})
    source_state = {
        "source_snapshot": _source_snapshot_payload(grouped),
        "source_evidence_fingerprint": _fingerprint(_source_snapshot_payload(grouped)),
        "database_reference": _relative_repository_path(repository.database_path, repository_root),
        "source_priority_changed": False,
        "strict_forward_eligibility": {
            "status": "PENDING_FUTURE_SOURCE_EVIDENCE",
            "evaluated_at_decision_time": False,
            "reason": "Strict forward eligibility is a future-window check and is not inferred at decision time.",
            "blocking_isins": [],
            "blocking_categories": [],
        },
    }
    strict_eligibility_fingerprint = _fingerprint(source_state["strict_forward_eligibility"])
    if freeze_path is not None:
        freeze = _load_object(freeze_path, "portfolio-NAV reconstruction freeze")
        if freeze.get("validation_status") != "PORTFOLIO_NAV_RECONSTRUCTION_FROZEN_UNRESOLVED":
            raise ProspectiveValidationError("portfolio-NAV reconstruction freeze is not active")
        source_state["portfolio_nav_reconstruction_freeze"] = _artifact_reference(freeze_path, repository_root)
    point_in_time_slice = {
        "decision_date": decision_date.isoformat(),
        "candidates": candidates,
        "source_snapshot": _source_snapshot_payload(grouped),
        "admitted_graphify_knowledge": knowledge_payload,
    }
    decision_identity = {
        "decision_date": decision_date.isoformat(),
        "policy_fingerprint": policy_fingerprint,
        "portfolio_universe_fingerprint": universe_fingerprint,
    }
    decision_identity_fingerprint = _fingerprint(decision_identity)
    # Research records retain the original identity scheme.  A stable live
    # namespace prevents a current live decision from overwriting a same-date
    # research replay while preserving the same financial identity inputs.
    decision_id = (
        decision_identity_fingerprint
        if record_type == RESEARCH_BACKFILL
        else _fingerprint({"record_type": LIVE_RECORD, "decision_identity_fingerprint": decision_identity_fingerprint})
    )
    record: dict[str, object] = {
        "record_schema_version": PIPELINE_SCHEMA_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "record_type": record_type,
        "lifecycle_status": DRAFT,
        "decision_id": decision_id,
        "decision_identity_fingerprint": decision_identity_fingerprint,
        "decision_date": decision_date.isoformat(),
        "information_date": decision_date.isoformat(),
        "portfolio_universe_id": universe_fingerprint,
        "portfolio_universe_fingerprint": universe_fingerprint,
        "policy_id": rules.policy_name,
        "policy_version": rules.version,
        "policy_fingerprint": policy_fingerprint,
        "candidate_count": len(candidates),
        "eligible_candidate_count": sum(item["ranking_eligible"] is True for item in candidates),
        "rejected_candidate_count": sum(item["ranking_eligible"] is not True for item in candidates),
        "selected_portfolio_id": selected_id,
        "selected_portfolio_name": selected_id,
        "selected_rank": selected_payload["rank"],
        "selected_score": selected_payload["total_score"],
        "full_candidate_ranking": candidates,
        "candidate_scores": {item["portfolio_id"]: item["total_score"] for item in candidates},
        "candidate_feature_values": {item["portfolio_id"]: item["candidate_feature_values"] for item in candidates},
        "candidate_normalized_values": {item["portfolio_id"]: item["candidate_normalized_values"] for item in candidates},
        "candidate_weighted_contributions": {
            item["portfolio_id"]: item["candidate_weighted_contributions"] for item in candidates
        },
        "portfolio_composition": {item["portfolio_id"]: item["portfolio_composition"] for item in candidates},
        "constituent_isins": {item["portfolio_id"]: item["constituent_isins"] for item in candidates},
        "constituent_weights": {item["portfolio_id"]: item["constituent_weights"] for item in candidates},
        "portfolio_currency": {item["portfolio_id"]: item["portfolio_currency"] for item in candidates},
        "strict_eligibility_result": source_state["strict_forward_eligibility"],
        "strict_eligibility_fingerprint": strict_eligibility_fingerprint,
        "blocking_isins": [],
        "blocking_categories": [],
        "point_in_time_dataset_fingerprint": _fingerprint(point_in_time_slice),
        "source_evidence_fingerprint": source_state["source_evidence_fingerprint"],
        "source_evidence_state": source_state,
        "graphify_knowledge_fingerprint": _fingerprint(knowledge_payload),
        "graphify_knowledge_ids": [item["knowledge_id"] for item in knowledge_payload],
        "graphify_source_document_ids": [item["source_document"] for item in knowledge_payload],
        "graphify_knowledge_categories": [item["knowledge_category"] for item in knowledge_payload],
        "graphify_constraints_used": [],
        "graphify_warnings": ["Graphify is not an executable ranking or outcome source."],
        "graphify_knowledge_available_at_decision": knowledge_payload,
        "point_in_time_dataset_fingerprint_scope": "DECISION_TIME_SLICE_ONLY",
        "point_in_time_guard": {
            "result": "NO_LOOKAHEAD",
            "information_date_rule": "information_date <= decision_date",
            "future_outcome_used_in_ranking": False,
            "future_graphify_fact_used": False,
            "forward_labels_used_as_features": False,
        },
        "outcome_contract": {
            "slots_created_on_finalization": list(OUTCOME_HORIZONS),
            "allowed_direct_channels": sorted(ALLOWED_OUTCOME_SOURCE_TYPES),
            "blocked_channels": sorted(BLOCKED_OUTCOME_SOURCE_TYPES),
            "unavailable_outcomes_are_zero_filled": False,
        },
    }
    _validate_draft(record)
    return record


def build_prospective_validation_audit(
    *,
    store: ProspectiveValidationStore,
    repository_root: Path,
    freeze_path: Path,
) -> dict[str, object]:
    """Build a stable, human-readable audit without changing ledger contents."""
    freeze = _load_object(freeze_path, "portfolio-NAV reconstruction freeze")
    if freeze.get("validation_status") != "PORTFOLIO_NAV_RECONSTRUCTION_FROZEN_UNRESOLVED":
        raise ProspectiveValidationError("prospective pipeline requires the active reconstruction freeze")
    decisions = store.rows(
        "SELECT decision_id, decision_date, record_type, policy_version, policy_fingerprint "
        "FROM prospective_decisions ORDER BY decision_date, decision_id"
    )
    slots = store.rows(
        "SELECT decision_id, horizon_days, status FROM prospective_outcome_slots "
        "ORDER BY decision_id, horizon_days"
    )
    types = {LIVE_RECORD: 0, RESEARCH_BACKFILL: 0}
    for row in decisions:
        types[str(row["record_type"])] = types.get(str(row["record_type"]), 0) + 1
    slot_counts = {status: 0 for status in (PENDING, AVAILABLE_OFFICIAL, *UNAVAILABLE_OUTCOME_STATUSES)}
    by_horizon: dict[str, dict[str, int]] = {
        str(horizon): {"pending": 0, "available_official": 0, "unavailable": 0} for horizon in OUTCOME_HORIZONS
    }
    live_ids = {str(row["decision_id"]) for row in decisions if row["record_type"] == LIVE_RECORD}
    live_available = {horizon: 0 for horizon in OUTCOME_HORIZONS}
    for row in slots:
        status = str(row["status"])
        slot_counts[status] = slot_counts.get(status, 0) + 1
        horizon = int(row["horizon_days"])
        key = "available_official" if status == AVAILABLE_OFFICIAL else "pending" if status == PENDING else "unavailable"
        by_horizon[str(horizon)][key] += 1
        if str(row["decision_id"]) in live_ids and status == AVAILABLE_OFFICIAL:
            live_available[horizon] += 1
    readiness = _readiness(live_available, live_ids)
    latest_live = next((row for row in reversed(decisions) if row["record_type"] == LIVE_RECORD), None)
    payload: dict[str, object] = {
        "schema_version": PIPELINE_SCHEMA_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "pipeline_status": "PROSPECTIVE_PORTFOLIO_VALIDATION_PIPELINE_VALIDATED_WITH_CAVEATS",
        "decision_store": _relative_repository_path(store.path, repository_root),
        "decision_count": len(decisions),
        "live_prospective_decision_count": types.get(LIVE_RECORD, 0),
        "research_backfill_decision_count": types.get(RESEARCH_BACKFILL, 0),
        "live_decision_count": types.get(LIVE_RECORD, 0),
        "research_backfill_count": types.get(RESEARCH_BACKFILL, 0),
        "research_backfill_excluded_from_live_readiness": True,
        "pending_outcome_count": slot_counts.get(PENDING, 0),
        "pending_90d": by_horizon["90"]["pending"],
        "pending_180d": by_horizon["180"]["pending"],
        "pending_365d": by_horizon["365"]["pending"],
        "available_outcome_count": slot_counts.get(AVAILABLE_OFFICIAL, 0),
        "available_official_outcomes": slot_counts.get(AVAILABLE_OFFICIAL, 0),
        "unavailable_outcome_count": sum(count for status, count in slot_counts.items() if status != PENDING and status != AVAILABLE_OFFICIAL),
        "unavailable_outcomes_by_status": {
            status: slot_counts[status] for status in sorted(UNAVAILABLE_OUTCOME_STATUSES)
        },
        "outcome_slots_by_horizon": by_horizon,
        "supported_outcome_channels": sorted(ALLOWED_OUTCOME_SOURCE_TYPES),
        "blocked_outcome_channels": sorted(BLOCKED_OUTCOME_SOURCE_TYPES),
        "point_in_time_guards": {
            "result": "NO_LOOKAHEAD",
            "decision_records_are_append_only_after_finalization": True,
            "future_outcomes_do_not_rewrite_decisions": True,
            "outcome_admission_requires_horizon_due": True,
            "no_runtime_network_calls": True,
        },
        "freeze_reference": _artifact_reference(freeze_path, repository_root),
        "freeze_validation_status": freeze["validation_status"],
        "outcome_source_absence_behavior": "Preserve decision and PENDING slot; do not synthesize or zero-fill an outcome.",
        "amendment_contract": "Append-only correction references may be added; the original finalized decision is never overwritten.",
        "policy_version_coverage": _counter(str(row["policy_version"]) for row in decisions),
        "policy_fingerprints": sorted({str(row["policy_fingerprint"]) for row in decisions}),
        "latest_live_decision_id": str(latest_live["decision_id"]) if latest_live is not None else None,
        "latest_live_decision_date": str(latest_live["decision_date"]) if latest_live is not None else None,
        "prospective_validation_readiness": readiness,
        "optimization_readiness": "OPTIMIZATION_NOT_READY",
        "optimization_reason": "No approved portfolio-level future outcomes exist; research backfills are excluded from live evidence.",
        "direct_source_integration_state": "NOT_IMPLEMENTED; no direct official portfolio performance source is currently admitted.",
        "caveats": [
            "The pipeline preserves evidence now but cannot create realized portfolio outcomes without a later direct official source.",
            "A research backfill tests schema only and is not prospective evidence.",
        ],
    }
    payload["audit_fingerprint"] = _fingerprint(payload)
    return payload


def write_prospective_validation_audit(path: Path, payload: Mapping[str, object]) -> None:
    """Atomically write deterministic audit JSON with no volatile metadata."""
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


def _candidate_payloads(
    ranking: Sequence[CandidateEvaluation], grouped: Mapping[str, Sequence[HoldingObservation]]
) -> list[dict[str, object]]:
    payloads = [_candidate_payload(candidate, grouped[candidate.metrics.portfolio_name]) for candidate in ranking]
    payloads.sort(key=lambda item: (_rank_sort_key(item["rank"]), str(item["portfolio_id"])))
    return payloads


def _candidate_payload(candidate: CandidateEvaluation, holdings: Sequence[HoldingObservation]) -> dict[str, object]:
    composition = [_holding_payload(item) for item in holdings]
    composition.sort(key=lambda item: (str(item["isin"]), str(item["product"]), str(item["currency"])))
    structure = portfolio_structure(list(holdings))
    metrics = asdict(candidate.metrics)
    features = {
        key: value
        for key, value in metrics.items()
        if key not in {"portfolio_name", "allocation_total", "unavailable_metrics", "warnings"}
    }
    normalized = {item.metric: item.normalized_value for item in candidate.contributions}
    contributions = {
        item.metric: {
            "raw_value": item.raw_value,
            "normalized_value": item.normalized_value,
            "weight": item.weight,
            "contribution": item.contribution,
        }
        for item in candidate.contributions
    }
    return {
        "portfolio_id": candidate.metrics.portfolio_name,
        "portfolio_name": candidate.metrics.portfolio_name,
        "rank": candidate.rank,
        "total_score": candidate.total_score,
        "ranking_eligible": candidate.eligible,
        "ranking_rejection_reasons": list(candidate.rejection_reasons),
        "candidate_feature_values": features,
        "candidate_normalized_values": normalized,
        "candidate_weighted_contributions": contributions,
        "portfolio_composition": composition,
        "constituent_isins": [item["isin"] for item in composition],
        "constituent_weights": [item["allocation"] for item in composition],
        "portfolio_currency": structure.get("portfolio_currency"),
        "composition_structure": structure,
    }


def _holding_payload(item: HoldingObservation) -> dict[str, object]:
    return {
        "portfolio_name": item.portfolio_name,
        "product": item.product,
        "isin": item.isin,
        "allocation": item.allocation,
        "currency": item.currency,
        "currency_risk": item.currency_risk,
        "asset_class": item.asset_class,
        "return_1y": item.return_1y,
        "sharpe_ratio_1y": item.sharpe_ratio_1y,
        "volatility_1y": item.volatility_1y,
        "downside_risk": item.downside_risk,
        "maximum_drawdown": item.maximum_drawdown,
    }


def _source_snapshot_payload(grouped: Mapping[str, Sequence[HoldingObservation]]) -> dict[str, object]:
    return {
        portfolio_id: sorted(
            (_holding_payload(item) for item in holdings),
            key=lambda item: (str(item["isin"]), str(item["product"]), str(item["currency"])),
        )
        for portfolio_id, holdings in sorted(grouped.items())
    }


def _group_holdings(holdings: Sequence[HoldingObservation]) -> dict[str, list[HoldingObservation]]:
    grouped: dict[str, list[HoldingObservation]] = {}
    for holding in holdings:
        grouped.setdefault(holding.portfolio_name, []).append(holding)
    return grouped


def _knowledge_payload(item: KnowledgeItem) -> dict[str, object]:
    value = asdict(item)  # KnowledgeItem is a frozen dataclass supplied by the feature layer.
    return {str(key): _json_value(raw) for key, raw in sorted(value.items())}


def _finalize_record(draft: Mapping[str, object]) -> dict[str, object]:
    record = _json_value(draft)
    if not isinstance(record, dict):
        raise ProspectiveValidationError("prospective decision must be an object")
    _validate_draft(record)
    if record.get("lifecycle_status") not in {DRAFT, FINALIZED}:
        raise ProspectiveValidationError("decision lifecycle status is invalid")
    record["lifecycle_status"] = FINALIZED
    record.pop("record_fingerprint", None)
    record["record_fingerprint"] = _fingerprint(record)
    return record


def _validate_draft(record: Mapping[str, object]) -> None:
    required = (
        "decision_id", "decision_date", "information_date", "record_type", "policy_id", "policy_version",
        "policy_fingerprint", "portfolio_universe_fingerprint", "decision_identity_fingerprint",
        "full_candidate_ranking", "selected_portfolio_id",
        "point_in_time_dataset_fingerprint", "source_evidence_fingerprint", "graphify_knowledge_fingerprint",
        "candidate_count", "eligible_candidate_count", "rejected_candidate_count", "selected_portfolio_name",
        "selected_rank", "selected_score", "candidate_scores", "candidate_feature_values",
        "candidate_normalized_values", "candidate_weighted_contributions", "portfolio_composition",
        "constituent_isins", "constituent_weights", "portfolio_currency", "strict_eligibility_result",
        "blocking_isins", "blocking_categories", "source_evidence_state", "strict_eligibility_fingerprint",
        "graphify_constraints_used",
        "graphify_knowledge_available_at_decision", "graphify_knowledge_ids", "graphify_source_document_ids",
        "graphify_knowledge_categories", "graphify_warnings", "outcome_contract",
    )
    if any(name not in record for name in required):
        raise ProspectiveValidationError("decision record is missing required point-in-time evidence")
    decision_date = _parse_date(record["decision_date"], "decision_date")
    information_date = _parse_date(record["information_date"], "information_date")
    if information_date > decision_date:
        raise ProspectiveValidationError("future information cannot enter a decision record")
    if record.get("record_type") not in {LIVE_RECORD, RESEARCH_BACKFILL}:
        raise ProspectiveValidationError("unknown prospective record type")
    candidates = record.get("full_candidate_ranking")
    if not isinstance(candidates, list) or not candidates:
        raise ProspectiveValidationError("full candidate universe is required")
    ids = [item.get("portfolio_id") for item in candidates if isinstance(item, dict)]
    if len(ids) != len(candidates) or any(not isinstance(item, str) or not item for item in ids):
        raise ProspectiveValidationError("candidate universe is malformed")
    if len(set(ids)) != len(ids):
        raise ProspectiveValidationError("candidate universe contains duplicate portfolio identifiers")
    counts = (record["candidate_count"], record["eligible_candidate_count"], record["rejected_candidate_count"])
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counts):
        raise ProspectiveValidationError("candidate counts must be non-negative integers")
    candidate_count, eligible_count, rejected_count = (cast(int, value) for value in counts)
    if candidate_count != len(candidates) or eligible_count + rejected_count != candidate_count:
        raise ProspectiveValidationError("candidate counts do not reconcile with the preserved universe")
    if record.get("selected_portfolio_id") not in ids:
        raise ProspectiveValidationError("selected portfolio is not present in candidate universe")
    if record.get("selected_rank") != 1:
        raise ProspectiveValidationError("selected portfolio must preserve deterministic rank one")
    selected_score = record.get("selected_score")
    if isinstance(selected_score, bool) or not isinstance(selected_score, (int, float)) or not math.isfinite(selected_score):
        raise ProspectiveValidationError("selected score must be finite")
    candidate_ids = set(cast(list[str], ids))
    for name in (
        "candidate_scores", "candidate_feature_values", "candidate_normalized_values",
        "candidate_weighted_contributions", "portfolio_composition", "constituent_isins",
        "constituent_weights", "portfolio_currency",
    ):
        value = record[name]
        if not isinstance(value, dict) or set(value) != candidate_ids:
            raise ProspectiveValidationError(f"{name} does not reconcile with the candidate universe")
    strict = record["strict_eligibility_result"]
    if not isinstance(strict, dict) or strict.get("evaluated_at_decision_time") is not False:
        raise ProspectiveValidationError("future strict eligibility must remain pending at decision time")
    point_in_time = record.get("point_in_time_guard")
    if not isinstance(point_in_time, dict) or point_in_time.get("result") != "NO_LOOKAHEAD":
        raise ProspectiveValidationError("decision record lacks the required no-look-ahead assertion")


def _outcome_payload(
    outcome: ProspectiveOutcome,
    *,
    observation_start: str,
    observation_end: str,
) -> dict[str, object]:
    payload = {
        "decision_id": outcome.decision_id,
        "horizon_days": outcome.horizon_days,
        "portfolio_id": outcome.portfolio_id,
        "observation_start": observation_start,
        "observation_end": observation_end,
        "observation_information_date": outcome.observation_information_date.isoformat(),
        "source_type": outcome.source_type,
        "source_provider": outcome.source_provider,
        "source_identifier": outcome.source_identifier,
        "source_reference": outcome.source_reference,
        "local_artifact": outcome.local_artifact,
        "sha256_or_fingerprint": outcome.sha256_or_fingerprint,
        "currency": outcome.currency,
        "value_semantics": outcome.value_semantics,
        "metrics": dict(sorted(outcome.metrics.items())),
        "validation_status": outcome.validation_status,
    }
    payload["outcome_fingerprint"] = _fingerprint(payload)
    return payload


def _validate_outcome_metrics(metrics: Mapping[str, float]) -> None:
    allowed = {
        "forward_return",
        "forward_annualized_return",
        "forward_volatility",
        "forward_sharpe",
        "forward_mdd",
        "forward_var",
        "forward_cvar",
    }
    if any(name not in allowed for name in metrics):
        raise ProspectiveValidationError("outcome contains an unsupported metric")
    for name, value in metrics.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ProspectiveValidationError(f"outcome metric {name} must be finite")
        if name == "forward_volatility" and value < 0:
            raise ProspectiveValidationError("forward volatility cannot be negative")
        if name == "forward_mdd" and value > 0:
            raise ProspectiveValidationError("maximum drawdown must remain non-positive")
        if name in {"forward_return", "forward_annualized_return"} and value < -1:
            raise ProspectiveValidationError(f"outcome metric {name} cannot be less than -100%")
        if name in {"forward_var", "forward_cvar"} and value < 0:
            raise ProspectiveValidationError(f"outcome metric {name} must use the canonical non-negative loss convention")
    if "forward_var" in metrics and "forward_cvar" in metrics and metrics["forward_cvar"] < metrics["forward_var"]:
        raise ProspectiveValidationError("forward CVaR cannot be lower than forward VaR")


def _readiness(live_available: Mapping[int, int], live_ids: set[str]) -> str:
    if not live_ids or not any(live_available.values()):
        return "PROSPECTIVE_VALIDATION_NOT_READY"
    # A cohort threshold is intentionally not fabricated here.  The ledger
    # reports its evidence counts for a separately governed validation plan.
    return "PROSPECTIVE_VALIDATION_PARTIALLY_READY"


def _counter(values: Sequence[str] | Any) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        result[value] = result.get(value, 0) + 1
    return dict(sorted(result.items()))


def _parse_date(value: object, label: str) -> date:
    if not isinstance(value, str):
        raise ProspectiveValidationError(f"{label} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ProspectiveValidationError(f"{label} must be an ISO date") from error


def _rank_sort_key(value: object) -> int:
    return int(value) if isinstance(value, int) else 2**31 - 1


def _relative_repository_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise ProspectiveValidationError("path must be repository-relative") from error


def _artifact_reference(path: Path, root: Path) -> dict[str, str]:
    return {"path": _relative_repository_path(path, root), "sha256": _sha256(path)}


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise ProspectiveValidationError(f"cannot read evidence artifact: {path}") from error


def _load_object(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProspectiveValidationError(f"{label} is missing or malformed") from error
    if not isinstance(value, dict):
        raise ProspectiveValidationError(f"{label} must be an object")
    return value


def _stored_record(value: str) -> dict[str, object]:
    try:
        record = json.loads(value)
    except json.JSONDecodeError as error:
        raise ProspectiveValidationError("stored prospective decision is malformed") from error
    if not isinstance(record, dict):
        raise ProspectiveValidationError("stored prospective decision must be an object")
    return record


def _repository_relative(value: str) -> bool:
    path = Path(value)
    return not path.is_absolute() and ".." not in path.parts and bool(path.parts)


def _json_value(value: object) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _canonical_json(value: object) -> str:
    return canonical_json(_json_value(value))


def _fingerprint(value: object) -> str:
    return canonical_fingerprint(_json_value(value))
