from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import cast

from portfolio_advisor.database.repository import (
    HoldingObservation,
    ModelPortfolioRepository,
)
from portfolio_advisor.features.dataset import (
    DATASET_STATUS_CAVEATS,
    KnowledgeItem,
    _field_definitions,
    build_point_in_time_feature_dataset,
    classify_graphify_node,
    knowledge_available_at,
    portfolio_structure,
)

ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "database/model_portfolio.sqlite"
RULES = ROOT / "data/knowledge/validated_rules/capital_preservation_ranking.yaml"
GRAPH = ROOT / "data/knowledge/graphify-out/graph.json"
CONTRACT = ROOT / "data/audit/capital_preservation_ranking_policy_contract.json"


def _holding(
    *, isin: str, allocation: float | None, currency: str | None = "EUR", risk: str | None = "Hedged"
) -> HoldingObservation:
    return HoldingObservation(
        portfolio_name="Portfolio", product="Fund", isin=isin, allocation=allocation, currency=currency,
        currency_risk=risk, return_1y=0.01, sharpe_ratio_1y=0.2, volatility_1y=0.03,
        downside_risk=0.02, maximum_drawdown=-0.04,
    )


def test_source_weight_structure_is_preserved_without_renormalizing_or_dropping() -> None:
    structure = portfolio_structure([
        _holding(isin="A", allocation=50.0, currency="EUR"),
        _holding(isin="B", allocation=30.0, currency="USD"),
        _holding(isin="C", allocation=20.0, currency="HUF", risk="Unhedged"),
    ])
    assert structure["allocation_total"] == 100.0
    assert structure["top_3_weight"] == 1.0
    assert structure["portfolio_concentration_hhi"] == 0.38
    assert structure["huf_exposure"] == 0.2
    assert structure["unhedged_exposure_source"] == 0.2

    unavailable = portfolio_structure([
        _holding(isin="A", allocation=50.0), _holding(isin="B", allocation=None),
    ])
    assert unavailable["allocation_total"] is None
    assert unavailable["portfolio_concentration_hhi"] is None
    assert unavailable["unhedged_exposure_source"] is None


def test_graphify_timing_categories_fail_closed_and_timeless_methodology_is_allowed() -> None:
    assert classify_graphify_node({"knowledge_category": "FORWARD_INFORMATION"}, False) == (
        "FORWARD_INFORMATION", False, "FORWARD_INFORMATION_EXCLUDED_FROM_HISTORICAL_FEATURES"
    )
    assert classify_graphify_node({"knowledge_category": "CURRENT_ONLY_FACT"}, False) == (
        "CURRENT_ONLY_FACT", False, "CURRENT_ONLY_FACT_EXCLUDED_FROM_HISTORICAL_FEATURES"
    )
    assert classify_graphify_node({"valid_from": "2027-01-01"}, False) == (
        "POINT_IN_TIME_FACT", True, None
    )
    assert classify_graphify_node({}, True) == ("TIMELESS_METHODOLOGY", True, None)

    timeless = KnowledgeItem("method", "Method", "source.pdf", "GRAPHIFY_NODE", ("node",), (), "TIMELESS_METHODOLOGY", None, None, True, None)
    future = KnowledgeItem("future", "Fact", "source.pdf", "GRAPHIFY_NODE", ("node2",), (), "POINT_IN_TIME_FACT", "2027-01-01", None, True, None)
    assert knowledge_available_at((timeless, future), date(2026, 7, 6)) == (timeless,)
    assert knowledge_available_at((timeless, future), date(2027, 1, 1)) == (timeless, future)


def test_dataset_is_deterministic_and_labels_remain_outside_feature_matrix() -> None:
    first_rows, first_manifest = build_point_in_time_feature_dataset(
        database_path=DATABASE, rules_path=RULES, graph_path=GRAPH, contract_path=CONTRACT,
    )
    second_rows, second_manifest = build_point_in_time_feature_dataset(
        database_path=DATABASE, rules_path=RULES, graph_path=GRAPH, contract_path=CONTRACT,
    )
    assert first_rows == second_rows
    assert first_manifest["dataset_fingerprint"] == second_manifest["dataset_fingerprint"]
    assert first_manifest["dataset_status"] == DATASET_STATUS_CAVEATS
    leakage = cast(dict[str, object], first_manifest["leakage_validation"])
    assert leakage["result"] == "NO_POINT_IN_TIME_LEAKAGE"
    repository = ModelPortfolioRepository(DATABASE)
    dates = repository.observation_dates()
    expected_keys = {
        (current.isoformat(), holding.portfolio_name)
        for current in dates
        for holding in repository.load_holdings(current)
    }
    assert first_manifest["decision_date_range"] == {
        "earliest": dates[0].isoformat(),
        "latest": repository.latest_observation_date().isoformat(),
        "total_decision_dates": len(dates),
    }
    assert {(str(row["decision_date"]), str(row["portfolio_id"])) for row in first_rows} == expected_keys
    assert all(str(row["label_90d_end_date"]) > str(row["decision_date"]) for row in first_rows)
    assert all(row["label_90d_available"] is False for row in first_rows)
    assert all(row["forward_return_90d"] is None for row in first_rows)
    assert all(row["knowledge_constraint_count"] == 4 for row in first_rows)
    fields = _field_definitions()
    assert not any(
        item["role"] == "POINT_IN_TIME_FEATURE" and str(item["field_name"]).startswith("forward_")
        for item in fields
    )
    assert all(item["timing"] == "FORWARD_VALIDATION_ONLY" for item in fields if item["role"] == "FORWARD_LABEL")
