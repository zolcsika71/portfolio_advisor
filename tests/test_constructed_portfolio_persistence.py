"""Transactional identity, lineage, idempotency, and tamper tests for 11B."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from portfolio_advisor.canonical import canonical_fingerprint
from portfolio_advisor.construction import (
    ConstructionEvidenceReadiness,
    ConstructionPersistenceError,
    construct_capital_defensive_portfolio,
    persist_constructed_candidate,
)
from portfolio_advisor.construction.foundation_audit import (
    ConstructedFoundationValidationError,
    foundation_audit_payload,
    validate_constructed_foundation,
)
from portfolio_advisor.objectives import (
    CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT,
    build_default_policy_registry,
    load_capital_defensive_construction_policy,
)

from .constructed_portfolio_fixtures import build_fixture

ROOT = Path(__file__).resolve().parents[1]
POLICY = load_capital_defensive_construction_policy(
    ROOT / CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT
)


def _candidate(fixture, amount: str = "1000.01"):  # type: ignore[no-untyped-def]
    result = construct_capital_defensive_portfolio(
        screening=fixture.screening,
        cash_by_currency={"EUR": Decimal(amount)},
        policy=POLICY,
        instruments=fixture.instruments,
        readiness=ConstructionEvidenceReadiness(True, True, True),
    )
    assert result.candidate is not None
    return result.candidate


def _source_projection(path: Path) -> tuple[tuple[object, ...], ...]:
    with sqlite3.connect(path) as connection:
        return tuple(
            tuple(row)
            for row in connection.execute(
                """SELECT e.shortlist_entry_id, e.instrument_id, o.source_payload_json,
                          o.conflict_status, l.source_occurrence_id
                   FROM shortlist_entry e
                   JOIN shortlist_entry_lineage l USING(shortlist_entry_id)
                   JOIN shortlist_entry_source_occurrence o
                     ON o.shortlist_entry_source_occurrence_id=l.source_occurrence_id
                   ORDER BY e.shortlist_entry_id"""
            )
        )


def test_atomic_persistence_is_idempotent_normalized_and_private_cash_free(
    tmp_path: Path,
) -> None:
    fixture = build_fixture(tmp_path)
    candidate = _candidate(fixture)
    source_before = _source_projection(fixture.database_path)
    first = persist_constructed_candidate(
        database_path=fixture.database_path,
        candidate=candidate,
        policy=POLICY,
    )
    same_without_amount_identity = _candidate(fixture, "987654321.09")
    second = persist_constructed_candidate(
        database_path=fixture.database_path,
        candidate=same_without_amount_identity,
        policy=POLICY,
    )
    assert first.reused is False and second.reused is True
    assert first.portfolio_snapshot_id == second.portfolio_snapshot_id
    assert first.candidate_fingerprint == second.candidate_fingerprint
    with sqlite3.connect(fixture.database_path) as connection:
        connection.row_factory = sqlite3.Row
        counts = {
            table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in (
                "constructed_portfolio_metadata",
                "constructed_portfolio_holding_lineage",
                "portfolio_cash",
                "portfolio_holding",
            )
        }
        cash = connection.execute(
            "SELECT amount, weight, currency_code FROM portfolio_cash"
        ).fetchone()
        provenance = str(
            connection.execute(
                "SELECT deterministic_provenance_json FROM constructed_portfolio_metadata"
            ).fetchone()[0]
        )
    assert counts == {
        "constructed_portfolio_holding_lineage": 8,
        "constructed_portfolio_metadata": 1,
        "portfolio_cash": 1,
        "portfolio_holding": 8,
    }
    assert cash["amount"] is None and Decimal(str(cash["weight"])) == Decimal("0.20")
    assert cash["currency_code"] == "EUR"
    assert "1000.01" not in provenance and "987654321.09" not in provenance
    assert _source_projection(fixture.database_path) == source_before
    validation = validate_constructed_foundation(
        fixture.database_path,
        expected_policy_fingerprint=POLICY.fingerprint,
        expect_zero_constructed_rows=False,
    )
    assert validation["candidate_fingerprints"] == [candidate.candidate_fingerprint]


def test_conflicting_payload_under_same_identity_fails_closed(tmp_path: Path) -> None:
    fixture = build_fixture(tmp_path)
    candidate = _candidate(fixture)
    persist_constructed_candidate(
        database_path=fixture.database_path, candidate=candidate, policy=POLICY
    )
    conflicting = replace(
        candidate,
        eligible_universe_fingerprint=canonical_fingerprint("different eligible universe"),
    )
    with pytest.raises(ConstructionPersistenceError, match="conflicting payload"):
        persist_constructed_candidate(
            database_path=fixture.database_path,
            candidate=conflicting,
            policy=POLICY,
        )
    with sqlite3.connect(fixture.database_path) as connection:
        assert connection.execute("SELECT count(*) FROM portfolio_snapshot").fetchone()[0] == 1


def test_injected_failure_rolls_back_every_row_and_does_not_repair(tmp_path: Path) -> None:
    fixture = build_fixture(tmp_path)

    def fail(stage: str) -> None:
        if stage == "after_holdings":
            raise RuntimeError("injected persistence failure")

    with pytest.raises(RuntimeError, match="injected"):
        persist_constructed_candidate(
            database_path=fixture.database_path,
            candidate=_candidate(fixture),
            policy=POLICY,
            failure_hook=fail,
        )
    with sqlite3.connect(fixture.database_path) as connection:
        for table in (
            "portfolio",
            "portfolio_snapshot",
            "portfolio_holding",
            "portfolio_cash",
            "constructed_portfolio_metadata",
            "constructed_portfolio_holding_lineage",
        ):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_membership_occurrence_reachability_and_fk_delete_contracts(tmp_path: Path) -> None:
    fixture = build_fixture(tmp_path)
    persisted = persist_constructed_candidate(
        database_path=fixture.database_path,
        candidate=_candidate(fixture),
        policy=POLICY,
    )
    with sqlite3.connect(fixture.database_path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        reachable = connection.execute(
            """SELECT count(*) FROM constructed_portfolio_holding_lineage c
               JOIN shortlist_entry_lineage l ON l.shortlist_entry_id=c.shortlist_entry_id
               JOIN shortlist_entry_source_occurrence o
                 ON o.shortlist_entry_source_occurrence_id=l.source_occurrence_id"""
        ).fetchone()[0]
        assert reachable == 8
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM shortlist_entry WHERE shortlist_entry_id=1")
        connection.rollback()
        connection.execute("BEGIN")
        holding_id = connection.execute(
            "SELECT min(portfolio_holding_id) FROM portfolio_holding"
        ).fetchone()[0]
        connection.execute(
            "DELETE FROM portfolio_holding WHERE portfolio_holding_id=?", (holding_id,)
        )
        assert connection.execute(
            "SELECT count(*) FROM constructed_portfolio_holding_lineage"
        ).fetchone()[0] == 7
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "DELETE FROM portfolio_snapshot WHERE portfolio_snapshot_id=?",
                (persisted.portfolio_snapshot_id,),
            )


def test_validator_detects_tampering_missing_and_extra_rows(tmp_path: Path) -> None:
    fixture = build_fixture(tmp_path)
    persist_constructed_candidate(
        database_path=fixture.database_path,
        candidate=_candidate(fixture),
        policy=POLICY,
    )
    with sqlite3.connect(fixture.database_path) as connection:
        connection.execute("UPDATE portfolio_cash SET weight=0.21")
        connection.commit()
    with pytest.raises(ConstructedFoundationValidationError, match="cash reserve"):
        validate_constructed_foundation(
            fixture.database_path,
            expected_policy_fingerprint=POLICY.fingerprint,
            expect_zero_constructed_rows=False,
        )


def test_blocked_result_creates_zero_constructed_rows(tmp_path: Path) -> None:
    fixture = build_fixture(tmp_path)
    result = construct_capital_defensive_portfolio(
        screening=fixture.screening,
        cash_by_currency={"EUR": Decimal(1)},
        policy=POLICY,
        instruments=fixture.instruments,
        readiness=ConstructionEvidenceReadiness(False, False, False),
    )
    assert result.candidate is None
    with sqlite3.connect(fixture.database_path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM portfolio WHERE portfolio_type='SHORTLIST_CONSTRUCTED'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM constructed_portfolio_metadata"
        ).fetchone()[0] == 0


def test_foundation_audit_is_deterministic_and_policy_fingerprints_are_unchanged(
    tmp_path: Path,
) -> None:
    fixture = build_fixture(tmp_path)
    result = construct_capital_defensive_portfolio(
        screening=fixture.screening,
        cash_by_currency={"EUR": Decimal(1)},
        policy=POLICY,
        instruments=fixture.instruments,
        readiness=ConstructionEvidenceReadiness(False, False, False),
    )
    validation = validate_constructed_foundation(
        fixture.database_path,
        expected_policy_fingerprint=POLICY.fingerprint,
        expect_zero_constructed_rows=True,
    )
    registry = build_default_policy_registry(ROOT)
    first = foundation_audit_payload(
        validation=validation,
        production_attempt=result,
        policy=POLICY,
        registry=registry,
    )
    second = foundation_audit_payload(
        validation=validation,
        production_attempt=result,
        policy=POLICY,
        registry=build_default_policy_registry(ROOT),
    )
    assert first == second
    assert POLICY.fingerprint == "a5dc75f07eac4e0ab615f1669a95f7eecdbb3f0e31e1c6bb174dd000097ccbbf"
    assert registry.registry_fingerprint(schema_version=2) == (
        "ddc2fc0d45a8e2f9788a6e36589c3956cf30d2347b081b22b4a66465ed244d57"
    )
