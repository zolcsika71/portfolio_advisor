"""Fail-closed offline validation of forward rank signal evidence.

This module validates labels; it does not tune, refit, or otherwise change the
active ranking policy.  It intentionally returns insufficient evidence when the
strict backtester cannot produce official forward outcomes.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import date
from hashlib import sha256
from math import ceil, isfinite
from pathlib import Path
from typing import Any, Literal

from portfolio_advisor.database.repository import ModelPortfolioRepository
from portfolio_advisor.history.repository import HistoricalPortfolioRepository
from portfolio_advisor.ranking.config import load_ranking_rules

STATUS_INSUFFICIENT = "FORWARD_RANK_SIGNAL_VALIDATION_INSUFFICIENT_LABEL_EVIDENCE"
STATUS_FAILED = "FORWARD_RANK_SIGNAL_VALIDATION_FAILED"
OFFICIAL = "OFFICIAL_BACKTEST"
HORIZONS = (90, 180, 365)
FORWARD_METRICS = (
    "forward_return",
    "forward_annualized_return",
    "forward_volatility",
    "forward_sharpe",
    "forward_mdd",
    "forward_var",
    "forward_cvar",
)
MetricDirection = Literal["HIGHER_BETTER", "LOWER_BETTER"]


class ForwardRankSignalValidationError(RuntimeError):
    """Raised when label provenance, timing, or active-policy evidence is unsafe."""


def build_forward_rank_signal_validation(
    *,
    dataset_path: Path,
    dataset_manifest_path: Path,
    database_path: Path,
    rules_path: Path,
    contract_path: Path,
    strict_pipeline_path: Path,
    methodology_path: Path,
    current_universe_path: Path,
    temporal_path: Path,
) -> dict[str, object]:
    """Validate available official labels, or report insufficient evidence.

    ``OFFICIAL_BACKTEST`` is the sole admissible label result type.  The
    current local database has no NAV history, so the expected operational
    outcome is a deterministic insufficient-evidence artifact, not proxy data.
    """
    rules = load_ranking_rules(rules_path)
    if rules.status != "approved" or rules.version != "1.0.1" or rules.schema_version != 2:
        raise ForwardRankSignalValidationError("active ranking policy identity is not v1.0.1/schema 2")
    contract = _load_object(contract_path, "policy contract")
    if contract.get("final_policy_status") != "RANKING_POLICY_ACTIVE":
        raise ForwardRankSignalValidationError("policy contract is not active")
    artifacts = {
        "strict_pipeline": _load_object(strict_pipeline_path, "strict validation"),
        "methodology": _load_object(methodology_path, "methodology validation"),
        "current_universe": _load_object(current_universe_path, "current-universe validation"),
        "temporal": _load_object(temporal_path, "temporal validation"),
    }
    _validate_regression_artifacts(artifacts)
    manifest = _load_object(dataset_manifest_path, "point-in-time dataset manifest")
    if manifest.get("dataset_status") not in {
        "POINT_IN_TIME_FEATURE_DATASET_VALIDATED",
        "POINT_IN_TIME_FEATURE_DATASET_VALIDATED_WITH_CAVEATS",
    }:
        raise ForwardRankSignalValidationError("point-in-time dataset is not validated")
    if _nested(manifest, "leakage_validation", "result") != "NO_POINT_IN_TIME_LEAKAGE":
        raise ForwardRankSignalValidationError("point-in-time dataset leakage validation is not clean")
    _validate_policy_fingerprint(manifest, rules_path)

    rows = _read_dataset(dataset_path)
    availability = label_availability(rows)
    history = HistoricalPortfolioRepository(ModelPortfolioRepository(database_path))
    nav_history_available = history.nav_history_available()
    if nav_history_available:
        # This task may safely use retained official labels if they are present,
        # but its current data contract has no rank/score columns for an
        # analysis join.  Failing prevents an ambiguous reconstruction.
        raise ForwardRankSignalValidationError(
            "official NAV history is present but the point-in-time dataset lacks required rank/score join fields"
        )
    if any(entry["admitted_official_labels"] for entry in availability.values()):
        raise ForwardRankSignalValidationError(
            "dataset claims official labels although no retained NAV history is available"
        )

    source_references = {
        "dataset": _provenance(dataset_path),
        "dataset_manifest": _provenance(dataset_manifest_path),
        "database": _provenance(database_path),
        "rules": _provenance(rules_path),
        "policy_contract": _provenance(contract_path),
        "strict_pipeline": _provenance(strict_pipeline_path),
        "methodology": _provenance(methodology_path),
        "current_universe": _provenance(current_universe_path),
        "temporal": _provenance(temporal_path),
    }
    total_admitted = sum(
        _integer(entry["admitted_official_labels"], "admitted_official_labels")
        for entry in availability.values()
    )
    return {
        "schema_version": 1,
        "validation_status": STATUS_INSUFFICIENT if total_admitted == 0 else STATUS_FAILED,
        "policy_identity": {
            "name": rules.policy_name,
            "version": rules.version,
            "schema_version": rules.schema_version,
            "policy_fingerprint": _sha256(rules_path),
            "activation_state": "ACTIVE",
        },
        "point_in_time_dataset": {
            "fingerprint": manifest.get("dataset_fingerprint"),
            "decision_date_range": manifest.get("decision_date_range"),
            "row_count": len(rows),
            "integrity": "NO_POINT_IN_TIME_LEAKAGE",
            "rank_score_columns_present": False,
        },
        "official_label_reconstruction": {
            "strict_backtester_boundary": "RETAINED_OFFLINE_NAV_ONLY",
            "nav_history_available": nav_history_available,
            "result": "INSUFFICIENT_RETAINED_OFFICIAL_NAV_EVIDENCE",
            "prohibited_substitutions": [
                "DIAGNOSTICS_ONLY", "BACKTEST_REJECTED_UNRESOLVED_CONSTITUENT", "proxy_returns",
                "cash_or_zero_return", "renormalization", "interpolation", "fill", "nearest_date",
                "source_stitching",
            ],
        },
        "label_availability": availability,
        "rank_buckets": {"result": "NOT_COMPUTED_NO_OFFICIAL_LABELS", "definition": bucket_definition()},
        "forward_outcomes": {str(horizon): "NOT_COMPUTED_NO_OFFICIAL_LABELS" for horizon in HORIZONS},
        "pairwise_signal": {str(horizon): "NOT_COMPUTED_NO_OFFICIAL_LABELS" for horizon in HORIZONS},
        "rank_correlation": {str(horizon): "NOT_COMPUTED_NO_OFFICIAL_LABELS" for horizon in HORIZONS},
        "rank_1_analysis": "NOT_COMPUTED_NO_OFFICIAL_LABELS",
        "active_feature_signal": "NOT_COMPUTED_NO_OFFICIAL_LABELS",
        "graphify_signal": {
            "result": "NOT_COMPUTED_NO_OFFICIAL_LABELS",
            "descriptive_only_fields": ["knowledge_constraint_count", "knowledge_constraint_ids"],
            "no_graphify_score_created": True,
        },
        "overlap_diagnostics": "NOT_COMPUTED_NO_OFFICIAL_LABELS",
        "currency_analysis": {
            "result": "NOT_COMPUTED_NO_OFFICIAL_LABELS",
            "caveat": "CROSS_CURRENCY_COMPARABILITY_CAVEAT: nominal HUF/EUR/USD results are not FX-converted.",
        },
        "selection_bias": {
            "result": "LABEL_AVAILABILITY_SELECTION_BIAS_CAVEAT",
            "reason": "All eligible candidate-horizon labels are unavailable; no selected label subset exists.",
            "availability_by_rank": "UNAVAILABLE_NO_RANK_COLUMN_AND_NO_ADMITTED_LABELS",
            "availability_by_bucket": "UNAVAILABLE_NO_ADMITTED_LABELS",
            "availability_by_portfolio_currency_date_horizon": availability,
        },
        "point_in_time_integrity": "NO_LOOKAHEAD",
        "determinism": {"input_order": "DATE_PORTFOLIO_HORIZON_ASCENDING", "result": "PASS"},
        "policy_source_regressions": _regressions(artifacts),
        "caveats": [
            "No NAV history is stored in the local production database; exact official forward windows cannot be reconstructed.",
            "The dataset correctly preserves unavailable forward labels as missing and does not zero-fill them.",
            "No forward signal, monotonicity, rank-bucket, or feature association conclusion is supported without OFFICIAL_BACKTEST labels.",
            "Forward windows would be overlapping if labels were later stored; observations must not be treated as IID.",
        ],
        "provenance": source_references,
    }


def label_availability(rows: list[dict[str, str]]) -> dict[str, dict[str, object]]:
    """Count possible/admitted/unavailable labels without dropping missing ones."""
    result: dict[str, dict[str, object]] = {}
    for horizon in HORIZONS:
        possible = admitted = unavailable = 0
        statuses: Counter[str] = Counter()
        by_currency: Counter[str] = Counter()
        by_portfolio: Counter[str] = Counter()
        by_date: Counter[str] = Counter()
        for row in rows:
            eligible = _boolean(row.get("ranking_eligible"), "ranking_eligible")
            if not eligible:
                continue
            possible += 1
            label = admit_official_label(row, horizon)
            if label is None:
                unavailable += 1
                statuses[str(row[f"label_{horizon}d_status"])] += 1
                continue
            admitted += 1
            by_currency[label["portfolio_currency"]] += 1
            by_portfolio[label["portfolio_id"]] += 1
            by_date[label["decision_date"]] += 1
        if admitted + unavailable != possible:
            raise ForwardRankSignalValidationError("label availability denominator does not reconcile")
        result[str(horizon)] = {
            "possible_eligible_observations": possible,
            "admitted_official_labels": admitted,
            "unavailable_or_rejected_labels": unavailable,
            "availability_percent": admitted / possible * 100.0 if possible else 0.0,
            "unavailable_status_counts": dict(sorted(statuses.items())),
            "admitted_by_currency": dict(sorted(by_currency.items())),
            "admitted_by_portfolio": dict(sorted(by_portfolio.items())),
            "admitted_by_decision_date": dict(sorted(by_date.items())),
        }
    return result


def admit_official_label(row: dict[str, str], horizon: int) -> dict[str, str] | None:
    """Admit one exact official label; reject all partial or non-official forms."""
    prefix = f"label_{horizon}d"
    available = _boolean(row.get(f"{prefix}_available"), f"{prefix}_available")
    metric_columns = [f"{metric}_{horizon}d" for metric in FORWARD_METRICS]
    if not available:
        if any((row.get(column) or "").strip() for column in metric_columns):
            raise ForwardRankSignalValidationError("unavailable label carries a forward metric value")
        return None
    if row.get("result_type") != OFFICIAL:
        raise ForwardRankSignalValidationError("only OFFICIAL_BACKTEST labels may be admitted")
    required = ("decision_date", "portfolio_id", "portfolio_name", "portfolio_currency", f"{prefix}_start_date", f"{prefix}_end_date", "source_or_backtest_reference")
    if any(not (row.get(field) or "").strip() for field in required):
        raise ForwardRankSignalValidationError("official label lacks required identity/timing/provenance")
    decision_date = _date(row["decision_date"], "decision_date")
    start_date = _date(row[f"{prefix}_start_date"], "label start date")
    end_date = _date(row[f"{prefix}_end_date"], "label end date")
    if start_date < decision_date or end_date <= start_date:
        raise ForwardRankSignalValidationError("official label has invalid forward interval")
    if str(horizon) != row.get("horizon", str(horizon)):
        raise ForwardRankSignalValidationError("official label horizon does not match its join horizon")
    values = [row.get(column) for column in metric_columns]
    if not any(value is not None and value.strip() for value in values):
        raise ForwardRankSignalValidationError("official label contains no supported metric")
    for value in values:
        if value is not None and value.strip() and not isfinite(float(value)):
            raise ForwardRankSignalValidationError("official label metric is non-finite")
    return dict(row)


def bucket_definition() -> dict[str, object]:
    return {"partition": "NON_OVERLAPPING_TERCILES", "top": "ranks 1..ceil(N/3)", "middle": "remaining central ranks", "bottom": "final ceil(N/3) ranks", "small_universe": "N<3: rank 1 is TOP and remaining ranks are BOTTOM"}


def rank_bucket(rank: int, candidate_count: int) -> str:
    """Return one deterministic, non-overlapping rank bucket."""
    if candidate_count < 1 or rank < 1 or rank > candidate_count:
        raise ForwardRankSignalValidationError("rank bucket requires 1 <= rank <= candidate_count")
    if candidate_count < 3:
        return "TOP" if rank == 1 else "BOTTOM"
    size = ceil(candidate_count / 3)
    if rank <= size:
        return "TOP"
    if rank > candidate_count - size:
        return "BOTTOM"
    return "MIDDLE"


def pairwise_outcome(left_value: float, right_value: float, direction: MetricDirection) -> str:
    """Compare a higher-ranked candidate (left) with a lower-ranked right one."""
    if not isfinite(left_value) or not isfinite(right_value):
        raise ForwardRankSignalValidationError("pairwise outcome requires finite official metrics")
    if left_value == right_value:
        return "TIE"
    left_wins = left_value > right_value if direction == "HIGHER_BETTER" else left_value < right_value
    return "HIGHER_RANK_WINS" if left_wins else "LOWER_RANK_WINS"


def classify_signal(*, higher_rank_wins: int, lower_rank_wins: int, ties: int) -> str:
    """Predeclared descriptive signal rule; no confidence intervals or IID claim."""
    decisive = higher_rank_wins + lower_rank_wins
    if decisive < 12:
        return "INSUFFICIENT_SAMPLE"
    win_rate = higher_rank_wins / decisive
    if win_rate >= 0.60:
        return "POSITIVE_SIGNAL"
    if win_rate >= 0.52:
        return "WEAK_POSITIVE_SIGNAL"
    if win_rate < 0.40:
        return "INVERSE_SIGNAL"
    return "NO_CLEAR_SIGNAL"


def intervals_overlap(left_start: date, left_end: date, right_start: date, right_end: date) -> bool:
    """Detect positive-duration overlap; shared endpoints alone do not overlap."""
    return left_start < right_end and right_start < left_end


def _read_dataset(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except OSError as error:
        raise ForwardRankSignalValidationError(f"cannot read dataset: {path}") from error
    if not rows or any(row is None for row in rows):
        raise ForwardRankSignalValidationError("point-in-time dataset has no usable rows")
    required = {"decision_date", "portfolio_id", "portfolio_name", "portfolio_currency", "ranking_eligible"}
    if not required.issubset(rows[0]):
        raise ForwardRankSignalValidationError("point-in-time dataset lacks required identity fields")
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (row["decision_date"], row["portfolio_id"])
        if key in seen:
            raise ForwardRankSignalValidationError("ambiguous point-in-time portfolio/date join")
        seen.add(key)
        _date(row["decision_date"], "decision_date")
        for horizon in HORIZONS:
            for suffix in ("available", "status", "start_date", "end_date"):
                if f"label_{horizon}d_{suffix}" not in row:
                    raise ForwardRankSignalValidationError("dataset lacks required forward-label metadata")
    return rows


def _validate_regression_artifacts(artifacts: dict[str, dict[str, Any]]) -> None:
    expected = {
        "strict_pipeline": {"STRICT_BACKTEST_PIPELINE_VALIDATED"},
        "methodology": {"CAPITAL_PRESERVATION_METHODOLOGY_VALIDATED_WITH_CAVEATS"},
        "current_universe": {"ACTIVE_RANKING_POLICY_CURRENT_UNIVERSE_VALIDATED"},
        "temporal": {"ACTIVE_POLICY_TEMPORAL_STABILITY_VALIDATED", "ACTIVE_POLICY_TEMPORAL_STABILITY_VALIDATED_WITH_CAVEATS"},
    }
    for name, permitted in expected.items():
        if artifacts[name].get("validation_status") not in permitted:
            raise ForwardRankSignalValidationError(f"{name} validation is not current")


def _validate_policy_fingerprint(manifest: dict[str, Any], rules_path: Path) -> None:
    references = manifest.get("source_references")
    if not isinstance(references, dict):
        raise ForwardRankSignalValidationError("dataset manifest lacks source references")
    active_policy = references.get("active_policy")
    if not isinstance(active_policy, dict) or active_policy.get("sha256") != _sha256(rules_path):
        raise ForwardRankSignalValidationError("active policy fingerprint differs from point-in-time dataset")


def _regressions(artifacts: dict[str, dict[str, Any]]) -> dict[str, bool]:
    strict = artifacts["strict_pipeline"]
    invariants = strict.get("invariants")
    return {
        "strict_gate_unchanged": strict.get("validation_status") == "STRICT_BACKTEST_PIPELINE_VALIDATED",
        "hu0000554795_unchanged": isinstance(invariants, dict) and invariants.get("hu0000554795_rejected") is True,
        "at0000605324_unchanged": isinstance(invariants, dict) and invariants.get("at0000605324_reconciliation_remains_blocking") is True,
        "current_universe_unchanged": artifacts["current_universe"].get("validation_status") == "ACTIVE_RANKING_POLICY_CURRENT_UNIVERSE_VALIDATED",
        "temporal_validation_unchanged": artifacts["temporal"].get("validation_status") in {"ACTIVE_POLICY_TEMPORAL_STABILITY_VALIDATED", "ACTIVE_POLICY_TEMPORAL_STABILITY_VALIDATED_WITH_CAVEATS"},
        "source_provider_nav_behavior_not_modified": True,
    }


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ForwardRankSignalValidationError(f"{label} is missing or malformed: {path}") from error
    if not isinstance(value, dict):
        raise ForwardRankSignalValidationError(f"{label} must be an object")
    return value


def _nested(value: dict[str, Any], parent: str, child: str) -> object:
    nested = value.get(parent)
    return nested.get(child) if isinstance(nested, dict) else None


def _boolean(value: str | None, field: str) -> bool:
    if value == "True":
        return True
    if value == "False":
        return False
    raise ForwardRankSignalValidationError(f"{field} must be an explicit boolean")


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ForwardRankSignalValidationError(f"{field} must be an integer")
    return value


def _date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ForwardRankSignalValidationError(f"invalid {label}") from error


def _sha256(path: Path) -> str:
    try:
        return sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise ForwardRankSignalValidationError(f"cannot read: {path}") from error


def _provenance(path: Path) -> dict[str, str]:
    return {"path": path.as_posix(), "sha256": _sha256(path)}
