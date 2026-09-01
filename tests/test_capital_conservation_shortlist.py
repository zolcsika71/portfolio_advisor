"""Behavioral tests for governed capital-conservation shortlist construction."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import date
from hashlib import sha256
from pathlib import Path

import pytest

from portfolio_advisor.construction import (
    ShortlistConstructionError,
    construct_capital_conservation_shortlist,
)
from portfolio_advisor.database.schema.v3 import connect, initialize_schema
from portfolio_advisor.objectives import (
    PolicyActivationStatus,
    PolicyRegistry,
    PortfolioObjective,
    build_default_policy_registry,
)

ROOT = Path(__file__).resolve().parents[1]
POLICY_FINGERPRINT = "d3cc192857459963eab539d93457396756b341ad8941e6c0832cedf7450091ba"
DATASET_FINGERPRINT = "d" * 64


def _build_target(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    target = tmp_path / "schema-v3.sqlite"
    source_specs = (
        ("synthetic_20260101.xls", "a" * 64, "2026-01-01"),
        ("synthetic_20260201.xls", "b" * 64, "2026-02-01"),
    )
    instruments = (
        (1, "US0378331005", "Synthetic Defensive A"),
        (2, "US5949181045", "Synthetic Defensive B"),
    )
    metric_values = {
        1: {
            "RETURN_1Y": 0.08,
            "SHARPE_RATIO_1Y": 0.9,
            "VOLATILITY_1Y": 0.03,
            "DOWNSIDE_RISK": 0.02,
            "MAXIMUM_DRAWDOWN": -0.05,
        },
        2: {
            "RETURN_1Y": 0.10,
            "SHARPE_RATIO_1Y": 1.0,
            "VOLATILITY_1Y": 0.05,
            "DOWNSIDE_RISK": 0.03,
            "MAXIMUM_DRAWDOWN": -0.10,
        },
    }
    fingerprints = {filename: digest for filename, digest, _ in source_specs}
    with connect(target) as connection:
        initialize_schema(connection)
        for instrument_id, isin, name in instruments:
            connection.execute(
                "INSERT INTO instrument(instrument_id, isin, canonical_name) VALUES (?, ?, ?)",
                (instrument_id, isin, name),
            )
        metric_codes = sorted({code for values in metric_values.values() for code in values})
        for metric_id, code in enumerate(metric_codes, start=1):
            connection.execute(
                """INSERT INTO metric_definition(metric_id, metric_code, name, unit, description)
                   VALUES (?, ?, ?, 'decimal', 'Synthetic governed test metric')""",
                (metric_id, code, code),
            )
        metric_ids = {
            str(row["metric_code"]): int(row["metric_id"])
            for row in connection.execute("SELECT metric_id, metric_code FROM metric_definition")
        }
        occurrence_id = membership_id = 0
        for snapshot_id, (filename, digest, snapshot_date) in enumerate(source_specs, start=1):
            connection.execute(
                """INSERT INTO source_file(
                       source_file_id, filename, sha256, source_type, source_date
                   ) VALUES (?, ?, ?, 'SHORTLIST_XLS', ?)""",
                (snapshot_id, filename, digest, snapshot_date),
            )
            connection.execute(
                "INSERT INTO source_sheet(source_sheet_id, source_file_id, sheet_name) VALUES (?, ?, ' shortlist')",
                (snapshot_id, snapshot_id),
            )
            connection.execute(
                """INSERT INTO shortlist_snapshot(
                       shortlist_snapshot_id, snapshot_date, source_sheet_id
                   ) VALUES (?, ?, ?)""",
                (snapshot_id, snapshot_date, snapshot_id),
            )
            for source_row, (instrument_id, isin, name) in enumerate(instruments, start=2):
                occurrence_id += 1
                membership_id += 1
                currency_risk = "Fedezve" if instrument_id == 1 else "Nincs fedezve"
                payload = json.dumps(
                    {
                        "Deviza": "EUR",
                        "Devizakockázat": currency_risk,
                        "ISIN": isin,
                        "Termék": name,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                connection.execute(
                    """INSERT INTO shortlist_entry(
                           shortlist_entry_id, shortlist_snapshot_id, instrument_id,
                           source_row_number, status
                       ) VALUES (?, ?, ?, ?, 'SOURCE_REPORTED')""",
                    (membership_id, snapshot_id, instrument_id, source_row),
                )
                connection.execute(
                    """INSERT INTO shortlist_entry_source_occurrence(
                           shortlist_entry_source_occurrence_id, shortlist_snapshot_id,
                           instrument_id, source_sheet_id, source_row_number,
                           observed_product_name, observed_currency_code,
                           source_payload_json, conflict_status
                       ) VALUES (?, ?, ?, ?, ?, ?, 'EUR', ?, 'SOURCE_REPORTED')""",
                    (
                        occurrence_id,
                        snapshot_id,
                        instrument_id,
                        snapshot_id,
                        source_row,
                        name,
                        payload,
                    ),
                )
                connection.execute(
                    "INSERT INTO shortlist_entry_lineage(shortlist_entry_id, source_occurrence_id) VALUES (?, ?)",
                    (membership_id, occurrence_id),
                )
                for code, value in metric_values[instrument_id].items():
                    source_reference = (
                        f"SHORTLIST:{digest}: shortlist:{source_row}:"
                        + {
                            "RETURN_1Y": "1yr",
                            "SHARPE_RATIO_1Y": "1Y Sharpe",
                            "VOLATILITY_1Y": "1Y Vol.",
                            "DOWNSIDE_RISK": "Down. risk",
                            "MAXIMUM_DRAWDOWN": "Max. drawd.",
                        }[code]
                    )
                    connection.execute(
                        """INSERT INTO instrument_metric_observation(
                               instrument_id, metric_id, observation_date, value,
                               provenance_type, source_file_id, source_reference
                           ) VALUES (?, ?, ?, ?, 'PROVIDER_REPORTED', ?, ?)""",
                        (
                            instrument_id,
                            metric_ids[code],
                            snapshot_date,
                            value,
                            snapshot_id,
                            source_reference,
                        ),
                    )
        connection.execute(
            """INSERT INTO shortlist_stage_manifest(
                   singleton, integration_version, workbook_fingerprints_json,
                   header_signature, source_occurrence_count, snapshot_count,
                   membership_count, lineage_count, instrument_count, alias_count,
                   metric_observation_count, multi_occurrence_count,
                   conflict_occurrence_count, dataset_fingerprint, completion_status
               ) VALUES (1, 'MILESTONE_9_SHORTLIST_V1', ?, 'synthetic-signature',
                         4, 2, 4, 4, 2, 0, 20, 0, 0, ?, 'COMPLETE')""",
            (json.dumps(fingerprints, sort_keys=True), DATASET_FINGERPRINT),
        )
        connection.commit()
    return target, fingerprints


def _construct(
    target: Path,
    fingerprints: dict[str, str],
    *,
    objective: PortfolioObjective | str = PortfolioObjective.CAPITAL_CONSERVATION,
    as_of: date | None = None,
    limit: int | None = None,
    registry: PolicyRegistry | None = None,
):
    return construct_capital_conservation_shortlist(
        database_path=target,
        repository_root=ROOT,
        expected_workbook_fingerprints=fingerprints,
        expected_manifest_fingerprint=DATASET_FINGERPRINT,
        objective=objective,
        as_of=as_of,
        limit=limit,
        registry=registry,
    )


def _hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def test_success_is_governed_deterministic_ranked_and_read_only(tmp_path: Path) -> None:
    target, fingerprints = _build_target(tmp_path)
    before_database = _hash(target)
    before_policy = _hash(ROOT / "data/knowledge/validated_rules/capital_preservation_ranking.yaml")

    first = _construct(target, fingerprints)
    second = _construct(target, dict(reversed(tuple(fingerprints.items()))))

    assert first.canonical_json() == second.canonical_json()
    assert first.result_fingerprint == second.result_fingerprint
    assert first.provenance.snapshot_date == date(2026, 2, 1)
    assert first.provenance.objective == "capital_conservation"
    assert first.provenance.strategy == "CAPITAL_DEFENSIVE"
    assert first.provenance.construction_capability == (
        "INTERMEDIATE_INSTRUMENT_SCREENING_NOT_PORTFOLIO_CONSTRUCTION"
    )
    assert first.provenance.policy_id == "CAPITAL_PRESERVATION_RANKING_POLICY"
    assert first.provenance.policy_version == "1.0.1"
    assert first.provenance.policy_fingerprint == POLICY_FINGERPRINT
    assert dict(first.provenance.capability_states) == {
        "constructed_portfolio_runtime": "NOT_IMPLEMENTED",
        "construction_policy": "AVAILABLE_REVIEWED",
        "eligibility": "AVAILABLE_REVIEWED",
        "finalist_comparison": "NOT_IMPLEMENTED",
        "instrument_screening_ranking": "AVAILABLE_REVIEWED",
        "outcome_success_criteria": "NOT_IMPLEMENTED",
    }
    assert [item.isin for item in first.constructed] == ["US0378331005", "US5949181045"]
    assert [item.rank for item in first.constructed] == [1, 2]
    assert all(len(item.lineage.source_occurrence_ids) == 1 for item in first.candidates)
    assert first.allocation_status == first.cash_deployment_status == first.fx_conversion_status
    assert first.allocation_status.value == "NOT_PERFORMED"
    assert _hash(target) == before_database
    assert _hash(ROOT / "data/knowledge/validated_rules/capital_preservation_ranking.yaml") == before_policy


def test_temporal_as_of_and_explicit_limit_have_no_future_leakage(tmp_path: Path) -> None:
    target, fingerprints = _build_target(tmp_path)
    historical = _construct(target, fingerprints, as_of=date(2026, 1, 15), limit=1)
    assert historical.provenance.snapshot_date == date(2026, 1, 1)
    assert len(historical.constructed) == 1
    with pytest.raises(ShortlistConstructionError, match="positive integer"):
        _construct(target, fingerprints, limit=0)
    with pytest.raises(ShortlistConstructionError, match="exceeds eligible"):
        _construct(target, fingerprints, limit=3)
    with pytest.raises(ShortlistConstructionError, match="no complete shortlist snapshot"):
        _construct(target, fingerprints, as_of=date(2025, 12, 31))


def test_existing_tie_breaker_orders_equal_candidates_by_stable_isin(tmp_path: Path) -> None:
    target, fingerprints = _build_target(tmp_path)
    with sqlite3.connect(target) as connection:
        for code in (
            "RETURN_1Y",
            "SHARPE_RATIO_1Y",
            "VOLATILITY_1Y",
            "DOWNSIDE_RISK",
            "MAXIMUM_DRAWDOWN",
        ):
            connection.execute(
                """UPDATE instrument_metric_observation
                   SET value=(
                       SELECT source.value FROM instrument_metric_observation source
                       JOIN metric_definition source_definition
                         ON source_definition.metric_id=source.metric_id
                       WHERE source.instrument_id=1
                         AND source.observation_date='2026-02-01'
                         AND source_definition.metric_code=?
                   )
                   WHERE instrument_id=2 AND observation_date='2026-02-01'
                     AND metric_id=(SELECT metric_id FROM metric_definition WHERE metric_code=?)""",
                (code, code),
            )
        row = connection.execute(
            """SELECT shortlist_entry_source_occurrence_id, source_payload_json
               FROM shortlist_entry_source_occurrence
               WHERE shortlist_snapshot_id=2 AND instrument_id=2"""
        ).fetchone()
        payload = json.loads(str(row[1]))
        payload["Devizakockázat"] = "Fedezve"
        connection.execute(
            """UPDATE shortlist_entry_source_occurrence SET source_payload_json=?
               WHERE shortlist_entry_source_occurrence_id=?""",
            (json.dumps(payload, ensure_ascii=False, sort_keys=True), int(row[0])),
        )
        connection.commit()
    result = _construct(target, fingerprints)
    assert [item.isin for item in result.constructed] == ["US0378331005", "US5949181045"]
    assert result.constructed[0].total_score == result.constructed[1].total_score


def test_unknown_and_dividend_objectives_fail_without_fallback(tmp_path: Path) -> None:
    target, fingerprints = _build_target(tmp_path)
    with pytest.raises(ShortlistConstructionError, match="objective or policy resolution failed"):
        _construct(target, fingerprints, objective="unknown")
    with pytest.raises(ShortlistConstructionError, match="NO_VALIDATED_ACTIVE_POLICY"):
        _construct(target, fingerprints, objective=PortfolioObjective.DIVIDEND_PORTFOLIO)


def test_inactive_conflicting_and_fingerprint_invalid_policy_fail_closed(tmp_path: Path) -> None:
    target, fingerprints = _build_target(tmp_path)
    default = build_default_policy_registry(ROOT)
    capital = default.resolve_active_policy(PortfolioObjective.CAPITAL_CONSERVATION)
    inactive = PolicyRegistry(
        policies=(replace(capital, activation_status=PolicyActivationStatus.NOT_ACTIVATED),)
    )
    with pytest.raises(ShortlistConstructionError, match="NO_VALIDATED_ACTIVE_POLICY"):
        _construct(target, fingerprints, registry=inactive)
    conflicting = PolicyRegistry(
        policies=(capital, replace(capital, version="1.0.2"))
    )
    with pytest.raises(ShortlistConstructionError, match="2 validated active policies"):
        _construct(target, fingerprints, registry=conflicting)
    tampered = PolicyRegistry(policies=(replace(capital, fingerprint="e" * 64),))
    with pytest.raises(ShortlistConstructionError, match="fingerprint mismatch"):
        _construct(target, fingerprints, registry=tampered)


@pytest.mark.parametrize(
    ("statement", "message"),
    [
        ("UPDATE shortlist_stage_manifest SET completion_status='FAILED'", "manifest is incomplete"),
        ("UPDATE shortlist_stage_manifest SET dataset_fingerprint='e' || substr(dataset_fingerprint,2)", "dataset fingerprint"),
        ("DELETE FROM shortlist_entry_lineage WHERE rowid=(SELECT min(rowid) FROM shortlist_entry_lineage)", "lineage_count mismatch"),
        (
            (
                "DELETE FROM shortlist_entry_lineage WHERE shortlist_entry_id=4; "
                "DELETE FROM shortlist_entry WHERE shortlist_entry_id=4;"
            ),
            "membership_count mismatch",
        ),
    ],
)
def test_incomplete_stale_or_missing_evidence_fails_closed(
    tmp_path: Path, statement: str, message: str
) -> None:
    target, fingerprints = _build_target(tmp_path)
    with sqlite3.connect(target) as connection:
        connection.executescript(statement)
    with pytest.raises(ShortlistConstructionError, match=message):
        _construct(target, fingerprints)


def test_stale_source_fingerprint_and_incompatible_schema_fail_closed(tmp_path: Path) -> None:
    target, fingerprints = _build_target(tmp_path)
    stale = dict(fingerprints)
    stale["synthetic_20260201.xls"] = "f" * 64
    with pytest.raises(ShortlistConstructionError, match="stale source workbook"):
        _construct(target, stale)
    with sqlite3.connect(target) as connection:
        connection.execute("ALTER TABLE shortlist_stage_manifest RENAME TO missing_manifest")
        connection.commit()
    with pytest.raises(ShortlistConstructionError, match="incompatible shortlist schema"):
        _construct(target, fingerprints)


def test_foreign_key_violation_is_rejected_explicitly(tmp_path: Path) -> None:
    target, fingerprints = _build_target(tmp_path)
    with sqlite3.connect(target) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "UPDATE shortlist_entry_lineage SET source_occurrence_id=9999 WHERE rowid=(SELECT min(rowid) FROM shortlist_entry_lineage)"
        )
        connection.commit()
        assert connection.execute("PRAGMA foreign_key_check").fetchall()
    with pytest.raises(ShortlistConstructionError, match="foreign_key_check"):
        _construct(target, fingerprints)


def test_empty_eligible_universe_fails_closed(tmp_path: Path) -> None:
    target, fingerprints = _build_target(tmp_path)
    with sqlite3.connect(target) as connection:
        connection.execute(
            "DELETE FROM instrument_metric_observation WHERE observation_date='2026-02-01'"
        )
        connection.execute(
            "UPDATE shortlist_stage_manifest SET metric_observation_count=10"
        )
        connection.commit()
    with pytest.raises(ShortlistConstructionError, match="eligible instrument universe is empty"):
        _construct(target, fingerprints)
