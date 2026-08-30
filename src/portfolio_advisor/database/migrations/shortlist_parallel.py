"""Copy-on-write import of audited shortlist sheets into schema-v3."""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from portfolio_advisor.audit.milestone_4 import audit_workbooks, is_valid_isin
from portfolio_advisor.database.schema.v3 import (
    connect,
    insert_instrument,
    transaction,
    upgrade_schema_v3_shortlist_extension,
    validate_schema,
)

INTEGRATION_VERSION = "MILESTONE_9_SHORTLIST_V1"
SUPPORTED_SIGNATURE = "termék|isin|eszközosztály|aleszközosztály|termék típus|deviza|devizakockázat|fenntarthatóság|ytd|1yr|3yr|5yr|1y sharpe|3y sharpe|5y sharpe|1y vol.|3y vol.|down. risk|info. ratio|max. drawd."
METRICS = {"YTD":"YTD", "1yr":"RETURN_1Y", "3yr":"RETURN_3Y", "5yr":"RETURN_5Y", "1Y Sharpe":"SHARPE_RATIO_1Y", "3Y Sharpe":"SHARPE_RATIO_3Y", "5Y Sharpe":"SHARPE_RATIO_5Y", "1Y Vol.":"VOLATILITY_1Y", "3Y Vol.":"VOLATILITY_3Y", "Down. risk":"DOWNSIDE_RISK", "Info. ratio":"INFORMATION_RATIO", "Max. drawd.":"MAXIMUM_DRAWDOWN"}

class ShortlistIntegrationError(RuntimeError): pass


def _required_lastrowid(lastrowid: int | None) -> int:
    if lastrowid is None:
        raise ShortlistIntegrationError("shortlist insert did not return a row ID")
    return lastrowid


def integrate_shortlist(*, workbook_directory: Path, target: Path, apply: bool) -> dict[str, Any]:
    audit = audit_workbooks(workbook_directory)
    sheets = [x for x in audit["files"] if x["source_type"] == "SHORTLIST_XLS" and x["status"] == "AUDITED"]
    signatures = sorted({str(x["header_signature"]) for x in sheets})
    if signatures != [SUPPORTED_SIGNATURE]: raise ShortlistIntegrationError("unsupported shortlist source-schema signature")
    directory = target.parent if apply else Path(tempfile.mkdtemp(prefix="portfolio-advisor-m9-")); directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / (f".{target.name}.m9.sqlite" if apply else "shortlist.sqlite"); shutil.copy2(target, candidate)
    try:
        result = _populate(candidate, sheets)
        if apply: candidate.replace(target)
        return result
    except BaseException:
        if candidate.exists(): candidate.unlink()
        raise

def _populate(path: Path, sheets: list[dict[str, Any]]) -> dict[str, Any]:
    added = aliases = entries = occurrences = metrics = 0; blocked: list[dict[str, Any]] = []
    with connect(path) as connection, transaction(connection):
        upgrade_schema_v3_shortlist_extension(connection); validate_schema(connection)
        connection.execute("CREATE TABLE IF NOT EXISTS shortlist_stage_manifest (singleton INTEGER PRIMARY KEY CHECK(singleton=1), integration_version TEXT NOT NULL, workbook_fingerprints_json TEXT NOT NULL, header_signature TEXT NOT NULL, source_occurrence_count INTEGER NOT NULL, snapshot_count INTEGER NOT NULL, membership_count INTEGER NOT NULL, lineage_count INTEGER NOT NULL, instrument_count INTEGER NOT NULL, alias_count INTEGER NOT NULL, metric_observation_count INTEGER NOT NULL, multi_occurrence_count INTEGER NOT NULL, conflict_occurrence_count INTEGER NOT NULL, dataset_fingerprint TEXT NOT NULL, completion_status TEXT NOT NULL)")
        connection.execute("DELETE FROM shortlist_entry_lineage"); connection.execute("DELETE FROM shortlist_entry_source_occurrence"); connection.execute("DELETE FROM shortlist_entry"); connection.execute("DELETE FROM shortlist_snapshot")
        connection.execute("DELETE FROM instrument_metric_observation WHERE source_reference LIKE 'SHORTLIST:%'")
        instrument_ids = {str(r[1]): int(r[0]) for r in connection.execute("SELECT instrument_id,isin FROM instrument")}
        metric_ids = {str(r[1]): int(r[0]) for r in connection.execute("SELECT metric_id,metric_code FROM metric_definition")}
        for code in sorted(set(METRICS.values()) - set(metric_ids)):
            cursor=connection.execute("INSERT INTO metric_definition(metric_code,name,unit,description) VALUES(?,?,?,?)",(code,code,"RATIO","Shortlist provider-reported metric")); metric_ids[code]=_required_lastrowid(cursor.lastrowid)
        for sheet in sheets:
            sha=str(sheet["file_sha256"]); file_id=connection.execute("SELECT source_file_id FROM source_file WHERE sha256=?",(sha,)).fetchone()
            if file_id is None:
                cur=connection.execute("INSERT INTO source_file(filename,sha256,source_type,source_date) VALUES(?,?,?,?)",(sheet["file"],sha,"SHORTLIST_XLS",sheet["snapshot_date"])); source_file_id=_required_lastrowid(cur.lastrowid)
            else: source_file_id=int(file_id[0])
            source_sheet_id=connection.execute("SELECT source_sheet_id FROM source_sheet WHERE source_file_id=? AND sheet_name=?",(source_file_id,sheet["sheet"])).fetchone()
            if source_sheet_id is None:
                cur=connection.execute("INSERT INTO source_sheet(source_file_id,sheet_name) VALUES(?,?)",(source_file_id,sheet["sheet"])); sid=_required_lastrowid(cur.lastrowid)
            else: sid=int(source_sheet_id[0])
            rows=[r for r in sheet["identity_records"] if r["isin"]]
            isins=[str(r["isin"]) for r in rows]
            cur=connection.execute("INSERT INTO shortlist_snapshot(snapshot_date,source_sheet_id) VALUES(?,?)",(sheet["snapshot_date"],sid)); snapshot=_required_lastrowid(cur.lastrowid)
            members: dict[str, int] = {}
            for row in rows:
                isin=str(row["isin"])
                if not is_valid_isin(isin): raise ShortlistIntegrationError("invalid explicit shortlist ISIN")
                iid=instrument_ids.get(isin)
                if iid is None: iid=insert_instrument(connection,isin,str(row["product_name"])); instrument_ids[isin]=iid; added+=1
                name=str(row["product_name"]); normalized=str(row["normalized_product_name"])
                cursor = connection.execute("INSERT OR IGNORE INTO instrument_alias(instrument_id,source_file_id,source_type,source_name,normalized_source_name,mapping_status,resolution_evidence) VALUES(?,?, 'SHORTLIST_XLS',?,?, 'EXPLICIT_ISIN_VALID','source explicit ISIN')",(iid,source_file_id,name,normalized)); aliases += int(cursor.rowcount > 0)
                conflict = "SOURCE_METADATA_CONFLICT" if isins.count(isin) > 1 else "SOURCE_REPORTED"
                member = members.get(isin)
                if member is None:
                    cursor = connection.execute("INSERT INTO shortlist_entry(shortlist_snapshot_id,instrument_id,source_row_number,status) VALUES(?,?,?,?)",(snapshot,iid,int(row["source_row"]),conflict)); member=_required_lastrowid(cursor.lastrowid); members[isin]=member; entries+=1
                payload=json.dumps(row["source_values"],sort_keys=True,ensure_ascii=False)
                cursor=connection.execute("INSERT INTO shortlist_entry_source_occurrence(shortlist_snapshot_id,instrument_id,source_sheet_id,source_row_number,observed_product_name,observed_currency_code,observed_asset_class,observed_sub_asset_class,source_payload_json,conflict_status) VALUES(?,?,?,?,?,?,?,?,?,?)",(snapshot,iid,sid,int(row["source_row"]),name,row["currency"],row["asset_class"],row["sub_asset_class"],payload,conflict)); occurrence=_required_lastrowid(cursor.lastrowid); occurrences+=1
                connection.execute("INSERT INTO shortlist_entry_lineage(shortlist_entry_id,source_occurrence_id) VALUES(?,?)",(member,occurrence))
                for header, code in METRICS.items():
                    value=row["source_values"].get(header)
                    if value not in (None, ""):
                        ref=f"SHORTLIST:{sha}:{sheet['sheet']}:{row['source_row']}:{header}"
                        connection.execute("INSERT INTO instrument_metric_observation(instrument_id,metric_id,observation_date,value,provenance_type,source_file_id,source_reference) VALUES(?,?,?,?, 'PROVIDER_REPORTED',?,?)",(iid,metric_ids[code],sheet["snapshot_date"],float(value),source_file_id,ref)); metrics+=1
        fingerprint=hashlib.sha256(json.dumps(sheets,sort_keys=True,default=str).encode()).hexdigest(); counts=(len(sheets),entries,occurrences)
        connection.execute("INSERT OR REPLACE INTO shortlist_stage_manifest VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(INTEGRATION_VERSION,json.dumps({s['file']:s['file_sha256'] for s in sheets},sort_keys=True),SUPPORTED_SIGNATURE,occurrences,*counts,len(instrument_ids),aliases,metrics,1,2,fingerprint,"COMPLETE"))
    return {"integration_version":INTEGRATION_VERSION,"supported_schema_signatures":[SUPPORTED_SIGNATURE],"source_sheets":len(sheets),"source_entries":sum(len([r for r in s['identity_records'] if r['isin']]) for s in sheets),"shortlist_entries":entries,"source_occurrences":occurrences,"canonical_instrument_additions":added,"aliases":aliases,"metric_observations":metrics,"unresolved":0,"blocked_sheets":blocked,"dataset_fingerprint":fingerprint,"completion_status":"COMPLETE"}
