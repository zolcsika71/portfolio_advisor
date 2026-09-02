"""Copy-on-write candidate construction for official New York Fed SOFR evidence."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from portfolio_advisor.canonical import canonical_fingerprint
from portfolio_advisor.database.migrations.reference_rate import (
    pre_reference_rate_logical_fingerprint,
    reference_rate_schema_contract,
    validate_reference_rate_schema_foundation,
)
from portfolio_advisor.database.schema.v3 import connect, validate_schema
from portfolio_advisor.objectives.construction_policy import (
    CapitalDefensiveConstructionPolicy,
)
from portfolio_advisor.reference_rates.provenance import (
    validate_reference_rate_database,
)
from portfolio_advisor.reference_rates.sofr import (
    SOFR_EXPECTED_OBSERVATION_COUNT,
    SofrError,
    SofrImportResult,
    import_sofr_evidence,
    validate_sofr_database,
)

SOFR_EVIDENCE_MIGRATION_REVISION = "MILESTONE_11C_PHASE_C_NYFED_SOFR_V1"
_REFERENCE_TABLES = (
    "reference_rate_definition",
    "reference_rate_source",
    "reference_rate_import_manifest",
    "reference_rate_observation",
)


@dataclass(frozen=True, slots=True)
class SofrCandidateResult:
    """Safety evidence for one populated disposable SOFR database candidate."""

    migration_revision: str
    source_sha256: str
    candidate_sha256: str
    source_logical_fingerprint: str
    candidate_logical_fingerprint: str
    estr_before_fingerprint: str
    estr_after_fingerprint: str
    schema_contract_fingerprint: str
    import_result: SofrImportResult
    source_reference_rate_counts: tuple[tuple[str, int], ...]
    candidate_reference_rate_counts: tuple[tuple[str, int], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_logical_fingerprint": self.candidate_logical_fingerprint,
            "candidate_reference_rate_counts": dict(
                self.candidate_reference_rate_counts
            ),
            "candidate_sha256": self.candidate_sha256,
            "estr_after_fingerprint": self.estr_after_fingerprint,
            "estr_before_fingerprint": self.estr_before_fingerprint,
            "import_result": self.import_result.to_dict(),
            "migration_revision": self.migration_revision,
            "schema_contract_fingerprint": self.schema_contract_fingerprint,
            "source_logical_fingerprint": self.source_logical_fingerprint,
            "source_reference_rate_counts": dict(self.source_reference_rate_counts),
            "source_sha256": self.source_sha256,
        }


def build_sofr_evidence_candidate(
    *,
    source: Path,
    candidate: Path,
    repository_root: Path,
    raw_artifact: Path,
    receipt_path: Path,
    policy: CapitalDefensiveConstructionPolicy,
) -> SofrCandidateResult:
    """Copy installed provenance-v2 data and add only one validated SOFR bundle."""
    if source.is_symlink() or candidate.is_symlink():
        raise SofrError("SOFR candidate database paths must not be symlinks")
    source = source.resolve()
    candidate = candidate.resolve()
    if not source.is_file() or source == candidate or candidate.exists():
        raise SofrError("SOFR candidate requires an existing source and absent distinct target")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    source_sha256 = _sha256(source)
    validate_reference_rate_schema_foundation(source)
    validate_reference_rate_database(target=source, repository_root=repository_root)
    source_logical, source_base_counts = pre_reference_rate_logical_fingerprint(source)
    estr_before = benchmark_stored_row_fingerprint(source, "ESTR")
    source_reference_counts = _reference_counts(source)
    if source_reference_counts != {
        "reference_rate_definition": 1,
        "reference_rate_source": 1,
        "reference_rate_import_manifest": 1,
        "reference_rate_observation": 1771,
    }:
        raise SofrError("SOFR candidate source does not contain the exact Phase C0 ESTR scope")
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        source_schema = reference_rate_schema_contract(connection)
    try:
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as source_connection:
            source_connection.execute("PRAGMA query_only=ON")
            with sqlite3.connect(candidate) as candidate_connection:
                source_connection.backup(candidate_connection)
        candidate_pre_logical, candidate_pre_counts = pre_reference_rate_logical_fingerprint(
            candidate
        )
        if (
            candidate_pre_logical != source_logical
            or candidate_pre_counts != source_base_counts
            or benchmark_stored_row_fingerprint(candidate, "ESTR") != estr_before
        ):
            raise SofrError("disposable SOFR candidate differs from its source before import")
        import_result = import_sofr_evidence(
            target=candidate,
            repository_root=repository_root,
            raw_artifact=raw_artifact,
            receipt_path=receipt_path,
            policy=policy,
        )
        if import_result.reused:
            raise SofrError("new SOFR candidate unexpectedly reused existing SOFR evidence")
        validate_sofr_database(
            target=candidate,
            repository_root=repository_root,
            raw_artifact=raw_artifact,
            receipt_path=receipt_path,
            policy=policy,
        )
        validate_reference_rate_database(
            target=candidate,
            repository_root=repository_root,
            require_sofr=True,
        )
        candidate_logical, candidate_base_counts = pre_reference_rate_logical_fingerprint(
            candidate
        )
        estr_after = benchmark_stored_row_fingerprint(candidate, "ESTR")
        if (
            candidate_logical != source_logical
            or candidate_base_counts != source_base_counts
            or estr_after != estr_before
        ):
            raise SofrError("SOFR candidate changed pre-existing logical or ESTR evidence")
        candidate_reference_counts = _reference_counts(candidate)
        expected = dict(source_reference_counts)
        expected["reference_rate_definition"] += 1
        expected["reference_rate_source"] += 1
        expected["reference_rate_import_manifest"] += 1
        expected["reference_rate_observation"] += SOFR_EXPECTED_OBSERVATION_COUNT
        if candidate_reference_counts != expected:
            raise SofrError("SOFR candidate row-count changes differ from the approved import")
        with connect(candidate) as connection:
            validate_schema(connection)
            candidate_schema = reference_rate_schema_contract(connection)
        if candidate_schema != source_schema:
            raise SofrError("SOFR candidate changed the provenance-v2 schema contract")
        if _sha256(source) != source_sha256:
            raise SofrError("installed source changed during SOFR candidate construction")
        return SofrCandidateResult(
            migration_revision=SOFR_EVIDENCE_MIGRATION_REVISION,
            source_sha256=source_sha256,
            candidate_sha256=_sha256(candidate),
            source_logical_fingerprint=source_logical,
            candidate_logical_fingerprint=candidate_logical,
            estr_before_fingerprint=estr_before,
            estr_after_fingerprint=estr_after,
            schema_contract_fingerprint=canonical_fingerprint(candidate_schema),
            import_result=import_result,
            source_reference_rate_counts=tuple(sorted(source_reference_counts.items())),
            candidate_reference_rate_counts=tuple(
                sorted(candidate_reference_counts.items())
            ),
        )
    except BaseException:
        candidate.unlink(missing_ok=True)
        raise


def benchmark_stored_row_fingerprint(path: Path, benchmark_id: str) -> str:
    """Fingerprint every stored field in one benchmark scope, IDs included."""
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        definitions = connection.execute(
            """SELECT * FROM reference_rate_definition WHERE benchmark_id=?
               ORDER BY reference_rate_definition_id""",
            (benchmark_id,),
        ).fetchall()
        definition_ids = [int(row["reference_rate_definition_id"]) for row in definitions]
        payload: dict[str, object] = {
            "benchmark_id": benchmark_id,
            "reference_rate_definition": _row_payload(definitions),
        }
        for table in _REFERENCE_TABLES[1:]:
            if not definition_ids:
                rows: list[sqlite3.Row] = []
            else:
                placeholders = ",".join("?" for _ in definition_ids)
                order = {
                    "reference_rate_source": "reference_rate_source_id",
                    "reference_rate_import_manifest": (
                        "reference_rate_import_manifest_id"
                    ),
                    "reference_rate_observation": (
                        "observation_date, revision_sequence, "
                        "reference_rate_observation_id"
                    ),
                }[table]
                rows = connection.execute(
                    f'SELECT * FROM "{table}" WHERE reference_rate_definition_id '
                    f"IN ({placeholders}) ORDER BY {order}",
                    definition_ids,
                ).fetchall()
            payload[table] = _row_payload(rows)
        return canonical_fingerprint(payload)


def _row_payload(rows: list[sqlite3.Row]) -> list[dict[str, object]]:
    return [dict(row) for row in rows]


def _reference_counts(path: Path) -> dict[str, int]:
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        return {
            table: int(connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0])
            for table in _REFERENCE_TABLES
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
