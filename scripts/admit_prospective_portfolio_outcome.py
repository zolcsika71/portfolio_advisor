"""Admit one due, direct official portfolio outcome into a pending ledger slot.

This command has no provider integration.  It only accepts a locally retained,
already validated direct official observation supplied with complete provenance.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from portfolio_advisor.prospective.validation import (
    ALLOWED_OUTCOME_SOURCE_TYPES,
    ProspectiveOutcome,
    ProspectiveValidationError,
    ProspectiveValidationStore,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decision-id", required=True)
    parser.add_argument("--horizon-days", required=True, type=int)
    parser.add_argument("--portfolio-id", required=True)
    parser.add_argument("--information-date", required=True, type=date.fromisoformat)
    parser.add_argument("--as-of-date", required=True, type=date.fromisoformat)
    parser.add_argument("--source-type", required=True, choices=sorted(ALLOWED_OUTCOME_SOURCE_TYPES))
    parser.add_argument("--source-provider", required=True)
    parser.add_argument("--source-identifier", required=True)
    parser.add_argument("--source-reference", required=True)
    parser.add_argument("--local-artifact", required=True)
    parser.add_argument("--fingerprint", required=True)
    parser.add_argument("--currency", required=True)
    parser.add_argument("--value-semantics", required=True)
    parser.add_argument("--metrics-json", required=True, help="Object containing only supported finite outcome metrics.")
    parser.add_argument("--store", type=Path, default=Path("database/prospective_portfolio_validation.sqlite"))
    args = parser.parse_args(argv)
    try:
        metrics = json.loads(args.metrics_json)
        if not isinstance(metrics, dict):
            raise ProspectiveValidationError("metrics JSON must be an object")
        outcome = ProspectiveOutcome(
            decision_id=args.decision_id,
            horizon_days=args.horizon_days,
            portfolio_id=args.portfolio_id,
            observation_information_date=args.information_date,
            source_type=args.source_type,
            source_provider=args.source_provider,
            source_identifier=args.source_identifier,
            source_reference=args.source_reference,
            local_artifact=args.local_artifact,
            sha256_or_fingerprint=args.fingerprint,
            currency=args.currency,
            value_semantics=args.value_semantics,
            metrics=metrics,
        )
        inserted = ProspectiveValidationStore(args.store).admit_outcome(outcome, current_date=args.as_of_date)
    except (json.JSONDecodeError, ProspectiveValidationError, RuntimeError, ValueError) as error:
        print(f"Prospective outcome admission failed: {error}", file=sys.stderr)
        return 2
    print("Admitted official prospective outcome." if inserted else "Idempotent existing official outcome.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
