"""Guarded parallel schema-v3 builder for the authoritative legacy model source.

This module deliberately never changes the legacy source and has no cutover
operation.  It is a build-and-validate path for a separately named analytical
database only.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from portfolio_advisor.database.migrations.model_portfolio_dry_run import (
    ModelPortfolioMigrationError,
    SchemaV3ModelPortfolioRepository,
    _counts,
    _destination_content_fingerprint,
    _duplicate_occurrence_report,
    _populate,
    _sha256,
    _source_fingerprints,
    dry_run_model_portfolio_to_v3,
    equivalence_report,
    reconcile_legacy_to_workbook,
)
from portfolio_advisor.database.migrations.validation import validate_integrity
from portfolio_advisor.database.repository import ModelPortfolioRepository
from portfolio_advisor.database.schema.v3 import (
    SCHEMA_VERSION,
    connect,
    initialize_schema,
    transaction,
)

BUILD_VERSION = "MILESTONE_7_MODEL_PORTFOLIO_PARALLEL_V1"
CUTOVER_STATUS = "NOT_AUTHORIZED"


class ParallelBuildError(ModelPortfolioMigrationError):
    """The parallel target cannot be safely built or validated."""


@dataclass(frozen=True, slots=True)
class ParallelBuildResult:
    mode: str
    published: bool
    source_fingerprints: dict[str, str]
    target_fingerprint: str | None
    database_fingerprint: str | None
    counts: dict[str, int]
    duplicate_occurrences: dict[str, Any]
    equivalence_by_date: dict[str, dict[str, Any]]
    maximum_numeric_delta: float
    build_status: str


def dry_run_parallel_build(*, legacy_path: Path, workbook_directory: Path, rules_path: Path) -> ParallelBuildResult:
    """Run all migration and ranking gates in a disposable sibling-free file."""
    with tempfile.TemporaryDirectory(prefix="portfolio-advisor-m7-") as directory:
        destination = Path(directory) / "schema-v3.sqlite"
        result = dry_run_model_portfolio_to_v3(
            legacy_path=legacy_path,
            workbook_directory=workbook_directory,
            destination_path=destination,
            rules_path=rules_path,
        )
    return ParallelBuildResult(
        mode="DRY_RUN", published=False, source_fingerprints=result.source_fingerprints,
        target_fingerprint=result.destination_fingerprint, database_fingerprint=None,
        counts=result.counts, duplicate_occurrences=result.duplicate_occurrences,
        equivalence_by_date=result.equivalence_by_date,
        maximum_numeric_delta=result.maximum_numeric_delta, build_status="DRY_RUN_EXACT_PASS",
    )


def build_parallel_database(
    *, legacy_path: Path, workbook_directory: Path, target_path: Path, rules_path: Path,
    audited_dry_run_path: Path,
) -> ParallelBuildResult:
    """Atomically publish a new absent parallel target only after every gate passes."""
    source_fingerprints = _source_fingerprints(legacy_path, workbook_directory)
    _assert_audited_source_fingerprints(source_fingerprints, audited_dry_run_path)
    target = target_path.resolve()
    if target == legacy_path.resolve():
        raise ParallelBuildError("parallel target cannot be the authoritative legacy source")
    if target.exists():
        return validate_parallel_database(
            legacy_path=legacy_path, workbook_directory=workbook_directory, target_path=target,
            rules_path=rules_path, audited_dry_run_path=audited_dry_run_path,
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.m7-", suffix=".sqlite", dir=target.parent)
    os.close(descriptor)
    Path(temporary_name).unlink()  # SQLite creates this exact, private sibling.
    temporary = Path(temporary_name)
    try:
        rows = reconcile_legacy_to_workbook(legacy_path, workbook_directory)
        connection = connect(temporary)
        try:
            initialize_schema(connection)
            _populate(connection, rows)
            validate_integrity(connection)
        finally:
            connection.close()
        equivalence = equivalence_report(
            ModelPortfolioRepository(legacy_path), SchemaV3ModelPortfolioRepository(temporary), rules_path,
        )
        if not all(item["exact"] for item in equivalence.values()):
            raise ParallelBuildError("exact legacy/schema-v3 equivalence gate failed")
        duplicate = _duplicate_occurrence_report(temporary)
        expected_duplicate = _load_audited_dry_run(audited_dry_run_path).get("duplicate_occurrence_results", {}).get("count")
        if duplicate["count"] != expected_duplicate:
            raise ParallelBuildError("unresolved duplicate source-occurrence count differs from audited corpus")
        counts = _counts(temporary)
        _assert_audited_target_counts(counts, audited_dry_run_path)
        dataset_fingerprint = _destination_content_fingerprint(temporary)
        _insert_manifest(temporary, source_fingerprints, rules_path, counts, duplicate, dataset_fingerprint)
        _validate_path(temporary)
        if source_fingerprints != _source_fingerprints(legacy_path, workbook_directory):
            raise ParallelBuildError("retained source fingerprint changed during parallel build")
        temporary.replace(target)
        return ParallelBuildResult(
            mode="APPLY", published=True, source_fingerprints=source_fingerprints,
            target_fingerprint=dataset_fingerprint, database_fingerprint=_sha256(target), counts=counts,
            duplicate_occurrences=duplicate, equivalence_by_date=equivalence,
            maximum_numeric_delta=max((float(item["maximum_numeric_delta"]) for item in equivalence.values()), default=0.0),
            build_status="PARALLEL_VALIDATED",
        )
    except BaseException:
        if temporary.exists():
            temporary.unlink()
        raise


def validate_parallel_database(
    *, legacy_path: Path, workbook_directory: Path, target_path: Path, rules_path: Path,
    audited_dry_run_path: Path,
) -> ParallelBuildResult:
    """Read-only validation: detect stale sources, manifest changes and ranking drift."""
    target = target_path.resolve()
    if not target.is_file():
        raise ParallelBuildError("parallel target is missing")
    source_fingerprints = _source_fingerprints(legacy_path, workbook_directory)
    _assert_audited_source_fingerprints(source_fingerprints, audited_dry_run_path)
    _validate_path(target)
    with sqlite3.connect(f"file:{target}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        manifest = connection.execute("SELECT * FROM migration_build_manifest WHERE singleton=1").fetchone()
        if manifest is None:
            raise ParallelBuildError("parallel target has no build manifest")
        if str(manifest["build_version"]) != BUILD_VERSION:
            raise ParallelBuildError("parallel target build version is stale")
        if json.loads(str(manifest["source_fingerprints_json"])) != source_fingerprints:
            raise ParallelBuildError("parallel target is stale: retained source fingerprint changed")
        if str(manifest["ranking_policy_sha256"]) != _sha256(rules_path):
            raise ParallelBuildError("parallel target is stale: ranking policy changed")
        # Later approved stages add instruments/NAV/shortlist facts.  They do
        # not alter the source-occurrence compatibility contract, which is
        # re-proven below by exact legacy ranking equivalence.
    equivalence = equivalence_report(
        ModelPortfolioRepository(legacy_path), SchemaV3ModelPortfolioRepository(target), rules_path,
    )
    if not all(item["exact"] for item in equivalence.values()):
        raise ParallelBuildError("exact legacy/schema-v3 equivalence gate failed")
    counts = _counts(target)
    duplicate = _duplicate_occurrence_report(target)
    return ParallelBuildResult(
        mode="VALIDATE", published=False, source_fingerprints=source_fingerprints,
        target_fingerprint=_destination_content_fingerprint(target), database_fingerprint=_sha256(target),
        counts=counts, duplicate_occurrences=duplicate, equivalence_by_date=equivalence,
        maximum_numeric_delta=max((float(item["maximum_numeric_delta"]) for item in equivalence.values()), default=0.0),
        build_status="PARALLEL_VALIDATED",
    )


def result_as_audit(result: ParallelBuildResult) -> dict[str, Any]:
    """Stable local-audit representation; no private LTIA facts are included."""
    return {
        **asdict(result),
        "cutover_status": CUTOVER_STATUS,
        "all_dates_exact": all(item["exact"] for item in result.equivalence_by_date.values()),
        "integrity_check": "ok",
        "foreign_key_check": [],
        "lineage_validation": {
            "source_occurrences": result.counts["portfolio_holding_source_occurrence"],
            "unresolved_duplicate_occurrences": result.duplicate_occurrences["count"],
            "analytical_holdings": result.counts["portfolio_holding"],
        },
        "blockers": ["IE00B7KFL990 semantics remain UNRESOLVED_DUPLICATE_SEMANTICS"],
    }


def _assert_audited_source_fingerprints(actual: dict[str, str], audit_path: Path) -> None:
    audit = _load_audited_dry_run(audit_path)
    if audit.get("source_fingerprints") != actual:
        raise ParallelBuildError("retained source fingerprints differ from the audited dry-run corpus; re-audit required")


def _assert_audited_target_counts(actual: dict[str, int], audit_path: Path) -> None:
    expected = _load_audited_dry_run(audit_path).get("migrated_counts")
    if expected != actual:
        raise ParallelBuildError("migrated counts differ from the audited dry-run corpus; re-audit required")


def _load_audited_dry_run(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ParallelBuildError("audited dry-run artifact is required before parallel apply")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ParallelBuildError("audited dry-run artifact is invalid JSON") from error


def _insert_manifest(
    path: Path, source_fingerprints: dict[str, str], rules_path: Path, counts: dict[str, int],
    duplicate: dict[str, Any], dataset_fingerprint: str,
) -> None:
    with connect(path) as connection, transaction(connection):
        connection.execute(
            """INSERT INTO migration_build_manifest (
                   singleton, schema_version, build_version, source_fingerprints_json, ranking_policy_sha256,
                   source_counts_json, target_counts_json, unresolved_semantic_count, equivalence_status,
                   dataset_fingerprint, database_fingerprint, build_status
               ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, 'EXACT_PASS', ?, NULL, 'PARALLEL_VALIDATED')""",
            (SCHEMA_VERSION, BUILD_VERSION, json.dumps(source_fingerprints, sort_keys=True), _sha256(rules_path),
             json.dumps({"source_occurrences": counts["portfolio_holding_source_occurrence"]}, sort_keys=True),
             json.dumps(counts, sort_keys=True), int(duplicate["count"]), dataset_fingerprint),
        )


def _validate_path(path: Path) -> None:
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as connection:
        integrity = tuple(str(row[0]) for row in connection.execute("PRAGMA integrity_check"))
        if integrity != ("ok",):
            raise ParallelBuildError("integrity_check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise ParallelBuildError("foreign_key_check failed")
