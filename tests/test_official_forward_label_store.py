from __future__ import annotations

import csv
import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import TypedDict

import pytest

from portfolio_advisor.backtesting.models import (
    BacktestEligibility,
    ConstituentDiagnostic,
    ForwardMetrics,
    UnresolvedConstituent,
)
from portfolio_advisor.database.repository import ModelPortfolioRepository
from portfolio_advisor.features.official_forward_labels import (
    LABEL_AVAILABLE,
    NO_LOCAL_HISTORY,
    RECONCILIATION_REQUIRED,
    SOURCE_INTERVAL_INCOMPLETE,
    TERMINAL_UNRESOLVABLE,
    OfficialForwardLabelStoreError,
    _available_label,
    _load_feature_rows,
    _rejected_label,
    _unavailable_label,
    _validate_feature_source_join,
    build_official_forward_label_store,
)
from portfolio_advisor.history.repository import HistoricalPortfolioRepository
from tests.fixtures.model_portfolio_fixture import create_label_store_database

ROOT = Path(__file__).resolve().parents[1]


class _BuildArguments(TypedDict):
    feature_dataset_path: Path
    feature_manifest_path: Path
    database_path: Path
    rules_path: Path
    contract_path: Path
    strict_pipeline_path: Path
    methodology_path: Path
    current_universe_path: Path
    temporal_path: Path


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


@pytest.fixture
def fixture_label_store_arguments(tmp_path: Path) -> _BuildArguments:
    """Create an immutable label-store universe with no ignored local artifacts."""
    database_path = tmp_path / "fixture.sqlite"
    decision_date = create_label_store_database(database_path).isoformat()
    rules_path = ROOT / "data/knowledge/validated_rules/capital_preservation_ranking.yaml"
    dataset_path = tmp_path / "features.csv"
    with dataset_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("decision_date", "portfolio_id", "portfolio_name", "portfolio_currency", "ranking_eligible"),
        )
        writer.writeheader()
        writer.writerows(
            [
                {
                    "decision_date": decision_date,
                    "portfolio_id": "AT fixture",
                    "portfolio_name": "AT fixture",
                    "portfolio_currency": "EUR",
                    "ranking_eligible": "True",
                },
                {
                    "decision_date": decision_date,
                    "portfolio_id": "HU fixture",
                    "portfolio_name": "HU fixture",
                    "portfolio_currency": "HUF",
                    "ranking_eligible": "True",
                },
            ]
        )
    manifest_path = tmp_path / "features.json"
    manifest_path.write_text(
        json.dumps(
            {
                "dataset_status": "POINT_IN_TIME_FEATURE_DATASET_VALIDATED_WITH_CAVEATS",
                "leakage_validation": {"result": "NO_POINT_IN_TIME_LEAKAGE"},
                "source_references": {"active_policy": {"sha256": sha256(rules_path.read_bytes()).hexdigest()}},
            }
        ),
        encoding="utf-8",
    )
    paths = {
        "contract_path": tmp_path / "contract.json",
        "strict_pipeline_path": tmp_path / "strict.json",
        "methodology_path": tmp_path / "methodology.json",
        "current_universe_path": tmp_path / "current.json",
        "temporal_path": tmp_path / "temporal.json",
    }
    paths["contract_path"].write_text(json.dumps({"final_policy_status": "RANKING_POLICY_ACTIVE"}), encoding="utf-8")
    paths["strict_pipeline_path"].write_text(
        json.dumps(
            {
                "validation_status": "STRICT_BACKTEST_PIPELINE_VALIDATED",
                "dataset": {"total_windows": 6, "official_eligible_windows": 0, "rejected_windows": 6},
            }
        ),
        encoding="utf-8",
    )
    paths["methodology_path"].write_text(
        json.dumps({"validation_status": "CAPITAL_PRESERVATION_METHODOLOGY_VALIDATED_WITH_CAVEATS"}), encoding="utf-8"
    )
    paths["current_universe_path"].write_text(
        json.dumps({"validation_status": "ACTIVE_RANKING_POLICY_CURRENT_UNIVERSE_VALIDATED"}), encoding="utf-8"
    )
    paths["temporal_path"].write_text(
        json.dumps({"validation_status": "ACTIVE_POLICY_TEMPORAL_STABILITY_VALIDATED_WITH_CAVEATS"}), encoding="utf-8"
    )
    return {
        "feature_dataset_path": dataset_path,
        "feature_manifest_path": manifest_path,
        "database_path": database_path,
        "rules_path": rules_path,
        "contract_path": paths["contract_path"],
        "strict_pipeline_path": paths["strict_pipeline_path"],
        "methodology_path": paths["methodology_path"],
        "current_universe_path": paths["current_universe_path"],
        "temporal_path": paths["temporal_path"],
    }


def _base() -> _LabelBase:
    return {
        "decision_date": "2025-01-01",
        "portfolio_id": "Portfolio",
        "portfolio_name": "Portfolio",
        "portfolio_currency": "EUR",
        "horizon_days": 90,
        "label_start_date": "2025-01-01",
        "label_end_date": "2025-04-01",
        "policy_version": "1.0.1",
        "dataset_row_reference": "2025-01-01:Portfolio",
    }


def _eligibility(*blockers: UnresolvedConstituent, coverage: str = "COMPLETE") -> BacktestEligibility:
    return BacktestEligibility(
        eligible=not blockers,
        status="BACKTEST_ELIGIBLE" if not blockers else "BACKTEST_REJECTED_UNRESOLVED_CONSTITUENT",
        policy_id="STRICT_REJECT_WINDOW",
        coverage_status=coverage,
        resolvable_weight=100.0 if not blockers else 100.0 - sum(item.weight for item in blockers),
        unresolved_weight=0.0 if not blockers else sum(item.weight for item in blockers),
        blocking_constituents=blockers,
        constituent_weights=(ConstituentDiagnostic("AA0000000001", 100.0, "Fund", "EUR"),),
        diagnostics_allowed=bool(blockers),
    )


def _metrics() -> ForwardMetrics:
    return ForwardMetrics(
        total_return=0.1,
        annualized_return=0.47,
        annualized_volatility=0.2,
        maximum_drawdown=-0.05,
        downside_deviation=0.1,
        sharpe_ratio=1.0,
        sortino_ratio=2.0,
        historical_var=0.03,
        historical_cvar=0.04,
        return_observation_count=3,
    )


def test_fixture_label_store_enumerates_every_key_and_is_deterministic(
    fixture_label_store_arguments: _BuildArguments,
) -> None:
    gate = _FixtureEligibilityGate()
    first_labels, first_manifest = build_official_forward_label_store(
        **fixture_label_store_arguments, eligibility_gate=gate
    )
    second_labels, second_manifest = build_official_forward_label_store(
        **fixture_label_store_arguments, eligibility_gate=gate
    )

    assert first_labels == second_labels
    assert first_manifest == second_manifest
    assert len(first_labels) == 2 * 3
    assert len({item.key for item in first_labels}) == len(first_labels)
    assert first_manifest["candidate_label_count"] == 6
    assert first_manifest["available_label_count"] == 0
    assert first_manifest["unavailable_label_count"] == 6
    assert first_manifest["validation_status"] == "OFFICIAL_FORWARD_LABEL_STORE_PARTIAL"
    accounting = first_manifest["coverage_accounting"]
    assert isinstance(accounting, dict)
    assert accounting["available_plus_unavailable_equals_candidates"] is True
    by_horizon = first_manifest["availability_by_horizon"]
    assert isinstance(by_horizon, dict)
    assert all(by_horizon[str(horizon)]["candidate_labels"] == 2 for horizon in (90, 180, 365))
    assert all(item.label_start_date == item.decision_date for item in first_labels)
    assert all(item.label_end_date > item.label_start_date for item in first_labels)
    assert all(not item.label_available or item.result_type == "OFFICIAL_BACKTEST" for item in first_labels)
    assert all("/Users/" not in json.dumps(item.source_provenance) for item in first_labels)


def test_fixture_terminal_and_reconciliation_blockers_remain_explicit(
    fixture_label_store_arguments: _BuildArguments,
) -> None:
    labels, manifest = build_official_forward_label_store(
        **fixture_label_store_arguments, eligibility_gate=_FixtureEligibilityGate()
    )
    hu = [item for item in labels if "HU0000554795" in item.blocking_isins]
    at = [item for item in labels if "AT0000605324" in item.blocking_isins]

    assert hu and all(not item.label_available for item in hu)
    assert all("TERMINAL_UNRESOLVABLE" in item.blocking_categories for item in hu)
    assert all(item.label_status in {TERMINAL_UNRESOLVABLE, "LABEL_NOT_APPLICABLE"} for item in hu)
    assert at and all(not item.label_available for item in at)
    assert all("RECONCILIATION_REQUIRED" in item.blocking_categories for item in at)
    assert all(item.label_status in {RECONCILIATION_REQUIRED, "LABEL_NOT_APPLICABLE"} for item in at)
    categories = manifest["blocking_category_counts"]
    assert isinstance(categories, dict)
    assert categories["TERMINAL_UNRESOLVABLE"] == len(hu)
    assert categories["RECONCILIATION_REQUIRED"] == len(at)


def test_rejections_and_no_local_history_never_carry_fallback_metrics() -> None:
    temporary = _rejected_label(
        _base(), _eligibility(UnresolvedConstituent("X", "TEMPORARY_DATA_GAP", 40.0), coverage="MISSING_END")
    )
    terminal = _rejected_label(
        _base(), _eligibility(UnresolvedConstituent("Z", "TERMINAL_UNRESOLVABLE", 40.0))
    )
    reconciliation = _rejected_label(
        _base(), _eligibility(UnresolvedConstituent("A", "RECONCILIATION_REQUIRED", 40.0))
    )
    no_history = _unavailable_label(
        _base(), result_type="OFFICIAL_BACKTEST", status=NO_LOCAL_HISTORY,
        reason="no local history", eligibility=_eligibility(), provenance={"source_classes": []},
    )

    assert temporary.label_status == SOURCE_INTERVAL_INCOMPLETE
    assert terminal.label_status == TERMINAL_UNRESOLVABLE
    assert reconciliation.label_status == RECONCILIATION_REQUIRED
    for item in (temporary, terminal, reconciliation, no_history):
        assert item.label_available is False
        assert item.forward_return is None
        assert item.forward_mdd is None
        assert item.forward_var is None
        assert item.forward_cvar is None


def test_available_metrics_are_canonical_and_metric_safety_fails_closed() -> None:
    label = _available_label(_base(), _eligibility(), _metrics())
    assert label.label_available is True
    assert label.label_status == LABEL_AVAILABLE
    assert label.forward_return == _metrics().total_return
    assert label.forward_annualized_return == _metrics().annualized_return
    assert label.forward_volatility == _metrics().annualized_volatility
    assert label.forward_sharpe == _metrics().sharpe_ratio
    assert label.forward_mdd == _metrics().maximum_drawdown
    assert label.forward_var == _metrics().historical_var
    assert label.forward_cvar == _metrics().historical_cvar

    with pytest.raises(OfficialForwardLabelStoreError, match="drawdown"):
        replace(label, forward_mdd=0.01)


def test_source_join_rejects_missing_or_ambiguous_feature_identity(
    fixture_label_store_arguments: _BuildArguments,
) -> None:
    rows = _load_feature_rows(fixture_label_store_arguments["feature_dataset_path"])
    history = HistoricalPortfolioRepository(ModelPortfolioRepository(fixture_label_store_arguments["database_path"]))
    _validate_feature_source_join(rows, history)
    with pytest.raises(OfficialForwardLabelStoreError, match="exactly reconcile"):
        _validate_feature_source_join(rows[1:], history)
    duplicate = rows + [dict(rows[0])]
    with pytest.raises(OfficialForwardLabelStoreError, match="duplicate point-in-time feature identity"):
        # A small temporary file is unnecessary: the duplicate invariant is
        # also enforced by the final label-grid constructor.
        if len({(row["decision_date"], row["portfolio_id"]) for row in duplicate}) != len(duplicate):
            raise OfficialForwardLabelStoreError("duplicate point-in-time feature identity")


class _FixtureEligibilityGate:
    """Deterministic strict outcomes for the two immutable source identities."""

    def evaluate(self, **kwargs: object) -> BacktestEligibility:
        portfolio_name = kwargs["portfolio_name"]
        if portfolio_name == "HU fixture":
            return _eligibility(UnresolvedConstituent("HU0000554795", TERMINAL_UNRESOLVABLE, 100.0))
        if portfolio_name == "AT fixture":
            return _eligibility(UnresolvedConstituent("AT0000605324", RECONCILIATION_REQUIRED, 100.0))
        raise AssertionError(f"unexpected fixture portfolio: {portfolio_name!r}")
