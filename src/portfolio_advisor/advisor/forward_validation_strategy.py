"""Fail-closed assessment of valid forward-validation evidence layers."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

STRATEGY_STATUS = "FORWARD_VALIDATION_STRATEGY_REASSESSED_WITH_CAVEATS"
OPTIMIZATION_NOT_READY = "OPTIMIZATION_NOT_READY"


@dataclass(frozen=True, slots=True)
class StrategyEvidence:
    applicable: bool
    requires_portfolio_performance: bool
    direct_official_portfolio_performance: bool
    diagnostic_only: bool
    caveats: bool


def classify_validation_strategy(evidence: StrategyEvidence) -> str:
    """Classify evidence capacity independently from desired outcomes."""
    if not evidence.applicable:
        return "NOT_APPLICABLE"
    if evidence.requires_portfolio_performance and not evidence.direct_official_portfolio_performance:
        return "BLOCKED"
    if evidence.diagnostic_only:
        return "DIAGNOSTIC_ONLY"
    if evidence.caveats:
        return "APPROVED_WITH_CAVEATS"
    return "APPROVED_VALIDATION_PATH"


def optimization_readiness(*, official_portfolio_label_count: int, diagnostic_evidence_only: bool) -> str:
    """Never permit performance optimization without valid portfolio outcomes."""
    if official_portfolio_label_count <= 0 or diagnostic_evidence_only:
        return OPTIMIZATION_NOT_READY
    return "OPTIMIZATION_READY_FOR_PORTFOLIO_FORWARD_PERFORMANCE"


def build_forward_validation_strategy_reassessment(
    *,
    repository_root: Path,
    freeze_path: Path,
    methodology_path: Path,
    label_store_path: Path,
    strict_validation_path: Path,
    feature_dataset_path: Path,
    temporal_policy_path: Path,
    current_policy_path: Path,
) -> dict[str, object]:
    """Assess evidence layers; do not derive performance data or alter policy."""
    paths = (
        freeze_path,
        methodology_path,
        label_store_path,
        strict_validation_path,
        feature_dataset_path,
        temporal_policy_path,
        current_policy_path,
    )
    evidence = [_reference(path, repository_root) for path in paths]
    freeze = _load_object(freeze_path)
    methodology = _load_object(methodology_path)
    labels = _load_object(label_store_path)
    strict = _load_object(strict_validation_path)
    features = _load_object(feature_dataset_path)
    temporal = _load_object(temporal_policy_path)
    current = _load_object(current_policy_path)
    if freeze.get("validation_status") != "PORTFOLIO_NAV_RECONSTRUCTION_FROZEN_UNRESOLVED":
        raise ValueError("portfolio-NAV reconstruction freeze is not active")
    official_labels = _integer(labels.get("available_label_count"), "available_label_count")
    candidate_labels = _integer(labels.get("candidate_label_count"), "candidate_label_count")
    strict_counts = strict.get("dataset")
    if not isinstance(strict_counts, dict):
        raise TypeError("strict validation dataset is malformed")
    strategies = [
        _strategy(
            "PORTFOLIO_RETURN_BACKTEST_VALIDATION",
            "Whether the ranking predicts realized portfolio capital preservation.",
            "Direct official portfolio NAV/performance series plus exact forward boundaries.",
            "No local official portfolio performance source; synthetic reconstruction is frozen.",
            StrategyEvidence(True, True, False, False, False),
            False,
            False,
            "Blocked by portfolio semantics, FX, total-return treatment, duplicate rows, and absent direct series.",
        ),
        _strategy(
            "POINT_IN_TIME_RANKING_STABILITY",
            "Determinism, winner persistence, turnover, monotonicity, dominance, and no-look-ahead.",
            "Point-in-time feature dataset and temporal/current-universe policy validations.",
            "Retained and validated with temporal caveats.",
            StrategyEvidence(True, False, False, False, True),
            False,
            False,
            "Structural/policy validation only; it does not establish realized performance.",
        ),
        _strategy(
            "CONSTITUENT_LEVEL_FORWARD_SIGNAL_VALIDATION",
            "Forward NAV-path behavior of individual constituents where exact approved coverage exists.",
            "Exact constituent interval coverage, approved NAV semantics, and currency-aware interpretation.",
            "19 retained NAV ISINs, but incomplete coverage and unresolved investor total-return semantics.",
            StrategyEvidence(True, False, False, True, True),
            False,
            False,
            "Useful descriptively for constituent risk/NAV signals only; it cannot validate portfolio performance or optimize portfolio ranking weights.",
        ),
        _strategy(
            "PORTFOLIO_RISK_EXPOSURE_VALIDATION_WITHOUT_RETURN",
            "Point-in-time concentration, HHI, top allocations, currency and asset-class exposure constraints.",
            "Dated portfolio snapshots and documented risk descriptors.",
            "Retained; allocation meaning remains insufficient for investable-return accounting.",
            StrategyEvidence(True, False, False, True, True),
            False,
            False,
            "Can test structural consistency, not forward capital preservation.",
        ),
        _strategy(
            "CROSS_SECTIONAL_CONSTITUENT_EVIDENCE",
            "Whether higher-ranked portfolios contain different point-in-time constituent risk descriptors.",
            "Point-in-time holdings plus approved constituent descriptors and exact coverage.",
            "Partially retained; portfolio aggregation and some constituent semantics remain unavailable.",
            StrategyEvidence(True, False, False, True, True),
            False,
            False,
            "Descriptive association only; constituent evidence cannot be reported as portfolio realized return.",
        ),
        _strategy(
            "EXTERNAL_OFFICIAL_PORTFOLIO_PERFORMANCE",
            "Whether an already-retained direct provider portfolio series can validate portfolio outcomes.",
            "Official provider-level historical portfolio performance export or NAV series.",
            "NO_LOCAL_OFFICIAL_PORTFOLIO_PERFORMANCE_SOURCE.",
            StrategyEvidence(True, True, False, False, False),
            False,
            False,
            "No network acquisition was performed; none may be inferred from constituent data.",
        ),
        _strategy(
            "PROSPECTIVE_PORTFOLIO_VALIDATION_DESIGN",
            "How future decisions can be evaluated from contemporaneously recorded official outcomes.",
            "Decision date, ranking, composition, approved source state, and later direct official portfolio observation.",
            "Sufficient evidence to specify a fail-closed design, but not yet realized outcomes.",
            StrategyEvidence(True, False, False, False, False),
            False,
            False,
            "Design only: no scheduler, data acquisition, or current performance claim is created.",
        ),
    ]
    payload: dict[str, object] = {
        "schema_version": 1,
        "validation_status": STRATEGY_STATUS,
        "evidence": evidence,
        "current_validation_hierarchy": [
            _level("L1", "data/source integrity", "APPROVED_VALIDATION_PATH"),
            _level("L2", "point-in-time integrity", "APPROVED_VALIDATION_PATH"),
            _level("L3", "ranking-methodology correctness", "APPROVED_VALIDATION_PATH"),
            _level("L4", "temporal ranking stability", "APPROVED_WITH_CAVEATS"),
            _level("L5", "constituent-level forward evidence", "DIAGNOSTIC_ONLY"),
            _level("L6", "portfolio-level forward performance", "BLOCKED"),
        ],
        "strategy_matrix": strategies,
        "current_state": {
            "portfolio_nav_freeze": freeze["validation_status"],
            "methodology_status": methodology["validation_status"],
            "candidate_label_count": candidate_labels,
            "official_portfolio_label_count": official_labels,
            "strict_eligible_windows": _integer(strict_counts.get("official_eligible_windows"), "official_eligible_windows"),
            "strict_rejected_windows": _integer(strict_counts.get("rejected_windows"), "rejected_windows"),
            "point_in_time_dataset_status": features.get("dataset_status"),
            "temporal_policy_status": temporal.get("validation_status"),
            "current_policy_status": current.get("validation_status"),
            "official_portfolio_source_status": "NO_LOCAL_OFFICIAL_PORTFOLIO_PERFORMANCE_SOURCE",
        },
        "graphify_role": {
            "allowed": ["timeless_methodology", "risk_constraints", "explainability", "validation_rule_provenance"],
            "prohibited": ["forward_labels", "realized_portfolio_performance", "portfolio_specific_facts_without_source"],
            "next_stage_use": "Methodology and explainability only; it must not become a historical outcome source.",
        },
        "prospective_design": {
            "required_records": ["decision_date", "portfolio_ranking", "portfolio_composition", "approved_source_state", "future_direct_official_portfolio_observation"],
            "rule": "Record each decision contemporaneously and admit later outcomes only from a retained direct official portfolio source.",
            "implementation_state": "DESIGN_ONLY",
        },
        "optimization_readiness": optimization_readiness(
            official_portfolio_label_count=official_labels,
            diagnostic_evidence_only=True,
        ),
        "is_policy_optimization_currently_approved": False,
        "recommended_next_path": "SEARCH_FOR_OFFICIAL_PORTFOLIO_PERFORMANCE_SOURCE",
        "caveats": [
            "Ranking stability and constituent diagnostics are not portfolio realized performance.",
            "No strategy may bypass the reconstruction freeze or strict eligibility.",
        ],
    }
    payload["evidence_fingerprint"] = _fingerprint(payload)
    return payload


def write_forward_validation_strategy_reassessment(path: Path, payload: dict[str, object]) -> None:
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


def _strategy(
    strategy: str,
    question: str,
    required: str,
    available: str,
    evidence: StrategyEvidence,
    portfolio_claim: bool,
    capital_preservation_claim: bool,
    limitations: str,
) -> dict[str, object]:
    return {
        "strategy": strategy,
        "question_answered": question,
        "evidence_required": required,
        "evidence_available": available,
        "lookahead_risk": "Controlled only by point-in-time inputs and exact future observations; no future outcome enters ranking.",
        "portfolio_performance_claim_allowed": portfolio_claim,
        "capital_preservation_claim_allowed": capital_preservation_claim,
        "limitations": limitations,
        "status": classify_validation_strategy(evidence),
    }


def _level(level: str, description: str, status: str) -> dict[str, str]:
    return {"level": level, "description": description, "status": status}


def _reference(path: Path, root: Path) -> dict[str, str]:
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise ValueError("strategy evidence path must be repository-relative") from error
    return {"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _load_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("strategy evidence must be a JSON object")
    return value


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _fingerprint(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
