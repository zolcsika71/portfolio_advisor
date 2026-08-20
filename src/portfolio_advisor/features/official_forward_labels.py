"""Offline construction of strict, auditable forward-label records.

This module is intentionally downstream of the point-in-time feature dataset.
It never changes a feature, a ranking rule, or a source-resolution decision.
Each feature-row/horizon key is retained: an exact canonical forward result is
materialised only when the strict gate and the existing NAV backtester both
complete; all other keys become explicit unavailable records.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import date
from math import isfinite
from pathlib import Path
from typing import Any, TypedDict

from portfolio_advisor.backtesting.eligibility import (
    BACKTEST_ELIGIBLE,
    BacktestEligibilityError,
    BacktestEligibilityGate,
    StrictCoverageEligibilityGate,
)
from portfolio_advisor.backtesting.models import BacktestEligibility, ForwardMetrics
from portfolio_advisor.backtesting.service import WalkForwardBacktester
from portfolio_advisor.database.repository import ModelPortfolioRepository
from portfolio_advisor.history.repository import HistoricalPortfolioRepository
from portfolio_advisor.ranking.config import load_ranking_rules

HORIZONS = (90, 180, 365)
LABEL_STORE_SCHEMA_VERSION = 1
LABEL_STORE_VERSION = "1.0.0"
STATUS_VALIDATED = "OFFICIAL_FORWARD_LABEL_STORE_VALIDATED"
STATUS_CAVEATS = "OFFICIAL_FORWARD_LABEL_STORE_VALIDATED_WITH_CAVEATS"
STATUS_PARTIAL = "OFFICIAL_FORWARD_LABEL_STORE_PARTIAL"
STATUS_FAILED = "OFFICIAL_FORWARD_LABEL_STORE_BUILD_FAILED"
OFFICIAL_BACKTEST = "OFFICIAL_BACKTEST"
BACKTEST_REJECTED = "BACKTEST_REJECTED"
LABEL_AVAILABLE = "OFFICIAL_LABEL_AVAILABLE"
LABEL_NOT_APPLICABLE = "LABEL_NOT_APPLICABLE"
NO_LOCAL_HISTORY = "NO_LOCAL_HISTORY"
SOURCE_INTERVAL_INCOMPLETE = "SOURCE_INTERVAL_INCOMPLETE"
RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
TERMINAL_UNRESOLVABLE = "TERMINAL_UNRESOLVABLE"
STRICT_BACKTEST_REJECTED = "STRICT_BACKTEST_REJECTED"

CSV_FIELDS = (
    "decision_date", "portfolio_id", "portfolio_name", "portfolio_currency", "horizon_days",
    "label_start_date", "label_end_date", "result_type", "label_available", "label_status",
    "forward_return", "forward_annualized_return", "forward_volatility", "forward_sharpe",
    "forward_mdd", "forward_var", "forward_cvar", "forward_return_observation_count",
    "constituent_count", "resolvable_weight", "unresolved_weight", "blocking_isins",
    "blocking_categories", "coverage_status", "rejection_reason", "source_provenance",
    "backtest_policy", "policy_version", "dataset_row_reference",
)


class OfficialForwardLabelStoreError(RuntimeError):
    """A label-store input or strict materialisation invariant is unsafe."""


class _LabelBase(TypedDict):
    decision_date: str
    portfolio_id: str
    portfolio_name: str
    portfolio_currency: str
    horizon_days: int
    label_start_date: str
    label_end_date: str
    policy_version: str
    dataset_row_reference: str


@dataclass(frozen=True, slots=True)
class OfficialForwardLabel:
    """One deterministic feature-row/horizon label candidate."""

    decision_date: str
    portfolio_id: str
    portfolio_name: str
    portfolio_currency: str
    horizon_days: int
    label_start_date: str
    label_end_date: str
    result_type: str
    label_available: bool
    label_status: str
    forward_return: float | None
    forward_annualized_return: float | None
    forward_volatility: float | None
    forward_sharpe: float | None
    forward_mdd: float | None
    forward_var: float | None
    forward_cvar: float | None
    forward_return_observation_count: int | None
    constituent_count: int
    resolvable_weight: float | None
    unresolved_weight: float | None
    blocking_isins: tuple[str, ...]
    blocking_categories: tuple[str, ...]
    coverage_status: str | None
    rejection_reason: str | None
    source_provenance: dict[str, object]
    backtest_policy: str | None
    policy_version: str
    dataset_row_reference: str

    def __post_init__(self) -> None:
        if self.horizon_days not in HORIZONS:
            raise OfficialForwardLabelStoreError("unsupported forward-label horizon")
        start = _parse_date(self.label_start_date, "label_start_date")
        end = _parse_date(self.label_end_date, "label_end_date")
        decision = _parse_date(self.decision_date, "decision_date")
        if start != decision or end <= start:
            raise OfficialForwardLabelStoreError("label interval is not the canonical forward window")
        if self.label_available:
            if self.result_type != OFFICIAL_BACKTEST or self.label_status != LABEL_AVAILABLE:
                raise OfficialForwardLabelStoreError("available label is not an OFFICIAL_BACKTEST")
            if self.forward_return is None:
                raise OfficialForwardLabelStoreError("available label has no canonical total return")
            _validate_metrics(self)
        elif any(
            value is not None
            for value in (
                self.forward_return, self.forward_annualized_return, self.forward_volatility,
                self.forward_sharpe, self.forward_mdd, self.forward_var, self.forward_cvar,
                self.forward_return_observation_count,
            )
        ):
            raise OfficialForwardLabelStoreError("unavailable label carries a numeric forward metric")
        if tuple(sorted(set(self.blocking_isins))) != self.blocking_isins:
            raise OfficialForwardLabelStoreError("blocking ISINs must be sorted and unique")
        if tuple(sorted(set(self.blocking_categories))) != self.blocking_categories:
            raise OfficialForwardLabelStoreError("blocking categories must be sorted and unique")

    @property
    def key(self) -> tuple[str, str, int]:
        return self.decision_date, self.portfolio_id, self.horizon_days

    def csv_row(self) -> dict[str, object]:
        value = asdict(self)
        value["label_available"] = "True" if self.label_available else "False"
        value["blocking_isins"] = json.dumps(self.blocking_isins, separators=(",", ":"))
        value["blocking_categories"] = json.dumps(self.blocking_categories, separators=(",", ":"))
        value["source_provenance"] = json.dumps(
            self.source_provenance, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return value


def build_official_forward_label_store(
    *,
    feature_dataset_path: Path,
    feature_manifest_path: Path,
    database_path: Path,
    rules_path: Path,
    contract_path: Path,
    strict_pipeline_path: Path,
    methodology_path: Path,
    current_universe_path: Path,
    temporal_path: Path,
    eligibility_gate: BacktestEligibilityGate | None = None,
) -> tuple[list[OfficialForwardLabel], dict[str, object]]:
    """Build every exact date/portfolio/horizon label candidate offline.

    The optional gate is solely a test seam. Production callers use the
    retained strict coverage and terminal-resolution artifacts through the
    default gate.
    """
    rules = load_ranking_rules(rules_path)
    if rules.status != "approved" or rules.version != "1.0.1" or rules.schema_version != 2:
        raise OfficialForwardLabelStoreError("active ranking policy identity is not v1.0.1/schema 2")
    contract = _load_json(contract_path, "ranking policy contract")
    if contract.get("final_policy_status") != "RANKING_POLICY_ACTIVE":
        raise OfficialForwardLabelStoreError("ranking policy contract is not active")
    prerequisites = {
        "strict_pipeline": _load_json(strict_pipeline_path, "strict pipeline validation"),
        "methodology": _load_json(methodology_path, "methodology validation"),
        "current_universe": _load_json(current_universe_path, "current-universe validation"),
        "temporal": _load_json(temporal_path, "temporal validation"),
    }
    _validate_prerequisites(prerequisites)
    feature_manifest = _load_json(feature_manifest_path, "point-in-time feature manifest")
    _validate_feature_manifest(feature_manifest, rules_path)
    feature_rows = _load_feature_rows(feature_dataset_path)

    repository = ModelPortfolioRepository(database_path)
    history = HistoricalPortfolioRepository(repository)
    _validate_feature_source_join(feature_rows, history)
    gate = eligibility_gate or StrictCoverageEligibilityGate.from_default_artifacts()
    backtester = WalkForwardBacktester(history, rules_path, eligibility_gate=gate)
    labels: list[OfficialForwardLabel] = []
    for row in feature_rows:
        for horizon in HORIZONS:
            labels.append(_materialize_label(row, horizon, history, gate, backtester, rules.version))
    labels.sort(key=lambda item: item.key)
    _validate_label_grid(labels, feature_rows)
    _validate_strict_universe(labels, prerequisites["strict_pipeline"])
    manifest = _manifest(
        labels=labels,
        feature_manifest=feature_manifest,
        feature_dataset_path=feature_dataset_path,
        feature_manifest_path=feature_manifest_path,
        database_path=database_path,
        rules_path=rules_path,
        contract_path=contract_path,
        strict_pipeline_path=strict_pipeline_path,
        methodology_path=methodology_path,
        current_universe_path=current_universe_path,
        temporal_path=temporal_path,
        rules_version=rules.version,
    )
    return labels, manifest


def _materialize_label(
    row: dict[str, str],
    horizon: int,
    history: HistoricalPortfolioRepository,
    gate: BacktestEligibilityGate,
    backtester: WalkForwardBacktester,
    policy_version: str,
) -> OfficialForwardLabel:
    decision = _parse_date(row["decision_date"], "decision_date")
    portfolio_name = row["portfolio_name"]
    window = history.forward_window(decision, horizon)
    base: _LabelBase = {
        "decision_date": decision.isoformat(),
        "portfolio_id": row["portfolio_id"],
        "portfolio_name": portfolio_name,
        "portfolio_currency": row["portfolio_currency"],
        "horizon_days": horizon,
        "label_start_date": window.evaluation_date.isoformat(),
        "label_end_date": window.end_date.isoformat(),
        "policy_version": policy_version,
        "dataset_row_reference": f"{decision.isoformat()}:{row['portfolio_id']}",
    }
    try:
        eligibility = gate.evaluate(history=history, portfolio_name=portfolio_name, window=window)
    except BacktestEligibilityError as exc:
        raise OfficialForwardLabelStoreError(f"strict eligibility failed closed: {exc}") from exc
    if row["ranking_eligible"] == "False":
        return _unavailable_label(
            base,
            result_type="NOT_APPLICABLE",
            status=LABEL_NOT_APPLICABLE,
            reason="Point-in-time ranking eligibility is false",
            eligibility=eligibility,
            provenance=_provenance(eligibility, source_classes=()),
        )
    if not eligibility.eligible:
        return _rejected_label(base, eligibility)
    if eligibility.status != BACKTEST_ELIGIBLE or eligibility.unresolved_weight != 0.0:
        raise OfficialForwardLabelStoreError("strict gate returned an inconsistent eligible decision")
    series = history.nav_series(portfolio_name, window)
    if series is None:
        return _unavailable_label(
            base,
            result_type=OFFICIAL_BACKTEST,
            status=NO_LOCAL_HISTORY if not history.nav_history_available() else SOURCE_INTERVAL_INCOMPLETE,
            reason=(
                "No retained official portfolio NAV history exists locally"
                if not history.nav_history_available()
                else "Retained official NAV history lacks an exact canonical interval endpoint"
            ),
            eligibility=eligibility,
            provenance=_provenance(eligibility, source_classes=()),
        )
    metrics = backtester._forward_metrics(series)
    return _available_label(base, eligibility, metrics)


def _available_label(
    base: _LabelBase, eligibility: BacktestEligibility, metrics: ForwardMetrics
) -> OfficialForwardLabel:
    return OfficialForwardLabel(
        **base,
        result_type=OFFICIAL_BACKTEST,
        label_available=True,
        label_status=LABEL_AVAILABLE,
        forward_return=metrics.total_return,
        forward_annualized_return=metrics.annualized_return,
        forward_volatility=metrics.annualized_volatility,
        forward_sharpe=metrics.sharpe_ratio,
        forward_mdd=metrics.maximum_drawdown,
        forward_var=metrics.historical_var,
        forward_cvar=metrics.historical_cvar,
        forward_return_observation_count=metrics.return_observation_count,
        constituent_count=len(eligibility.constituent_weights),
        resolvable_weight=eligibility.resolvable_weight,
        unresolved_weight=eligibility.unresolved_weight,
        blocking_isins=(),
        blocking_categories=(),
        coverage_status=eligibility.coverage_status,
        rejection_reason=None,
        source_provenance=_provenance(
            eligibility,
            source_classes=("portfolio_nav_history",),
            extra={"nav_interval": [base["label_start_date"], base["label_end_date"]]},
        ),
        backtest_policy=eligibility.policy_id,
    )


def _rejected_label(base: _LabelBase, eligibility: BacktestEligibility) -> OfficialForwardLabel:
    categories = tuple(sorted({item.category for item in eligibility.blocking_constituents}))
    status = (
        TERMINAL_UNRESOLVABLE if TERMINAL_UNRESOLVABLE in categories
        else RECONCILIATION_REQUIRED if RECONCILIATION_REQUIRED in categories
        else SOURCE_INTERVAL_INCOMPLETE if eligibility.coverage_status == "MISSING_END"
        else STRICT_BACKTEST_REJECTED
    )
    return _unavailable_label(
        base,
        result_type=BACKTEST_REJECTED,
        status=status,
        reason="Strict backtest eligibility rejected the complete original constituent set",
        eligibility=eligibility,
        provenance=_provenance(eligibility, source_classes=()),
    )


def _unavailable_label(
    base: _LabelBase,
    *,
    result_type: str,
    status: str,
    reason: str,
    provenance: dict[str, object],
    eligibility: BacktestEligibility | None = None,
) -> OfficialForwardLabel:
    blockers = eligibility.blocking_constituents if eligibility is not None else ()
    constituents = eligibility.constituent_weights if eligibility is not None else ()
    return OfficialForwardLabel(
        **base,
        result_type=result_type,
        label_available=False,
        label_status=status,
        forward_return=None,
        forward_annualized_return=None,
        forward_volatility=None,
        forward_sharpe=None,
        forward_mdd=None,
        forward_var=None,
        forward_cvar=None,
        forward_return_observation_count=None,
        constituent_count=len(constituents),
        resolvable_weight=eligibility.resolvable_weight if eligibility is not None else None,
        unresolved_weight=eligibility.unresolved_weight if eligibility is not None else None,
        blocking_isins=tuple(sorted(item.isin for item in blockers)),
        blocking_categories=tuple(sorted({item.category for item in blockers})),
        coverage_status=eligibility.coverage_status if eligibility is not None else None,
        rejection_reason=reason,
        source_provenance=provenance,
        backtest_policy=eligibility.policy_id if eligibility is not None else None,
    )


def _provenance(
    eligibility: BacktestEligibility,
    *,
    source_classes: tuple[str, ...],
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "strict_policy": eligibility.policy_id,
        "coverage_status": eligibility.coverage_status,
        "constituents": [
            {"isin": item.isin, "weight": item.weight}
            for item in sorted(eligibility.constituent_weights, key=lambda item: item.isin)
        ],
        "blocking_references": [
            item.resolution_reference
            for item in sorted(eligibility.blocking_constituents, key=lambda item: item.isin)
            if item.resolution_reference is not None
        ],
        "source_classes": list(source_classes),
        "audit_references": ["data/audit/backtest_window_coverage.json"],
    }
    if extra:
        value.update(extra)
    return value


def _validate_metrics(label: OfficialForwardLabel) -> None:
    values = (
        label.forward_return, label.forward_annualized_return, label.forward_volatility,
        label.forward_sharpe, label.forward_mdd, label.forward_var, label.forward_cvar,
    )
    if any(value is not None and not isfinite(value) for value in values):
        raise OfficialForwardLabelStoreError("official forward metric is non-finite")
    if label.forward_return is not None and label.forward_return <= -1.0:
        raise OfficialForwardLabelStoreError("official total return is impossible for positive NAVs")
    if label.forward_volatility is not None and label.forward_volatility < 0.0:
        raise OfficialForwardLabelStoreError("official volatility is negative")
    if label.forward_mdd is not None and label.forward_mdd > 0.0:
        raise OfficialForwardLabelStoreError("maximum drawdown must use the non-positive convention")
    if label.forward_var is not None and label.forward_var < 0.0:
        raise OfficialForwardLabelStoreError("historical VaR must be a non-negative loss")
    if label.forward_cvar is not None and label.forward_cvar < 0.0:
        raise OfficialForwardLabelStoreError("historical CVaR must be a non-negative loss")
    if (
        label.forward_var is not None and label.forward_cvar is not None
        and label.forward_cvar + 1e-12 < label.forward_var
    ):
        raise OfficialForwardLabelStoreError("historical CVaR is lower than historical VaR")


def _load_feature_rows(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except OSError as exc:
        raise OfficialForwardLabelStoreError(f"cannot read feature dataset: {path}") from exc
    required = {"decision_date", "portfolio_id", "portfolio_name", "portfolio_currency", "ranking_eligible"}
    if not rows or rows[0] is None or not required.issubset(rows[0]):
        raise OfficialForwardLabelStoreError("feature dataset lacks required point-in-time identity fields")
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if row is None:
            raise OfficialForwardLabelStoreError("feature dataset contains a malformed row")
        decision = _parse_date(row["decision_date"], "decision_date")
        if not row["portfolio_id"] or not row["portfolio_name"] or not row["portfolio_currency"]:
            raise OfficialForwardLabelStoreError("feature dataset has an incomplete portfolio identity")
        if row["portfolio_id"] != row["portfolio_name"]:
            raise OfficialForwardLabelStoreError("portfolio_id must be the canonical source portfolio identifier")
        if row["ranking_eligible"] not in {"True", "False"}:
            raise OfficialForwardLabelStoreError("ranking_eligible must be an explicit boolean")
        key = (decision.isoformat(), row["portfolio_id"])
        if key in seen:
            raise OfficialForwardLabelStoreError("duplicate point-in-time feature identity")
        seen.add(key)
    return sorted(rows, key=lambda row: (row["decision_date"], row["portfolio_id"]))


def _validate_feature_source_join(rows: list[dict[str, str]], history: HistoricalPortfolioRepository) -> None:
    source_keys = {
        (current.isoformat(), name)
        for current in history.observation_dates()
        for name in {holding.portfolio_name for holding in history.holdings_at(current)}
    }
    feature_keys = {(row["decision_date"], row["portfolio_id"]) for row in rows}
    if feature_keys != source_keys:
        raise OfficialForwardLabelStoreError("feature dataset does not exactly reconcile to source portfolio/date identities")


def _validate_label_grid(labels: list[OfficialForwardLabel], rows: list[dict[str, str]]) -> None:
    expected = {(row["decision_date"], row["portfolio_id"], horizon) for row in rows for horizon in HORIZONS}
    actual = {item.key for item in labels}
    if len(actual) != len(labels):
        raise OfficialForwardLabelStoreError("duplicate official forward-label key")
    if actual != expected:
        raise OfficialForwardLabelStoreError("official forward-label grid silently omitted or added a candidate")
    for label in labels:
        if label.label_available and label.label_start_date < label.decision_date:
            raise OfficialForwardLabelStoreError("official forward label starts before its decision date")


def _validate_feature_manifest(manifest: dict[str, object], rules_path: Path) -> None:
    if manifest.get("dataset_status") not in {
        "POINT_IN_TIME_FEATURE_DATASET_VALIDATED",
        "POINT_IN_TIME_FEATURE_DATASET_VALIDATED_WITH_CAVEATS",
    }:
        raise OfficialForwardLabelStoreError("point-in-time feature dataset is not validated")
    leakage = manifest.get("leakage_validation")
    if not isinstance(leakage, dict) or leakage.get("result") != "NO_POINT_IN_TIME_LEAKAGE":
        raise OfficialForwardLabelStoreError("point-in-time feature dataset leakage validation is not clean")
    sources = manifest.get("source_references")
    policy = sources.get("active_policy") if isinstance(sources, dict) else None
    if not isinstance(policy, dict) or policy.get("sha256") != _file_hash(rules_path):
        raise OfficialForwardLabelStoreError("active policy differs from the feature dataset")


def _validate_prerequisites(artifacts: dict[str, dict[str, object]]) -> None:
    accepted = {
        "strict_pipeline": {"STRICT_BACKTEST_PIPELINE_VALIDATED"},
        "methodology": {"CAPITAL_PRESERVATION_METHODOLOGY_VALIDATED_WITH_CAVEATS"},
        "current_universe": {"ACTIVE_RANKING_POLICY_CURRENT_UNIVERSE_VALIDATED"},
        "temporal": {
            "ACTIVE_POLICY_TEMPORAL_STABILITY_VALIDATED",
            "ACTIVE_POLICY_TEMPORAL_STABILITY_VALIDATED_WITH_CAVEATS",
        },
    }
    for name, statuses in accepted.items():
        if artifacts[name].get("validation_status") not in statuses:
            raise OfficialForwardLabelStoreError(f"{name} prerequisite is not validated")


def _validate_strict_universe(labels: list[OfficialForwardLabel], strict_pipeline: dict[str, object]) -> None:
    dataset = strict_pipeline.get("dataset")
    if not isinstance(dataset, dict):
        raise OfficialForwardLabelStoreError("strict pipeline lacks window-universe accounting")
    total = dataset.get("total_windows")
    eligible = dataset.get("official_eligible_windows")
    rejected = dataset.get("rejected_windows")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in (total, eligible, rejected)):
        raise OfficialForwardLabelStoreError("strict pipeline has invalid window-universe counts")
    assert isinstance(total, int) and isinstance(eligible, int) and isinstance(rejected, int)
    if eligible + rejected != total or total != len(labels):
        raise OfficialForwardLabelStoreError("label-store and strict-pipeline window universes do not reconcile")


def _manifest(
    *,
    labels: list[OfficialForwardLabel],
    feature_manifest: dict[str, object],
    feature_dataset_path: Path,
    feature_manifest_path: Path,
    database_path: Path,
    rules_path: Path,
    contract_path: Path,
    strict_pipeline_path: Path,
    methodology_path: Path,
    current_universe_path: Path,
    temporal_path: Path,
    rules_version: str,
) -> dict[str, object]:
    available = [item for item in labels if item.label_available]
    unavailable = [item for item in labels if not item.label_available]
    by_horizon = {
        str(horizon): _availability(item for item in labels if item.horizon_days == horizon)
        for horizon in HORIZONS
    }
    status = STATUS_VALIDATED if len(available) == len(labels) else STATUS_CAVEATS if available else STATUS_PARTIAL
    references = {
        "feature_dataset": _reference(feature_dataset_path),
        "feature_manifest": _reference(feature_manifest_path),
        "database": _reference(database_path),
        "active_policy": _reference(rules_path),
        "policy_contract": _reference(contract_path),
        "strict_pipeline": _reference(strict_pipeline_path),
        "methodology": _reference(methodology_path),
        "current_universe": _reference(current_universe_path),
        "temporal": _reference(temporal_path),
    }
    fingerprint_payload = {
        "schema_version": LABEL_STORE_SCHEMA_VERSION,
        "label_store_version": LABEL_STORE_VERSION,
        "labels": [item.csv_row() for item in labels],
        "source_references": references,
    }
    fingerprint = _canonical_fingerprint(fingerprint_payload)
    reasons = Counter(item.label_status for item in labels if not item.label_available)
    blockers = Counter(isin for item in unavailable for isin in item.blocking_isins)
    categories = Counter(category for item in unavailable for category in item.blocking_categories)
    source_usage: Counter[str] = Counter()
    for item in available:
        source_classes = item.source_provenance.get("source_classes")
        if isinstance(source_classes, list):
            source_usage.update(source for source in source_classes if isinstance(source, str))
    intervals = [item for item in available]
    strict_rejected = sum(
        item.label_status in {
            SOURCE_INTERVAL_INCOMPLETE,
            RECONCILIATION_REQUIRED,
            TERMINAL_UNRESOLVABLE,
            STRICT_BACKTEST_REJECTED,
        }
        for item in labels
    )
    return {
        "schema_version": LABEL_STORE_SCHEMA_VERSION,
        "label_store_version": LABEL_STORE_VERSION,
        "validation_status": status,
        "feature_dataset_fingerprint": feature_manifest.get("dataset_fingerprint"),
        "decision_date_range": feature_manifest.get("decision_date_range"),
        "portfolio_count": len({item.portfolio_id for item in labels}),
        "candidate_label_count": len(labels),
        "available_label_count": len(available),
        "unavailable_label_count": len(unavailable),
        "availability_percent": len(available) / len(labels) * 100.0 if labels else 0.0,
        "availability_by_horizon": by_horizon,
        "availability_by_status": dict(sorted(reasons.items())),
        "availability_by_portfolio": _availability_by(labels, "portfolio_id"),
        "availability_by_currency": _availability_by(labels, "portfolio_currency"),
        "blocking_isin_counts": dict(sorted(blockers.items())),
        "blocking_category_counts": dict(sorted(categories.items())),
        "source_usage_counts": dict(sorted(source_usage.items())),
        "coverage_accounting": {
            "strict_rejected": strict_rejected,
            "strict_rejected_all_candidates": sum(
                item.unresolved_weight is not None and item.unresolved_weight > 0.0 for item in labels
            ),
            "reconciliation_required": reasons[RECONCILIATION_REQUIRED],
            "terminal_unresolved": reasons[TERMINAL_UNRESOLVABLE],
            "source_interval_incomplete": reasons[SOURCE_INTERVAL_INCOMPLETE],
            "no_local_history": reasons[NO_LOCAL_HISTORY],
            "other_unavailable": reasons[LABEL_NOT_APPLICABLE] + reasons[STRICT_BACKTEST_REJECTED],
            "available_plus_unavailable_equals_candidates": len(available) + len(unavailable) == len(labels),
        },
        "official_label_interval_range": (
            {"earliest_start": min(item.label_start_date for item in intervals), "latest_end": max(item.label_end_date for item in intervals)}
            if intervals else None
        ),
        "candidate_label_interval_range": {
            "earliest_start": min(item.label_start_date for item in labels),
            "latest_end": max(item.label_end_date for item in labels),
        },
        "overlap_ready_metadata": {
            "label_start_date_coverage": len(labels),
            "label_end_date_coverage": len(labels),
            "result": "COMPLETE",
        },
        "lookahead_validation": {
            "result": "NO_POINT_IN_TIME_LEAKAGE",
            "feature_dataset_unchanged": True,
            "labels_are_external_to_feature_dataset": True,
            "all_label_starts_on_or_after_decision_date": True,
        },
        "policy_identity": {"version": rules_version, "policy_fingerprint": _file_hash(rules_path)},
        "source_reconciliation": {
            "strict_policy": "STRICT_REJECT_WINDOW",
            "available_labels_require": OFFICIAL_BACKTEST,
            "unavailable_labels_retained": True,
            "no_fallbacks": [
                "constituent_dropping", "renormalization", "proxy_returns", "cash_or_zero_return",
                "interpolation", "forward_fill", "nearest_date", "source_stitching",
                "graphify_generated_performance_labels",
            ],
        },
        "source_references": references,
        "label_store_fingerprint": fingerprint,
    }


def _availability(labels: Iterable[OfficialForwardLabel]) -> dict[str, object]:
    items = list(labels)
    available = sum(item.label_available for item in items)
    unavailable = len(items) - available
    if available + unavailable != len(items):
        raise OfficialForwardLabelStoreError("label availability accounting does not reconcile")
    return {
        "candidate_labels": len(items),
        "available_official_labels": available,
        "unavailable_labels": unavailable,
        "availability_percent": available / len(items) * 100.0 if items else 0.0,
    }


def _availability_by(labels: list[OfficialForwardLabel], attribute: str) -> dict[str, dict[str, int]]:
    grouped: dict[str, list[OfficialForwardLabel]] = {}
    for item in labels:
        grouped.setdefault(str(getattr(item, attribute)), []).append(item)
    return {
        key: {
            "candidate_labels": len(items),
            "available_official_labels": sum(item.label_available for item in items),
            "unavailable_labels": sum(not item.label_available for item in items),
        }
        for key, items in sorted(grouped.items())
    }


def write_official_forward_labels_csv(path: Path, labels: list[OfficialForwardLabel]) -> None:
    if not labels:
        raise OfficialForwardLabelStoreError("official forward-label store has no candidates")
    _write_atomic(path, lambda handle: _write_csv(handle, labels))


def write_official_forward_label_manifest(path: Path, manifest: dict[str, object]) -> None:
    def write(handle: Any) -> None:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")

    _write_atomic(path, write)


def _write_csv(handle: Any, labels: list[OfficialForwardLabel]) -> None:
    writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="raise")
    writer.writeheader()
    for label in labels:
        writer.writerow({key: "" if value is None else value for key, value in label.csv_row().items()})


def _write_atomic(path: Path, write: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            write(handle)
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _load_json(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OfficialForwardLabelStoreError(f"{label} is missing or malformed: {path}") from exc
    if not isinstance(value, dict):
        raise OfficialForwardLabelStoreError(f"{label} must be an object")
    return value


def _parse_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise OfficialForwardLabelStoreError(f"{label} must be ISO date") from exc


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_fingerprint(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _reference(path: Path) -> dict[str, str]:
    root = Path(__file__).resolve().parents[3]
    try:
        location = path.resolve().relative_to(root).as_posix()
    except ValueError:
        location = f"EXTERNAL_INPUT/{path.name}"
    return {"path": location, "sha256": _file_hash(path)}
