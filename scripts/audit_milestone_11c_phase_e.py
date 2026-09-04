"""Read-only deterministic audit of installed Phase E NAV evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from portfolio_advisor.history.nav_provenance import (
    NavProvenanceError,
    assess_erste_market_quarantined_chart,
    validate_phase_e_nav,
)

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=ROOT / "database/portfolio_advisor.sqlite")
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    parser.add_argument(
        "--legacy-source",
        type=Path,
        default=ROOT / "database/portfolio_advisor.sqlite",
    )
    parser.add_argument(
        "--index", type=Path,
        default=ROOT / "data/raw/nav/erste_market/phase-e-index.json",
    )
    parser.add_argument(
        "--assess-erstemarket-quarantine",
        action="store_true",
        help="Read only the retained AT0000673322 quarantine under its provider-specific media contract.",
    )
    parser.add_argument(
        "--quarantine-raw-reference",
        default=(
            "data/raw/nav/erste_market/quarantine/"
            "030bac8b4ed8e2434075c01436b260226aed177b12760a95f9909f6a186373ad.response.bin"
        ),
    )
    parser.add_argument("--quarantine-isin", default="AT0000673322")
    parser.add_argument(
        "--quarantine-raw-sha256",
        default="030bac8b4ed8e2434075c01436b260226aed177b12760a95f9909f6a186373ad",
    )
    parser.add_argument(
        "--quarantine-receipt-reference",
        default=(
            "data/raw/nav/erste_market/quarantine/"
            "4b534ac49c604bd36fb23302497c5ae87ab2c97ed7c8daf2a4ee77e46adcf7fb.quarantine.receipt.json"
        ),
    )
    parser.add_argument(
        "--quarantine-receipt-sha256",
        default="4b534ac49c604bd36fb23302497c5ae87ab2c97ed7c8daf2a4ee77e46adcf7fb",
    )
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args(argv)
    try:
        result = (
            assess_erste_market_quarantined_chart(
                repository_root=arguments.repository_root,
                database_path=arguments.target,
                index_path=arguments.index,
                isin=arguments.quarantine_isin,
                raw_reference=arguments.quarantine_raw_reference,
                raw_sha256=arguments.quarantine_raw_sha256,
                receipt_reference=arguments.quarantine_receipt_reference,
                receipt_sha256=arguments.quarantine_receipt_sha256,
            )
            if arguments.assess_erstemarket_quarantine
            else validate_phase_e_nav(
                repository_root=arguments.repository_root,
                target=arguments.target,
                index_path=arguments.index,
                legacy_source=arguments.legacy_source,
            )
        )
    except (NavProvenanceError, OSError, ValueError) as error:
        print(f"Phase E NAV audit failed: {error}", file=sys.stderr)
        return 2
    output = json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        sys.stdout.write(output)
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(output, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
