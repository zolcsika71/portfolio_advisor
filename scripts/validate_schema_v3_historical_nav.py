"""Read-only exact validation of retained official NAV integration."""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from portfolio_advisor.database.migrations.historical_nav_parallel import (
    validate_historical_nav,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("database/official_historical_nav.sqlite"))
    parser.add_argument("--target", type=Path, default=Path("database/portfolio_advisor.sqlite"))
    parser.add_argument("--output", type=Path, default=Path("data/audit/milestone_8_historical_nav_integration.json"))
    args = parser.parse_args(argv)
    result = validate_historical_nav(nav_source=args.source, target=args.target)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({**asdict(result), "mode": "VALIDATE", "cutover_status": "NOT_AUTHORIZED"}, indent=2, sort_keys=True) + "\n")
    return 0
if __name__ == "__main__": raise SystemExit(main())
