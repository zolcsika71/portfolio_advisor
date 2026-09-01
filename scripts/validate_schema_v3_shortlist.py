"""Read-only complete-stage reconciliation of schema-v3 shortlist evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from portfolio_advisor.audit.milestone_4 import audit_workbooks
from portfolio_advisor.database.migrations.shortlist_parallel import (
    INTEGRATION_VERSION,
    METRICS,
    SUPPORTED_SIGNATURE,
)

_REQUIRED_SHORTLIST_COLUMNS = {
    "shortlist_stage_manifest": {
        "singleton",
        "integration_version",
        "workbook_fingerprints_json",
        "header_signature",
        "source_occurrence_count",
        "snapshot_count",
        "membership_count",
        "lineage_count",
        "dataset_fingerprint",
        "completion_status",
    },
    "shortlist_snapshot": {"shortlist_snapshot_id", "source_sheet_id"},
    "shortlist_entry": {"shortlist_entry_id", "shortlist_snapshot_id", "instrument_id"},
    "shortlist_entry_source_occurrence": {
        "shortlist_entry_source_occurrence_id",
        "shortlist_snapshot_id",
        "instrument_id",
        "source_sheet_id",
        "source_row_number",
        "source_payload_json",
        "conflict_status",
    },
    "shortlist_entry_lineage": {"shortlist_entry_id", "source_occurrence_id"},
}


def _validate_schema_contract(connection: sqlite3.Connection) -> None:
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    missing = sorted(set(_REQUIRED_SHORTLIST_COLUMNS) - tables)
    if missing:
        raise RuntimeError(f"missing or incomplete shortlist schema: {', '.join(missing)}")
    for table, required_columns in _REQUIRED_SHORTLIST_COLUMNS.items():
        columns = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
        missing_columns = sorted(required_columns - columns)
        if missing_columns:
            raise RuntimeError(
                f"incompatible shortlist schema: {table} missing columns: "
                f"{', '.join(missing_columns)}"
            )


def validate_shortlist_stage(target: Path, workbook_audit: dict[str, Any]) -> dict[str, int]:
    """Read-only reconciliation with injected audit data for deterministic tests."""
    sheets=[s for s in workbook_audit["files"] if s["source_type"]=="SHORTLIST_XLS" and s["status"]=="AUDITED"]
    if {s["header_signature"] for s in sheets} != {SUPPORTED_SIGNATURE}: raise RuntimeError("unknown signature")
    fingerprints={s["file"]:s["file_sha256"] for s in sheets}; dataset=hashlib.sha256(json.dumps(sheets,sort_keys=True,default=str).encode()).hexdigest()
    expected_payloads = {(s["file_sha256"], int(r["source_row"])): json.dumps(r["source_values"], sort_keys=True, ensure_ascii=False) for s in sheets for r in s["identity_records"] if r["isin"]}
    expected_metrics = {(s["file_sha256"], int(r["source_row"]), h, float(v)) for s in sheets for r in s["identity_records"] if r["isin"] for h, v in r["source_values"].items() if h in METRICS and v not in (None, "")}
    expected=sum(len([r for r in s["identity_records"] if r["isin"]]) for s in sheets)
    expected_multi_occurrence_groups = sum(
        1
        for sheet in sheets
        for isin in {str(row["isin"]) for row in sheet["identity_records"] if row["isin"]}
        if sum(str(row["isin"]) == isin for row in sheet["identity_records"]) > 1
    )
    with sqlite3.connect(f"file:{target.resolve()}?mode=ro",uri=True) as c:
        c.row_factory = sqlite3.Row
        try:
            _validate_schema_contract(c)
        except sqlite3.DatabaseError as exc:
            raise RuntimeError("incompatible shortlist schema") from exc
        if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("SQLite integrity failure")
        if c.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("foreign-key integrity failure")
        manifest=c.execute("SELECT * FROM shortlist_stage_manifest WHERE singleton=1").fetchone()
        if manifest is None or manifest["integration_version"]!=INTEGRATION_VERSION or manifest["completion_status"]!="COMPLETE" or manifest["header_signature"]!=SUPPORTED_SIGNATURE or json.loads(manifest["workbook_fingerprints_json"])!=fingerprints or manifest["dataset_fingerprint"]!=dataset: raise RuntimeError("stale manifest")
        actual=[int(c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]) for t in ("shortlist_snapshot","shortlist_entry","shortlist_entry_source_occurrence","shortlist_entry_lineage")]
        conflict=int(c.execute("SELECT count(*) FROM shortlist_entry_source_occurrence WHERE conflict_status='SOURCE_METADATA_CONFLICT'").fetchone()[0])
        manifest_counts=(manifest["snapshot_count"],manifest["membership_count"],manifest["source_occurrence_count"],manifest["lineage_count"])
        if conflict != 2: raise RuntimeError("conflict status tampering")
        multi_occurrence_groups = int(c.execute("SELECT count(*) FROM (SELECT shortlist_snapshot_id,instrument_id FROM shortlist_entry_source_occurrence GROUP BY shortlist_snapshot_id,instrument_id HAVING count(*) > 1)").fetchone()[0])
        if multi_occurrence_groups != expected_multi_occurrence_groups: raise RuntimeError("unexpected multi-occurrence group")
        observed = c.execute("SELECT f.sha256,o.source_row_number,o.source_payload_json FROM shortlist_entry_source_occurrence o JOIN source_sheet sh ON sh.source_sheet_id=o.source_sheet_id JOIN source_file f ON f.source_file_id=sh.source_file_id").fetchall()
        if {(r[0], int(r[1])) for r in observed} != set(expected_payloads) or any(r[2] != expected_payloads[(r[0], int(r[1]))] for r in observed): raise RuntimeError("source occurrence payload tampering")
        links=c.execute("SELECT o.shortlist_snapshot_id,o.instrument_id,e.shortlist_snapshot_id,e.instrument_id FROM shortlist_entry_lineage l JOIN shortlist_entry_source_occurrence o ON o.shortlist_entry_source_occurrence_id=l.source_occurrence_id JOIN shortlist_entry e ON e.shortlist_entry_id=l.shortlist_entry_id").fetchall()
        if any(r[0]!=r[2] or r[1]!=r[3] for r in links): raise RuntimeError("lineage tampering")
        metric_rows=c.execute("SELECT source_reference,value FROM instrument_metric_observation WHERE source_reference LIKE 'SHORTLIST:%'").fetchall()
        got={(x[0].split(":")[1],int(x[0].split(":")[3]),x[0].split(":")[4],float(x[1])) for x in metric_rows}
        if got != expected_metrics: raise RuntimeError("metric provenance tampering")
        if any(r[0] == 'US5949181045' and r[1] != 'SOURCE_METADATA_CONFLICT' for r in c.execute("SELECT i.isin,o.conflict_status FROM shortlist_entry_source_occurrence o JOIN instrument i ON i.instrument_id=o.instrument_id")): raise RuntimeError("conflict status tampering")
        if actual != [len(sheets), expected-1, expected, expected]: raise RuntimeError("incomplete or stale shortlist stage")
        if manifest_counts != tuple(actual): raise RuntimeError("manifest count reconciliation")
    return {"snapshots":actual[0],"memberships":actual[1],"occurrences":actual[2],"lineage":actual[3],"conflict_occurrences":conflict}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=Path("database/portfolio_advisor.sqlite"))
    parser.add_argument("--workbooks", type=Path, default=Path("data/xls/processed"))
    arguments = parser.parse_args(argv)
    result = validate_shortlist_stage(
        arguments.target,
        audit_workbooks(arguments.workbooks),
    )
    print(json.dumps({**result,"status":"PASS"},sort_keys=True)); return 0
if __name__=="__main__": raise SystemExit(main())
