"""Read-only schema-v3 shortlist evidence adapter."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from portfolio_advisor.database.migrations.shortlist_parallel import INTEGRATION_VERSION
from portfolio_advisor.database.schema.v3 import SCHEMA_VERSION


class ShortlistEvidenceError(RuntimeError):
    """Raised when governed shortlist evidence is absent, stale, or inconsistent."""


@dataclass(frozen=True, slots=True)
class SnapshotIdentity:
    snapshot_id: int
    snapshot_date: date
    source_sheet_id: int
    source_sheet_name: str
    source_file: str
    source_file_sha256: str
    manifest_fingerprint: str
    integration_version: str


@dataclass(frozen=True, slots=True)
class MembershipEvidence:
    shortlist_entry_id: int
    instrument_id: int
    isin: str
    canonical_name: str
    currency: str | None
    currency_risk: str | None
    occurrence_ids: tuple[int, ...]
    source_rows: tuple[int, ...]
    metrics: tuple[tuple[str, float], ...]


_REQUIRED_TABLE_COLUMNS = {
    "schema_version": {"singleton", "version"},
    "shortlist_stage_manifest": {
        "singleton", "integration_version", "workbook_fingerprints_json",
        "snapshot_count", "source_occurrence_count", "membership_count",
        "lineage_count", "dataset_fingerprint", "completion_status",
    },
    "shortlist_snapshot": {"shortlist_snapshot_id", "snapshot_date", "source_sheet_id"},
    "shortlist_entry": {"shortlist_entry_id", "shortlist_snapshot_id", "instrument_id"},
    "shortlist_entry_source_occurrence": {
        "shortlist_entry_source_occurrence_id", "shortlist_snapshot_id", "instrument_id",
        "source_row_number", "source_payload_json", "conflict_status",
    },
    "shortlist_entry_lineage": {"shortlist_entry_id", "source_occurrence_id"},
    "instrument": {"instrument_id", "isin", "canonical_name"},
    "instrument_metric_observation": {
        "instrument_id", "metric_id", "observation_date", "value",
        "provenance_type", "source_reference",
    },
    "metric_definition": {"metric_id", "metric_code"},
    "source_file": {"source_file_id", "filename", "sha256"},
    "source_sheet": {"source_sheet_id", "source_file_id", "sheet_name"},
}


class SchemaV3ShortlistRepository:
    """Validate and load complete shortlist evidence without opening a write path."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        if not self.database_path.is_file():
            raise ShortlistEvidenceError(f"derived database is missing: {self.database_path}")
        try:
            connection = sqlite3.connect(
                f"file:{self.database_path.resolve()}?mode=ro", uri=True
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            connection.execute("PRAGMA foreign_keys = ON")
            return connection
        except sqlite3.Error as error:
            raise ShortlistEvidenceError("derived database cannot be opened read-only") from error

    def validate(
        self,
        expected_workbook_fingerprints: Mapping[str, str],
        expected_manifest_fingerprint: str,
    ) -> None:
        with self._connect() as connection:
            try:
                self._validate_schema(connection)
                manifest = self._manifest(connection)
                recorded = json.loads(str(manifest["workbook_fingerprints_json"]))
            except (sqlite3.Error, json.JSONDecodeError) as error:
                raise ShortlistEvidenceError("incompatible shortlist schema or manifest") from error
            if recorded != dict(expected_workbook_fingerprints):
                raise ShortlistEvidenceError("stale source workbook fingerprint manifest")
            if str(manifest["dataset_fingerprint"]) != expected_manifest_fingerprint:
                raise ShortlistEvidenceError("stale shortlist dataset fingerprint")

    def select_snapshot(
        self,
        *,
        as_of: date | None,
        expected_workbook_fingerprints: Mapping[str, str],
        expected_manifest_fingerprint: str,
    ) -> SnapshotIdentity:
        self.validate(expected_workbook_fingerprints, expected_manifest_fingerprint)
        with self._connect() as connection:
            manifest = self._manifest(connection)
            if as_of is None:
                selected_date = connection.execute(
                    "SELECT max(snapshot_date) FROM shortlist_snapshot"
                ).fetchone()[0]
            else:
                selected_date = connection.execute(
                    "SELECT max(snapshot_date) FROM shortlist_snapshot WHERE snapshot_date <= ?",
                    (as_of.isoformat(),),
                ).fetchone()[0]
            if selected_date is None:
                raise ShortlistEvidenceError("no complete shortlist snapshot is available as of request")
            rows = connection.execute(
                """SELECT ss.shortlist_snapshot_id, ss.snapshot_date, sh.source_sheet_id,
                          sh.sheet_name, sf.filename, sf.sha256
                   FROM shortlist_snapshot ss
                   JOIN source_sheet sh ON sh.source_sheet_id=ss.source_sheet_id
                   JOIN source_file sf ON sf.source_file_id=sh.source_file_id
                   WHERE ss.snapshot_date=?""",
                (selected_date,),
            ).fetchall()
            if len(rows) != 1:
                raise ShortlistEvidenceError("latest complete shortlist snapshot is ambiguous")
            row = rows[0]
            return SnapshotIdentity(
                snapshot_id=int(row["shortlist_snapshot_id"]),
                snapshot_date=date.fromisoformat(str(row["snapshot_date"])),
                source_sheet_id=int(row["source_sheet_id"]),
                source_sheet_name=str(row["sheet_name"]),
                source_file=str(row["filename"]),
                source_file_sha256=str(row["sha256"]),
                manifest_fingerprint=str(manifest["dataset_fingerprint"]),
                integration_version=str(manifest["integration_version"]),
            )

    def load_memberships(self, snapshot: SnapshotIdentity) -> tuple[MembershipEvidence, ...]:
        with self._connect() as connection:
            memberships = connection.execute(
                """SELECT e.shortlist_entry_id, e.instrument_id, i.isin, i.canonical_name
                   FROM shortlist_entry e JOIN instrument i ON i.instrument_id=e.instrument_id
                   WHERE e.shortlist_snapshot_id=? ORDER BY i.isin""",
                (snapshot.snapshot_id,),
            ).fetchall()
            result: list[MembershipEvidence] = []
            for item in memberships:
                entry_id = int(item["shortlist_entry_id"])
                occurrences = connection.execute(
                    """SELECT o.shortlist_entry_source_occurrence_id, o.source_row_number,
                              o.source_payload_json, o.conflict_status
                       FROM shortlist_entry_lineage l
                       JOIN shortlist_entry_source_occurrence o
                         ON o.shortlist_entry_source_occurrence_id=l.source_occurrence_id
                       WHERE l.shortlist_entry_id=?
                       ORDER BY o.shortlist_entry_source_occurrence_id""",
                    (entry_id,),
                ).fetchall()
                if len(occurrences) != 1:
                    raise ShortlistEvidenceError(
                        f"membership {entry_id} lacks one unambiguous source occurrence"
                    )
                occurrence = occurrences[0]
                if str(occurrence["conflict_status"]) != "SOURCE_REPORTED":
                    raise ShortlistEvidenceError(f"membership {entry_id} has unresolved source metadata")
                payload = self._payload(occurrence["source_payload_json"])
                metrics = self._metrics(
                    connection,
                    instrument_id=int(item["instrument_id"]),
                    observation_date=snapshot.snapshot_date,
                    source_hash=snapshot.source_file_sha256,
                    sheet_name=snapshot.source_sheet_name,
                    source_row=int(occurrence["source_row_number"]),
                )
                result.append(
                    MembershipEvidence(
                        shortlist_entry_id=entry_id,
                        instrument_id=int(item["instrument_id"]),
                        isin=str(item["isin"]),
                        canonical_name=str(item["canonical_name"]),
                        currency=self._optional_text(payload.get("Deviza")),
                        currency_risk=self._optional_text(payload.get("Devizakockázat")),
                        occurrence_ids=(int(occurrence["shortlist_entry_source_occurrence_id"]),),
                        source_rows=(int(occurrence["source_row_number"]),),
                        metrics=metrics,
                    )
                )
            if not result:
                raise ShortlistEvidenceError("selected shortlist snapshot contains no memberships")
            expected = int(
                connection.execute(
                    "SELECT count(*) FROM shortlist_entry WHERE shortlist_snapshot_id=?",
                    (snapshot.snapshot_id,),
                ).fetchone()[0]
            )
            if len(result) != expected:
                raise ShortlistEvidenceError("shortlist membership reconciliation failed")
            return tuple(result)

    def _validate_schema(self, connection: sqlite3.Connection) -> None:
        integrity = tuple(str(row[0]) for row in connection.execute("PRAGMA integrity_check"))
        if integrity != ("ok",):
            raise ShortlistEvidenceError("SQLite integrity_check did not return ok")
        if tuple(connection.execute("PRAGMA foreign_key_check")):
            raise ShortlistEvidenceError("SQLite foreign_key_check reported violations")
        version = connection.execute(
            "SELECT version FROM schema_version WHERE singleton=1"
        ).fetchone()
        if version is None or int(version[0]) != SCHEMA_VERSION:
            raise ShortlistEvidenceError("incompatible schema version")
        for table, required in _REQUIRED_TABLE_COLUMNS.items():
            columns = {
                str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')
            }
            if not required.issubset(columns):
                raise ShortlistEvidenceError(f"incompatible shortlist schema: {table}")

    @staticmethod
    def _manifest(connection: sqlite3.Connection) -> sqlite3.Row:
        rows = connection.execute("SELECT * FROM shortlist_stage_manifest WHERE singleton=1").fetchall()
        if len(rows) != 1:
            raise ShortlistEvidenceError("shortlist stage manifest is missing or ambiguous")
        manifest = rows[0]
        if str(manifest["completion_status"]) != "COMPLETE":
            raise ShortlistEvidenceError("shortlist stage manifest is incomplete")
        if str(manifest["integration_version"]) != INTEGRATION_VERSION:
            raise ShortlistEvidenceError("shortlist integration version is stale")
        expected_counts = {
            "snapshot_count": "shortlist_snapshot",
            "source_occurrence_count": "shortlist_entry_source_occurrence",
            "membership_count": "shortlist_entry",
            "lineage_count": "shortlist_entry_lineage",
        }
        for field, table in expected_counts.items():
            actual = int(connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0])
            if int(manifest[field]) != actual:
                raise ShortlistEvidenceError(f"shortlist manifest {field} mismatch")
        fingerprint = str(manifest["dataset_fingerprint"])
        if len(fingerprint) != 64 or any(char not in "0123456789abcdef" for char in fingerprint):
            raise ShortlistEvidenceError("shortlist manifest fingerprint is invalid")
        return manifest

    @staticmethod
    def _payload(value: object) -> dict[str, object]:
        try:
            parsed = json.loads(str(value))
        except json.JSONDecodeError as error:
            raise ShortlistEvidenceError("source occurrence payload is malformed") from error
        if not isinstance(parsed, dict):
            raise ShortlistEvidenceError("source occurrence payload must be an object")
        return parsed

    @staticmethod
    def _optional_text(value: object) -> str | None:
        return str(value) if value is not None and str(value).strip() else None

    @staticmethod
    def _metrics(
        connection: sqlite3.Connection,
        *,
        instrument_id: int,
        observation_date: date,
        source_hash: str,
        sheet_name: str,
        source_row: int,
    ) -> tuple[tuple[str, float], ...]:
        prefix = f"SHORTLIST:{source_hash}:{sheet_name}:{source_row}:"
        rows = connection.execute(
            """SELECT md.metric_code, imo.value, imo.source_reference
               FROM instrument_metric_observation imo
               JOIN metric_definition md ON md.metric_id=imo.metric_id
               WHERE imo.instrument_id=? AND imo.observation_date=?
                 AND imo.provenance_type='PROVIDER_REPORTED'
                 AND imo.source_reference LIKE ?
               ORDER BY md.metric_code""",
            (instrument_id, observation_date.isoformat(), prefix + "%"),
        ).fetchall()
        metrics: dict[str, float] = {}
        for row in rows:
            code = str(row["metric_code"])
            if code in metrics:
                raise ShortlistEvidenceError(f"duplicate metric evidence for {code}")
            metrics[code] = float(row["value"])
        return tuple(sorted(metrics.items()))
