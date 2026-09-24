"""Synthetic Phase 3A consumer-injection and shadow-comparison tests."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from portfolio_advisor.advisor.service import CapitalPreservationAdvisor
from portfolio_advisor.database.model_portfolio_phase1 import (
    AnalyticalModelPortfolioRepository,
    MnbEvidenceBinding,
    MnbOtcExactText,
    ModelPortfolioPhase1Error,
    admit_mnb_otc_evidence,
    admit_workbook_normalization,
    install_phase1_schema,
)
from portfolio_advisor.database.model_portfolio_shadow import (
    ExpectedDifference,
    ShadowComparisonError,
    ShadowReadContext,
    ShadowWorkflow,
    run_shadow_comparison,
)
from portfolio_advisor.database.repository import ModelPortfolioRepository
from portfolio_advisor.features.dataset import portfolio_structure
from portfolio_advisor.history.mnb_otc import MnbOtcObservation, MnbOtcRepository
from portfolio_advisor.history.models import ForwardWindow
from portfolio_advisor.history.repository import HistoricalPortfolioRepository
from portfolio_advisor.metrics.portfolio import calculate_all_portfolio_metrics
from portfolio_advisor.prospective.validation import (
    LIVE_RECORD,
    build_prospective_decision,
)
from portfolio_advisor.tbsz.comparison import _target_allocations
from tests.fixtures.model_portfolio_phase1_fixture import (
    FIXTURE_DATE,
    create_phase1_databases,
)

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "data/knowledge/validated_rules/capital_preservation_ranking.yaml"


def _admitted_sources(
    tmp_path: Path,
) -> tuple[ModelPortfolioRepository, AnalyticalModelPortfolioRepository]:
    legacy, analytical, request = create_phase1_databases(tmp_path)
    exact = MnbOtcExactText(
        nominal_value_huf_thousand="1000.0",
        purchase_value_huf_thousand="1020.0",
        average_price="102.000000",
        minimum_price="101.900000",
        maximum_price="102.9096",
    )
    observation = MnbOtcObservation(
        isin="HU0000554795",
        instrument_name="Synthetic one-year government security",
        currency="HUF",
        period_start=date(2024, 11, 25),
        period_end=date(2024, 12, 1),
        nominal_value_huf_thousand=Decimal(exact.nominal_value_huf_thousand),
        purchase_value_huf_thousand=Decimal(exact.purchase_value_huf_thousand),
        average_price=Decimal(exact.average_price),
        minimum_price=Decimal(exact.minimum_price),
        maximum_price=Decimal("102.909600"),
        transaction_count=2,
        source_document="evidence/mnb/synthetic.pdf",
        source_document_hash="d" * 64,
    )
    MnbOtcRepository(legacy).ensure_schema()
    with sqlite3.connect(legacy) as connection:
        connection.execute(
            """INSERT INTO mnb_otc_observations VALUES(
                   ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
               )""",
            (
                observation.source,
                observation.isin,
                observation.instrument_name,
                observation.currency,
                observation.period_start.isoformat(),
                observation.period_end.isoformat(),
                exact.nominal_value_huf_thousand,
                exact.purchase_value_huf_thousand,
                exact.average_price,
                exact.minimum_price,
                exact.maximum_price,
                observation.transaction_count,
                observation.price_type,
                observation.frequency,
                observation.source_document,
                observation.source_document_hash,
            ),
        )
    with sqlite3.connect(analytical) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        install_phase1_schema(connection)
        admit_workbook_normalization(connection, request)
        admit_mnb_otc_evidence(
            connection,
            observation,
            exact,
            MnbEvidenceBinding(
                portable_evidence_role="evidence/mnb/synthetic.pdf",
                authorization_reference="synthetic-MNB-authorization",
            ),
        )
    return (
        ModelPortfolioRepository(legacy),
        AnalyticalModelPortfolioRepository(
            analytical, authority_epoch_id=request.authority.epoch_id
        ),
    )


def _advisor(context: ShadowReadContext) -> CapitalPreservationAdvisor:
    return CapitalPreservationAdvisor(context.model, RULES)


def _synthetic_graph(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "nodes": [
                    {
                        "id": "synthetic-sharpe-methodology",
                        "label": "Sharpe Ratio",
                        "source_file": "synthetic-methodology.txt",
                        "knowledge_category": "TIMELESS_METHODOLOGY",
                    }
                ]
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def test_shadow_run_covers_each_consumer_with_one_validated_session(
    tmp_path: Path,
) -> None:
    legacy, analytical = _admitted_sources(tmp_path)
    graph = _synthetic_graph(tmp_path / "graph.json")

    def current(context: ShadowReadContext) -> object:
        return _advisor(context).evaluate(alternative_count=100)

    def temporal(context: ShadowReadContext) -> object:
        return tuple(
            _advisor(context).evaluate(observation_date=item, alternative_count=100)
            for item in context.model.observation_dates()
        )

    def coverage(context: ShadowReadContext) -> object:
        history = HistoricalPortfolioRepository(context.model, None)
        return {
            "dates": history.observation_dates(),
            "nav_available": history.nav_history_available(),
            "holdings": tuple(
                asdict(item) for item in history.holdings_at(FIXTURE_DATE)
            ),
        }

    def features(context: ShadowReadContext) -> object:
        holdings = context.model.load_holdings(FIXTURE_DATE)
        return {
            "metrics": calculate_all_portfolio_metrics(holdings),
            "structures": {
                name: portfolio_structure(
                    [item for item in holdings if item.portfolio_name == name]
                )
                for name in sorted({item.portfolio_name for item in holdings})
            },
        }

    def labels(context: ShadowReadContext) -> object:
        history = HistoricalPortfolioRepository(context.model, None)
        return {
            "nav_available": history.nav_history_available(),
            "windows": tuple(
                asdict(history.forward_window(FIXTURE_DATE, horizon))
                for horizon in (90, 180, 365)
            ),
        }

    def prospective(context: ShadowReadContext) -> object:
        advisor = _advisor(context).evaluate(
            observation_date=FIXTURE_DATE, alternative_count=100
        )
        return build_prospective_decision(
            advisor_result=advisor,
            repository=context.model,
            rules_path=RULES,
            graph_path=graph,
            repository_root=tmp_path,
            record_type=LIVE_RECORD,
        )

    def ltia(context: ShadowReadContext) -> object:
        return _target_allocations(
            context.model,
            RULES,
            "PB Konzervatív USD",
        )

    def mnb(context: ShadowReadContext) -> object:
        return context.mnb_observations

    report = run_shadow_comparison(
        legacy_repository=legacy,
        analytical_repository=analytical,
        workflows=(
            ShadowWorkflow("ADVISOR_CURRENT", current),
            ShadowWorkflow("ADVISOR_TEMPORAL", temporal),
            ShadowWorkflow("STRICT_BACKTEST_COVERAGE", coverage),
            ShadowWorkflow("FEATURE_DATASET", features),
            ShadowWorkflow("FORWARD_LABELS", labels),
            ShadowWorkflow(
                "PROSPECTIVE_DECISION",
                prospective,
                expected_differences=(
                    ExpectedDifference(
                        "$.source_evidence_state.database_reference",
                        "legacy.sqlite",
                        "analytical.sqlite",
                        "the two isolated sources have different explicit paths",
                    ),
                ),
            ),
            ShadowWorkflow("LTIA_MODEL_COMPARISON", ltia),
            ShadowWorkflow("MNB_SEMANTIC_AUDIT", mnb),
        ),
    )

    assert report.status == "PASS"
    assert [item.name for item in report.workflows] == [
        "ADVISOR_CURRENT",
        "ADVISOR_TEMPORAL",
        "STRICT_BACKTEST_COVERAGE",
        "FEATURE_DATASET",
        "FORWARD_LABELS",
        "PROSPECTIVE_DECISION",
        "LTIA_MODEL_COMPARISON",
        "MNB_SEMANTIC_AUDIT",
    ]
    assert report.analytical_full_validation_count == 1
    assert all(item.status == "PASS" for item in report.workflows)
    assert (
        report.legacy_provenance.model_projection_fingerprint
        == report.analytical_provenance.model_projection_fingerprint
    )


def test_shadow_run_requires_every_workflow_and_rejects_stale_allowlist(
    tmp_path: Path,
) -> None:
    legacy, analytical = _admitted_sources(tmp_path)
    with pytest.raises(ShadowComparisonError, match="workflow set mismatch"):
        run_shadow_comparison(
            legacy_repository=legacy,
            analytical_repository=analytical,
            workflows=(ShadowWorkflow("ADVISOR_CURRENT", lambda context: None),),
        )

    workflows = tuple(
        ShadowWorkflow(
            name,
            lambda context: context.model.observation_dates(),
            expected_differences=(
                ExpectedDifference("$.stale", "legacy", "analytical", "stale test"),
            )
            if name == "ADVISOR_CURRENT"
            else (),
        )
        for name in (
            "ADVISOR_CURRENT",
            "ADVISOR_TEMPORAL",
            "STRICT_BACKTEST_COVERAGE",
            "FEATURE_DATASET",
            "FORWARD_LABELS",
            "PROSPECTIVE_DECISION",
            "LTIA_MODEL_COMPARISON",
            "MNB_SEMANTIC_AUDIT",
        )
    )
    with pytest.raises(ShadowComparisonError, match="stale expected"):
        run_shadow_comparison(
            legacy_repository=legacy,
            analytical_repository=analytical,
            workflows=workflows,
        )


def test_shadow_run_records_a_concrete_blocker_as_partial(tmp_path: Path) -> None:
    legacy, analytical = _admitted_sources(tmp_path)
    workflows = tuple(
        ShadowWorkflow(
            name,
            blocker="synthetic retained coverage window is unavailable",
        )
        if name == "FORWARD_LABELS"
        else ShadowWorkflow(name, lambda context: context.model.observation_dates())
        for name in (
            "ADVISOR_CURRENT",
            "ADVISOR_TEMPORAL",
            "STRICT_BACKTEST_COVERAGE",
            "FEATURE_DATASET",
            "FORWARD_LABELS",
            "PROSPECTIVE_DECISION",
            "LTIA_MODEL_COMPARISON",
            "MNB_SEMANTIC_AUDIT",
        )
    )

    report = run_shadow_comparison(
        legacy_repository=legacy,
        analytical_repository=analytical,
        workflows=workflows,
    )

    assert report.status == "PARTIAL"
    blocked = next(item for item in report.workflows if item.name == "FORWARD_LABELS")
    assert blocked.status == "BLOCKED"
    assert blocked.blocker == "synthetic retained coverage window is unavailable"


def test_shadow_session_rejects_changed_database_state(tmp_path: Path) -> None:
    _legacy, analytical = _admitted_sources(tmp_path)
    with (
        pytest.raises(ModelPortfolioPhase1Error, match="changed"),
        analytical.validated_session() as session,
    ):
        assert session.observation_dates() == (FIXTURE_DATE,)
        connection = session._checked_connection()
        connection.execute("PRAGMA query_only=OFF")
        connection.execute("CREATE TEMP TABLE injected(value INTEGER)")
        connection.execute("INSERT INTO injected VALUES(1)")
        session.observation_dates()


def test_optional_nav_source_is_explicit_without_changing_legacy_default(
    tmp_path: Path,
) -> None:
    legacy, _analytical = _admitted_sources(tmp_path)
    with sqlite3.connect(legacy.database_path) as connection:
        connection.execute(
            "CREATE TABLE portfolio_nav_history("
            '"Date" TEXT, "Portfolio Name" TEXT, "Net Asset Value" REAL)'
        )
        connection.executemany(
            "INSERT INTO portfolio_nav_history VALUES(?,?,?)",
            [
                ("2024-09-17", "PB Konzervatív USD", 100.0),
                ("2024-12-16", "PB Konzervatív USD", 101.0),
            ],
        )

    compatible = HistoricalPortfolioRepository(legacy)
    model_only = HistoricalPortfolioRepository(legacy, None)
    window = ForwardWindow.build(FIXTURE_DATE, 90)

    assert compatible.nav_history_available() is True
    assert compatible.nav_series("PB Konzervatív USD", window) is not None
    assert model_only.nav_history_available() is False
    assert model_only.nav_series("PB Konzervatív USD", window) is None
