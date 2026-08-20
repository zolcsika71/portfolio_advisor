"""Build bounded direct-official portfolio-performance research evidence."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from portfolio_advisor.history.official_portfolio_performance_research import (
    OfficialPortfolioPerformanceResearchError,
    build_research_artifact,
    build_search_targets,
    write_research_artifact,
    write_search_targets,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("database/model_portfolio.sqlite"))
    parser.add_argument("--processed-workbooks", type=Path, default=Path("data/xls/processed"))
    parser.add_argument("--targets-output", type=Path, default=Path("data/audit/official_portfolio_performance_search_targets.json"))
    parser.add_argument("--output", type=Path, default=Path("data/audit/official_portfolio_performance_source_research.json"))
    parser.add_argument("--source-family", action="append", default=[])
    parser.add_argument("--query-family", action="append", default=[])
    parser.add_argument("--discovery-rejection", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        targets = build_search_targets(database_path=args.database, processed_workbook_dir=args.processed_workbooks)
        write_search_targets(args.targets_output, targets)
        payload = build_research_artifact(
            targets=targets,
            source_families_searched=tuple(args.source_family),
            query_families=tuple(args.query_family),
            candidates=(),
            local_direct_source_found=False,
            pre_admission_discovery_rejections=tuple(args.discovery_rejection),
        )
        write_research_artifact(args.output, payload)
    except (OSError, ValueError, OfficialPortfolioPerformanceResearchError) as error:
        print(f"Official portfolio performance research failed: {error}", file=sys.stderr)
        return 2
    print(f"Search targets: {len(targets)}")
    print(f"Research status: {payload['search_status']}")
    print(f"Targets output: {args.targets_output}")
    print(f"Research output: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
