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
    """Pin the current explicitly enabled proposed-policy outcome."""
    before = sha256(DATABASE_PATH.read_bytes()).digest()

    result = CapitalPreservationAdvisor(
        ModelPortfolioRepository(DATABASE_PATH), RULES_PATH
    ).evaluate(allow_proposed_rules=True, alternative_count=20)

    assert result.observation_date is not None
    assert result.observation_date.isoformat() == "2026-07-06"
    assert result.rule_set_version == "1.0.0"
    assert result.rules_status == "proposed"
    assert result.proposed_rules_explicitly_enabled
    assert result.selected_portfolio is not None
    assert result.selected_portfolio.metrics.portfolio_name == "PB Dinamikus MultiCCY"
    assert [item.metrics.portfolio_name for item in result.ranking] == [
        "PB Dinamikus MultiCCY",
        "PB Dinamikus EUR",
        "PB Kiegyensúlyozott MultiCCY",
        "PB Dinamikus HUF",
        "PB Konzervatív MultiCCY",
        "PB Kiegyensúlyozott HUF",
        "PB Konzervatív HUF",
        "PB Kiegyensúlyozott USD",
        "PB Kiegyensúlyozott EUR",
        "PB Konzervatív USD",
        "PB Konzervatív EUR",
        "PB Dinamikus USD",
    ]
    assert [item.rank for item in result.ranking] == [*range(1, 12), None]
    assert any(warning.startswith("historical_var:") for warning in result.warnings)
    assert any(warning.startswith("historical_cvar:") for warning in result.warnings)
    assert any(warning.startswith("liquidity_indicators:") for warning in result.warnings)
    assert any(warning.startswith("cost_indicators:") for warning in result.warnings)
    assert any(warning.startswith("sortino_ratio:") for warning in result.warnings)
    assert sha256(DATABASE_PATH.read_bytes()).digest() == before
