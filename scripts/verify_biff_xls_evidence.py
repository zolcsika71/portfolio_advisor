"""Verify the hash-bound retained BIFF corpus without admitting data."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from portfolio_advisor.workbook_source.biff_evidence import (
    BiffEvidenceError,
    verify_corpus,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = (
    ROOT / "data/knowledge/biff_xls_processed_expected_inventory_v1.json"
)
DEFAULT_SOURCE = ROOT / "data/xls/processed"


def main(argv: list[str] | None = None) -> int:
    """Run the read-only verifier and create one non-overwriting report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Fresh report path outside the repository; the parent must exist.",
    )
    arguments = parser.parse_args(argv)
    try:
        output = arguments.output.resolve(strict=False)
        repository = ROOT.resolve()
        if output == repository or repository in output.parents:
            raise BiffEvidenceError(
                "OUTPUT_INSIDE_REPOSITORY",
                "generated evidence must remain outside committed source",
            )
        if output.exists() or output.is_symlink():
            raise BiffEvidenceError(
                "OUTPUT_ALREADY_EXISTS", "refusing to overwrite an existing path"
            )
        if not output.parent.is_dir() or output.parent.is_symlink():
            raise BiffEvidenceError(
                "INVALID_OUTPUT_PARENT",
                "output parent must be an existing ordinary directory",
            )
        report = verify_corpus(
            source_dir=arguments.source_dir,
            inventory_path=arguments.inventory,
        )
        serialized = (
            json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
        )
        try:
            with output.open("x", encoding="utf-8", newline="\n") as report_file:
                report_file.write(serialized)
        except FileExistsError as error:
            raise BiffEvidenceError(
                "OUTPUT_ALREADY_EXISTS",
                "refusing to overwrite a path created during verification",
            ) from error
    except (BiffEvidenceError, OSError, ValueError) as error:
        print(f"BIFF evidence verification failed: {error}", file=sys.stderr)
        return 2
    print(f"report={output}")
    print(f"report_fingerprint={report['report_fingerprint']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
