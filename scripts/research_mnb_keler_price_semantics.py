"""Create an offline, bounded MNB/KELER price-semantics evidence ledger."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from portfolio_advisor.history.mnb_keler_price_semantics import (
    PriceSemanticsError,
    build_price_semantics_ledger,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--findings", type=Path, default=Path("data/mnb_otc/price_semantics/research_findings.json"))
    parser.add_argument("--output", type=Path, default=Path("data/audit/mnb_keler_price_semantics_evidence.json"))
    args = parser.parse_args()
    try:
        findings = json.loads(args.findings.read_text(encoding="utf-8"))
        if not isinstance(findings, dict):
            raise PriceSemanticsError("Price-semantics findings must be an object")
        ledger = build_price_semantics_ledger(findings)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, PriceSemanticsError) as exc:
        print(f"MNB/KELER price-semantics research failed closed: {exc}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"MNB/KELER price candidates reviewed: {ledger['candidate_document_count']}")
    print(f"Price-semantics research status: {ledger['research_status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
