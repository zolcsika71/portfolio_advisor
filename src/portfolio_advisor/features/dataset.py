"""Build an offline, point-in-time portfolio feature dataset.

The module deliberately contains no optimisation or model-training code.  It
serialises source snapshot facts available at a decision date and appends
strictly later outcomes only as separately labelled fields.
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from hashlib import sha256
from math import isfinite
from pathlib import Path
from typing import Any

from portfolio_advisor.advisor.service import CapitalPreservationAdvisor
from portfolio_advisor.backtesting.eligibility import (
    BACKTEST_ELIGIBLE,
    BacktestEligibilityError,
    StrictCoverageEligibilityGate,
)
from portfolio_advisor.backtesting.service import WalkForwardBacktester
from portfolio_advisor.database.repository import (
    HoldingObservation,
    ModelPortfolioRepository,
)
from portfolio_advisor.history.repository import HistoricalPortfolioRepository
from portfolio_advisor.metrics.models import PortfolioMetrics
from portfolio_advisor.metrics.portfolio import calculate_all_portfolio_metrics
from portfolio_advisor.ranking.config import load_ranking_rules

DATASET_SCHEMA_VERSION = 1
DATASET_VERSION = "1.0.0"
DATASET_STATUS_VALIDATED = "POINT_IN_TIME_FEATURE_DATASET_VALIDATED"
DATASET_STATUS_CAVEATS = "POINT_IN_TIME_FEATURE_DATASET_VALIDATED_WITH_CAVEATS"
DATASET_STATUS_FAILED = "POINT_IN_TIME_FEATURE_DATASET_BUILD_FAILED"
ASSET_HISTORY_UNAVAILABLE = "POINT_IN_TIME_ASSET_RETURN_SERIES_NOT_STORED"
HORIZONS = (90, 180, 365)
FORWARD_METRIC_NAMES = (
    "forward_return",
    "forward_annualized_return",
    "forward_volatility",
    "forward_sharpe",
    "forward_mdd",
    "forward_var",
    "forward_cvar",
    "forward_downside_deviation",
    "forward_return_observation_count",
)
ACTIVE_FEATURES = (
    "maximum_drawdown",
    "annualized_volatility",
    "unhedged_allocation",
    "sharpe_ratio",
    "return_1y",
)


class DatasetBuildError(RuntimeError):
    """Point-in-time evidence cannot safely be converted into a dataset."""


@dataclass(frozen=True, slots=True)
class KnowledgeItem:
    """A locally retained Graphify item and its historical-use classification."""

    knowledge_id: str
    label: str
    source_document: str
    source_type: str
    graph_node_ids: tuple[str, ...]
    graph_edge_ids: tuple[str, ...]
    knowledge_category: str
    valid_from: str | None
    valid_to: str | None
    admitted: bool
    exclusion_reason: str | None
    source_section_or_page: str | None = None


def build_point_in_time_feature_dataset(
    *,
    database_path: Path,
    rules_path: Path,
    graph_path: Path,
    contract_path: Path,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Build rows and a deterministic manifest without mutating source data.

    The returned rows are suitable for CSV serialization.  Forward labels are
    independently named and schema-classified, preventing a caller from
    accidentally treating them as point-in-time inputs.
    """
    rules = load_ranking_rules(rules_path)
    if rules.status != "approved" or rules.version != "1.0.1" or rules.schema_version != 2:
        raise DatasetBuildError("the required active policy v1.0.1/schema 2 is not approved")
    contract = _load_object(contract_path, "active ranking policy contract")
    if contract.get("final_policy_status") != "RANKING_POLICY_ACTIVE":
        raise DatasetBuildError("ranking policy contract is not active")
    evidence_paths = _validate_existing_policy_evidence()

    repository = ModelPortfolioRepository(database_path)
    history = HistoricalPortfolioRepository(repository)
    dates = repository.observation_dates()
    knowledge = load_graphify_knowledge(graph_path)
    admitted_knowledge = tuple(item for item in knowledge if item.admitted)
    if not admitted_knowledge:
        raise DatasetBuildError("Graphify contains no provenance-backed timeless methodology items")

    advisor = CapitalPreservationAdvisor(repository, rules_path)
    gate = StrictCoverageEligibilityGate.from_default_artifacts()
    backtester = WalkForwardBacktester(history, rules_path, eligibility_gate=gate)
    rows: list[dict[str, object]] = []
    for decision_date in dates:
        source_holdings = repository.load_holdings(decision_date)
        metrics_by_name = {
            metrics.portfolio_name: metrics
            for metrics in calculate_all_portfolio_metrics(source_holdings)
        }
        ranked = advisor.evaluate(observation_date=decision_date, alternative_count=100)
        ranking_by_name = {item.metrics.portfolio_name: item for item in ranked.ranking}
        grouped: dict[str, list[HoldingObservation]] = defaultdict(list)
        for holding in source_holdings:
            grouped[holding.portfolio_name].append(holding)
        if set(metrics_by_name) != set(grouped) or set(ranking_by_name) != set(grouped):
            raise DatasetBuildError("point-in-time portfolio grouping does not reconcile with active ranking")
        for portfolio_name in sorted(grouped):
            metrics = metrics_by_name[portfolio_name]
            row = _base_row(
                decision_date=decision_date,
                portfolio_name=portfolio_name,
                holdings=grouped[portfolio_name],
                metrics=metrics,
                ranking=ranking_by_name[portfolio_name],
                knowledge=knowledge_available_at(admitted_knowledge, decision_date),
            )
            for horizon in HORIZONS:
                row.update(
                    _forward_label_fields(
                        history=history,
                        gate=gate,
                        backtester=backtester,
                        decision_date=decision_date,
                        portfolio_name=portfolio_name,
                        horizon=horizon,
                    )
                )
            _validate_row_timing(row, decision_date)
            rows.append(row)

    rows.sort(key=lambda row: (str(row["decision_date"]), str(row["portfolio_name"])))
    _validate_no_forward_labels_in_feature_schema()
    manifest = _manifest(
        rows=rows,
        dates=dates,
        rules_path=rules_path,
        database_path=database_path,
        graph_path=graph_path,
        contract_path=contract_path,
        evidence_paths=evidence_paths,
        knowledge=knowledge,
        policy_name=rules.policy_name,
        policy_version=rules.version,
        policy_schema_version=rules.schema_version,
    )
    return rows, manifest


def load_graphify_knowledge(graph_path: Path) -> tuple[KnowledgeItem, ...]:
    """Classify local Graphify nodes without querying or rebuilding the graph.

    The graph has no dated portfolio facts.  A small whitelist of explicit,
    provenance-backed measurement/methodology concepts is therefore admitted as
    ``TIMELESS_METHODOLOGY`` constraints.  Every other node is retained in the
    catalog but excluded instead of assigning it an invented numeric score.
    """
    graph = _load_object(graph_path, "Graphify graph")
    nodes = graph.get("nodes")
    if not isinstance(nodes, list):
        raise DatasetBuildError("Graphify graph has no node list")
    allowed = {
        "maximum drawdown (mdd)": "MDD_MEASUREMENT_METHODOLOGY",
        "sharpe ratio": "SHARPE_MEASUREMENT_METHODOLOGY",
        "look-ahead bias": "NO_LOOKAHEAD_METHODOLOGY",
        "value at risk (var) and conditional var (cvar)": "TAIL_RISK_MEASUREMENT_METHODOLOGY",
    }
    items: list[KnowledgeItem] = []
    for node in nodes:
        if not isinstance(node, dict):
            raise DatasetBuildError("Graphify graph contains a malformed node")
        node_id = node.get("id")
        label = node.get("label")
        source = node.get("source_file")
        if not all(isinstance(value, str) and value.strip() for value in (node_id, label, source)):
            items.append(
                KnowledgeItem(
                    knowledge_id=str(node_id or "UNKNOWN"), label=str(label or "UNKNOWN"),
                    source_document=str(source or "UNKNOWN"), source_type="GRAPHIFY_NODE",
                    graph_node_ids=(str(node_id),) if node_id else (), graph_edge_ids=(),
                    knowledge_category="UNUSABLE_FOR_HISTORICAL_RANKING", valid_from=None,
                    valid_to=None, admitted=False, exclusion_reason="MISSING_REQUIRED_PROVENANCE",
                    source_section_or_page=_optional_text(node.get("source_location")),
                )
            )
            continue
        if not isinstance(node_id, str) or not isinstance(label, str) or not isinstance(source, str):
            raise DatasetBuildError("Graphify node provenance type changed during validation")
        category, admitted, reason = classify_graphify_node(node, label.casefold() in allowed)
        items.append(
            KnowledgeItem(
                knowledge_id=allowed.get(label.casefold(), str(node_id)), label=label,
                source_document=source, source_type="GRAPHIFY_NODE", graph_node_ids=(node_id,),
                graph_edge_ids=(), knowledge_category=category,
                valid_from=_optional_iso_date(node.get("valid_from")), valid_to=_optional_iso_date(node.get("valid_to")),
                admitted=admitted, exclusion_reason=reason,
                source_section_or_page=_optional_text(node.get("source_location")),
            )
        )
    return tuple(sorted(items, key=lambda item: (item.knowledge_id, item.label)))


def classify_graphify_node(node: dict[str, object], is_whitelisted_timeless: bool) -> tuple[str, bool, str | None]:
    """Classify a Graphify item without treating undated facts as historical facts."""
    category = node.get("knowledge_category")
    if category in {"CURRENT_ONLY_FACT", "FORWARD_INFORMATION"}:
        return str(category), False, f"{category}_EXCLUDED_FROM_HISTORICAL_FEATURES"
    valid_from = _optional_iso_date(node.get("valid_from"))
    valid_to = _optional_iso_date(node.get("valid_to"))
    if node.get("valid_from") is not None and valid_from is None:
        return "UNUSABLE_FOR_HISTORICAL_RANKING", False, "INVALID_KNOWLEDGE_VALID_FROM"
    if node.get("valid_to") is not None and valid_to is None:
        return "UNUSABLE_FOR_HISTORICAL_RANKING", False, "INVALID_KNOWLEDGE_VALID_TO"
    if valid_from is not None:
        return "POINT_IN_TIME_FACT", True, None
    if is_whitelisted_timeless:
        return "TIMELESS_METHODOLOGY", True, None
    return "UNUSABLE_FOR_HISTORICAL_RANKING", False, "NOT_AN_AUDITABLE_PORTFOLIO_FACT_OR_TIMELESS_RULE"


def knowledge_available_at(items: tuple[KnowledgeItem, ...], decision_date: date) -> tuple[KnowledgeItem, ...]:
    """Return only provenance-backed timeless or already-valid point-in-time knowledge."""
    available: list[KnowledgeItem] = []
    for item in items:
        if not item.admitted:
            continue
        if item.knowledge_category == "TIMELESS_METHODOLOGY":
            available.append(item)
            continue
        if item.knowledge_category != "POINT_IN_TIME_FACT" or item.valid_from is None:
            continue
        valid_from = date.fromisoformat(item.valid_from)
        valid_to = date.fromisoformat(item.valid_to) if item.valid_to else None
        if valid_from <= decision_date and (valid_to is None or decision_date <= valid_to):
            available.append(item)
    return tuple(available)


def _base_row(
    *,
    decision_date: date,
    portfolio_name: str,
    holdings: list[HoldingObservation],
    metrics: PortfolioMetrics,
    ranking: Any,
    knowledge: tuple[KnowledgeItem, ...],
) -> dict[str, object]:
    structure = portfolio_structure(holdings)
    feature_values = {name: _metric_or_none(metrics, name) for name in ACTIVE_FEATURES}
    feature_complete = all(value is not None for value in feature_values.values())
    if any(value is not None and not isfinite(value) for value in feature_values.values()):
        raise DatasetBuildError(f"non-finite active-policy feature for {portfolio_name}")
    constraints = tuple(item.knowledge_id for item in knowledge)
    return {
        "decision_date": decision_date.isoformat(),
        "portfolio_id": portfolio_name,
        "portfolio_name": portfolio_name,
        "portfolio_currency": structure["portfolio_currency"],
        "constituent_count": len(holdings),
        **feature_values,
        "allocation_total": structure["allocation_total"],
        "portfolio_concentration_hhi": structure["portfolio_concentration_hhi"],
        "top_3_weight": structure["top_3_weight"],
        "huf_exposure": structure["huf_exposure"],
        "eur_exposure": structure["eur_exposure"],
        "usd_exposure": structure["usd_exposure"],
        "other_currency_exposure": structure["other_currency_exposure"],
        "currency_exposure_complete": structure["currency_exposure_complete"],
        "unhedged_exposure_source": structure["unhedged_exposure_source"],
        "asset_history_features_available": False,
        "asset_history_unavailable_reason": ASSET_HISTORY_UNAVAILABLE,
        "knowledge_constraint_count": len(constraints),
        "knowledge_constraint_ids": ";".join(constraints),
        "knowledge_complete": True,
        "ranking_eligible": bool(ranking.eligible),
        "ranking_rejection_reasons": ";".join(ranking.rejection_reasons),
        "feature_complete": feature_complete,
        "insufficient_history": True,
        "source_conflict": False,
        "duplicate_constituent_count": len(holdings) - len({item.isin for item in holdings}),
    }


def portfolio_structure(holdings: list[HoldingObservation]) -> dict[str, object]:
    """Describe source composition without dropping, proxying, or renormalizing holdings."""
    if not holdings:
        raise DatasetBuildError("portfolio has no source holdings")
    allocations = [item.allocation for item in holdings]
    if any(value is None or not isfinite(float(value)) or float(value) < 0.0 for value in allocations):
        return _unavailable_structure("MALFORMED_OR_MISSING_SOURCE_WEIGHT")
    weights = [float(value) for value in allocations if value is not None]
    total = sum(weights)
    if not isfinite(total) or total <= 0.0:
        return _unavailable_structure("NON_POSITIVE_SOURCE_WEIGHT_TOTAL")
    ratios = [weight / total for weight in weights]
    currencies_known = all(item.currency is not None and item.currency.strip() for item in holdings)
    risks_known = all(item.currency_risk is not None and item.currency_risk.strip() for item in holdings)
    exposures = {"HUF": 0.0, "EUR": 0.0, "USD": 0.0, "OTHER": 0.0}
    for item, weight in zip(holdings, weights, strict=True):
        currency = (item.currency or "").upper()
        if currency in {"HUF", "EUR", "USD"}:
            exposures[currency] += weight / total
        elif currency:
            exposures["OTHER"] += weight / total
    observed_currencies = {item.currency for item in holdings if item.currency}
    return {
        "portfolio_currency": next(iter(observed_currencies)) if len(observed_currencies) == 1 else "MULTI_CURRENCY",
        "allocation_total": total,
        "portfolio_concentration_hhi": sum(value * value for value in ratios),
        "top_3_weight": sum(sorted(ratios, reverse=True)[:3]),
        "huf_exposure": exposures["HUF"] if currencies_known else None,
        "eur_exposure": exposures["EUR"] if currencies_known else None,
        "usd_exposure": exposures["USD"] if currencies_known else None,
        "other_currency_exposure": exposures["OTHER"] if currencies_known else None,
        "currency_exposure_complete": currencies_known,
        "unhedged_exposure_source": (
            sum(weight / total for item, weight in zip(holdings, weights, strict=True)
                if (item.currency_risk or "").casefold() == "unhedged")
            if risks_known else None
        ),
    }


def _unavailable_structure(reason: str) -> dict[str, object]:
    return {
        "portfolio_currency": None, "allocation_total": None, "portfolio_concentration_hhi": None,
        "top_3_weight": None, "huf_exposure": None, "eur_exposure": None, "usd_exposure": None,
        "other_currency_exposure": None, "currency_exposure_complete": False,
        "unhedged_exposure_source": None, "structure_unavailable_reason": reason,
    }


def _metric_or_none(metrics: PortfolioMetrics, name: str) -> float | None:
    metric = getattr(metrics, name)
    if not metric.available:
        return None
    if metric.value is None or not isfinite(metric.value):
        raise DatasetBuildError(f"non-finite {name} for {metrics.portfolio_name}")
    return metric.value


def _forward_label_fields(
    *,
    history: HistoricalPortfolioRepository,
    gate: StrictCoverageEligibilityGate,
    backtester: WalkForwardBacktester,
    decision_date: date,
    portfolio_name: str,
    horizon: int,
) -> dict[str, object]:
    prefix = f"label_{horizon}d"
    window = history.forward_window(decision_date, horizon)
    common: dict[str, object] = {
        f"{prefix}_start_date": window.evaluation_date.isoformat(),
        f"{prefix}_end_date": window.end_date.isoformat(),
        f"{prefix}_available": False,
        f"{prefix}_status": None,
        **{f"{name}_{horizon}d": None for name in FORWARD_METRIC_NAMES},
    }
    try:
        eligibility = gate.evaluate(history=history, portfolio_name=portfolio_name, window=window)
    except BacktestEligibilityError as error:
        return {**common, f"{prefix}_status": f"BACKTEST_COVERAGE_EVIDENCE_UNAVAILABLE:{error}"}
    if not eligibility.eligible:
        return {**common, f"{prefix}_status": eligibility.status}
    if eligibility.status != BACKTEST_ELIGIBLE:
        raise DatasetBuildError("strict gate returned an inconsistent eligible status")
    series = history.nav_series(portfolio_name, window)
    if series is None:
        return {**common, f"{prefix}_status": "OFFICIAL_BACKTEST_INCOMPLETE_NAV"}
    metrics = backtester._forward_metrics(series)
    values: dict[str, float | int | None] = {
        "forward_return": metrics.total_return,
        "forward_annualized_return": metrics.annualized_return,
        "forward_volatility": metrics.annualized_volatility,
        "forward_sharpe": metrics.sharpe_ratio,
        "forward_mdd": metrics.maximum_drawdown,
        "forward_var": metrics.historical_var,
        "forward_cvar": metrics.historical_cvar,
        "forward_downside_deviation": metrics.downside_deviation,
        "forward_return_observation_count": metrics.return_observation_count,
    }
    if any(value is not None and not isfinite(value) for value in values.values() if isinstance(value, float)):
        raise DatasetBuildError("official forward label contains a non-finite metric")
    return {
        **common,
        f"{prefix}_available": True,
        f"{prefix}_status": "OFFICIAL_BACKTEST",
        **{f"{name}_{horizon}d": value for name, value in values.items()},
    }


def _field_definitions() -> list[dict[str, object]]:
    fields: list[tuple[str, str, str, str]] = [
        ("decision_date", "IDENTIFIER", "POINT_IN_TIME", "SQLite model_portfolios.Date"),
        ("portfolio_id", "IDENTIFIER", "POINT_IN_TIME", "portfolio_name is the stable source identifier"),
        ("portfolio_name", "IDENTIFIER", "POINT_IN_TIME", "SQLite model_portfolios.Portfolio Name"),
        ("portfolio_currency", "IDENTIFIER", "POINT_IN_TIME", "source constituent currency descriptor"),
        ("constituent_count", "IDENTIFIER", "POINT_IN_TIME", "source holdings count"),
    ]
    fields.extend((name, "POINT_IN_TIME_FEATURE", "POINT_IN_TIME", "canonical active-policy aggregation") for name in ACTIVE_FEATURES)
    fields.extend((name, "POINT_IN_TIME_FEATURE", "POINT_IN_TIME", "source allocation composition") for name in (
        "allocation_total", "portfolio_concentration_hhi", "top_3_weight", "huf_exposure", "eur_exposure",
        "usd_exposure", "other_currency_exposure", "unhedged_exposure_source",
    ))
    fields.extend((name, "KNOWLEDGE_CONSTRAINT", "TIMELESS_METHODOLOGY", "local Graphify graph node") for name in (
        "knowledge_constraint_count", "knowledge_constraint_ids",
    ))
    fields.extend((name, "DATA_QUALITY", "POINT_IN_TIME", "dataset construction audit") for name in (
        "currency_exposure_complete", "asset_history_features_available", "asset_history_unavailable_reason",
        "knowledge_complete", "ranking_eligible", "ranking_rejection_reasons", "feature_complete",
        "insufficient_history", "source_conflict", "duplicate_constituent_count",
    ))
    for horizon in HORIZONS:
        fields.extend((name, "FORWARD_LABEL", "FORWARD_VALIDATION_ONLY", "strict official backtest") for name in (
            f"label_{horizon}d_start_date", f"label_{horizon}d_end_date", f"label_{horizon}d_available",
            f"label_{horizon}d_status", f"forward_return_{horizon}d", f"forward_annualized_return_{horizon}d",
            f"forward_volatility_{horizon}d", f"forward_sharpe_{horizon}d", f"forward_mdd_{horizon}d",
            f"forward_var_{horizon}d", f"forward_cvar_{horizon}d", f"forward_downside_deviation_{horizon}d",
            f"forward_return_observation_count_{horizon}d",
        ))
    return [
        {"field_name": name, "type": "boolean" if name.endswith(("_available", "_complete")) or name in {"feature_complete", "insufficient_history", "source_conflict", "ranking_eligible"} else "number_or_null" if name not in {"decision_date", "portfolio_id", "portfolio_name", "portfolio_currency", "knowledge_constraint_ids", "asset_history_unavailable_reason", "ranking_rejection_reasons"} else "string_or_null", "role": role, "timing": timing, "provenance": provenance, "missing_semantics": "UNAVAILABLE_NOT_ZERO"}
        for name, role, timing, provenance in fields
    ]


def _validate_no_forward_labels_in_feature_schema() -> None:
    fields = _field_definitions()
    if any(item["role"] == "FORWARD_LABEL" and item["timing"] != "FORWARD_VALIDATION_ONLY" for item in fields):
        raise DatasetBuildError("forward label has an unsafe timing classification")
    if any(item["role"] == "POINT_IN_TIME_FEATURE" and str(item["field_name"]).startswith("forward_") for item in fields):
        raise DatasetBuildError("forward label entered point-in-time feature schema")


def _validate_row_timing(row: dict[str, object], decision_date: date) -> None:
    for horizon in HORIZONS:
        start = row[f"label_{horizon}d_start_date"]
        end = row[f"label_{horizon}d_end_date"]
        if start != decision_date.isoformat() or not isinstance(end, str) or end <= decision_date.isoformat():
            raise DatasetBuildError("forward label interval does not start at decision date and end later")


def _manifest(
    *, rows: list[dict[str, object]], dates: tuple[date, ...], rules_path: Path, database_path: Path,
    graph_path: Path, contract_path: Path, evidence_paths: dict[str, Path], knowledge: tuple[KnowledgeItem, ...], policy_name: str,
    policy_version: str, policy_schema_version: int,
) -> dict[str, object]:
    field_definitions = _field_definitions()
    missing_counts = {
        item["field_name"]: sum(row.get(str(item["field_name"])) is None for row in rows)
        for item in field_definitions
    }
    label_availability = {
        f"{horizon}d": sum(row[f"label_{horizon}d_available"] is True for row in rows)
        for horizon in HORIZONS
    }
    per_date = [
        {
            "decision_date": current.isoformat(),
            "portfolio_rows": sum(row["decision_date"] == current.isoformat() for row in rows),
            "eligible_rows": sum(
                row["decision_date"] == current.isoformat() and row["ranking_eligible"] is True
                for row in rows
            ),
            "ineligible_rows": sum(
                row["decision_date"] == current.isoformat() and row["ranking_eligible"] is False
                for row in rows
            ),
        }
        for current in dates
    ]
    categories = Counter(item.knowledge_category for item in knowledge)
    rows_with_partial = sum(not bool(row["feature_complete"]) for row in rows)
    sources = {
        "database": _source_reference(database_path), "active_policy": _source_reference(rules_path),
        "active_contract": _source_reference(contract_path), "graphify_graph": _source_reference(graph_path),
        **{name: _source_reference(path) for name, path in sorted(evidence_paths.items())},
    }
    fingerprint_payload = {
        "schema_version": DATASET_SCHEMA_VERSION, "dataset_version": DATASET_VERSION,
        "field_definitions": field_definitions, "sources": sources, "rows": rows,
    }
    fingerprint = _canonical_fingerprint(fingerprint_payload)
    status = DATASET_STATUS_VALIDATED if all(label_availability.values()) and rows_with_partial == 0 else DATASET_STATUS_CAVEATS
    return {
        "schema_version": DATASET_SCHEMA_VERSION,
        "dataset_version": DATASET_VERSION,
        "dataset_status": status,
        "policy_identity": {"name": policy_name, "version": policy_version, "schema_version": policy_schema_version, "activation_state": "ACTIVE"},
        "decision_date_range": {"earliest": dates[0].isoformat(), "latest": dates[-1].isoformat(), "total_decision_dates": len(dates)},
        "row_count": len(rows), "portfolio_count": len({row["portfolio_id"] for row in rows}),
        "eligible_rows": sum(row["ranking_eligible"] is True for row in rows),
        "ineligible_rows": sum(row["ranking_eligible"] is False for row in rows),
        "decision_date_coverage": per_date,
        "point_in_time_complete_rows": len(rows) - rows_with_partial, "rows_with_partial_features": rows_with_partial,
        "currencies": dict(sorted(Counter(str(row["portfolio_currency"]) for row in rows).items())),
        "feature_schema": field_definitions,
        "asset_history": {"implemented_features": [], "status": "UNAVAILABLE", "reason": ASSET_HISTORY_UNAVAILABLE},
        "graphify_knowledge": {
            "source": "data/knowledge/graphify-out/graph.json", "total_relevant_items": len(knowledge),
            "items_admitted": sum(item.admitted for item in knowledge), "items_excluded": sum(not item.admitted for item in knowledge),
            "classification_counts": dict(sorted(categories.items())), "items": [asdict(item) for item in knowledge],
            "coverage_rows": len(rows), "retrieval_query_identifier": "LOCAL_GRAPH_JSON_NODE_CLASSIFICATION_V1",
        },
        "forward_label_availability": label_availability,
        "missing_value_counts": missing_counts,
        "leakage_validation": {"result": "NO_POINT_IN_TIME_LEAKAGE", "feature_information_date_rule": "feature_information_date <= decision_date", "forward_labels_not_features": True, "current_or_forward_graphify_facts_admitted": False},
        "source_references": sources,
        "dataset_fingerprint": fingerprint,
        "caveats": _caveats(rows, label_availability),
    }


def _caveats(rows: list[dict[str, object]], labels: dict[str, int]) -> list[str]:
    caveats = ["Asset-level return history is not stored in a source with safe portfolio-return semantics; no asset trailing features were synthesized."]
    if any(value == 0 for value in labels.values()):
        caveats.append("No complete official NAV labels are available for one or more horizons; unavailable labels remain null.")
    if any(not bool(row["feature_complete"]) for row in rows):
        caveats.append("Some rows have incomplete active-policy features and remain explicitly incomplete rather than zero-filled.")
    return caveats


def write_dataset_csv(path: Path, rows: list[dict[str, object]]) -> None:
    """Atomically write deterministically ordered dataset rows as standard CSV."""
    if not rows:
        raise DatasetBuildError("dataset has no rows")
    fieldnames = sorted({key for row in rows for key in row})
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="raise")
            writer.writeheader()
            for row in rows:
                writer.writerow({key: "" if value is None else value for key, value in row.items()})
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def write_manifest(path: Path, manifest: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True)
            handle.write("\n")
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DatasetBuildError(f"{label} is missing or malformed: {path}") from error
    if not isinstance(value, dict):
        raise DatasetBuildError(f"{label} root must be an object")
    return value


def _validate_existing_policy_evidence() -> dict[str, Path]:
    """Fail closed when the retained active-policy validations are no longer valid."""
    root = Path(__file__).resolve().parents[3]
    paths = {
        "strict_pipeline_validation": root / "data/audit/strict_backtest_pipeline_validation.json",
        "methodology_validation": root / "data/audit/capital_preservation_metrics_ranking_validation.json",
        "current_universe_validation": root / "data/audit/active_ranking_policy_current_universe_validation.json",
        "temporal_policy_validation": root / "data/audit/active_ranking_policy_temporal_stability.json",
    }
    expected = {
        "strict_pipeline_validation": {"STRICT_BACKTEST_PIPELINE_VALIDATED"},
        "methodology_validation": {"CAPITAL_PRESERVATION_METHODOLOGY_VALIDATED_WITH_CAVEATS"},
        "current_universe_validation": {"ACTIVE_RANKING_POLICY_CURRENT_UNIVERSE_VALIDATED"},
        "temporal_policy_validation": {
            "ACTIVE_POLICY_TEMPORAL_STABILITY_VALIDATED",
            "ACTIVE_POLICY_TEMPORAL_STABILITY_VALIDATED_WITH_CAVEATS",
        },
    }
    for name, path in paths.items():
        result = _load_object(path, name).get("validation_status")
        if result not in expected[name]:
            raise DatasetBuildError(f"retained {name} is not validated: {result!r}")
    return paths


def _optional_iso_date(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _source_reference(path: Path) -> dict[str, str]:
    try:
        return {"path": path.as_posix(), "sha256": sha256(path.read_bytes()).hexdigest()}
    except OSError as error:
        raise DatasetBuildError(f"cannot read source artifact: {path}") from error


def _canonical_fingerprint(value: object) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
