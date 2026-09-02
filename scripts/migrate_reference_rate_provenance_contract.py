"""Build a disposable candidate with reference-rate provenance contract v2."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from portfolio_advisor.database.migrations import (
    ReferenceRateProvenanceMigrationError,
    build_reference_rate_provenance_candidate,
    migrate_reference_rate_provenance_v2,
)

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=ROOT / "database/portfolio_advisor.sqlite",
    )
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument(
        "--candidate",
        type=Path,
        help="new disposable copy built from an exact v1 source",
    )
    destination.add_argument(
        "--target",
        type=Path,
        help="existing disposable v1/v2 candidate to migrate or replay in place",
    )
    parser.add_argument("--raw-artifact", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args(argv)
    try:
        if arguments.target is None:
            result = build_reference_rate_provenance_candidate(
                source=arguments.source,
                candidate=arguments.candidate,
                repository_root=arguments.repository_root,
                raw_artifact=arguments.raw_artifact,
                receipt_path=arguments.receipt,
            )
        else:
            result = migrate_reference_rate_provenance_v2(
                target=arguments.target,
                repository_root=arguments.repository_root,
                raw_artifact=arguments.raw_artifact,
                receipt_path=arguments.receipt,
            )
    except (ReferenceRateProvenanceMigrationError, OSError, ValueError) as error:
        print(f"Reference-rate provenance migration failed: {error}", file=sys.stderr)
        return 2
    output = json.dumps(result.to_dict(), ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        sys.stdout.write(output)
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(output, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
