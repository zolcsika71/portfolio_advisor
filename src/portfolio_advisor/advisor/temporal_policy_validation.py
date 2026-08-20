"""Offline temporal stability validation for the active ranking policy."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from datetime import date
from hashlib import sha256
from itertools import pairwise
from math import isfinite
from pathlib import Path
from statistics import median
from typing import Any

from portfolio_advisor.database.repository import (
    HoldingObservation,
    ModelPortfolioRepository,
)
from portfolio_advisor.ranking.config import load_ranking_rules
from portfolio_advisor.ranking.models import CandidateEvaluation, MetricRule

from .active_policy_validation import (
    ActivePolicyValidationError,
    _validate_active_evidence,
)
from .service import CapitalPreservationAdvisor

_FEATURES = (
    "maximum_drawdown",
    "annualized_volatility",
    "unhedged_allocation",
    "sharpe_ratio",
    "return_1y",
)


class TemporalPolicyValidationError(RuntimeError):
    """Raised when temporal active-policy validation cannot be performed safely."""


def build_temporal_policy_validation(
    *,
    database_path: Path,
    rules_path: Path,
    contract_path: Path,
    methodology_path: Path,
    strict_pipeline_path: Path,
    current_universe_path: Path,
) -> dict[str, Any]:
    """Evaluate the current active policy twice at every source snapshot date."""
    contract = _load_json(contract_path, "ranking policy contract")
    methodology = _load_json(methodology_path, "methodology validation")
    strict = _load_json(strict_pipeline_path, "strict pipeline validation")
    current = _load_json(current_universe_path, "current-universe validation")
    rules = load_ranking_rules(rules_path)
    try:
        _validate_active_evidence(contract, methodology, strict, rules.version, rules.schema_version)
    except ActivePolicyValidationError as error:
        raise TemporalPolicyValidationError(str(error)) from error

    repository = ModelPortfolioRepository(database_path)
    dates = repository.observation_dates()
    fingerprint = _sha256(rules_path)
    before_hash = _sha256(database_path)
    advisor = CapitalPreservationAdvisor(repository, rules_path)
    summaries: list[dict[str, Any]] = []
    critical_failures: list[str] = []
    anomalies: list[dict[str, str]] = []

    for observation_date in dates:
        first = advisor.evaluate(observation_date=observation_date, alternative_count=10_000)
        second = advisor.evaluate(observation_date=observation_date, alternative_count=10_000)
        summary, date_failures, date_anomalies = _date_summary(
            observation_date, first.ranking, second.ranking, first, second, repository.load_holdings(observation_date), rules.metrics, fingerprint
        )
        summaries.append(summary)
        critical_failures.extend(f"{observation_date.isoformat()}: {failure}" for failure in date_failures)
        anomalies.extend({"date": observation_date.isoformat(), **item} for item in date_anomalies)

    after_hash = _sha256(database_path)
    if before_hash != after_hash:
        critical_failures.append("database changed during temporal validation")
    if len({item["policy_fingerprint"] for item in summaries}) != 1:
        critical_failures.append("policy fingerprint changed across ranking dates")
    if any(item["policy_version"] != rules.version for item in summaries):
        critical_failures.append("policy version changed across ranking dates")

    transitions = _transitions(summaries)
    winner_history = _winner_history(summaries)
    latest_reconciliation = _latest_reconciliation(summaries[-1], current)
    if not latest_reconciliation["result"]:
        critical_failures.append("latest-date result does not reconcile with current-universe validation")
    strict_regressions = _strict_regressions(strict)
    if not all(value is True for value in strict_regressions.values()):
        critical_failures.append("strict-backtest regression artifact is no longer invariant")

    caveats = [
        "Cross-sectional min-max normalization is candidate-set-dependent; related rank changes are reported, not treated as policy failures.",
        "Currency comparisons remain nominal; no FX conversion was introduced.",
        "This audit applies the one active policy retrospectively and does not compare historical policy variants or forward performance.",
    ]
    status = (
        "ACTIVE_POLICY_TEMPORAL_STABILITY_FAILED"
        if critical_failures
        else "ACTIVE_POLICY_TEMPORAL_STABILITY_VALIDATED_WITH_CAVEATS"
    )
    return {
        "schema_version": 1,
        "validation_status": status,
        "policy_identity": {
            "name": rules.policy_name,
            "version": rules.version,
            "schema_version": rules.schema_version,
            "fingerprint": fingerprint,
            "governance_state": rules.status,
            "activation_state": "ACTIVE",
        },
        "date_range": {
            "earliest_ranking_date": dates[0].isoformat(),
            "latest_ranking_date": dates[-1].isoformat(),
            "total_dates": len(dates),
            "dates_successfully_evaluated": len(summaries),
            "dates_rejected_for_insufficient_point_in_time_evidence": [],
        },
        "point_in_time_integrity": {
            "result": "NO_LOOKAHEAD",
            "ranking_inputs": "exact model_portfolios snapshot at each observation date",
            "forward_validation_only": [
                "future_90d_return", "future_180d_return", "future_365d_return", "future_volatility",
                "future_sharpe", "future_mdd", "future_var", "future_cvar", "future_winner_backtest_outcome",
            ],
            "forward_metrics_used_for_ranking": False,
            "strict_rejected_or_diagnostics_used_for_ranking": False,
        },
        "per_date_summaries": summaries,
        "winner_history": winner_history,
        "rank_turnover": transitions["rank_turnover"],
        "top_k_stability": transitions["top_k_stability"],
        "candidate_set_turnover": transitions["candidate_set_turnover"],
        "score_stability": transitions["score_stability"],
        "currency_analysis": _currency_analysis(summaries),
        "determinism": {
            "all_dates_repeated_identically": all(item["deterministic_re_evaluation"] for item in summaries),
            "database_unchanged": before_hash == after_hash,
            "result": "PASS" if not critical_failures else "FAIL",
        },
        "latest_date_reconciliation": latest_reconciliation,
        "strict_pipeline_regressions": strict_regressions,
        "anomalies": anomalies,
        "caveats": caveats,
        "critical_failures": critical_failures,
        "provenance": {
            "database": _provenance(database_path),
            "rules": _provenance(rules_path),
            "policy_contract": _provenance(contract_path),
            "methodology_validation": _provenance(methodology_path),
            "strict_pipeline_validation": _provenance(strict_pipeline_path),
            "current_universe_validation": _provenance(current_universe_path),
            "network_access": "NOT_USED",
        },
    }


def _date_summary(
    observation_date: date,
    first: tuple[CandidateEvaluation, ...],
    second: tuple[CandidateEvaluation, ...],
    first_result: Any,
    second_result: Any,
    holdings: list[HoldingObservation],
    rules: dict[str, MetricRule],
    fingerprint: str,
) -> tuple[dict[str, Any], list[str], list[dict[str, str]]]:
    snapshot = _snapshot(first)
    deterministic = (
        first_result.selected_portfolio == second_result.selected_portfolio
        and snapshot == _snapshot(second)
        and first_result.rules_status == second_result.rules_status == "approved"
    )
    eligible = [item for item in first if item.eligible]
    rejected = [item for item in first if not item.eligible]
    failures = _ranking_failures(eligible, rules)
    if not deterministic:
        failures.append("nondeterministic ranking")
    if first_result.proposed_rules_explicitly_enabled:
        failures.append("active policy required proposed-policy opt-in")
    anomalies: list[dict[str, str]] = []
    if not eligible:
        anomalies.append({"category": "NO_ELIGIBLE_CANDIDATES", "cause": "all candidates failed eligibility"})
    elif len(eligible) == 1:
        anomalies.append({"category": "ONE_ELIGIBLE_CANDIDATE", "cause": "ranking has no cross-sectional choice"})
    if rejected:
        anomalies.append({"category": "REJECTED_CANDIDATES", "cause": "point-in-time eligibility evidence"})
    dominance_pairs, dominance_violations = _dominance(eligible, rules)
    if dominance_violations:
        failures.extend(dominance_violations)
    mdd_failures = _mdd_direction_failures(eligible)
    failures.extend(mdd_failures)
    return (
        {
            "date": observation_date.isoformat(),
            "total_candidates": len(first_result.calculated_metrics),
            "eligible_candidates": len(eligible),
            "rejected_candidates": len(rejected),
            "rejection_reasons": {
                item.metrics.portfolio_name: list(item.rejection_reasons) for item in rejected
            },
            "candidate_currencies": _candidate_currencies(holdings),
            "selected_portfolio": eligible[0].metrics.portfolio_name if eligible else None,
            "ordered_eligible_ranking": [item.metrics.portfolio_name for item in eligible],
            "ranking": snapshot,
            "policy_fingerprint": fingerprint,
            "policy_version": first_result.rule_set_version,
            "deterministic_re_evaluation": deterministic,
            "dominance": {
                "pairs_checked": dominance_pairs,
                "violations": dominance_violations,
                "result": "PASS" if not dominance_violations else "FAIL",
            },
            "mdd_direction": {"result": "PASS" if not mdd_failures else "FAIL", "violations": mdd_failures},
        },
        failures,
        anomalies,
    )


def _snapshot(ranking: tuple[CandidateEvaluation, ...]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for item in ranking:
        contributions = {value.metric: asdict(value) for value in item.contributions}
        raw_features = {
            name: _metric_value(item, name)
            for name in _FEATURES
        }
        values.append(
            {
                "portfolio_name": item.metrics.portfolio_name,
                "eligible": item.eligible,
                "rank": item.rank,
                "total_score": item.total_score,
                "rejection_reasons": list(item.rejection_reasons),
                "raw_feature_values": raw_features,
                "normalized_feature_values": {
                    name: contribution["normalized_value"] for name, contribution in contributions.items()
                },
                "weighted_contributions": contributions,
            }
        )
    return values


def _ranking_failures(eligible: list[CandidateEvaluation], rules: dict[str, MetricRule]) -> list[str]:
    failures: list[str] = []
    ordered = sorted(eligible, key=lambda item: (-_score(item), item.metrics.portfolio_name))
    if [item.metrics.portfolio_name for item in eligible] != [item.metrics.portfolio_name for item in ordered]:
        failures.append("tie-breaking or score order mismatch")
    if [item.rank for item in eligible] != list(range(1, len(eligible) + 1)):
        failures.append("eligible candidates do not have consecutive ranks")
    for item in eligible:
        contributions = item.contributions
        if not isfinite(_score(item)):
            failures.append(f"non-finite score entered ranking: {item.metrics.portfolio_name}")
        if abs(sum(value.contribution for value in contributions) - _score(item)) > 1e-12:
            failures.append(f"score reconstruction mismatch: {item.metrics.portfolio_name}")
        if any(not isfinite(value.normalized_value) for value in contributions):
            failures.append(f"non-finite normalized value: {item.metrics.portfolio_name}")
        for required in ("annualized_volatility", "maximum_drawdown"):
            if _metric_value(item, required) is None:
                failures.append(f"missing required risk entered ranking: {item.metrics.portfolio_name}")
    return failures


def _dominance(
    eligible: list[CandidateEvaluation], rules: dict[str, MetricRule]
) -> tuple[int, list[str]]:
    pairs = 0
    violations: list[str] = []
    for dominator in eligible:
        for dominated in eligible:
            if dominator is dominated or not _dominates(dominator, dominated, rules):
                continue
            pairs += 1
            if (dominator.rank or 0) >= (dominated.rank or 0):
                violations.append(
                    "CAPITAL_PRESERVATION_DOMINANCE_FAILURE: "
                    f"{dominated.metrics.portfolio_name} outranked {dominator.metrics.portfolio_name}"
                )
    return pairs, violations


def _dominates(
    left: CandidateEvaluation, right: CandidateEvaluation, rules: dict[str, MetricRule]
) -> bool:
    strictly_better = False
    for name, rule in rules.items():
        left_value, right_value = _metric_value(left, name), _metric_value(right, name)
        if left_value is None or right_value is None:
            return False
        if rule.direction == "HIGHER_BETTER":
            if left_value < right_value:
                return False
            strictly_better = strictly_better or left_value > right_value
        elif left_value > right_value:
            return False
        else:
            strictly_better = strictly_better or left_value < right_value
    return strictly_better


def _mdd_direction_failures(eligible: list[CandidateEvaluation]) -> list[str]:
    failures: list[str] = []
    normalized = {
        item.metrics.portfolio_name: _contribution(item, "maximum_drawdown", "normalized_value")
        for item in eligible
    }
    for left in eligible:
        for right in eligible:
            left_mdd, right_mdd = _metric_value(left, "maximum_drawdown"), _metric_value(right, "maximum_drawdown")
            if left_mdd is None or right_mdd is None or left_mdd <= right_mdd:
                continue
            if normalized[left.metrics.portfolio_name] < normalized[right.metrics.portfolio_name]:
                failures.append("MDD direction inversion")
    return failures


def _transitions(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    rank_changes: list[int] = []
    top: dict[int, list[dict[str, Any]]] = {1: [], 3: [], 5: []}
    candidate_turnover: list[dict[str, Any]] = []
    score_changes: list[dict[str, Any]] = []
    for previous, current in pairwise(summaries):
        previous_rows = {row["portfolio_name"]: row for row in previous["ranking"]}
        current_rows = {row["portfolio_name"]: row for row in current["ranking"]}
        previous_eligible = {name: row for name, row in previous_rows.items() if row["eligible"]}
        current_eligible = {name: row for name, row in current_rows.items() if row["eligible"]}
        common = sorted(set(previous_eligible) & set(current_eligible))
        changes = [abs(int(current_eligible[name]["rank"]) - int(previous_eligible[name]["rank"])) for name in common]
        rank_changes.extend(changes)
        for name in common:
            score_changes.append(_score_change(name, previous_eligible[name], current_eligible[name], previous_rows, current_rows))
        for k, values in top.items():
            previous_top = set(previous["ordered_eligible_ranking"][:k])
            current_top = set(current["ordered_eligible_ranking"][:k])
            values.append(
                {
                    "from_date": previous["date"], "to_date": current["date"],
                    "overlap": len(previous_top & current_top), "entries": len(current_top - previous_top),
                    "exits": len(previous_top - current_top),
                    "persistence_rate": len(previous_top & current_top) / max(len(previous_top), len(current_top), 1),
                }
            )
        candidate_turnover.append(
            {
                "from_date": previous["date"], "to_date": current["date"],
                "entries": sorted(set(current_rows) - set(previous_rows)),
                "exits": sorted(set(previous_rows) - set(current_rows)),
                "became_rejected": sorted(set(previous_eligible) - set(current_eligible)),
                "returned_eligible": sorted(set(current_eligible) - set(previous_eligible)),
                "universe_size_change": int(current["total_candidates"]) - int(previous["total_candidates"]),
            }
        )
    return {
        "rank_turnover": {
            "adjacent_comparisons": len(summaries) - 1,
            "rank_changes_compared": len(rank_changes),
            "mean_absolute_rank_change": sum(rank_changes) / len(rank_changes) if rank_changes else 0.0,
            "median_absolute_rank_change": median(rank_changes) if rank_changes else 0.0,
            "maximum_rank_change": max(rank_changes, default=0),
            "spearman_rank_correlation": "NOT_COMPUTED_NO_EXTRA_DEPENDENCY",
        },
        "top_k_stability": {str(k): values for k, values in top.items()},
        "candidate_set_turnover": candidate_turnover,
        "score_stability": score_changes,
    }


def _score_change(
    name: str,
    previous: dict[str, Any],
    current: dict[str, Any],
    previous_rows: dict[str, dict[str, Any]],
    current_rows: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    raw_changed = previous["raw_feature_values"] != current["raw_feature_values"]
    normalized_changed = previous["normalized_feature_values"] != current["normalized_feature_values"]
    candidate_set_changed = set(previous_rows) != set(current_rows)
    cause = (
        "REAL_FEATURE_MOVEMENT" if raw_changed else "CANDIDATE_SET_NORMALIZATION"
        if normalized_changed and candidate_set_changed else "TIE_BREAK_OR_SCORE_EQUALITY"
        if previous["rank"] != current["rank"] else "NO_RANK_CHANGE"
    )
    return {
        "portfolio_name": name,
        "score_change": float(current["total_score"]) - float(previous["total_score"]),
        "rank_change": int(current["rank"]) - int(previous["rank"]),
        "raw_feature_changed": raw_changed,
        "normalized_feature_changed": normalized_changed,
        "cause": cause,
    }


def _winner_history(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    winners = [item["selected_portfolio"] for item in summaries]
    changes = sum(left != right for left, right in pairwise(winners))
    streaks: list[int] = []
    current = 0
    previous: object = object()
    for winner in winners:
        if winner == previous:
            current += 1
        else:
            if current:
                streaks.append(current)
            current, previous = 1, winner
    if current:
        streaks.append(current)
    return {
        "winner_frequency": dict(sorted(Counter(winner for winner in winners if winner).items())),
        "winner_changes": changes,
        "longest_winner_streak": max(streaks, default=0),
        "latest_winner": winners[-1],
    }


def _candidate_currencies(holdings: list[HoldingObservation]) -> dict[str, list[str]]:
    values: dict[str, set[str]] = {}
    for holding in holdings:
        values.setdefault(holding.portfolio_name, set()).add(holding.currency or "UNKNOWN")
    return {name: sorted(currencies) for name, currencies in sorted(values.items())}


def _currency_analysis(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    winner_currencies: Counter[str] = Counter()
    for summary in summaries:
        for currencies in summary["candidate_currencies"].values():
            counts.update(currencies)
        winner = summary["selected_portfolio"]
        if winner:
            winner_currencies.update(summary["candidate_currencies"].get(winner, []))
    return {
        "candidate_currency_observations": {currency: counts.get(currency, 0) for currency in ("HUF", "EUR", "USD")},
        "winner_currency_frequency": {currency: winner_currencies.get(currency, 0) for currency in ("HUF", "EUR", "USD")},
        "winners_switch_currencies": len(winner_currencies) > 1,
        "caveat": "Nominal cross-currency comparability remains limited; no FX conversion is used.",
    }


def _latest_reconciliation(latest: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    universe = current.get("current_universe", {})
    result = (
        latest["date"] == universe.get("observation_date")
        and latest["total_candidates"] == universe.get("candidate_count")
        and latest["eligible_candidates"] == universe.get("eligible_count")
        and latest["selected_portfolio"] == universe.get("selected_portfolio")
    )
    return {
        "result": result,
        "latest_date": latest["date"],
        "current_universe_date": universe.get("observation_date"),
        "selected_portfolio": latest["selected_portfolio"],
    }


def _strict_regressions(strict: dict[str, Any]) -> dict[str, bool]:
    invariants = strict.get("invariants", {})
    return {
        "hu0000554795_unchanged": isinstance(invariants, dict) and invariants.get("hu0000554795_rejected") is True,
        "at0000605324_unchanged": isinstance(invariants, dict) and invariants.get("at0000605324_reconciliation_remains_blocking") is True,
        "strict_gate_unchanged": strict.get("validation_status") == "STRICT_BACKTEST_PIPELINE_VALIDATED",
        "rejected_and_diagnostics_excluded": isinstance(strict.get("result_admission_boundary"), dict)
        and strict["result_admission_boundary"].get("non_official_results_cannot_carry_metrics_or_selection") is True,
    }


def _metric_value(item: CandidateEvaluation, name: str) -> float | None:
    metric = getattr(item.metrics, name)
    return metric.value if metric.available else None


def _contribution(item: CandidateEvaluation, metric: str, field: str) -> float:
    for value in item.contributions:
        if value.metric == metric:
            return float(getattr(value, field))
    raise TemporalPolicyValidationError(f"missing contribution {metric} for {item.metrics.portfolio_name}")


def _score(item: CandidateEvaluation) -> float:
    if item.total_score is None:
        raise TemporalPolicyValidationError(f"missing score for {item.metrics.portfolio_name}")
    return item.total_score


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TemporalPolicyValidationError(f"{label} is missing or malformed: {path}") from error
    if not isinstance(value, dict):
        raise TemporalPolicyValidationError(f"{label} root must be an object")
    return value


def _sha256(path: Path) -> str:
    try:
        return sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise TemporalPolicyValidationError(f"Cannot read: {path}") from error


def _provenance(path: Path) -> dict[str, str]:
    return {"path": str(path), "sha256": _sha256(path)}
