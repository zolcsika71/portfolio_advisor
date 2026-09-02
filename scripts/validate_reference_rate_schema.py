"""Read-only deterministic audit of the current reference-rate schema foundation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from portfolio_advisor.database.migrations import (
    ReferenceRateMigrationError,
    validate_reference_rate_schema_foundation,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        type=Path,
        default=Path("database/portfolio_advisor.sqlite"),
    )
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args(argv)
    try:
        result = validate_reference_rate_schema_foundation(arguments.target)
    except (ReferenceRateMigrationError, OSError, ValueError) as error:
        print(f"Reference-rate schema validation failed: {error}", file=sys.stderr)
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
