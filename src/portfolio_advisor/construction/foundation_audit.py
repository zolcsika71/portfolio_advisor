"""Deterministic read-only validation and audit for Milestone 11B."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from portfolio_advisor.database.migrations.constructed_portfolio import (
    MIGRATION_REVISION,
    constructed_schema_contract,
)
from portfolio_advisor.database.schema.v3 import (
    CONSTRUCTED_PORTFOLIO_FEATURE_FINGERPRINT,
    CONSTRUCTED_PORTFOLIO_FEATURE_ID,
    CONSTRUCTED_PORTFOLIO_FEATURE_REVISION,
    initialize_schema,
    validate_schema,
)
from portfolio_advisor.objectives import (
    CapitalDefensiveConstructionPolicy,
    PolicyRegistry,
    PortfolioObjective,
)

from .models import ConstructionResult, ConstructionRuntimeStatus
from .persistence import ConstructionPersistenceError, validate_persisted_snapshot


class ConstructedFoundationValidationError(RuntimeError):
    """The schema, rows, or production blocked-state contract failed."""


def validate_constructed_foundation(
    database_path: Path,
    *,
    expected_policy_fingerprint: str,
    expect_zero_constructed_rows: bool,
) -> dict[str, object]:
    """Validate feature schema and every persisted candidate without writes."""
    if not database_path.is_file():
        raise ConstructedFoundationValidationError("schema-v3 database is missing")
    try:
        connection = sqlite3.connect(
            f"file:{database_path.resolve()}?mode=ro", uri=True
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA foreign_keys=ON")
        validate_schema(connection)
        target_contract = constructed_schema_contract(connection)
        with sqlite3.connect(":memory:") as scratch:
            scratch.row_factory = sqlite3.Row
            initialize_schema(scratch)
            scratch_contract = constructed_schema_contract(scratch)
        if target_contract != scratch_contract:
            raise ConstructedFoundationValidationError(
                "installed feature differs from from-scratch schema contract"
            )
        counts = _constructed_counts(connection)
        if expect_zero_constructed_rows and any(counts.values()):
            raise ConstructedFoundationValidationError(
                "production target contains constructed portfolio rows while blocked"
            )
        snapshot_ids = tuple(
            int(row[0])
            for row in connection.execute(
                "SELECT portfolio_snapshot_id FROM constructed_portfolio_metadata ORDER BY 1"
            )
        )
        expected_snapshot_count = int(
            connection.execute(
                """SELECT count(*) FROM portfolio_snapshot ps
                   JOIN portfolio p ON p.portfolio_id=ps.portfolio_id
                   WHERE p.portfolio_type='SHORTLIST_CONSTRUCTED'"""
            ).fetchone()[0]
        )
        if len(snapshot_ids) != expected_snapshot_count:
            raise ConstructedFoundationValidationError(
                "constructed snapshots and metadata do not reconcile"
            )
        candidate_fingerprints = []
        for snapshot_id in snapshot_ids:
            validated = validate_persisted_snapshot(
                connection,
                snapshot_id,
                expected_policy_fingerprint=expected_policy_fingerprint,
            )
            candidate_fingerprints.append(str(validated["candidate_fingerprint"]))
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ConstructedFoundationValidationError("SQLite integrity_check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise ConstructedFoundationValidationError("SQLite foreign_key_check failed")
    except (sqlite3.DatabaseError, ConstructionPersistenceError) as error:
        raise ConstructedFoundationValidationError(str(error)) from error
    finally:
        if "connection" in locals():
            connection.close()
    return {
        "candidate_fingerprints": sorted(candidate_fingerprints),
        "constructed_row_counts": counts,
        "foreign_key_check": [],
        "integrity_check": "ok",
        "schema_contract": target_contract,
    }


def foundation_audit_payload(
    *,
    validation: dict[str, object],
    production_attempt: ConstructionResult,
    policy: CapitalDefensiveConstructionPolicy,
    registry: PolicyRegistry,
) -> dict[str, object]:
    """Build stable privacy-safe audit JSON with no amount, timestamp, or path."""
    if production_attempt.status is not ConstructionRuntimeStatus.IMPLEMENTED_BLOCKED_BY_DATA:
        raise ConstructedFoundationValidationError(
            "current production evidence did not return IMPLEMENTED_BLOCKED_BY_DATA"
        )
    active = registry.resolve_active_policy(PortfolioObjective.CAPITAL_CONSERVATION)
    return {
        "audit_schema_version": 1,
        "capability_states": active.capabilities.to_dict(),
        "construction_policy": {
            "fingerprint": policy.fingerprint,
            "policy_id": policy.policy_id,
            "version": policy.version,
        },
        "explicit_statement": "NO_PRODUCTION_CONSTRUCTED_PORTFOLIO_CAN_BE_PRODUCED",
        "migration_revision": MIGRATION_REVISION,
        "production_attempt": production_attempt.to_dict(),
        "production_cutover": "NOT_AUTHORIZED",
        "registry_fingerprint": registry.registry_fingerprint(),
        "runtime_dependencies": {
            "allocation_engine": "IMPLEMENTED",
            "constructed_portfolio_schema": "IMPLEMENTED",
            "current_admitted_nav": "BLOCKED_BY_COVERAGE_AND_STALENESS",
            "official_reference_rates": "MISSING",
            "portfolio_metrics": "NOT_IMPLEMENTED",
            "transactional_persistence": "IMPLEMENTED",
        },
        "schema_feature": {
            "contract_fingerprint": CONSTRUCTED_PORTFOLIO_FEATURE_FINGERPRINT,
            "feature_id": CONSTRUCTED_PORTFOLIO_FEATURE_ID,
            "revision": CONSTRUCTED_PORTFOLIO_FEATURE_REVISION,
        },
        "validation": validation,
    }


def _constructed_counts(connection: sqlite3.Connection) -> dict[str, int]:
    queries = {
        "cash_rows": """SELECT count(*) FROM portfolio_cash pc
                         JOIN portfolio_snapshot ps USING(portfolio_snapshot_id)
                         JOIN portfolio p USING(portfolio_id)
                         WHERE p.portfolio_type='SHORTLIST_CONSTRUCTED'""",
        "holding_lineage_rows": "SELECT count(*) FROM constructed_portfolio_holding_lineage",
        "holding_rows": """SELECT count(*) FROM portfolio_holding ph
                            JOIN portfolio_snapshot ps USING(portfolio_snapshot_id)
                            JOIN portfolio p USING(portfolio_id)
                            WHERE p.portfolio_type='SHORTLIST_CONSTRUCTED'""",
        "metadata_rows": "SELECT count(*) FROM constructed_portfolio_metadata",
        "portfolio_rows": "SELECT count(*) FROM portfolio WHERE portfolio_type='SHORTLIST_CONSTRUCTED'",
        "snapshot_rows": """SELECT count(*) FROM portfolio_snapshot ps
                             JOIN portfolio p USING(portfolio_id)
                             WHERE p.portfolio_type='SHORTLIST_CONSTRUCTED'""",
    }
    return {
        name: int(connection.execute(query).fetchone()[0])
        for name, query in sorted(queries.items())
    }
