"""Classify live prospective outcome slots without network access or admission."""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from portfolio_advisor.prospective.due_monitoring import (
    build_prospective_outcome_due_monitoring,
    write_prospective_outcome_due_monitoring,
)
from portfolio_advisor.prospective.validation import (
    ProspectiveValidationError,
    ProspectiveValidationStore,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of-date", type=date.fromisoformat)
    parser.add_argument("--store", type=Path, default=Path("database/prospective_portfolio_validation.sqlite"))
    parser.add_argument("--freeze", type=Path, default=Path("data/audit/portfolio_nav_reconstruction_freeze.json"))
    parser.add_argument("--direct-performance-store", type=Path, default=Path("database/official_portfolio_performance.sqlite"))
    parser.add_argument("--output", type=Path, default=Path("data/audit/prospective_outcome_due_monitoring.json"))
    args = parser.parse_args(argv)
    as_of_date = args.as_of_date or datetime.now(ZoneInfo("Europe/Budapest")).date()
    try:
        payload = build_prospective_outcome_due_monitoring(
            store=ProspectiveValidationStore(args.store),
            repository_root=Path.cwd().resolve(),
            freeze_path=args.freeze,
            direct_performance_store_path=args.direct_performance_store,
            as_of_date=as_of_date,
        )
        repeated = build_prospective_outcome_due_monitoring(
            store=ProspectiveValidationStore(args.store),
            repository_root=Path.cwd().resolve(),
            freeze_path=args.freeze,
            direct_performance_store_path=args.direct_performance_store,
            as_of_date=as_of_date,
        )
        if payload != repeated:
            raise ProspectiveValidationError("due monitoring output is nondeterministic")
        write_prospective_outcome_due_monitoring(args.output, payload)
    except (ProspectiveValidationError, RuntimeError, ValueError) as error:
        print(f"Prospective outcome due monitoring failed: {error}", file=sys.stderr)
        return 2
    print(f"Monitoring status: {payload['monitoring_status']}")
    print(f"As of: {payload['as_of_date']}; not-yet-due: {payload['not_yet_due_count']}")
    print(f"Due-unassessed: {payload['due_unassessed_count']}; next due: {payload['next_due_date']}")
    print(f"Audit fingerprint: {payload['audit_fingerprint']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
