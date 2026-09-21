"""Write the read-only, aggregate-only Milestone 6 LTIA audit artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from portfolio_advisor.tbsz.ltia_reconciliation import (
    audit_current_ltia_projection_read_only,
    audit_ltia_read_only,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("database/tbsz_portfolio.sqlite"))
    parser.add_argument("--identity-store", type=Path, default=Path("data/tbsz/ltia_identity_confirmations.json"))
    parser.add_argument("--output", type=Path, default=Path("data/audit/milestone_6_ltia_identity_reconciliation.json"))
    args = parser.parse_args(argv)
    report = audit_ltia_read_only(args.database)
    confirmed_mapping_count = 0
    if args.identity_store.is_file():
        mappings = json.loads(args.identity_store.read_text(encoding="utf-8")).get("mappings", {})
        confirmed_mapping_count = len(mappings)
        report["confirmed_identity_store"] = {"distinct_mappings": len(mappings), "resolved_position_observations": report["positions"], "store_fingerprint": hashlib.sha256(args.identity_store.read_bytes()).hexdigest()}
        report["effective_identity_status_counts"] = {"CONFIRMED_MANUAL_ALIAS": report["positions"]}
    projection = audit_current_ltia_projection_read_only(args.database)
    report["current_projection"] = projection
    report["projection_views"] = {
        "account_level_positions": projection["positions"],
        "consolidated_confirmed_isins": confirmed_mapping_count,
        "cash_by_account_currency": projection["cash"],
        "equivalent_representatives": projection["equivalent_representatives"],
        "equivalent_lineage": projection["equivalent_lineage"],
    }
    report["fingerprint"] = hashlib.sha256(json.dumps({key: value for key, value in report.items() if key != "fingerprint"}, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
