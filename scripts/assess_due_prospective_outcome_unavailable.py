"""Explicitly close one due live outcome slot without creating a numeric outcome.

This is an offline assessment boundary.  It never fetches a provider, changes
the finalized decision, or replaces unavailable evidence with a return value.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from portfolio_advisor.prospective.validation import (
    UNAVAILABLE_OUTCOME_STATUSES,
    ProspectiveValidationError,
    ProspectiveValidationStore,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decision-id", required=True)
    parser.add_argument("--horizon-days", required=True, type=int)
    parser.add_argument("--status", required=True, choices=sorted(UNAVAILABLE_OUTCOME_STATUSES))
    parser.add_argument("--source-reference", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--as-of-date", required=True, type=date.fromisoformat)
    parser.add_argument("--store", type=Path, default=Path("database/prospective_portfolio_validation.sqlite"))
    args = parser.parse_args(argv)
    try:
        inserted = ProspectiveValidationStore(args.store).mark_outcome_unavailable(
            decision_id=args.decision_id,
            horizon_days=args.horizon_days,
            status=args.status,
            source_reference=args.source_reference,
            reason=args.reason,
            current_date=args.as_of_date,
        )
    except (ProspectiveValidationError, RuntimeError, ValueError) as error:
        print(f"Prospective unavailable-outcome assessment failed: {error}", file=sys.stderr)
        return 2
    print("Closed due outcome as explicitly unavailable." if inserted else "Idempotent unavailable-outcome assessment.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
