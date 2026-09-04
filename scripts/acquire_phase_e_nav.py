"""Explicitly acquire the fixed Phase E exact-share-class NAV responses."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from portfolio_advisor.history.nav_provenance import NavProvenanceError
from portfolio_advisor.history.nav_provenance_acquisition import (
    acquire_phase_e_nav,
    audit_phase_e_acquisition,
    recover_at0000673322_chart,
)

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    parser.add_argument("--database", type=Path, default=ROOT / "database/portfolio_advisor.sqlite")
    parser.add_argument(
        "--raw-directory", type=Path,
        default=ROOT / "data/raw/nav/erste_market",
    )
    parser.add_argument(
        "--index", type=Path,
        default=ROOT / "data/raw/nav/erste_market/phase-e-index.json",
    )
    parser.add_argument(
        "--recover-at0000673322-chart",
        action="store_true",
        help="Make only the one bounded Phase E recovery request; never admit it.",
    )
    parser.add_argument(
        "--offline-audit",
        action="store_true",
        help="Replay every retained response without network access or filesystem writes.",
    )
    arguments = parser.parse_args(argv)
    try:
        if arguments.offline_audit and arguments.recover_at0000673322_chart:
            parser.error("--offline-audit and --recover-at0000673322-chart are mutually exclusive")
        if arguments.offline_audit:
            result = audit_phase_e_acquisition(
                repository_root=arguments.repository_root,
                database_path=arguments.database,
                index_path=arguments.index,
            )
        else:
            operation = (
                recover_at0000673322_chart
                if arguments.recover_at0000673322_chart
                else acquire_phase_e_nav
            )
            result = operation(
                repository_root=arguments.repository_root,
                database_path=arguments.database,
                raw_directory=arguments.raw_directory,
                index_path=arguments.index,
            )
    except (NavProvenanceError, OSError, ValueError) as error:
        print(f"Phase E NAV acquisition failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
