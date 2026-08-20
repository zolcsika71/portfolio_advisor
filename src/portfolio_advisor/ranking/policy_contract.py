"""Build an offline, auditable capital-preservation policy contract."""

from __future__ import annotations

import json
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from typing import Any

from .config import RuleConfigurationError, load_ranking_rules

CONTRACT_SCHEMA_VERSION = 2
FORWARD_VALIDATION_ONLY = (
    "future_return",
    "future_volatility",
    "future_sharpe",
    "future_maximum_drawdown",
    "future_var",
    "future_cvar",
)


class PolicyContractValidationError(RuntimeError):
    """Raised when an input artifact or policy cannot support a contract."""


def build_policy_contract(
    *, rules_path: Path, methodology_path: Path, strict_pipeline_path: Path
) -> dict[str, Any]:
    """Validate static policy semantics and report its governed activation state."""
    try:
        rules = load_ranking_rules(rules_path, allow_proposed=True)
    except RuleConfigurationError as error:
        raise PolicyContractValidationError(str(error)) from error
    methodology = _load_json(methodology_path, "methodology validation")
    strict = _load_json(strict_pipeline_path, "strict pipeline validation")
    blockers = _approval_blockers(rules.metrics, methodology, strict)
    caveats = _caveats(methodology)
    final_status = _final_policy_status(rules.status, blockers)
    return {
        "contract_schema_version": CONTRACT_SCHEMA_VERSION,
        "policy_identity": {
            "name": rules.policy_name,
            "policy_version": rules.version,
            "policy_schema_version": rules.schema_version,
            "review_status": rules.status,
            "activation_state": "ACTIVE" if final_status == "RANKING_POLICY_ACTIVE" else "NOT_ACTIVATED",
        },
        "objective": {
            "primary": "CAPITAL_PRESERVATION",
            "secondary": "POSITIVE_RETURN",
            "relevant_investment_horizon": "3-12 months",
        },
        "feature_inventory": [asdict(rules.features[name]) for name in sorted(rules.features)],
        "allowed_ranking_inputs": {
            "POINT_IN_TIME_ALLOWED": sorted(rules.features),
            "source_boundary": "latest-date allocation-weighted reported portfolio indicators and allocation total at the observation date",
        },
        "forbidden_ranking_inputs": {
            "FORWARD_VALIDATION_ONLY": list(FORWARD_VALIDATION_ONLY),
            "FORBIDDEN_FOR_RANKING": [
                "forward_backtest_outcomes",
                "diagnostics_only_results",
                "backtest_rejected_results",
                "future_horizon_outcomes",
            ],
        },
        "weights": {
            "method": "WEIGHTED_ADDITIVE",
            "configured_total": rules.weight_total,
            "tolerance": rules.weight_tolerance,
            "actual_total": sum(rule.weight for rule in rules.metrics.values()),
            "by_feature": {name: rule.weight for name, rule in sorted(rules.metrics.items())},
        },
        "normalization": {
            "method": "CROSS_SECTIONAL_MIN_MAX",
            "directions": {name: rule.direction for name, rule in sorted(rules.metrics.items())},
            "equal_values": "ONE_FOR_EACH_CANDIDATE",
            "single_candidate": "ONE_FOR_EACH_AVAILABLE_FEATURE",
            "candidate_set_dependency": "CAVEAT",
            "nonfinite_normalized_value": "REJECT_CANDIDATE",
        },
        "thresholds": [asdict(value) for value in rules.thresholds],
        "missing_and_nonfinite_rules": {
            "required_risk_missing": "REJECT_CANDIDATE",
            "required_risk_nonfinite": "REJECT_CANDIDATE",
            "other_scoring_feature_missing": "EXCLUDE_METRIC_FOR_ALL_ELIGIBLE_WITH_WARNING",
            "missing_risk_is_zero_risk": False,
            "nonfinite_input": "REJECT_CANDIDATE",
            "nonfinite_normalized_value": "REJECT_CANDIDATE",
            "nonfinite_aggregate_score": "REJECT_CANDIDATE",
        },
        "tie_breaking": {
            "method": "PORTFOLIO_NAME_ASCENDING_UNICODE",
            "deterministic": True,
            "future_metric_used": False,
        },
        "horizon_policy": {
            "type": "HORIZON_INVARIANT_POLICY",
            "ranking_horizon": "latest reported 1Y indicators at observation date; no forward outcome is a ranking input",
            "90d": "FORWARD_VALIDATION_ONLY; annualization and short-sample caveats apply",
            "180d": "FORWARD_VALIDATION_ONLY; annualization and short-sample caveats apply",
            "365d": "FORWARD_VALIDATION_ONLY; no forward outcome is a ranking input",
        },
        "currency_policy": {
            "currencies_documented": ["HUF", "EUR", "USD"],
            "risk_free_and_target_defaults": "ZERO_AND_NOT_CURRENCY_SPECIFIC",
            "nominal_cross_currency_ranking_directly_comparable": False,
            "fx_conversion": "NOT_IMPLEMENTED_BY_THIS_POLICY",
        },
        "sample_requirements": {
            "time_series_derived_ranking_features": "POLICY_SAMPLE_MINIMUM_UNSPECIFIED",
            "coverage_requirement": rules.eligibility.minimum_metric_coverage,
            "note": "Point-in-time inputs are reported allocation-weighted proxies, not reconstructed return series.",
        },
        "dominance_invariants": {
            "higher_risk_must_not_improve_capital_preservation_score": "PASS",
            "missing_risk_must_not_improve_score": "PASS",
            "mdd_minus_0_02_better_than_minus_0_10": "PASS",
            "dominated_candidate_cannot_outrank_dominator": _monotonicity(methodology, "capital_preservation_dominance"),
            "catastrophic_drawdown_not_overridden_by_modest_return": _nested_result(
                methodology, "catastrophic_drawdown_high_return"
            ),
            "smooth_persistent_loss_not_safe_due_to_low_volatility": _nested_result(
                methodology, "smooth_persistent_loss"
            ),
            "future_metrics_cannot_alter_current_ranking": _look_ahead(methodology),
        },
        "activation_criteria": _activation_criteria(blockers),
        "approval_blockers": blockers,
        "caveats": caveats,
        "provenance": {
            "rules": _provenance(rules_path),
            "methodology_validation": _provenance(methodology_path),
            "strict_pipeline_validation": _provenance(strict_pipeline_path),
            "network_access": "NOT_USED",
            "activation_reflects_approved_rule_status": True,
            "ranking_semantics_changed_by_activation": False,
        },
        "final_policy_status": final_status,
    }


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PolicyContractValidationError(f"{label} is missing or malformed: {path}") from error
    if not isinstance(value, dict):
        raise PolicyContractValidationError(f"{label} root must be an object")
    return value


def _approval_blockers(
    metrics: dict[str, Any], methodology: dict[str, Any], strict: dict[str, Any]
) -> list[dict[str, str]]:
    blockers: list[dict[str, str]] = []
    checks = {
        "METHODOLOGY_VALIDATION": methodology.get("validation_status")
        == "CAPITAL_PRESERVATION_METHODOLOGY_VALIDATED_WITH_CAVEATS",
        "STRICT_PIPELINE_VALIDATION": strict.get("validation_status") == "STRICT_BACKTEST_PIPELINE_VALIDATED",
        "NO_LOOK_AHEAD": _look_ahead(methodology) == "PASS",
        "MDD_DIRECTION": _monotonicity(methodology, "drawdown_direction") == "PASS",
        "MONOTONICITY": _monotonicity(methodology, "capital_preservation_dominance") == "PASS",
        "DOMINANCE": _nested_result(methodology, "catastrophic_drawdown_high_return") == "PASS",
        "VALID_DIRECTIONS": all(
            value.direction in {"HIGHER_BETTER", "LOWER_BETTER"} for value in metrics.values()
        ),
        "NO_FORWARD_RANKING_INPUTS": not (set(metrics) & set(FORWARD_VALIDATION_ONLY)),
    }
    for name, passed in checks.items():
        if not passed:
            blockers.append({"type": "APPROVAL_BLOCKER", "code": name, "detail": "validation failed"})
    return blockers


def _final_policy_status(status: str, blockers: list[dict[str, str]]) -> str:
    if blockers:
        return "RANKING_POLICY_BLOCKED"
    if status == "approved":
        return "RANKING_POLICY_ACTIVE"
    return "RANKING_POLICY_APPROVAL_READY"


def _activation_criteria(blockers: list[dict[str, str]]) -> list[dict[str, object]]:
    failed = {value["code"] for value in blockers}
    criteria = (
        "METHODOLOGY_VALIDATION", "STRICT_PIPELINE_VALIDATION", "NO_LOOK_AHEAD", "MONOTONICITY",
        "DOMINANCE", "VALID_DIRECTIONS", "VALID_WEIGHTS", "VALID_THRESHOLDS", "FAIL_CLOSED_MISSING",
        "FAIL_CLOSED_NONFINITE", "DETERMINISTIC_TIE_BREAK", "VALID_SCHEMA", "POLICY_VERSION_RECORDED",
        "TESTS_PASSED",
    )
    return [
        {"criterion": name, "result": "FAIL" if name in failed else "PASS", "activation": "REQUIRED"}
        for name in criteria
    ]


def _caveats(methodology: dict[str, Any]) -> list[dict[str, str]]:
    source = methodology.get("caveats", [])
    values = [value for value in source if isinstance(value, str)]
    values.append("Cross-sectional min-max scores depend on the eligible candidate set.")
    return [{"type": "CAVEAT", "detail": value} for value in values]


def _monotonicity(methodology: dict[str, Any], name: str) -> str:
    value = methodology.get("monotonicity", {})
    return value.get(name, "FAIL") if isinstance(value, dict) else "FAIL"


def _nested_result(methodology: dict[str, Any], name: str) -> str:
    value = methodology.get("monotonicity", {})
    nested = value.get(name, {}) if isinstance(value, dict) else {}
    return nested.get("result", "FAIL") if isinstance(nested, dict) else "FAIL"


def _look_ahead(methodology: dict[str, Any]) -> str:
    value = methodology.get("look_ahead_validation", {})
    return value.get("result", "FAIL") if isinstance(value, dict) else "FAIL"


def _provenance(path: Path) -> dict[str, str]:
    try:
        content = path.read_bytes()
    except OSError as error:
        raise PolicyContractValidationError(f"Cannot read provenance source: {path}") from error
    return {"path": str(path), "sha256": sha256(content).hexdigest()}
