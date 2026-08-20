from __future__ import annotations

import json
import sqlite3
import subprocess
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import cast

import pytest

from portfolio_advisor.advisor.service import CapitalPreservationAdvisor
from portfolio_advisor.database.repository import ModelPortfolioRepository
from portfolio_advisor.history.official_portfolio_performance import (
    OfficialPortfolioPerformanceObservation,
    OfficialPortfolioPerformanceStore,
)
from portfolio_advisor.history.portfolio_nav_reconstruction_freeze import (
    PortfolioNavReconstructionFrozenError,
    assert_reconstruction_allowed,
)
from portfolio_advisor.prospective import due_schedule_installation
from portfolio_advisor.prospective.due_monitoring import (
    DUE_UNASSESSED,
    NOT_YET_DUE,
    build_prospective_outcome_due_monitoring,
)
from portfolio_advisor.prospective.due_scheduling import (
    LAUNCHD_JOB_IDENTIFIER,
    build_prospective_outcome_due_schedule,
    write_prospective_outcome_due_schedule,
)
from portfolio_advisor.prospective.validation import (
    AVAILABLE_OFFICIAL,
    LIVE_RECORD,
    RESEARCH_BACKFILL,
    ProspectiveOutcome,
    ProspectiveValidationError,
    ProspectiveValidationStore,
    build_prospective_decision,
    build_prospective_validation_audit,
)

ROOT = Path(__file__).resolve().parents[1]


def _draft(
    *,
    decision_id: str = "decision-1",
    decision_date: str = "2026-01-05",
    record_type: str = LIVE_RECORD,
    policy_version: str = "1.0.1",
    policy_fingerprint: str = "policy-a",
) -> dict[str, object]:
    return {
        "record_schema_version": 1,
        "pipeline_version": "1.0.0",
        "record_type": record_type,
        "lifecycle_status": "DRAFT",
        "decision_id": decision_id,
        "decision_identity_fingerprint": f"identity-{decision_id}",
        "decision_date": decision_date,
        "information_date": decision_date,
        "portfolio_universe_id": "universe-a",
        "portfolio_universe_fingerprint": "universe-a",
        "policy_id": "CAPITAL_PRESERVATION_RANKING_POLICY",
        "policy_version": policy_version,
        "policy_fingerprint": policy_fingerprint,
        "candidate_count": 2,
        "eligible_candidate_count": 2,
        "rejected_candidate_count": 0,
        "selected_portfolio_id": "Portfolio A",
        "selected_portfolio_name": "Portfolio A",
        "selected_rank": 1,
        "selected_score": 0.8,
        "full_candidate_ranking": [
            {"portfolio_id": "Portfolio A", "rank": 1, "total_score": 0.8, "ranking_eligible": True},
            {"portfolio_id": "Portfolio B", "rank": 2, "total_score": 0.5, "ranking_eligible": True},
        ],
        "candidate_scores": {"Portfolio A": 0.8, "Portfolio B": 0.5},
        "candidate_feature_values": {"Portfolio A": {}, "Portfolio B": {}},
        "candidate_normalized_values": {"Portfolio A": {}, "Portfolio B": {}},
        "candidate_weighted_contributions": {"Portfolio A": {}, "Portfolio B": {}},
        "portfolio_composition": {"Portfolio A": [], "Portfolio B": []},
        "constituent_isins": {"Portfolio A": [], "Portfolio B": []},
        "constituent_weights": {"Portfolio A": [], "Portfolio B": []},
        "portfolio_currency": {"Portfolio A": "HUF", "Portfolio B": "HUF"},
        "strict_eligibility_result": {
            "status": "PENDING_FUTURE_SOURCE_EVIDENCE",
            "evaluated_at_decision_time": False,
        },
        "strict_eligibility_fingerprint": "strict-a",
        "blocking_isins": [],
        "blocking_categories": [],
        "source_evidence_state": {},
        "point_in_time_dataset_fingerprint": "point-in-time-a",
        "source_evidence_fingerprint": "source-a",
        "graphify_knowledge_fingerprint": "graph-a",
        "graphify_knowledge_ids": [],
        "graphify_source_document_ids": [],
        "graphify_knowledge_categories": [],
        "graphify_constraints_used": [],
        "graphify_warnings": [],
        "graphify_knowledge_available_at_decision": [],
        "point_in_time_guard": {"result": "NO_LOOKAHEAD"},
        "outcome_contract": {},
    }


def _outcome(decision_id: str, horizon_days: int = 90) -> ProspectiveOutcome:
    return ProspectiveOutcome(
        decision_id=decision_id,
        horizon_days=horizon_days,
        portfolio_id="Portfolio A",
        observation_information_date=date(2026, 4, 6),
        source_type="DIRECT_OFFICIAL_PORTFOLIO_NAV",
        source_provider="Official Provider",
        source_identifier="portfolio-a",
        source_reference="provider-export-1",
        local_artifact="data/portfolio_performance/raw/provider/portfolio-a.csv",
        sha256_or_fingerprint="a" * 64,
        currency="HUF",
        value_semantics="PORTFOLIO_NAV",
        metrics={"forward_return": 0.02, "forward_volatility": 0.04, "forward_mdd": -0.01},
    )


def _freeze(tmp_path: Path) -> Path:
    path = tmp_path / "freeze.json"
    path.write_text(
        json.dumps({"validation_status": "PORTFOLIO_NAV_RECONSTRUCTION_FROZEN_UNRESOLVED"}), encoding="utf-8"
    )
    return path


def test_finalized_decision_is_append_only_and_idempotent(tmp_path: Path) -> None:
    store = ProspectiveValidationStore(tmp_path / "prospective.sqlite")
    draft = _draft()

    assert store.finalize(draft)
    assert not store.finalize(draft)
    changed = _draft()
    changed["full_candidate_ranking"] = [
        {"portfolio_id": "Portfolio A", "rank": 1, "total_score": 0.9, "ranking_eligible": True},
        {"portfolio_id": "Portfolio B", "rank": 2, "total_score": 0.5, "ranking_eligible": True},
    ]
    with pytest.raises(ProspectiveValidationError, match="conflicts"):
        store.finalize(changed)

    rows = store.rows("SELECT lifecycle_status FROM prospective_decisions")
    assert [row["lifecycle_status"] for row in rows] == ["FINALIZED"]
    slots = store.rows("SELECT horizon_days, expected_start_date, expected_end_date, status FROM prospective_outcome_slots")
    assert [(row["horizon_days"], row["expected_start_date"], row["expected_end_date"], row["status"]) for row in slots] == [
        (90, "2026-01-05", "2026-04-05", "PENDING"),
        (180, "2026-01-05", "2026-07-04", "PENDING"),
        (365, "2026-01-05", "2027-01-05", "PENDING"),
    ]


def test_outcome_admission_is_due_direct_and_idempotent(tmp_path: Path) -> None:
    store = ProspectiveValidationStore(tmp_path / "prospective.sqlite")
    store.finalize(_draft())
    official = _outcome("decision-1")

    with pytest.raises(ProspectiveValidationError, match="not yet due"):
        store.admit_outcome(official, current_date=date(2026, 4, 4))
    assert store.admit_outcome(official, current_date=date(2026, 4, 5))
    assert not store.admit_outcome(official, current_date=date(2026, 4, 5))
    status = store.rows("SELECT status FROM prospective_outcome_slots WHERE horizon_days = 90")[0]["status"]
    assert status == AVAILABLE_OFFICIAL
    payload = json.loads(store.rows("SELECT outcome_json FROM prospective_outcomes")[0]["outcome_json"])
    assert (payload["observation_start"], payload["observation_end"]) == ("2026-01-05", "2026-04-05")

    with pytest.raises(ProspectiveValidationError, match="approved direct portfolio channel"):
        replace(official, source_type="CONSTITUENT_AGGREGATION")
    with pytest.raises(ProspectiveValidationError, match="approved direct portfolio channel"):
        replace(official, source_type="GRAPHIFY_INFERENCE")
    with pytest.raises(ProspectiveValidationError, match="approved for a direct portfolio outcome"):
        replace(official, value_semantics="UNPROVEN_MANUAL_RETURN")
    with pytest.raises(ProspectiveValidationError, match="maximum drawdown"):
        replace(official, metrics={"forward_mdd": 0.01})


def test_due_unavailable_outcome_has_no_numeric_label_and_is_idempotent(tmp_path: Path) -> None:
    store = ProspectiveValidationStore(tmp_path / "prospective.sqlite")
    store.finalize(_draft())
    assert store.mark_outcome_unavailable(
        decision_id="decision-1",
        horizon_days=90,
        status="UNAVAILABLE_NO_SOURCE",
        source_reference="data/audit/official_portfolio_performance_source_research.json",
        current_date=date(2026, 4, 5),
    )
    assert not store.mark_outcome_unavailable(
        decision_id="decision-1",
        horizon_days=90,
        status="UNAVAILABLE_NO_SOURCE",
        source_reference="data/audit/official_portfolio_performance_source_research.json",
        current_date=date(2026, 4, 5),
    )
    payload = json.loads(store.rows("SELECT outcome_json FROM prospective_outcomes")[0]["outcome_json"])
    assert payload["metrics"] == {}
    assert payload["numeric_label_present"] is False
    assert payload["reason"]
    assert payload["assessed_at"] == "2026-04-05"


def test_unavailable_outcome_can_be_reopened_only_by_new_qualifying_official_evidence(tmp_path: Path) -> None:
    store = ProspectiveValidationStore(tmp_path / "prospective.sqlite")
    store.finalize(_draft())
    assert store.mark_outcome_unavailable(
        decision_id="decision-1",
        horizon_days=90,
        status="UNAVAILABLE_NO_SOURCE",
        source_reference="data/audit/research.json",
        current_date=date(2026, 4, 5),
        reason="No retained direct official source was available when due.",
    )
    assert store.admit_outcome(_outcome("decision-1"), current_date=date(2026, 4, 5))
    assert not store.admit_outcome(_outcome("decision-1"), current_date=date(2026, 4, 5))
    assert store.rows("SELECT status FROM prospective_outcome_slots WHERE horizon_days = 90")[0]["status"] == AVAILABLE_OFFICIAL
    assessments = store.rows("SELECT outcome_json FROM prospective_outcomes")
    assert len(assessments) == 1
    assert json.loads(assessments[0]["outcome_json"])["metrics"] == {}
    events = store.rows(
        "SELECT previous_status, new_status FROM prospective_outcome_events ORDER BY rowid"
    )
    assert [(row["previous_status"], row["new_status"]) for row in events] == [
        ("PENDING", "UNAVAILABLE_NO_SOURCE"),
        ("UNAVAILABLE_NO_SOURCE", AVAILABLE_OFFICIAL),
    ]


def test_due_monitor_only_classifies_live_slots_and_is_deterministic(tmp_path: Path) -> None:
    store = ProspectiveValidationStore(tmp_path / "database" / "prospective.sqlite")
    store.finalize(_draft(decision_id="research", record_type=RESEARCH_BACKFILL))
    store.finalize(_draft(decision_id="live", record_type=LIVE_RECORD))
    freeze = _freeze(tmp_path)
    direct_store = tmp_path / "database" / "official_portfolio_performance.sqlite"

    before = build_prospective_outcome_due_monitoring(
        store=store,
        repository_root=tmp_path,
        freeze_path=freeze,
        direct_performance_store_path=direct_store,
        as_of_date=date(2026, 4, 4),
    )
    same = build_prospective_outcome_due_monitoring(
        store=store,
        repository_root=tmp_path,
        freeze_path=freeze,
        direct_performance_store_path=direct_store,
        as_of_date=date(2026, 4, 4),
    )
    assert before == same
    assert before["live_decision_count"] == 1
    assert before["research_backfill_count"] == 1
    assert [item["temporal_status"] for item in cast(list[dict[str, object]], before["slots"])] == [
        NOT_YET_DUE,
        NOT_YET_DUE,
        NOT_YET_DUE,
    ]
    assert before["next_due_date"] == "2026-04-05"
    assert before["research_backfill_monitored"] is False

    due = build_prospective_outcome_due_monitoring(
        store=store,
        repository_root=tmp_path,
        freeze_path=freeze,
        direct_performance_store_path=direct_store,
        as_of_date=date(2026, 4, 5),
    )
    slots = cast(list[dict[str, object]], due["slots"])
    assert [item["temporal_status"] for item in slots] == [DUE_UNASSESSED, NOT_YET_DUE, NOT_YET_DUE]
    assert due["due_unassessed_count"] == 1
    assert due["source_acquisition_required_count"] == 1
    assert store.rows("SELECT status FROM prospective_outcome_slots WHERE decision_id = ?", ("live",))[0]["status"] == "PENDING"

    official_store = OfficialPortfolioPerformanceStore(direct_store)
    official_store.persist(
        (
            OfficialPortfolioPerformanceObservation(
                portfolio_id="Portfolio A",
                observation_date=date(2026, 1, 5),
                value=100.0,
                currency="HUF",
                value_type="PORTFOLIO_NAV",
                source_provider="Official Provider",
                source_identifier="portfolio-a",
                provenance_reference="data/portfolio_performance/raw/provider/portfolio-a.csv",
            ),
            OfficialPortfolioPerformanceObservation(
                portfolio_id="Portfolio A",
                observation_date=date(2026, 4, 5),
                value=102.0,
                currency="HUF",
                value_type="PORTFOLIO_NAV",
                source_provider="Official Provider",
                source_identifier="portfolio-a",
                provenance_reference="data/portfolio_performance/raw/provider/portfolio-a.csv",
            ),
        )
    )
    retained = build_prospective_outcome_due_monitoring(
        store=store,
        repository_root=tmp_path,
        freeze_path=freeze,
        direct_performance_store_path=direct_store,
        as_of_date=date(2026, 4, 5),
    )
    retained_slot = cast(list[dict[str, object]], retained["slots"])[0]
    assert retained_slot["temporal_status"] == DUE_UNASSESSED
    assert retained_slot["local_direct_source_state"] == "LOCAL_DIRECT_SOURCE_EXACT_BOUNDARIES_FOUND_REQUIRES_EXPLICIT_ADMISSION"
    assert retained["available_official_count"] == 0


def test_due_schedule_selects_one_earliest_live_pending_slot_and_is_idempotent(tmp_path: Path) -> None:
    store = ProspectiveValidationStore(tmp_path / "database" / "prospective.sqlite")
    store.finalize(_draft(decision_id="research", decision_date="2025-12-01", record_type=RESEARCH_BACKFILL))
    store.finalize(_draft(decision_id="z-live", decision_date="2026-01-05"))
    store.finalize(_draft(decision_id="a-live", decision_date="2026-01-05"))
    freeze = _freeze(tmp_path)

    first = build_prospective_outcome_due_schedule(
        store=store, repository_root=tmp_path, freeze_path=freeze, as_of_date=date(2026, 1, 1)
    )
    second = build_prospective_outcome_due_schedule(
        store=store, repository_root=tmp_path, freeze_path=freeze, as_of_date=date(2026, 1, 1)
    )
    assert first == second
    assert first["next_due_decision_id"] == "a-live"
    assert first["next_due_horizon"] == 90
    assert first["next_due_date"] == "2026-04-05"
    assert first["current_slot_status"] == "PENDING"
    assert first["job_identifier"] == LAUNCHD_JOB_IDENTIFIER
    assert first["schedule_status"] == "PROSPECTIVE_OUTCOME_DUE_SCHEDULE_VALIDATED_WITH_CAVEATS"
    assert first["research_backfills_excluded"] is True
    assert "admit_prospective" not in str(first["monitor_command"])
    assert "acquire" not in str(first["monitor_command"])

    artifact = tmp_path / "data" / "audit" / "due_schedule.json"
    template = tmp_path / "ops" / "launchd" / "schedule.plist"
    write_prospective_outcome_due_schedule(artifact_path=artifact, template_path=template, schedule=first)
    first_template = template.read_text(encoding="utf-8")
    write_prospective_outcome_due_schedule(artifact_path=artifact, template_path=template, schedule=second)
    assert template.read_text(encoding="utf-8") == first_template
    assert "<integer>5</integer>" in first_template
    assert "<integer>9</integer>" in first_template
    assert "__PROJECT_ROOT__" in first_template


def test_due_schedule_ignores_closed_slots_and_blocks_rolling_schedule_when_due(tmp_path: Path) -> None:
    store = ProspectiveValidationStore(tmp_path / "database" / "prospective.sqlite")
    store.finalize(_draft())
    freeze = _freeze(tmp_path)
    initial = build_prospective_outcome_due_schedule(
        store=store, repository_root=tmp_path, freeze_path=freeze, as_of_date=date(2026, 1, 1)
    )
    assert store.mark_outcome_unavailable(
        decision_id="decision-1",
        horizon_days=90,
        status="UNAVAILABLE_NO_SOURCE",
        source_reference="data/audit/research.json",
        current_date=date(2026, 4, 5),
    )
    changed = build_prospective_outcome_due_schedule(
        store=store, repository_root=tmp_path, freeze_path=freeze, as_of_date=date(2026, 4, 6)
    )
    assert changed["next_due_horizon"] == 180
    assert changed["next_due_date"] == "2026-07-04"
    assert changed["job_identifier"] == LAUNCHD_JOB_IDENTIFIER
    assert changed["fingerprint"] != initial["fingerprint"]

    due = build_prospective_outcome_due_schedule(
        store=store, repository_root=tmp_path, freeze_path=freeze, as_of_date=date(2026, 7, 4)
    )
    assert due["schedule_status"] == "PROSPECTIVE_OUTCOME_DUE_SCHEDULE_NOT_REQUIRED"
    assert due["overdue_monitoring_required"] is True
    assert due["scheduling_blocked_by_due_slot"] is True
    assert due["launchd_template"] is None


def test_due_schedule_has_explicit_no_schedule_state_without_live_pending_slots(tmp_path: Path) -> None:
    store = ProspectiveValidationStore(tmp_path / "database" / "prospective.sqlite")
    store.finalize(_draft())
    freeze = _freeze(tmp_path)
    for horizon in (90, 180, 365):
        assert store.mark_outcome_unavailable(
            decision_id="decision-1",
            horizon_days=horizon,
            status="UNAVAILABLE_NO_SOURCE",
            source_reference="data/audit/research.json",
            current_date=date(2028, 1, 1),
        )
    schedule = build_prospective_outcome_due_schedule(
        store=store, repository_root=tmp_path, freeze_path=freeze, as_of_date=date(2028, 1, 1)
    )
    assert schedule["schedule_status"] == "PROSPECTIVE_OUTCOME_DUE_SCHEDULE_NOT_REQUIRED"
    assert schedule["next_due_date"] is None
    assert schedule["no_future_pending_live_slot"] is True


def test_explicit_current_user_launchd_installation_is_idempotent_and_conflicts_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launch_agents = tmp_path / "Library" / "LaunchAgents"
    launch_agents.mkdir(parents=True)
    monkeypatch.setattr(due_schedule_installation, "LAUNCH_AGENTS_DIRECTORY", launch_agents)
    template = tmp_path / "template.plist"
    template.write_text(
        """<plist><dict>
<key>Label</key><string>com.portfolio_advisor.prospective_outcome_due_check</string>
<key>ProgramArguments</key><array><string>scripts/check_due_prospective_outcomes.py</string><string>scripts/audit_prospective_portfolio_validation.py</string></array>
<key>WorkingDirectory</key><string>__PROJECT_ROOT__</string>
<key>StandardOutPath</key><string>__PROJECT_ROOT__/data/audit/out.log</string>
<key>StandardErrorPath</key><string>__PROJECT_ROOT__/data/audit/error.log</string>
</dict></plist>""",
        encoding="utf-8",
    )
    loaded = False
    calls: list[list[str]] = []

    def runner(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        nonlocal loaded
        calls.append(arguments)
        if arguments[:2] == ["launchctl", "print"]:
            return subprocess.CompletedProcess(arguments, 0 if loaded else 1, "", "")
        if arguments[:2] == ["launchctl", "bootstrap"]:
            loaded = True
        return subprocess.CompletedProcess(arguments, 0, "", "")

    first = due_schedule_installation.install_prospective_due_schedule(
        template_path=template, repository_root=tmp_path, user_id=501, runner=runner
    )
    assert first.status == "INSTALLED_AND_ENABLED"
    assert first.loaded and first.enabled
    target = launch_agents / "com.portfolio_advisor.prospective_outcome_due_check.plist"
    assert target.is_file()
    assert "__PROJECT_ROOT__" not in target.read_text(encoding="utf-8")
    assert any(item[:2] == ["launchctl", "bootstrap"] for item in calls)
    assert any(item[:2] == ["launchctl", "enable"] for item in calls)

    calls.clear()
    second = due_schedule_installation.install_prospective_due_schedule(
        template_path=template, repository_root=tmp_path, user_id=501, runner=runner
    )
    assert second.status == "ALREADY_INSTALLED_IDENTICAL"
    assert not any(item[:2] == ["launchctl", "bootstrap"] for item in calls)

    target.write_text(
        "<?xml version=\"1.0\"?><plist version=\"1.0\"><dict><key>Label</key><string>different</string></dict></plist>",
        encoding="utf-8",
    )
    with pytest.raises(ProspectiveValidationError, match="differs"):
        due_schedule_installation.install_prospective_due_schedule(
            template_path=template, repository_root=tmp_path, user_id=501, runner=runner
        )


def test_future_information_and_missing_or_synthetic_evidence_fail_closed(tmp_path: Path) -> None:
    store = ProspectiveValidationStore(tmp_path / "prospective.sqlite")
    future = _draft()
    future["information_date"] = "2026-01-06"
    with pytest.raises(ProspectiveValidationError, match="future information"):
        store.finalize(future)

    with pytest.raises(ProspectiveValidationError, match="requires at least one"):
        replace(_outcome("decision-1"), metrics={})


def test_outcome_identity_currency_and_research_backfill_fail_closed(tmp_path: Path) -> None:
    store = ProspectiveValidationStore(tmp_path / "prospective.sqlite")
    store.finalize(_draft())
    official = _outcome("decision-1")
    with pytest.raises(ProspectiveValidationError, match="portfolio identity"):
        store.admit_outcome(replace(official, portfolio_id="Portfolio B"), current_date=date(2026, 4, 5))
    with pytest.raises(ProspectiveValidationError, match="currency"):
        store.admit_outcome(replace(official, currency="EUR"), current_date=date(2026, 4, 5))

    store.finalize(_draft(decision_id="research", record_type=RESEARCH_BACKFILL))
    with pytest.raises(ProspectiveValidationError, match="research backfills"):
        store.admit_outcome(replace(official, decision_id="research"), current_date=date(2026, 4, 5))


def test_research_backfill_is_excluded_from_live_readiness_and_audit_is_deterministic(tmp_path: Path) -> None:
    store = ProspectiveValidationStore(tmp_path / "database" / "prospective.sqlite")
    store.finalize(_draft(record_type=RESEARCH_BACKFILL))
    freeze = _freeze(tmp_path)

    first = build_prospective_validation_audit(store=store, repository_root=tmp_path, freeze_path=freeze)
    second = build_prospective_validation_audit(store=store, repository_root=tmp_path, freeze_path=freeze)

    assert first == second
    assert first["research_backfill_decision_count"] == 1
    assert first["live_prospective_decision_count"] == 0
    assert first["prospective_validation_readiness"] == "PROSPECTIVE_VALIDATION_NOT_READY"
    assert str(tmp_path) not in json.dumps(first)


def test_policy_versions_remain_separate_and_amendments_do_not_overwrite(tmp_path: Path) -> None:
    store = ProspectiveValidationStore(tmp_path / "database" / "prospective.sqlite")
    store.finalize(_draft(decision_id="v101", policy_version="1.0.1", policy_fingerprint="policy-101"))
    store.finalize(_draft(decision_id="v102", policy_version="1.0.2", policy_fingerprint="policy-102"))
    assert store.append_amendment(
        original_decision_id="v101",
        amendment_id="amendment-1",
        reason="Corrected source metadata reference",
        evidence={"artifact": "data/audit/correction.json"},
        affected_fields=("source_evidence_state",),
        effective_date=date(2026, 1, 7),
    )
    assert not store.append_amendment(
        original_decision_id="v101",
        amendment_id="amendment-1",
        reason="Corrected source metadata reference",
        evidence={"artifact": "data/audit/correction.json"},
        affected_fields=("source_evidence_state",),
        effective_date=date(2026, 1, 7),
    )
    assert len(store.rows("SELECT * FROM prospective_decisions")) == 2
    assert len(store.rows("SELECT * FROM prospective_decision_amendments")) == 1


def _database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        '''CREATE TABLE model_portfolios (
            "Date" TEXT, "Portfolio Name" TEXT, "Product" TEXT, "ISIN" TEXT,
            "Allocation (%)" REAL, "Currency" TEXT, "Currency Risk" TEXT,
            "1 Year" REAL, "1Y Sharpe Ratio" REAL, "1Y Volatility" REAL,
            "Downside Risk" REAL, "Maximum Drawdown" REAL
        )'''
    )
    connection.executemany(
        'INSERT INTO model_portfolios VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        [
            ("2026/08/20", "A", "Fund A", "ISIN-A", 100.0, "HUF", "Hedged", 0.02, 0.5, 0.02, 0.01, -0.02),
            ("2026/08/20", "B", "Fund B", "ISIN-B", 100.0, "HUF", "Unhedged", 0.04, 0.2, 0.08, 0.03, -0.10),
        ],
    )
    connection.commit()
    connection.close()


def test_builder_captures_full_ranking_and_point_in_time_composition(tmp_path: Path) -> None:
    database = tmp_path / "model.sqlite"
    _database(database)
    rules = ROOT / "data/knowledge/validated_rules/capital_preservation_ranking.yaml"
    result = CapitalPreservationAdvisor(ModelPortfolioRepository(database), rules).evaluate(
        observation_date=date(2026, 8, 20), alternative_count=100
    )
    record = build_prospective_decision(
        advisor_result=result,
        repository=ModelPortfolioRepository(database),
        rules_path=rules,
        graph_path=ROOT / "data/knowledge/graphify-out/graph.json",
        repository_root=tmp_path,
        record_type=LIVE_RECORD,
    )

    assert record["selected_portfolio_id"] == "A"
    ranking = cast(list[dict[str, object]], record["full_candidate_ranking"])
    composition = cast(dict[str, list[dict[str, object]]], record["portfolio_composition"])
    strict = cast(dict[str, object], record["strict_eligibility_result"])
    guard = cast(dict[str, object], record["point_in_time_guard"])
    assert [item["portfolio_id"] for item in ranking] == ["A", "B"]
    assert composition["A"][0]["isin"] == "ISIN-A"
    assert strict["status"] == "PENDING_FUTURE_SOURCE_EVIDENCE"
    assert guard["result"] == "NO_LOOKAHEAD"


def test_latest_snapshot_creates_a_distinct_live_record_and_identical_replay_is_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "model.sqlite"
    _database(database)
    repository = ModelPortfolioRepository(database)
    rules = ROOT / "data/knowledge/validated_rules/capital_preservation_ranking.yaml"
    result = CapitalPreservationAdvisor(repository, rules).evaluate(
        observation_date=repository.latest_observation_date(), alternative_count=100
    )
    research = build_prospective_decision(
        advisor_result=result,
        repository=repository,
        rules_path=rules,
        graph_path=ROOT / "data/knowledge/graphify-out/graph.json",
        repository_root=tmp_path,
        record_type=RESEARCH_BACKFILL,
    )
    live = build_prospective_decision(
        advisor_result=result,
        repository=repository,
        rules_path=rules,
        graph_path=ROOT / "data/knowledge/graphify-out/graph.json",
        repository_root=tmp_path,
        record_type=LIVE_RECORD,
    )
    assert live["decision_id"] != research["decision_id"]
    assert live["decision_identity_fingerprint"] == research["decision_identity_fingerprint"]
    assert live["strict_eligibility_fingerprint"]
    assert live["policy_fingerprint"]
    assert live["graphify_knowledge_fingerprint"]

    store = ProspectiveValidationStore(tmp_path / "prospective.sqlite")
    assert store.finalize(live)
    assert not store.finalize(live)
    assert len(store.rows("SELECT * FROM prospective_decisions")) == 1
    assert len(store.rows("SELECT * FROM prospective_candidates")) == 2


def test_historical_snapshot_cannot_be_silently_classified_as_live(tmp_path: Path) -> None:
    database = tmp_path / "model.sqlite"
    _database(database)
    connection = sqlite3.connect(database)
    connection.execute(
        'INSERT INTO model_portfolios VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        ("2026/08/19", "Old", "Old Fund", "OLD", 100.0, "HUF", "Hedged", 0.02, 0.5, 0.02, 0.01, -0.02),
    )
    connection.commit()
    connection.close()
    repository = ModelPortfolioRepository(database)
    rules = ROOT / "data/knowledge/validated_rules/capital_preservation_ranking.yaml"
    historical = CapitalPreservationAdvisor(repository, rules).evaluate(
        observation_date=date(2026, 8, 19), alternative_count=100
    )
    with pytest.raises(ProspectiveValidationError, match="latest canonical"):
        build_prospective_decision(
            advisor_result=historical,
            repository=repository,
            rules_path=rules,
            graph_path=ROOT / "data/knowledge/graphify-out/graph.json",
            repository_root=tmp_path,
            record_type=LIVE_RECORD,
        )
    assert build_prospective_decision(
        advisor_result=historical,
        repository=repository,
        rules_path=rules,
        graph_path=ROOT / "data/knowledge/graphify-out/graph.json",
        repository_root=tmp_path,
        record_type=RESEARCH_BACKFILL,
    )["record_type"] == RESEARCH_BACKFILL


def test_future_graphify_knowledge_is_excluded_and_later_changes_do_not_rewrite_record(tmp_path: Path) -> None:
    database = tmp_path / "model.sqlite"
    _database(database)
    graph = tmp_path / "graph.json"
    graph.write_text(
        json.dumps(
            {
                "nodes": [
                    {
                        "id": "mdd",
                        "label": "Maximum Drawdown (MDD)",
                        "source_file": "methodology.md",
                    },
                    {
                        "id": "future-fact",
                        "label": "Future portfolio fact",
                        "source_file": "future.md",
                        "knowledge_category": "POINT_IN_TIME_FACT",
                        "valid_from": "2027-01-01",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    repository = ModelPortfolioRepository(database)
    result = CapitalPreservationAdvisor(
        repository, ROOT / "data/knowledge/validated_rules/capital_preservation_ranking.yaml"
    ).evaluate(observation_date=date(2026, 8, 20), alternative_count=100)
    record = build_prospective_decision(
        advisor_result=result,
        repository=repository,
        rules_path=ROOT / "data/knowledge/validated_rules/capital_preservation_ranking.yaml",
        graph_path=graph,
        repository_root=tmp_path,
    )
    knowledge = cast(list[dict[str, object]], record["graphify_knowledge_available_at_decision"])
    assert [item["knowledge_id"] for item in knowledge] == ["MDD_MEASUREMENT_METHODOLOGY"]
    store = ProspectiveValidationStore(tmp_path / "prospective.sqlite")
    store.finalize(record)
    stored = store.rows("SELECT record_json FROM prospective_decisions")[0]["record_json"]
    graph.write_text(json.dumps({"nodes": []}), encoding="utf-8")
    assert store.rows("SELECT record_json FROM prospective_decisions")[0]["record_json"] == stored


def test_freeze_blocks_synthetic_reconstruction_but_not_a_future_direct_source() -> None:
    freeze = {
        "status": "PORTFOLIO_NAV_RECONSTRUCTION_UNRESOLVED",
        "portfolio_nav_generation_allowed": False,
    }
    with pytest.raises(PortfolioNavReconstructionFrozenError):
        assert_reconstruction_allowed(freeze, reconstruction_requested=True)
    assert_reconstruction_allowed(
        freeze,
        reconstruction_requested=True,
        direct_official_portfolio_source=True,
    )
