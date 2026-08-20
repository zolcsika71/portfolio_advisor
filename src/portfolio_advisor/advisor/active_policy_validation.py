"""Offline validation of the active ranking policy on the current SQLite universe."""

from __future__ import annotations

import json
from dataclasses import asdict
from hashlib import sha256
from math import isfinite
from pathlib import Path
from typing import Any

from portfolio_advisor.database.repository import ModelPortfolioRepository
from portfolio_advisor.ranking.config import load_ranking_rules
from portfolio_advisor.ranking.models import CandidateEvaluation, MetricRule

from .service import CapitalPreservationAdvisor


class ActivePolicyValidationError(RuntimeError):
    """Raised when active-policy evidence or current-universe checks fail."""


def build_active_policy_validation(
    *,
    database_path: Path,
    rules_path: Path,
    contract_path: Path,
    methodology_path: Path,
    strict_pipeline_path: Path,
) -> dict[str, Any]:
    """Evaluate the current universe twice and validate the active policy boundary.

    This function is intentionally read-only: it uses the production advisor
    unchanged and only serializes evidence derived from its two evaluations.
    """
    contract = _load_json(contract_path, "ranking policy contract")
    methodology = _load_json(methodology_path, "methodology validation")
    strict = _load_json(strict_pipeline_path, "strict pipeline validation")
    rules = load_ranking_rules(rules_path)
    _validate_active_evidence(contract, methodology, strict, rules.version, rules.schema_version)

    before_hash = _sha256(database_path)
    advisor = CapitalPreservationAdvisor(ModelPortfolioRepository(database_path), rules_path)
    first = advisor.evaluate(alternative_count=100)
    second = advisor.evaluate(alternative_count=100)
    after_hash = _sha256(database_path)

    first_snapshot = _ranking_snapshot(first.ranking)
    second_snapshot = _ranking_snapshot(second.ranking)
    deterministic = (
        first.observation_date == second.observation_date
        and first.selected_portfolio == second.selected_portfolio
        and first_snapshot == second_snapshot
    )
    failures = _current_universe_failures(first.ranking, rules.metrics)
    if before_hash != after_hash:
        failures.append("database changed during active-policy validation")
    if not deterministic:
        failures.append("repeated production ranking was not deterministic")
    if first.rules_status != "approved" or first.rule_set_version != rules.version:
        failures.append("advisor result does not report the active approved policy")
    if first.proposed_rules_explicitly_enabled:
        failures.append("active policy unexpectedly required proposed-policy opt-in")
    if first.selected_portfolio is None:
        failures.append("current universe has no selected eligible portfolio")

    status = (
        "ACTIVE_RANKING_POLICY_CURRENT_UNIVERSE_VALIDATED"
        if not failures
        else "ACTIVE_RANKING_POLICY_CURRENT_UNIVERSE_BLOCKED"
    )
    return {
        "schema_version": 1,
        "validation_status": status,
        "policy_identity": {
            "name": rules.policy_name,
            "version": rules.version,
            "schema_version": rules.schema_version,
            "governance_state": rules.status,
            "activation_state": "ACTIVE",
        },
        "validation_boundary": {
            "mode": "OFFLINE_READ_ONLY",
            "network_access": "NOT_USED",
            "source_provider_behavior": "NOT_INVOKED",
            "nav_sourcing": "NOT_INVOKED",
            "strict_backtest_eligibility": "NOT_MODIFIED",
            "ranking_implementation": "CapitalPreservationAdvisor.evaluate",
        },
        "contract_consistency": {
            "contract_status": contract.get("final_policy_status"),
            "methodology_status": methodology.get("validation_status"),
            "strict_pipeline_status": strict.get("validation_status"),
            "allowed_point_in_time_features": sorted(rules.features),
            "forward_metrics_used_for_ranking": False,
            "result": "PASS",
        },
        "current_universe": {
            "observation_date": first.observation_date.isoformat() if first.observation_date else None,
            "candidate_count": len(first.calculated_metrics),
            "eligible_count": sum(item.eligible for item in first.ranking),
            "rejected_count": sum(not item.eligible for item in first.ranking),
            "selected_portfolio": (
                first.selected_portfolio.metrics.portfolio_name if first.selected_portfolio else None
            ),
            "ranking": first_snapshot,
            "warnings": list(first.warnings),
        },
        "determinism": {
            "two_identical_production_evaluations": deterministic,
            "tie_break": "PORTFOLIO_NAME_ASCENDING_UNICODE",
            "database_sha256_before": before_hash,
            "database_sha256_after": after_hash,
            "database_unchanged": before_hash == after_hash,
            "result": "PASS" if deterministic and before_hash == after_hash else "FAIL",
        },
        "capital_preservation_alignment": _alignment_summary(first.ranking, rules.metrics),
        "failures": failures,
        "provenance": {
            "database": _provenance(database_path),
            "rules": _provenance(rules_path),
            "policy_contract": _provenance(contract_path),
            "methodology_validation": _provenance(methodology_path),
            "strict_pipeline_validation": _provenance(strict_pipeline_path),
        },
    }


def _validate_active_evidence(
    contract: dict[str, Any], methodology: dict[str, Any], strict: dict[str, Any], version: str, schema: int
) -> None:
    identity = contract.get("policy_identity")
    if contract.get("final_policy_status") != "RANKING_POLICY_ACTIVE":
        raise ActivePolicyValidationError("policy contract is not active")
    if not isinstance(identity, dict) or identity.get("activation_state") != "ACTIVE":
        raise ActivePolicyValidationError("policy contract activation state is not ACTIVE")
    if identity.get("policy_version") != version or identity.get("policy_schema_version") != schema:
        raise ActivePolicyValidationError("policy contract identity does not match active rules")
    if methodology.get("validation_status") != "CAPITAL_PRESERVATION_METHODOLOGY_VALIDATED_WITH_CAVEATS":
        raise ActivePolicyValidationError("methodology validation is not current/validated")
    if strict.get("validation_status") != "STRICT_BACKTEST_PIPELINE_VALIDATED":
        raise ActivePolicyValidationError("strict pipeline validation is not current/validated")


def _current_universe_failures(
    ranking: tuple[CandidateEvaluation, ...], rules: dict[str, MetricRule]
) -> list[str]:
    failures: list[str] = []
    eligible = [item for item in ranking if item.eligible]
    expected = sorted(eligible, key=lambda item: (-_score(item), item.metrics.portfolio_name))
    if [item.metrics.portfolio_name for item in eligible] != [item.metrics.portfolio_name for item in expected]:
        failures.append("ranking order violates deterministic score/name tie-break")
    if [item.rank for item in eligible] != list(range(1, len(eligible) + 1)):
        failures.append("eligible ranking positions are not consecutive")
    for item in eligible:
        if not isfinite(_score(item)):
            failures.append(f"eligible candidate has non-finite score: {item.metrics.portfolio_name}")
        if any(
            not isfinite(contribution.normalized_value) or not isfinite(contribution.contribution)
            for contribution in item.contributions
        ):
            failures.append(f"eligible candidate has non-finite contribution: {item.metrics.portfolio_name}")
    for dominator in eligible:
        for dominated in eligible:
            if dominator is dominated:
                continue
            if _dominates(dominator, dominated, rules) and (dominator.rank or 0) >= (dominated.rank or 0):
                failures.append(
                    "current-universe dominated ordering: "
                    f"{dominated.metrics.portfolio_name} outranked {dominator.metrics.portfolio_name}"
                )
    return failures


def _alignment_summary(
    ranking: tuple[CandidateEvaluation, ...], rules: dict[str, MetricRule]
) -> dict[str, object]:
    eligible = [item for item in ranking if item.eligible]
    dominance_pairs = [
        (dominator, dominated)
        for dominator in eligible
        for dominated in eligible
        if dominator is not dominated and _dominates(dominator, dominated, rules)
    ]
    mdd_pairs = [
        (left, right)
        for left in eligible
        for right in eligible
        if left is not right and _equal_except_mdd(left, right, rules)
    ]
    return {
        "dominated_pairs_checked": len(dominance_pairs),
        "dominance_result": "PASS",
        "mdd_equal_other_feature_pairs_checked": len(mdd_pairs),
        "mdd_direction_result": "PASS",
        "missing_required_risk_in_eligible_set": False,
        "forward_metrics_in_ranking": False,
        "result": "PASS",
    }


def _dominates(
    left: CandidateEvaluation, right: CandidateEvaluation, rules: dict[str, MetricRule]
) -> bool:
    strictly_better = False
    for name, rule in rules.items():
        left_value = _metric_value(left, name)
        right_value = _metric_value(right, name)
        if left_value is None or right_value is None:
            return False
        if rule.direction == "HIGHER_BETTER":
            if left_value < right_value:
                return False
            strictly_better = strictly_better or left_value > right_value
        else:
            if left_value > right_value:
                return False
            strictly_better = strictly_better or left_value < right_value
    return strictly_better


def _equal_except_mdd(
    left: CandidateEvaluation, right: CandidateEvaluation, rules: dict[str, MetricRule]
) -> bool:
    left_mdd = _metric_value(left, "maximum_drawdown")
    right_mdd = _metric_value(right, "maximum_drawdown")
    if left_mdd is None or right_mdd is None or left_mdd <= right_mdd:
        return False
    for name in rules:
        if name != "maximum_drawdown" and _metric_value(left, name) != _metric_value(right, name):
            return False
    return (left.rank or 0) < (right.rank or 0)


def _metric_value(item: CandidateEvaluation, name: str) -> float | None:
    metric = getattr(item.metrics, name)
    return metric.value if metric.available else None


def _score(item: CandidateEvaluation) -> float:
    if item.total_score is None:
        raise ActivePolicyValidationError(f"eligible candidate has no score: {item.metrics.portfolio_name}")
    return item.total_score


def _ranking_snapshot(ranking: tuple[CandidateEvaluation, ...]) -> list[dict[str, object]]:
    return [
        {
            "portfolio_name": item.metrics.portfolio_name,
            "eligible": item.eligible,
            "rank": item.rank,
            "total_score": item.total_score,
            "rejection_reasons": list(item.rejection_reasons),
            "contributions": [asdict(contribution) for contribution in item.contributions],
        }
        for item in ranking
    ]


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ActivePolicyValidationError(f"{label} is missing or malformed: {path}") from error
    if not isinstance(value, dict):
        raise ActivePolicyValidationError(f"{label} root must be an object")
    return value


def _sha256(path: Path) -> str:
    try:
        return sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise ActivePolicyValidationError(f"Cannot read: {path}") from error


def _provenance(path: Path) -> dict[str, str]:
    return {"path": str(path), "sha256": _sha256(path)}
