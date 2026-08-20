"""Record one completed deterministic ranking as immutable prospective evidence."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from portfolio_advisor.advisor.service import CapitalPreservationAdvisor
from portfolio_advisor.database.repository import ModelPortfolioRepository
from portfolio_advisor.prospective.validation import (
    LIVE_RECORD,
    RESEARCH_BACKFILL,
    ProspectiveValidationError,
    ProspectiveValidationStore,
    build_prospective_decision,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--decision-date",
        type=date.fromisoformat,
        help="Optional source snapshot date; live mode otherwise uses the repository's latest canonical date.",
    )
    parser.add_argument(
        "--record-type",
        choices=("live", "research-backfill", LIVE_RECORD, RESEARCH_BACKFILL),
        default="live",
        help="Defaults to a genuine live record; research-backfill is reserved for historical schema replay.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Build and validate the record without writing it.")
    parser.add_argument("--database", type=Path, default=Path("database/model_portfolio.sqlite"))
    parser.add_argument(
        "--rules", type=Path, default=Path("data/knowledge/validated_rules/capital_preservation_ranking.yaml")
    )
    parser.add_argument("--graph", type=Path, default=Path("data/knowledge/graphify-out/graph.json"))
    parser.add_argument("--freeze", type=Path, default=Path("data/audit/portfolio_nav_reconstruction_freeze.json"))
    parser.add_argument("--store", type=Path, default=Path("database/prospective_portfolio_validation.sqlite"))
    args = parser.parse_args(argv)
    root = Path.cwd().resolve()
    try:
        repository = ModelPortfolioRepository(args.database)
        record_type = {
            "live": LIVE_RECORD,
            LIVE_RECORD: LIVE_RECORD,
            "research-backfill": RESEARCH_BACKFILL,
            RESEARCH_BACKFILL: RESEARCH_BACKFILL,
        }[args.record_type]
        decision_date = args.decision_date or repository.latest_observation_date()
        result = CapitalPreservationAdvisor(repository, args.rules).evaluate(
            observation_date=decision_date,
            alternative_count=100,
        )
        record = build_prospective_decision(
            advisor_result=result,
            repository=repository,
            rules_path=args.rules,
            graph_path=args.graph,
            repository_root=root,
            record_type=record_type,
            freeze_path=args.freeze,
        )
        if args.dry_run:
            print(f"DRY_RUN prospective decision: {record['decision_id']}")
            print(f"Record type: {record_type}; decision date: {decision_date.isoformat()}")
            print(f"Candidates: {record['candidate_count']}; selected: {record['selected_portfolio_name']}")
            return 0
        inserted = ProspectiveValidationStore(args.store).finalize(record)
    except (ProspectiveValidationError, RuntimeError, ValueError) as error:
        print(f"Prospective decision record failed: {error}", file=sys.stderr)
        return 2
    action = "FINALIZED" if inserted else "ALREADY_RECORDED_IDENTICAL"
    print(f"{action} prospective decision: {record['decision_id']}")
    print(f"Record type: {record_type}; decision date: {decision_date.isoformat()}")
    print(f"Store: {args.store}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
