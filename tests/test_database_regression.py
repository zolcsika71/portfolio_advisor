"""Regression coverage for the checked-in SQLite observation.

The repository opens SQLite with ``mode=ro``.  The digest assertion makes the
read-only contract visible at this integration boundary as well.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from portfolio_advisor.advisor.service import CapitalPreservationAdvisor
from portfolio_advisor.database.repository import ModelPortfolioRepository
from tests.fixtures.model_portfolio_fixture import (
    HISTORICAL_RANKING_DATE,
    HISTORICAL_RANKING_ORDER,
    LATER_PRODUCTION_LIKE_DATE,
    append_later_production_like_snapshot,
    create_historical_ranking_database,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = (
    PROJECT_ROOT
    / "data"
    / "knowledge"
    / "validated_rules"
    / "capital_preservation_ranking.yaml"
)


def test_historical_fixture_ranking_is_deterministic_and_read_only(tmp_path: Path) -> None:
    """Pin the reviewed D1 ranking without reading mutable production SQLite."""
    database_path = tmp_path / "historical_ranking.sqlite"
    create_historical_ranking_database(database_path)
    before = sha256(database_path.read_bytes()).digest()

    result = CapitalPreservationAdvisor(
        ModelPortfolioRepository(database_path), RULES_PATH
    ).evaluate(alternative_count=20)

    assert result.observation_date is not None
    assert result.observation_date == HISTORICAL_RANKING_DATE
    assert result.rule_set_version == "1.0.1"
    assert result.rules_status == "approved"
    assert not result.proposed_rules_explicitly_enabled
    assert result.selected_portfolio is not None
    assert result.selected_portfolio.metrics.portfolio_name == "PB Konzervatív MultiCCY"
    assert [item.metrics.portfolio_name for item in result.ranking] == list(HISTORICAL_RANKING_ORDER)
    assert [item.rank for item in result.ranking] == [*range(1, 12), None]
    assert any(warning.startswith("historical_var:") for warning in result.warnings)
    assert any(warning.startswith("historical_cvar:") for warning in result.warnings)
    assert any(warning.startswith("liquidity_indicators:") for warning in result.warnings)
    assert any(warning.startswith("cost_indicators:") for warning in result.warnings)
    assert any(warning.startswith("sortino_ratio:") for warning in result.warnings)
    assert sha256(database_path.read_bytes()).digest() == before


def test_later_snapshot_changes_dynamic_latest_without_changing_historical_fixture(tmp_path: Path) -> None:
    """Monthly D2 additions cannot move the immutable D1 regression baseline."""
    database_path = tmp_path / "production_like.sqlite"
    create_historical_ranking_database(database_path)
    repository = ModelPortfolioRepository(database_path)
    assert repository.latest_observation_date() == HISTORICAL_RANKING_DATE

    append_later_production_like_snapshot(database_path)

    dynamic = CapitalPreservationAdvisor(repository, RULES_PATH).evaluate(alternative_count=20)
    historical = CapitalPreservationAdvisor(repository, RULES_PATH).evaluate(
        observation_date=HISTORICAL_RANKING_DATE, alternative_count=20
    )
    assert LATER_PRODUCTION_LIKE_DATE > HISTORICAL_RANKING_DATE
    assert repository.latest_observation_date() == LATER_PRODUCTION_LIKE_DATE
    assert dynamic.observation_date == LATER_PRODUCTION_LIKE_DATE
    assert historical.observation_date == HISTORICAL_RANKING_DATE
    assert [item.metrics.portfolio_name for item in historical.ranking] == list(HISTORICAL_RANKING_ORDER)
