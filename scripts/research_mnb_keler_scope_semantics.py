"""Build a bounded MNB/KELER report-scope research ledger.

This command is deliberately offline: network discovery is performed separately
and its reviewed, non-promoting findings are recorded in the input JSON.  Only
the resulting ledger is consumed by the offline semantic audit.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from portfolio_advisor.history.mnb_keler_scope_research import (
    ScopeResearchError,
    build_research_ledger,
)


def load_findings(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ScopeResearchError(f"Unable to read bounded research findings: {exc}") from exc
    if not isinstance(value, dict):
        raise ScopeResearchError("Bounded research findings must be an object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--findings",
        type=Path,
        default=Path("data/mnb_otc/report_scope/research_findings.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/audit/mnb_keler_scope_research_evidence.json"),
    )
    args = parser.parse_args()
    try:
        ledger = build_research_ledger(load_findings(args.findings))
    except ScopeResearchError as exc:
        print(f"MNB/KELER scope research failed closed: {exc}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"MNB/KELER scope candidates reviewed: {ledger['candidate_document_count']}")
    print(f"Scope research status: {ledger['research_status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
