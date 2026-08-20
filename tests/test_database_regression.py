"""Regression coverage for the checked-in SQLite observation.

The repository opens SQLite with ``mode=ro``.  The digest assertion makes the
read-only contract visible at this integration boundary as well.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from portfolio_advisor.advisor.service import CapitalPreservationAdvisor
from portfolio_advisor.database.repository import ModelPortfolioRepository

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = PROJECT_ROOT / "database" / "model_portfolio.sqlite"
RULES_PATH = (
    PROJECT_ROOT
    / "data"
    / "knowledge"
    / "validated_rules"
    / "capital_preservation_ranking.yaml"
)


def test_2026_07_06_database_ranking_is_deterministic_and_read_only() -> None:
    """Pin the current approved-policy outcome without database mutation."""
    before = sha256(DATABASE_PATH.read_bytes()).digest()

    result = CapitalPreservationAdvisor(
        ModelPortfolioRepository(DATABASE_PATH), RULES_PATH
    ).evaluate(alternative_count=20)

    assert result.observation_date is not None
    assert result.observation_date.isoformat() == "2026-07-06"
    assert result.rule_set_version == "1.0.1"
    assert result.rules_status == "approved"
    assert not result.proposed_rules_explicitly_enabled
    assert result.selected_portfolio is not None
    assert result.selected_portfolio.metrics.portfolio_name == "PB Konzervatív MultiCCY"
    assert [item.metrics.portfolio_name for item in result.ranking] == [
        "PB Konzervatív MultiCCY",
        "PB Konzervatív HUF",
        "PB Konzervatív EUR",
        "PB Kiegyensúlyozott MultiCCY",
        "PB Kiegyensúlyozott HUF",
        "PB Konzervatív USD",
        "PB Dinamikus MultiCCY",
        "PB Dinamikus HUF",
        "PB Kiegyensúlyozott EUR",
        "PB Kiegyensúlyozott USD",
        "PB Dinamikus EUR",
        "PB Dinamikus USD",
    ]
    assert [item.rank for item in result.ranking] == [*range(1, 12), None]
    assert any(warning.startswith("historical_var:") for warning in result.warnings)
    assert any(warning.startswith("historical_cvar:") for warning in result.warnings)
    assert any(warning.startswith("liquidity_indicators:") for warning in result.warnings)
    assert any(warning.startswith("cost_indicators:") for warning in result.warnings)
    assert any(warning.startswith("sortino_ratio:") for warning in result.warnings)
    assert sha256(DATABASE_PATH.read_bytes()).digest() == before
