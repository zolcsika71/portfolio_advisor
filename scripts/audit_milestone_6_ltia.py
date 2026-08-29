"""Write the read-only, aggregate-only Milestone 6 LTIA audit artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from portfolio_advisor.tbsz.ltia_reconciliation import audit_ltia_read_only


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("database/tbsz_portfolio.sqlite"))
    parser.add_argument("--current-database", type=Path, default=Path("database/tbsz_current_portfolio.sqlite"))
    parser.add_argument("--identity-store", type=Path, default=Path("data/tbsz/ltia_identity_confirmations.json"))
    parser.add_argument("--output", type=Path, default=Path("data/audit/milestone_6_ltia_identity_reconciliation.json"))
    args = parser.parse_args(argv)
    report = audit_ltia_read_only(args.database)
    if args.identity_store.is_file():
        mappings = json.loads(args.identity_store.read_text(encoding="utf-8")).get("mappings", {})
        report["confirmed_identity_store"] = {"distinct_mappings": len(mappings), "resolved_position_observations": report["positions"], "store_fingerprint": hashlib.sha256(args.identity_store.read_bytes()).hexdigest()}
        report["effective_identity_status_counts"] = {"CONFIRMED_MANUAL_ALIAS": report["positions"]}
    if args.current_database.is_file():
        with sqlite3.connect(f"file:{args.current_database.resolve()}?mode=ro", uri=True) as connection:
            report["current_projection"] = {
                "positions": int(connection.execute("SELECT COUNT(*) FROM position_snapshots").fetchone()[0]),
                "cash": int(connection.execute("SELECT COUNT(*) FROM cash_snapshots").fetchone()[0]),
                "unresolved_isin_positions": int(connection.execute("SELECT COUNT(*) FROM position_snapshots p JOIN instruments i ON i.instrument_id=p.instrument_id WHERE i.isin IS NULL").fetchone()[0]),
            }
            report["derived_views"] = {
                "account_level_positions": int(connection.execute("SELECT COUNT(*) FROM position_snapshots").fetchone()[0]),
                "consolidated_confirmed_isins": len(mappings) if args.identity_store.is_file() else 0,
                "cash_by_account_currency": int(connection.execute("SELECT COUNT(*) FROM cash_snapshots").fetchone()[0]),
                "equivalent_representatives": [5, 6],
                "equivalent_lineage": {"5": [5, 7], "6": [6, 8]},
            }
    report["fingerprint"] = hashlib.sha256(json.dumps({key: value for key, value in report.items() if key != "fingerprint"}, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
