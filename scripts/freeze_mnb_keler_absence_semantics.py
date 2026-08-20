"""Freeze the terminal MNB/KELER absence-semantics result using local audits."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from portfolio_advisor.history.mnb_keler_absence_semantics import (
    AbsenceSemanticsFreezeError,
    build_absence_semantics_freeze,
)


def load(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AbsenceSemanticsFreezeError(f"Unable to load {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AbsenceSemanticsFreezeError(f"{path} must contain an object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", type=Path, default=Path("data/audit/mnb_keler_report_scope_semantics.json"))
    parser.add_argument("--ledger", type=Path, default=Path("data/audit/mnb_keler_scope_research_evidence.json"))
    parser.add_argument("--sparse", type=Path, default=Path("data/audit/hu0000554795_sparse_trading_semantics.json"))
    parser.add_argument("--output", type=Path, default=Path("data/audit/mnb_keler_absence_semantics_freeze.json"))
    args = parser.parse_args()
    try:
        artifact = build_absence_semantics_freeze(
            scope_semantics=load(args.scope),
            research_ledger=load(args.ledger),
            sparse_semantics=load(args.sparse),
            scope_artifact=str(args.scope),
            evidence_ledger=str(args.ledger),
        )
    except AbsenceSemanticsFreezeError as exc:
        print(f"MNB/KELER absence-semantics freeze failed closed: {exc}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("Absence semantics: AUTHORITATIVE_EVIDENCE_NOT_FOUND")
    print("Research closed: YES")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
