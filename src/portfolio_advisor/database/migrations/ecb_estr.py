"""Copy-on-write candidate construction for official ECB €STR evidence."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from portfolio_advisor.canonical import canonical_fingerprint
from portfolio_advisor.database.migrations.reference_rate import (
    ReferenceRateMigrationError,
    pre_reference_rate_logical_fingerprint,
    reference_rate_schema_contract,
    validate_reference_rate_schema_foundation,
)
from portfolio_advisor.database.schema.v3 import connect, validate_schema
from portfolio_advisor.objectives.construction_policy import (
    CapitalDefensiveConstructionPolicy,
)
from portfolio_advisor.reference_rates.ecb_estr import (
    EcbEstrError,
    EcbEstrImportResult,
    import_ecb_estr_evidence,
    validate_ecb_estr_database,
)

ECB_ESTR_EVIDENCE_MIGRATION_REVISION = "MILESTONE_11C_PHASE_B_ECB_ESTR_V1"


@dataclass(frozen=True, slots=True)
class EcbEstrCandidateResult:
    """Deterministic safety evidence for a populated disposable database candidate."""

    migration_revision: str
    source_sha256: str
    candidate_sha256: str
    source_logical_fingerprint: str
    candidate_base_logical_fingerprint: str
    schema_contract_fingerprint: str
    import_result: EcbEstrImportResult
    base_table_counts: tuple[tuple[str, int], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "base_table_counts": dict(self.base_table_counts),
            "candidate_base_logical_fingerprint": self.candidate_base_logical_fingerprint,
            "candidate_sha256": self.candidate_sha256,
            "import_result": self.import_result.to_dict(),
            "migration_revision": self.migration_revision,
            "schema_contract_fingerprint": self.schema_contract_fingerprint,
            "source_logical_fingerprint": self.source_logical_fingerprint,
            "source_sha256": self.source_sha256,
        }


def build_ecb_estr_evidence_candidate(
    *,
    source: Path,
    candidate: Path,
    repository_root: Path,
    raw_artifact: Path,
    receipt_path: Path,
    policy: CapitalDefensiveConstructionPolicy,
) -> EcbEstrCandidateResult:
    """Copy the empty Phase A database and import only validated ECB evidence."""
    if source.is_symlink():
        raise EcbEstrError("ECB candidate source must not be a symlink")
    if candidate.is_symlink():
        raise EcbEstrError("ECB candidate target must not be a symlink")
    source = source.resolve()
    candidate = candidate.resolve()
    if not source.is_file():
        raise EcbEstrError("ECB candidate source must be a regular SQLite file")
    if candidate == source:
        raise EcbEstrError("ECB candidate must differ from the installed source")
    if candidate.exists():
        raise EcbEstrError("ECB candidate target already exists")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    source_sha256 = _sha256(source)
    validate_reference_rate_schema_foundation(source)
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as count_connection:
        count_connection.execute("PRAGMA query_only=ON")
        if any(
            int(count_connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0])
            for table in (
                "reference_rate_definition",
                "reference_rate_source",
                "reference_rate_import_manifest",
                "reference_rate_observation",
            )
        ):
            raise ReferenceRateMigrationError(
                "ECB Phase B candidate requires zero evidence rows"
            )
    source_logical, counts = pre_reference_rate_logical_fingerprint(source)
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA query_only=ON")
        source_schema_contract = reference_rate_schema_contract(connection)
    try:
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as source_connection:
            source_connection.execute("PRAGMA foreign_keys=ON")
            source_connection.execute("PRAGMA query_only=ON")
            with sqlite3.connect(candidate) as candidate_connection:
                candidate_connection.execute("PRAGMA foreign_keys=ON")
                source_connection.backup(candidate_connection)
        import_result = import_ecb_estr_evidence(
            target=candidate,
            repository_root=repository_root,
            raw_artifact=raw_artifact,
            receipt_path=receipt_path,
            policy=policy,
        )
        audit = validate_ecb_estr_database(
            target=candidate,
            repository_root=repository_root,
            raw_artifact=raw_artifact,
            receipt_path=receipt_path,
            policy=policy,
        )
        if audit["status"] != "PASS":
            raise EcbEstrError("ECB candidate read-only audit did not pass")
        candidate_logical, candidate_counts = pre_reference_rate_logical_fingerprint(candidate)
        if source_logical != candidate_logical or counts != candidate_counts:
            raise EcbEstrError("ECB candidate changed pre-existing logical data")
        with connect(candidate) as connection:
            validate_schema(connection)
            candidate_schema_contract = reference_rate_schema_contract(connection)
        if source_schema_contract != candidate_schema_contract:
            raise EcbEstrError("ECB candidate changed the Phase A schema contract")
        if _sha256(source) != source_sha256:
            raise EcbEstrError("installed source changed during ECB candidate construction")
        return EcbEstrCandidateResult(
            migration_revision=ECB_ESTR_EVIDENCE_MIGRATION_REVISION,
            source_sha256=source_sha256,
            candidate_sha256=_sha256(candidate),
            source_logical_fingerprint=source_logical,
            candidate_base_logical_fingerprint=candidate_logical,
            schema_contract_fingerprint=canonical_fingerprint(candidate_schema_contract),
            import_result=import_result,
            base_table_counts=counts,
        )
    except BaseException:
        candidate.unlink(missing_ok=True)
        raise


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
