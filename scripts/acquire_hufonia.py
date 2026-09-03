"""Explicitly acquire and retain the fixed official MNB HUFONIA workbook."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from portfolio_advisor.reference_rates.hufonia import HufoniaError
from portfolio_advisor.reference_rates.hufonia_acquisition import acquire_hufonia

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    parser.add_argument(
        "--raw-directory",
        type=Path,
        default=ROOT / "data" / "raw" / "reference_rates" / "mnb" / "hufonia",
    )
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args(argv)
    try:
        result = acquire_hufonia(
            repository_root=arguments.repository_root,
            raw_directory=arguments.raw_directory,
        )
    except (OSError, HufoniaError, ValueError) as error:
        print(f"HUFONIA acquisition failed: {error}", file=sys.stderr)
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
