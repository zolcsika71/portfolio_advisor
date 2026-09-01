"""Transactional, idempotent persistence for normalized constructed candidates."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from portfolio_advisor.canonical import canonical_fingerprint, canonical_json
from portfolio_advisor.database.schema.v3 import connect, transaction, validate_schema
from portfolio_advisor.objectives import CapitalDefensiveConstructionPolicy

from .models import ConstructedPortfolioCandidate
from .validation import validate_constructed_candidate

PERSISTENCE_VERSION = "MILESTONE_11B_CONSTRUCTION_PERSISTENCE_V1"
ALLOCATION_BASIS = "FIXED_TOTAL_PORTFOLIO_WEIGHT"


class ConstructionPersistenceError(RuntimeError):
    """A candidate cannot be persisted without conflict or partial state."""


@dataclass(frozen=True, slots=True)
class PersistenceResult:
    portfolio_id: int
    portfolio_snapshot_id: int
    candidate_fingerprint: str
    reused: bool


def persist_constructed_candidate(
    *,
    database_path: Path,
    candidate: ConstructedPortfolioCandidate,
    policy: CapitalDefensiveConstructionPolicy,
    failure_hook: Callable[[str], None] | None = None,
) -> PersistenceResult:
    """Insert or reuse one logical candidate atomically; never persist private cash."""
    validate_constructed_candidate(candidate, policy)
    if candidate.policy_fingerprint != policy.fingerprint:
        raise ConstructionPersistenceError("candidate construction-policy fingerprint mismatch")
    connection = connect(database_path)
    try:
        validate_schema(connection)
        with transaction(connection):
            existing = connection.execute(
                """SELECT portfolio_snapshot_id FROM constructed_portfolio_metadata
                   WHERE candidate_fingerprint=?""",
                (candidate.candidate_fingerprint,),
            ).fetchone()
            if existing is not None:
                snapshot_id = int(existing[0])
                validated = validate_persisted_snapshot(
                    connection,
                    snapshot_id,
                    expected_policy_fingerprint=policy.fingerprint,
                    expected_candidate=candidate,
                )
                validated_portfolio_id = validated["portfolio_id"]
                if not isinstance(validated_portfolio_id, int):
                    raise ConstructionPersistenceError("persisted portfolio identity is invalid")
                return PersistenceResult(
                    portfolio_id=validated_portfolio_id,
                    portfolio_snapshot_id=snapshot_id,
                    candidate_fingerprint=candidate.candidate_fingerprint,
                    reused=True,
                )
            portfolio_name = (
                f"CAPITAL_DEFENSIVE:{candidate.currency}:"
                f"{candidate.policy_id}:{candidate.policy_version}"
            )
            portfolio_row = connection.execute(
                """SELECT portfolio_id, base_currency_code FROM portfolio
                   WHERE portfolio_name=? AND portfolio_type='SHORTLIST_CONSTRUCTED'""",
                (portfolio_name,),
            ).fetchone()
            if portfolio_row is None:
                cursor = connection.execute(
                    """INSERT INTO portfolio(portfolio_name, portfolio_type, base_currency_code)
                       VALUES (?, 'SHORTLIST_CONSTRUCTED', ?)""",
                    (portfolio_name, candidate.currency),
                )
                portfolio_id = _lastrowid(cursor)
            else:
                portfolio_id = int(portfolio_row[0])
                if str(portfolio_row[1]) != candidate.currency:
                    raise ConstructionPersistenceError("constructed portfolio currency conflict")
            conflict = connection.execute(
                """SELECT ps.portfolio_snapshot_id, m.candidate_fingerprint
                   FROM portfolio_snapshot ps
                   LEFT JOIN constructed_portfolio_metadata m
                     ON m.portfolio_snapshot_id=ps.portfolio_snapshot_id
                   WHERE ps.portfolio_id=? AND ps.snapshot_date=?""",
                (portfolio_id, candidate.provenance.snapshot_date.isoformat()),
            ).fetchone()
            if conflict is not None:
                raise ConstructionPersistenceError(
                    "conflicting payload under deterministic portfolio snapshot identity"
                )
            snapshot_cursor = connection.execute(
                """INSERT INTO portfolio_snapshot(
                       portfolio_id, snapshot_date, source_sheet_id, construction_policy_id
                   ) VALUES (?, ?, NULL, ?)""",
                (
                    portfolio_id,
                    candidate.provenance.snapshot_date.isoformat(),
                    candidate.policy_id,
                ),
            )
            snapshot_id = _lastrowid(snapshot_cursor)
            _call_hook(failure_hook, "after_snapshot")
            for holding in candidate.holdings:
                cursor = connection.execute(
                    """INSERT INTO portfolio_holding(
                           portfolio_snapshot_id, instrument_id, reported_weight,
                           derivation_status, calculation_version, approval_reference
                       ) VALUES (?, ?, ?, 'APPROVED_AGGREGATION', ?, ?)""",
                    (
                        snapshot_id,
                        holding.instrument_id,
                        float(holding.weight),
                        PERSISTENCE_VERSION,
                        f"{candidate.policy_id}:{candidate.policy_version}:{candidate.policy_fingerprint}",
                    ),
                )
                holding_id = _lastrowid(cursor)
                connection.execute(
                    """INSERT INTO constructed_portfolio_holding_lineage(
                           portfolio_holding_id, shortlist_entry_id, selected_instrument_rank,
                           allocation_basis, allocation_weight_decimal,
                           constraint_evidence_fingerprint
                       ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        holding_id,
                        holding.shortlist_entry_id,
                        holding.rank,
                        ALLOCATION_BASIS,
                        format(holding.weight, "f"),
                        holding.constraint_evidence_fingerprint,
                    ),
                )
            _call_hook(failure_hook, "after_holdings")
            connection.execute(
                """INSERT INTO portfolio_cash(
                       portfolio_snapshot_id, currency_code, amount, weight, cash_role, source
                   ) VALUES (?, ?, NULL, ?, 'RESERVE', ?)""",
                (
                    snapshot_id,
                    candidate.currency,
                    float(candidate.cash_weight),
                    f"{candidate.policy_id}:{candidate.policy_version}",
                ),
            )
            connection.execute(
                """INSERT INTO constructed_portfolio_metadata(
                       portfolio_snapshot_id, shortlist_snapshot_id, objective_code,
                       construction_policy_id, construction_policy_version,
                       construction_policy_fingerprint, construction_strategy, cash_currency,
                       portfolio_identity_fingerprint, eligible_universe_fingerprint,
                       selected_universe_fingerprint, candidate_fingerprint,
                       construction_status, deterministic_provenance_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    snapshot_id,
                    candidate.provenance.shortlist_snapshot_id,
                    candidate.objective,
                    candidate.policy_id,
                    candidate.policy_version,
                    candidate.policy_fingerprint,
                    candidate.strategy,
                    candidate.currency,
                    candidate.portfolio_identity_fingerprint,
                    candidate.eligible_universe_fingerprint,
                    candidate.selected_universe_fingerprint,
                    candidate.candidate_fingerprint,
                    candidate.status.value,
                    canonical_json(candidate.provenance.stable_payload()),
                ),
            )
            _call_hook(failure_hook, "after_metadata")
            validate_persisted_snapshot(
                connection,
                snapshot_id,
                expected_policy_fingerprint=policy.fingerprint,
                expected_candidate=candidate,
            )
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ConstructionPersistenceError("post-write integrity_check failed")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise ConstructionPersistenceError("post-write foreign_key_check failed")
        return PersistenceResult(
            portfolio_id=portfolio_id,
            portfolio_snapshot_id=snapshot_id,
            candidate_fingerprint=candidate.candidate_fingerprint,
            reused=False,
        )
    except sqlite3.DatabaseError as error:
        raise ConstructionPersistenceError("constructed candidate transaction failed") from error
    finally:
        connection.close()


def validate_persisted_snapshot(
    connection: sqlite3.Connection,
    portfolio_snapshot_id: int,
    *,
    expected_policy_fingerprint: str | None = None,
    expected_candidate: ConstructedPortfolioCandidate | None = None,
) -> dict[str, object]:
    """Read-only structural, lineage, allocation, and fingerprint validation."""
    header = connection.execute(
        """SELECT p.portfolio_id, p.portfolio_type, p.base_currency_code,
                  ps.snapshot_date, ps.source_sheet_id,
                  ps.construction_policy_id AS snapshot_construction_policy_id, m.*
           FROM constructed_portfolio_metadata m
           JOIN portfolio_snapshot ps ON ps.portfolio_snapshot_id=m.portfolio_snapshot_id
           JOIN portfolio p ON p.portfolio_id=ps.portfolio_id
           WHERE m.portfolio_snapshot_id=?""",
        (portfolio_snapshot_id,),
    ).fetchone()
    if header is None:
        raise ConstructionPersistenceError("constructed portfolio metadata is missing")
    if (
        str(header["portfolio_type"]) != "SHORTLIST_CONSTRUCTED"
        or header["source_sheet_id"] is not None
        or str(header["base_currency_code"]) != str(header["cash_currency"])
        or str(header["snapshot_construction_policy_id"])
        != str(header["construction_policy_id"])
    ):
        raise ConstructionPersistenceError("constructed portfolio header contract failed")
    if expected_policy_fingerprint is not None and str(
        header["construction_policy_fingerprint"]
    ) != expected_policy_fingerprint:
        raise ConstructionPersistenceError("persisted policy fingerprint mismatch")
    holdings = connection.execute(
        """SELECT ph.portfolio_holding_id, ph.instrument_id, ph.reported_weight,
                  ph.derivation_status, ph.calculation_version, i.isin,
                  l.shortlist_entry_id, l.selected_instrument_rank, l.allocation_basis,
                  l.allocation_weight_decimal, l.constraint_evidence_fingerprint,
                  e.instrument_id AS membership_instrument_id,
                  e.shortlist_snapshot_id AS membership_snapshot_id
           FROM portfolio_holding ph
           JOIN instrument i ON i.instrument_id=ph.instrument_id
           JOIN constructed_portfolio_holding_lineage l
             ON l.portfolio_holding_id=ph.portfolio_holding_id
           JOIN shortlist_entry e ON e.shortlist_entry_id=l.shortlist_entry_id
           WHERE ph.portfolio_snapshot_id=?
           ORDER BY l.selected_instrument_rank, i.isin""",
        (portfolio_snapshot_id,),
    ).fetchall()
    actual_holding_count = int(
        connection.execute(
            "SELECT count(*) FROM portfolio_holding WHERE portfolio_snapshot_id=?",
            (portfolio_snapshot_id,),
        ).fetchone()[0]
    )
    if len(holdings) != 8 or actual_holding_count != 8:
        raise ConstructionPersistenceError("constructed portfolio requires exactly eight holdings")
    if len({str(row["isin"]) for row in holdings}) != 8:
        raise ConstructionPersistenceError("constructed holding identities are not unique")
    groups: list[tuple[str, str]] = []
    holding_payloads: list[dict[str, object]] = []
    for row in holdings:
        if (
            int(row["instrument_id"]) != int(row["membership_instrument_id"])
            or int(row["membership_snapshot_id"]) != int(header["shortlist_snapshot_id"])
            or Decimal(str(row["reported_weight"])) != Decimal("0.10")
            or str(row["allocation_weight_decimal"]) != "0.10"
            or str(row["allocation_basis"]) != ALLOCATION_BASIS
            or str(row["derivation_status"]) != "APPROVED_AGGREGATION"
            or str(row["calculation_version"]) != PERSISTENCE_VERSION
        ):
            raise ConstructionPersistenceError("constructed holding or membership contract failed")
        occurrences = connection.execute(
            """SELECT o.observed_asset_class, o.observed_sub_asset_class, o.conflict_status
               FROM shortlist_entry_lineage sl
               JOIN shortlist_entry_source_occurrence o
                 ON o.shortlist_entry_source_occurrence_id=sl.source_occurrence_id
               WHERE sl.shortlist_entry_id=? ORDER BY sl.source_occurrence_id""",
            (int(row["shortlist_entry_id"]),),
        ).fetchall()
        category_groups = {
            (str(item[0]).strip(), str(item[1]).strip()) for item in occurrences
        }
        if (
            not occurrences
            or len(category_groups) != 1
            or any(str(item[2]) != "SOURCE_REPORTED" for item in occurrences)
            or any(not part for part in next(iter(category_groups)))
        ):
            raise ConstructionPersistenceError("constructed category lineage is incomplete")
        group = next(iter(category_groups))
        groups.append(group)
        holding_payloads.append(
            {
                "constraint_evidence_fingerprint": str(
                    row["constraint_evidence_fingerprint"]
                ),
                "currency": str(header["cash_currency"]),
                "group": list(group),
                "isin": str(row["isin"]),
                "rank": int(row["selected_instrument_rank"]),
                "weight": "0.10",
            }
        )
    group_counts = Counter(groups)
    if len(group_counts) < 3 or max(group_counts.values()) > 4:
        raise ConstructionPersistenceError("constructed diversification contract failed")
    cash = connection.execute(
        "SELECT currency_code, amount, weight, cash_role FROM portfolio_cash WHERE portfolio_snapshot_id=?",
        (portfolio_snapshot_id,),
    ).fetchall()
    if (
        len(cash) != 1
        or str(cash[0][0]) != str(header["cash_currency"])
        or cash[0][1] is not None
        or Decimal(str(cash[0][2])) != Decimal("0.20")
        or str(cash[0][3]) != "RESERVE"
    ):
        raise ConstructionPersistenceError("constructed cash reserve contract failed")
    selected_fingerprint = canonical_fingerprint(sorted(str(row["isin"]) for row in holdings))
    if selected_fingerprint != str(header["selected_universe_fingerprint"]):
        raise ConstructionPersistenceError("selected-universe fingerprint mismatch")
    provenance = _validated_provenance(connection, header)
    identity_fingerprint = canonical_fingerprint(
        {
            "currency": str(header["cash_currency"]),
            "objective": str(header["objective_code"]),
            "policy_id": str(header["construction_policy_id"]),
            "policy_version": str(header["construction_policy_version"]),
            "strategy": str(header["construction_strategy"]),
        }
    )
    if identity_fingerprint != str(header["portfolio_identity_fingerprint"]):
        raise ConstructionPersistenceError("portfolio identity fingerprint mismatch")
    payload = {
        "cash": {"currency": str(header["cash_currency"]), "weight": "0.20"},
        "eligible_universe_fingerprint": str(header["eligible_universe_fingerprint"]),
        "holdings": holding_payloads,
        "objective": str(header["objective_code"]),
        "policy_fingerprint": str(header["construction_policy_fingerprint"]),
        "policy_id": str(header["construction_policy_id"]),
        "policy_version": str(header["construction_policy_version"]),
        "portfolio_identity_fingerprint": identity_fingerprint,
        "provenance": provenance,
        "selected_universe_fingerprint": selected_fingerprint,
        "status": str(header["construction_status"]),
        "strategy": str(header["construction_strategy"]),
    }
    candidate_fingerprint = canonical_fingerprint(payload)
    if candidate_fingerprint != str(header["candidate_fingerprint"]):
        raise ConstructionPersistenceError("candidate fingerprint mismatch")
    if expected_candidate is not None and candidate_fingerprint != expected_candidate.candidate_fingerprint:
        raise ConstructionPersistenceError("persisted candidate differs from requested candidate")
    return {
        "candidate_fingerprint": candidate_fingerprint,
        "portfolio_id": int(header["portfolio_id"]),
        "portfolio_snapshot_id": portfolio_snapshot_id,
    }


def _validated_provenance(
    connection: sqlite3.Connection, header: sqlite3.Row
) -> dict[str, object]:
    try:
        persisted = json.loads(str(header["deterministic_provenance_json"]))
    except json.JSONDecodeError as error:
        raise ConstructionPersistenceError("deterministic provenance is malformed") from error
    source = connection.execute(
        """SELECT ss.snapshot_date, sf.sha256, sh.sheet_name,
                  sm.dataset_fingerprint, sm.integration_version
           FROM shortlist_snapshot ss
           JOIN source_sheet sh ON sh.source_sheet_id=ss.source_sheet_id
           JOIN source_file sf ON sf.source_file_id=sh.source_file_id
           JOIN shortlist_stage_manifest sm ON sm.singleton=1
           WHERE ss.shortlist_snapshot_id=?""",
        (int(header["shortlist_snapshot_id"]),),
    ).fetchone()
    if source is None:
        raise ConstructionPersistenceError("source shortlist provenance is missing")
    expected: dict[str, object] = {
        "shortlist_integration_version": str(source[4]),
        "shortlist_manifest_fingerprint": str(source[3]),
        "snapshot_date": str(source[0]),
        "source_file_sha256": str(source[1]),
        "source_sheet_name": str(source[2]),
    }
    if persisted != expected or canonical_json(persisted) != str(
        header["deterministic_provenance_json"]
    ):
        raise ConstructionPersistenceError("deterministic provenance conflicts with source")
    return expected


def _lastrowid(cursor: sqlite3.Cursor) -> int:
    if cursor.lastrowid is None:
        raise ConstructionPersistenceError("SQLite insert did not return a row ID")
    return int(cursor.lastrowid)


def _call_hook(hook: Callable[[str], None] | None, stage: str) -> None:
    if hook is not None:
        hook(stage)
