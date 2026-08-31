"""Read-only schema-v3 evidence access for the reference workflow."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from pathlib import Path

from portfolio_advisor.database.migrations.model_portfolio_dry_run import (
    SchemaV3ModelPortfolioRepository,
)
from portfolio_advisor.database.migrations.model_portfolio_parallel import BUILD_VERSION
from portfolio_advisor.metrics.portfolio import calculate_all_portfolio_metrics
from portfolio_advisor.ranking.config import load_ranking_rules
from portfolio_advisor.ranking.models import CandidateEvaluation
from portfolio_advisor.ranking.ranking import rank_portfolios


class ReferenceEvidenceError(RuntimeError):
    """Raised when the governed model side cannot be proven from schema v3."""


@dataclass(frozen=True, slots=True)
class ModelRankingEvidence:
    evaluation: CandidateEvaluation
    portfolio_id: int
    snapshot_id: int
    snapshot_date: date
    source_file: str
    source_file_sha256: str
    source_sheet_id: int
    source_sheet_name: str
    occurrence_ids: tuple[int, ...]
    source_rows: tuple[int, ...]
    dataset_fingerprint: str
    warnings: tuple[str, ...]


class SchemaV3ReferenceRepository:
    """Validate and read the model-portfolio foundation without writes."""

    def __init__(self, database_path: Path, rules_path: Path) -> None:
        self.database_path = database_path
        self.rules_path = rules_path

    def model_dates(self) -> tuple[date, ...]:
        with self._connection() as connection:
            self._validate(connection)
            rows = connection.execute(
                "SELECT DISTINCT snapshot_date FROM portfolio_snapshot ORDER BY snapshot_date"
            ).fetchall()
        return tuple(date.fromisoformat(str(row[0])) for row in rows)

    def shortlist_dates(self) -> tuple[date, ...]:
        with self._connection() as connection:
            self._validate(connection)
            rows = connection.execute(
                "SELECT snapshot_date FROM shortlist_snapshot ORDER BY snapshot_date"
            ).fetchall()
        return tuple(date.fromisoformat(str(row[0])) for row in rows)

    def rank_models(self, observation_date: date) -> tuple[ModelRankingEvidence, ...]:
        with self._connection() as connection:
            manifest = self._validate(connection)
        repository = SchemaV3ModelPortfolioRepository(self.database_path)
        metrics = calculate_all_portfolio_metrics(repository.load_holdings(observation_date))
        ranking, warnings = rank_portfolios(metrics, load_ranking_rules(self.rules_path))
        if not ranking:
            raise ReferenceEvidenceError("model ranking is empty")
        return tuple(
            self._model_evidence(item, observation_date, str(manifest["dataset_fingerprint"]), warnings)
            for item in ranking
        )

    def _model_evidence(
        self,
        evaluation: CandidateEvaluation,
        observation_date: date,
        dataset_fingerprint: str,
        warnings: tuple[str, ...],
    ) -> ModelRankingEvidence:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT p.portfolio_id, ps.portfolio_snapshot_id, sf.filename, sf.sha256,
                          ss.source_sheet_id, ss.sheet_name,
                          o.portfolio_holding_source_occurrence_id, o.source_row_number
                   FROM portfolio p
                   JOIN portfolio_snapshot ps ON ps.portfolio_id=p.portfolio_id
                   JOIN source_sheet ss ON ss.source_sheet_id=ps.source_sheet_id
                   JOIN source_file sf ON sf.source_file_id=ss.source_file_id
                   JOIN portfolio_holding_source_occurrence o
                     ON o.portfolio_snapshot_id=ps.portfolio_snapshot_id
                   WHERE p.portfolio_name=? AND ps.snapshot_date=?
                   ORDER BY o.portfolio_holding_source_occurrence_id""",
                (evaluation.metrics.portfolio_name, observation_date.isoformat()),
            ).fetchall()
        if not rows:
            raise ReferenceEvidenceError("model finalist source lineage is missing")
        header = tuple(rows[0][:6])
        if any(tuple(row[:6]) != header for row in rows):
            raise ReferenceEvidenceError("model finalist source snapshot is ambiguous")
        return ModelRankingEvidence(
            evaluation=evaluation,
            portfolio_id=int(header[0]),
            snapshot_id=int(header[1]),
            snapshot_date=observation_date,
            source_file=str(header[2]),
            source_file_sha256=str(header[3]),
            source_sheet_id=int(header[4]),
            source_sheet_name=str(header[5]),
            occurrence_ids=tuple(int(row[6]) for row in rows),
            source_rows=tuple(int(row[7]) for row in rows),
            dataset_fingerprint=dataset_fingerprint,
            warnings=warnings,
        )

    def _connection(self) -> sqlite3.Connection:
        if not self.database_path.is_file():
            raise ReferenceEvidenceError(f"schema-v3 database missing: {self.database_path}")
        connection = sqlite3.connect(f"file:{self.database_path.resolve()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    def _validate(self, connection: sqlite3.Connection) -> sqlite3.Row:
        try:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ReferenceEvidenceError("SQLite integrity_check failed")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise ReferenceEvidenceError("SQLite foreign_key_check failed")
            required = {
                "migration_build_manifest",
                "portfolio",
                "portfolio_snapshot",
                "portfolio_holding_source_occurrence",
                "shortlist_snapshot",
            }
            tables = {
                str(row[0])
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if not required <= tables:
                raise ReferenceEvidenceError("incompatible schema-v3 reference schema")
            rows = connection.execute("SELECT * FROM migration_build_manifest").fetchall()
        except sqlite3.DatabaseError as error:
            raise ReferenceEvidenceError("incompatible or corrupted schema-v3 database") from error
        if len(rows) != 1:
            raise ReferenceEvidenceError("model build manifest is missing or ambiguous")
        manifest = rows[0]
        if int(manifest["schema_version"]) != 3 or manifest["build_version"] != BUILD_VERSION:
            raise ReferenceEvidenceError("model build manifest version is stale or incompatible")
        if manifest["build_status"] != "PARALLEL_VALIDATED":
            raise ReferenceEvidenceError("model build manifest is incomplete")
        if manifest["equivalence_status"] != "EXACT_PASS":
            raise ReferenceEvidenceError("model equivalence manifest is not exact")
        expected_policy = sha256(self.rules_path.read_bytes()).hexdigest()
        if manifest["ranking_policy_sha256"] != expected_policy:
            raise ReferenceEvidenceError("model manifest ranking-policy fingerprint mismatch")
        try:
            fingerprints = json.loads(str(manifest["source_fingerprints_json"]))
        except (TypeError, json.JSONDecodeError) as error:
            raise ReferenceEvidenceError("model source fingerprint manifest is malformed") from error
        if not isinstance(fingerprints, dict) or not fingerprints:
            raise ReferenceEvidenceError("model source fingerprint manifest is empty")
        for source, expected in sorted(fingerprints.items()):
            path = Path(str(source))
            if not path.is_file() or sha256(path.read_bytes()).hexdigest() != expected:
                raise ReferenceEvidenceError(f"stale model source fingerprint: {path.name}")
        try:
            target_counts = json.loads(str(manifest["target_counts_json"]))
        except (TypeError, json.JSONDecodeError) as error:
            raise ReferenceEvidenceError("model target-count manifest is malformed") from error
        for table in ("portfolio_snapshot", "portfolio_holding_source_occurrence"):
            actual = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            if target_counts.get(table) != actual:
                raise ReferenceEvidenceError(f"model {table} manifest count mismatch")
        return manifest
