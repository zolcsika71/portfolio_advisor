"""Create a bounded, offline-reviewed alternative-price-source research artifact."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from portfolio_advisor.history.alternative_price_sources import (
    AlternativePriceSourceError,
    build_alternative_source_research,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--findings", type=Path, default=Path("data/alternative_price_sources/research_findings.json"))
    parser.add_argument("--output", type=Path, default=Path("data/audit/hu0000554795_alternative_price_sources.json"))
    args = parser.parse_args()
    try:
        findings = json.loads(args.findings.read_text(encoding="utf-8"))
        if not isinstance(findings, dict):
            raise AlternativePriceSourceError("Alternative-source findings must be an object")
        artifact = build_alternative_source_research(findings)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, AlternativePriceSourceError) as exc:
        print(f"Alternative price-source research failed closed: {exc}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Alternative price candidates reviewed: {artifact['candidate_count']}")
    print(f"Alternative price-source outcome: {artifact['research_outcome']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
